"""Do Scout's privacy hooks fire under the scheduled runs' claude flags? (spec 2026-11-13 §3.5, plan Phase 1a).

Scout's gates (kb-read-gate, outbound-gate, mcp-tool-gate) are Claude Code hooks.
Claude Code's headless docs say ``--bare`` "will become the default for ``-p`` in a
future release", and bare mode loads no hooks: not from settings, not from
plugins. A run on such a claude would read and send the vault with every gate
silently off. A hook can't report its own absence, so the check has to come
before the run:

The probe starts ``claude`` with the runners' permission flags against
:class:`~scout.agent_runtimes.fake_anthropic.FakeAnthropic` on 127.0.0.1 (no
model, no spend, a dummy key, never the owner's credentials, every other
provider switched off), in a throwaway fixture vault that holds one canary note
and a copy of the vault's own ``.claude/settings*.json`` (a project-level
``disableAllHooks`` there kills the gates for the run, so it must kill them for
the probe). The fake asks for one Bash call, ``cat knowledge-base/probe.md``. The
probe passes only when claude answered that call with the kb-read-gate's deny
and the canary never came back. Anything else, including a claude that won't
start, hangs, or sends its requests anywhere but the fake, is a failure: the
runners then skip the run (fail closed).

A pass is cached in ``.scout-state/gate-probe.json`` for :data:`MAX_AGE`, keyed by
claude's version, this engine's ``hooks.json``, the flags, the environment that
switches hook loading, and :func:`config_sha256`: every settings file and plugin
record that decides which hooks claude loads (user, project, managed, the
installed Scout plugin's own ``hooks.json``). So ``claude plugin disable scout``
or a new ``disableAllHooks`` re-probes at the next run. A cached run costs one
``claude --version``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scout.agent_runtimes.fake_anthropic import FakeAnthropic, ScriptedTurn

# The kb-read-gate's mark (scout.hooks.kb_read_gate.MARK); a test pins that the two agree.
GATE_MARK = "[scout:kb-read-gate]"
CANARY = "SCOUT-GATE-PROBE-CANARY-3b91"
PROBE_COMMAND = "cat knowledge-base/probe.md"
PROBE_MODE = "gate-probe"  # the probe session's SCOUT_MODE; the eval shim recognises it
# The runners' permission flags (run-*.sh.tmpl; a test keeps the two in step). Model, cap and name don't decide hooks.
PROBE_FLAGS: tuple[str, ...] = ("--permission-mode", "auto")
# Environment that switches claude's hook loading (``--bare`` sets CLAUDE_CODE_SIMPLE) or where it reads settings.
HOOK_ENV_VARS: tuple[str, ...] = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CONFIG_DIR")
MAX_AGE = timedelta(hours=24)
CACHE_REL = Path(".scout-state") / "gate-probe.json"
DEFAULT_TIMEOUT_S = 90.0
FIRST_REQUEST_S = 30.0  # a claude that hasn't asked the fake anything by then talks to some other endpoint
TMP_PREFIX = "scout-gate-probe-"
STALE_TMP = timedelta(hours=1)
# Never handed to the probe's claude: it talks to the fake only.
_CREDENTIAL_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
# Set to "0", not removed: a settings file's env could switch them back on, and the process env wins.
_PROVIDER_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
DUMMY_KEY = "sk-ant-scout-gate-probe-not-a-key"
_SETTINGS_FILES = ("settings.json", "settings.local.json")
_MANAGED_SETTINGS = (
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/etc/claude-code/managed-settings.json"),
)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str
    claude_version: str | None
    hooks_sha256: str
    flags: tuple[str, ...]
    env_key: str
    config_sha256: str
    checked_at: str  # ISO-8601 UTC, with offset

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["flags"] = list(self.flags)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ProbeResult:
        """Strict: any field of the wrong type raises, so a hand-edited or corrupt cache is a miss."""
        version = data["claude_version"]
        flags = data["flags"]
        checked = datetime.fromisoformat(data["checked_at"])
        if (
            not isinstance(data["ok"], bool)
            or not (version is None or isinstance(version, str))
            or not isinstance(flags, list)
            or not all(isinstance(f, str) for f in flags)
            or checked.tzinfo is None
        ):
            raise ValueError("malformed gate-probe cache")
        return cls(
            ok=data["ok"],
            reason=str(data["reason"]),
            claude_version=version,
            hooks_sha256=str(data["hooks_sha256"]),
            flags=tuple(flags),
            env_key=str(data["env_key"]),
            config_sha256=str(data["config_sha256"]),
            checked_at=str(data["checked_at"]),
        )


def default_plugin_root() -> Path:
    """The plugin directory this engine ships in (``<plugin>/engine/scout/…``)."""
    return Path(__file__).resolve().parents[3]


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def hooks_sha256(plugin_root: Path) -> str:
    return _sha(plugin_root / "hooks" / "hooks.json")


def env_key(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return ";".join(f"{k}={env.get(k, '')}" for k in HOOK_ENV_VARS)


def claude_config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = env.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path(env.get("HOME") or Path.home()) / ".claude"


def _installed_scout_hooks(config: Path) -> list[Path]:
    """Each installed Scout plugin's hooks.json, from the plugin registry (missing or unreadable: none)."""
    try:
        registry = json.loads((config / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        plugins = registry["plugins"]
        out: list[Path] = []
        for name, entries in plugins.items():
            if not str(name).startswith("scout@"):
                continue
            for entry in entries if isinstance(entries, list) else [entries]:
                out.append(Path(str(entry["installPath"])) / "hooks" / "hooks.json")
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def config_files(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Path]:
    """The files that decide which hooks claude loads for a run of ``vault``."""
    config = claude_config_dir(env)
    files = [config / name for name in _SETTINGS_FILES]
    files += [config / "plugins" / "installed_plugins.json", config / "plugins" / "known_marketplaces.json"]
    files += _installed_scout_hooks(config)
    files += list(_MANAGED_SETTINGS)
    if vault is not None:
        files += [vault / ".claude" / name for name in _SETTINGS_FILES]
    return files


def config_sha256(vault: Path | None, env: Mapping[str, str] | None = None) -> str:
    digest = hashlib.sha256()
    for path in config_files(vault, env):
        digest.update(f"{path}\0{_sha(path)}\n".encode())
    return digest.hexdigest()


def claude_version(claude_bin: Path, *, timeout_s: float = 15.0) -> str | None:
    try:
        out = subprocess.run(
            [str(claude_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip().splitlines()
    return line[0].strip() if out.returncode == 0 and line else None


def probe_env(*, base_url: str, vault: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The probe's environment: the caller's, minus every credential, pointed at the fake and the fixture vault."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in _CREDENTIAL_VARS}
    env.update({k: "0" for k in _PROVIDER_SWITCHES})
    env.update(
        ANTHROPIC_BASE_URL=base_url,
        ANTHROPIC_API_KEY=DUMMY_KEY,
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_TERMINAL_TITLE="1",
        SCOUT_DATA_DIR=str(vault),
        SCOUT_MODE=PROBE_MODE,
        SCOUT_READER="autonomous",
    )
    return env


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def _fixture_vault(root: Path, source: Path | None) -> Path:
    vault = root / "vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("instance_name: gate-probe\n", encoding="utf-8")
    (vault / "knowledge-base" / "probe.md").write_text(f"{CANARY}\n", encoding="utf-8")
    if source is not None:
        for name in _SETTINGS_FILES:
            src = source / ".claude" / name
            if src.is_file():
                (vault / ".claude").mkdir(exist_ok=True)
                shutil.copyfile(src, vault / ".claude" / name)
    return vault


def sweep_stale_tmp(now: float | None = None) -> None:
    """Remove fixture dirs a killed probe left behind (the prefix is ours; only ones over an hour old)."""
    cutoff = (now or time.time()) - STALE_TMP.total_seconds()
    tmp = Path(tempfile.gettempdir())
    try:
        candidates = list(tmp.glob(f"{TMP_PREFIX}*"))
    except OSError:
        return
    for path in candidates:
        try:
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()
    proc.wait()


def probe(
    claude_bin: Path,
    *,
    plugin_root: Path,
    flags: Sequence[str] = PROBE_FLAGS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    first_request_s: float = FIRST_REQUEST_S,
    vault: Path | None = None,
    version: str | None = None,
) -> ProbeResult:
    """Run one probe session with ``vault``'s project settings. Never raises: every failure is ``ok=False``."""
    flags = tuple(flags)
    sha, key, cfg = hooks_sha256(plugin_root), env_key(), config_sha256(vault)

    def result(ok: bool, reason: str, ver: str | None) -> ProbeResult:
        return ProbeResult(ok, reason, ver, sha, flags, key, cfg, _now_iso())

    resolved = shutil.which(str(claude_bin)) if os.path.sep not in str(claude_bin) else str(claude_bin)
    if not resolved or not Path(resolved).is_file():
        return result(False, f"claude not found at {claude_bin}", None)
    if not os.access(resolved, os.X_OK):
        return result(False, f"claude at {resolved} is not executable", None)
    version = version or claude_version(Path(resolved))
    sweep_stale_tmp()

    fake = FakeAnthropic([ScriptedTurn("Bash", {"command": PROBE_COMMAND})])
    try:
        base_url = fake.start()
    except OSError as e:
        return result(False, f"could not start the probe's local endpoint: {e}", version)
    try:
        with tempfile.TemporaryDirectory(prefix=TMP_PREFIX) as tmp:
            fixture = _fixture_vault(Path(tmp), vault)
            out_path = Path(tmp) / "claude.out"
            with out_path.open("wb") as out:
                try:
                    proc = subprocess.Popen(
                        [resolved, *flags, "--no-session-persistence", "-p", "Scout gate probe."],
                        cwd=fixture,
                        env=probe_env(base_url=base_url, vault=fixture),
                        stdin=subprocess.DEVNULL,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,  # its hooks and helpers die with it
                    )
                except OSError as e:
                    return result(False, f"could not start claude: {e}", version)
                started = time.monotonic()
                while proc.poll() is None:
                    elapsed = time.monotonic() - started
                    if fake.requests == 0 and elapsed > first_request_s:
                        _kill(proc)
                        return result(
                            False,
                            f"claude sent no request to the probe's local endpoint in {first_request_s:.0f}s "
                            "(does a setting send it to another provider or gateway?)",
                            version,
                        )
                    if elapsed > timeout_s:
                        _kill(proc)
                        return result(False, f"the probe session timed out after {timeout_s:.0f}s", version)
                    time.sleep(0.1)
            tail = out_path.read_text(encoding="utf-8", errors="replace").strip()[-300:]
    finally:
        fake.stop()

    results = fake.tool_results
    texts = [FakeAnthropic.result_text(r) for r in results]
    if any(CANARY in t for t in texts):
        return result(
            False,
            "Scout's gates are not loaded: the probe's shell read of a KB note ran instead of being denied "
            "(is claude running hooks-free, e.g. bare mode, or do settings disable hooks or the Scout plugin?)",
            version,
        )
    if fake.issued == 0:
        return result(False, f"claude never asked for the probe's tool call (exit {proc.returncode}): {tail}", version)
    if not results:
        return result(False, f"claude never answered the probe's tool call (exit {proc.returncode})", version)
    if not (results[0].get("is_error") is True and GATE_MARK in texts[0]):
        return result(False, f"the probe's shell read was not denied by the kb-read-gate: {texts[0][:300]}", version)
    return result(True, "kb-read-gate denied the probe's shell read of a KB note", version)


def read_cache(vault: Path) -> ProbeResult | None:
    try:
        return ProbeResult.from_json(json.loads((vault / CACHE_REL).read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - any unreadable cache is a miss, never a wedge
        return None


def _write_cache(vault: Path, res: ProbeResult) -> None:
    path = vault / CACHE_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(res.to_json(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # a cache we can't write only costs a re-probe next time


def _fresh(
    cached: ProbeResult, *, now: datetime, flags: tuple[str, ...], plugin_root: Path, vault: Path, version: str | None
) -> bool:
    try:
        age = now - datetime.fromisoformat(cached.checked_at)
        return (
            cached.ok is True
            and timedelta(0) <= age < MAX_AGE
            and cached.flags == flags
            and cached.hooks_sha256 == hooks_sha256(plugin_root)
            and cached.env_key == env_key()
            and cached.config_sha256 == config_sha256(vault)
            and version is not None
            and cached.claude_version == version
        )
    except Exception:  # noqa: BLE001 - a cache we can't compare is stale
        return False


def cached_probe(
    vault: Path,
    claude_bin: Path,
    *,
    plugin_root: Path,
    flags: Sequence[str] = PROBE_FLAGS,
    now: datetime | None = None,
    runner: Callable[..., ProbeResult] = probe,
) -> ProbeResult:
    """A fresh matching pass from the cache, else a new probe (whose result is cached)."""
    flags = tuple(flags)
    version = claude_version(claude_bin)
    cached = read_cache(vault)
    if cached is not None and _fresh(
        cached, now=now or datetime.now(UTC), flags=flags, plugin_root=plugin_root, vault=vault, version=version
    ):
        return cached
    res = runner(claude_bin, plugin_root=plugin_root, flags=flags, vault=vault, version=version)
    _write_cache(vault, res)
    return res

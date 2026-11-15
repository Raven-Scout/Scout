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
import mmap
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
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
# A project settings env key that switches claude's hook loading (``--bare`` sets CLAUDE_CODE_SIMPLE): copied into
# the probe's fixture. The cache key covers every CLAUDE_*/ANTHROPIC_* variable (env_key).
HOOK_ENV_COPY: tuple[str, ...] = ("CLAUDE_CODE_SIMPLE",)
MAX_AGE = timedelta(hours=24)
CACHE_REL = Path(".scout-state") / "gate-probe.json"
DEFAULT_TIMEOUT_S = 90.0
FIRST_REQUEST_S = 30.0  # a claude that hasn't asked the fake anything by then talks to some other endpoint
TMP_PREFIX = "scout-gate-probe-"
STALE_TMP = timedelta(hours=1)
# Never handed to the probe's claude: it talks to the fake only.
_CREDENTIAL_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
# Provider switches: each is set to "0", not removed, since a settings file's env could turn it back on and the
# process env wins. The known ones, plus every CLAUDE_CODE_USE_* the claude binary or a settings env names, minus
# the few that aren't providers (an unknown new switch is treated as a provider: zeroed).
_KNOWN_PROVIDER_SWITCHES = frozenset(
    {
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS",
        "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
        "CLAUDE_CODE_USE_MANTLE",
        "CLAUDE_CODE_USE_GATEWAY",
    }
)
_NOT_PROVIDER_SWITCHES = frozenset(
    {
        "CLAUDE_CODE_USE_NATIVE_FILE_SEARCH",
        "CLAUDE_CODE_USE_POWERSHELL_TOOL",
        "CLAUDE_CODE_USE_COWORK_PLUGINS",
        "CLAUDE_CODE_USE_CCR_V2",
    }
)
_SWITCH_RE = re.compile(rb"CLAUDE_CODE_USE_[A-Z0-9_]+")
# A settings env key claude itself may read that names an endpoint (AWS_ENDPOINT_URL_BEDROCK_RUNTIME,
# CLOUD_ML_REGION's siblings, ANTHROPIC_*_URL …). *_BASE_URL is pointed at the fake; any other one can't be
# neutralised from outside, so without the network sandbox the probe refuses rather than risk it. Other tools'
# keys (DOCKER_HOST, GH_HOST, JIRA_URL, DATABASE_URL, OTEL_*) are none of claude's business and pass.
_ENDPOINT_KEY_RE = re.compile(r"URL|ENDPOINT|HOST", re.IGNORECASE)
_CLAUDE_READ_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "CLOUD_ML_", "VERTEX_")
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
# Everything claude would reach besides the model: telemetry, error reports, the updater, claude.ai's MCP servers.
_QUIET_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_TRACES_EXPORTER": "none",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
}
# No MCP servers at all (the user's, a project's, a plugin's): none of them decides whether hooks load.
NO_MCP_FLAGS: tuple[str, ...] = ("--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}')
# The probe's claude may reach the fake on loopback and nothing else (macOS; elsewhere the env layers above).
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE = (
    '(version 1) (allow default) (deny network-outbound) (allow network-outbound (remote ip "localhost:*"))'
)
DUMMY_KEY = "sk-ant-scout-gate-probe-not-a-key"
_SETTINGS_FILES = ("settings.json", "settings.local.json")
# The project settings keys that decide whether and which hooks load; only these go into the probe's fixture,
# so a project hook, statusLine or apiKeyHelper never runs from the probe.
_HOOK_SETTING_KEYS = ("disableAllHooks", "allowManagedHooksOnly", "enabledPlugins", "permissions")
MANAGED_DIRS: tuple[Path, ...] = (Path("/Library/Application Support/ClaudeCode"), Path("/etc/claude-code"))
MDM_DIR = Path("/Library/Managed Preferences")


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
    unsafe: bool = False  # refused without trying: it couldn't be kept on this machine (CLI exit 4)

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
            or not isinstance(data["unsafe"], bool)
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
            unsafe=data["unsafe"],
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
    """A hash of every CLAUDE_* and ANTHROPIC_* variable but the credentials: any of them may change hook loading."""
    env = os.environ if env is None else env
    relevant = sorted(
        (k, v) for k, v in env.items() if k.startswith(("CLAUDE_", "ANTHROPIC_")) and k not in _CREDENTIAL_VARS
    )
    return "sha256:" + hashlib.sha256(json.dumps(relevant).encode()).hexdigest()


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


def managed_files() -> list[Path]:
    """Managed settings: managed-settings.json, its managed-settings.d/ drop-ins, and MDM managed preferences."""
    files: list[Path] = []
    for d in MANAGED_DIRS:
        files.append(d / "managed-settings.json")
        files += sorted((d / "managed-settings.d").glob("*.json"))
    files += sorted(MDM_DIR.glob("com.anthropic.claudecode*.plist"))
    files += sorted(MDM_DIR.glob("*/com.anthropic.claudecode*.plist"))
    return files


def config_files(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Path]:
    """The files that decide which hooks claude loads for a run of ``vault``."""
    config = claude_config_dir(env)
    files = [config / name for name in _SETTINGS_FILES]
    files += [config / "plugins" / "installed_plugins.json", config / "plugins" / "known_marketplaces.json"]
    files += _installed_scout_hooks(config)
    files += managed_files()
    if vault is not None:
        files += [vault / ".claude" / name for name in _SETTINGS_FILES]
    return files


def _load_settings(path: Path) -> Mapping[str, Any] | None:
    try:
        data = plistlib.loads(path.read_bytes()) if path.suffix == ".plist" else json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable settings file has no env to worry about
        return None
    return data if isinstance(data, Mapping) else None


def settings_env_blocks(env: Mapping[str, str] | None = None) -> list[Mapping[str, Any]]:
    """The ``env`` blocks of the user's and the managed settings: the probe's claude reads these as they are."""
    config = claude_config_dir(env)
    blocks: list[Mapping[str, Any]] = []
    for path in [*(config / name for name in _SETTINGS_FILES), *managed_files()]:
        data = _load_settings(path)
        block = data.get("env") if data is not None else None
        if isinstance(block, Mapping):
            blocks.append(block)
    return blocks


def _scan(path: Path) -> set[str]:
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return {match.decode() for match in set(_SWITCH_RE.findall(m))}
    except (OSError, ValueError):
        return set()


_SHIM_MAX = 64 * 1024
_SHIM_PATH_RE = re.compile(r"""(?:\$basedir/|\$\{basedir\}/)?([~./\w@-]*/[\w@./-]+)""")


def _shim_targets(script: Path) -> list[Path]:
    """The files a small launcher script (an npm, mise or asdf shim) names: absolute, or relative to its folder."""
    try:
        if script.stat().st_size > _SHIM_MAX:
            return []
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.startswith("#!"):
        return []
    out: list[Path] = []
    for raw in _SHIM_PATH_RE.findall(text.split("\n", 1)[-1]):
        cand = Path(os.path.expanduser(raw))
        cand = cand if cand.is_absolute() else script.parent / cand
        if cand.is_file() and cand.resolve() != script.resolve():
            out.append(cand.resolve())
    return out


def _binary_switches(claude_bin: Path) -> set[str]:
    """Every CLAUDE_CODE_USE_* name in the claude binary, through a launcher script to what it runs if need be."""
    real = Path(os.path.realpath(claude_bin))
    found = _scan(real)
    if not found:
        for target in _shim_targets(real):
            found |= _scan(target)
    return found


def provider_switches(claude_bin: Path | None, blocks: Sequence[Mapping[str, Any]]) -> set[str]:
    names = set(_KNOWN_PROVIDER_SWITCHES)
    if claude_bin is not None:
        names |= _binary_switches(claude_bin)
    for block in blocks:
        names |= {str(k) for k in block if str(k).startswith("CLAUDE_CODE_USE_")}
    return names - _NOT_PROVIDER_SWITCHES


def endpoint_keys(blocks: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    """(base URLs to point at the fake, endpoint keys that can't be neutralised) in the settings env blocks."""
    base_urls: set[str] = set()
    other: set[str] = set()
    for block in blocks:
        for raw in block:
            key = str(raw)
            if key.endswith("_BASE_URL"):
                base_urls.add(key)
            elif key.upper().startswith(_CLAUDE_READ_PREFIXES) and _ENDPOINT_KEY_RE.search(key):
                other.add(key)
    return base_urls, other


def sandbox_prefix() -> list[str]:
    """The command prefix that limits the probe's claude to loopback, or [] where there is none (not macOS)."""
    if sys.platform == "darwin" and os.access(SANDBOX_EXEC, os.X_OK):
        return [str(SANDBOX_EXEC), "-p", SANDBOX_PROFILE]
    return []


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


def probe_env(
    *,
    base_url: str,
    vault: Path,
    base: Mapping[str, str] | None = None,
    switches: Iterable[str] = _KNOWN_PROVIDER_SWITCHES,
    base_urls: Iterable[str] = (),
) -> dict[str, str]:
    """The probe's environment: the caller's, minus every credential, pointed at the fake and the fixture vault.

    Every provider switch is "0" and every base URL a settings env names is the fake's: the process env wins
    over a settings env, so no setting can send the probe to a billed provider.
    """
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in _CREDENTIAL_VARS}
    # Every provider switch already in the environment too, not only the ones the settings and binary name.
    in_env = {k for k in env if k.startswith("CLAUDE_CODE_USE_") and k not in _NOT_PROVIDER_SWITCHES}
    env.update({k: "0" for k in {*switches, *in_env}})
    env.update({k: base_url for k in base_urls})
    # No proxy: a loopback proxy sits outside the sandbox and would carry everything past it. Empty, not removed,
    # so a settings env can't set one (the process env wins); NO_PROXY=* sends any that survives nowhere.
    env.update({k: "" for k in _PROXY_VARS})
    env.update(NO_PROXY="*", no_proxy="*")
    env.update(_QUIET_ENV)
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
            data = _load_settings(source / ".claude" / name)
            if data is None:
                continue
            kept: dict[str, Any] = {k: data[k] for k in _HOOK_SETTING_KEYS if k in data}
            block = data.get("env")
            if isinstance(block, Mapping):
                hook_env = {k: block[k] for k in HOOK_ENV_COPY if k in block}
                if hook_env:
                    kept["env"] = hook_env
            (vault / ".claude").mkdir(exist_ok=True)
            (vault / ".claude" / name).write_text(json.dumps(kept), encoding="utf-8")
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

    def result(ok: bool, reason: str, ver: str | None, *, unsafe: bool = False) -> ProbeResult:
        return ProbeResult(ok, reason, ver, sha, flags, key, cfg, _now_iso(), unsafe)

    resolved = shutil.which(str(claude_bin)) if os.path.sep not in str(claude_bin) else str(claude_bin)
    if not resolved or not Path(resolved).is_file():
        return result(False, f"claude not found at {claude_bin}", None)
    if not os.access(resolved, os.X_OK):
        return result(False, f"claude at {resolved} is not executable", None)
    version = version or claude_version(Path(resolved))
    sweep_stale_tmp()

    # No request may leave the machine: a loopback-only sandbox where there is one (macOS), and in any case every
    # provider switch off and every base URL a settings env names pointed at the fake (the process env wins).
    sandbox = sandbox_prefix()
    blocks = settings_env_blocks()
    switches = provider_switches(Path(resolved), blocks)
    base_urls, other_endpoints = endpoint_keys(blocks)
    if not sandbox and other_endpoints:
        return result(
            False,
            "can't probe safely: a settings env names an endpoint the probe can't point at its local fake "
            f"({', '.join(sorted(other_endpoints))}) and there is no network sandbox here",
            version,
            unsafe=True,
        )
    if not sandbox and not _binary_switches(Path(resolved)):
        return result(
            False,
            f"can't probe safely: no provider switch could be read from {resolved} (a launcher the probe can't "
            "follow?), so a new one couldn't be turned off, and there is no network sandbox here",
            version,
            unsafe=True,
        )
    network = "loopback-only sandbox" if sandbox else "provider switches off, base URLs at the fake"

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
                        [
                            *sandbox,
                            resolved,
                            *flags,
                            *NO_MCP_FLAGS,
                            "--no-session-persistence",
                            "-p",
                            "Scout gate probe.",
                        ],
                        cwd=fixture,
                        env=probe_env(base_url=base_url, vault=fixture, switches=switches, base_urls=base_urls),
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
    return result(True, f"kb-read-gate denied the probe's shell read of a KB note (network: {network})", version)


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

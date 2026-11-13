"""Do Scout's privacy hooks fire under the scheduled runs' claude flags? (spec 2026-11-13 §3.5, plan Phase 1a).

Scout's gates (kb-read-gate, outbound-gate, mcp-tool-gate) are Claude Code hooks.
Claude Code's headless docs say ``--bare`` "will become the default for ``-p`` in a
future release", and bare mode loads no hooks: not from settings, not from
plugins. A run on such a claude would read and send the vault with every gate
silently off. A hook can't report its own absence, so the check has to come
before the run:

The probe starts ``claude`` with the runners' permission flags against
:class:`~scout.agent_runtimes.fake_anthropic.FakeAnthropic` on 127.0.0.1 (no
model, no spend, a dummy key, never the owner's credentials), in a throwaway
fixture vault holding one canary note. The fake asks for one Bash call,
``cat knowledge-base/probe.md``. The probe passes only when claude answered that
call with the kb-read-gate's deny and the canary never came back. Anything else,
including a claude that won't start, hangs or never asks, is a failure: the
runners then skip the run (fail closed).

A pass is cached in ``.scout-state/gate-probe.json`` for :data:`MAX_AGE`, keyed by
claude's version, the plugin's ``hooks.json`` and the flags and environment that
decide whether hooks load, so a normal run costs one ``claude --version``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
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
# The runners' permission flags (run-*.sh.tmpl). The model, cap and name don't decide whether hooks load.
PROBE_FLAGS: tuple[str, ...] = ("--permission-mode", "auto")
# Environment that switches claude's hook loading (``--bare`` sets CLAUDE_CODE_SIMPLE).
HOOK_ENV_VARS: tuple[str, ...] = ("CLAUDE_CODE_SIMPLE",)
MAX_AGE = timedelta(hours=24)
CACHE_REL = Path(".scout-state") / "gate-probe.json"
DEFAULT_TIMEOUT_S = 90.0
# Never handed to the probe's claude: it talks to the fake only.
_CREDENTIAL_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)
_DUMMY_KEY = "sk-ant-scout-gate-probe-not-a-key"


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str
    claude_version: str | None
    hooks_sha256: str
    flags: tuple[str, ...]
    env_key: str
    checked_at: str  # ISO-8601 UTC

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["flags"] = list(self.flags)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ProbeResult:
        return cls(
            ok=bool(data["ok"]),
            reason=str(data["reason"]),
            claude_version=data.get("claude_version"),
            hooks_sha256=str(data["hooks_sha256"]),
            flags=tuple(str(f) for f in data["flags"]),
            env_key=str(data.get("env_key", "")),
            checked_at=str(data["checked_at"]),
        )


def default_plugin_root() -> Path:
    """The plugin directory this engine ships in (``<plugin>/engine/scout/…``)."""
    return Path(__file__).resolve().parents[3]


def hooks_sha256(plugin_root: Path) -> str:
    try:
        return hashlib.sha256((plugin_root / "hooks" / "hooks.json").read_bytes()).hexdigest()
    except OSError:
        return ""


def env_key(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return ";".join(f"{k}={env.get(k, '')}" for k in HOOK_ENV_VARS)


def claude_version(claude_bin: Path, *, timeout_s: float = 15.0) -> str | None:
    try:
        out = subprocess.run(
            [str(claude_bin), "--version"], capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip().splitlines()
    return line[0].strip() if out.returncode == 0 and line else None


def probe_env(*, base_url: str, vault: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The probe's environment: the caller's, minus every credential, pointed at the fake and the fixture vault."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in _CREDENTIAL_VARS}
    env.update(
        ANTHROPIC_BASE_URL=base_url,
        ANTHROPIC_API_KEY=_DUMMY_KEY,
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_TERMINAL_TITLE="1",
        SCOUT_DATA_DIR=str(vault),
        SCOUT_MODE="gate-probe",
        SCOUT_READER="autonomous",
    )
    return env


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def _fixture_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("instance_name: gate-probe\n", encoding="utf-8")
    (vault / "knowledge-base" / "probe.md").write_text(f"{CANARY}\n", encoding="utf-8")
    return vault


def probe(
    claude_bin: Path,
    *,
    plugin_root: Path,
    flags: Sequence[str] = PROBE_FLAGS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ProbeResult:
    """Run one probe session. Never raises: every failure is a result with ``ok=False``."""
    flags = tuple(flags)
    sha = hooks_sha256(plugin_root)
    key = env_key()

    def result(ok: bool, reason: str, version: str | None) -> ProbeResult:
        return ProbeResult(ok, reason, version, sha, flags, key, _now_iso())

    resolved = shutil.which(str(claude_bin)) if os.path.sep not in str(claude_bin) else str(claude_bin)
    if not resolved or not Path(resolved).is_file():
        return result(False, f"claude not found at {claude_bin}", None)
    if not os.access(resolved, os.X_OK):
        return result(False, f"claude at {resolved} is not executable", None)
    version = claude_version(Path(resolved))

    fake = FakeAnthropic([ScriptedTurn("Bash", {"command": PROBE_COMMAND})])
    with tempfile.TemporaryDirectory(prefix="scout-gate-probe-") as tmp:
        vault = _fixture_vault(Path(tmp))
        try:
            base_url = fake.start()
            proc = subprocess.run(
                [resolved, *flags, "--no-session-persistence", "-p", "Scout gate probe."],
                cwd=vault,
                env=probe_env(base_url=base_url, vault=vault),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return result(False, f"the probe session timed out after {timeout_s:.0f}s", version)
        except OSError as e:
            return result(False, f"could not start claude: {e}", version)
        finally:
            fake.stop()

    results = fake.tool_results
    texts = [FakeAnthropic.result_text(r) for r in results]
    if any(CANARY in t for t in texts):
        return result(
            False,
            "Scout's gates are not loaded: the probe's shell read of a KB note ran instead of being denied "
            "(is claude running hooks-free, e.g. bare mode?)",
            version,
        )
    if fake.issued == 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return result(False, f"claude never asked for the probe's tool call (exit {proc.returncode}): {tail}", version)
    if not results:
        return result(False, f"claude never answered the probe's tool call (exit {proc.returncode})", version)
    if not (results[0].get("is_error") and GATE_MARK in texts[0]):
        return result(False, f"the probe's shell read was not denied by the kb-read-gate: {texts[0][:300]}", version)
    return result(True, "kb-read-gate denied the probe's shell read of a KB note", version)


def read_cache(vault: Path) -> ProbeResult | None:
    try:
        return ProbeResult.from_json(json.loads((vault / CACHE_REL).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
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
    cached = read_cache(vault)
    if cached is not None and cached.ok:
        try:
            age = (now or datetime.now(UTC)) - datetime.fromisoformat(cached.checked_at)
        except ValueError:
            age = MAX_AGE * 2
        if (
            timedelta(0) <= age < MAX_AGE
            and cached.flags == flags
            and cached.hooks_sha256 == hooks_sha256(plugin_root)
            and cached.env_key == env_key()
            and cached.claude_version is not None
            and cached.claude_version == claude_version(claude_bin)
        ):
            return cached
    res = runner(claude_bin, plugin_root=plugin_root, flags=flags)
    _write_cache(vault, res)
    return res

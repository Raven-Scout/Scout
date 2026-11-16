"""Whether and how a scheduled run is sandboxed: the switch, the status, the profile the runner hands claude-with-retry.

``scoutctl sandbox profile`` prints the profile and exits with the status (:data:`STATUS_EXIT`); the runners
act on that exit code (spec 2026-11-15 §8, Jordan's answers to Q1/Q2):

- ``enforced`` (0): run sandboxed;
- ``failed`` (3): a sandbox is expected here but the profile couldn't be built or failed its self-test, so the
  run is skipped (``skipped_sandbox``), never run with a profile that may be wrong;
- ``unavailable`` (6): no OS sandbox on this platform, so the run goes ahead as today and health says so;
- ``off`` (7): the owner turned it off (``scoutctl sandbox off``);
- ``conflict`` (8): Claude Code's own Bash sandbox is on and can't run inside this one, so the run goes ahead
  under Claude's sandbox only.

The switch is NOT in scout-config.yaml, which a run can write: it's ``~/.local/state/scout/run-sandbox.json``,
protected by both layers (hook_paths' engine roots), or ``SCOUT_RUN_SANDBOX`` in the environment (tests and the
eval harness), which a run can't set for a later run.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from scout.kb.hook_paths import SANDBOX_SWITCH_NAME
from scout.sandbox import profile as sbx

STATUS_EXIT = {"enforced": 0, "failed": 3, "unavailable": 6, "off": 7, "conflict": 8}
ENV_SWITCH = "SCOUT_RUN_SANDBOX"
_OFF = ("0", "off", "false", "no")
OFF_VALUES = _OFF  # what turns it off, for callers that read the variable themselves
_ON = ("1", "on", "true", "yes")


def switch_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "scout" / SANDBOX_SWITCH_NAME


def switch(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """(on, where that came from): the environment, then the owner's file, then the default (on).

    An unreadable or malformed file counts as on: the safe side.
    """
    env = os.environ if env is None else env
    raw = env.get(ENV_SWITCH, "").strip().lower()
    if raw in _OFF:
        return False, f"{ENV_SWITCH}={raw}"
    if raw in _ON:
        return True, f"{ENV_SWITCH}={raw}"
    try:
        data = json.loads(switch_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return True, "default"
    if isinstance(data, dict) and data.get("scheduled_runs") is False:
        return False, str(switch_path(env))
    return True, "default"


def set_switch(on: bool, env: Mapping[str, str] | None = None) -> Path:
    path = switch_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fresh temp file of our own (mkstemp opens it O_CREAT|O_EXCL, mode 0600), never a fixed name that could
    # already exist as a link to somewhere else; then an atomic rename over the switch.
    fd, tmp = tempfile.mkstemp(prefix=f".{SANDBOX_SWITCH_NAME}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"scheduled_runs": on}) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


@dataclass(frozen=True)
class Prepared:
    status: str  # a STATUS_EXIT key
    reason: str
    profile: str = ""

    @property
    def exit_code(self) -> int:
        return STATUS_EXIT[self.status]


def prepare(
    vault: Path | None,
    env: Mapping[str, str] | None = None,
    *,
    available: Callable[[], bool] = sbx.available,
    self_test: Callable[[str], str | None] = sbx.self_test,
    platform: str | None = None,
) -> Prepared:
    """Decide the run's sandbox status and, when it's ``enforced``, build and self-test the profile."""
    import sys

    env = os.environ if env is None else env
    on, source = switch(env)
    if not on:
        return Prepared("off", f"turned off ({source})")
    if not available():
        where = platform or sys.platform
        why = "no sandbox-exec" if where == "darwin" else f"no OS write sandbox for {where} yet"
        return Prepared("unavailable", why)
    if sbx.claude_sandbox_enabled(vault, env):
        return Prepared("conflict", "Claude Code's own Bash sandbox is on (sandbox.enabled), and the two can't nest")
    try:
        text = sbx.build_profile(vault, env)
    except sbx.ProfileError as e:
        return Prepared("failed", str(e))
    problem = self_test(text)
    if problem is not None:
        return Prepared("failed", f"self-test: {problem}")
    return Prepared("enforced", "sandbox-exec, self-test ok", text)

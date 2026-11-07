"""Whether a scheduled run is live in a vault, from its session lock rather than the environment (#595).

The commit hook used to decide "scheduled run" from the commit's environment
alone (``SCOUT_MODE``, ``SCOUT_READER``: :func:`scout.kb.reader.is_scheduled`),
so a run could commit with ``env -u SCOUT_MODE`` or ``SCOUT_MODE=`` and get the
owner's rules. Now the runner's session lock (``scripts/scout-lock.sh``) also
writes a run marker, ``.scout-logs/.scout-session.lock.run`` (``<pid> <mode>``),
for as long as the run holds the lock, and :func:`live_run` reads it. A run is
live while:

- the marker exists,
- the lock (``.scout-logs/.scout-session.lock``) names a live PID, and
- the marker names that PID, or none it can parse (fail closed: the runner
  writes it whole, so a garbled one was edited).

The lock alone isn't enough: ``scoutctl bootstrap`` takes the same lock for
the owner's ``/scout-update``. A marker left by a run killed before its EXIT
trap names a dead PID, or one that no longer holds the lock, so it isn't live.

The owner committing by hand while a run is live gets the run's rules for that
commit; the hook says so and names the run, and the owner commits again once
it ends. A run can still remove its marker; :func:`names_run_env` is the
PreToolUse gate's check (``scout.hooks.kb_read_gate``) that refuses the
obvious ways to do that, or to change its environment, and so leaves a trace.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

LOCK_NAME = ".scout-session.lock"
RUN_MARKER = LOCK_NAME + ".run"
UNKNOWN_MODE = "scheduled"
_MODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class LiveRun:
    pid: int
    mode: str


_DIGITS_RE = re.compile(r"^[0-9]+$")


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if _DIGITS_RE.match(text) else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive under another uid
    return True


def live_run(vault: Path) -> LiveRun | None:
    """The scheduled run holding ``vault``'s session lock now, or None."""
    logs = vault / ".scout-logs"
    try:
        marker = (logs / RUN_MARKER).read_text(encoding="utf-8", errors="replace")
        holder = _number((logs / LOCK_NAME).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if holder is None or holder <= 0 or not _alive(holder):
        return None
    fields = marker.split()
    marker_pid = _number(fields[0]) if fields else None
    if marker_pid is not None and marker_pid != holder:
        return None
    mode = fields[1] if marker_pid is not None and len(fields) > 1 and _MODE_RE.match(fields[1]) else UNKNOWN_MODE
    return LiveRun(pid=holder, mode=mode)


# A run's Bash command that changes the variables that mark it a run, clears its
# whole environment, or touches its session lock or marker. Reading the
# variables ($SCOUT_MODE, ${SCOUT_READER:-…}) is fine.
_RUN_ENV_RE = re.compile(
    r"(?<!\$)(?<!\$\{)SCOUT_(?:MODE|READER)(?![A-Za-z0-9_])"
    r"|\benv\b(?=[^;&|\n]*?\s(?:-[A-Za-z]*i[A-Za-z]*|--ignore-environment|-)(?:\s|$))"
    r"|" + re.escape(LOCK_NAME)
)


def names_run_env(command: str) -> bool:
    """Whether a run's shell command would change its run environment or its lock (see module doc)."""
    return _RUN_ENV_RE.search(command) is not None

"""``inline-comment`` engagement rows, derived from git on every read and never written anywhere.

The owner leaves a comment in a vault file by adding a line with ``//==<<`` or ``scout: user:``, then commits it in
an interactive session. Git already holds that fact, so a reader asks git instead of trusting a log a run could write.
One row per interactive commit, however many marker lines it adds."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from scout.changes.models import Signal
from scout.changes.scan import _git, lane_of

MARKERS = ("//==<<", "scout: user:")
_RS, _US = "\x1e", "\x1f"
# git's own date filters use the committer date; the exact author-date window is applied below.
_SLACK = timedelta(days=1)


def _adds_marker(patch: str) -> bool:
    """True when a ``-U0`` patch has an added content line holding a marker (never a ``+++`` file header)."""
    in_hunk = False
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+") and any(m in line for m in MARKERS):
            return True
    return False


def inline_comments(vault: Path, *, since: datetime, now: datetime) -> list[Signal]:
    """One ``inline-comment`` signal (surface ``kb``, ref = the commit sha, ts = its author time) per
    interactive-lane commit in ``[since, now]`` that adds a marker line. ``[]`` when git can't be read."""
    raw = _git(
        vault,
        "log",
        "--no-merges",
        "-G//==<<|scout: user:",
        f"--since={(since - _SLACK).isoformat()}",
        f"--until={(now + _SLACK).isoformat()}",
        f"--format={_RS}%H{_US}%aI{_US}%s",
        "-p",
        "-U0",
        "--",
    )
    out: list[Signal] = []
    for rec in (raw or "").split(_RS):
        head, _, patch = rec.partition("\n")
        try:
            sha, stamp, subject = head.split(_US, 2)
            ts = datetime.fromisoformat(stamp)
        except ValueError:  # the empty lead record, or one that doesn't parse
            continue
        if since <= ts <= now and lane_of(subject) == "interactive" and _adds_marker(patch):
            out.append(Signal(ts=ts, kind="inline-comment", surface="kb", ref=sha))
    return out


def merge_inline(signals: list[Signal], extra: list[Signal]) -> list[Signal]:
    """``signals`` plus each of ``extra`` whose ``(kind, ref)`` they don't already hold, so a stored row for the same
    commit never counts twice."""
    held = {(s.kind, s.ref) for s in signals}
    return [*signals, *(s for s in extra if (s.kind, s.ref) not in held)]

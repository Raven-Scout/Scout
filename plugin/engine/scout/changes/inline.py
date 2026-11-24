"""``inline-comment`` engagement rows, derived from git on every read and never written anywhere.

The owner leaves a comment in a vault file by adding a line with ``//==<<`` or ``scout: user:``, then commits it in
an interactive session. Git already holds that fact, so a reader asks git instead of trusting a log a run could write.
One row per interactive commit, however many marker lines it adds.

Two git calls keep this cheap on a vault with thousands of run commits a year: a listing of the window's commits over
Markdown files (no diffs), then the ``-U0`` diffs of just the interactive ones. A timeout is said on stderr once per
process ("inline comments not counted: git timed out"), never silently read as no comments."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from scout.changes.models import Signal
from scout.changes.scan import _git_timed, lane_of

MARKERS = ("//==<<", "scout: user:")
# The owner's comments live in vault notes: git reads Markdown files only.
PATHSPEC = ("*.md",)
# Commits per diff call, so a big window stays under ARG_MAX and the timeout.
DIFF_CHUNK = 200
TIMEOUT_WARNING = "inline comments not counted: git timed out"
_RS, _US = "\x1e", "\x1f"
# git's own date filters use the committer date; the exact author-date window is applied below.
_SLACK = timedelta(days=1)
_warned = False


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


def _timed_out() -> list[Signal]:
    global _warned
    if not _warned:
        _warned = True
        print(TIMEOUT_WARNING, file=sys.stderr)
    return []


def inline_comments(vault: Path, *, since: datetime, now: datetime) -> list[Signal]:
    """One ``inline-comment`` signal (surface ``kb``, ref = the commit sha, ts = its author time) per
    interactive-lane commit in ``[since, now]`` that adds a marker line to a Markdown file. ``[]`` when git can't be
    read (warned on stderr once when it timed out)."""
    listing, timed_out = _git_timed(
        vault,
        "log",
        "--no-merges",
        f"--since={(since - _SLACK).isoformat()}",
        f"--until={(now + _SLACK).isoformat()}",
        f"--format={_RS}%H{_US}%aI{_US}%s",
        "--",
        *PATHSPEC,
    )
    if timed_out:
        return _timed_out()
    stamps: dict[str, datetime] = {}
    for rec in (listing or "").split(_RS):
        try:
            sha, stamp, subject = rec.strip("\n").split(_US, 2)
            ts = datetime.fromisoformat(stamp)
        except ValueError:  # the empty lead record, or one that doesn't parse
            continue
        if since <= ts <= now and lane_of(subject) == "interactive":
            stamps[sha] = ts
    shas = list(stamps)
    out: list[Signal] = []
    for i in range(0, len(shas), DIFF_CHUNK):
        raw, timed_out = _git_timed(
            vault,
            "log",
            "--no-walk=unsorted",
            "-G//==<<|scout: user:",
            f"--format={_RS}%H",
            "-p",
            "-U0",
            *shas[i : i + DIFF_CHUNK],
            "--",
            *PATHSPEC,
        )
        if timed_out:
            return _timed_out()
        for rec in (raw or "").split(_RS):
            sha, _, patch = rec.partition("\n")
            if sha in stamps and _adds_marker(patch):
                out.append(Signal(ts=stamps[sha], kind="inline-comment", surface="kb", ref=sha))
    return out


def merge_inline(signals: list[Signal], extra: list[Signal]) -> list[Signal]:
    """``signals`` plus each of ``extra`` whose ``(kind, ref)`` they don't already hold, so a stored row for the same
    commit never counts twice."""
    held = {(s.kind, s.ref) for s in signals}
    return [*signals, *(s for s in extra if (s.kind, s.ref) not in held)]

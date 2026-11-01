"""*Deadline-Distance Ceiling*, checked instead of left to prose.

The rule (``phases/core/action-items.md``) caps a task's tier by how far
away its due date is: 30+ days out 🟢 at most, 7–30 🟡 at most, under 7
eligible for 🔴, and under 3 🔴 when the next step is the user's. Alarm words
never promote past the cap. Runs still put a 12-days-out task in 🔴 in 6 of
29 eval runs (hard-rule audit, 2026-10), so ``check_tier_ceiling`` holds
today's file to it before commit.

It computes the ceiling at check time, from today's file, rather than from a
pre-fetch: the ceiling depends only on the due date and today, and the tasks
it most often catches are ones this run created (a Linear issue with a due
date), which no pre-fetch of an earlier file can see. The due date is the
``due <date>`` in the body, read by :func:`scout.action_items.past_due.parse_due`;
when today's body lost it, the same task's body in the newest earlier daily
file stands in, and then the due date of a Linear issue the task names. A task
with no due date is out of scope, and a past-due one belongs to
``check-demotion``.

The pre-fetch does supply one list: ``linear_assigned_due`` in
``session-context.json`` (:mod:`scout.linear_activity`), the open Linear issues
assigned to the user that have a due date. Each must have a line in a tier
section. A first eval of this check left far-dated new issues out of every
tier: the cap gave them nowhere to go, and a task never written passes a cap.
Without a Linear key that list is absent and this part does nothing.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import paths
from scout.action_items.linear_delta import mentions
from scout.action_items.past_due import body_of, latest_daily, parse_due, sectioned_blocks
from scout.action_items.stale import _snoozed_past

RED, YELLOW, GREEN = "🔴", "🟡", "🟢"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}
_SECTION = {RED: "🔴 Urgent", YELLOW: "🟡 To Do", GREEN: "🟢 Watching"}
# A task line in any of these counts as tracking an issue: parked and completed ones too.
_TRACKED_IN = (RED, YELLOW, GREEN, "🅿️", "✅")
YELLOW_FROM = 7  # days out: 7–29 → 🟡 at most
GREEN_FROM = 30  # 30+ → 🟢 at most
RED_UNDER = 3  # under 3 days out and the user's move → 🔴
OVERRIDE_FORMAT = "- Ceiling override [#TAG] — <reason>"
_OVERRIDE = re.compile(
    r"^[-*]\s+Ceiling override\s+\[(?:#([A-Za-z0-9]{2,8})|([A-Za-z][A-Za-z0-9]*-\d+))\]\s+[—–-]\s*\S",
    re.IGNORECASE,
)
# A 🟡 task that only waits on someone else is a nudge, not the user's own move.
_NOT_MINE = re.compile(r"\bwaiting on\b|\bnudge\b", re.IGNORECASE)


def ceiling(days_out: int) -> str:
    """The highest tier a task due ``days_out`` days from today may sit in."""
    if days_out >= GREEN_FROM:
        return GREEN
    if days_out >= YELLOW_FROM:
        return YELLOW
    return RED


def users_move(section: str, body: str) -> bool:
    """🔴 and 🟡 are the user's move, unless the body says the task only waits on someone else."""
    return section in (RED, YELLOW) and not _NOT_MINE.search(body)


@dataclass(frozen=True)
class AssignedIssue:
    """A pre-fetched open Linear issue assigned to the user, with its due date."""

    identifier: str
    title: str
    due: _dt.date


def assigned_issues(raw: object) -> list[AssignedIssue] | None:
    """``linear_assigned_due`` from the context, or None when nothing was pre-fetched."""
    if not isinstance(raw, list):
        return None
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("identifier"):
            continue
        try:
            due = _dt.date.fromisoformat(str(entry.get("dueDate") or "")[:10])
        except ValueError:
            continue
        out.append(AssignedIssue(str(entry["identifier"]), str(entry.get("title") or ""), due))
    return out


@dataclass(frozen=True)
class Placement:
    """One open task with a future (or today's) due date, and where the rule allows it."""

    tag: str
    section: str
    due: _dt.date
    days_out: int
    ceiling: str
    due_from: str  # "body", "earlier file" (today's body has none) or the Linear issue it names
    mine: bool  # the next step is the user's (see users_move)

    @property
    def must_be_red(self) -> bool:
        """Under 3 days out and the user's move: 🔴, not 🟡."""
        return self.days_out < RED_UNDER and self.section == YELLOW and self.mine

    def problem(self) -> str | None:
        if _RANK[self.section] > _RANK[self.ceiling]:
            return f"in {self.section}, ceiling {self.ceiling} (due {_short(self.due)}, {self.days_out} days out)"
        if self.must_be_red:
            return f"in {YELLOW}, due {_short(self.due)} ({self.days_out} days out) and your move: move it to {RED}"
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "section": self.section,
            "due": self.due.isoformat(),
            "days_out": self.days_out,
            "ceiling": self.ceiling,
            "due_from": self.due_from,
            "mine": self.mine,
        }


def _short(day: _dt.date) -> str:
    return f"{day:%b} {day.day}"


def placements(
    blocks: Mapping[str, tuple[str, list[str]]],
    *,
    today: _dt.date,
    earlier: Mapping[str, tuple[str, list[str]]] | None = None,
    linear: Sequence[AssignedIssue] = (),
) -> list[Placement]:
    """Each open 🔴/🟡/🟢 task with a due date of today or later.

    A task with no due date, a past-due one (``check-demotion``'s), or one
    snoozed past today is left out.
    """
    out: list[Placement] = []
    for tag, (section, block) in blocks.items():
        if section not in _RANK or _snoozed_past(list(block), today):
            continue
        due, due_from = parse_due(body_of(block[0]), today), "body"
        if due is None and earlier and tag in earlier:
            due, due_from = parse_due(body_of(earlier[tag][1][0]), today), "earlier file"
        if due is None:
            text = "\n".join(block)
            named = next((i for i in linear if mentions(i.identifier, text)), None)
            if named is not None:
                due, due_from = named.due, named.identifier
        if due is None or due < today:
            continue
        days = (due - today).days
        mine = users_move(section, body_of(block[0]))
        out.append(Placement(tag, section, due, days, ceiling(days), due_from, mine))
    return out


def tier_text(lines: Sequence[str]) -> str:
    """The lines under today's tier sections (🔴 🟡 🟢 🅿️ ✅): where a task line tracks an issue."""
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line[3:].strip().startswith(_TRACKED_IN)
        elif inside:
            out.append(line)
    return "\n".join(out)


def overrides(lines: Sequence[str]) -> set[str]:
    """Tags and issue ids with a ``- Ceiling override [#TAG] — <reason>`` line in today's 🪵 Run notes."""
    from scout.action_items.prefetched import run_note_entries

    out: set[str] = set()
    for entry in run_note_entries(lines):
        for line in entry:
            m = _OVERRIDE.match(line.strip())
            if m:
                out.add(m.group(1) or m.group(2).upper())
    return out


@dataclass(frozen=True)
class Finding:
    tag: str  # a task tag, or a Linear issue id when no task line names the issue
    problem: str
    untracked: bool = False

    @property
    def label(self) -> str:
        return f"[{self.tag}]" if self.untracked else f"[#{self.tag}]"


@dataclass(frozen=True)
class CeilingCheck:
    checked: tuple[Placement, ...]
    findings: tuple[Finding, ...]
    overridden: tuple[str, ...] = ()  # off the rule, with a reason in 🪵 Run notes
    assigned: tuple[AssignedIssue, ...] | None = None  # pre-fetched dated Linear issues; None: none fetched

    @property
    def passed(self) -> bool:
        return not self.findings


def _untracked(issue: AssignedIssue, today: _dt.date) -> str:
    days = (issue.due - today).days
    title = f' "{issue.title}"' if issue.title else ""
    return (
        f"Linear issue{title} is assigned to you, due {_short(issue.due)} ({days} days out), and no task line "
        f"names it: add it under {_SECTION[ceiling(days)]} or lower, "
        f"with `due {issue.due}` and `Refs: {issue.identifier}`"
    )


def judge(
    lines: Sequence[str],
    *,
    today: _dt.date,
    earlier: Mapping[str, tuple[str, list[str]]] | None = None,
    assigned: Sequence[AssignedIssue] | None = None,
) -> CeilingCheck:
    """Hold today's file (``lines``) to the ceiling, and each pre-fetched dated issue to a task line."""
    upcoming = [i for i in assigned or () if i.due >= today]
    checked = placements(sectioned_blocks(lines), today=today, earlier=earlier, linear=upcoming)
    allowed = overrides(lines)
    findings: list[Finding] = []
    overridden: list[str] = []
    for p in checked:
        problem = p.problem()
        if problem is None:
            continue
        if p.tag in allowed:
            overridden.append(p.tag)
        else:
            findings.append(Finding(p.tag, problem))
    tracked = tier_text(lines)
    for issue in upcoming:
        if mentions(issue.identifier, tracked):
            continue
        if issue.identifier.upper() in allowed:
            overridden.append(issue.identifier)
        else:
            findings.append(Finding(issue.identifier, _untracked(issue, today), untracked=True))
    return CeilingCheck(
        tuple(checked), tuple(findings), tuple(overridden), None if assigned is None else tuple(assigned)
    )


def _earlier_blocks(data_dir: Path, today: _dt.date) -> dict[str, tuple[str, list[str]]]:
    latest = latest_daily(data_dir, today - _dt.timedelta(days=1))
    if latest is None:
        return {}
    try:
        return sectioned_blocks(latest[1].read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return {}


def _prefetched(data_dir: Path | None, today: str, context: Path | None) -> list[AssignedIssue] | None:
    """The pre-fetched dated Linear issues, or None: no context today, or nothing fetched."""
    from scout.action_items.prefetched import PrefetchCheckError, context_path, load_context

    try:
        data = load_context(context_path(data_dir, context), today=today)
    except PrefetchCheckError:
        return None  # the ceiling part needs no pre-fetch; this part has nothing to check
    return assigned_issues(data.get("linear_assigned_due"))


def check_tier_ceiling(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> CeilingCheck:
    """Check today's file against each task's due-date ceiling and the pre-fetched dated issues."""
    from scout.action_items.prefetched import daily_lines

    lines = daily_lines(data_dir, today)
    day = _dt.date.fromisoformat(today)
    return judge(
        lines,
        today=day,
        earlier=_earlier_blocks(paths.data_dir(data_dir), day),
        assigned=_prefetched(data_dir, today, context),
    )

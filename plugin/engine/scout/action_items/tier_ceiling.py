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
file stands in. A task with no due date is out of scope, and a past-due one
belongs to ``check-demotion``.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import paths
from scout.action_items.past_due import body_of, latest_daily, parse_due, sectioned_blocks
from scout.action_items.stale import _snoozed_past

RED, YELLOW, GREEN = "🔴", "🟡", "🟢"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}
YELLOW_FROM = 7  # days out: 7–29 → 🟡 at most
GREEN_FROM = 30  # 30+ → 🟢 at most
RED_UNDER = 3  # under 3 days out and the user's move → 🔴
OVERRIDE_FORMAT = "- Ceiling override [#TAG] — <reason>"
_OVERRIDE = re.compile(r"^[-*]\s+Ceiling override\s+\[#([A-Za-z0-9]{2,8})\]\s+[—–-]\s*\S", re.IGNORECASE)
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
class Placement:
    """One open task with a future (or today's) due date, and where the rule allows it."""

    tag: str
    section: str
    due: _dt.date
    days_out: int
    ceiling: str
    from_earlier_file: bool  # today's body has no due date; an earlier file's did
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
            "from_earlier_file": self.from_earlier_file,
            "mine": self.mine,
        }


def _short(day: _dt.date) -> str:
    return f"{day:%b} {day.day}"


def placements(
    blocks: Mapping[str, tuple[str, list[str]]],
    *,
    today: _dt.date,
    earlier: Mapping[str, tuple[str, list[str]]] | None = None,
) -> list[Placement]:
    """Each open 🔴/🟡/🟢 task with a due date of today or later.

    A task with no due date, a past-due one (``check-demotion``'s), or one
    snoozed past today is left out.
    """
    out: list[Placement] = []
    for tag, (section, block) in blocks.items():
        if section not in _RANK or _snoozed_past(list(block), today):
            continue
        due = parse_due(body_of(block[0]), today)
        from_earlier = False
        if due is None and earlier and tag in earlier:
            due = parse_due(body_of(earlier[tag][1][0]), today)
            from_earlier = due is not None
        if due is None or due < today:
            continue
        days = (due - today).days
        mine = users_move(section, body_of(block[0]))
        out.append(Placement(tag, section, due, days, ceiling(days), from_earlier, mine))
    return out


def overrides(lines: Sequence[str]) -> set[str]:
    """Tags with a ``- Ceiling override [#TAG] — <reason>`` line in today's 🪵 Run notes."""
    from scout.action_items.prefetched import run_note_entries

    out: set[str] = set()
    for entry in run_note_entries(lines):
        for line in entry:
            m = _OVERRIDE.match(line.strip())
            if m:
                out.add(m.group(1))
    return out


@dataclass(frozen=True)
class Finding:
    tag: str
    problem: str


@dataclass(frozen=True)
class CeilingCheck:
    checked: tuple[Placement, ...]
    findings: tuple[Finding, ...]
    overridden: tuple[str, ...] = ()  # off the rule, with a reason in 🪵 Run notes

    @property
    def passed(self) -> bool:
        return not self.findings


def judge(
    lines: Sequence[str],
    *,
    today: _dt.date,
    earlier: Mapping[str, tuple[str, list[str]]] | None = None,
) -> CeilingCheck:
    """Hold today's file (``lines``) to the ceiling."""
    checked = placements(sectioned_blocks(lines), today=today, earlier=earlier)
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
    return CeilingCheck(tuple(checked), tuple(findings), tuple(overridden))


def _earlier_blocks(data_dir: Path, today: _dt.date) -> dict[str, tuple[str, list[str]]]:
    latest = latest_daily(data_dir, today - _dt.timedelta(days=1))
    if latest is None:
        return {}
    try:
        return sectioned_blocks(latest[1].read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return {}


def check_tier_ceiling(*, data_dir: Path | None = None, today: str) -> CeilingCheck:
    """Check today's file against each task's due-date ceiling."""
    from scout.action_items.prefetched import daily_lines

    lines = daily_lines(data_dir, today)
    day = _dt.date.fromisoformat(today)
    return judge(lines, today=day, earlier=_earlier_blocks(paths.data_dir(data_dir), day))

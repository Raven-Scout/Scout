"""The daily action-items file's size budget: settings and measurement.

The daily file is the user's board, read top to bottom every morning. Left
alone it grows: runs add tasks faster than they close them, park items in
place and write context into extra sub-bullets, until one vault's file sat at
300+ KB and 250 open tasks for weeks. ``scoutctl action-items fit`` keeps it
to a shape (``scout.action_items.fit``); this module says what that shape is
and measures how far a file is from it, for ``check-size`` and the doctor.

Two levels, both warnings, never a failed run:

- **target** (default 50 KB, 80 open tasks): where ``fit`` aims;
- **hard** (default 60 KB, 100 open tasks): past this the doctor warns too.

Settings live under ``action_items:`` in ``scout-config.yaml`` (defaults in
``scout/defaults/scout-config.yaml``). A bad value falls back to its default
with a one-line warning: a mangled vault file must never block a run.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from scout.action_items._common import _COMMENT_SUB_BULLET_RE
from scout.action_items.task_fields import match_field_line

FitMode = Literal["report", "apply"]
FIT_MODES: tuple[FitMode, ...] = ("report", "apply")

_BUDGET_FIELDS = ("target_bytes", "target_open", "hard_bytes", "hard_open", "prose_sub_bullets")
_TOP_FIELDS = ("backlog_due_days", "recently_completed_days")


@dataclass(frozen=True)
class Budget:
    target_bytes: int = 51200
    target_open: int = 80
    hard_bytes: int = 61440
    hard_open: int = 100
    prose_sub_bullets: int = 1


@dataclass(frozen=True)
class ActionItemsSettings:
    fit: FitMode = "report"
    backlog_due_days: int = 14
    recently_completed_days: int = 3
    budget: Budget = Budget()

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> ActionItemsSettings:
        block = cfg.get("action_items")
        if not isinstance(block, dict):
            return cls()
        out = cls()
        mode = block.get("fit")
        if mode is not None:
            if mode in FIT_MODES:
                out = replace(out, fit=cast(FitMode, mode))
            else:
                _warn(f"action_items.fit: expected report or apply, got {mode!r} — using report")
        for name in _TOP_FIELDS:
            value = _non_negative(block, name, f"action_items.{name}")
            if value is not None:
                out = replace(out, **cast(dict[str, Any], {name: value}))
        raw = block.get("budget")
        if isinstance(raw, dict):
            budget = out.budget
            for name in _BUDGET_FIELDS:
                value = _non_negative(raw, name, f"action_items.budget.{name}")
                if value is not None:
                    budget = replace(budget, **cast(dict[str, Any], {name: value}))
            out = replace(out, budget=budget)
        elif raw is not None:
            _warn(f"action_items.budget: expected a mapping, got {type(raw).__name__} — using defaults")
        return out


def _non_negative(block: dict[str, Any], name: str, where: str) -> int | None:
    if name not in block:
        return None
    raw = block[name]
    if isinstance(raw, bool):
        _warn(f"{where}: expected an integer, got {raw!r} — using default")
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _warn(f"{where}: expected an integer, got {raw!r} — using default")
        return None
    if value < 0:
        _warn(f"{where}: must be >= 0, got {value} — using default")
        return None
    return value


def load_settings(data_dir: Path | None = None) -> ActionItemsSettings:
    from scout import config as scout_config

    return ActionItemsSettings.from_config(scout_config.load_config(data_dir))


def _warn(msg: str) -> None:
    print(f"scout-config: {msg}", file=sys.stderr)


# ----- classifying an item's sub-bullets ------------------------------------

_OPEN_TOP = re.compile(r"^- \[ \]\s")
_TASK_LINE = re.compile(r"^\s*[-*]\s+\[[ xX]\]")
_SUB_BULLET = re.compile(r"^(?P<indent>\s+)[-*]\s")
_MARK_LINE = re.compile(r"^\s+[-*]\s+(?:estimate|block|actual|snoozed-until|refs)\s*:", re.IGNORECASE)


def is_machine_line(line: str) -> bool:
    """A sub-bullet the board needs under its item: a board field, a plan
    marker, a snooze, ``Refs:``, a person's comment, or a nested task."""
    if match_field_line(line) is not None or _MARK_LINE.match(line) or _TASK_LINE.match(line):
        return True
    # Any other `name: text` sub-bullet is a comment by the parser's rule: never prose to move.
    return _COMMENT_SUB_BULLET_RE.match(line) is not None


def sub_bullet_groups(block: list[str]) -> list[tuple[bool, list[str]]]:
    """A task block's children as ``(is_prose, lines)`` groups.

    A group is one sub-bullet at the block's shallowest child indent plus the
    deeper lines under it; text that is not a bullet joins the group above it.
    A group whose first line is a machine line (see ``is_machine_line``) is
    not prose.
    """
    children = list(block[1:])
    indents = [len(m.group("indent")) for ln in children if (m := _SUB_BULLET.match(ln))]
    if not indents:
        return [(False, children)] if children else []
    base = min(indents)
    groups: list[tuple[bool, list[str]]] = []
    for line in children:
        m = _SUB_BULLET.match(line)
        if m and len(m.group("indent")) == base:
            groups.append((not is_machine_line(line), [line]))
        elif groups:
            groups[-1][1].append(line)
        else:
            groups.append((False, [line]))
    return groups


# ----- measuring a daily file ------------------------------------------------


@dataclass(frozen=True)
class SizeReport:
    bytes: int
    lines: int
    open_tasks: int
    over_prose: tuple[str, ...]  # tags with more prose sub-bullets than the budget allows

    def level(self, budget: Budget) -> Literal["ok", "target", "hard"]:
        if self.bytes > budget.hard_bytes or self.open_tasks > budget.hard_open:
            return "hard"
        if self.bytes > budget.target_bytes or self.open_tasks > budget.target_open or self.over_prose:
            return "target"
        return "ok"

    def problems(self, budget: Budget) -> list[str]:
        out: list[str] = []
        kb = self.bytes / 1024
        if self.bytes > budget.hard_bytes:
            out.append(f"{kb:.1f} KB is over the hard limit of {budget.hard_bytes / 1024:.0f} KB")
        elif self.bytes > budget.target_bytes:
            out.append(f"{kb:.1f} KB is over the {budget.target_bytes / 1024:.0f} KB target")
        if self.open_tasks > budget.hard_open:
            out.append(f"{self.open_tasks} open tasks is over the hard limit of {budget.hard_open}")
        elif self.open_tasks > budget.target_open:
            out.append(f"{self.open_tasks} open tasks is over the target of {budget.target_open}")
        if self.over_prose:
            n = len(self.over_prose)
            allowed = budget.prose_sub_bullets
            tasks = "task" if n == 1 else "tasks"
            bullets = "sub-bullet" if allowed == 1 else "sub-bullets"
            out.append(
                f"{n} {tasks} with more than {allowed} prose {bullets}"
                " (context belongs in action-items/context/<TAG>.md)"
            )
        return out

    def to_json(self, budget: Budget) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "lines": self.lines,
            "open_tasks": self.open_tasks,
            "over_prose": list(self.over_prose),
            "level": self.level(budget),
            "problems": self.problems(budget),
            "budget": {
                "target_bytes": budget.target_bytes,
                "target_open": budget.target_open,
                "hard_bytes": budget.hard_bytes,
                "hard_open": budget.hard_open,
                "prose_sub_bullets": budget.prose_sub_bullets,
            },
        }


def count_open(lines: list[str]) -> int:
    """Top-level open tasks (``- [ ]`` at column 0), outside fenced code."""
    n = 0
    in_fence = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and _OPEN_TOP.match(line):
            n += 1
    return n


def measure(text: str, budget: Budget) -> SizeReport:
    from scout.action_items.stale import task_blocks

    lines = text.splitlines()
    over: list[str] = []
    for block in task_blocks(lines):
        prose = sum(is_prose for is_prose, _ in sub_bullet_groups(lines[block.start : block.end]))
        if prose > budget.prose_sub_bullets and block.tag not in over:
            over.append(block.tag)
    return SizeReport(
        bytes=len(text.encode("utf-8")),
        lines=len(lines),
        open_tasks=count_open(lines),
        over_prose=tuple(over),
    )

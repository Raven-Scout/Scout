"""Per-item context notes: `action-items/context/<TAG>.md`.

An action item in the daily file is one line. Its context — status,
evidence, quotes, links — lives in its own note, which the apps read into
Full context / Launch Claude. `backfill_context` restores notes, verbatim,
from the pre-restructure snapshot of a daily file (the one-time conversion to
one-line items moved everything but the first sub-bullet there).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scout.ids import leading_prefix_pattern
from scout.kb.lint_config import load_lint_config

_TASK = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RETAG = re.compile(r"re-tagged from `?\[#([A-Z0-9]{2,8})\]`?")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def context_note_rel(tag: str) -> str:
    return f"action-items/context/{tag}.md"


def _tag_of(rest: str) -> str | None:
    m = leading_prefix_pattern().match(rest)
    return m.group(1) if m else None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def snapshot_blocks(text: str) -> dict[str, list[str]]:
    """Each tagged task's block: its line plus every following blank or
    more-indented line. Nested tagged tasks get their own sub-block and stay
    in their parent's. First occurrence of a tag wins."""
    blocks: dict[str, list[str]] = {}
    active: list[tuple[str, int]] = []
    for line in text.split("\n"):
        if not line.strip():
            for tag, _ in active:
                blocks[tag].append(line)
            continue
        ind = _indent(line)
        active = [(t, i) for t, i in active if i < ind]
        for tag, _ in active:
            blocks[tag].append(line)
        m = _TASK.match(line)
        if m:
            new_tag = _tag_of(m.group(3))
            if new_tag is not None and new_tag not in blocks:
                blocks[new_tag] = [line]
                active.append((new_tag, ind))
    for lines in blocks.values():
        while lines and not lines[-1].strip():
            lines.pop()
    return blocks


def render_note(tag: str, title: str, day: str, block: list[str]) -> str:
    return (
        f"---\ntag: {tag}\ntitle: {json.dumps(title, ensure_ascii=False)}\n"
        f"created: {day}\nupdated: {day}\n---\n\n"
        f"Restored verbatim from the {day} pre-restructure snapshot.\n\n" + "\n".join(block) + "\n"
    )


@dataclass
class BackfillContextReport:
    to_write: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    over_budget: list[tuple[str, int]] = field(default_factory=list)


def _plain_title(rest: str) -> str:
    """The bold title after the `[#TAG]`, or the rest of the line if unbolded."""
    rest = leading_prefix_pattern().sub("", rest, count=1).strip()
    m = _BOLD.search(rest)
    return (m.group(1) if m else rest).strip()


def _open_items(path: Path) -> list[tuple[str, str, str]]:
    """(tag, title, raw line) for every open tagged task line in `path`,
    nested child tasks included. Scans lines directly: `parse_lines` folds an
    indented `- [ ]` child into its parent's details, so it would miss them."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TASK.match(line)
        if m is None or m.group(2) != " ":
            continue
        tag = _tag_of(m.group(3))
        if tag is not None:
            out.append((tag, _plain_title(m.group(3)), line))
    return out


def backfill_context(*, data_dir: Path, snapshot: Path, daily: Path, write: bool) -> BackfillContextReport:
    blocks = snapshot_blocks(snapshot.read_text(encoding="utf-8"))
    m = _DATE.search(snapshot.name)
    day = m.group(1) if m else ""
    cfg = load_lint_config(data_dir)
    report = BackfillContextReport()
    seen: set[str] = set()
    for tag, title, raw in _open_items(daily) + _open_items(data_dir / "action-items" / "backlog.md"):
        if tag in seen:
            continue
        seen.add(tag)
        rel = context_note_rel(tag)
        path = data_dir / rel
        if path.exists():
            report.existing.append(tag)
            continue
        block = blocks.get(tag)
        if block is None and (rt := _RETAG.search(raw)):
            block = blocks.get(rt.group(1))
        if block is None:
            report.missing.append(tag)
            continue
        text = render_note(tag, title, day, block)
        size = len(text.encode("utf-8"))
        budget = cfg.budget_for(rel)
        if budget is not None and size > budget:
            report.over_budget.append((tag, size))
        report.to_write.append(tag)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return report

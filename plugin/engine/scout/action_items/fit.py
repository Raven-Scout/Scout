"""``scoutctl action-items fit``: keep the daily file to its budget, deterministically.

The daily action-items file is the user's board. Runs keep it honest item by
item but nothing kept its *shape*: tasks arrive faster than they close, past
due tasks park in place, Watching items and far-off 🟡 tasks sit beside today's
work, and context piles up in extra sub-bullets. A one-off restructure can bring
a file back to its budget; with nothing holding that shape it grows back to
several times its budget within weeks.

``fit`` is that hold. No model is involved, and no open task is ever deleted:

1. **Backlog.** 🅿️ Parked tasks move to ``action-items/backlog.md``, and so do
   🟢 Watching and 🟡 tasks with no due date or one due more than
   ``backlog_due_days`` (14) out. A backlog 🟡 or 🟢 task that has come due
   within that window moves back into its section. 🔴 and every other section
   stay.
2. **Cap.** If more than ``budget.target_open`` (80) tasks are still open, the
   bottom of 🟡 moves to the backlog until it fits. The phase orders 🟡 by
   priority, so the bottom is the least pressing.
3. **Context.** A task keeps ``budget.prose_sub_bullets`` (1) prose
   sub-bullet; the rest move, verbatim, to the end of its context note
   (``action-items/context/<TAG>.md``, created if missing). A sub-bullet group
   holding any machine line (board field, plan marker, ``Refs:``, snooze,
   comment, nested task) or markup (an inline directive, an HTML comment,
   ``<details>``) always stays.
4. **Recently Completed** keeps ``recently_completed_days`` (3) days. Older
   completed (``- [x]`` or struck-through) entries are deleted from the daily
   file: git keeps them.
5. A ``🗂️ Backlog: [[backlog]] (N items)`` line under 🟡 says where the rest went.

**Safe to re-run.** A tag already open in the backlog is never appended twice:
a block moving there replaces the backlog's copy, merged so no line of either is
lost. A backlog copy of a task still in the daily file is dropped only when the
daily row already holds every one of its lines; otherwise both stay and the
result names the conflict. Prose already in a note is not appended again.

**Writes.** Everything is read and planned first; an unreadable backlog or
context note aborts before anything is written. Then the backlog, the notes,
the rewrite records and the daily file are written in that order, each
atomically through ``scout.safe_write``, so an interruption can leave a task in both files (which the next
run reconciles) but never in neither.

A moved block is the same text, so its stale-archive clock carries over
(``scout.action_items.stale``). A block whose prose moved is recorded as a
rewrite, like the title cleanup's, so that edit doesn't restart its clock.

``action_items.fit`` in ``scout-config.yaml`` decides whether it writes:
``report`` (the default) only records what it would do in
``.scout-logs/action-items-fit.json``; ``apply`` writes.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import paths, safe_write
from scout.action_items import stale
from scout.action_items.budget import ActionItemsSettings, count_open, sub_bullet_groups
from scout.action_items.past_due import History, body_of, parse_due
from scout.errors import ActionItemError

REPORT_NAME = "action-items-fit.json"
SESSION_LOCK = Path(".scout-logs") / ".scout-session.lock"
BACKLOG_LINE = "🗂️ Backlog: [[backlog]] ({n} {noun})"
_BACKLOG_LINE_RE = re.compile(r"^🗂️ Backlog: \[\[backlog\]\]")
_H2 = re.compile(r"^##\s")
_H3 = re.compile(r"^###\s")
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# "closed 2026-11-12", "✅ 2026-11-12": a close date. Never one that a "due" stands in front of.
_CLOSED_ON = re.compile(
    r"(?:closed|done|completed|finished|✅)(?:(?!\bdue\b)\D){0,12}(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)
_TOP_BULLET = re.compile(r"^[-*]\s")
# A completed entry: a ticked row, or a row struck through from its start.
_DONE_ENTRY = re.compile(r"^[-*]\s+(?:\[[xX]\]|~~)")
_OPEN_LINE = re.compile(r"^\s*[-*]\s+\[ \]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")

YELLOW, GREEN, PARKED, DONE = "🟡", "🟢", "🅿️", "✅"
# The backlog's sections, in order: section emoji → heading.
BACKLOG_SECTIONS = {
    YELLOW: "## 🟡 To Do (soon)",
    GREEN: "## 🟢 Watching (no action yet)",
    PARKED: "## 🅿️ Parked",
}
# A section created in the daily file for a task coming back from the backlog.
DAILY_HEADINGS = {YELLOW: "## 🟡 To Do", GREEN: "## 🟢 Watching"}
# Sections a new daily section goes before, in the phase's fixed order.
_DAILY_AFTER = {
    YELLOW: (GREEN, "📥", "🧹", PARKED, "🐾", "📅", DONE, "📋", "🪵"),
    GREEN: ("📥", "🧹", PARKED, "🐾", "📅", DONE, "📋", "🪵"),
}
_BACKLOG_HEADER = [
    "# Backlog",
    "",
    "Open action items that are not today's: 🟡 tasks not due soon, 🟢 Watching and 🅿️ Parked.",
    "`scoutctl action-items fit` moves them here from the daily file and back when they come due.",
]


@dataclass(frozen=True)
class Move:
    tag: str
    section: str  # where it was
    to: str  # "backlog" or "daily"
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"tag": self.tag, "from": self.section, "to": self.to, "reason": self.reason}


@dataclass
class FitResult:
    daily: list[str]
    backlog: list[str]
    moves: list[Move] = field(default_factory=list)
    # tag → prose sub-bullet lines moved to its context note
    to_notes: dict[str, list[str]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    completed_dropped: int = 0
    # Backlog copies dropped because another row of the same tag holds all their lines.
    deduped: list[str] = field(default_factory=list)
    # Tags open twice with differing text: left alone for a person to merge.
    conflicts: list[str] = field(default_factory=list)
    rewrites: list[stale.Rewrite] = field(default_factory=list)
    # The backlog with today's leavers added and promotions still in it: written before the daily file.
    backlog_staged: list[str] = field(default_factory=list)
    applied: bool = False
    bytes_before: int = 0
    bytes_after: int = 0
    open_before: int = 0
    open_after: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.moves or self.to_notes or self.completed_dropped or self.deduped)

    def summary(self) -> str:
        out = [f"{self.bytes_before / 1024:.1f} KB → {self.bytes_after / 1024:.1f} KB"]
        out.append(f"{self.open_before} → {self.open_after} open")
        to_backlog = sum(m.to == "backlog" for m in self.moves)
        back = sum(m.to == "daily" for m in self.moves)
        if to_backlog:
            out.append(f"{to_backlog} to the backlog")
        if back:
            out.append(f"{back} back from the backlog")
        if self.to_notes:
            n = sum(len(v) for v in self.to_notes.values())
            out.append(f"{n} context line{'s' if n != 1 else ''} from {len(self.to_notes)} tasks to their notes")
        if self.completed_dropped:
            out.append(f"{self.completed_dropped} old Recently Completed entries deleted (git keeps them)")
        if self.deduped:
            out.append(f"{len(self.deduped)} duplicate backlog copies dropped")
        if self.conflicts:
            out.append(f"{len(self.conflicts)} tags open twice with different text, left alone")
        return ", ".join(out)

    def to_json(self) -> dict[str, Any]:
        return {
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "open_before": self.open_before,
            "open_after": self.open_after,
            "moves": [m.to_json() for m in self.moves],
            "to_notes": {tag: len(lines) for tag, lines in sorted(self.to_notes.items())},
            "completed_dropped": self.completed_dropped,
            "deduped": sorted(self.deduped),
            "conflicts": sorted(self.conflicts),
        }


# ----- reading sections -------------------------------------------------------


def _section_of(heading: str) -> str:
    rest = heading[3:].strip()
    return rest.split(" ", 1)[0] if rest else ""


def _blocks_by_section(lines: list[str]) -> list[tuple[str, stale.TaskBlock]]:
    """Each top-level open tagged task with the emoji of the ``## `` section it is in."""
    starts = {b.start: b for b in stale.task_blocks(lines)}
    out: list[tuple[str, stale.TaskBlock]] = []
    section = ""
    for i, line in enumerate(lines):
        if _H2.match(line):
            section = _section_of(line)
        block = starts.get(i)
        if block is not None:
            out.append((section, block))
    return out


def _section_span(lines: list[str], emoji: str) -> tuple[int, int] | None:
    """(heading index, end index) of the first ``## <emoji>`` section."""
    for i, line in enumerate(lines):
        if _H2.match(line) and _section_of(line) == emoji:
            j = i + 1
            while j < len(lines) and not _H2.match(lines[j]):
                j += 1
            return i, j
    return None


def _insert_into_section(
    lines: list[str], emoji: str, heading: str, block: list[str], *, before: tuple[str, ...]
) -> list[str]:
    """``lines`` with ``block`` added to the ``emoji`` section's own rows (above any ``###``
    subgroup), creating the section (before the first section in ``before``, else at the end)."""
    span = _section_span(lines, emoji)
    if span is None:
        at = next((i for i, ln in enumerate(lines) if _H2.match(ln) and _section_of(ln) in before), len(lines))
        new = [heading, "", *block, ""]
        if at > 0 and lines[at - 1].strip():
            new = ["", *new]
        return lines[:at] + new + lines[at:]
    start, end = span
    at = next((i for i in range(start + 1, end) if _H3.match(lines[i])), end)
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    return lines[:at] + block + lines[at:]


# ----- the plan ---------------------------------------------------------------


def _due(block: list[str], today: _dt.date) -> _dt.date | None:
    return parse_due(body_of(block[0]), today)


def _title(line: str) -> str:
    m = _BOLD.search(line)
    return (m.group(1) if m else line).strip()


def _trim(block: list[str], keep: int) -> tuple[list[str], list[str]]:
    """``block`` with its prose sub-bullet groups past the first ``keep`` taken out, and the lines taken."""
    kept: list[str] = [block[0]]
    moved: list[str] = []
    prose = 0
    for is_prose, group in sub_bullet_groups(block):
        if is_prose:
            prose += 1
            if prose > keep:
                moved += group
                continue
        kept += group
    while moved and not moved[-1].strip():
        moved.pop()
    return kept, moved


def _merge(primary: list[str], other: list[str]) -> list[str]:
    """``primary`` plus every line of ``other`` it lacks, in ``other``'s order: nothing is lost.

    Callers merge only copies with the same task line (``other[0]``), so it is never dropped."""
    have = {ln.rstrip() for ln in primary}
    extra = [ln for ln in other[1:] if ln.strip() and ln.rstrip() not in have]
    return [*primary, *extra]


def _holds(container: list[str], block: list[str]) -> bool:
    """Whether ``container`` already has every non-blank line of ``block``."""
    have = {ln.rstrip() for ln in container}
    return all(ln.rstrip() in have for ln in block if ln.strip())


def _completed_day(entry: list[str], heading_day: _dt.date | None, history: History, today: _dt.date) -> _dt.date:
    m = _CLOSED_ON.search(entry[0])
    if m and _valid(m.group(1)):
        return _dt.date.fromisoformat(m.group(1))
    if heading_day is not None:
        return heading_day
    return history.first_seen(entry[0], upto=today)


def _drop_old_completed(lines: list[str], *, today: _dt.date, days: int, history: History) -> tuple[list[str], int]:
    """Delete completed entries older than ``days`` from ✅ Recently Completed. An entry
    holding any open ``- [ ]`` row, or one that isn't ``- [x]`` or struck through from
    its start, stays."""
    span = _section_span(lines, DONE)
    if span is None:
        return lines, 0
    start, end = span
    cutoff = today - _dt.timedelta(days=max(days, 1) - 1)
    remove: set[int] = set()
    heading_day: _dt.date | None = None
    dropped = 0
    i = start + 1
    while i < end:
        line = lines[i]
        if _H3.match(line):
            m = _ISO.search(line)
            heading_day = _dt.date.fromisoformat(m.group(1)) if m and _valid(m.group(1)) else None
            i += 1
            continue
        if not _TOP_BULLET.match(line):
            i += 1
            continue
        j = i + 1
        while j < end and lines[j][:1] in (" ", "\t") and lines[j].strip():
            j += 1
        entry = lines[i:j]
        # An open task (the entry's own row or a nested one) is never pruned, whatever it says.
        done = _DONE_ENTRY.match(entry[0]) and not any(_OPEN_LINE.match(ln) for ln in entry)
        if done and _completed_day(entry, heading_day, history, today) < cutoff:
            remove.update(range(i, j))
            dropped += 1
        i = j
    if not dropped:
        return lines, 0
    return stale._drop_emptied_subheadings(lines, remove), dropped


def _valid(iso: str) -> bool:
    try:
        _dt.date.fromisoformat(iso)
    except ValueError:
        return False
    return True


def _set_backlog_line(lines: list[str], n: int) -> list[str]:
    """``lines`` with one up-to-date backlog line at the top of 🟡 (added only when the backlog has items).

    An existing line is updated in place, so running again changes nothing."""
    text = BACKLOG_LINE.format(n=n, noun="item" if n == 1 else "items")
    found = [i for i, ln in enumerate(lines) if _BACKLOG_LINE_RE.match(ln)]
    if found and n:
        first = found[0]
        return [text if i == first else ln for i, ln in enumerate(lines) if i == first or i not in found]
    if found:
        # Take the blank line written above it along with it.
        drop = set(found) | {i - 1 for i in found if i > 0 and not lines[i - 1].strip()}
        return [ln for i, ln in enumerate(lines) if i not in drop]
    if n == 0:
        return lines
    span = _section_span(lines, YELLOW)
    if span is None:
        return _insert_into_section(lines, YELLOW, DAILY_HEADINGS[YELLOW], [text], before=_DAILY_AFTER[YELLOW])
    at = span[0] + 1
    return lines[:at] + ["", text] + lines[at:]


def _dedupe_backlog(backlog: list[str], daily_blocks: dict[str, list[str]], result: FitResult) -> list[str]:
    """The backlog without copies that add nothing.

    A copy goes when the daily row of its tag, or the backlog's own first row of
    it, already holds every line of it. A copy that differs is left alone and
    named in ``result.conflicts``.
    """
    blocks = stale.task_blocks(backlog)
    first: dict[str, stale.TaskBlock] = {}
    drop: set[int] = set()
    for block in blocks:
        body = backlog[block.start : block.end]
        if block.tag in daily_blocks:
            if _holds(daily_blocks[block.tag], body):
                drop.update(range(block.start, block.end))
                result.deduped.append(block.tag)
            elif block.tag not in result.conflicts:
                result.conflicts.append(block.tag)
            continue
        if block.tag in first:
            kept = first[block.tag]
            if _holds(backlog[kept.start : kept.end], body):
                drop.update(range(block.start, block.end))
                result.deduped.append(block.tag)
            elif block.tag not in result.conflicts:
                result.conflicts.append(block.tag)
            continue
        first[block.tag] = block
    return stale._drop_emptied_subheadings(backlog, drop) if drop else backlog


def plan_fit(
    daily: list[str],
    backlog: list[str],
    *,
    today: _dt.date,
    settings: ActionItemsSettings,
    history: History,
) -> FitResult:
    """What ``fit`` does to ``daily`` and ``backlog``, without touching disk."""
    budget = settings.budget
    horizon = today + _dt.timedelta(days=settings.backlog_due_days)
    result = FitResult(daily=list(daily), backlog=list(backlog))
    result.bytes_before = len("\n".join(daily).encode("utf-8")) + (1 if daily else 0)
    result.open_before = count_open(daily)

    # 1. Decide what leaves the daily file and why.
    leaving: dict[int, tuple[str, str]] = {}  # block start → (section, reason)
    yellow_kept: list[stale.TaskBlock] = []
    for section, block in _blocks_by_section(daily):
        due = _due(daily[block.start : block.end], today)
        if section == PARKED:
            leaving[block.start] = (PARKED, "parked")
        elif section in (YELLOW, GREEN):
            if due is None:
                leaving[block.start] = (section, "no due date")
            elif due > horizon:
                leaving[block.start] = (section, f"due {due.isoformat()}")
            elif section == YELLOW:
                yellow_kept.append(block)

    # 2. Reconcile copies, then pick backlog tasks now due within the window, soonest first.
    # A row under ✅ is history, never a task's live copy: it can't stand in for a backlog copy.
    daily_tags = {b.tag for b in stale.task_blocks(daily)}
    daily_blocks: dict[str, list[str]] = {}
    for section, block in _blocks_by_section(daily):
        if section != DONE:
            daily_blocks.setdefault(block.tag, daily[block.start : block.end])
    backlog = _dedupe_backlog(list(backlog), daily_blocks, result)
    coming: list[tuple[_dt.date, str, stale.TaskBlock]] = []
    seen: set[str] = set()
    for section, block in _blocks_by_section(backlog):
        if section not in (YELLOW, GREEN) or block.tag in daily_tags or block.tag in seen:
            continue
        if block.tag in result.conflicts:
            continue  # two differing copies: a person merges them first
        seen.add(block.tag)
        due = _due(backlog[block.start : block.end], today)
        if due is not None and due <= horizon:
            coming.append((due, section, block))
    coming.sort(key=lambda item: item[0])

    # 3. The cap: fewer promotions first, then the bottom of 🟡.
    open_now = result.open_before - len(leaving)
    room = budget.target_open - open_now
    promoted = coming[: max(room, 0)]
    over = open_now + len(promoted) - budget.target_open
    for block in reversed(yellow_kept):
        if over <= 0:
            break
        leaving[block.start] = (YELLOW, "over the open-task cap")
        over -= 1

    # 4. Build the new daily file: take the leavers out, trim what stays.
    blocks = {b.start: b for b in stale.task_blocks(daily)}
    remove: set[int] = set()
    to_backlog: list[tuple[str, str, list[str]]] = []
    for start in sorted(leaving):
        section, reason = leaving[start]
        block = blocks[start]
        body = _trim_and_record(result, daily[block.start : block.end], block.tag, budget.prose_sub_bullets, today)
        to_backlog.append((section, block.tag, body))
        remove.update(range(block.start, block.end))
        result.moves.append(Move(block.tag, section, "backlog", reason))
    new_daily = stale._drop_emptied_subheadings(daily, remove)
    new_daily = _drop_empty_section(new_daily, PARKED)
    out: list[str] = []
    starts = {b.start: b for b in stale.task_blocks(new_daily)}
    i = 0
    while i < len(new_daily):
        here = starts.get(i)
        if here is None:
            out.append(new_daily[i])
            i += 1
            continue
        out += _trim_and_record(result, new_daily[here.start : here.end], here.tag, budget.prose_sub_bullets, today)
        i = here.end
    new_daily = out

    # 5. Leavers join the backlog first. A tag already open there with the same task line is
    # merged in place; with a different task line both copies stay and the conflict is named.
    staged = list(backlog)
    if to_backlog and not staged:
        staged = list(_BACKLOG_HEADER)
    for section, tag, body in to_backlog:
        existing = next((b for b in stale.task_blocks(staged) if b.tag == tag), None)
        if existing is not None and staged[existing.start].rstrip() == body[0].rstrip():
            merged = _merge(body, staged[existing.start : existing.end])
            staged = staged[: existing.start] + merged + staged[existing.end :]
            if tag in result.conflicts:
                result.conflicts.remove(tag)  # same task line: every line of both copies is kept
            continue
        if existing is not None and tag not in result.conflicts:
            result.conflicts.append(tag)
        staged = _insert_into_section(staged, section, BACKLOG_SECTIONS[section], body, before=_backlog_after(section))

    # 6. Promotions join their section's rows; they leave the backlog only in the final copy, which
    # is written after the daily file (see ``fit``), so a failed write never loses one.
    promoted_tags: set[str] = set()
    for _, section, block in promoted:
        body = backlog[block.start : block.end]
        body = _trim_and_record(result, body, block.tag, budget.prose_sub_bullets, today)
        new_daily = _insert_into_section(
            new_daily, section, DAILY_HEADINGS[section], body, before=_DAILY_AFTER[section]
        )
        promoted_tags.add(block.tag)
        result.moves.append(Move(block.tag, section, "daily", "due within the window"))
    new_backlog = staged
    if promoted_tags:
        drop: set[int] = set()
        for block in stale.task_blocks(staged):
            if block.tag in promoted_tags:
                drop.update(range(block.start, block.end))
        new_backlog = stale._drop_emptied_subheadings(staged, drop)

    new_daily, result.completed_dropped = _drop_old_completed(
        new_daily, today=today, days=settings.recently_completed_days, history=history
    )
    new_daily = _set_backlog_line(new_daily, count_open(new_backlog))
    result.daily = new_daily
    result.backlog = new_backlog
    result.backlog_staged = staged
    result.bytes_after = len("\n".join(new_daily).encode("utf-8")) + (1 if new_daily else 0)
    result.open_after = count_open(new_daily)
    return result


def _backlog_after(section: str) -> tuple[str, ...]:
    order = list(BACKLOG_SECTIONS)
    return tuple(order[order.index(section) + 1 :])


def _drop_empty_section(lines: list[str], emoji: str) -> list[str]:
    span = _section_span(lines, emoji)
    if span is None:
        return lines
    start, end = span
    if any(ln.strip() for ln in lines[start + 1 : end]):
        return lines
    return lines[:start] + lines[end:]


def _trim_and_record(result: FitResult, block: list[str], tag: str, keep: int, today: _dt.date) -> list[str]:
    kept, moved = _trim(block, keep)
    if not moved:
        return block
    result.to_notes.setdefault(tag, []).extend(moved)
    result.titles.setdefault(tag, _title(block[0]))
    before = stale.fingerprints(block).get(tag)
    after = stale.fingerprints(kept).get(tag)
    if before and after and before != after:
        result.rewrites.append(stale.Rewrite(tag=tag, on=today, before=before, after=after))
    return kept


# ----- writing ----------------------------------------------------------------

_FRONT_UPDATED = re.compile(r"^updated:\s*.*$", re.MULTILINE)


def _dedent(moved: list[str]) -> list[str]:
    indent = min((len(ln) - len(ln.lstrip(" \t")) for ln in moved if ln.strip()), default=0)
    return [ln[indent:] if ln.strip() else "" for ln in moved]


def note_with_moved(existing: str | None, *, tag: str, title: str, day: str, moved: list[str]) -> str | None:
    """A context note with ``moved`` appended under a dated heading (a new note when
    ``existing`` is None), or None when the note already holds every moved line."""
    body = _dedent(moved)
    if existing is not None:
        have = {ln.rstrip() for ln in existing.splitlines()}
        if all(ln.rstrip() in have for ln in body if ln.strip()):
            return None
    section = [f"## Moved from the daily file ({day})", "", *body, ""]
    if existing is None:
        head = [
            "---",
            f"tag: {tag}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"created: {day}",
            f"updated: {day}",
            "---",
            "",
        ]
        return "\n".join(head + section)
    text = existing
    if text.startswith("---") and _FRONT_UPDATED.search(text.split("\n---", 1)[0]):
        front, rest = text.split("\n---", 1)
        text = _FRONT_UPDATED.sub(f"updated: {day}", front, count=1) + "\n---" + rest
    if not text.endswith("\n"):
        text += "\n"
    return text + "\n" + "\n".join(section)


def _write_text(target: Path, text: str) -> None:
    """Replace ``target`` whole through :mod:`scout.safe_write`: never through a planted link."""
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_write.atomic_write_text(target, text)


def report_path(data_dir: Path) -> Path:
    return paths.data_dir(data_dir) / ".scout-logs" / REPORT_NAME


def write_report(data_dir: Path, result: FitResult, *, day: _dt.date, mode: str, applied: bool) -> None:
    """The last fit, for the owner to review before turning ``apply`` on. Best-effort."""
    payload = {"day": day.isoformat(), "mode": mode, "applied": applied, **result.to_json()}
    try:
        _write_text(report_path(data_dir), json.dumps(payload, indent=1, sort_keys=True) + "\n")
    except OSError:
        pass


def write_error_report(data_dir: Path | None, *, day: _dt.date, error: BaseException) -> None:
    """A ``fit`` that failed, in the same report the owner reviews. Best-effort."""
    payload = {"day": day.isoformat(), "error": f"{type(error).__name__}: {error}"}
    try:
        _write_text(report_path(paths.data_dir(data_dir)), json.dumps(payload, indent=1, sort_keys=True) + "\n")
    except OSError:
        pass


@dataclass
class _Writes:
    notes: list[tuple[Path, str]]
    rewrites: str
    # (target, its raw text or None when new, the backlog with promotions still in it, the final backlog)
    backlog: tuple[Path, str | None, list[str], list[str]]


def _prepare(vault: Path, result: FitResult, *, day: _dt.date, raw_backlog: str | None) -> _Writes:
    """Read everything the writes depend on. Any read error but a missing note raises, before a write."""
    notes: list[tuple[Path, str]] = []
    folder = paths.action_items_dir(vault) / "context"
    for tag, moved in result.to_notes.items():
        path = folder / f"{tag}.md"
        try:
            existing: str | None = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        text = note_with_moved(existing, tag=tag, title=result.titles.get(tag, tag), day=day.isoformat(), moved=moved)
        if text is not None:
            notes.append((path, text))
    records = "".join(r.to_json() + "\n" for r in result.rewrites)
    if records:
        try:
            prior = stale.rewrites_path(vault).read_text(encoding="utf-8")
        except FileNotFoundError:
            prior = ""
        if prior and not prior.endswith("\n"):
            records = "\n" + records
        records = prior + records
    staged = result.backlog_staged or result.backlog
    return _Writes(
        notes=notes, rewrites=records, backlog=(stale.backlog_path(vault), raw_backlog, staged, result.backlog)
    )


def _write_backlog(target: Path, raw: str | None, lines: list[str]) -> None:
    if raw is None:
        _write_text(target, "\n".join(lines) + ("\n" if lines else ""))
    else:
        stale.write_lines_like(target, raw, lines)


def _write(vault: Path, writes: _Writes, *, daily: Path, raw_daily: str, daily_lines: list[str]) -> None:
    """Write in an order where an interruption anywhere leaves every open task in at least one file.

    1. The additive files: context notes, then the rewrite records.
    2. The backlog with today's leavers added and promotions still in it.
    3. The daily file (leavers out, promotions in).
    4. The backlog with the promotions taken out.

    Each write is atomic. A failure after 2 leaves leavers in both files and
    after 3 leaves promotions in both, which the next ``fit`` reconciles; no
    step removes a task from a file before it exists in the other.
    """
    for path, text in writes.notes:
        _write_text(path, text)
    if writes.rewrites:
        _write_text(stale.rewrites_path(vault), writes.rewrites)
    target, raw, staged, final = writes.backlog
    current = raw.splitlines() if raw is not None else []
    if staged != current:
        _write_backlog(target, raw, staged)
        raw = "\n".join(staged) + "\n" if raw is None else raw
    if daily_lines != raw_daily.splitlines():
        stale.write_lines_like(daily, raw_daily, daily_lines)
    if final != staged:
        _write_backlog(target, raw, final)


@contextmanager
def apply_lock(vault: Path) -> Iterator[None]:
    """Hold the vault's session lock for a manual apply, as ``retitle`` does.

    A run holds that lock for its whole session, so a ``fit`` the run itself
    starts goes ahead: see :func:`_started_by_the_run`. Anyone else is refused
    while a run is live, rather than racing its edits.
    """
    from scout.scripts.bootstrap_lock import LockBusyError, acquire_lock_with_wait, release_lock

    lock = vault / SESSION_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        acquire_lock_with_wait(lock, timeout_s=0)
    except LockBusyError as e:
        if _started_by_the_run(vault, lock):
            yield
            return
        raise ActionItemError("refused: a Scout session holds the vault's lock (a run is live); try again later") from e
    try:
        yield
    finally:
        release_lock(lock)


# A holder whose process started up to this long after its lock file was written still counts.
_LOCK_CLOCK_SLACK_S = 5


def _started_by_the_run(vault: Path, lock: Path) -> bool:
    """Whether this process runs under the run that holds the lock.

    All of: the lock names a holder, the run marker beside it names the same
    PID, that PID is one of this process's ancestors, and it has been alive at
    least as long as the lock file is old (so a PID reused after a crash isn't
    taken for the run). When ``ps`` can't be read, the answer is no.
    """
    import subprocess
    import time

    from scout.kb.live_run import _ancestry, _marker, lock_holder, process_table

    holder = lock_holder(vault)
    if holder is None or _marker(vault)[0] != holder:
        return False
    try:
        table = process_table()
        age = time.time() - lock.stat().st_mtime
    except (OSError, subprocess.SubprocessError, ValueError):
        return False  # can't tell: refuse
    proc = table.get(holder)
    if proc is None or proc.elapsed + _LOCK_CLOCK_SLACK_S < age:
        return False
    return holder in _ancestry(table, os.getpid())


def fit(
    *,
    data_dir: Path | None = None,
    date: _dt.date,
    apply: bool | None = None,
    settings: ActionItemsSettings | None = None,
) -> FitResult | None:
    """Fit the existing daily file for ``date``. ``apply`` None follows ``action_items.fit``.

    Returns None when there is no daily file for ``date``. An unreadable daily
    file, backlog or context note raises before anything is written.
    """
    from scout.action_items.budget import load_settings

    vault = paths.data_dir(data_dir)
    settings = settings or load_settings(vault)
    target = paths.action_items_daily_path(vault, date=date)
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8")
    raw_backlog = stale.read_backlog(vault)
    result = plan_fit(
        raw.splitlines(),
        raw_backlog.splitlines() if raw_backlog is not None else [],
        today=date,
        settings=settings,
        history=History(vault, overrides={date: raw.splitlines()}),
    )
    do_apply = settings.fit == "apply" if apply is None else apply
    # The backlog line's count can change with nothing moved (the stale archive took backlog items).
    writes = result.changed or result.daily != raw.splitlines()
    if do_apply and writes:
        planned = _prepare(vault, result, day=date, raw_backlog=raw_backlog)
        _write(vault, planned, daily=target, raw_daily=raw, daily_lines=result.daily)
        result.applied = True
    write_report(vault, result, day=date, mode=settings.fit, applied=result.applied)
    return result

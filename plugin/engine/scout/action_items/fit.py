"""``scoutctl action-items fit``: keep the daily file to its budget, deterministically.

The daily action-items file is the user's board. Runs keep it honest item by
item but nothing kept its *shape*: tasks arrive faster than they close, past
due tasks park in place, Watching items and far-off 🟡 tasks sit beside today's
work, and context piles up in extra sub-bullets. A one-off restructure got one
vault's file from 300 KB to 57 KB; with nothing holding that shape it was back
over 300 KB in a month.

``fit`` is that hold. No model is involved and nothing is deleted:

1. **Backlog.** 🅿️ Parked and 🟢 Watching tasks move to
   ``action-items/backlog.md``, and so does a 🟡 task with no due date or one due
   more than ``backlog_due_days`` (14) out. A backlog 🟡 task that has come due
   within that window moves back into 🟡. 🔴 and every other section stay.
2. **Cap.** If more than ``budget.target_open`` (80) tasks are still open, the
   bottom of 🟡 moves to the backlog until it fits. The phase orders 🟡 by
   priority, so the bottom is the least pressing.
3. **Context.** A task keeps ``budget.prose_sub_bullets`` (1) prose
   sub-bullet; the rest move, verbatim, to the end of its context note
   (``action-items/context/<TAG>.md``, created if missing). Board fields, plan
   markers, ``Refs:``, snoozes, comments and nested tasks always stay.
4. **Recently Completed** keeps ``recently_completed_days`` (3) days; older
   entries are dropped from the daily file (git keeps them).
5. A ``🗂️ Backlog: [[backlog]] (N items)`` line under 🟡 says where the rest went.

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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import paths
from scout.action_items import stale
from scout.action_items.budget import ActionItemsSettings, count_open, sub_bullet_groups
from scout.action_items.past_due import History, body_of, parse_due

REPORT_NAME = "action-items-fit.json"
BACKLOG_LINE = "🗂️ Backlog: [[backlog]] ({n} {noun})"
_BACKLOG_LINE_RE = re.compile(r"^🗂️ Backlog: \[\[backlog\]\]")
_H2 = re.compile(r"^##\s")
_H3 = re.compile(r"^###\s")
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_CLOSED_ON = re.compile(r"(?:closed|done|completed|finished|✅)\D{0,12}(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_TOP_BULLET = re.compile(r"^[-*]\s")
_BOLD = re.compile(r"\*\*(.+?)\*\*")

YELLOW, GREEN, PARKED, DONE = "🟡", "🟢", "🅿️", "✅"
# The backlog's sections, in order: section emoji → heading.
BACKLOG_SECTIONS = {
    YELLOW: "## 🟡 To Do (soon)",
    GREEN: "## 🟢 Watching (no action yet)",
    PARKED: "## 🅿️ Parked",
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
    rewrites: list[stale.Rewrite] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0
    open_before: int = 0
    open_after: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.moves or self.to_notes or self.completed_dropped)

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
            out.append(f"{self.completed_dropped} old Recently Completed entries dropped")
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
    """``lines`` with ``block`` appended to the end of the ``emoji`` section,
    creating it (before the first section in ``before``, else at the end) when missing."""
    span = _section_span(lines, emoji)
    if span is None:
        at = next((i for i, ln in enumerate(lines) if _H2.match(ln) and _section_of(ln) in before), len(lines))
        new = [heading, "", *block, ""]
        if at > 0 and lines[at - 1].strip():
            new = ["", *new]
        return lines[:at] + new + lines[at:]
    _, end = span
    at = end
    while at > span[0] + 1 and not lines[at - 1].strip():
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


def _completed_day(entry: list[str], heading_day: _dt.date | None, history: History, today: _dt.date) -> _dt.date:
    m = _CLOSED_ON.search(entry[0])
    if m:
        try:
            return _dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    if heading_day is not None:
        return heading_day
    return history.first_seen(entry[0], upto=today)


def _drop_old_completed(lines: list[str], *, today: _dt.date, days: int, history: History) -> tuple[list[str], int]:
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
        if _completed_day(entry, heading_day, history, today) < cutoff:
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
        return _insert_into_section(lines, YELLOW, "## 🟡 To Do", [text], before=_AFTER_YELLOW)
    at = span[0] + 1
    return lines[:at] + ["", text] + lines[at:]


# Sections a new 🟡 section goes before, in the phase's fixed order.
_AFTER_YELLOW = (GREEN, "📥", "🧹", PARKED, "🐾", "📅", DONE, "📋", "🪵")


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
        body = daily[block.start : block.end]
        if section == PARKED:
            leaving[block.start] = (PARKED, "parked")
        elif section == GREEN:
            leaving[block.start] = (GREEN, "watching")
        elif section == YELLOW:
            due = _due(body, today)
            if due is None:
                leaving[block.start] = (YELLOW, "no due date")
            elif due > horizon:
                leaving[block.start] = (YELLOW, f"due {due.isoformat()}")
            else:
                yellow_kept.append(block)

    # 2. Backlog 🟡 tasks now due within the window come back, soonest first.
    daily_tags = {b.tag for b in stale.task_blocks(daily)}
    coming: list[tuple[_dt.date, stale.TaskBlock]] = []
    for section, block in _blocks_by_section(backlog):
        if section != YELLOW or block.tag in daily_tags:
            continue
        due = _due(backlog[block.start : block.end], today)
        if due is not None and due <= horizon:
            coming.append((due, block))
    coming.sort(key=lambda pair: pair[0])

    # 3. The cap: fewer promotions first, then the bottom of 🟡.
    open_now = result.open_before - len(leaving)
    room = budget.target_open - open_now
    promoted = [b for _, b in coming[: max(room, 0)]]
    over = open_now + len(promoted) - budget.target_open
    for block in reversed(yellow_kept):
        if over <= 0:
            break
        leaving[block.start] = (YELLOW, "over the open-task cap")
        over -= 1

    # 4. Build the new files.
    blocks = {b.start: b for b in stale.task_blocks(daily)}
    remove: set[int] = set()
    to_backlog: list[tuple[str, list[str]]] = []
    for start in sorted(leaving):
        section, reason = leaving[start]
        block = blocks[start]
        body = daily[block.start : block.end]
        body = _trim_and_record(result, body, block.tag, budget.prose_sub_bullets, today)
        to_backlog.append((section, body))
        remove.update(range(block.start, block.end))
        result.moves.append(Move(block.tag, section, "backlog", reason))
    new_daily = stale._drop_emptied_subheadings(daily, remove)
    new_daily = _drop_empty_section(new_daily, PARKED)

    # Trim what stays.
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

    # Promotions leave the backlog and join the bottom of 🟡.
    new_backlog = list(backlog)
    if promoted:
        drop: set[int] = set()
        for block in promoted:
            body = backlog[block.start : block.end]
            body = _trim_and_record(result, body, block.tag, budget.prose_sub_bullets, today)
            new_daily = _insert_into_section(new_daily, YELLOW, "## 🟡 To Do", body, before=_AFTER_YELLOW)
            drop.update(range(block.start, block.end))
            result.moves.append(Move(block.tag, YELLOW, "daily", "due within the window"))
        new_backlog = [ln for k, ln in enumerate(new_backlog) if k not in drop]
    if to_backlog:
        if not new_backlog:
            new_backlog = list(_BACKLOG_HEADER)
        for section, body in to_backlog:
            new_backlog = _insert_into_section(
                new_backlog, section, BACKLOG_SECTIONS[section], body, before=_backlog_after(section)
            )

    new_daily, result.completed_dropped = _drop_old_completed(
        new_daily, today=today, days=settings.recently_completed_days, history=history
    )
    new_daily = _set_backlog_line(new_daily, count_open(new_backlog))
    result.daily = new_daily
    result.backlog = new_backlog
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


def note_with_moved(existing: str | None, *, tag: str, title: str, day: str, moved: list[str]) -> str:
    """A context note with ``moved`` appended under a dated heading (a new note when ``existing`` is None)."""
    indent = min((len(ln) - len(ln.lstrip(" \t")) for ln in moved if ln.strip()), default=0)
    body = [ln[indent:] if ln.strip() else "" for ln in moved]
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


def report_path(data_dir: Path) -> Path:
    return paths.data_dir(data_dir) / ".scout-logs" / REPORT_NAME


def write_report(data_dir: Path, result: FitResult, *, day: _dt.date, mode: str, applied: bool) -> None:
    """The last fit, for the owner to review before turning ``apply`` on. Best-effort."""
    target = report_path(data_dir)
    payload = {"day": day.isoformat(), "mode": mode, "applied": applied, **result.to_json()}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def apply_side_files(data_dir: Path, result: FitResult, *, day: _dt.date) -> None:
    """Write the backlog, the context notes and the rewrite records ``result`` calls for."""
    vault = paths.data_dir(data_dir)
    raw_backlog = stale.read_backlog(vault)
    if result.backlog != (raw_backlog.splitlines() if raw_backlog is not None else []):
        target = stale.backlog_path(vault)
        target.parent.mkdir(parents=True, exist_ok=True)
        stale.write_lines_like(target, raw_backlog if raw_backlog is not None else "\n", result.backlog)
    notes = paths.action_items_dir(vault) / "context"
    for tag, moved in result.to_notes.items():
        path = notes / f"{tag}.md"
        try:
            existing: str | None = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        text = note_with_moved(existing, tag=tag, title=result.titles.get(tag, tag), day=day.isoformat(), moved=moved)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    if result.rewrites:
        target = stale.rewrites_path(vault)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write("".join(r.to_json() + "\n" for r in result.rewrites))


def fit(
    *,
    data_dir: Path | None = None,
    date: _dt.date,
    apply: bool | None = None,
    settings: ActionItemsSettings | None = None,
) -> FitResult | None:
    """Fit the existing daily file for ``date``. ``apply`` None follows ``action_items.fit``.

    Returns None when there is no daily file for ``date``.
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
        apply_side_files(vault, result, day=date)
        stale.write_lines_like(target, raw, result.daily)
    write_report(vault, result, day=date, mode=settings.fit, applied=do_apply and writes)
    return result

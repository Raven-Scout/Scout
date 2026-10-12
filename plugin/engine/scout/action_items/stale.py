"""Auto-archive open action items nobody has touched for STALE_AFTER_DAYS.

Every daily file starts as a verbatim copy of the previous one (materialize),
and the session prompts forbid dropping an open item without an explicit
directive. Nothing ever retired an item, so the open list only grew — one
vault went from 154 open rows to 944 in two months, 59% of them older than a
month and most byte-identical since they were written. A list that long is
unreadable, and rendering it froze scout-app.

This module is the one sanctioned exit. An item is *touched* when the text of
its block (the task line plus its indented sub-bullets) changes — a comment,
a snooze, a status note, a rewrite. Moving it between sections is not a
touch: demotion is exactly what happens to items nobody is working. Neither is
an edit that only changes dates, times or ages ("now 5 days overdue", a fresh
`_re-anchored <date>_` stamp): that is a run re-anchoring an item, not anyone
working it, and it kept overdue items alive forever (#372). Once an
item's block has been unchanged for STALE_AFTER_DAYS it moves, verbatim, to
``action-items/archive/stale-items-YYYY-MM.md`` with a dated note. Nothing is
deleted; bringing an item back is copying its block into today's file, which
restarts its clock.

When a block last changed comes from an activity ledger that replays the
daily files in date order. The ledger is a cache — rebuilt from the archived
daily files whenever it is missing — so it lives under ``.scout-cache/``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from scout import paths
from scout.action_items.task_fields import match_field_line
from scout.action_items.writer import atomic_write_lines
from scout.ids import leading_prefix_pattern

STALE_AFTER_DAYS = 60

# 2: hashes fold dates and ages out of the text (#372). A v1 ledger is rebuilt.
_LEDGER_VERSION = 2
_DAILY_NAME_RE = re.compile(r"^action-items-(\d{4}-\d{2}-\d{2})\.md$")
_OPEN_TASK_RE = re.compile(r"^- \[ \]\s+")
_SNOOZE_RE = re.compile(r"snoozed-until:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s")
_SUBHEADING_RE = re.compile(r"^###\s")

_DATE_TOKEN = "\x00date\x00"
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?"
)
_ORDINAL = r"\d{1,2}(?:st|nd|rd|th)?"
_AGE_UNIT = (
    r"(?:(?:working|business)\s+)?"
    r"(?:days?|d|weeks?|wks?|w|hours?|hrs?|h|minutes?|mins?|months?|mos?|years?|yrs?)"
)
# Applied in order; each match becomes _DATE_TOKEN. Month and weekday names are
# case-sensitive so "may" and "sun" in prose are left alone.
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]m)?\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(rf"(?:\b{_ORDINAL}\s+)?\b{_MONTH}(?:\s+{_ORDINAL}\b)?(?:,?\s+\d{{4}}\b)?"),
    re.compile(
        r"\b(?:Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:rs?|rsday)?|Fri(?:day)?"
        r"|Sat(?:urday)?|Sun(?:day)?)\b\.?"
    ),
    re.compile(r"\b(?:today|tonight|tomorrow|yesterday)\b", re.IGNORECASE),
    re.compile(rf"\b\d+(?:\.\d+)?\s*-?\s*{_AGE_UNIT}\b", re.IGNORECASE),
    re.compile(r"\b(?:day|week)\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bT[-+]\d+\b"),
)
# "Mon 2026-10-12 08:05" is one date, however many tokens it took.
_DATE_RUN_RE = re.compile(rf"{_DATE_TOKEN}(?:[\s,]*{_DATE_TOKEN})+")


@dataclass(frozen=True)
class TaskBlock:
    """A top-level open task and its indented continuation: lines[start:end]."""

    tag: str
    start: int
    end: int


@dataclass(frozen=True)
class ArchivedItem:
    tag: str
    last_changed: _dt.date
    lines: list[str]


@dataclass(frozen=True)
class SplitResult:
    lines: list[str]
    archived: list[ArchivedItem]


def _is_indented(line: str) -> bool:
    return line[:1] in (" ", "\t") and bool(line.strip())


def task_blocks(lines: list[str]) -> list[TaskBlock]:
    """Every top-level open `- [ ] [#TAG]` task, with the lines it owns.

    A block runs through the indented lines below its task line, across a
    blank line when indented content resumes after it (nested `<details>`,
    split note blocks). Nested tasks belong to their parent's block. Rows in
    fenced code or inside an HTML comment (the files keep deleted rows there
    as an audit trail) are not live and are skipped; so are untagged rows,
    which the ledger has no identity to track.
    """
    blocks: list[TaskBlock] = []
    in_fence = False
    in_comment = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if in_comment:
            in_comment = "-->" not in line
            i += 1
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
            i += 1
            continue

        opener = _OPEN_TASK_RE.match(line)
        tag_match = leading_prefix_pattern().match(line[opener.end() :]) if opener else None
        if tag_match is None:
            i += 1
            continue

        end = i + 1
        j = i + 1
        while j < len(lines):
            if _is_indented(lines[j]):
                j += 1
                end = j
            elif not lines[j].strip():
                k = j
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k < len(lines) and _is_indented(lines[k]):
                    j = k
                else:
                    break
            else:
                break
        blocks.append(TaskBlock(tag=tag_match.group(1), start=i, end=end))
        i = end
    return blocks


def _without_dates(line: str) -> str:
    """`line` with every date, time and age folded into one placeholder.

    Runs re-anchor overdue items — "due Sep 22, now 4 days overdue" becomes
    "now 5 days overdue", or a `_re-anchored <date>_` note gets a new stamp —
    and that is not the item being worked on (#372). Other numbers (a PR
    number, an amount, "3 of 5 done") still count. A snooze date is kept as
    written: re-snoozing is a decision.
    """
    out: list[str] = []
    pos = 0
    for m in _SNOOZE_RE.finditer(line):
        out += [_fold_dates(line[pos : m.start()]), m.group(0)]
        pos = m.end()
    out.append(_fold_dates(line[pos:]))
    return "".join(out)


def _fold_dates(text: str) -> str:
    for pattern in _DATE_PATTERNS:
        text = pattern.sub(_DATE_TOKEN, text)
    return _DATE_RUN_RE.sub(_DATE_TOKEN, text)


def _fingerprints(lines: list[str]) -> dict[str, str]:
    """Tag → digest of its block text. Rows sharing a tag hash together.

    Board-field lines (`- Priority:`, `- Cycle:`, …) are left out: a run
    refreshing them from Linear is not the item being worked on. Dates and
    ages are folded out of the rest (see `_without_dates`).
    """
    parts: dict[str, list[str]] = {}
    for b in task_blocks(lines):
        parts.setdefault(b.tag, []).extend(
            _without_dates(ln.rstrip()) for ln in lines[b.start : b.end] if ln.strip() and match_field_line(ln) is None
        )
    return {tag: hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() for tag, body in parts.items()}


def daily_files(data_dir: Path) -> list[tuple[_dt.date, Path]]:
    """Every dated daily file, live and archived, oldest first."""
    root = paths.action_items_dir(data_dir)
    found: dict[_dt.date, Path] = {}
    for folder in (root / "archive", root):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            m = _DAILY_NAME_RE.match(path.name)
            if m:
                found[_dt.date.fromisoformat(m.group(1))] = path
    return sorted(found.items())


class ActivityLedger:
    """When each open item's block last changed, by tag."""

    def __init__(self, path: Path, items: dict[str, dict[str, str]], seen_through: _dt.date | None) -> None:
        self.path = path
        self._items = items
        self.seen_through = seen_through

    @staticmethod
    def path_for(data_dir: Path) -> Path:
        return paths.cache_dir(data_dir) / "action-item-activity.json"

    @classmethod
    def load(cls, data_dir: Path) -> ActivityLedger:
        path = cls.path_for(data_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("version") != _LEDGER_VERSION:
                raise ValueError("ledger version mismatch")
            seen = raw.get("seen_through")
            return cls(path, dict(raw["items"]), _dt.date.fromisoformat(seen) if seen else None)
        except (OSError, ValueError, KeyError, TypeError):
            # Missing or unreadable: rebuild from the daily files.
            return cls(path, {}, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _LEDGER_VERSION,
            "seen_through": self.seen_through.isoformat() if self.seen_through else None,
            "items": self._items,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def last_changed(self, tag: str) -> _dt.date | None:
        entry = self._items.get(tag)
        return _dt.date.fromisoformat(entry["since"]) if entry else None

    def unchanged(self, tag: str, digest: str | None) -> bool:
        """Whether `digest` is the text the ledger last saw for `tag`."""
        entry = self._items.get(tag)
        return entry is not None and entry["hash"] == digest

    def observe(self, lines: list[str], on: _dt.date) -> None:
        """Fold one day's final file in. A tag absent that day is forgotten, so
        an item that comes back later counts as new from the day it returns."""
        current = _fingerprints(lines)
        updated: dict[str, dict[str, str]] = {}
        for tag, digest in current.items():
            prior = self._items.get(tag)
            if prior is not None and prior["hash"] == digest:
                updated[tag] = prior
            else:
                updated[tag] = {"hash": digest, "since": on.isoformat()}
        self._items = updated
        self.seen_through = on

    def catch_up(self, data_dir: Path, before: _dt.date) -> None:
        """Replay every daily file dated after `seen_through` and before `before`."""
        for day, path in daily_files(data_dir):
            if day >= before or (self.seen_through is not None and day <= self.seen_through):
                continue
            self.observe(path.read_text(encoding="utf-8").splitlines(), on=day)


def _snoozed_past(lines: list[str], today: _dt.date) -> bool:
    for line in lines:
        m = _SNOOZE_RE.search(line)
        if m:
            try:
                if _dt.date.fromisoformat(m.group(1)) > today:
                    return True
            except ValueError:
                continue
    return False


def split_stale(
    lines: list[str],
    ledger: ActivityLedger,
    *,
    today: _dt.date,
    max_age_days: int = STALE_AFTER_DAYS,
) -> SplitResult:
    """Separate the blocks unchanged for `max_age_days` from the rest of `lines`.

    A block whose text differs from what the ledger last saw was touched
    today, and one the ledger has never seen is new — neither is stale. So is
    anything snoozed past today: a snooze is a decision to park it.
    """
    known = _fingerprints(lines)
    cutoff = today - _dt.timedelta(days=max_age_days)
    remove: set[int] = set()
    archived: list[ArchivedItem] = []
    for block in task_blocks(lines):
        since = ledger.last_changed(block.tag)
        if since is None or since > cutoff or not ledger.unchanged(block.tag, known.get(block.tag)):
            continue
        body = lines[block.start : block.end]
        if _snoozed_past(body, today):
            continue
        archived.append(ArchivedItem(tag=block.tag, last_changed=since, lines=list(body)))
        remove.update(range(block.start, block.end))
        # Take the blank line after the block with it, so removal leaves no gap.
        nxt = block.end
        if nxt < len(lines) and not lines[nxt].strip() and (block.start == 0 or not lines[block.start - 1].strip()):
            remove.add(nxt)

    if not archived:
        return SplitResult(lines=list(lines), archived=[])
    return SplitResult(lines=_drop_emptied_subheadings(lines, remove), archived=archived)


def _drop_emptied_subheadings(lines: list[str], remove: set[int]) -> list[str]:
    """Apply `remove`, also dropping any `###` heading whose content it emptied.

    Sessions open a `### From this run …` subsection per run, so archiving a
    run's last items would otherwise leave a trail of bare headings. Only
    headings that lost content here go: an already-empty one is left alone.
    """
    drop = set(remove)
    i = 0
    while i < len(lines):
        if not _SUBHEADING_RE.match(lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not _HEADING_RE.match(lines[j]):
            j += 1
        span = range(i + 1, j)
        lost = any(k in remove for k in span)
        left = any(lines[k].strip() for k in span if k not in remove)
        if lost and not left:
            drop.update(range(i, j))
        i = j
    return [ln for k, ln in enumerate(lines) if k not in drop]


def archive_path(data_dir: Path, today: _dt.date) -> Path:
    return paths.action_items_dir(data_dir) / "archive" / f"stale-items-{today:%Y-%m}.md"


def write_archive(
    data_dir: Path, today: _dt.date, items: list[ArchivedItem], *, max_age_days: int = STALE_AFTER_DAYS
) -> Path:
    """Append `items` to this month's stale-items file, verbatim plus a dated note."""
    target = archive_path(data_dir, today)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    if not existing:
        existing = [
            f"# Auto-archived action items — {today:%Y-%m}",
            "",
            f"Open items whose text did not change for {max_age_days}+ days, moved out of the daily",
            "file by `scoutctl action-items` (materialize / archive-stale). Nothing here was deleted.",
            "To bring one back, copy its block into today's file — that restarts its clock.",
        ]
    oldest = min(item.last_changed for item in items)
    noun = "item" if len(items) == 1 else "items"
    heading = (
        f"## {today.isoformat()} — {len(items)} {noun} untouched for {max_age_days}+ days"
        f" (oldest: {oldest.isoformat()})"
    )
    out = [*existing, "", heading, ""]
    for item in items:
        out += item.lines
        out.append(f"  - _auto-archived {today.isoformat()} · unchanged since {item.last_changed.isoformat()}_")
        out.append("")
    atomic_write_lines(target, out[:-1])
    return target


def archive_stale(
    *,
    data_dir: Path,
    date: _dt.date,
    max_age_days: int = STALE_AFTER_DAYS,
    dry_run: bool = False,
) -> list[ArchivedItem]:
    """Archive stale items out of the existing daily file for `date`, in place."""
    target = paths.action_items_daily_path(data_dir, date=date)
    if not target.exists():
        return []
    ledger = ActivityLedger.load(data_dir)
    ledger.catch_up(data_dir, before=date)
    raw = target.read_text(encoding="utf-8")
    result = split_stale(raw.splitlines(), ledger, today=date, max_age_days=max_age_days)
    if dry_run:
        return result.archived
    ledger.save()
    if result.archived:
        write_archive(data_dir, date, result.archived, max_age_days=max_age_days)
        atomic_write_lines(
            target,
            result.lines,
            newline="\r\n" if "\r\n" in raw else "\n",
            trailing_newline=raw.endswith("\n"),
        )
    return result.archived

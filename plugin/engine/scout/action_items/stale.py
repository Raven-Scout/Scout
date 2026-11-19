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

Two things the replay does not count as an item going away. A day whose file
has no open task at all is a truncated rewrite, not a board, so it is skipped:
forgetting every tag on it restarted every clock. And an item that moved to
``action-items/backlog.md`` is still open: the replay keeps it, and
:meth:`ActivityLedger.observe_backlog` folds the backlog's current text in, so
moving an item between the two files is not a touch and a backlog item nobody
works ages out like any other.

The backlog has no dated history, so its dates are only as good as the cache.
A backlog item the ledger has never seen starts its clock the day it is first
observed, and a full rebuild (no cache, or a new ledger version) starts the
clock of every item that is only in the backlog on the rebuild day: the replay
can't tell how long such an item sat there unchanged, so it never guesses old.
The backlog archives at most ``BACKLOG_ARCHIVE_DAILY_CAP`` items a day, oldest
first, so the day a whole cohort reaches 60 days it drains over several mornings.

One edit is not a touch: the owner's one-off title cleanup (``action-items
retitle``, #708) shortens titles without restarting their clocks. It records
each rewrite in ``.scout-state/title-rewrites.jsonl`` (committed with the
vault): the tag, the day, and the block's fingerprint before and after. When
the replay sees a block change on that day from exactly ``before`` to exactly
``after``, the item keeps its date. Any other edit that day leaves a different
final fingerprint, so a real touch still restarts the clock, and because the
record is durable a rebuilt cache gives the same answer.
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
BACKLOG_ARCHIVE_DAILY_CAP = 25

# 2: hashes fold dates and ages out of the text (#372). A v1 ledger is rebuilt.
_LEDGER_VERSION = 2
_DAILY_NAME_RE = re.compile(r"^action-items-(\d{4}-\d{2}-\d{2})\.md$")
BACKLOG_NAME = "backlog.md"
# An open task: a daily file with none lost its item sections.
_OPEN_ROW_RE = re.compile(r"^\s*[-*] \[ \]")
_OPEN_TASK_RE = re.compile(r"^- \[ \]\s+")
_SNOOZE_RE = re.compile(r"snoozed-until:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s")
_SUBHEADING_RE = re.compile(r"^###\s")

_DATE_TOKEN = "\x00date\x00"
# Word edges that treat `_` as a separator, so `_re-anchored Oct 11_` folds whole.
_L = r"(?<![A-Za-z0-9])"
_R = r"(?![A-Za-z0-9])"
_MONTH = (
    rf"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    rf"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?){_R}\.?"
)
_ORDINAL = r"\d{1,2}(?:st|nd|rd|th)?"
_COUNT = r"(?:\d+(?:\.\d+)?\+?|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a\s+few|several)"
_AGE_UNIT = (
    r"(?:(?:working|business)\s+)?"
    r"(?:days?|weeks?|wks?|hours?|hrs?|minutes?|mins?|months?|mos?|years?|yrs?)"
)
# Applied in order; each match becomes _DATE_TOKEN. Month and weekday names are
# case-sensitive so "may" and "sun" in prose are left alone. A one-letter unit
# counts only attached to its number ("3d", "2w"), so "500 h budget" stays.
_DATE_PATTERNS = (
    re.compile(rf"{_L}\d{{4}}-\d{{2}}-\d{{2}}(?:[T ]\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?{_R}"),
    re.compile(
        rf"{_L}\d{{1,2}}(?::\d{{2}}(?::\d{{2}})?)?\s?[ap]m{_R}|{_L}\d{{1,2}}:\d{{2}}(?::\d{{2}})?{_R}", re.IGNORECASE
    ),
    re.compile(rf"{_L}\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_R}"),
    re.compile(rf"(?:{_L}{_ORDINAL}\s+)?{_L}{_MONTH}(?:\s+{_ORDINAL}{_R})?(?:,?\s+\d{{4}}{_R})?"),
    re.compile(
        rf"{_L}(?:Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:rs?|rsday)?|Fri(?:day)?"
        rf"|Sat(?:urday)?|Sun(?:day)?){_R}\.?"
    ),
    re.compile(rf"{_L}(?:today|tonight|tomorrow|yesterday){_R}", re.IGNORECASE),
    re.compile(rf"{_L}{_COUNT}\s*(?:-\s*)?{_AGE_UNIT}{_R}|{_L}\d+[dwh]{_R}", re.IGNORECASE),
    re.compile(rf"{_L}(?:day|week)\s+\d+{_R}", re.IGNORECASE),
    re.compile(rf"{_L}T[-+]\d+{_R}"),
)
# "Mon 2026-10-12 08:05" is one date, however many tokens it took.
_DATE_RUN_RE = re.compile(rf"{_DATE_TOKEN}(?:[\s,]*{_DATE_TOKEN})+")


REWRITES_NAME = "title-rewrites.jsonl"

# (tag, day) → the (before, after) fingerprint pairs recorded for it.
Rewrites = dict[tuple[str, _dt.date], set[tuple[str, str]]]


@dataclass(frozen=True)
class Rewrite:
    """One block change that is not a touch: see the module doc."""

    tag: str
    on: _dt.date
    before: str
    after: str

    def to_json(self) -> str:
        return json.dumps(
            {"tag": self.tag, "on": self.on.isoformat(), "before": self.before, "after": self.after}, sort_keys=True
        )


def rewrites_path(data_dir: Path) -> Path:
    return paths.state_dir(data_dir) / REWRITES_NAME


def load_rewrites(data_dir: Path) -> Rewrites:
    """Every recorded rewrite; a missing file is none, and a malformed row is skipped."""
    out: Rewrites = {}
    try:
        text = rewrites_path(data_dir).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
            key = (str(row["tag"]), _dt.date.fromisoformat(row["on"]))
            pair = (str(row["before"]), str(row["after"]))
        except (ValueError, KeyError, TypeError):
            continue
        out.setdefault(key, set()).add(pair)
    return out


def contains_date(text: str) -> bool:
    """Whether ``text`` holds a date, time or age, as the clock folds them out."""
    return _fold_dates(text) != text


def fingerprints(lines: list[str]) -> dict[str, str]:
    """Tag → the digest the clock compares (see ``_fingerprints``)."""
    return _fingerprints(lines)


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


def backlog_path(data_dir: Path) -> Path:
    return paths.action_items_dir(data_dir) / BACKLOG_NAME


def read_backlog(data_dir: Path) -> str | None:
    """The backlog's text, or None when there is no backlog.

    Any other read error (permissions, a bad encoding) raises: treating an
    unreadable backlog as empty would plan, and then write, over it."""
    try:
        return backlog_path(data_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def backlog_fingerprints(data_dir: Path) -> dict[str, str]:
    """Tag → digest for every open item in the backlog (none when there is no backlog)."""
    text = read_backlog(data_dir)
    return _fingerprints(text.splitlines()) if text else {}


class ActivityLedger:
    """When each open item's block last changed, by tag."""

    def __init__(
        self,
        path: Path,
        items: dict[str, dict[str, str]],
        seen_through: _dt.date | None,
        rewrites: Rewrites | None = None,
    ) -> None:
        self.path = path
        self._items = items
        self.seen_through = seen_through
        self.rewrites: Rewrites = rewrites or {}
        self._last_seen: set[str] = set()
        self._rewrite_index: tuple[int, dict[str, set[tuple[str, str]]]] | None = None

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

    def unchanged(self, tag: str, digest: str | None, on: _dt.date | None = None) -> bool:
        """Whether `digest` is the text the ledger last saw for `tag`, or a recorded rewrite of it `on` that day."""
        entry = self._items.get(tag)
        if entry is None or digest is None:
            return False
        return entry["hash"] == digest or (on is not None and self._rewritten(tag, on, entry["hash"], digest))

    def _rewritten(self, tag: str, on: _dt.date, before: str, after: str) -> bool:
        return (before, after) in self.rewrites.get((tag, on), set())

    def observe(self, lines: list[str], on: _dt.date, *, keep: frozenset[str] = frozenset()) -> None:
        """Fold one day's final file in. A tag absent that day is forgotten, so
        an item that comes back later counts as new from the day it returns,
        unless it is in ``keep`` (open in the backlog). A file with no
        checklist items is skipped: it is a truncated rewrite, not a cleared board."""
        if not any(_OPEN_ROW_RE.match(line) for line in lines):
            self.seen_through = on
            return
        current = _fingerprints(lines)
        self._last_seen = set(current)
        updated: dict[str, dict[str, str]] = {
            tag: entry for tag, entry in self._items.items() if tag in keep and tag not in current
        }
        for tag, digest in current.items():
            prior = self._items.get(tag)
            if prior is not None and prior["hash"] == digest:
                updated[tag] = prior
            elif prior is not None and self._rewritten(tag, on, prior["hash"], digest):
                updated[tag] = {"hash": digest, "since": prior["since"]}
            else:
                updated[tag] = {"hash": digest, "since": on.isoformat()}
        self._items = updated
        self.seen_through = on

    def observe_backlog(self, lines: list[str], on: _dt.date, *, skip: frozenset[str] = frozenset()) -> None:
        """Fold the backlog's current text in, as of ``on``.

        A block unchanged since the ledger last saw it (in the daily files or
        here) keeps its date, and so does one changed by a recorded rewrite;
        any other change, or a tag never seen, starts its clock ``on``. Tags in
        ``skip`` (also open in the daily file, which wins) are left alone.
        """
        for tag, digest in _fingerprints(lines).items():
            if tag in skip:
                continue
            prior = self._items.get(tag)
            if prior is not None and (prior["hash"] == digest or self._rewritten_any(tag, prior["hash"], digest)):
                self._items[tag] = {"hash": digest, "since": prior["since"]}
            else:
                self._items[tag] = {"hash": digest, "since": on.isoformat()}

    def _rewritten_any(self, tag: str, before: str, after: str) -> bool:
        """A recorded rewrite of ``tag`` from exactly ``before`` to exactly ``after``, on any day.

        The backlog has no dated history to replay, so the day can't be matched."""
        if self._rewrite_index is None or self._rewrite_index[0] != id(self.rewrites):
            index: dict[str, set[tuple[str, str]]] = {}
            for (t, _), pairs in self.rewrites.items():
                index.setdefault(t, set()).update(pairs)
            self._rewrite_index = (id(self.rewrites), index)
        return (before, after) in self._rewrite_index[1].get(tag, set())

    def catch_up(self, data_dir: Path, before: _dt.date) -> None:
        """Replay every daily file dated after `seen_through` and before `before`.

        Items open in the backlog now are kept across days they are absent from.
        On a full rebuild those kept items' clocks start at ``before`` (see the
        module doc): the replay can't date how long they sat in the backlog."""
        self.rewrites = load_rewrites(data_dir)
        keep = frozenset(backlog_fingerprints(data_dir))
        rebuilding = self.seen_through is None
        for day, path in daily_files(data_dir):
            if day >= before or (self.seen_through is not None and day <= self.seen_through):
                continue
            self.observe(path.read_text(encoding="utf-8").splitlines(), on=day, keep=keep)
        if rebuilding:
            for tag in keep - self._last_seen:
                if tag in self._items:
                    self._items[tag] = {"hash": self._items[tag]["hash"], "since": before.isoformat()}


def last_changed_by_tag(lines: list[str], *, data_dir: Path, today: _dt.date) -> dict[str, _dt.date]:
    """When each open item in ``lines`` (``today``'s file) last changed, as the archive judges it.

    Read-only: the ledger is replayed in memory from the daily files dated
    before ``today`` and never saved, so the cached ledger is neither read nor
    written. An item whose block differs from what that replay last saw, or
    that it never saw, changed ``today``.
    """
    ledger = ActivityLedger(ActivityLedger.path_for(data_dir), {}, None)
    ledger.catch_up(data_dir, before=today)
    out: dict[str, _dt.date] = {}
    for tag, digest in _fingerprints(lines).items():
        since = ledger.last_changed(tag)
        out[tag] = since if since is not None and ledger.unchanged(tag, digest, today) else today
    return out


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
    exclude: frozenset[str] = frozenset(),
    limit: int | None = None,
) -> SplitResult:
    """Separate the blocks unchanged for `max_age_days` from the rest of `lines`.

    A block whose text differs from what the ledger last saw was touched
    today, and one the ledger has never seen is new — neither is stale. So is
    anything snoozed past today: a snooze is a decision to park it. Tags in
    ``exclude`` are never archived here, and at most ``limit`` items (the
    oldest) are.
    """
    known = _fingerprints(lines)
    cutoff = today - _dt.timedelta(days=max_age_days)
    remove: set[int] = set()
    archived: list[ArchivedItem] = []
    candidates: list[tuple[TaskBlock, _dt.date]] = []
    for block in task_blocks(lines):
        if block.tag in exclude:
            continue
        since = ledger.last_changed(block.tag)
        if since is None or since > cutoff or not ledger.unchanged(block.tag, known.get(block.tag), today):
            continue
        if _snoozed_past(lines[block.start : block.end], today):
            continue
        candidates.append((block, since))
    if limit is not None and len(candidates) > limit:
        oldest = {b.start for b, _ in sorted(candidates, key=lambda c: (c[1], c[0].start))[:limit]}
        candidates = [c for c in candidates if c[0].start in oldest]
    for block, since in candidates:
        body = lines[block.start : block.end]
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


def split_backlog(
    data_dir: Path,
    ledger: ActivityLedger,
    *,
    today: _dt.date,
    skip: frozenset[str],
    max_age_days: int = STALE_AFTER_DAYS,
) -> tuple[str, SplitResult] | None:
    """Observe the backlog into ``ledger`` and split its stale items out.

    Returns the backlog's raw text and the split, or None when there is no
    backlog. ``skip`` is the tags open in the day's file, which wins.
    """
    raw = read_backlog(data_dir)
    if raw is None:
        return None
    lines = raw.splitlines()
    ledger.observe_backlog(lines, on=today, skip=skip)
    # A tag also open in the day's file is the daily row's to age, not this copy's.
    return raw, split_stale(
        lines, ledger, today=today, max_age_days=max_age_days, exclude=skip, limit=BACKLOG_ARCHIVE_DAILY_CAP
    )


def write_lines_like(target: Path, raw: str, lines: list[str]) -> None:
    """Write ``lines`` to ``target`` with ``raw``'s newline style and trailing newline."""
    atomic_write_lines(
        target,
        lines,
        newline="\r\n" if "\r\n" in raw else "\n",
        trailing_newline=raw.endswith("\n"),
    )


def archive_stale(
    *,
    data_dir: Path,
    date: _dt.date,
    max_age_days: int = STALE_AFTER_DAYS,
    dry_run: bool = False,
) -> list[ArchivedItem]:
    """Archive stale items out of the existing daily file for `date` and out of the backlog, in place."""
    target = paths.action_items_daily_path(data_dir, date=date)
    if not target.exists():
        return []
    ledger = ActivityLedger.load(data_dir)
    ledger.catch_up(data_dir, before=date)
    raw = target.read_text(encoding="utf-8")
    result = split_stale(raw.splitlines(), ledger, today=date, max_age_days=max_age_days)
    daily_tags = frozenset(b.tag for b in task_blocks(raw.splitlines()))
    backlog = split_backlog(data_dir, ledger, today=date, skip=daily_tags, max_age_days=max_age_days)
    archived = result.archived + (backlog[1].archived if backlog else [])
    if dry_run:
        return archived
    ledger.save()
    if archived:
        write_archive(data_dir, date, archived, max_age_days=max_age_days)
    if result.archived:
        write_lines_like(target, raw, result.lines)
    if backlog and backlog[1].archived:
        write_lines_like(backlog_path(data_dir), backlog[0], backlog[1].lines)
    return archived

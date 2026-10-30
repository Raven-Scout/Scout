"""*Past-Due Tasks Step Down* (#372), computed instead of left to prose (#534).

The rule (``phases/core/action-items.md``) counts a past-due 🔴 or 🟡 task's
**quiet days**: calendar days since the later of its due date and the last
new evidence on it. Under 7 it holds; at 7 it steps down to 🟡 with a
``due <date>; still want this?`` body and a dated ``stepped down, past due
with no news`` line in its context note; 14 days after that line, still
quiet, it parks. A recurring task (one linking ``[[recurring-tasks/…]]``)
steps down on its 3rd day past due instead of its 7th. Runs read 100% of the
rule and still missed it in every eval: counting days across context-note
dates is a poor fit for prose.

So the runner computes it. ``past_due_tasks`` reads the newest daily file on
or before today and returns each past-due task with its branch;
``scoutctl pre-session data`` writes them into
``.scout-cache/session-context.json`` (``past_due_tasks``), and
``check_demotion`` holds the run to them before it commits.

What counts as evidence:

- a dated line in the task's context note (``- 2026-10-20: Sam replied``,
  ``- Oct 20: …``, or any line under a dated heading), except the step-down
  line itself and lines that only say nothing happened (``no news``,
  ``no reply``, ``still waiting``, ``parked``…);
- a comment on the task (``  - alex: …``), dated by the first daily file it
  appears in;
- a new due date in the body (an answered "still want this?").

A task snoozed past today is left out: the snooze is the user's decision.
A task with no readable ``due <date>`` in its body is not past due.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import paths
from scout.action_items._common import _COMMENT_SUB_BULLET_RE, _NON_COMMENT_AUTHORS
from scout.action_items.stale import _snoozed_past, daily_files, task_blocks

STEP_DOWN_AFTER = 7
RECURRING_STEP_DOWN_AFTER = 3
PARK_AFTER = 14
MARKER = "stepped down, past due with no news"
HOLD, STEP_DOWN, PARK = "hold", "step_down", "park"
RED, YELLOW, PARKED = "🔴", "🟡", "🅿️"
# How far back a comment's first appearance is looked for.
_HISTORY_DAYS = 120

_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for m in names
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WEEKDAY = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+"
_DATE = (
    rf"(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"|(?:{_WEEKDAY})?(?P<mon>{_MONTH})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(?P<year>\d{{4}}))?"
    rf"|(?:{_WEEKDAY})?(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon2>{_MONTH})\b\.?(?:,?\s+(?P<year2>\d{{4}}))?"
)
_DUE = re.compile(rf"\bdue\b(?:\s+(?:on|by))?\s*:?\s*(?:{_DATE})", re.IGNORECASE)
_LEADING_DATE = re.compile(rf"^(?:{_DATE})(?![\w-])", re.IGNORECASE)
_LINE_PREFIX = re.compile(r"^[\s#>*_-]*(?:\[[ xX]\]\s*)?[*_\s]*")
_HEADING = re.compile(r"^#{1,6}\s")
_QUESTION = re.compile(r"\bdue [^;]+; still want this\?", re.IGNORECASE)
# A dated note line that only says nothing happened is not evidence.
_NO_NEWS = re.compile(
    r"\bno (?:news|reply|replies|response|answer|update|updates|change|movement|activity|follow-?up|progress)\b"
    r"|\bnothing (?:new|since)\b|\bstill (?:no|waiting|open|quiet|pending)\b|\bparked\b|\bstill want this\?",
    re.IGNORECASE,
)
# Machine sub-bullets that look like `key: value` but are not comments.
_NOT_COMMENTS = _NON_COMMENT_AUTHORS | {"refs", "estimate", "block", "actual", "evidence", "completed"}
_RECURRING = "[[recurring-tasks/"
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_TAG = re.compile(r"^- \[ \]\s+\[#([A-Za-z0-9]{2,8})\]")


def _closest(month: int, day: int, near: _dt.date, *, not_after: _dt.date | None = None) -> _dt.date | None:
    """The date with this month and day nearest ``near``, from the years around it."""
    found: list[_dt.date] = []
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            found.append(_dt.date(year, month, day))
        except ValueError:
            continue
    if not_after is not None and any(d <= not_after for d in found):
        found = [d for d in found if d <= not_after]
    return min(found, key=lambda d: abs((d - near).days)) if found else None


def _date_of(m: re.Match[str], near: _dt.date, *, not_after: _dt.date | None = None) -> _dt.date | None:
    if m.group("iso"):
        try:
            return _dt.date.fromisoformat(m.group("iso"))
        except ValueError:
            return None
    mon, day, year = (m.group("mon"), m.group("day"), m.group("year"))
    if mon is None:
        mon, day, year = m.group("mon2"), m.group("day2"), m.group("year2")
    month = _MONTHS[mon.lower().rstrip(".")]
    if year:
        try:
            return _dt.date(int(year), month, int(day))
        except ValueError:
            return None
    return _closest(month, int(day), near, not_after=not_after)


def body_of(line: str) -> str:
    """A task line's body: the text after its first ` — `."""
    return line.split(" — ", 1)[1].strip() if " — " in line else ""


def asks_still_want(body: str) -> bool:
    return bool(_QUESTION.search(body))


def parse_due(body: str, today: _dt.date) -> _dt.date | None:
    """The first ``due <date>`` in ``body``. A date without a year is the one nearest today."""
    m = _DUE.search(body)
    return _date_of(m, today) if m else None


@dataclass(frozen=True)
class NoteEntry:
    on: _dt.date
    text: str

    @property
    def is_marker(self) -> bool:
        return MARKER in self.text.lower()

    @property
    def is_evidence(self) -> bool:
        return not self.is_marker and not _NO_NEWS.search(self.text)


def note_entries(text: str, today: _dt.date) -> list[NoteEntry]:
    """Every dated line of a context note. A line under a dated heading takes the heading's date."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---"), 0)
        lines = lines[end + 1 :]
    out: list[NoteEntry] = []
    heading: _dt.date | None = None
    for line in lines:
        if not line.strip():
            continue
        rest = _LINE_PREFIX.sub("", line, count=1)
        m = _LEADING_DATE.match(rest)
        on = _date_of(m, today, not_after=today) if m else None
        if _HEADING.match(line):
            heading = on
            if on is not None:
                out.append(NoteEntry(on, rest))
            continue
        if on is not None:
            out.append(NoteEntry(on, rest))
        elif heading is not None:
            out.append(NoteEntry(heading, rest))
    return out


def comment_lines(block: Sequence[str]) -> list[str]:
    """The comment sub-bullets (``  - alex: …``) of a task block, stripped."""
    out = []
    for line in block[1:]:
        m = _COMMENT_SUB_BULLET_RE.match(line)
        if m and m.group("author").lower() not in _NOT_COMMENTS:
            out.append(line.strip())
    return out


class History:
    """When a line first appeared in the daily files, walking back from a day while it is there."""

    def __init__(self, data_dir: Path | None, *, overrides: Mapping[_dt.date, Sequence[str]] | None = None) -> None:
        self._files = daily_files(paths.data_dir(data_dir))
        self._lines: dict[_dt.date, set[str]] = {d: {ln.strip() for ln in v} for d, v in (overrides or {}).items()}

    def _day(self, day: _dt.date, path: Path | None) -> set[str]:
        if day not in self._lines:
            try:
                text = path.read_text(encoding="utf-8") if path else ""
            except (OSError, UnicodeDecodeError):
                text = ""
            self._lines[day] = {ln.strip() for ln in text.splitlines()}
        return self._lines[day]

    def first_seen(self, line: str, *, upto: _dt.date) -> _dt.date:
        days = sorted({d for d, _ in self._files} | set(self._lines), reverse=True)
        files = dict(self._files)
        seen = upto
        for day in days:
            if day > upto:
                continue
            if (upto - day).days > _HISTORY_DAYS or line.strip() not in self._day(day, files.get(day)):
                break
            seen = day
        return seen


@dataclass(frozen=True)
class PastDue:
    """One past-due task and the branch the rule puts it on."""

    tag: str
    title: str
    section: str  # 🔴, 🟡 or 🅿️
    line: str  # the task line as it stood
    due: _dt.date
    recurring: bool
    last_evidence: _dt.date | None
    stepped_down: _dt.date | None  # the step-down line on or after the count's start
    quiet_days: int
    branch: str

    @property
    def threshold(self) -> int:
        return RECURRING_STEP_DOWN_AFTER if self.recurring else STEP_DOWN_AFTER

    @property
    def counted_from(self) -> _dt.date:
        return max(self.due, self.last_evidence or self.due)

    def to_json(self) -> dict[str, Any]:
        def iso(d: _dt.date | None) -> str | None:
            return d.isoformat() if d else None

        step_on = self.stepped_down or self.counted_from + _dt.timedelta(days=self.threshold)
        return {
            "tag": self.tag,
            "title": self.title,
            "section": self.section,
            "line": self.line,
            "due": self.due.isoformat(),
            "recurring": self.recurring,
            "last_evidence": iso(self.last_evidence),
            "stepped_down": iso(self.stepped_down),
            "quiet_days": self.quiet_days,
            "branch": self.branch,
            "steps_down_on": None if self.stepped_down else step_on.isoformat(),
            "parks_on": iso(self.stepped_down + _dt.timedelta(days=PARK_AFTER)) if self.stepped_down else None,
        }

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> PastDue:
        def day(key: str) -> _dt.date | None:
            raw = row.get(key)
            return _dt.date.fromisoformat(str(raw)) if raw else None

        due = day("due")
        if due is None:
            raise ValueError("no due date")
        return cls(
            tag=str(row["tag"]),
            title=str(row.get("title") or ""),
            section=str(row.get("section") or ""),
            line=str(row.get("line") or ""),
            due=due,
            recurring=bool(row.get("recurring")),
            last_evidence=day("last_evidence"),
            stepped_down=day("stepped_down"),
            quiet_days=int(row.get("quiet_days") or 0),
            branch=str(row["branch"]),
        )


def assess(
    block: Sequence[str],
    section: str,
    note: str,
    *,
    today: _dt.date,
    first_seen: Callable[[str], _dt.date],
    due_fallback: _dt.date | None = None,
) -> PastDue | None:
    """The branch for one task block, or None when it isn't past due, is snoozed, or has no due date.

    ``first_seen`` dates a comment line; ``due_fallback`` stands in for a body
    that no longer says its due date.
    """
    line = block[0]
    m = _TAG.match(line)
    if m is None or _snoozed_past(list(block), today):
        return None
    due = parse_due(body_of(line), today) or due_fallback
    if due is None or due >= today:
        return None
    entries = note_entries(note, today)
    evidence = [e.on for e in entries if e.is_evidence and e.on <= today]
    evidence += [first_seen(c) for c in comment_lines(block)]
    last_evidence = max(evidence) if evidence else None
    start = max(due, last_evidence or due)
    markers = sorted(e.on for e in entries if e.is_marker and start <= e.on <= today)
    stepped_down = markers[0] if markers else None
    recurring = any(_RECURRING in ln for ln in block)
    quiet = (today - start).days
    if stepped_down is not None and (today - stepped_down).days >= PARK_AFTER:
        branch = PARK
    elif stepped_down is not None or quiet >= (RECURRING_STEP_DOWN_AFTER if recurring else STEP_DOWN_AFTER):
        branch = STEP_DOWN
    else:
        branch = HOLD
    title = _BOLD.search(line)
    return PastDue(
        tag=m.group(1),
        title=title.group(1).strip() if title else "",
        section=section,
        line=line.rstrip(),
        due=due,
        recurring=recurring,
        last_evidence=last_evidence,
        stepped_down=stepped_down,
        quiet_days=quiet,
        branch=branch,
    )


def sectioned_blocks(lines: Sequence[str]) -> dict[str, tuple[str, list[str]]]:
    """Tag → (section emoji, block lines) for each open task, first in a tier section wins."""
    starts = {b.start: b for b in task_blocks(list(lines))}
    out: dict[str, tuple[str, list[str]]] = {}
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line[3:].strip().split(" ", 1)[0] if line[3:].strip() else ""
        block = starts.get(i)
        if block is not None and block.tag not in out and section in (RED, YELLOW, PARKED, "🟢"):
            out[block.tag] = (section, list(lines[block.start : block.end]))
    return out


def read_note(data_dir: Path, tag: str) -> str:
    try:
        return (paths.action_items_dir(data_dir) / "context" / f"{tag}.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def latest_daily(data_dir: Path, today: _dt.date) -> tuple[_dt.date, Path] | None:
    """The newest daily file dated on or before today."""
    found = [(d, p) for d, p in daily_files(data_dir) if d <= today]
    return found[-1] if found else None


def past_due_tasks(data_dir: Path, today: _dt.date) -> tuple[str | None, list[PastDue]]:
    """(the daily file's date, each past-due task on it). A 🔴 or 🟡 task counts; a 🅿️ one only
    when this rule parked it (its note has a step-down line)."""
    latest = latest_daily(data_dir, today)
    if latest is None:
        return None, []
    day, path = latest
    lines = path.read_text(encoding="utf-8").splitlines()
    history = History(data_dir)
    out: list[PastDue] = []
    for tag, (section, block) in sectioned_blocks(lines).items():
        if section not in (RED, YELLOW, PARKED):
            continue
        note = read_note(data_dir, tag)
        if section == PARKED and MARKER not in note.lower():
            continue
        found = assess(block, section, note, today=today, first_seen=lambda c: history.first_seen(c, upto=day))
        if found is not None:
            out.append(found)
    return day.isoformat(), out


# ----- the pre-commit check --------------------------------------------------


@dataclass(frozen=True)
class Finding:
    tag: str
    branch: str
    problem: str


@dataclass(frozen=True)
class DemotionCheck:
    checked: tuple[PastDue, ...]  # as this run should leave them, new evidence counted
    findings: tuple[Finding, ...]
    released: tuple[str, ...] = ()  # no longer past due (a new due date) or snoozed since the fetch

    @property
    def passed(self) -> bool:
        return not self.findings


def _rows(raw: object) -> list[PastDue]:
    from scout.action_items.prefetched import PrefetchCheckError

    if raw is None:
        raise PrefetchCheckError("past_due_tasks was not pre-fetched; run `scoutctl pre-session data` first")
    if not isinstance(raw, list):
        raise PrefetchCheckError("past_due_tasks is not a list")
    try:
        return [PastDue.from_json(r) for r in raw]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise PrefetchCheckError(f"past_due_tasks has an unreadable entry: {e}") from e


def judge(
    rows: Iterable[PastDue],
    blocks: Mapping[str, tuple[str, list[str]]],
    notes: Callable[[str], str],
    *,
    today: _dt.date,
    first_seen: Callable[[str], _dt.date],
) -> DemotionCheck:
    """Hold today's file to the pre-fetched branches.

    New evidence this run recorded (a dated note line, a comment, a new due
    date) restarts the count, so the task is judged on the recount; a
    step-down line this run added doesn't count as a step-down already done.
    """
    findings: list[Finding] = []
    checked: list[PastDue] = []
    released: list[str] = []
    for row in rows:
        found = blocks.get(row.tag)
        if found is None:
            findings.append(Finding(row.tag, row.branch, "missing from today's file"))
            continue
        section, block = found
        now = assess(block, section, notes(row.tag), today=today, first_seen=first_seen, due_fallback=row.due)
        if now is None:
            released.append(row.tag)
            continue
        branch = row.branch
        restarted = now.counted_from > row.counted_from
        if restarted:
            branch = HOLD if now.quiet_days < now.threshold else now.branch
        checked.append(now)
        body = body_of(block[0])
        problems: list[str] = []
        if branch == STEP_DOWN:
            if section == RED:
                problems.append("still in 🔴; move it to 🟡 at most")
            if not asks_still_want(body):
                problems.append(f"body doesn't say `due {now.due:%b} {now.due.day}; still want this?`")
            if now.stepped_down is None:
                problems.append(f"context note has no dated `- {today.isoformat()}: {MARKER}` line")
        elif branch == PARK:
            if section != PARKED:
                problems.append(f"in {section}; move it to {PARKED} Parked")
            if row.line and block[0].strip() != row.line.strip():
                problems.append("its text changed; park it as it stood")
        else:
            if section == PARKED and row.section != PARKED:
                problems.append(f"parked under {now.quiet_days} quiet days")
            elif section == PARKED:
                problems.append("new evidence since it was parked; place it under the usual rules again")
            if asks_still_want(body) and row.stepped_down is None:
                problems.append("asks 'still want this?' under the step-down threshold")
            if now.stepped_down is not None and row.stepped_down is None:
                problems.append(f"context note gained a `{MARKER}` line under the step-down threshold")
            if problems and restarted:
                problems.append(
                    f"(the count restarted on {now.counted_from}: a dated note line, a comment or a new due date "
                    "reads as news; if it isn't, reword it)"
                )
        findings += [Finding(row.tag, branch, p) for p in problems]
    return DemotionCheck(tuple(checked), tuple(findings), tuple(released))


def check_demotion(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> DemotionCheck:
    """Compare today's file with the pre-fetched ``past_due_tasks``."""
    from scout.action_items.prefetched import context_path, daily_lines, load_context

    data = load_context(context_path(data_dir, context), today=today)
    rows = _rows(data.get("past_due_tasks"))
    lines = daily_lines(data_dir, today)
    vault = paths.data_dir(data_dir)
    day = _dt.date.fromisoformat(today)
    history = History(vault, overrides={day: lines})
    return judge(
        rows,
        sectioned_blocks(lines),
        lambda tag: read_note(vault, tag),
        today=day,
        first_seen=lambda c: history.first_seen(c, upto=day),
    )

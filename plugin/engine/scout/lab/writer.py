"""Pure text transforms for lab front pages and timelines (``str -> str``).

Every front-page writer takes the whole page text and returns the whole page text. Edits are
line-level, located with the parser's line numbers, so hand-written prose in sections an edit
does not touch survives byte-for-byte. Unusable input raises ``LabError`` naming the heading or
ID at fault; nothing here does I/O.
"""

from __future__ import annotations

import datetime as _dt
import json
import re

import yaml

from scout.errors import LabError
from scout.kb.frontmatter import frontmatter_block
from scout.lab.model import (
    EXPERIMENT_STATUSES,
    SLUG_RE,
    TIMELINE_TAGS,
    Comment,
    ExperimentRef,
    Question,
    Thread,
    TimelineEntry,
)
from scout.lab.parser import parse_front_page

MAX_TEXT = 2000

_H2_RE = re.compile(r"^## (?P<name>.+?)\s*$")
_H1_RE = re.compile(r"^# ")
_QID_FULL = re.compile(r"^Q[1-9]\d{0,3}\Z")
_EID_FULL = re.compile(r"^E[1-9]\d{0,3}\Z")
_AUTHOR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STAMP_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d$")


# --- normalization ---------------------------------------------------------------------


def _norm(value: str, what: str, *, dot: bool = False) -> str:
    """Collapse whitespace to one line, refuse empty or overlong, optionally defuse ``" · "``.

    ``dot`` is for text that sits left of the ``· status · when`` tail (question text, titles,
    notes, reasons): a middle dot there would move the parser's greedy right-split.
    """
    out = " ".join(value.split())
    if not out:
        raise LabError(f"{what} is empty")
    if len(out) > MAX_TEXT:
        raise LabError(f"{what} is {len(out)} characters; the limit is {MAX_TEXT}")
    if dot:
        out = out.replace(" · ", " - ")
    return out


def _field(value: str, what: str) -> str:
    """A machine-made tail field (a date, ``by``): one line, no tail or note separators."""
    return _norm(value, what, dot=True).replace(" — ", " - ")


def _prose(value: str, what: str) -> list[str]:
    """Multi-line prose for a section body. Refuses lines that would read as headings."""
    out = value.strip()
    if not out:
        raise LabError(f"{what} is empty")
    if len(out) > 20 * MAX_TEXT:
        raise LabError(f"{what} is {len(out)} characters; the limit is {20 * MAX_TEXT}")
    lines = out.replace("\r\n", "\n").split("\n")
    for ln in lines:
        if ln.startswith(("# ", "## ")) or ln.rstrip() == "---":
            raise LabError(f"{what} has a line that would read as a heading or fence: {ln!r}")
    return [ln.rstrip() for ln in lines]


def _link_target(target: str) -> str:
    t = target.strip()
    if not t or any(c in t for c in "[]|\n\r"):
        raise LabError(f"bad link target {target!r}")
    return t


# --- locating --------------------------------------------------------------------------


def _split(text: str) -> list[str]:
    return text.removeprefix("\ufeff").split("\n")


def _body_start(lines: list[str]) -> int:
    block = frontmatter_block(lines)
    return 0 if block is None else len(block) + 2


def _section(lines: list[str], name: str) -> tuple[int, int]:
    """``(heading index, end index)``: end is the next ``## `` heading or the end of the file."""
    start = _body_start(lines)
    heading = None
    for i in range(start, len(lines)):
        m = _H2_RE.match(lines[i])
        if not m:
            continue
        if heading is not None:
            return heading, i
        if m.group("name") == name:
            heading = i
    if heading is None:
        raise LabError(f"front page has no '## {name}' heading; fix the page by hand before writing")
    return heading, len(lines)


def _last_content(lines: list[str], heading: int, end: int) -> int:
    """Index of the last non-blank line in the section, or the heading itself when it is empty."""
    for i in range(end - 1, heading, -1):
        if lines[i].strip():
            return i
    return heading


def _thread(text: str) -> Thread:
    return parse_front_page("", text)


def _join(lines: list[str]) -> str:
    return "\n".join(lines)


def _last_line(item_line: int, comments: list[Comment]) -> int:
    """1-based line of the last comment or reply under an item, or the item's own line."""
    last = item_line
    for c in comments:
        last = max(last, c.line, _last_line(c.line, c.replies))
    return last


def _find_question(t: Thread, qid: str) -> Question:
    if not _QID_FULL.match(qid):
        raise LabError(f"'{qid}' is not a question ID (Q<n>)")
    for q in [*t.open, *t.answered]:
        if q.qid == qid:
            return q
    raise LabError(f"no question {qid} on this page")


def _find_experiment(t: Thread, eid: str) -> ExperimentRef:
    for e in t.experiments:
        if e.eid == eid:
            return e
    raise LabError(f"no experiment {eid} on this page")


def _comment_scope(t: Thread, target: str) -> tuple[list[Comment], int, int]:
    """``(top-level comments, item line, indent)`` for a comment target."""
    if target == "thread":
        return t.comments, 0, 0
    if _QID_FULL.match(target):
        q = _find_question(t, target)
        return q.comments, q.line, 2
    if _EID_FULL.match(target):
        e = _find_experiment(t, target)
        return e.comments, e.line, 2
    raise LabError(f"comment target must be 'thread', a Q<n> or an E<n>, not '{target}'")


# --- frontmatter -----------------------------------------------------------------------


def _scalar(value: str | int) -> str:
    if isinstance(value, int):
        return str(value)
    if "\n" in value or "\r" in value:
        raise LabError("frontmatter values must be one line")
    try:
        got = yaml.safe_load(f"v: {value}")
    except yaml.YAMLError:
        got = None
    if isinstance(got, dict) and set(got) == {"v"}:
        v = got["v"]
        if v == value or isinstance(v, _dt.date):  # datetime is a date subclass
            return value
    return json.dumps(value, ensure_ascii=False)


def set_frontmatter(text: str, **fields: str | int) -> str:
    """Add or replace top-level frontmatter keys, keeping order and every other key."""
    lines = _split(text)
    block = frontmatter_block(lines)
    if block is None:
        raise LabError("front page has no frontmatter block")
    close = len(block) + 1
    for key, value in fields.items():
        if not _KEY_RE.match(key):
            raise LabError(f"bad frontmatter key '{key}'")
        new = f"{key}: {_scalar(value)}"
        for i in range(1, close):
            if re.match(rf"^{re.escape(key)}\s*:", lines[i]):
                lines[i] = new
                break
        else:
            lines.insert(close, new)
            close += 1
    return _join(lines)


# --- comments --------------------------------------------------------------------------


def _comment_line(indent: int, author: str, stamp: str, body: str) -> str:
    if not _AUTHOR_RE.match(author):
        raise LabError(f"bad comment author {author!r}")
    stamp = stamp.strip()
    if "\n" in stamp or ")" in stamp:
        raise LabError(f"bad comment stamp {stamp!r}")
    text = _norm(body, "comment")
    who = f"{author} ({stamp})" if stamp else author
    return f"{' ' * indent}- {who}: {text}"


def add_comment(text: str, target: str, author: str, body: str, stamp: str) -> str:
    """Append a top-level comment after the target's last comment or reply."""
    t = _thread(text)
    lines = _split(text)
    comments, item_line, indent = _comment_scope(t, target)
    new = _comment_line(indent, author, stamp, body)
    if target == "thread":
        heading, end = _section(lines, "Comments")
        at = _last_content(lines, heading, end) + 1
    else:
        at = _last_line(item_line, comments)  # 1-based line == 0-based index of the next line
    lines.insert(at, new)
    return _join(lines)


def add_reply(text: str, target: str, index: int, body: str, stamp: str) -> str:
    """Scout's reply to the ``index``-th (1-based) top-level comment of ``target``."""
    t = _thread(text)
    lines = _split(text)
    comments, _, indent = _comment_scope(t, target)
    if not 1 <= index <= len(comments):
        raise LabError(f"{target} has {len(comments)} comment(s); there is no comment {index}")
    parent = comments[index - 1]
    new = _comment_line(indent + 2, "scout", stamp, body)
    lines.insert(_last_line(parent.line, parent.replies), new)
    return _join(lines)


# --- questions -------------------------------------------------------------------------


def add_question(text: str, qtext: str, raised: str, by: str | None = None) -> tuple[str, str]:
    """Append an open question, taking its ID from ``next_q``. Returns ``(text, qid)``."""
    t = _thread(text)
    lines = _split(text)
    heading, end = _section(lines, "Open questions")
    raw = t.frontmatter.get("next_q")
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 9999:
        raise LabError(f"frontmatter next_q is {raw!r}; it must be an integer from 1 to 9999")
    qid = f"Q{raw}"
    when = f"raised {_field(raised, 'raised date')}"
    if by is not None:
        when += f" by {_field(by, 'by')}"
    line = f"- **{qid}** {_norm(qtext, 'question text', dot=True)} · open · {when}"
    lines.insert(_last_content(lines, heading, end) + 1, line)
    return set_frontmatter(_join(lines), next_q=raw + 1), qid


def _move_to_answered(text: str, q: Question, status: str, day: str, note: str) -> str:
    lines = _split(text)
    last = _last_line(q.line, q.comments)
    block = lines[q.line - 1 : last]
    block[0] = f"- **{q.qid}** {q.text} · {status} · {_field(day, 'day')} — {note}"
    del lines[q.line - 1 : last]
    heading, _ = _section(lines, "Answered questions")
    lines[heading + 1 : heading + 1] = block
    return _join(lines)


def _open_question(t: Thread, qid: str) -> Question:
    q = _find_question(t, qid)
    if q not in t.open:
        raise LabError(f"question {qid} is not open")
    return q


def answer_question(text: str, qid: str, note: str, day: str) -> str:
    """Move an open question and its comments to the top of ``## Answered questions``."""
    t = _thread(text)
    _section(_split(text), "Answered questions")  # refuse before touching anything
    q = _open_question(t, qid)
    return _move_to_answered(text, q, "answered", day, _norm(note, "answer note", dot=True))


def _owner_comments(comments: list[Comment]) -> list[Comment]:
    out: list[Comment] = []
    for c in comments:
        if not c.is_scout:
            out.append(c)
        out.extend(_owner_comments(c.replies))
    return out


def drop_question(text: str, qid: str, reason: str, day: str, owner_comment: int | None = None) -> str:
    """Move an open question to Answered as ``dropped``.

    A question the owner has commented on is dropped only when ``owner_comment`` (1-based among
    its top-level comments) names one of the owner's comments.
    """
    t = _thread(text)
    _section(_split(text), "Answered questions")
    q = _open_question(t, qid)
    if _owner_comments(q.comments):
        named = owner_comment is not None and 1 <= owner_comment <= len(q.comments)
        if owner_comment is None or not named or q.comments[owner_comment - 1].is_scout:
            raise LabError(
                f"question {qid} has an owner comment; dropping it needs owner_comment=<n> naming one of "
                "the owner's top-level comments"
            )
    return _move_to_answered(text, q, "dropped", day, _norm(reason, "drop reason", dot=True))


# --- understanding, evidence, experiments ----------------------------------------------


def set_understanding(text: str, prose: str) -> str:
    """Replace the body of ``## Current understanding``, keeping the blank lines after it."""
    lines = _split(text)
    heading, end = _section(lines, "Current understanding")
    new = _prose(prose, "understanding")
    trailing = 0
    for ln in reversed(lines[heading + 1 : end]):
        if ln.strip():
            break
        trailing += 1
    lines[heading + 1 : end] = [*new, *([""] * trailing)]
    return _join(lines)


def add_evidence(text: str, target: str, why: str) -> str:
    """List ``[[target]] — why`` under Evidence. A no-op when the target is already listed."""
    t = _thread(text)
    lines = _split(text)
    heading, end = _section(lines, "Evidence")
    name = _link_target(target)
    line = f"- [[{name}]] — {_norm(why, 'evidence reason')}"
    if any(e.target == name for e in t.evidence):
        return text
    lines.insert(_last_content(lines, heading, end) + 1, line)
    return _join(lines)


def set_experiment_line(
    text: str, eid: str, target: str, title: str, status: str, when: str, note: str | None = None
) -> str:
    """Insert an experiment line at the top of ``## Experiments``, or update it in place."""
    if not _EID_FULL.match(eid):
        raise LabError(f"'{eid}' is not an experiment ID (E<n>)")
    if status not in EXPERIMENT_STATUSES:
        raise LabError(f"unknown experiment status '{status}'")
    t = _thread(text)
    lines = _split(text)
    heading, _ = _section(lines, "Experiments")
    line = f"- [[{_link_target(target)}|{eid}]] {_norm(title, 'experiment title', dot=True)} · {status} · "
    line += _field(when, "experiment when")
    if note is not None:
        line += f" — {_norm(note, 'experiment note', dot=True)}"
    existing = next((e for e in t.experiments if e.eid == eid), None)
    if existing is not None:
        lines[existing.line - 1] = line
    else:
        lines.insert(heading + 1, line)
    return _join(lines)


# --- new files -------------------------------------------------------------------------


def _check_slug(slug: str) -> None:
    if not re.fullmatch(SLUG_RE, slug):
        raise LabError(f"bad thread slug '{slug}'")


def new_front_page(
    slug: str,
    title: str,
    question: str,
    open_questions: list[str],
    evidence: list[tuple[str, str]],
    created: str,
    visibility: str,
) -> str:
    """A fresh ``proposed`` front page with every heading present."""
    _check_slug(slug)
    name = _norm(title, "title", dot=True)
    day = _field(created, "created date")
    fm = [
        "---",
        "type: lab-thread",
        f"title: {_scalar(name)}",
        "status: proposed",
        f"created: {_scalar(day)}",
        f"visibility: {_scalar(_field(visibility, 'visibility'))}",
        f"next_q: {len(open_questions) + 1}",
        "next_e: 1",
        "---",
    ]
    qs = [
        f"- **Q{n}** {_norm(q, 'question text', dot=True)} · open · raised {day}"
        for n, q in enumerate(open_questions, 1)
    ]
    ev = [f"- [[{_link_target(tgt)}]] — {_norm(why, 'evidence reason')}" for tgt, why in evidence]
    out = [
        *fm,
        f"# {name}",
        "",
        "## Question",
        *_prose(question, "question"),
        "",
        "## Current understanding",
        "",
        "## Open questions",
        *qs,
        "",
        "## Answered questions",
        "",
        "## Experiments",
        "",
        "## Evidence",
        *ev,
        "",
        "## Comments",
        "",
    ]
    return _join(out)


def new_timeline(slug: str, title: str) -> str:
    _check_slug(slug)
    return f"# {_norm(title, 'title', dot=True)} — timeline\n"


def prepend_timeline(text: str, entry: TimelineEntry) -> str:
    """Insert an entry directly under the H1, so the file stays newest first."""
    if not _STAMP_RE.match(entry.stamp):
        raise LabError(f"bad timeline stamp {entry.stamp!r}; expected YYYY-MM-DD HH:MM")
    if entry.tag not in TIMELINE_TAGS:
        raise LabError(f"unknown timeline tag '{entry.tag}'")
    if not entry.bullets:
        raise LabError("timeline entry has no bullets")
    bullets = [f"- {_norm(b, 'timeline bullet')}" for b in entry.bullets]
    lines = _split(text)
    h1 = next((i for i, ln in enumerate(lines) if _H1_RE.match(ln)), None)
    if h1 is None:
        raise LabError("timeline has no '# <title> — timeline' heading")
    rest = lines[h1 + 1 :]
    while rest and not rest[0].strip():
        rest.pop(0)
    block = [f"### {entry.stamp} · {entry.tag}", *bullets]
    out = [*lines[: h1 + 1], "", *block]
    out += ["", *rest] if rest else [""]
    return _join(out)

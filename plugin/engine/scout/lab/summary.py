"""The lab's JSON read model: ``lab list`` and ``lab show``.

These documents are a contract with the macOS app (``tests/fixtures/contract/lab-list.json`` and
``lab-thread.json``). Everything returned is plain JSON data: YAML dates become ISO strings and
no absolute path is emitted. Nothing here writes.

**What a reader who doesn't see everything gets.** Unless the reader is full (``owner``, ``autonomous``), a
thread is left out when its front page isn't in the reader's visibility map or says it is private, and so is an
experiment whose note exists but isn't labeled ``normal`` in that map (a private note, or one the reader may not
see). A left-out experiment is gone from every part of the document: ``lab show``'s ``experiments`` list (with its
``frontmatter``), the experiment-ID lists of ``lab list`` (whose ``due`` and ``held`` read the note), and the
replies and unanswered comments counted on its line. ``lab show`` counts them in ``private_experiments``, as
``lab.md`` counts private threads; it is 0 for a full reader. An experiment with no note is kept, with
``frontmatter`` null.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scout import config
from scout.errors import LabError
from scout.lab import experiments, ops, paths
from scout.lab.model import (
    CLOSE_PREFIX,
    THREAD_STATUSES,
    Comment,
    EvidenceRef,
    ExperimentRef,
    Problem,
    Question,
    Thread,
)
from scout.lab.parser import load_thread, parse_experiment, parse_timeline

SCHEMA_VERSION = 1
TIMELINE_LIMIT = 50
_RAISED_RE = re.compile(r"^raised (\d{4}-\d\d-\d\d)(?: by .*)?$")
_CLOSE_PREFIX = CLOSE_PREFIX


def _json(value: Any) -> Any:
    """``value`` with dates and datetimes as ISO strings, recursively; anything else odd as ``str``."""
    if isinstance(value, _dt.datetime | _dt.date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _moment(vault: Path, now: _dt.datetime | None) -> _dt.datetime:
    return now if now is not None else config.now(vault)


def _rel(vault: Path, path: Path) -> str:
    return path.relative_to(vault).as_posix()


# --- list ------------------------------------------------------------------------------


def _questions(thread: Thread) -> list[Question]:
    return [*thread.open, *thread.answered]


def _raised_recently(thread: Thread, now: _dt.datetime) -> int:
    today = now.date()
    start = today - _dt.timedelta(days=7)
    count = 0
    for q in _questions(thread):
        m = _RAISED_RE.match(q.when)
        if not m:
            continue
        try:
            day = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if start <= day <= today:
            count += 1
    return count


def _scout_replies(thread: Thread, hidden: set[str]) -> list[dict[str, str]]:
    """Every Scout reply anywhere in the thread as ``{target, stamp}``, newest first; none on a ``hidden``
    experiment's line."""
    found: list[tuple[str, str]] = []
    holders: list[tuple[str, list[Comment]]] = [(q.qid, q.comments) for q in _questions(thread)]
    holders += [(e.eid, e.comments) for e in thread.experiments if e.eid not in hidden]
    holders.append(("thread", thread.comments))
    for target, comments in holders:
        for c in comments:
            found.extend((target, r.stamp or "") for r in c.replies if r.is_scout)
    found.sort(key=lambda pair: pair[1], reverse=True)
    return [{"target": target, "stamp": stamp} for target, stamp in found]


def _proposes_close(thread: Thread) -> bool:
    last = next((c for c in reversed(thread.comments) if c.is_scout), None)
    return last is not None and last.text.startswith(_CLOSE_PREFIX)


def _hidden_experiments(vault: Path, thread: Thread, labels: Mapping[str, str] | None, full: bool) -> set[str]:
    """The IDs of the experiments a reader who isn't ``full`` may not see: their note exists and isn't labeled
    ``normal`` in ``labels``, the reader's visibility map (a path missing from it is private)."""
    if full:
        return set()
    seen = labels or {}
    hidden: set[str] = set()
    for e in thread.experiments:
        note = paths.experiment_note(vault, thread.slug, e.eid)
        if note is not None and seen.get(_rel(vault, note)) != "normal":
            hidden.add(e.eid)
    return hidden


def _unanswered(thread: Thread, hidden: set[str]) -> int:
    return sum(1 for target, _ in thread.unanswered_owner_comments() if target not in hidden)


def _experiment_ids(vault: Path, thread: Thread, now: _dt.datetime, hidden: set[str]) -> dict[str, list[str]]:
    """``planned``, ``running``, ``due`` and ``held``: the due experiments that the owner has a comment waiting
    on, on the experiment's line or on any question it lists. Scout answers those before it runs them. The
    ``hidden`` experiments are left out."""
    waiting = {target for target, _ in thread.unanswered_owner_comments()}
    planned = [e.eid for e in thread.experiments if e.status == "planned" and e.eid not in hidden]
    running = [e.eid for e in thread.experiments if e.status == "running" and e.eid not in hidden]
    due: list[str] = []
    held: list[str] = []
    for eid in planned:
        note = paths.experiment_note(vault, thread.slug, eid)
        if note is None:
            continue
        try:
            exp = parse_experiment(thread.slug, note.read_text(encoding="utf-8"))
            not_before = experiments.as_datetime(exp.frontmatter.get("not_before"), now.tzinfo, "not_before")
        except (OSError, UnicodeDecodeError, LabError):
            continue
        if not_before <= now:
            due.append(eid)
            if eid in waiting or waiting.intersection(exp.questions):
                held.append(eid)
    return {"planned": planned, "running": running, "due": due, "held": held}


def _summary(vault: Path, thread: Thread, now: _dt.datetime, hidden: set[str]) -> dict[str, Any]:
    questions = _questions(thread)
    last_run = thread.frontmatter.get("last_run")
    return {
        "slug": thread.slug,
        "path": _rel(vault, paths.front_page(vault, thread.slug)),
        "title": thread.title,
        "question": thread.question,
        "status": thread.status,
        "visibility": thread.visibility,
        "counts": {
            "open": sum(1 for q in thread.open if q.status == "open"),
            "answered": sum(1 for q in questions if q.status == "answered"),
            "dropped": sum(1 for q in questions if q.status == "dropped"),
            "raised_7d": _raised_recently(thread, now),
        },
        "unanswered_comments": _unanswered(thread, hidden),
        "scout_replies": _scout_replies(thread, hidden),
        "experiments": _experiment_ids(vault, thread, now, hidden),
        "last_run": None if last_run is None else _json(last_run),
        "problems": len(thread.problems),
    }


def _broken_summary(vault: Path, thread: Thread, exc: Exception) -> dict[str, Any]:
    """The row for a thread whose summary couldn't be built: the same keys, nothing counted, the failure named
    in ``error`` and counted as one more problem, so the app shows it as not matching the lab format."""
    return {
        "slug": thread.slug,
        "path": _rel(vault, paths.front_page(vault, thread.slug)),
        "title": str(thread.title),
        "question": str(thread.question),
        "status": thread.status,
        "visibility": thread.visibility,
        "counts": {"open": 0, "answered": 0, "dropped": 0, "raised_7d": 0},
        "unanswered_comments": 0,
        "scout_replies": [],
        "experiments": {"planned": [], "running": [], "due": [], "held": []},
        "last_run": None,
        "problems": len(thread.problems) + 1,
        "error": f"cannot summarize this thread: {exc.__class__.__name__}: {exc}"[:300],
    }


def _status_rank(status: str) -> int:
    return THREAD_STATUSES.index(status) if status in THREAD_STATUSES else len(THREAD_STATUSES)


def lab_list(
    vault: Path, labels: Mapping[str, str] | None, *, full: bool, now: _dt.datetime | None = None
) -> dict[str, Any]:
    """Every readable thread as a summary row, plus what needs the owner.

    ``labels`` is the reader's visibility map. Unless ``full``, a thread is left out when its front
    page is not in ``labels`` or its own frontmatter says ``visibility: private``, and an experiment when its
    note isn't labeled ``normal`` there (the module docstring).
    """
    moment = _moment(vault, now)
    seen = labels or {}
    rows: list[tuple[Thread, dict[str, Any]]] = []
    for thread in ops._threads(vault):
        rel = _rel(vault, paths.front_page(vault, thread.slug))
        if not full and (rel not in seen or thread.visibility == "private"):
            continue
        try:
            row = _summary(vault, thread, moment, _hidden_experiments(vault, thread, labels, full))
        except Exception as exc:  # noqa: BLE001 - one malformed thread must not empty the whole list
            row = _broken_summary(vault, thread, exc)
        rows.append((thread, row))
    rows.sort(key=lambda pair: (_status_rank(pair[0].status), pair[0].title.lower(), pair[0].slug))
    return {
        "schema_version": SCHEMA_VERSION,
        "threads": [row for _, row in rows],
        "needs_you": {
            "proposals": sum(1 for t, _ in rows if t.status == "proposed"),
            "close_proposals": sum(1 for t, _ in rows if _proposes_close(t)),
        },
    }


# --- show ------------------------------------------------------------------------------


def _comment(c: Comment) -> dict[str, Any]:
    return {
        "author": c.author,
        "text": c.text,
        "stamp": c.stamp,
        "line": c.line,
        "replies": [_comment(r) for r in c.replies],
    }


def _question(q: Question) -> dict[str, Any]:
    return {
        "qid": q.qid,
        "text": q.text,
        "status": q.status,
        "when": q.when,
        "note": q.note,
        "line": q.line,
        "comments": [_comment(c) for c in q.comments],
    }


def _experiment(vault: Path, slug: str, e: ExperimentRef) -> dict[str, Any]:
    frontmatter: dict[str, Any] | None = None
    note = paths.experiment_note(vault, slug, e.eid)
    if note is not None:
        try:
            frontmatter = _json(parse_experiment(slug, note.read_text(encoding="utf-8")).frontmatter)
        except (OSError, UnicodeDecodeError):
            frontmatter = None
    return {
        "eid": e.eid,
        "target": e.target,
        "title": e.title,
        "status": e.status,
        "when": e.when,
        "note": e.note,
        "line": e.line,
        "comments": [_comment(c) for c in e.comments],
        "frontmatter": frontmatter,
    }


def _evidence(e: EvidenceRef) -> dict[str, Any]:
    return {"target": e.target, "why": e.why, "line": e.line}


def _problem(p: Problem) -> dict[str, Any]:
    return {"line": p.line, "message": p.message}


def lab_thread(
    vault: Path,
    slug: str,
    *,
    labels: Mapping[str, str] | None = None,
    full: bool = True,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Everything the front page and timeline hold for one thread, as JSON. Raises ``LabError`` for an unknown slug.

    ``labels`` is the reader's visibility map and ``full`` whether the reader sees everything. Unless ``full``,
    the experiments whose note isn't labeled ``normal`` in ``labels`` are left out and counted in
    ``private_experiments`` (the module docstring). Whether the reader may see the thread itself is the caller's
    check (``lab show``)."""
    del now  # accepted for symmetry with lab_list; nothing in this document is relative to the clock
    paths.check_slug(slug)
    thread = load_thread(vault, slug)
    hidden = _hidden_experiments(vault, thread, labels, full)
    timeline_file = paths.timeline_path(vault, slug)
    try:
        entries = parse_timeline(timeline_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        entries = []
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": thread.slug,
        "path": _rel(vault, paths.front_page(vault, slug)),
        "title": thread.title,
        "status": thread.status,
        "visibility": thread.visibility,
        "frontmatter": _json(thread.frontmatter),
        "question": thread.question,
        "understanding": thread.understanding,
        "open": [_question(q) for q in thread.open],
        "answered": [_question(q) for q in thread.answered],
        "experiments": [_experiment(vault, slug, e) for e in thread.experiments if e.eid not in hidden],
        "private_experiments": len(hidden),
        "evidence": [_evidence(e) for e in thread.evidence],
        "comments": [_comment(c) for c in thread.comments],
        "unanswered_comments": _unanswered(thread, hidden),
        "timeline": [{"stamp": t.stamp, "tag": t.tag, "bullets": list(t.bullets)} for t in entries[:TIMELINE_LIMIT]],
        "problems": [_problem(p) for p in thread.problems],
    }

"""Experiment lifecycle at the file level: plan, start, finish, abandon, veto, and ``holds``.

An experiment is a note (``experiments/<slug>-<EID>-<short>.md``) plus one line on the thread's front
page. Every verb changes both under one lab lock, written through ``_write_files`` so a failure puts
the earlier file back. The run broker (not this module) enforces notice, veto and limits against git;
this module records and checks them in the files. Nothing here commits.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scout import config
from scout.errors import LabError
from scout.lab import ops, paths, writer
from scout.lab.model import EID_RE, Comment, Experiment, Thread, TimelineEntry
from scout.lab.parser import fenced, h2_name, parse_experiment, split_lines

_SHORT_RE = re.compile(r"^[a-z0-9-]{1,32}\Z")
_AUTHOR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*\Z")
_FM_TIME = "%Y-%m-%dT%H:%M"
_REQUIRED = ("Hypothesis", "Method", "Data")
_RESERVED = ("Result", "What it means")


# --- helpers ---------------------------------------------------------------------------


def _moment(vault: Path, now: _dt.datetime | None) -> _dt.datetime:
    return now if now is not None else config.now(vault)


def as_datetime(value: Any, tz: _dt.tzinfo | None, what: str) -> _dt.datetime:
    """A frontmatter time as an aware datetime; a naive one is read in ``tz``."""
    if isinstance(value, _dt.datetime):
        got = value
    elif isinstance(value, _dt.date):
        got = _dt.datetime(value.year, value.month, value.day)
    else:
        try:
            got = _dt.datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise LabError(f"experiment note has a bad {what} time {value!r}; expected YYYY-MM-DDTHH:MM") from None
    return got if got.tzinfo is not None else got.replace(tzinfo=tz)


def _shown(moment: _dt.datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _check_eid(eid: str) -> None:
    if not re.fullmatch(EID_RE, eid):
        raise LabError(f"'{eid}' is not an experiment ID (E<n>)")


def _lab_setting(vault: Path, key: str, default: float, *, positive: bool) -> float:
    """A numeric ``lab:`` setting: finite, and greater than 0 (``positive``) or at least 0."""
    raw = ops._lab_settings(vault).get(key, default)
    if isinstance(raw, bool):
        raise LabError(f"lab.{key} is {raw!r}; it must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise LabError(f"lab.{key} is {raw!r}; it must be a number") from None
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        bound = "greater than 0" if positive else "0 or more"
        raise LabError(f"lab.{key} is {raw!r}; it must be a finite number {bound}")
    return value


def _load(vault: Path, slug: str, eid: str, *, active: bool = True) -> tuple[Thread, str, Path, str, Experiment]:
    """Front page text and thread, the experiment's note path, text and parsed form. Call under the lock."""
    paths.check_slug(slug)
    _check_eid(eid)
    front = paths.front_page(vault, slug)
    text = ops._read(front)
    thread = ops.guard_headings(slug, text)
    if active:
        ops.require_active(thread)
    note = paths.experiment_note(vault, slug, eid)
    if note is None:
        raise LabError(f"{slug}: no experiment note for {eid}")
    note_text = ops._read(note)
    return thread, text, note, note_text, parse_experiment(slug, note_text)


def _ref(thread: Thread, eid: str, slug: str) -> Any:
    ref = next((e for e in thread.experiments if e.eid == eid), None)
    if ref is None:
        raise LabError(f"{slug}: {eid} is not listed under '## Experiments'; fix the page by hand")
    return ref


def _fill_section(text: str, name: str, body: str) -> str:
    lines = writer._split(text)
    heading, end = writer._section(lines, name)
    lines[heading + 1 : end] = ["", *writer._prose(body, name), ""]
    return writer._join(lines)


def _set_line(
    front_text: str, thread: Thread, eid: str, slug: str, status: str, when: str, note: str | None = None
) -> str:
    ref = _ref(thread, eid, slug)
    return writer.set_experiment_line(front_text, eid, ref.target, ref.title, status, when, note)


def holds(thread: Thread, exp: Experiment) -> list[Comment]:
    """Unanswered owner comments on the experiment's front-page line or on any question it lists."""
    wanted = {exp.eid, *exp.questions}
    return [c for target, c in thread.unanswered_owner_comments() if target in wanted]


# --- plan ------------------------------------------------------------------------------


def plan(
    vault: Path,
    slug: str,
    short: str,
    title: str,
    questions: list[str],
    body_md: str,
    cap_usd: float | None = None,
    now: _dt.datetime | None = None,
) -> Experiment:
    """Plan an experiment: write its note and put ``planned · runs after <not_before>`` on the front page."""
    paths.check_slug(slug)
    if not _SHORT_RE.match(short):
        raise LabError(f"bad experiment short name '{short}'; use 1-32 of a-z, 0-9 and '-'")
    lines = split_lines(body_md)
    present = {n for ln, skip in zip(lines, fenced(lines), strict=True) if not skip and (n := h2_name(ln))}
    for heading in _REQUIRED:
        if heading not in present:
            raise LabError(f"experiment body needs a '## {heading}' section")
    for heading in _RESERVED:
        if heading in present:
            raise LabError(f"experiment body must not contain '## {heading}'; plan adds it empty")
    if cap_usd is not None:
        if isinstance(cap_usd, bool) or not math.isfinite(cap_usd) or cap_usd <= 0:
            raise LabError(f"cost cap is {cap_usd!r}; it must be a finite number greater than 0")
        cap = float(cap_usd)
    else:
        cap = _lab_setting(vault, "experiment_cost_cap_usd", 1.0, positive=True)
    notice = _lab_setting(vault, "experiment_notice_hours", 6.0, positive=False)
    planned = _moment(vault, now)
    not_before = planned + _dt.timedelta(hours=notice)
    clean_title = writer._norm(title, "experiment title", dot=True)

    front = paths.front_page(vault, slug)
    with ops.lab_lock(vault):
        text = ops._read(front)
        thread = ops.guard_headings(slug, text)
        ops.require_active(thread)
        if not thread.frontmatter or thread.visibility == "private":
            raise LabError(
                f"{slug}: experiments in a private thread aren't supported yet; "
                "an experiment's scripts and outputs aren't markdown, so they couldn't be labelled private"
            )
        open_ids = {q.qid for q in thread.open}
        for qid in questions:
            if qid not in open_ids:
                raise LabError(f"{slug}: question {qid} is not an open question")
        raw = thread.frontmatter.get("next_e")
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 9999:
            raise LabError(f"{slug}: frontmatter next_e is {raw!r}; it must be an integer from 1 to 9999")
        eid = f"E{raw}"
        if any(e.eid == eid for e in thread.experiments) or paths.experiment_note(vault, slug, eid) is not None:
            raise LabError(f"{slug}: next_e is {raw} but {eid} already exists; fix next_e on the front page by hand")

        stem = f"{slug}-{eid}-{short}"
        note = paths.thread_dir(vault, slug) / "experiments" / f"{stem}.md"
        note_text = "\n".join(
            [
                "---",
                "type: lab-experiment",
                f"id: {eid}",
                f"thread: {slug}",
                f"questions: [{', '.join(questions)}]",
                "status: planned",
                f"planned: {planned.strftime(_FM_TIME)}",
                f"not_before: {not_before.strftime(_FM_TIME)}",
                f"cost_cap_usd: {cap:.2f}",
                f"visibility: {writer._scalar(thread.visibility)}",
                "---",
                body_md.strip(),
                "",
                "## Result",
                "",
                "## What it means",
                "",
            ]
        )
        new_front = writer.set_experiment_line(
            text, eid, stem, clean_title, "planned", f"runs after {_shown(not_before)}"
        )
        new_front = writer.set_frontmatter(new_front, next_e=raw + 1)
        note.parent.mkdir(parents=True, exist_ok=True)
        ops._write_files([(note, note_text), (front, new_front)])
        return parse_experiment(slug, note_text)


# --- start / finish / abandon ----------------------------------------------------------


def start(vault: Path, slug: str, eid: str, now: _dt.datetime | None = None) -> Experiment:
    """planned -> running. Refuses before ``not_before``, while owner comments hold it, or from any other status."""
    paths.check_slug(slug)
    moment = _moment(vault, now)
    with ops.lab_lock(vault):
        thread, text, note, note_text, exp = _load(vault, slug, eid)
        if exp.status != "planned":
            raise LabError(f"{eid} is {exp.status}, not planned")
        not_before = as_datetime(exp.frontmatter.get("not_before"), moment.tzinfo, "not_before")
        if moment < not_before:
            raise LabError(f"{eid} cannot start yet: not before {_shown(not_before)}")
        held = holds(thread, exp)
        if held:
            raise LabError(f"{eid} is held by {len(held)} comment(s) from the owner; answer them first")
        new_note = writer.set_frontmatter(note_text, status="running", started=moment.strftime(_FM_TIME))
        new_front = _set_line(text, thread, eid, slug, "running", f"started {_shown(moment)}")
        ops._write_files([(note, new_note), (paths.front_page(vault, slug), new_front)])
        return parse_experiment(slug, new_note)


def finish(
    vault: Path,
    slug: str,
    eid: str,
    result_md: str,
    means_md: str,
    one_line: str,
    now: _dt.datetime | None = None,
) -> Experiment:
    """running -> done. Fills ``## Result`` and ``## What it means`` and sets ``done · <day> — <one_line>``."""
    paths.check_slug(slug)
    moment = _moment(vault, now)
    with ops.lab_lock(vault):
        thread, text, note, note_text, exp = _load(vault, slug, eid)
        if exp.status != "running":
            raise LabError(f"{eid} is {exp.status}, not running")
        new_note = writer.set_frontmatter(note_text, status="done", finished=moment.strftime(_FM_TIME))
        new_note = _fill_section(new_note, "Result", result_md)
        new_note = _fill_section(new_note, "What it means", means_md)
        new_front = _set_line(text, thread, eid, slug, "done", _shown(moment)[:10], one_line)
        ops._write_files([(note, new_note), (paths.front_page(vault, slug), new_front)])
        return parse_experiment(slug, new_note)


def abandon(vault: Path, slug: str, eid: str, reason: str, now: _dt.datetime | None = None) -> Experiment:
    """planned or running -> abandoned, with the reason on the front-page line."""
    paths.check_slug(slug)
    moment = _moment(vault, now)
    with ops.lab_lock(vault):
        thread, text, note, note_text, exp = _load(vault, slug, eid)
        if exp.status not in ("planned", "running"):
            raise LabError(f"{eid} is {exp.status}; only a planned or running experiment can be abandoned")
        new_note = writer.set_frontmatter(note_text, status="abandoned", finished=moment.strftime(_FM_TIME))
        new_front = _set_line(text, thread, eid, slug, "abandoned", _shown(moment)[:10], reason)
        ops._write_files([(note, new_note), (paths.front_page(vault, slug), new_front)])
        return parse_experiment(slug, new_note)


# --- veto ------------------------------------------------------------------------------


def veto(
    vault: Path,
    slug: str,
    eid: str,
    author: str,
    now: _dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> Experiment:
    """Owner verb: planned -> vetoed, with an ``owner`` timeline entry. Allowed on active or paused threads.

    The veto also goes into the owner's store (:func:`scout.sandbox.broker.record_veto`, in the folder ``env``'s
    HOME names; default this process's), which a run can't write, so the broker refuses the experiment even if a
    run rewrites the files before they are committed. That store is written first: when it can't be, this raises
    and no file changes."""
    from scout.sandbox import broker

    paths.check_slug(slug)
    if not _AUTHOR_RE.match(author):
        raise LabError(f"bad author {author!r}")
    moment = _moment(vault, now)
    timeline = paths.timeline_path(vault, slug)
    with ops.lab_lock(vault):
        thread, text, note, note_text, exp = _load(vault, slug, eid, active=False)
        if thread.status not in ("active", "paused"):
            raise LabError(f"cannot veto on {ops._article(thread.status)} {thread.status} thread")
        if exp.status != "planned":
            raise LabError(f"cannot veto: {eid} is {exp.status}; only a planned experiment can be vetoed")
        entry = TimelineEntry(_shown(moment), "owner", [f"**Veto:** {eid} by {author}"])
        new_timeline = ops._timeline_with(slug, thread, entry, timeline)
        new_note = writer.set_frontmatter(note_text, status="vetoed")
        new_front = _set_line(text, thread, eid, slug, "vetoed", _shown(moment)[:10], f"by {author}")
        try:
            broker.record_veto(os.environ if env is None else env, vault, slug, eid, moment)
        except OSError as exc:
            raise LabError(
                f"cannot record the veto of {eid} in the owner's veto store ({exc.__class__.__name__}: {exc}); "
                "nothing was changed. Inside a scheduled run the owner's folder can't be written: veto from the app "
                "or the owner's own session"
            ) from None
        # The store holds the veto now, so a failure below still leaves it in force.
        # Timeline first: a failure after it leaves an audit entry with no status change, never the reverse.
        ops._write_files([(timeline, new_timeline), (note, new_note), (paths.front_page(vault, slug), new_front)])
        return parse_experiment(slug, new_note)

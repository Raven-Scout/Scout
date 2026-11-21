"""Markdown to lab model. Tolerant: a hand-edited page yields Problems, never exceptions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scout.errors import LabError
from scout.kb.frontmatter import frontmatter_block
from scout.lab import paths
from scout.lab.model import (
    EID_RE,
    EXPERIMENT_HEADINGS,
    EXPERIMENT_STATUSES,
    FRONT_HEADINGS,
    QID_RE,
    QUESTION_STATUSES,
    THREAD_STATUSES,
    Comment,
    EvidenceRef,
    Experiment,
    ExperimentRef,
    Problem,
    Question,
    Thread,
    TimelineEntry,
)

# "<text> · <status> · <when>[ — <note>]". Text is greedy, so it keeps any
# middle dots of its own; <when> runs up to the first " — ".
_TAIL_RE = re.compile(r"^(?P<text>.*) · (?P<status>[A-Za-z][\w-]*) · (?P<when>.*?)(?: — (?P<note>.*))?$")
_QUESTION_RE = re.compile(rf"^- \*\*(?P<id>{QID_RE})\*\* (?P<rest>.*)$")
_EXPERIMENT_RE = re.compile(r"^- \[\[(?P<target>[^\]|]+)\|(?P<id>" + EID_RE + r")\]\] (?P<rest>.*)$")
_EVIDENCE_RE = re.compile(r"^- \[\[(?P<target>[^\]|]+)(?:\|[^\]]*)?\]\] — (?P<why>.*)$")
_COMMENT_RE = re.compile(
    r"^(?P<indent> *)- (?P<author>[A-Za-z][A-Za-z0-9._-]*)(?: \((?P<stamp>[^)]*)\))?: (?P<text>.*)$"
)
_TIMELINE_RE = re.compile(r"^### (?P<stamp>\d{4}-\d\d-\d\d \d\d:\d\d) · (?P<tag>.+?)\s*$")
_H2_RE = re.compile(r"^## (?P<name>.+?)\s*$")
_H1_RE = re.compile(r"^# (?P<name>.+?)\s*$")

_ITEM_INDENT = 2  # comment indent under a question or experiment line
_STEP = 2  # extra indent for a reply


def _split_frontmatter(text: str, problems: list[Problem]) -> tuple[dict[str, Any], list[str], int]:
    """Frontmatter dict, the body lines, and the body's 0-based offset in the file."""
    lines = text.removeprefix("\ufeff").split("\n")
    block = frontmatter_block(lines)
    if block is None:
        problems.append(Problem(1, "missing or unterminated frontmatter block"))
        return {}, lines, 0
    offset = len(block) + 2
    try:
        data = yaml.safe_load("\n".join(block)) or {}
    except (yaml.YAMLError, ValueError, TypeError, OverflowError) as exc:  # e.g. created: 2026-13-45
        problems.append(Problem(1, f"frontmatter is not valid YAML: {exc}"))
        return {}, lines[offset:], offset
    if not isinstance(data, dict):
        problems.append(Problem(1, "frontmatter is not a mapping"))
        return {}, lines[offset:], offset
    return data, lines[offset:], offset


def _sections(lines: list[str], offset: int, problems: list[Problem]) -> dict[str, tuple[int, list[str]]]:
    """``## `` sections as ``name -> (first body line number, body lines)``.

    Line numbers are 1-based in the whole file.
    """
    out: dict[str, tuple[int, list[str]]] = {}
    current: list[str] | None = None
    for i, raw in enumerate(lines):
        lineno = offset + i + 1
        m = _H2_RE.match(raw)
        if m:
            name = m.group("name")
            if name in out:
                problems.append(Problem(lineno, f"duplicate heading '## {name}'"))
                current = None
                continue
            current = []
            out[name] = (lineno + 1, current)
        elif current is not None:
            current.append(raw)
    return out


def _check_headings(
    sections: dict[str, tuple[int, list[str]]], expected: tuple[str, ...], problems: list[Problem]
) -> None:
    for name in expected:
        if name not in sections:
            problems.append(Problem(None, f"missing heading '## {name}'"))
    for name, (start, _) in sections.items():
        if name not in expected:
            problems.append(Problem(start - 1, f"unrecognised heading '## {name}'"))
    seen = [n for n in sections if n in expected]
    if seen != [n for n in expected if n in seen]:
        problems.append(Problem(None, "headings are out of order: expected " + ", ".join(expected)))


def _prose(body: list[str]) -> str:
    return "\n".join(body).strip()


def _parse_item_tail(rest: str, lineno: int, problems: list[Problem]) -> tuple[str, str, str, str | None] | None:
    m = _TAIL_RE.match(rest)
    if not m:
        problems.append(Problem(lineno, f"line is not '<text> · <status> · <when>': {rest!r}"))
        return None
    return m.group("text"), m.group("status"), m.group("when"), m.group("note")


def _parse_comment(m: re.Match[str], lineno: int) -> Comment:
    return Comment(author=m.group("author"), text=m.group("text"), stamp=m.group("stamp"), line=lineno)


def _attach_comment(
    m: re.Match[str],
    lineno: int,
    base: int,
    target: list[Comment],
    last_top: Comment | None,
    problems: list[Problem],
) -> Comment | None:
    """File the comment at ``lineno`` as a top-level comment or a reply. Returns the new last top."""
    indent = len(m.group("indent"))
    comment = _parse_comment(m, lineno)
    if indent == base:
        target.append(comment)
        return comment
    if indent == base + _STEP and last_top is not None:
        last_top.replies.append(comment)
        return last_top
    problems.append(Problem(lineno, f"comment indented {indent} spaces, expected {base} or {base + _STEP}"))
    return last_top


def _parse_questions(
    body: list[str], first_line: int, expect: tuple[str, ...], section: str, problems: list[Problem]
) -> list[Question]:
    out: list[Question] = []
    last_top: Comment | None = None
    for i, raw in enumerate(body):
        lineno = first_line + i
        if not raw.strip():
            continue
        qm = _QUESTION_RE.match(raw)
        if qm:
            last_top = None
            tail = _parse_item_tail(qm.group("rest"), lineno, problems)
            if tail is None:
                out.append(Question(qm.group("id"), qm.group("rest"), "", "", None, lineno))
                continue
            text, status, when, note = tail
            if status not in QUESTION_STATUSES:
                problems.append(Problem(lineno, f"unknown question status '{status}'"))
            elif status not in expect:
                problems.append(
                    Problem(lineno, f"question {qm.group('id')} is '{status}' but sits under '## {section}'")
                )
            out.append(Question(qm.group("id"), text, status, when, note, lineno))
            continue
        cm = _COMMENT_RE.match(raw)
        if cm and out:
            last_top = _attach_comment(cm, lineno, _ITEM_INDENT, out[-1].comments, last_top, problems)
            continue
        problems.append(Problem(lineno, f"unrecognised line under '## {section}': {raw.strip()!r}"))
    return out


def _parse_experiments(body: list[str], first_line: int, problems: list[Problem]) -> list[ExperimentRef]:
    out: list[ExperimentRef] = []
    last_top: Comment | None = None
    for i, raw in enumerate(body):
        lineno = first_line + i
        if not raw.strip():
            continue
        em = _EXPERIMENT_RE.match(raw)
        if em:
            last_top = None
            tail = _parse_item_tail(em.group("rest"), lineno, problems)
            if tail is None:
                out.append(ExperimentRef(em.group("id"), em.group("target"), em.group("rest"), "", "", None, lineno))
                continue
            title, status, when, note = tail
            if status not in EXPERIMENT_STATUSES:
                problems.append(Problem(lineno, f"unknown experiment status '{status}'"))
            out.append(ExperimentRef(em.group("id"), em.group("target"), title, status, when, note, lineno))
            continue
        cm = _COMMENT_RE.match(raw)
        if cm and out:
            last_top = _attach_comment(cm, lineno, _ITEM_INDENT, out[-1].comments, last_top, problems)
            continue
        problems.append(Problem(lineno, f"unrecognised line under '## Experiments': {raw.strip()!r}"))
    return out


def _parse_evidence(body: list[str], first_line: int, problems: list[Problem]) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    for i, raw in enumerate(body):
        lineno = first_line + i
        if not raw.strip():
            continue
        m = _EVIDENCE_RE.match(raw)
        if m:
            out.append(EvidenceRef(m.group("target"), m.group("why"), lineno))
        else:
            problems.append(Problem(lineno, f"unrecognised line under '## Evidence': {raw.strip()!r}"))
    return out


def _parse_thread_comments(body: list[str], first_line: int, problems: list[Problem]) -> list[Comment]:
    out: list[Comment] = []
    last_top: Comment | None = None
    for i, raw in enumerate(body):
        lineno = first_line + i
        if not raw.strip():
            continue
        cm = _COMMENT_RE.match(raw)
        if cm:
            last_top = _attach_comment(cm, lineno, 0, out, last_top, problems)
        else:
            problems.append(Problem(lineno, f"unrecognised line under '## Comments': {raw.strip()!r}"))
    return out


def _check_unique_ids(items: list[Question] | list[ExperimentRef], problems: list[Problem]) -> None:
    seen: set[str] = set()
    for item in items:
        key = item.qid if isinstance(item, Question) else item.eid
        if key in seen:
            problems.append(Problem(item.line, f"duplicate ID {key}"))
        seen.add(key)


def parse_front_page(slug: str, text: str) -> Thread:
    problems: list[Problem] = []
    fm, body, offset = _split_frontmatter(text, problems)
    if fm and str(fm.get("status", "")) not in THREAD_STATUSES:
        problems.append(Problem(1, f"unknown thread status '{fm.get('status', '')}'"))

    title = str(fm.get("title") or "")
    for raw in body:
        m = _H1_RE.match(raw)
        if m:
            title = m.group("name")
            break

    sections = _sections(body, offset, problems)
    _check_headings(sections, FRONT_HEADINGS, problems)

    def sect(name: str) -> tuple[int, list[str]]:
        return sections.get(name, (0, []))

    open_q = _parse_questions(*_args(sect("Open questions")), ("open",), "Open questions", problems)
    answered = _parse_questions(
        *_args(sect("Answered questions")), ("answered", "dropped"), "Answered questions", problems
    )
    experiments = _parse_experiments(*_args(sect("Experiments")), problems)
    evidence = _parse_evidence(*_args(sect("Evidence")), problems)
    comments = _parse_thread_comments(*_args(sect("Comments")), problems)
    _check_unique_ids([*open_q, *answered], problems)
    _check_unique_ids(experiments, problems)

    return Thread(
        slug=slug,
        frontmatter=fm,
        title=title,
        question=_prose(sect("Question")[1]),
        understanding=_prose(sect("Current understanding")[1]),
        open=open_q,
        answered=answered,
        experiments=experiments,
        evidence=evidence,
        comments=comments,
        problems=sorted(problems, key=lambda p: (p.line is None, p.line or 0)),
    )


def _args(section: tuple[int, list[str]]) -> tuple[list[str], int]:
    start, body = section
    return body, start


def parse_timeline(text: str) -> list[TimelineEntry]:
    """Timeline entries in file order (newest first, as written). Unrecognised lines are skipped."""
    entries: list[TimelineEntry] = []
    for raw in text.removeprefix("\ufeff").split("\n"):
        m = _TIMELINE_RE.match(raw)
        if m:
            entries.append(TimelineEntry(m.group("stamp"), m.group("tag"), []))
        elif entries and raw.startswith("- "):
            entries[-1].bullets.append(raw[2:].rstrip())
    return entries


def parse_experiment(slug: str, text: str) -> Experiment:
    problems: list[Problem] = []
    fm, body, offset = _split_frontmatter(text, problems)
    sections = _sections(body, offset, problems)
    _check_headings(sections, EXPERIMENT_HEADINGS, problems)

    status = str(fm.get("status", ""))
    if fm and status not in EXPERIMENT_STATUSES:
        problems.append(Problem(1, f"unknown experiment status '{status}'"))
    eid = str(fm.get("id", ""))
    if fm and not re.fullmatch(EID_RE, eid):
        problems.append(Problem(1, f"experiment id '{eid}' is not E<n>"))
    raw_q = fm.get("questions") or []
    questions = [str(q) for q in raw_q] if isinstance(raw_q, list) else [str(raw_q)]
    return Experiment(
        slug=slug,
        eid=eid,
        frontmatter=fm,
        status=status,
        questions=questions,
        sections={name: _prose(lines) for name, (_, lines) in sections.items()},
        problems=problems,
    )


def load_thread(vault: Path, slug: str) -> Thread:
    path = paths.front_page(vault, slug)
    if not path.is_file():
        raise LabError(f"no lab thread '{slug}' (expected {path})")
    return parse_front_page(slug, path.read_text(encoding="utf-8"))

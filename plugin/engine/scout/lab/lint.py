"""``lab-format``: the kb-lint check for files under ``knowledge-base/lab/``.

:func:`lab_findings` reads one file's text and, for the file kinds the lab format defines (a
thread's front page, its timeline, an experiment note, ``lab.md``), reports what a hand edit or a
bad write broke. Parsing is the parser's: its problems become findings as they are, and this module
adds the cross-checks the parser can't see (IDs the page promises vs the IDs it uses, the timeline's
references, the experiment's times, ``lab.md``'s coverage of the thread folders).

``lab-format`` findings block in every kb-lint mode (``lint_config.ALWAYS_BLOCKING``); ``SCOUT_LINT_OVERRIDE``
still overrides them. ``lab-understanding-size`` is a warning.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from scout.errors import LabError
from scout.kb.lint import Finding
from scout.lab import paths
from scout.lab.model import EID_RE, QID_RE, TIMELINE_TAGS, Thread
from scout.lab.parser import parse_experiment, parse_front_page

CHECK = "lab-format"
SIZE_CHECK = "lab-understanding-size"
UNDERSTANDING_BUDGET = 2048
EXPERIMENT_KEYS = ("id", "thread", "questions", "status", "planned", "not_before", "cost_cap_usd")

_PREFIX = "knowledge-base/lab/"
_TIMELINE_H1 = re.compile(r"^# .+ — timeline\s*$")
_TIMELINE_HEADING = re.compile(r"^### \d{4}-\d\d-\d\d \d\d:\d\d · (?P<tag>.+?)\s*$")
_QID = re.compile(rf"(?<![\w-]){QID_RE}(?!\w)")
_EID = re.compile(rf"(?<![\w-]){EID_RE}(?!\w)")
_EXPERIMENT_NAME = re.compile(rf"^(?P<slug>.+)-(?P<eid>{EID_RE})-.+\.md$")
_LINK = re.compile(r"\[\[([^\]|#]+)")
_PRIVATE_COUNT = re.compile(r"^_(\d+) private thread\(s\) not listed\._\s*$", re.MULTILINE)


def _bad(rel: str, message: str, line: int | None = None) -> Finding:
    return Finding(rel, CHECK, True, message, line)


def _line_of(text: str, prefix: str) -> int:
    """1-based line of the first line starting with ``prefix``; 1 when there is none."""
    for n, raw in enumerate(text.removeprefix("\ufeff").split("\n"), 1):
        if raw.startswith(prefix):
            return n
    return 1


def lab_findings(repo: Path, rel: str, text: str) -> list[Finding]:
    """Findings for one file under ``knowledge-base/lab/`` (``rel`` is vault-relative, ``text`` its content)."""
    if not rel.startswith(_PREFIX) or not rel.endswith(".md"):
        return []
    parts = rel[len(_PREFIX) :].split("/")
    if parts == ["lab.md"]:
        return _index(repo, rel, text)
    if len(parts) == 2:
        slug, name = parts
        if name == f"{slug}.md":
            return _front(rel, slug, text)
        if name == f"{slug}-timeline.md":
            return _timeline(repo, rel, slug, text)
    if len(parts) == 3 and parts[1] == "experiments" and parts[2].startswith(f"{parts[0]}-E"):
        return _experiment(rel, parts[0], parts[2], text)
    return []


# --- front page ------------------------------------------------------------------------


def _counter(rel: str, text: str, thread: Thread, key: str, used: list[int], noun: str) -> list[Finding]:
    raw = thread.frontmatter.get(key)
    line = _line_of(text, f"{key}:")
    if not used and raw is None:
        return []
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 9999:
        return [_bad(rel, f"frontmatter {key} is {raw!r}; it must be an integer from 1 to 9999", line)]
    if used and raw <= max(used):
        top = f"{noun}{max(used)}"
        return [_bad(rel, f"{key} is {raw} but {top} is already used; {key} must be above every ID in use", line)]
    return []


def _id_numbers(ids: list[str]) -> list[int]:
    return [int(i[1:]) for i in ids if i[1:].isdigit()]


def _front(rel: str, slug: str, text: str) -> list[Finding]:
    thread = parse_front_page(slug, text)
    out = [_bad(rel, p.message, p.line) for p in thread.problems]
    questions = _id_numbers([q.qid for q in [*thread.open, *thread.answered]])
    experiments = _id_numbers([e.eid for e in thread.experiments])
    if thread.frontmatter:
        out += _counter(rel, text, thread, "next_q", questions, "Q")
        out += _counter(rel, text, thread, "next_e", experiments, "E")
    if len(thread.understanding.encode("utf-8")) > UNDERSTANDING_BUDGET:
        out.append(
            Finding(
                rel,
                SIZE_CHECK,
                False,
                f"Current understanding is over {UNDERSTANDING_BUDGET} bytes; it is the thread's summary, "
                "so keep it short and move detail to the timeline or a topic note",
            )
        )
    return out


# --- timeline --------------------------------------------------------------------------


def _read_front(repo: Path, slug: str) -> Thread | None:
    try:
        return parse_front_page(slug, paths.front_page(repo, slug).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _timeline(repo: Path, rel: str, slug: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    lines = text.removeprefix("\ufeff").split("\n")
    first = next((i for i, raw in enumerate(lines) if raw.strip()), None)
    if first is None or not _TIMELINE_H1.match(lines[first]):
        out.append(_bad(rel, "the file must start with '# <title> — timeline'", (first or 0) + 1))
    thread = _read_front(repo, slug)
    if thread is None:
        out.append(_bad(rel, f"no front page for thread '{slug}' (expected {paths.front_page(repo, slug).name})"))
    qids = {q.qid for q in [*thread.open, *thread.answered]} if thread else set()
    eids = {e.eid for e in thread.experiments} if thread else set()
    for n, raw in enumerate(lines, 1):
        if raw.startswith("### "):
            m = _TIMELINE_HEADING.match(raw)
            if m is None:
                out.append(_bad(rel, "timeline heading is not '### YYYY-MM-DD HH:MM · <tag>'", n))
            elif m.group("tag") not in TIMELINE_TAGS:
                out.append(
                    _bad(rel, f"unknown timeline tag '{m.group('tag')}'; use one of {', '.join(TIMELINE_TAGS)}", n)
                )
            continue
        if thread is None or raw.startswith("# "):
            continue
        out += [
            _bad(rel, f"{token} is not a question of thread '{slug}'", n)
            for token in dict.fromkeys(_QID.findall(raw))
            if token not in qids
        ]
        out += [
            _bad(rel, f"{token} is not an experiment of thread '{slug}'", n)
            for token in dict.fromkeys(_EID.findall(raw))
            if token not in eids
        ]
    return out


# --- experiment note -------------------------------------------------------------------


def _experiment(rel: str, slug: str, name: str, text: str) -> list[Finding]:
    exp = parse_experiment(slug, text)
    out = [_bad(rel, p.message, p.line) for p in exp.problems]
    fm = exp.frontmatter
    if not fm:
        return out
    out += [_bad(rel, f"frontmatter is missing '{key}'", 1) for key in EXPERIMENT_KEYS if key not in fm]
    if "thread" in fm and str(fm["thread"]) != slug:
        out.append(_bad(rel, f"frontmatter thread is '{fm['thread']}', but the note lives under '{slug}'", 1))
    named = _EXPERIMENT_NAME.match(name)
    if named and "id" in fm and str(fm["id"]) != named.group("eid"):
        out.append(_bad(rel, f"frontmatter id is '{fm['id']}', but the file name says {named.group('eid')}", 1))
    if "planned" in fm and "not_before" in fm:
        line = _line_of(text, "not_before:")
        try:
            planned = _as_naive(fm["planned"], "planned")
            not_before = _as_naive(fm["not_before"], "not_before")
        except LabError as exc:
            out.append(_bad(rel, str(exc), line))
        else:
            if not_before < planned:
                out.append(
                    _bad(rel, f"not_before ({fm['not_before']}) is earlier than planned ({fm['planned']})", line)
                )
    return out


def _as_naive(value: object, what: str) -> _dt.datetime:
    from scout.lab.experiments import as_datetime

    return as_datetime(value, _dt.UTC, what)


# --- lab.md ----------------------------------------------------------------------------


def _index(repo: Path, rel: str, text: str) -> list[Finding]:
    root = paths.lab_dir(repo)
    folders = (
        sorted(p.name for p in root.iterdir() if p.is_dir() and (p / f"{p.name}.md").is_file()) if root.is_dir() else []
    )
    linked = set(_LINK.findall(text))
    private = 0
    out: list[Finding] = []
    for slug in folders:
        thread = _read_front(repo, slug)
        if thread is not None and thread.visibility == "private":
            private += 1
        elif slug not in linked:
            out.append(_bad(rel, f"thread folder '{slug}' is not listed in lab.md"))
    counts = [int(n) for n in _PRIVATE_COUNT.findall(text)]
    shown = sum(counts)
    if shown != private:
        say = f"says {shown}" if counts else "has no such line"
        out.append(
            _bad(
                rel,
                f"there are {private} private thread folder(s) but the '_<n> private thread(s) not listed._' "
                f"line {say}",
            )
        )
    return out

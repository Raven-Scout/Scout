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
from typing import Any, Protocol

import yaml

from scout import plural
from scout.errors import LabError
from scout.kb.frontmatter import frontmatter_block
from scout.kb.lint import Finding
from scout.kb.privacy import visibility_value
from scout.lab import paths
from scout.lab.model import (
    EID_RE,
    QID_RE,
    TIMELINE_TAGS,
    Thread,
    disk_budget_of,
    model_repo_ok,
    model_revision_ok,
    timeout_hours_of,
)
from scout.lab.parser import fenced, parse_experiment, parse_front_page, split_lines

CHECK = "lab-format"
SIZE_CHECK = "lab-understanding-size"
UNDERSTANDING_BUDGET = 2048
EXPERIMENT_KEYS = ("id", "thread", "questions", "status", "planned", "not_before", "cost_cap_usd")

_PREFIX = "knowledge-base/lab/"
_TIMELINE_H1 = re.compile(r"^# .+ — timeline\s*$")
_TIMELINE_HEADING = re.compile(r"^### \d{4}-\d\d-\d\d \d\d:\d\d · (?P<tag>.+)$")
_QID = re.compile(rf"(?<![\w-]){QID_RE}(?!\w)")
_EID = re.compile(rf"(?<![\w-]){EID_RE}(?!\w)")
_ID_BULLETS = ("- **Questions:**", "- **Steers handled:**")
_EXPERIMENT_NAME = re.compile(rf"^(?P<slug>.+)-(?P<eid>{EID_RE})-.+\.md$")
_LINK = re.compile(r"\[\[([^\]|#]+)")
_PRIVATE_COUNT = re.compile(r"^_(\d+) private threads? not listed\._\s*$", re.MULTILINE)


def _bad(rel: str, message: str, line: int | None = None) -> Finding:
    return Finding(rel, CHECK, True, message, line)


def _line_of(text: str, prefix: str) -> int:
    """1-based line of the first line starting with ``prefix``; 1 when there is none."""
    for n, raw in enumerate(text.removeprefix("\ufeff").split("\n"), 1):
        if raw.startswith(prefix):
            return n
    return 1


INDEX_REL = f"{_PREFIX}lab.md"


class Source(Protocol):
    """Where the cross-file checks read the rest of the lab from."""

    def files(self) -> list[str]:
        """Vault-relative paths of the lab's markdown files."""
        ...

    def read(self, rel: str) -> str | None:
        """A file's text, or None when it does not exist or cannot be read."""
        ...


class WorkingTree:
    """The files on disk: ``scoutctl lab lint``."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def files(self) -> list[str]:
        root = paths.lab_dir(self.repo)
        if not root.is_dir():
            return []
        return sorted(p.relative_to(self.repo).as_posix() for p in root.rglob("*.md"))

    def read(self, rel: str) -> str | None:
        try:
            return (self.repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


class StagedIndex:
    """The git index: what a commit would contain, whatever the working tree holds."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self._files: list[str] | None = None

    def files(self) -> list[str]:
        if self._files is None:
            from scout.kb.git_io import git_bytes

            out = git_bytes(self.repo, "ls-files", "-z", "--", _PREFIX).stdout.decode("utf-8", errors="replace")
            self._files = sorted(p for p in out.split("\0") if p.endswith(".md"))
        return self._files

    def read(self, rel: str) -> str | None:
        from scout.kb.git_io import staged_text_lossy

        return staged_text_lossy(self.repo, rel) if rel in self.files() else None


def lab_findings(
    repo: Path, rel: str, text: str, source: Source | None = None, *, include_index: bool = True
) -> list[Finding]:
    """Findings for one file under ``knowledge-base/lab/`` (``rel`` is vault-relative, ``text`` its content).

    ``source`` is where sibling files are read from (default: the working tree). ``include_index`` False
    leaves ``lab.md``'s own check to :func:`index_findings`, for a caller that runs it once per commit.
    """
    if not rel.startswith(_PREFIX):
        return []
    source = source or WorkingTree(repo)
    parts = rel[len(_PREFIX) :].split("/")
    if not rel.endswith(".md"):
        return _private_non_markdown(source, rel, parts[0]) if len(parts) >= 2 else []
    if parts == ["lab.md"]:
        return index_findings(source, text) if include_index else []
    if len(parts) == 2 and parts[1] == f"{parts[0]}.md":
        return _front(rel, parts[0], text) + _private_siblings(source, rel, parts[0], text)
    out: list[Finding] = []
    if len(parts) == 2 and parts[1] == f"{parts[0]}-timeline.md":
        out = _timeline(source, rel, parts[0], text)
    elif len(parts) == 3 and parts[1] == "experiments" and parts[2].startswith(f"{parts[0]}-E"):
        out = _experiment(rel, parts[0], parts[2], text)
    return out + _private_thread(source, rel, parts[0], text) if len(parts) >= 2 else out


# --- private threads -------------------------------------------------------------------


def _says_private(text: str) -> bool:
    """Whether a note's own frontmatter parses and holds a ``visibility:`` the privacy layer reads as private
    (:func:`scout.kb.privacy.visibility_value`: ``Private`` and ``secret`` are private too)."""
    block = frontmatter_block(text.removeprefix("﻿").split("\n"))
    if block is None:
        return False
    try:
        data = yaml.safe_load("\n".join(block))
    except (yaml.YAMLError, ValueError, TypeError, OverflowError):
        return False
    return isinstance(data, dict) and "visibility" in data and visibility_value(data["visibility"]) == "private"


def _private_thread(source: Source, rel: str, slug: str, text: str) -> list[Finding]:
    """Every markdown file in a private thread's folder says ``visibility: private`` itself (spec §11): the
    privacy layer labels each file by its own frontmatter, so one that doesn't would be shown to a delegate,
    naming the thread."""
    thread = _read_front(source, slug)
    if thread is None or thread.visibility != "private" or _says_private(text):
        return []
    return [
        _bad(
            rel,
            f"thread '{slug}' is private, so this file must say 'visibility: private' in its frontmatter "
            "(a file without it is shown to delegates)",
            1,
        )
    ]


def _private_non_markdown(source: Source, rel: str, slug: str) -> list[Finding]:
    """A non-markdown file (an experiment's script or output) has no frontmatter to say it is private, so the
    privacy layer would label it normal and show it to a delegate: a private thread can't hold one."""
    thread = _read_front(source, slug)
    if thread is None or thread.visibility != "private":
        return []
    return [_bad(rel, f"thread '{slug}' is private, and a private thread can't hold non-markdown files", 1)]


def _private_siblings(source: Source, rel: str, slug: str, text: str) -> list[Finding]:
    """A private front page whose thread still holds files that don't say so (the thread was made private, or
    a file was added by hand): each is named here, since only the front page may be in the commit."""
    if parse_front_page(slug, text).visibility != "private":
        return []
    folder = f"{_PREFIX}{slug}/"
    out: list[Finding] = []
    for other in source.files():
        if other == rel or not other.startswith(folder):
            continue
        body = source.read(other)
        if body is not None and not _says_private(body):
            out.append(_bad(rel, f"thread '{slug}' is private but {other} doesn't say 'visibility: private'"))
    return out


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


def _read_front(source: Source, slug: str) -> Thread | None:
    text = source.read(f"{_PREFIX}{slug}/{slug}.md")
    return None if text is None else parse_front_page(slug, text)


def _timeline(source: Source, rel: str, slug: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    lines = split_lines(text)
    # A private thread's timeline opens with a frontmatter block holding only its visibility.
    block = frontmatter_block(lines)
    start = 0 if block is None else len(block) + 2
    if block is not None:
        try:
            data = yaml.safe_load("\n".join(block))
        except (yaml.YAMLError, ValueError, TypeError, OverflowError):
            data = None
        if not isinstance(data, dict) or set(data) != {"visibility"} or data["visibility"] not in ("normal", "private"):
            out.append(_bad(rel, "a timeline's frontmatter may hold only 'visibility: private' (or normal)", 1))
    first = next((i for i in range(start, len(lines)) if lines[i].strip()), None)
    if first is None or not _TIMELINE_H1.match(lines[first]):
        out.append(
            _bad(rel, "the file must start with '# <title> — timeline'", (start if first is None else first) + 1)
        )
    thread = _read_front(source, slug)
    if thread is None:
        out.append(_bad(rel, f"no front page for thread '{slug}' (expected {slug}.md)"))
    qids = {q.qid for q in [*thread.open, *thread.answered]} if thread else set()
    eids = {e.eid for e in thread.experiments} if thread else set()
    in_fence = [False] * start + fenced(lines[start:])
    for n, raw in enumerate(lines, 1):
        if in_fence[n - 1]:
            continue
        if raw.startswith("### "):
            m = _TIMELINE_HEADING.match(raw)
            if m is None:
                out.append(_bad(rel, "timeline heading is not '### YYYY-MM-DD HH:MM · <tag>'", n))
            elif m.group("tag").rstrip() not in TIMELINE_TAGS:
                out.append(
                    _bad(
                        rel,
                        f"unknown timeline tag '{m.group('tag').rstrip()}'; use one of {', '.join(TIMELINE_TAGS)}",
                        n,
                    )
                )
            continue
        # IDs are written only in these bullets (ops.timeline_add); elsewhere "Q3 2026" is prose.
        if thread is None or not raw.startswith(_ID_BULLETS):
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
    out += _ml_fields(rel, text, fm)
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


def _ml_fields(rel: str, text: str, fm: dict[str, Any]) -> list[Finding]:
    """The optional ML fields (spec §6.4), each present one checked: a bad one is one finding naming the field."""
    out: list[Finding] = []
    if "timeout_hours" in fm and timeout_hours_of(fm["timeout_hours"]) is None:
        out.append(
            _bad(
                rel,
                f"timeout_hours is {fm['timeout_hours']!r}; it must be a number above 0",
                _line_of(text, "timeout_hours:"),
            )
        )
    if "disk_budget_bytes" in fm and disk_budget_of(fm["disk_budget_bytes"]) is None:
        out.append(
            _bad(
                rel,
                f"disk_budget_bytes is {fm['disk_budget_bytes']!r}; it must be a whole number above 0",
                _line_of(text, "disk_budget_bytes:"),
            )
        )
    if "models" not in fm:
        return out
    line = _line_of(text, "models:")
    models = fm["models"]
    if not isinstance(models, list):
        return [*out, _bad(rel, f"models is {models!r}; it must be a list of repo and revision pairs", line)]
    for entry in models:
        entry = entry if isinstance(entry, dict) else {}
        if not model_repo_ok(entry.get("repo")):
            out.append(_bad(rel, f"models repo is {entry.get('repo')!r}; it must be owner/name", line))
        if not model_revision_ok(entry.get("revision")):
            out.append(
                _bad(
                    rel,
                    f"models revision is {entry.get('revision')!r}; it must be a quoted 40-character commit hash",
                    line,
                )
            )
    return out


def _as_naive(value: object, what: str) -> _dt.datetime:
    from scout.lab.experiments import as_datetime

    return as_datetime(value, _dt.UTC, what)


# --- lab.md ----------------------------------------------------------------------------


def _names_thread(target: str, slug: str) -> bool:
    """Whether a wikilink target points into thread ``slug``: its front page, timeline, an experiment note, or any
    path through its folder."""
    parts = target.strip().removesuffix(".md").split("/")
    last = parts[-1]
    return (
        slug in parts[:-1]
        or last in (slug, f"{slug}-timeline")
        or re.match(rf"{re.escape(slug)}-{EID_RE}-", last) is not None
    )


def index_findings(source: Source, text: str | None) -> list[Finding]:
    """``lab.md`` against the thread folders ``source`` holds; ``text`` None means lab.md is absent."""
    rel = INDEX_REL
    pattern = re.compile(rf"^{re.escape(_PREFIX)}([^/]+)/\1\.md$")
    folders = sorted(m.group(1) for f in source.files() if (m := pattern.match(f)))
    out: list[Finding] = []
    if text is None:
        return [_bad(rel, "lab.md is missing; it must list every thread folder")] if folders else []
    linked = set(_LINK.findall(text))
    private = 0
    for slug in folders:
        thread = _read_front(source, slug)
        if thread is not None and thread.visibility == "private":
            private += 1
            if any(_names_thread(target, slug) for target in linked):
                out.append(_bad(rel, f"lab.md links private thread '{slug}'; a private thread is never named there"))
        elif slug not in linked:
            out.append(_bad(rel, f"thread folder '{slug}' is not listed in lab.md"))
    counts = [int(n) for n in _PRIVATE_COUNT.findall(text)]
    shown = sum(counts)
    if shown != private:
        say = f"says {shown}" if counts else "has no such line"
        out.append(
            _bad(
                rel,
                f"there {plural.are(private)} {plural.count(private, 'private thread folder')} but the "
                f"'_<n> private threads not listed._' "
                f"line {say}",
            )
        )
    return out

"""*The item file is a card, not a log*, held at commit: ``wishlist check-log-move``.

The wishlist phase (``phases/modes/wishlist.md``, Step 3d) keeps a wishlist
card (``docs/wishlist/<item>.md``) to the request and a ``## Status`` section.
Dated entries live in the item's progress note,
``docs/wishlist/progress/<item file name>``, and a run that touches a card
whose body still holds a log moves it there verbatim, in the same commit.
Nothing is deleted. Prose alone can't hold "verbatim": a model tidying a log
rewords, merges, reorders or drops lines. This check holds it.

What it compares: each card as last committed (``HEAD``) with the card as it
is on disk, and the progress note as this commit will hold it (on disk when
the note is among the paths being committed, else as last committed). Every
non-blank *dated* line the card loses must be in that note, byte for byte, in
the card's order. Lines the note already held count: an earlier run may have
copied a log without removing it.

A line is dated when it is:

- a heading with an ISO date in it (``## 2026-01-05``, ``## Progress — 2026-01-05``),
  or any non-blank line under one, up to the next heading of the same or a
  higher level;
- a line whose text starts with a date, after any list marker, checkbox and
  emphasis (``- 2026-01-05: shipped``, ``**2026-01-05** — reviewed``);
- a ticked sub-task with a date in it (``- [x] Draft it — completed 2026-01-05``).

Frontmatter never counts, nor does a date inside a path or a wikilink target
(``progress/2026-01-05-x``), an open sub-task or request prose that mentions one.
A dated line that only moved within the card wasn't removed from it.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from scout import paths as scout_paths

WISHLIST_DIR = "docs/wishlist"
PROGRESS_DIR = "docs/wishlist/progress"

# A standalone ISO date: not part of a path or slug (``progress/2026-01-05-x``) or a longer number.
_DATE = r"(?<![\w/-])\d{4}-\d{2}-\d{2}(?![\d-])"
_DATE_RE = re.compile(_DATE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CHECKBOX_RE = re.compile(r"^\[[ xX]\]\s+")
_TICKED_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]\s+")
_LEADS_WITH_DATE_RE = re.compile(r"^[\s*_\[(`]*" + _DATE)


class LogMoveCheckError(Exception):
    """The check could not run: not a git repository, git failed, or a file isn't UTF-8."""


@dataclass(frozen=True)
class MoveFinding:
    item: str  # the card's file name
    note: str  # the progress note's vault-relative path
    missing: str  # the first dated line the card lost that the note doesn't hold, in order
    removed: int  # dated lines the card lost


@dataclass(frozen=True)
class LogMoveCheck:
    checked: list[str]  # changed cards, vault-relative
    moved: int  # dated lines that left a card and are in its note
    findings: list[MoveFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings


def _frontmatter_end(lines: Sequence[str]) -> int:
    """Index of the first line after a leading ``---`` frontmatter block (0 when there is none)."""
    if not lines or lines[0].rstrip("\r") != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            return i + 1
    return 0


def _is_dated_entry(line: str) -> bool:
    if _TICKED_RE.match(line) and _DATE_RE.search(line):
        return True
    text = _LIST_MARKER_RE.sub("", line, count=1)
    text = _CHECKBOX_RE.sub("", text, count=1)
    return bool(_LEADS_WITH_DATE_RE.match(text))


def dated_lines(lines: Sequence[str]) -> set[int]:
    """Indexes of the dated log lines in a card's ``lines`` (see the module docstring)."""
    out: set[int] = set()
    section_level: int | None = None  # the level of the dated heading we're under, if any
    in_fence = False
    for i in range(_frontmatter_end(lines), len(lines)):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if section_level is not None:
                out.add(i)
            continue
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            if section_level is not None and level <= section_level:
                section_level = None
            if section_level is None and _DATE_RE.search(line):
                section_level = level
            if section_level is not None:
                out.add(i)
            continue
        if not line.strip():
            continue
        if section_level is not None or (not in_fence and _is_dated_entry(line)):
            out.add(i)
    return out


def removed_dated_lines(old: Sequence[str], new: Sequence[str]) -> list[str]:
    """The dated lines of ``old`` that ``new`` no longer has, in ``old``'s order."""
    dated = dated_lines(old)
    removed: list[str] = []
    inserted: Counter[str] = Counter()
    matcher = difflib.SequenceMatcher(None, list(old), list(new), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed += [old[i] for i in range(i1, i2) if i in dated]
        if tag in ("insert", "replace"):
            inserted.update(new[j1:j2])
    out: list[str] = []
    for line in removed:
        if inserted[line] > 0:  # moved within the card, not removed from it
            inserted[line] -= 1
            continue
        out.append(line)
    return out


def first_missing(removed: Sequence[str], note: Sequence[str]) -> str | None:
    """The first of ``removed`` that ``note`` doesn't hold after the ones before it, or None."""
    j = 0
    for line in removed:
        while j < len(note) and note[j] != line:
            j += 1
        if j == len(note):
            return line
        j += 1
    return None


def _lines(data: bytes, rel: str) -> list[str]:
    """Lines split on ``\\n`` only, so a ``\\r`` or trailing space is part of the line (byte for byte)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise LogMoveCheckError(f"{rel} is not UTF-8") from e
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _git(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=repo,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise LogMoveCheckError(f"git {args[0]} failed: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise LogMoveCheckError(f"git {args[0]} failed: {err or f'exit {proc.returncode}'}")
    return proc.stdout


def _head_files(repo: Path) -> set[str] | None:
    """Every path under ``docs/wishlist/`` at ``HEAD``; None when the repository has no commit yet."""
    try:
        _git(repo, "rev-parse", "--verify", "-q", "HEAD")
    except LogMoveCheckError:
        return None
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", WISHLIST_DIR + "/")
    return {p.decode("utf-8", errors="surrogateescape") for p in out.split(b"\0") if p}


def _is_card(rel: str) -> bool:
    p = PurePosixPath(rel)
    return str(p.parent) == WISHLIST_DIR and p.suffix == ".md"


def _rel(repo: Path, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as e:
            raise LogMoveCheckError(f"{path} is not inside the vault {repo}") from e
    return PurePosixPath(path).as_posix()


def check_log_move(*, data_dir: Path | None = None, paths: Iterable[str] | None = None) -> LogMoveCheck:
    """Judge the cards this commit changes. ``paths`` (vault-relative or absolute) is what the commit will hold.

    With no ``paths``, every changed card is checked and every progress note is
    taken as on disk, as if the commit will hold the whole working tree.
    Raises :class:`LogMoveCheckError` when it can't check.
    """
    repo = scout_paths.data_dir(data_dir)
    if not repo.is_dir():
        raise LogMoveCheckError(f"no vault at {repo}")
    try:
        inside = _git(repo, "rev-parse", "--is-inside-work-tree").strip()
    except LogMoveCheckError as e:
        raise LogMoveCheckError(f"{repo} is not a git repository ({e})") from e
    if inside != b"true":
        raise LogMoveCheckError(f"{repo} is not a git working tree")

    head = _head_files(repo)
    committing = None if paths is None else {_rel(repo, p) for p in paths}

    def at_head(rel: str) -> list[str]:
        if head is None or rel not in head:
            return []
        return _lines(_git(repo, "show", f"HEAD:./{rel}"), rel)

    def on_disk(rel: str) -> list[str]:
        p = repo / rel
        if not p.is_file():
            return []
        try:
            return _lines(p.read_bytes(), rel)
        except OSError as e:
            raise LogMoveCheckError(f"cannot read {rel}: {e}") from e

    if committing is not None:
        cards = sorted(rel for rel in committing if _is_card(rel))
    else:
        wishlist = repo / WISHLIST_DIR
        on_disk_cards = {f"{WISHLIST_DIR}/{p.name}" for p in wishlist.glob("*.md") if p.is_file()}
        cards = sorted(on_disk_cards | {rel for rel in (head or ()) if _is_card(rel)})

    checked: list[str] = []
    findings: list[MoveFinding] = []
    moved = 0
    for card in cards:
        old, new = at_head(card), on_disk(card)
        if old == new:
            continue
        checked.append(card)
        removed = [line for line in removed_dated_lines(old, new) if line.strip()]
        if not removed:
            continue
        name = PurePosixPath(card).name
        note_rel = f"{PROGRESS_DIR}/{name}"
        note = on_disk(note_rel) if committing is None or note_rel in committing else at_head(note_rel)
        missing = first_missing(removed, note)
        if missing is None:
            moved += len(removed)
        else:
            findings.append(MoveFinding(item=name, note=note_rel, missing=missing, removed=len(removed)))
    return LogMoveCheck(checked=checked, moved=moved, findings=findings)

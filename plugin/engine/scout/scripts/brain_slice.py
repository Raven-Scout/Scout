"""Mode markers in SKILL.md, and the per-run slice cut from them (#84, spec
docs/superpowers/specs/2026-10-13-static-instruction-trim-design.md §4).

SKILL.md serves briefing and consolidation runs alike. Assembly wraps each
section that only one of them runs in a marker pair, generated from the
section's ``mode:`` frontmatter::

    <!-- scout:only briefing -->
    ## Slack Query — Briefing Data Gathering
    …

    <!-- /scout:only -->

Before a scheduled run, ``scoutctl brain slice`` cuts the live SKILL.md down to
the unmarked text plus the blocks of the run's mode class, so the session reads
(and re-reads from cache on every turn) only what it runs. SKILL.md stays the
file a vault edits; the slice is a read-only view under ``.scout-cache/``.

The closing marker sits after the blank line that ends its section, at the
start of the next one. A vault that appended a rule to a marked section then
3-way merges without a conflict on upgrade, and the rule stays inside its block.

Anything unexpected (an unbalanced, nested or unknown marker) makes slicing
refuse, and the caller falls back to the whole file: a run never loses rules to
a damaged marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The classes a SKILL.md block can be limited to: the modes SKILL.md serves.
SLICE_CLASSES: frozenset[str] = frozenset({"briefing", "consolidation"})

CLOSE_MARKER = "<!-- /scout:only -->"
_OPEN_RE = re.compile(r"^<!-- scout:only ([a-z]+(?: [a-z]+)*) -->$")
# Any line that looks like it means to be a marker. One that isn't exactly a
# marker is damage (a hand edit, a merge), never prose to keep.
_MARKERISH_RE = re.compile(r"^\s*<!--\s*/?\s*scout:only\b")


def open_marker(classes: list[str] | tuple[str, ...] | frozenset[str]) -> str:
    return f"<!-- scout:only {' '.join(sorted(classes))} -->"


def only_classes(section_modes: list[str], target_modes: set[str]) -> tuple[str, ...] | None:
    """The marker classes for a section in a target serving ``target_modes``.

    ``None`` (no marker) when the section has no ``mode:`` or runs in every mode
    the target serves; otherwise the target modes it does run in.
    """
    if not section_modes:
        return None
    runs_in = set(section_modes) & target_modes
    if not runs_in or runs_in == target_modes:
        return None
    return tuple(sorted(runs_in))


def join_marked(entries: list[tuple[str, tuple[str, ...] | None]]) -> list[str]:
    """Section bodies, each with its markers, ready for ``"\\n\\n".join``.

    A marked body gets its open marker as its first line; the close marker goes
    in front of the next body (or as a last element), so it follows the blank
    line between the two sections.
    """
    out: list[str] = []
    is_open = False
    for body, classes in entries:
        prefix = [CLOSE_MARKER] if is_open else []
        if classes:
            prefix.append(open_marker(classes))
        is_open = bool(classes)
        out.append("\n".join([*prefix, body]))
    if is_open:
        out.append(CLOSE_MARKER)
    return out


class MarkerError(ValueError):
    """The markers can't be trusted: slice nothing, read the whole file."""


@dataclass(frozen=True)
class Slice:
    text: str
    marked_blocks: int  # how many marked blocks the file has (0: nothing to cut)
    dropped_blocks: int


def slice_text(text: str, mode_class: str) -> Slice:
    """``text`` with the blocks ``mode_class`` doesn't run removed and every marker line dropped.

    Raises ``MarkerError`` on an unbalanced, nested, malformed or unknown marker.
    """
    if mode_class not in SLICE_CLASSES:
        raise MarkerError(f"unknown mode class {mode_class!r}")
    kept: list[str] = []
    open_at: int | None = None  # line number of the open marker we're inside
    keep_block = True
    blocks = dropped = 0
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        bare = line.rstrip("\r\n").rstrip()
        if not _MARKERISH_RE.match(bare):
            if keep_block:
                kept.append(line)
            continue
        if bare == CLOSE_MARKER:
            if open_at is None:
                raise MarkerError(f"line {n}: close marker without an open one")
            open_at, keep_block = None, True
            continue
        m = _OPEN_RE.match(bare)
        if not m:
            raise MarkerError(f"line {n}: malformed marker {bare!r}")
        if open_at is not None:
            raise MarkerError(f"line {n}: marker nested in the block opened on line {open_at}")
        classes = set(m.group(1).split())
        if unknown := classes - SLICE_CLASSES:
            raise MarkerError(f"line {n}: unknown mode class(es) {', '.join(sorted(unknown))}")
        open_at, blocks = n, blocks + 1
        keep_block = mode_class in classes
        dropped += not keep_block
    if open_at is not None:
        raise MarkerError(f"line {open_at}: block never closed")
    return Slice("".join(kept), blocks, dropped)


def strip_markers(text: str) -> str:
    """``text`` without its marker lines (everything else, every mode's blocks, kept)."""
    return "".join(
        line for line in text.splitlines(keepends=True) if not _MARKERISH_RE.match(line.rstrip("\r\n").rstrip())
    )


SLICE_DIR = Path(".scout-cache") / "brain"
_SLICE_NOTE = (
    "> **Read-only view.** This is {kind}.md cut down to what {cls} runs execute, rebuilt before every run. "
    "Make any brain-file edit in `{live}`, never here.\n\n"
)


def _tokens(text: str) -> str:
    return f"{len(text) / 4 / 1000:.1f}k"


def write_slice(vault: Path, kind: str, slot: str | None) -> tuple[Path, str]:
    """Cut ``<vault>/<kind>.md`` for ``slot``'s run into ``.scout-cache/brain/<kind>.md``.

    Returns the file the run should read and one line for the run log. That is
    the slice, or the live file itself when the slot has no mode class or the
    file has no markers; on any problem it is the live file too, and the line
    says why. Never raises for a bad file: a run always gets a brain to read.
    A stale slice from an earlier run is removed whenever none is written.
    """
    live = vault / f"{kind}.md"
    out = vault / SLICE_DIR / f"{kind}.md"
    cls = slot_class(slot)
    try:
        text = live.read_text(encoding="utf-8") if cls else ""
        cut = slice_text(text, cls) if cls else None
        if cls is None or cut is None:
            reason = f"slot {slot or '(none)'} reads the whole file"
        elif cut.dropped_blocks == 0:
            reason = f"no {cls}-excluded blocks (marked blocks: {cut.marked_blocks})"
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".md.tmp")
            tmp.write_text(_SLICE_NOTE.format(kind=kind, cls=cls, live=live) + cut.text, encoding="utf-8")
            tmp.replace(out)
            return out, f"brain slice: {kind} {_tokens(text)} → {_tokens(cut.text)} tok ({cls}, {slot})"
    except (OSError, UnicodeDecodeError, MarkerError) as e:
        reason = f"{type(e).__name__}: {e}"
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass  # the run is pointed at the live file below either way
    return live, f"brain slice: none, reading {kind}.md whole ({reason})"


def slot_class(slot: str | None) -> str | None:
    """The mode class a slot's run reads SKILL.md as, or ``None`` for the whole file.

    Keyed on the slot name, like the Run Modes table: ``*-briefing`` and
    ``*-consolidation``. ``manual``, an unset slot or any other name gets the
    whole file (a manual run picks its mode inside the session).
    """
    if not slot:
        return None
    if slot.endswith("-briefing"):
        return "briefing"
    if slot.endswith("-consolidation"):
        return "consolidation"
    return None

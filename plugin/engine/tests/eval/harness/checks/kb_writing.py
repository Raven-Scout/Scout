"""Checks on how a run wrote one project's KB pages (the KB-writing release gate;
templates/docs/kb-page-standard.md, phases/core/write-protocol.md).

- ``kb_structure``: ``scoutctl kb structure`` still shows the project folder ✓.
- ``front_page_growth``: the front page grew by at most the scenario's byte limit.
- ``kb_facts_cited``: each expected fact landed in a new or changed block of a note in the
  project's ``knowledge/`` folder, and that block cites a source.
- ``front_page_no_narration``: the front page's body has no narration marker, and no section of it
  gained a prose paragraph or a heading. Rewriting a bullet or a table row in place is fine; that
  is what "current state replaces old state" asks for.

A block is a paragraph, one list item (with its indented continuation lines), one table row or
one heading. Pure: tests/eval/harness/kb_gate.py reads the pages and the ``kb structure`` row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from scout.kb.structure import Marker, narration_found, split_page

from . import CheckResult

KB_CHECKS = ("kb_structure", "front_page_growth", "kb_facts_cited", "front_page_no_narration")

# A permalink, or a source note by path or by its dated name (write-protocol.md: "Every paragraph
# cites a source note or a permalink").
CITATION = re.compile(r"https?://\S+|\[\[(?:[^\]|]*/)?sources/[^\]]+\]\]|\[\[\d{4}-\d{2}-\d{2}-[^\]]+\]\]")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class KbFact:
    """A fact the run must file: a block matches when every pattern matches it."""

    id: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, block: str) -> bool:
        return all(p.search(block) for p in self.patterns)


def _kind(line: str) -> str:
    if _HEADING.match(line):
        return "heading"
    if line.startswith("|"):
        return "row"
    if _ITEM.match(line):
        return "item"
    if line.startswith("<!--"):
        return "comment"
    return "prose"


def blocks(text: str) -> list[tuple[str, str]]:
    """``(kind, text)`` for each block of a page's body (frontmatter dropped), in order.

    Kinds: ``heading``, ``row`` (one table row; the ``|---|`` rule is dropped), ``item`` (one list
    item and its indented continuation), ``comment`` and ``prose`` (a paragraph, a blockquote or a
    bold status line)."""
    _fm, body = split_page(text)
    out: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if not line.strip():
            out.append(("", []))  # a blank line closes whatever block is open
            continue
        if _TABLE_RULE.match(line.strip()):
            continue
        kind = _kind(line)
        last = out[-1] if out else ("", [])
        if line[:1].isspace() and last[0] in ("item", "prose"):
            last[1].append(line)  # an indented continuation of the open item or paragraph
        elif kind == "prose" and last[0] == "prose":
            last[1].append(line)
        else:
            out.append((kind, [line]))
    return [(kind, "\n".join(lines)) for kind, lines in out if kind]


def _norm(block: str) -> str:
    return " ".join(block.split())


def kb_structure(row: Mapping[str, Any] | None, project: str) -> CheckResult:
    if row is None:
        return CheckResult("kb_structure", False, (f"{project}: not in `kb structure` (front page gone or moved?)",))
    if row.get("conforms"):
        return CheckResult("kb_structure", True, (f"✓ {project}",))
    return CheckResult("kb_structure", False, tuple(f"✗ {project}: {p}" for p in row.get("problems") or ["fails"]))


def front_page_growth(before: str | None, after: str | None, limit: int) -> CheckResult:
    if before is None:
        return CheckResult("front_page_growth", None, ("the fixture has no front page",))
    if after is None:
        return CheckResult("front_page_growth", False, ("the front page is gone",))
    old, new = len(before.encode("utf-8")), len(after.encode("utf-8"))
    detail = f"{old} → {new} bytes ({new - old:+d}; limit +{limit})"
    return CheckResult("front_page_growth", new - old <= limit, (detail,))


def kb_facts_cited(before: Mapping[str, str], after: Mapping[str, str], facts: Sequence[KbFact]) -> CheckResult:
    """``before``/``after``: each note in the project's ``knowledge/`` folder, path → text."""
    if not facts:
        return CheckResult("kb_facts_cited", None, ("the scenario names no expected facts",))
    old = {_norm(b) for text in before.values() for _k, b in blocks(text)}
    fresh = [(path, b) for path, text in sorted(after.items()) for _k, b in blocks(text) if _norm(b) not in old]
    problems, found = [], []
    for fact in facts:
        hits = [(path, b) for path, b in fresh if fact.matches(b)]
        cited = [path for path, b in hits if CITATION.search(b)]
        if cited:
            found.append(f"{fact.id}: {cited[0]}")
        elif hits:
            problems.append(f"{fact.id}: in {hits[0][0]} without a citation (a URL or a [[sources/…]] note)")
        else:
            problems.append(f"{fact.id}: in no new or changed block of a knowledge/ note")
    return CheckResult("kb_facts_cited", not problems, tuple(problems or found))


def _sections(text: str) -> tuple[dict[str, int], list[str]]:
    """Prose blocks per section (``""`` is the part above the first ``##``) and every heading."""
    prose: dict[str, int] = {}
    headings: list[str] = []
    section = ""
    for kind, block in blocks(text):
        if kind == "heading":
            headings.append(block.strip())
            if block.startswith("## "):
                section = block[3:].strip()
            continue
        prose.setdefault(section, 0)
        if kind == "prose":
            prose[section] += 1
    return prose, headings


def front_page_no_narration(before: str | None, after: str | None, markers: Sequence[Marker]) -> CheckResult:
    if before is None:
        return CheckResult("front_page_no_narration", None, ("the fixture has no front page",))
    if after is None:
        return CheckResult("front_page_no_narration", False, ("the front page is gone",))
    problems = [f"narration marker: {label}" for label in narration_found(split_page(after)[1], markers)]
    old_prose, old_heads = _sections(before)
    new_prose, new_heads = _sections(after)
    problems += [f"new heading: {h}" for h in new_heads if h not in old_heads]
    for section, n in new_prose.items():
        gained = n - old_prose.get(section, 0)
        if gained > 0:
            where = f"section {section!r}" if section else "the intro"
            problems.append(f"{where} gained {gained} paragraph{'s' if gained > 1 else ''}")
    return CheckResult("front_page_no_narration", not problems, tuple(problems))

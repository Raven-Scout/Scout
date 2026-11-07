"""Checks on how a run wrote one project's KB pages (the KB-writing release gate;
templates/docs/kb-page-standard.md, phases/core/write-protocol.md).

- ``kb_structure``: ``scoutctl kb structure`` still shows the project folder ✓.
- ``front_page_growth``: the front page grew by at most the scenario's byte limit.
- ``kb_facts_cited``: each expected fact landed in a new or changed block of a note in the
  project's ``knowledge/`` folder, and that block cites a source: a URL, or a wikilink to a note
  that exists under ``sources/`` or ``meetings/``. A status-only fact (one with
  ``status_decision``, such as a ticket closing) may instead land as a new or changed, cited entry
  in the front page's "Where it stands", where the standard puts current status, when a
  ``knowledge/`` block (before or after the run) records the decision behind it.
- ``front_page_no_narration``: the front page's body has no narration marker, no new entry reads
  as a run's update (the scenario's ``front_page_narration`` markers: a dated or "update" bullet,
  "this run"), and no section gained a prose paragraph or a heading. Rewriting a bullet or a table
  row in place is fine; that is what "current state replaces old state" asks for. The standard's
  own "None right now." is not a paragraph.
- ``front_page_bullets``: no entry the run added to a ``kb_structure.bullet_sections`` section is
  over ``max_bullet_chars`` (``kb lint``'s ``page-bullet`` rule, #597), and "Where it stands"
  holds at most the standard's 6 bullets unless the fixture already had more.

A block is a paragraph, one list item (with its continuation lines, indented or lazily wrapped
as CommonMark allows), one table row or one heading. Pure: tests/eval/harness/kb_gate.py reads
the pages, the ``kb structure`` row and the vault's source notes.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from scout.kb.structure import Marker, narration_found, section_entries, split_page

from . import CheckResult

KB_CHECKS = (
    "kb_structure", "front_page_growth", "kb_facts_cited", "front_page_no_narration", "front_page_bullets",
)  # fmt: skip
# Notes a wikilink may cite, by vault-relative folder (write-protocol.md's source notes;
# meeting-prep.md's meetings/<slug>/YYYY-MM-DD.md, in both places a vault keeps them).
SOURCE_ROOTS = ("knowledge-base/sources/", "knowledge-base/meetings/", "meetings/")
STANDS = "Where it stands"  # kb-page-standard.md: the front page's current-status section
MAX_STANDS_BULLETS = 6  # kb-page-standard.md: "Where it stands", up to 6 bullets
_URL = re.compile(r"https?://\S+")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
# The page standard's placeholder for an emptied section, as a line, in italics or bold.
_PLACEHOLDER = re.compile(r"^[_*\s]*None right now\.?[_*\s]*$", re.IGNORECASE)


@dataclass(frozen=True)
class KbFact:
    """A fact the run must file: a block matches when every pattern matches it and no negation does.
    ``status_decision``: for a status-only fact, patterns that all match the knowledge-note block
    recording the decision behind the status (see :func:`kb_facts_cited`)."""

    id: str
    patterns: tuple[re.Pattern[str], ...]
    negations: tuple[re.Pattern[str], ...] = ()
    status_decision: tuple[re.Pattern[str], ...] = ()

    def matches(self, block: str) -> bool:
        return all(p.search(block) for p in self.patterns) and not any(n.search(block) for n in self.negations)


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
    item and its continuation lines), ``comment`` and ``prose`` (a paragraph, a blockquote or a
    bold status line). A plain line straight after an item, with no blank line between, continues
    that item, as CommonMark's lazy continuation reads it."""
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
        elif kind == "prose" and last[0] in ("prose", "item"):
            last[1].append(line)  # a paragraph's next line, or a lazily wrapped item
        else:
            out.append((kind, [line]))
    return [(kind, "\n".join(lines)) for kind, lines in out if kind]


def _norm(block: str) -> str:
    return " ".join(block.split())


# Sections whose entries are dated by design (kb-page-standard.md's tables), so a date there isn't a diary.
DATED_SECTIONS = frozenset({"What's next", "Key decisions"})


def _new_entries(before: str, after: str) -> list[tuple[str, str]]:
    """``(section, text)`` for each bullet or paragraph in ``after`` that ``before`` lacks."""
    old = {_norm(b) for _k, b in blocks(before)}
    out, section = [], ""
    for kind, block in blocks(after):
        if kind == "heading" and block.startswith("## "):
            section = block[3:].strip()
        elif kind in ("item", "prose") and _norm(block) not in old:
            out.append((section, block))
    return out


def source_keys(paths: Collection[str]) -> frozenset[str]:
    """What a wikilink to each existing source note may say: its vault path, its path under
    ``knowledge-base/``, or its bare name, each without ``.md``."""
    keys: set[str] = set()
    for path in paths:
        if not path.endswith(".md") or not path.startswith(SOURCE_ROOTS):
            continue
        stem = path[: -len(".md")]
        keys |= {stem, stem.removeprefix("knowledge-base/"), stem.rsplit("/", 1)[-1]}
    return frozenset(keys)


def cites(block: str, sources: Collection[str]) -> bool:
    """A URL, or a wikilink that resolves to an existing note (``sources``: from :func:`source_keys`)."""
    if _URL.search(block):
        return True
    return any(m.group(1).strip().removesuffix(".md") in sources for m in _WIKILINK.finditer(block))


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


_CITATION = "(a URL, or a link to an existing note under sources/ or meetings/)"


def kb_facts_cited(
    before: Mapping[str, str],
    after: Mapping[str, str],
    facts: Sequence[KbFact],
    sources: Collection[str] = (),
    *,
    front_before: str | None = None,
    front_after: str | None = None,
) -> CheckResult:
    """``before``/``after``: each note in the project's ``knowledge/`` folder, path → text.
    ``sources``: :func:`source_keys` of the notes that exist after the run.
    ``front_before``/``front_after``: the front page, for a fact with ``status_decision`` that no
    cited knowledge-note block holds. It passes when a new or changed, cited entry in "Where it
    stands" matches it and a knowledge-note block, before or after the run, matches every
    ``status_decision`` pattern: the status is where the standard puts it, the decision is filed."""
    if not facts:
        return CheckResult("kb_facts_cited", None, ("the scenario names no expected facts",))
    old = {_norm(b) for text in before.values() for _k, b in blocks(text)}
    fresh = [(path, b) for path, text in sorted(after.items()) for _k, b in blocks(text) if _norm(b) not in old]
    every = [(path, b) for notes in (after, before) for path, text in sorted(notes.items()) for _k, b in blocks(text)]
    stands = (
        [b for section, b in _new_entries(front_before or "", front_after) if section == STANDS]
        if front_after is not None
        else []
    )
    problems, found = [], []
    for fact in facts:
        hits = [(path, b) for path, b in fresh if fact.matches(b)]
        cited = [path for path, b in hits if cites(b, sources)]
        on_front = [b for b in stands if fact.matches(b)] if fact.status_decision and not cited else []
        if cited:
            found.append(f"{fact.id}: {cited[0]}")
        elif on_front:
            decision = next((path for path, b in every if all(p.search(b) for p in fact.status_decision)), None)
            if not any(cites(b, sources) for b in on_front):
                problems.append(f"{fact.id}: on the front page's '{STANDS}' without a citation {_CITATION}")
            elif decision is None:
                problems.append(
                    f"{fact.id}: on the front page's '{STANDS}', but no knowledge/ note records the decision behind it"
                )
            else:
                found.append(f"{fact.id}: front page (status; decision in {decision})")
        elif hits:
            problems.append(f"{fact.id}: in {hits[0][0]} without a citation {_CITATION}")
        else:
            problems.append(f"{fact.id}: in no new or changed block of a knowledge/ note")
    return CheckResult("kb_facts_cited", not problems, tuple(problems or found))


def _sections(text: str) -> tuple[dict[str, int], list[str]]:
    """Prose blocks per section (``""`` is the part above the first ``##``) and every heading.
    The standard's "None right now." placeholder is not counted."""
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
        if kind == "prose" and not _PLACEHOLDER.match(_norm(block)):
            prose[section] += 1
    return prose, headings


def front_page_no_narration(
    before: str | None, after: str | None, markers: Sequence[Marker], entry_markers: Sequence[Marker] = ()
) -> CheckResult:
    """``markers``: the vault's ``kb_structure.narration_markers``, anywhere in the body.
    ``entry_markers``: the scenario's, on each bullet or paragraph the run added outside
    :data:`DATED_SECTIONS`."""
    if before is None:
        return CheckResult("front_page_no_narration", None, ("the fixture has no front page",))
    if after is None:
        return CheckResult("front_page_no_narration", False, ("the front page is gone",))
    problems = [f"narration marker: {label}" for label in narration_found(split_page(after)[1], markers)]
    for section, block in _new_entries(before, after):
        labels = [] if section in DATED_SECTIONS else narration_found(_norm(block), entry_markers)
        if labels:
            problems.append(f"new entry reads as a run update ({', '.join(labels)}): {_norm(block)[:80]}")
    old_prose, old_heads = _sections(before)
    new_prose, new_heads = _sections(after)
    problems += [f"new heading: {h}" for h in new_heads if h not in old_heads]
    for section, n in new_prose.items():
        gained = n - old_prose.get(section, 0)
        if gained > 0:
            where = f"section {section!r}" if section else "the intro"
            problems.append(f"{where} gained {gained} paragraph{'s' if gained > 1 else ''}")
    return CheckResult("front_page_no_narration", not problems, tuple(problems))


def _stands_bullets(text: str) -> int:
    return sum(1 for _f, _l, t in section_entries(text, [STANDS]) if _ITEM.match(t))


def front_page_bullets(before: str | None, after: str | None, limit: int, sections: Sequence[str]) -> CheckResult:
    """``limit`` and ``sections``: the vault's ``kb_structure.max_bullet_chars`` and ``bullet_sections``."""
    if before is None:
        return CheckResult("front_page_bullets", None, ("the fixture has no front page",))
    if after is None:
        return CheckResult("front_page_bullets", False, ("the front page is gone",))
    old = {t for _f, _l, t in section_entries(before, sections)}
    problems = [
        f"line {first}: a new {len(t)}-char entry (limit {limit}): {t[:60]}…"
        for first, _l, t in section_entries(after, sections)
        if len(t) > limit and t not in old
    ]
    count, was = _stands_bullets(after), _stands_bullets(before)
    if count > max(MAX_STANDS_BULLETS, was):
        problems.append(f"'Where it stands' has {count} bullets (the standard's cap is {MAX_STANDS_BULLETS})")
    return CheckResult("front_page_bullets", not problems, tuple(problems))

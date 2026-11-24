"""Plain-words glosses for the identifiers a change's why and targets carry (spec: the Changes tab never shows an
identifier alone).

A gloss is worked out at report time, from the vault as it is now, never stored: ``pattern-<N>`` is that pattern's
title in the mistake audit, ``wishlist:<slug>`` the wishlist item's ``title``, and the other why types have fixed
words. Anything else has no gloss (None), and the app says so in its own words. Titles are cut to
:data:`TITLE_MAX` characters. Readers never raise; a file that can't be read, or is a symlink, gives no titles."""

from __future__ import annotations

import re
from pathlib import Path

from scout.changes.models import SUMMARY_MAX, Why
from scout.yaml_safe import load_yaml

OWNER_ASK = "You asked for this"
VERDICT = "A test found the earlier change hurt"
RESEARCH = "From research"
ENGAGEMENT = "How much you use Scout"
TITLE_MAX = SUMMARY_MAX
AUDIT_FILE = "knowledge-base/scout-mistake-audit.md"
AUDIT_DIR = "knowledge-base/scout-mistake-audit"
WISHLIST_DIR = "docs/wishlist"
_MAX_BYTES = 4 * 1024 * 1024
_FIXED = {"owner-ask": OWNER_ASK, "verdict": VERDICT, "research": RESEARCH}
_PATTERN_REF_RE = re.compile(r"^pattern[-:]#?(\d+)$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SEP = r"[ \t]*(?:\*\*|__)?[ \t]*[:—–.-][ \t]*"
_HEADING_RE = re.compile(rf"^#{{1,6}}[ \t]+(?:\*\*|__)?Pattern[ \t]+#(\d+)\b{_SEP}(.+?)[ \t#]*$", re.M | re.I)
_ROW_RE = re.compile(rf"^\|[ \t]*(?:\*\*|__)?(?:Pattern[ \t]+)?#(\d+)\b(?:{_SEP})?([^|]*)\|", re.M | re.I)
_FILE_RE = re.compile(r"^pattern-(\d+)(?:-.*)?$")
_FIRST_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
_TITLE_PREFIX_RE = re.compile(rf"^(?:\*\*|__)?Pattern[ \t]+#\d+\b{_SEP}", re.I)
_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _read(path: Path) -> str | None:
    """A regular file's text, or None (missing, a symlink, too big, not UTF-8, unreadable)."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _clean(raw: str) -> str | None:
    text = _WIKILINK_RE.sub(r"\1", raw)
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"\*\*|__|`", "", text)
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"


def _frontmatter(text: str) -> dict | None:
    parts = text.split("---\n", 2) if text.startswith("---\n") else []
    if len(parts) < 3:
        return None
    try:
        meta = load_yaml(parts[1])
    except Exception:  # noqa: BLE001 - malformed YAML: no title
        return None
    return meta if isinstance(meta, dict) else None


def _title_of_file(text: str) -> str | None:
    """A per-item file's title: its frontmatter ``title``, else its first heading without a ``Pattern #N:`` lead."""
    meta = _frontmatter(text)
    title = meta.get("title") if meta else None
    if isinstance(title, str) and _clean(title):
        return _clean(title)
    m = _FIRST_HEADING_RE.search(text)
    return _clean(_TITLE_PREFIX_RE.sub("", m.group(1))) if m else None


class Glossary:
    """Glosses for one report: the mistake audit is read once, on first use; wishlist items as they are asked for."""

    def __init__(self, vault: Path) -> None:
        self._vault = vault
        self._patterns: dict[str, str] | None = None

    def _load_patterns(self) -> dict[str, str]:
        found: dict[str, str] = {}
        text = _read(self._vault / AUDIT_FILE) or ""
        # Headings first: a summary-table row only fills in a pattern no heading named; list lines are evidence.
        for regex in (_HEADING_RE, _ROW_RE):
            for hit in regex.finditer(text):
                title = _clean(hit.group(2))
                if title:
                    found.setdefault(hit.group(1), title)
        for path in sorted((self._vault / AUDIT_DIR).glob("*.md")):  # a pattern's own file names it best
            named = _FILE_RE.match(path.stem)
            body = _read(path) if named else None
            title = _title_of_file(body) if body else None
            if named and title:
                found[named.group(1)] = title
        return found

    def pattern(self, number: str) -> str | None:
        if self._patterns is None:
            self._patterns = self._load_patterns()
        return self._patterns.get(number)

    def wishlist(self, slug: str) -> str | None:
        if not _SLUG_RE.match(slug) or ".." in slug:
            return None
        text = _read(self._vault / WISHLIST_DIR / f"{slug.removesuffix('.md')}.md")
        meta = _frontmatter(text) if text else None
        title = meta.get("title") if meta else None
        return _clean(title) if isinstance(title, str) else None

    def of_why(self, why: Why) -> str | None:
        """The gloss for a change's why (its type says which kind of reference ``ref`` is)."""
        if why.type in _FIXED:
            return _FIXED[why.type]
        if why.type == "pattern":
            m = _PATTERN_REF_RE.match(why.ref)
            return self.pattern(m.group(1)) if m else None
        if why.type == "wishlist":
            for prefix in ("wishlist:", "wishlist-"):
                if why.ref.startswith(prefix):
                    found = self.wishlist(why.ref[len(prefix) :])
                    if found:
                        return found
            return self.wishlist(why.ref)  # the scanner infers a wishlist why with the bare slug as its ref
        return None

    def of_value(self, value: str) -> str | None:
        """The gloss for a ``Scout-Change-Why:``-style value or a target (``pattern-288``, ``engagement``)."""
        from scout.changes.scan import _why_from_trailer  # the scanner's own reading of a why value

        if value == "engagement":
            return ENGAGEMENT
        return self.of_why(_why_from_trailer(value))

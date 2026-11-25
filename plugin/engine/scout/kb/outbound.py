"""The private-content matchers: ``kb check-outbound`` / ``kb check-query`` (#453, spec §6.1, §8.1, §8.2).

Before a phase sends text to a shared surface (Slack, a connector write, a
connector or web query, a public repo, a Telegram group ...), it runs these
checks; the lint checks ``private-link`` / ``private-mention`` (Task 8) reuse
:func:`find_mentions`.

**Private terms** (:func:`private_terms`) are built from every file labelled
``private`` (owner view, so every private file):

- whole-word *terms*: the file's slug (its name without the extension), and
  for a markdown note its frontmatter ``name:``, ``aliases:`` and ``title:``
  (spec §4.2 counts the title as private content; ruling R17), each kept only
  when it has 4 or more characters. A slug, ``name:`` or ``title:`` that is one
  ordinary word (:data:`~scout.kb.ordinary_words.ORDINARY_WORDS`: ``plan``,
  ``notes``, ``billing``) is not a term, unless the note names a proper noun
  (:data:`PROPER_NOUN_TYPES` such as ``type: person`` or ``organization``, or in
  a ``people/`` folder). A note with such a slug matches by its path instead,
  from its last two segments up (``billing/billing``), as a note shared with
  this person does, or by its file name (``billing.md``) at the vault root
  (#576). Aliases are always terms.
- *wikilinks*: ``[[target]]``, ``[[target|display]]`` (``\\|`` inside a table
  too), ``[[target#heading]]``, ``![[target]]`` and ``[[path/to/target]]``
  whose target is the note's slug, file name, ``name:``, an alias, or its vault
  path (or a trailing part of it), with or without ``.md``. A link is a hit
  whatever the length of its target, so ``[[sam]]`` hits although ``sam`` is
  too short to be a term.

**How terms match.** Text and terms are compared NFKC-normalised under full
Unicode case folding (``STRASSE`` matches ``Straße``; a fullwidth or
combining-accent spelling matches the plain one). For terms of two words or
more, and one-word terms of 5+ characters (``UNACCENT_MIN_CHARS``), accents on
Latin, Greek and Cyrillic letters are also ignored on both sides, so ``Rene Lee``
and ``René Lee`` match each other however either is written; a short term such
as ``Mila`` stays exact, so the Czech greeting ``Milá`` passes. Japanese voicing
marks, Indic vowel signs and Thai tone marks are never ignored. A term's boundary is anything but a
letter or digit, so ``_Sammy_`` (Slack italics), ``sammy_notes``, ``Sammy.``
and ``(Sammy)`` all match and ``Sammyson`` doesn't. The parts of a term match
across any run of spaces, line breaks, ``-`` or ``_`` (``task renew permit``,
``task_renew_permit``, ``Mary-Jane`` for ``Mary Jane``), so matching runs over
the whole text, and a term split over a line break reports every line it
spans. Each line is also matched once more URL-decoded (``Jos%C3%A9+Lee``), and
the whole text once more with HTML comments and tags (across lines too) and a
link's ``](…)`` dropped, then HTML entities decoded and inline markup dropped
(``S&#97;mmy``, ``Sa**mm**y``, ``Sa<!--⏎-->mmy``, ``[Sa](x)mmy``, ``Sa![](x)mmy``;
:func:`_demarked`), and once more line by line. Only real tags count (``<`` then a
letter), and an unclosed ``<!--`` is literal text, as CommonMark renders it.

**What it can't see.** Terms come from a private note's path and frontmatter,
never its body: a sentence restating what the note says, initials, a role
("the new hire"), a first or last name alone (unless it is an alias), a
reordered name ("Rivers, Sammy"), parts joined by ``.`` or ``/``, or a
look-alike letter from another script (Cyrillic ``а``) doesn't match.

The result is a :class:`PrivateTerms`, grouped per private note, so a lint
marker can allow one note's hits and not another's. It is cached per process,
keyed by the vault, the policy and each private file's stat, so an edited or
added note is picked up by the next call.

**Markers.** With ``allow_markers=True`` (lint only), a same-line
``%%privacy:allow <slug>%%`` allows that line's hits that refer to that one
private note (a term spanning lines only when every line carries the marker).
Sends pass ``allow_markers=False``: a marker is not a send pass (spec §8.2),
and since the marker itself names the slug, such a line is a hit.

**Notes shared with this person** (Agnes Shared Areas §7): :func:`with_shared`
adds their paths, long titles, quotes and close rewordings, whatever the vault's
own sharing switch says. :mod:`scout.kb.shared_reads` says how and what it can't see.

A result is only ever line numbers: no function here returns the matched term,
the note or its path to a caller that would print it.
"""

from __future__ import annotations

import bisect
import html
import os
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import yaml

from scout.kb.ordinary_words import ORDINARY_WORDS
from scout.kb.privacy import (
    FULL_READERS,
    Policy,
    classifier_block,
    load_frontmatter_yaml,
    load_policy,
    read_head,
    surface_class,
)
from scout.kb.read import Output, UsageError, all_labels, policy_hash
from scout.kb.shared_reads import (
    LEDGER_DIR,
    ExcerptIndex,
    SharedReadsUnreadable,
    SharedRecord,
    load_ledger,
    source_note_records,
)

MIN_TERM_CHARS = 4
# Which terms also match with their accents ignored (``Rene`` for ``René`` and back). The more terms,
# the more an ordinary word in another language trips them: hidden ``Mila`` would block the Czech
# greeting "Milá". Default: terms of two words or more, and one-word terms of 5+ characters. Set
# UNACCENT_MIN_CHARS to MIN_TERM_CHARS to ignore accents on every term, or very high (with
# UNACCENT_MULTI_WORD False) to never ignore them; exact and NFKC matching stay as they are. A deliberate
# trade: a one-word term under 5 characters without accents (``Rene``) no longer matches text that has them
# (``René``, precomposed or NFD); only the exact spelling does.
UNACCENT_MIN_CHARS = 5
UNACCENT_MULTI_WORD = True
MIN_SHARED_NAME_WORDS = 3
# Entity types whose names are proper nouns (schema.yaml's, and free-form ones vaults use):
# a one-word name stays a term even when it's an ordinary word (#576).
PROPER_NOUN_TYPES = frozenset({"person", "organization", "pet", "org", "company", "client"})

_WIKILINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
# The one definition of the lint override marker (spec §8.2): ``%%privacy:allow <slug>%%``,
# case-insensitive, any spaces or tabs around the parts. Lint re-exports it.
ALLOW_MARKER_RE = r"%%[ \t]*privacy:allow[ \t]+([^%\s]+)[ \t]*%%"
ALLOW_MARKER = re.compile(ALLOW_MARKER_RE, re.IGNORECASE)
_MARKER = ALLOW_MARKER
# Markup that can sit inside a word without showing. _SPANNING may run over line breaks: an HTML
# comment start (its end is found with str.find: a lazy `.*?` was quadratic on unclosed ones), a
# real CommonMark tag (`<` then a letter, so `a < b` and `<!` never open one), and the `](target)`
# of a markdown link. _MARKUP is inline: emphasis, strike, highlight and code marks, and `![`.
_TAG = r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>"
_SPANNING = re.compile(r"<!--|" + _TAG + r"|\]\([^()\n]*\)")
_MARKUP = re.compile(r"\*\*|__|~~|==|`|\*|!\[")
_SEP = r"[-_\s]+"
_SPLIT = re.compile(_SEP)
# Not a letter or digit on either side: `_` and punctuation are boundaries.
_BEFORE = r"(?<![^\W_])"
_AFTER = r"(?![^\W_])"


@dataclass(frozen=True)
class PrivateNote:
    """One private file's matchable forms.

    ``terms`` are the whole-word terms (4+ characters, as written);
    ``link_keys`` the case-folded names a wikilink may use; ``link_path`` the
    case-folded vault path without ``.md``; ``pattern`` matches any term in
    case-folded text.
    """

    path: str
    slug: str
    terms: tuple[str, ...]
    link_keys: frozenset[str]
    link_path: str
    pattern: re.Pattern[str] | None
    bare_pattern: re.Pattern[str] | None = None  # the accent-free forms of its longer terms, over bare text
    # Every match of ``pattern`` (``bare_pattern``) starts with one of these, as written, so :func:`_hits` tries
    # the pattern only where one occurs (:func:`_matches`). None: no such promise, the whole text is searched. Set
    # only beside a pattern built from the same words (:func:`_prefixes`), never written by hand.
    prefixes: tuple[str, ...] | None = None
    bare_prefixes: tuple[str, ...] | None = None

    def linked_by(self, target: str) -> bool:
        """``target`` (already case-folded, heading, display and ``.md`` stripped) names this note."""
        if target in self.link_keys:
            return True
        if "/" in target:
            return self.link_path == target or self.link_path.endswith("/" + target)
        return False


# The citing form a run writes (`<owner>'s shared note <path>`, `source: shared:<owner-email>:<path>`):
# refused on a shared surface with nothing recorded, so a citation pasted from the daily file can't pass.
_CITATION = PrivateNote(
    path="shared-note-citation",
    slug="shared-note-citation",
    terms=(),
    link_keys=frozenset(),
    link_path="",
    pattern=re.compile(r"['’]s shared note(?![^\W_])|(?<![^\W_])shared:[^\s:@]+@[^\s:@]+:\S"),
)


@dataclass(frozen=True)
class PrivateTerms:
    """Every private note's terms and link keys (what :func:`find_mentions` consumes)."""

    notes: tuple[PrivateNote, ...]
    any_term: re.Pattern[str] | None  # a fast pre-check: any note's term
    excerpts: ExcerptIndex | None = None  # quotes of notes shared with this person (with_shared)
    any_bare: re.Pattern[str] | None = None  # the same pre-check for the notes' bare_pattern

    def __bool__(self) -> bool:
        return bool(self.notes)


def _norm(text: str) -> str:
    """NFKC case folding: ``Jose\u0301``, ``José`` and ``JOSÉ`` all become ``josé``."""
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())
    return folded.translate(_INVISIBLE)  # a soft hyphen or zero-width character inside a name hides nothing


# Characters that change no word a reader sees: the soft hyphen and the zero-width ones. ZWNJ and ZWJ do shape
# Persian and Indic words; folding them out only makes a match likelier there (fails towards leaving text out).
_INVISIBLE = dict.fromkeys(map(ord, "­᠎​‌‍⁠﻿"))


def _strips(base: str) -> bool:
    """Whether accents on ``base`` are ignored: Latin, Greek and Cyrillic only. A Japanese voicing
    mark (``ガ`` is ``カ`` plus U+3099), an Indic vowel sign or a Thai tone mark changes the word."""
    o = ord(base)
    return o < 0x0530 or any(lo <= o <= hi for lo, hi in _STRIP_RANGES)


# Latin, Greek and Cyrillic past U+0530: Cyrillic Extended-C, Latin and Greek Extended, Latin Extended-C,
# Cyrillic Extended-A and -B, Latin Extended-D and -E.
_STRIP_RANGES = ((0x1C80, 0x1C8F), (0x1E00, 0x1FFF), (0x2C60, 0x2C7F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F),
                 (0xA720, 0xA7FF), (0xAB30, 0xAB6F))  # fmt: skip


def _unaccent(text: str) -> str:
    """``text`` without the combining marks on Latin, Greek and Cyrillic letters: ``café`` becomes ``cafe``."""
    out: list[str] = []
    base = ""
    for c in unicodedata.normalize("NFD", text):
        if unicodedata.combining(c):
            if base and _strips(base):
                continue
        else:
            base = c
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out))


def _words_of(term: str) -> list[str]:
    return [w for w in _SPLIT.split(term) if w]


def _alternation(forms: Iterable[str]) -> re.Pattern[str] | None:
    parts = {_SEP.join(re.escape(w) for w in words) for f in forms if (words := _words_of(f))}
    if not parts:
        return None
    return re.compile(_BEFORE + "(?:" + "|".join(sorted(parts, key=len, reverse=True)) + ")" + _AFTER)


def _prefixes(forms: Iterable[str]) -> tuple[str, ...]:
    """The first word of each alternative :func:`_alternation` builds from ``forms``.

    An alternative is its words, each escaped, joined by separators; the pattern
    is the alternatives between two zero-width boundary assertions. So every
    match starts with an alternative's first word, as written.
    """
    return tuple(dict.fromkeys(words[0] for f in forms if (words := _words_of(f))))


def _term_forms(terms: Iterable[str]) -> list[str]:
    return [_norm(t) for t in terms]


def _term_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for ``terms`` over folded text (:func:`_fold`; module docstring)."""
    return _alternation(_term_forms(terms))


def _term_prefixes(terms: Iterable[str]) -> tuple[str, ...]:
    """:func:`_prefixes` of :func:`_term_regex`'s forms (``PrivateNote.prefixes``)."""
    return _prefixes(_term_forms(terms))


def _unaccents(term: str) -> bool:
    """Whether ``term`` also matches with its accents ignored (UNACCENT_MIN_CHARS, UNACCENT_MULTI_WORD)."""
    words = _words_of(_norm(term))
    return (UNACCENT_MULTI_WORD and len(words) > 1) or len(_norm(term)) >= UNACCENT_MIN_CHARS


def _bare_forms(terms: Iterable[str]) -> list[str]:
    return [_unaccent(_norm(t)) for t in terms if _unaccents(t)]


def _bare_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for the accent-free forms of ``terms`` that ignore accents, over bare text (:func:`_folds`)."""
    return _alternation(_bare_forms(terms))


def _bare_prefixes(terms: Iterable[str]) -> tuple[str, ...]:
    """:func:`_prefixes` of :func:`_bare_regex`'s forms (``PrivateNote.bare_prefixes``)."""
    return _prefixes(_bare_forms(terms))


def _strings(value: Any) -> list[str]:
    """A frontmatter value as strings: a scalar, or the scalars of a list."""
    if isinstance(value, (list, tuple)):
        return [s for v in value if not isinstance(v, (list, tuple)) for s in _strings(v)]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = " ".join(str(value).split())
        return [text] if text else []
    return []


def _frontmatter(text: str | None) -> dict[Any, Any]:
    """A note's (leading) text's frontmatter; {} when it has none or it doesn't parse."""
    if text is None:
        return {}
    block = classifier_block(text)
    if block is None:
        return {}
    try:
        data = load_frontmatter_yaml("\n".join(block))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _ordinary(name: str) -> bool:
    """``name`` is one ordinary word (#576): a note named so doesn't make it a term."""
    return _words(name) == 1 and name.strip().casefold() in ORDINARY_WORDS


def _names_a_proper_noun(rel: str, data: dict[Any, Any]) -> bool:
    kind = data.get("type")
    return (isinstance(kind, str) and kind.strip().casefold() in PROPER_NOUN_TYPES) or (
        "people" in rel.casefold().split("/")[:-1]
    )


def _strip_md(name: str) -> str:
    return name[:-3] if name.lower().endswith(".md") else name


def _note(vault: Path, rel: str) -> PrivateNote:
    return note_from_text(rel, read_head(vault / rel)[0])


def note_from_text(rel: str, text: str | None) -> PrivateNote:
    """The matchable forms of the private file ``rel`` whose (leading) text is ``text``.

    For a version of a note that isn't on disk: the one at ``HEAD`` or in the
    index (the lint checks the commit, spec §8.2). ``text`` is None for a
    non-markdown file (slug only).
    """
    name = rel.rsplit("/", 1)[-1]
    slug = _strip_md(name) if name.lower().endswith(".md") else Path(name).stem
    data = _frontmatter(text) if name.lower().endswith(".md") else {}
    proper = _names_a_proper_noun(rel, data)
    names = [slug]
    named = [slug] if proper or not _ordinary(slug) else []
    for key in ("name", "aliases", "title"):
        for n in _strings(data.get(key)):
            names.append(n)
            if key == "aliases" or proper or not _ordinary(n):
                named.append(n)
    if _ordinary(slug) and not proper:
        parts = (rel[: len(rel) - len(name)] + slug).split("/")
        named += ["/".join(parts[i:]) for i in range(len(parts) - 1)] if len(parts) > 1 else [name]
    terms = tuple(dict.fromkeys(n for n in named if len(n) >= MIN_TERM_CHARS))
    keys = {_norm(n) for n in names} | {_norm(name), _norm(_strip_md(name))}
    return PrivateNote(
        path=rel,
        slug=slug,
        terms=terms,
        link_keys=frozenset(keys),
        link_path=_norm(_strip_md(rel)),
        pattern=_term_regex(terms),
        bare_pattern=_bare_regex(terms),
        prefixes=_term_prefixes(terms),
        bare_prefixes=_bare_prefixes(terms),
    )


_CACHE: dict[str, tuple[Any, PrivateTerms]] = {}
_SHARED_CACHE: dict[str, tuple[Any, PrivateTerms]] = {}


def clear_cache() -> None:
    """Forget the per-process term lists (tests)."""
    _CACHE.clear()
    _SHARED_CACHE.clear()


def _stat(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def private_terms(vault: Path, policy: Policy, *, persist: bool = True) -> PrivateTerms:
    """The private notes' terms and link keys in ``vault`` under ``policy`` (see the module docstring).

    ``persist=False`` never writes the privacy index (:func:`scout.kb.read.all_labels`).
    """
    root = vault.resolve()
    labels = all_labels(root, policy, persist=persist)
    private = sorted(rel for rel, label in labels.items() if label.visibility == "private")
    signature = (policy_hash(policy), tuple((rel, _stat(root / rel)) for rel in private))
    cached = _CACHE.get(str(root))
    if cached is not None and cached[0] == signature:
        return cached[1]
    terms = terms_of(_note(root, rel) for rel in private)
    _CACHE[str(root)] = (signature, terms)
    return terms


def terms_of(notes: Iterable[PrivateNote]) -> PrivateTerms:
    """A :class:`PrivateTerms` over ``notes``, duplicates (same path and forms) dropped."""
    seen: dict[tuple[str, tuple[str, ...], frozenset[str]], PrivateNote] = {}
    for n in notes:
        seen.setdefault((n.path, n.terms, n.link_keys), n)
    kept = tuple(seen.values())
    return PrivateTerms(
        notes=kept,
        any_term=_term_regex(t for n in kept for t in n.terms),
        any_bare=_bare_regex(t for n in kept if n.bare_pattern is not None for t in n.terms),
    )


def _words(text: str) -> int:
    return len([w for w in _SPLIT.split(text) if w])


def _shared_note(key: str, path: str, title: str | None) -> PrivateNote:
    """The matchable forms of a note or folder someone shared with this person.

    A note: its path, or a trailing part of two segments or more. A folder: its full
    path only. Its title and slug only
    at three words or more: a note named ``plan`` or ``documents`` must not turn
    that word into a term (the reader's Slack says "plan" all day). ``pattern``
    is left out: :func:`with_shared` compiles every shared note's terms as one,
    since a regex per note was most of a cold hook's time on a long ledger.
    """
    rel = _strip_md(path)
    parts = rel.split("/")
    slug = parts[-1]
    if not path.lower().endswith(".md"):
        # A folder from `shared_with_me`: the reader's vault has the same layout, so `projects/widget`
        # is likely their own folder too. Only the full path names the owner's.
        return PrivateNote(path=key, slug=slug, terms=(rel,), link_keys=frozenset(), link_path="", pattern=None)
    names = [n for n in (title, slug) if n and _words(n) >= MIN_SHARED_NAME_WORDS]
    terms = tuple(dict.fromkeys(["/".join(parts[i:]) for i in range(len(parts) - 1)] + names))
    keys = {_norm(slug), _norm(path.rsplit("/", 1)[-1])} if slug in names else set()
    return PrivateNote(
        path=key,
        slug=slug,
        terms=terms,
        link_keys=frozenset(keys),
        link_path=_norm(rel),
        pattern=None,
    )


def with_shared(terms: PrivateTerms, vault: Path) -> PrivateTerms:
    """``terms`` plus what this person has read of other people's shared notes, and the citing form.

    Raises :class:`~scout.kb.shared_reads.SharedReadsUnreadable` when the ledger
    can't be read, so a caller fails closed. ``source:`` of the private notes
    in ``terms`` counts too, so a cleared cache keeps what was filed in the vault.
    Cached per process like :func:`private_terms`: the outbound gate checks a tool
    call's every field, and the ledger only grows.
    """
    root = vault.resolve()
    ledger = root / LEDGER_DIR
    try:
        entries = sorted(p.name for p in ledger.iterdir())
    except OSError:
        entries = []
    signature = (
        tuple((name, _stat(ledger / name)) for name in entries),
        tuple((n.path, _stat(root / n.path)) for n in terms.notes),
    )
    cached = _SHARED_CACHE.get(str(root))
    if cached is not None and cached[0] == signature:
        return cached[1]
    merged_terms = _with_shared(terms, root)
    _SHARED_CACHE[str(root)] = (signature, merged_terms)
    return merged_terms


def _with_shared(terms: PrivateTerms, vault: Path) -> PrivateTerms:
    records = [*load_ledger(vault), *source_note_records(vault, (n.path for n in terms.notes))]
    merged: dict[str, list[SharedRecord]] = {}
    for record in records:
        merged.setdefault(record.key, []).append(record)
    shared = [
        _shared_note(key, group[0].path, next((r.title for r in group if r.title), None))
        for key, group in merged.items()
    ]
    shared_terms = [t for n in shared for t in n.terms]
    paths = _term_regex(shared_terms)
    bare_paths = _bare_regex(shared_terms)
    together = PrivateNote(
        path="shared-note-paths",
        slug="shared-note-paths",
        terms=(),
        link_keys=frozenset(),
        link_path="",
        pattern=paths,
        bare_pattern=bare_paths,
        prefixes=_term_prefixes(shared_terms),
        bare_prefixes=_bare_prefixes(shared_terms),
    )
    notes = (*terms.notes, _CITATION, together, *shared)
    patterns = [p.pattern for p in (terms.any_term, _CITATION.pattern, paths) if p is not None]
    bare = [p.pattern for p in (terms.any_bare, bare_paths) if p is not None]
    return PrivateTerms(
        notes=notes,
        any_term=re.compile("|".join(patterns)),
        excerpts=ExcerptIndex(merged),
        any_bare=re.compile("|".join(bare)) if bare else None,
    )


def _link_target(inner: str) -> str:
    """A wikilink's target, case-folded: display text (``|`` or a table's ``\\|``),
    heading/block ref and ``.md`` dropped."""
    target = inner.split("|", 1)[0].rstrip("\\").split("#", 1)[0].strip().strip("/")
    return _norm(_strip_md(target))


_ASCII_RUN = re.compile(r"[\x00-\x7f]+")


def _joins(ch: str) -> bool:
    """Whether ``ch`` belongs with the character before it: a combining mark, or a Hangul vowel or final jamo."""
    return bool(unicodedata.combining(ch)) or "\u1160" <= ch <= "\u11ff"


def _folds(text: str) -> tuple[tuple[str, list[int] | None], tuple[str, list[int] | None]]:
    """``text`` NFKC case-folded (:func:`_norm`), then the same without accents
    (:func:`_unaccent`), each with, for every folded character, the index of the
    character it came from (None when ``text`` is ASCII, where folding keeps every
    position). Normalised one base character with its marks at a time, so a name
    spelled with combining accents (NFD) matches its precomposed term."""
    if text.isascii():
        lower = text.lower()
        return (lower, None), (lower, None)
    out: list[str] = []
    bare: list[str] = []
    origin: list[int] = []
    bare_origin: list[int] = []
    i, n = 0, len(text)
    while i < n:
        run = _ASCII_RUN.match(text, i)
        if run is not None:
            j = run.end()
            if j < n and _joins(text[j]):
                j -= 1  # the run's last character folds with the marks after it
            if j > i:
                part, where = text[i:j].lower(), range(i, j)
                out.append(part)
                bare.append(part)
                origin.extend(where)
                bare_origin.extend(where)
                i = j
                continue
        j = i + 1
        while j < n and _joins(text[j]):
            j += 1
        folded = _norm(text[i:j])
        stripped = folded if folded.isascii() else _unaccent(folded)
        out.append(folded)
        bare.append(stripped)
        origin.extend([i] * len(folded))
        bare_origin.extend([i] * len(stripped))
        i = j
    return ("".join(out), origin), ("".join(bare), bare_origin)


def _fold(text: str) -> tuple[str, list[int] | None]:
    """``text`` NFKC case-folded, with each folded character's origin (:func:`_folds`)."""
    return _folds(text)[0]


def _line(starts: list[int], origin: list[int] | None, pos: int) -> int:
    """The 0-based line of ``text`` that folded position ``pos`` came from (``starts``: each line's offset)."""
    return bisect.bisect_right(starts, origin[pos] if origin is not None else pos) - 1


def _hits(text: str, terms: PrivateTerms) -> list[tuple[str, int, int]]:
    """``(note path, first line, last line)`` (0-based lines of ``text``) of every term and link hit."""
    (folded, origin), bare = _folds(text)
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    found: list[tuple[str, int, int]] = []
    targets = [(_line(starts, origin, m.start()), _link_target(m.group(1))) for m in _WIKILINK.finditer(folded)]
    for n, target in targets:
        if target:
            found += [(note.path, n, n) for note in terms.notes if note.linked_by(target)]
    for form, where, pre, attr, prefix_attr in (
        (folded, origin, terms.any_term, "pattern", "prefixes"),
        (bare[0], bare[1], terms.any_bare, "bare_pattern", "bare_prefixes"),  # accents ignored, where allowed
    ):
        if pre is None or not pre.search(form):
            continue
        seen: dict[str, list[int]] = {}
        for note in terms.notes:
            pattern = getattr(note, attr)
            if pattern is not None:
                found += [
                    (note.path, _line(starts, where, m.start()), _line(starts, where, m.end() - 1))
                    for m in _matches(pattern, getattr(note, prefix_attr), form, seen)
                ]
    if terms.excerpts is not None:
        found += terms.excerpts.hits(text)
    return found


def _occurrences(text: str, literal: str) -> list[int]:
    """Every index ``literal`` starts at in ``text``, overlapping ones too."""
    out: list[int] = []
    i = text.find(literal)
    while i >= 0:
        out.append(i)
        i = text.find(literal, i + 1)
    return out


def _matches(
    pattern: re.Pattern[str], prefixes: tuple[str, ...] | None, form: str, seen: dict[str, list[int]]
) -> Iterator[re.Match[str]]:
    """``pattern.finditer(form)``, trying the pattern only where one of ``prefixes`` starts.

    ``prefixes`` is ``PrivateNote.prefixes``: every match starts with one. So the
    leftmost match at or after any index starts at one of their occurrences, and
    ``pattern.match(form, i)`` there is the match finditer finds (its lookbehind
    still sees ``form`` before ``i``). A match is never empty (it starts with a
    prefix that isn't), so each search resumes at the last match's end, as
    finditer does. ``seen`` keeps each prefix's occurrences across notes.
    """
    if prefixes is None or "" in prefixes:
        yield from pattern.finditer(form)
        return
    starts: set[int] = set()
    for p in prefixes:
        if p not in seen:
            seen[p] = _occurrences(form, p)
        starts.update(seen[p])
    pos = 0
    for i in sorted(starts):
        if i < pos:
            continue
        m = pattern.match(form, i)
        if m is not None:
            yield m
            pos = m.end()


def _spanning(text: str) -> Iterator[tuple[int, int]]:
    """``(start, end)`` of each comment, tag and link target in ``text``, in order, in linear time.

    A ``<!--`` runs to the next ``-->``. With none after it, it is literal text, as
    CommonMark renders it: skipped, and since no later ``<!--`` can close either, those
    are skipped without another search."""
    pos, closable = 0, True
    while (m := _SPANNING.search(text, pos)) is not None:
        if m.group(0) != "<!--":
            yield m.start(), m.end()
            pos = m.end()
            continue
        close = text.find("-->", m.end()) if closable else -1
        if close < 0:
            closable, pos = False, m.end()
            continue
        yield m.start(), close + 3
        pos = close + 3


def _demarked(text: str) -> tuple[str, list[tuple[int, int]]]:
    """``text`` without its markup, and for each of its lines the span of ``text``'s lines it came from.

    A comment or tag over a line break joins the lines around it (``Sa<!--⏎-->mmy`` reads
    ``Sammy``), so a joined line maps back to every line it took in: a hit on it reports
    them all. Then, per line, entities are decoded and inline markup dropped."""
    lines: list[str] = []
    spans: list[tuple[int, int]] = []
    current: list[str] = []
    first = line = pos = 0

    def take(segment: str) -> None:
        nonlocal current, first, line
        for k, part in enumerate(segment.split("\n")):
            if k:
                lines.append("".join(current))
                spans.append((first, line))
                current, line = [], line + 1
                first = line
            current.append(part)

    for start, end in _spanning(text):
        take(text[pos:start])
        line += text.count("\n", start, end)  # swallowed: the line being built now reaches this far
        pos = end
    take(text[pos:])
    lines.append("".join(current))
    spans.append((first, line))
    return "\n".join(_clean_line(ln) for ln in lines), spans


def _clean_line(line: str) -> str:
    """One line with entities decoded, then comments, tags, link targets and inline marks dropped."""
    return _MARKUP.sub("", _without_spans(re.sub(r"[\r\n]", " ", html.unescape(line))))


def _without_spans(line: str) -> str:
    """``line`` with what :func:`_spanning` finds in it dropped (a comment or tag decoding an entity made)."""
    out: list[str] = []
    pos = 0
    for start, end in _spanning(line):
        out.append(line[pos:start])
        pos = end
    out.append(line[pos:])
    return "".join(out)


def find_mentions(text: str, terms: PrivateTerms, *, allow_markers: bool) -> list[int]:
    """Sorted, unique 1-based numbers of the lines of ``text`` that name or link a private note.

    ``allow_markers=True`` (lint only) honours a same-line
    ``%%privacy:allow <slug>%%`` for hits on that one note; the marker text
    itself is not matched. Sends pass ``False``.
    """
    if not terms:
        return []
    lines = text.split("\n")
    allowed: list[set[str]] = [set() for _ in lines]
    if allow_markers:
        for i, ln in enumerate(lines):
            allowed[i] = {m.group(1).casefold() for m in _MARKER.finditer(ln)}
        text = _MARKER.sub(lambda m: " " * len(m.group(0)), text)  # keeps every offset
        lines = text.split("\n")
    slugs = {note.path: note.slug.casefold() for note in terms.notes}
    hits = _hits(text, terms)
    demarked, spans = _demarked(text)  # once more without markup, mapped back to text's lines
    if demarked != text:
        hits += [(p, spans[a][0], spans[b][1]) for p, a, b in _hits(demarked, terms)]
    # And line by line, so markup a join swallowed (a comment that does close, lines later) can't hide one.
    # It also flags a name inside a closed comment, which doesn't render: that fails closed, on purpose.
    per_line = "\n".join(_clean_line(ln) for ln in text.split("\n"))
    if per_line not in (text, demarked):
        hits += _hits(per_line, terms)
    for i, ln in enumerate(lines):  # each line once more, URL-decoded
        decoded = unquote_plus(ln)
        if decoded != ln:
            hits += [(p, i, i) for p, _, _ in _hits(decoded, terms)]
    out: set[int] = set()
    for path, first, last in hits:
        span = range(first, last + 1)
        if all(slugs[path] in allowed[n] for n in span):
            continue
        out.update(n + 1 for n in span)
    return sorted(out)


def marker_slugs(line: str) -> set[str]:
    """Case-folded targets of the ``%%privacy:allow <slug>%%`` markers on ``line`` (as find_mentions reads them)."""
    return {m.group(1).casefold() for m in _MARKER.finditer(line)}


def markers(line: str) -> list[str]:
    """The targets of the markers on ``line``, as written (``kb lint --report``)."""
    return [m.group(1) for m in _MARKER.finditer(line)]


def linked_slugs(line: str, terms: PrivateTerms) -> set[str]:
    """Case-folded slugs of the private notes a ``[[wikilink]]`` on ``line`` resolves to.

    Lint uses it to tell a ``private-link`` from a ``private-mention``; the
    slugs never reach its output.
    """
    out: set[str] = set()
    for m in _WIKILINK.finditer(_fold(line)[0]):
        target = _link_target(m.group(1))
        if target:
            out |= {note.slug.casefold() for note in terms.notes if note.linked_by(target)}
    return out


def is_owner_only(policy: Policy, surface: str) -> bool:
    return surface_class(policy, surface) == "owner_only"


def outbound_terms(vault: Path, surface: str) -> PrivateTerms | None:
    """What may not go to ``surface``, or ``None`` for an owner-only one (nothing is checked there).

    For a caller with several texts for one surface (the outbound gate checks a
    tool call's every field): building this walks the vault each time, cache or
    not, so build it once and give each text to :func:`find_mentions`. An
    unknown surface id is shared (fail closed).
    """
    root = vault.resolve()
    policy = load_policy(root)
    if is_owner_only(policy, surface):
        return None
    return with_shared(private_terms(root, policy), root)


def check_outbound(vault: Path, text: str, surface: str) -> list[int]:
    """Lines of ``text`` that may not go to ``surface``: ``[]`` for an owner-only one.

    An unknown surface id is shared (fail closed). Markers are ignored: a send
    is never allowed by one.
    """
    terms = outbound_terms(vault, surface)
    return [] if terms is None else find_mentions(text, terms, allow_markers=False)


def _checked(vault: Path, text: str, surface: str) -> list[int]:
    try:
        return check_outbound(vault, text, surface)
    except SharedReadsUnreadable as e:
        raise UsageError(str(e)) from e


def _refuse_agents(reader: str) -> None:
    """Only ``owner`` and ``autonomous`` may run a check: it needs the private terms,
    and an agent must not be able to probe them one guess at a time."""
    if reader not in FULL_READERS:
        raise UsageError(f"{reader} may not run privacy checks")


def cmd_check_outbound(vault: Path, reader: str, path: str, surface: str) -> Output:
    """``kb check-outbound``: exit 1 with the offending line numbers, else 0 (``ok``).

    A file that can't be read is a ``UsageError`` (exit 2); its content is never echoed.
    So is a ledger of shared reads that can't be read: the check can't say what it covers.
    """
    _refuse_agents(reader)
    try:
        with open(os.fspath(path), "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")  # bad UTF-8 never skips the check
    except OSError as e:
        raise UsageError(f"can't read {path}: {e.strerror or type(e).__name__}") from e
    lines = _checked(vault, text, surface)
    if lines:
        return Output(
            data={"ok": False, "lines": lines},
            lines=["blocked: lines " + ", ".join(str(n) for n in lines)],
            code=1,
            surface=surface,
            blocked=True,
        )
    return Output(data={"ok": True, "lines": []}, lines=["ok"], surface=surface)


def cmd_check_query(vault: Path, reader: str, text: str, surface: str) -> Output:
    """``kb check-query``: exit 1 and ``blocked`` when the query names a private note, else 0 (``ok``)."""
    _refuse_agents(reader)
    if _checked(vault, text, surface):
        return Output(data={"ok": False}, lines=["blocked"], code=1, surface=surface, blocked=True)
    return Output(data={"ok": True}, lines=["ok"], surface=surface)

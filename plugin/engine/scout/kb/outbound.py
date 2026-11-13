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

**How terms match.** Text and terms are compared under full Unicode case
folding (``STRASSE`` matches ``Straße``). A term's boundary is anything but a
letter or digit, so ``_Sammy_`` (Slack italics), ``sammy_notes``, ``Sammy.``
and ``(Sammy)`` all match and ``Sammyson`` doesn't. The parts of a term match
across any run of spaces, line breaks, ``-`` or ``_`` (``task renew permit``,
``task_renew_permit``, ``Mary-Jane`` for ``Mary Jane``), so matching runs over
the whole text, and a term split over a line break reports every line it
spans. Each line is also matched once more URL-decoded (``Jos%C3%A9+Lee``).

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
import os
import re
import unicodedata
from collections.abc import Iterable
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

    def __bool__(self) -> bool:
        return bool(self.notes)


def _norm(text: str) -> str:
    """NFKC case folding: ``Jose\u0301``, ``José`` and ``JOSÉ`` all become ``josé``."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())


def _term_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for ``terms`` over folded text (:func:`_fold`; module docstring)."""
    parts: set[str] = set()
    for t in terms:
        words = [w for w in _SPLIT.split(_norm(t)) if w]
        if words:
            parts.add(_SEP.join(re.escape(w) for w in words))
    if not parts:
        return None
    return re.compile(_BEFORE + "(?:" + "|".join(sorted(parts, key=len, reverse=True)) + ")" + _AFTER)


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
    return PrivateTerms(notes=kept, any_term=_term_regex(t for n in kept for t in n.terms))


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
    paths = _term_regex(t for n in shared for t in n.terms)
    together = PrivateNote(
        path="shared-note-paths",
        slug="shared-note-paths",
        terms=(),
        link_keys=frozenset(),
        link_path="",
        pattern=paths,
    )
    notes = (*terms.notes, _CITATION, together, *shared)
    patterns = [p.pattern for p in (terms.any_term, _CITATION.pattern, paths) if p is not None]
    return PrivateTerms(notes=notes, any_term=re.compile("|".join(patterns)), excerpts=ExcerptIndex(merged))


def _link_target(inner: str) -> str:
    """A wikilink's target, case-folded: display text (``|`` or a table's ``\\|``),
    heading/block ref and ``.md`` dropped."""
    target = inner.split("|", 1)[0].rstrip("\\").split("#", 1)[0].strip().strip("/")
    return _norm(_strip_md(target))


def _joins(ch: str) -> bool:
    """Whether ``ch`` belongs with the character before it: a combining mark, or a Hangul vowel or final jamo."""
    return bool(unicodedata.combining(ch)) or "\u1160" <= ch <= "\u11ff"


def _fold(text: str) -> tuple[str, list[int] | None]:
    """``text`` NFKC case-folded (:func:`_norm`), and for each folded character the
    index of the character it came from (None when it is ASCII, where folding keeps
    every position). Normalised one base character with its marks at a time, so a
    name spelled with combining accents (NFD) matches its precomposed term."""
    if text.isascii():
        return text.lower(), None
    out: list[str] = []
    origin: list[int] = []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and _joins(text[j]):
            j += 1
        folded = _norm(text[i:j])
        out.append(folded)
        origin.extend([i] * len(folded))
        i = j
    return "".join(out), origin


def _hits(text: str, terms: PrivateTerms) -> list[tuple[str, int, int]]:
    """``(note path, first line, last line)`` (0-based lines of ``text``) of every term and link hit."""
    folded, origin = _fold(text)
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def line(pos: int) -> int:
        return bisect.bisect_right(starts, origin[pos] if origin is not None else pos) - 1

    found: list[tuple[str, int, int]] = []
    targets = [(line(m.start()), _link_target(m.group(1))) for m in _WIKILINK.finditer(folded)]
    for n, target in targets:
        if target:
            found += [(note.path, n, n) for note in terms.notes if note.linked_by(target)]
    if terms.any_term is not None and terms.any_term.search(folded):
        for note in terms.notes:
            if note.pattern is not None:
                found += [(note.path, line(m.start()), line(m.end() - 1)) for m in note.pattern.finditer(folded)]
    if terms.excerpts is not None:
        found += terms.excerpts.hits(text)
    return found


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


def check_outbound(vault: Path, text: str, surface: str) -> list[int]:
    """Lines of ``text`` that may not go to ``surface``: ``[]`` for an owner-only one.

    An unknown surface id is shared (fail closed). Markers are ignored: a send
    is never allowed by one.
    """
    root = vault.resolve()
    policy = load_policy(root)
    if is_owner_only(policy, surface):
        return []
    return find_mentions(text, with_shared(private_terms(root, policy), root), allow_markers=False)


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

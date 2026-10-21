"""The private-content matchers: ``kb check-outbound`` / ``kb check-query`` (#453, spec §6.1, §8.1, §8.2).

Before a phase sends text to a shared surface (Slack, a connector write, a
connector or web query, a public repo, a Telegram group ...), it runs these
checks; the lint checks ``private-link`` / ``private-mention`` (Task 8) reuse
:func:`find_mentions`.

**Private terms** (:func:`private_terms`) are built from every file labelled
``private`` (owner view, so every private file):

- whole-word, case-insensitive *terms*: the file's slug (its name without the
  extension), and for a markdown note its frontmatter ``name:``, ``aliases:``
  and ``title:`` (spec §4.2 counts the title as private content), each kept
  only when it has 4 or more characters. A word boundary is "not a letter,
  digit or underscore" on each side, so ``Sammy.`` and ``(Sammy)`` match and
  ``Sammyson`` doesn't; a multi-word term matches across any run of
  whitespace (``Pri  Pri Two``).
- *wikilinks*: ``[[target]]``, ``[[target|display]]``, ``[[target#heading]]``,
  ``![[target]]`` and ``[[path/to/target]]`` whose target is, case-insensitively,
  the note's slug, file name, ``name:``, an alias, or its vault path (or a
  trailing part of it), with or without ``.md``. A link is a hit whatever the
  length of its target, so ``[[sam]]`` hits although ``sam`` is too short to be
  a term.

The result is a :class:`PrivateTerms`, grouped per private note, so a lint
marker can allow one note's hits and not another's. It is cached per process,
keyed by the vault, the policy and each private file's stat, so an edited or
added note is picked up by the next call.

**Markers.** With ``allow_markers=True`` (lint only), a same-line
``%%privacy:allow <slug>%%`` allows that line's hits that refer to that one
private note. Sends pass ``allow_markers=False``: a marker is not a send pass
(spec §8.2), and since the marker itself names the slug, such a line is a hit.

A result is only ever line numbers: no function here returns the matched term,
the note or its path to a caller that would print it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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

MIN_TERM_CHARS = 4

_WIKILINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_MARKER = re.compile(r"%%\s*privacy:allow\s+([^%\s]+)\s*%%", re.IGNORECASE)


@dataclass(frozen=True)
class PrivateNote:
    """One private file's matchable forms.

    ``terms`` are the whole-word terms (4+ characters); ``link_keys`` the
    case-folded names a wikilink may use; ``link_path`` the case-folded vault
    path without ``.md``.
    """

    path: str
    slug: str
    terms: tuple[str, ...]
    link_keys: frozenset[str]
    link_path: str
    pattern: re.Pattern[str] | None

    def mentioned_in(self, line: str) -> bool:
        return self.pattern is not None and self.pattern.search(line) is not None

    def linked_by(self, target: str) -> bool:
        """``target`` (already case-folded, heading, display and ``.md`` stripped) names this note."""
        if target in self.link_keys:
            return True
        if "/" in target:
            return self.link_path == target or self.link_path.endswith("/" + target)
        return False


@dataclass(frozen=True)
class PrivateTerms:
    """Every private note's terms and link keys (what :func:`find_mentions` consumes)."""

    notes: tuple[PrivateNote, ...]
    any_term: re.Pattern[str] | None  # a fast pre-check: any note's term

    def __bool__(self) -> bool:
        return bool(self.notes)


def _term_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    parts = sorted({r"\s+".join(re.escape(w) for w in t.split()) for t in terms}, key=len, reverse=True)
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def _strings(value: Any) -> list[str]:
    """A frontmatter value as strings: a scalar, or the scalars of a list."""
    if isinstance(value, (list, tuple)):
        return [s for v in value if not isinstance(v, (list, tuple)) for s in _strings(v)]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = " ".join(str(value).split())
        return [text] if text else []
    return []


def _frontmatter_names(path: Path) -> list[str]:
    """``name:``, ``aliases:`` and ``title:`` of a markdown note; [] when it has none or they don't parse."""
    text, _ = read_head(path)
    if text is None:
        return []
    block = classifier_block(text)
    if block is None:
        return []
    try:
        data = load_frontmatter_yaml("\n".join(block))
    except (yaml.YAMLError, RecursionError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for key in ("name", "aliases", "title"):
        out += _strings(data.get(key))
    return out


def _strip_md(name: str) -> str:
    return name[:-3] if name.lower().endswith(".md") else name


def _note(vault: Path, rel: str) -> PrivateNote:
    name = rel.rsplit("/", 1)[-1]
    slug = _strip_md(name) if name.lower().endswith(".md") else Path(name).stem
    names = [slug]
    if name.lower().endswith(".md"):
        names += _frontmatter_names(vault / rel)
    terms = tuple(dict.fromkeys(n for n in names if len(n) >= MIN_TERM_CHARS))
    keys = {n.casefold() for n in names} | {name.casefold(), _strip_md(name).casefold()}
    return PrivateNote(
        path=rel,
        slug=slug,
        terms=terms,
        link_keys=frozenset(keys),
        link_path=_strip_md(rel).casefold(),
        pattern=_term_regex(terms),
    )


_CACHE: dict[str, tuple[Any, PrivateTerms]] = {}


def clear_cache() -> None:
    """Forget the per-process term lists (tests)."""
    _CACHE.clear()


def _stat(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def private_terms(vault: Path, policy: Policy) -> PrivateTerms:
    """The private notes' terms and link keys in ``vault`` under ``policy`` (see the module docstring)."""
    root = vault.resolve()
    private = sorted(rel for rel, label in all_labels(root, policy).items() if label.visibility == "private")
    signature = (policy_hash(policy), tuple((rel, _stat(root / rel)) for rel in private))
    cached = _CACHE.get(str(root))
    if cached is not None and cached[0] == signature:
        return cached[1]
    notes = tuple(_note(root, rel) for rel in private)
    terms = PrivateTerms(notes=notes, any_term=_term_regex(t for n in notes for t in n.terms))
    _CACHE[str(root)] = (signature, terms)
    return terms


def _link_target(inner: str) -> str:
    """A wikilink's target, case-folded: display text, heading/block ref and ``.md`` dropped."""
    target = inner.split("|", 1)[0].split("#", 1)[0].strip().strip("/")
    return _strip_md(target).casefold()


def _line_hits(line: str, terms: PrivateTerms) -> set[str]:
    """Paths of the private notes ``line`` refers to."""
    hit: set[str] = set()
    targets = [_link_target(m.group(1)) for m in _WIKILINK.finditer(line)]
    targets = [t for t in targets if t]
    if targets:
        for note in terms.notes:
            if any(note.linked_by(t) for t in targets):
                hit.add(note.path)
    if terms.any_term is not None and terms.any_term.search(line):
        for note in terms.notes:
            if note.path not in hit and note.mentioned_in(line):
                hit.add(note.path)
    return hit


def find_mentions(text: str, terms: PrivateTerms, *, allow_markers: bool) -> list[int]:
    """Sorted, unique 1-based numbers of the lines of ``text`` that name or link a private note.

    ``allow_markers=True`` (lint only) honours a same-line
    ``%%privacy:allow <slug>%%`` for hits on that one note; the marker text
    itself is not matched. Sends pass ``False``.
    """
    if not terms:
        return []
    lines: list[int] = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.removesuffix("\r")
        allowed: set[str] = set()
        if allow_markers:
            allowed = {m.group(1).casefold() for m in _MARKER.finditer(line)}
            line = _MARKER.sub(" ", line)
        hits = _line_hits(line, terms)
        if allowed:
            by_path = {note.path: note for note in terms.notes}
            hits = {p for p in hits if by_path[p].slug.casefold() not in allowed}
        if hits:
            lines.append(n)
    return lines


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
    return find_mentions(text, private_terms(root, policy), allow_markers=False)


def _refuse_agents(reader: str) -> None:
    """Only ``owner`` and ``autonomous`` may run a check: it needs the private terms,
    and an agent must not be able to probe them one guess at a time."""
    if reader not in FULL_READERS:
        raise UsageError(f"{reader} may not run privacy checks")


def cmd_check_outbound(vault: Path, reader: str, path: str, surface: str) -> Output:
    """``kb check-outbound``: exit 1 with the offending line numbers, else 0 (``ok``).

    A file that can't be read is a ``UsageError`` (exit 2); its content is never echoed.
    """
    _refuse_agents(reader)
    try:
        with open(os.fspath(path), "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")  # bad UTF-8 never skips the check
    except OSError as e:
        raise UsageError(f"can't read {path}: {e.strerror or type(e).__name__}") from e
    lines = check_outbound(vault, text, surface)
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
    if check_outbound(vault, text, surface):
        return Output(data={"ok": False}, lines=["blocked"], code=1, surface=surface, blocked=True)
    return Output(data={"ok": True}, lines=["ok"], surface=surface)

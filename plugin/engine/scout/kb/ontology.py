"""SCOUT Knowledge Graph — ontology parser and query interface.

Loads the ontology schema and entity files from the knowledge base,
builds an in-memory graph, and exposes a query interface.

Usage::

    from scout.kb.ontology import KnowledgeGraph
    from scout.kb.reader import resolve_reader

    graph = KnowledgeGraph(
        schema_path="path/to/schema.yaml",
        kb_root="path/to/knowledge-base",
        reader=resolve_reader(None, os.environ),
    )
    graph.load()
    results = graph.query(type="task", domain="personal", status="open")

This is the one implementation of the vault parser's queries and walks (#453,
spec §6.1): ``name_lookup``, ``traverse``, ``path`` and ``export_json`` are
ported from the vault's ``knowledge-base/ontology/parser.py`` (rendered from
``templates/knowledge-base/ontology/parser.py``), with the same results.

**Privacy labels.** Every entity carries ``_label``, its file's
:class:`~scout.kb.privacy.Label` as JSON: the same label ``scoutctl kb
classify`` gives that file. ``reader`` is an already-resolved reader
(``resolve_reader``); ``owner`` and ``autonomous`` get every entity, labelled,
and any other reader gets an empty graph: ``load()`` lists and opens nothing.

**What a mature vault's parser grew, generically** (so such a vault can run the
shim without losing behaviour its scheduled runs rely on):

- ``load()`` is two passes: every entity first, then each relationship target
  resolved against them by display name, ``[[slug|Display]]`` pipe alias,
  filename stem or path form (``[[people/sam]]``), then declared ``aliases:``.
  A stem or alias two entities share stays unindexed, and an entity's real
  ``name`` always beats another's alias.
- Files matching the schema's ``graph.exclude`` globs (KB-relative; default
  ``projects/**/archive/**``) are skipped, so an archived snapshot can't
  shadow the live entity.
- A file whose frontmatter isn't valid YAML is kept in ``parse_errors``, and
  ``validate()`` reports it rather than dropping it silently.
- ``validate()`` checks that every relationship target resolves. The vault's
  ``schema.yaml`` ``validation:`` block lists the exceptions:
  ``external_references`` (real things never modelled), ``known_gaps``
  (``{target: reason}``, reviewed and accepted; reported in ``known_gaps``,
  not as errors) and ``provenance_relationship_types`` (targets are pointers
  to dated artifacts, not entities).
"""

from __future__ import annotations

import json as _json
import os
import pickle
import re
import threading
import time
from collections import deque
from collections.abc import Collection
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

import yaml

from scout.errors import KBError, KBSchemaError
from scout.kb.frontmatter import frontmatter_block
from scout.kb.lint_config import glob_match
from scout.kb.privacy import (
    FULL_READERS,
    NoteFacts,
    Policy,
    classifier_block,
    decode_head,
    facts_from_frontmatter,
    label_from_facts,
    load_frontmatter_yaml,
    load_policy,
    normalize_rel,
    note_facts,
    read_head,
)

# Per-process frontmatter cache (#77). ``load()`` used to read and YAML-parse
# every KB file on every call; a 3,000-file KB took ~1 s each time. Now each
# file is stat'ed, and only a file whose stat key changed is read and parsed
# again. The key is (mtime_ns, ctime_ns, size, inode): any write moves ctime,
# and an atomic replace changes the inode, so a hit is the same bytes.
#
# Values are pickled so every hit hands back fresh dicts: ``load()`` mutates
# what it gets, and callers may mutate the entities. Unparseable files are
# cached too (as None). An entity's cached frontmatter carries the file's
# privacy facts (``NoteFacts``, under ``_FACTS_KEY``), read from the same
# bytes, so a warm load labels it without reading it again; the label itself
# is rebuilt each load, because the policy may have changed.
#
# A file modified within _RACY_WINDOW_NS of the read is not cached — on a
# coarse-timestamp filesystem (HFS+: 1 s) a same-size edit in the same tick
# would otherwise keep the old key. It is re-read until it settles (git's
# "racily clean" rule).
#
# This cache lives only within one process. A command that loads the graph from
# a fresh process (``scoutctl kb query`` and the other graph commands) passes a
# ``store`` instead: the stat-keyed privacy index (``scout.kb.read``), which
# keeps each file's facts and parsed frontmatter across processes (ruling R15c).
_StatKey = tuple[int, int, int, int]
_FrontmatterCache = dict[str, tuple[_StatKey, bytes]]
_CACHE: dict[str, _FrontmatterCache] = {}
_CACHE_LOCK = threading.Lock()
_RACY_WINDOW_NS = 2_000_000_000
_FACTS_KEY = "__scout_privacy_facts__"
# A file whose frontmatter block isn't valid YAML parses to {PARSE_ERROR_KEY: message}
# (so the per-process cache keeps the failure too); ``load()`` records it in ``parse_errors``.
PARSE_ERROR_KEY = "__scout_parse_error__"

# KB-relative globs ``load()`` skips when the schema sets no ``graph.exclude``:
# verbatim pre-restructure snapshots repeat the live page's name and type.
DEFAULT_EXCLUDE = ("projects/**/archive/**",)


def clear_frontmatter_cache() -> None:
    """Forget every cached parse (tests; a caller that wants a cold load)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _stat_key(st: os.stat_result) -> _StatKey:
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _yaml_error_text(exc: yaml.YAMLError) -> str:
    """What went wrong and where (a file line), without the source snippet PyYAML
    quotes: that snippet is the note's own text, and may be private."""
    if not isinstance(exc, yaml.MarkedYAMLError):
        return type(exc).__name__
    text = "; ".join(p for p in (exc.context, exc.problem) if p) or type(exc).__name__
    mark = exc.problem_mark or exc.context_mark
    # The mark counts from the block's first line, which is the file's line 2.
    return f"{text} (line {mark.line + 2}, column {mark.column + 1})" if mark else text


def _str_set(value: Any, key: str) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise KBSchemaError(f"KB schema: validation.{key} must be a list")
    return {str(v) for v in value}


def parse_lookup_types(raw: str | None) -> set[str]:
    """``--lookup-type`` as :meth:`KnowledgeGraph.name_lookup` ``types``: ``any`` is
    the empty set (every type); otherwise a comma-separated list; default ``person``."""
    text = (raw or "person").strip().lower()
    if text == "any":
        return set()
    return {t.strip() for t in text.split(",") if t.strip()} or {"person"}


_KB_DIRS = ("knowledge-base", "knowledge_base")


def find_vault(kb_root: str | Path) -> Path:
    """The vault holding ``kb_root``: a KB folder or any folder inside one.

    Walks up from the resolved ``kb_root`` to the nearest directory that has a
    ``knowledge-base`` (or ``knowledge_base``) entry whose resolved path
    contains ``kb_root``, or that holds ``scout-config.yaml``. Raises
    ``KBError`` when there is none: a wrong vault would mislabel every file
    (path defaults are vault-relative) and miss the owner's overlay, so it
    never guesses.
    """
    kb_real = Path(kb_root).resolve()
    for d in (kb_real, *kb_real.parents):
        for name in _KB_DIRS:
            kb_dir = d / name
            if kb_dir.is_dir() and kb_real.is_relative_to(kb_dir.resolve()):
                return d
        if (d / "scout-config.yaml").is_file():
            return d
    raise KBError(
        f"can't find the vault for KB root {kb_root}: no parent folder has a knowledge-base/ holding it "
        "or a scout-config.yaml; pass vault= explicitly"
    )


_CONTAINERS = (list, dict, set, tuple)


def _cyclic(v: Any, path: frozenset[int] = frozenset()) -> bool:
    """Whether ``v`` reaches itself (``x: &r [*r]``: ``SafeLoader`` builds such a value)."""
    if not isinstance(v, _CONTAINERS):
        return False
    if id(v) in path:
        return True
    inner = path | {id(v)}
    items = (x for kv in v.items() for x in kv) if isinstance(v, dict) else v
    return any(_cyclic(x, inner) for x in items)


def _break_cycles(v: Any, path: frozenset[int] = frozenset()) -> Any:
    """A copy of ``v`` with each reference back to an enclosing container replaced by None.

    No JSON form (``kb query --json``, ``export``) or stored parse has a cycle;
    an entity whose frontmatter has one keeps everything else.
    """
    if not isinstance(v, _CONTAINERS):
        return v
    if id(v) in path:
        return None
    inner = path | {id(v)}
    if isinstance(v, dict):
        return {k: _break_cycles(x, inner) for k, x in v.items()}  # keys are hashable: no cycle through them
    if isinstance(v, list):
        return [_break_cycles(x, inner) for x in v]
    return type(v)(_break_cycles(x, inner) for x in v)


class _Labeller:
    """Vault-relative paths and labels for one ``load()``: the policy is loaded once.

    The vault is ``vault`` when given, else :func:`find_vault`. The KB root's
    vault-relative spelling comes from :func:`~scout.kb.privacy.normalize_rel`
    (symlinks such as ``knowledge_base`` and case resolved); each file's path
    is that plus the walk's own entry names, which is what ``normalize_rel``
    returns for them, so labels match ``scoutctl kb classify``. A file reached
    through a symlink (the file or a folder) goes through ``normalize_rel``
    itself and is labelled by its target; one whose target is outside the
    vault keeps its in-KB spelling.
    """

    def __init__(self, kb_root: str, vault: Path | None = None) -> None:
        self.kb_root = Path(kb_root)
        kb_real = self.kb_root.resolve()
        self.vault = (vault if vault is not None else find_vault(kb_root)).resolve()
        self.kb_rel = normalize_rel(self.vault, kb_real)  # ValueError: kb_root is outside vault
        self.policy: Policy = load_policy(self.vault)
        # String forms: pathlib's relative_to costs more than the label itself.
        root = str(self.kb_root)
        self._prefix = "" if root == "." else root.rstrip(os.sep) + os.sep
        self._real = str(kb_real)
        self._dir_ok: dict[str, bool] = {}

    def _plain(self, path: str, tail: str) -> bool:
        """No symlink between the KB root and ``path`` (``tail`` is its part below the root)."""
        if os.path.islink(path):
            return False
        parent = os.path.dirname(path)
        ok = self._dir_ok.get(parent)
        if ok is None:
            below = os.path.dirname(tail)
            ok = os.path.realpath(parent) == (os.path.join(self._real, below) if below else self._real)
            self._dir_ok[parent] = ok
        return ok

    def rel(self, path: Path) -> str:
        """``path`` (a walk result under the KB root) as a vault-relative label path."""
        s = str(path)
        tail = s[len(self._prefix) :] if s.startswith(self._prefix) else os.path.relpath(s, self.kb_root)
        fast = f"{self.kb_rel}/{tail.replace(os.sep, '/')}"
        if self._plain(s, tail):
            return fast
        try:
            return normalize_rel(self.vault, path)
        except ValueError:
            return fast

    def label(self, path: Path, facts: NoteFacts) -> dict[str, Any]:
        return label_from_facts(self.rel(path), facts, self.policy).to_json()


class FrontmatterStore(Protocol):
    """A cross-process cache of each file's parse, keyed by vault-relative path and stat key.

    ``get`` returns ``(facts, frontmatter)`` stored for exactly that key, else
    None; ``frontmatter`` is a fresh copy (``load()`` mutates it), or None for a
    file that isn't an entity. ``put`` records a parse ``load()`` just made:
    ``facts`` is None when the file isn't an entity (the store derives them).
    Both are pure-``SafeLoader`` results, so labels never depend on the cache.
    """

    def get(self, rel: str, key: _StatKey) -> tuple[NoteFacts, dict[str, Any] | None] | None: ...

    def put(
        self, rel: str, key: _StatKey, path: Path, facts: NoteFacts | None, frontmatter: dict[str, Any] | None
    ) -> None: ...


class KnowledgeGraph:
    """Markdown-native knowledge graph with YAML frontmatter entities."""

    def __init__(
        self,
        schema_path: str,
        kb_root: str,
        *,
        reader: str = "owner",
        vault: Path | str | None = None,
        store: FrontmatterStore | None = None,
    ) -> None:
        self.schema_path = schema_path
        self.kb_root = kb_root
        self.reader = reader
        # The cross-process parse cache (FrontmatterStore); None uses the per-process one.
        self.store = store
        # The vault labels are relative to; None finds it from kb_root on load (find_vault).
        self.vault = Path(vault) if vault is not None else None
        # A reader that sees nothing gets no schema read either: nothing under
        # the KB is opened for it.
        self.schema: dict[str, Any] = self._load_schema() if self._full else {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.relationships: list[dict[str, str]] = []
        # Files whose frontmatter isn't valid YAML: path (vault-relative), error, _source_path, _label.
        self.parse_errors: list[dict[str, Any]] = []
        # Set by validate(): dangling targets the vault's schema accepts as known gaps.
        self.known_gaps: list[dict[str, str]] = []
        self._slug_index: dict[str, str] = {}
        self._alias_index: dict[str, str] = {}
        self._adj: dict[str, list[tuple[str, str]]] | None = None

    @property
    def _full(self) -> bool:
        return self.reader in FULL_READERS

    def _load_schema(self) -> dict[str, Any]:
        try:
            with open(self.schema_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except OSError as e:
            raise KBSchemaError(f"KB schema not found at {self.schema_path}: {e}") from e
        except yaml.YAMLError as e:
            raise KBSchemaError(f"KB schema at {self.schema_path} is malformed: {e}") from e

    def load(self) -> KnowledgeGraph:
        """Walk all .md files in kb_root, extract frontmatter, build graph.

        Two passes: every entity first, then each relationship target resolved
        against them (:meth:`_resolve_target`). Files matching the schema's
        ``graph.exclude`` are skipped; a malformed frontmatter block lands in
        ``parse_errors``. Each entity gets ``_source_path`` and ``_label``. A
        reader outside ``FULL_READERS`` gets an empty graph and nothing is
        read. ``KBError`` when no ``vault`` was given and :func:`find_vault`
        finds none.
        """
        self.entities = {}
        self.relationships = []
        self.parse_errors = []
        self.known_gaps = []
        self._slug_index = {}
        self._alias_index = {}
        self._adj = None
        if not self._full:
            return self
        exclude = self._exclude_globs()

        labeller = _Labeller(self.kb_root, self.vault)
        root_key = os.path.abspath(self.kb_root)
        with _CACHE_LOCK:
            previous = _CACHE.get(root_key, {})
        # Rebuilt each load, so files deleted since the last one drop out.
        current: _FrontmatterCache = {}

        pending: list[tuple[str, Any]] = []
        for md_file in Path(self.kb_root).rglob("*.md"):
            if exclude:
                tail = md_file.relative_to(self.kb_root).as_posix()
                if any(glob_match(g, tail) for g in exclude):
                    continue
            if self.store is not None:
                frontmatter = self._stored_frontmatter(md_file, labeller.rel(md_file), self.store)
            else:
                frontmatter = self._cached_frontmatter(md_file, previous, current)
            if isinstance(frontmatter, dict) and PARSE_ERROR_KEY in frontmatter:
                facts = frontmatter.get(_FACTS_KEY)
                if not isinstance(facts, NoteFacts):
                    text, unreadable = read_head(md_file)
                    facts = note_facts(text, unreadable=unreadable)
                self.parse_errors.append(
                    {
                        "path": labeller.rel(md_file),
                        "error": str(frontmatter[PARSE_ERROR_KEY]),
                        "_source_path": str(md_file),
                        "_label": labeller.label(md_file, facts),
                    }
                )
                continue
            if not frontmatter or "name" not in frontmatter or "type" not in frontmatter:
                continue

            name = frontmatter["name"]
            facts = frontmatter.pop(_FACTS_KEY, None)
            if not isinstance(facts, NoteFacts):  # an override's own parse: read the head
                text, unreadable = read_head(md_file)
                facts = note_facts(text, unreadable=unreadable)
            frontmatter["_source_path"] = str(md_file)
            frontmatter["_label"] = labeller.label(md_file, facts)
            raw_relationships = frontmatter.pop("relationships", [])
            self.entities[name] = frontmatter
            pending.append((name, raw_relationships))

        self._build_indexes()
        for name, raw_relationships in pending:
            for rel in raw_relationships or []:
                target = self._resolve_target(rel.get("target", ""))
                rel_type = rel.get("type", "")
                if target and rel_type:
                    self.relationships.append({"source": name, "type": rel_type, "target": target})
                    inverse = self._get_inverse(rel_type)
                    if inverse:
                        self.relationships.append({"source": target, "type": inverse, "target": name})

        if self.store is None:
            with _CACHE_LOCK:
                _CACHE[root_key] = current
        return self

    def _stored_frontmatter(self, path: Path, rel: str, store: FrontmatterStore) -> dict[str, Any] | None:
        """``_extract_frontmatter`` through ``store`` (the privacy index), keyed by ``rel``."""
        try:
            st = path.stat()
        except OSError:
            return None
        key = _stat_key(st)  # stat before read, as in _cached_frontmatter
        hit = store.get(rel, key)
        if hit is not None:
            facts, frontmatter = hit
            if frontmatter is not None:
                frontmatter[_FACTS_KEY] = facts
            return frontmatter
        frontmatter = self._extract_frontmatter(path)
        popped = frontmatter.pop(_FACTS_KEY, None) if isinstance(frontmatter, dict) else None
        parsed = popped if isinstance(popped, NoteFacts) else None
        store.put(rel, key, path, parsed, frontmatter)
        if isinstance(frontmatter, dict) and parsed is not None:
            frontmatter[_FACTS_KEY] = parsed
        return frontmatter

    def _cached_frontmatter(
        self, path: Path, previous: _FrontmatterCache, current: _FrontmatterCache
    ) -> dict[str, Any] | None:
        """``_extract_frontmatter`` through the per-file stat cache (#77)."""
        try:
            st = path.stat()
        except OSError:
            return None
        key = _stat_key(st)
        # Stat-before-read: a write landing mid-read changes the key, so the
        # next load re-reads rather than trusting this parse.
        hit = previous.get(str(path))
        if hit is not None and hit[0] == key:
            current[str(path)] = hit
            return pickle.loads(hit[1])
        frontmatter = self._extract_frontmatter(path)
        if time.time_ns() - st.st_mtime_ns > _RACY_WINDOW_NS:
            # The safe loader yields only plain types (dict, list, str, numbers, dates), all picklable.
            current[str(path)] = (key, pickle.dumps(frontmatter, protocol=pickle.HIGHEST_PROTOCOL))
        return frontmatter

    def _extract_frontmatter(self, path: Path) -> dict[str, Any] | None:
        """Extract YAML frontmatter from a markdown file.

        One read feeds both the entity (the whole file, strict UTF-8, newlines
        as text mode reads them) and, for a mapping, the file's privacy facts
        under ``_FACTS_KEY`` for an entity (the classifier's head window, from the same
        bytes; ``load()`` pops it). A block that isn't valid YAML gives
        ``{PARSE_ERROR_KEY: message}`` (with its facts).
        """
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        block = frontmatter_block(text.split("\n"))
        if block is None:
            return None

        try:
            frontmatter = load_frontmatter_yaml("\n".join(block))
        except yaml.YAMLError as e:
            return {PARSE_ERROR_KEY: _yaml_error_text(e), _FACTS_KEY: note_facts(decode_head(data))}
        if isinstance(frontmatter, dict) and "name" in frontmatter and "type" in frontmatter:
            head = decode_head(data)
            # The classifier reads the same lines nearly always (no BOM, the
            # fence inside its window): reuse this parse rather than YAML-parse twice.
            same = classifier_block(head) == block
            facts = facts_from_frontmatter(frontmatter) if same else note_facts(head)
            if _cyclic(frontmatter):  # facts come from the parse as is, like the classifier's
                frontmatter = _break_cycles(frontmatter)
            frontmatter[_FACTS_KEY] = facts
        return frontmatter

    def _resolve_wikilink(self, text: str) -> str:
        """The inner text of '[[...]]' syntax (a pipe alias kept intact)."""
        match = re.search(r"\[\[(.+?)]]", text)
        return match.group(1) if match else text

    def _exclude_globs(self) -> tuple[str, ...]:
        """The schema's ``graph.exclude`` (KB-relative globs), else :data:`DEFAULT_EXCLUDE`."""
        graph = self.schema.get("graph") if isinstance(self.schema, dict) else None
        if not isinstance(graph, dict) or "exclude" not in graph:
            return DEFAULT_EXCLUDE
        globs = graph["exclude"]
        if globs is None:
            return ()
        if not isinstance(globs, list):
            raise KBSchemaError(f"KB schema at {self.schema_path}: graph.exclude must be a list of globs")
        return tuple(str(g) for g in globs)

    def _build_indexes(self) -> None:
        """Index entities by filename stem and by declared ``aliases:``.

        A stem or alias two entities share is left unindexed: a target never
        resolves to the wrong entity. An alias equal to some entity's ``name``
        is skipped: real names win.
        """
        slugs: dict[str, str] = {}
        aliases: dict[str, str] = {}
        clashes: set[str] = set()
        alias_clashes: set[str] = set()
        for name, entity in self.entities.items():
            stem = Path(entity.get("_source_path", "")).stem
            if stem:
                if slugs.get(stem, name) != name:
                    clashes.add(stem)
                slugs.setdefault(stem, name)
            declared = entity.get("aliases") or []
            if not isinstance(declared, list):
                continue
            for alias in declared:
                key = alias.strip() if isinstance(alias, str) else ""
                if not key or key in self.entities:
                    continue
                if aliases.get(key, name) != name:
                    alias_clashes.add(key)
                aliases.setdefault(key, name)
        self._slug_index = {k: v for k, v in slugs.items() if k not in clashes}
        self._alias_index = {k: v for k, v in aliases.items() if k not in alias_clashes}

    @staticmethod
    def _lookup(index: dict[str, str], text: str) -> str | None:
        """``text`` in ``index`` as written, else by its last path segment (``people/sam`` → ``sam``)."""
        text = text.strip()
        if not text:
            return None
        base = text.split("/")[-1].strip()
        if base.endswith(".md"):
            base = base[:-3]
        return index.get(text) or index.get(base)

    def _resolve_target(self, raw: str) -> str:
        """A relationship target as an entity's display-name key, when it names one.

        In order: the display name (``[[Sam Rivera]]``); for a pipe alias
        ``[[people/sam|Sam Rivera]]`` the right side as a name, then the left
        as a slug, then either as a declared alias; a filename stem or path
        (``[[sam]]``, ``[[people/sam]]``); a declared alias. Anything else is
        returned as written (the right side of a pipe alias), and
        ``validate()`` reports it as dangling.
        """
        text = self._resolve_wikilink(raw)
        if not text or text in self.entities:
            return text
        if "|" in text:
            left, _, right = text.partition("|")
            left, right = left.strip(), right.strip()
            if right in self.entities:
                return right
            found = (
                self._lookup(self._slug_index, left)
                or self._lookup(self._alias_index, right)
                or self._lookup(self._alias_index, left)
            )
            return found or right or left
        return self._lookup(self._slug_index, text) or self._lookup(self._alias_index, text) or text

    def entity(self, name: str) -> dict[str, Any] | None:
        """Get an entity by name. Returns None if not found."""
        return self.entities.get(name)

    def label_of(self, name: str) -> dict[str, Any] | None:
        """The ``_label`` of entity ``name``, or None when it isn't a loaded entity."""
        entity = self.entities.get(name)
        return entity.get("_label") if entity else None

    def query(self, *, visibility: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        """Query entities by property filters.

        Special filters:
            visibility: "normal" | "private" — matches the entity's privacy label
            deadline_before: str (ISO date) — matches entities with deadline <= value
            birthday_month: int — matches entities with birthday in that month
            exclude_status: str (comma-separated) or iterable — status NOT in the set

        All other filters match exact property values.
        """
        results = []
        for entity in self.entities.values():
            if visibility is not None and (entity.get("_label") or {}).get("visibility") != visibility:
                continue
            if self._matches_filters(entity, filters):
                results.append(entity)
        return results

    def related(self, name: str) -> list[dict[str, str]]:
        """Get all relationships where ``name`` is the source. ``label_of(target)`` labels each end."""
        return [r for r in self.relationships if r["source"] == name]

    def export_json(self, indent: int = 2) -> str:
        """Export the full knowledge graph as JSON (the vault parser's ``export``).

        Internal ``_`` keys are dropped, except each entity's ``_label``.
        """
        clean_entities = {}
        for name, entity in self.entities.items():
            clean_entities[name] = {k: v for k, v in entity.items() if not k.startswith("_") or k == "_label"}

        return _json.dumps(
            {"entities": clean_entities, "relationships": self.relationships},
            indent=indent,
            default=str,
        )

    def _validation_config(self) -> tuple[set[str], dict[str, str], set[str]]:
        """The schema's ``validation:`` block: external references, known gaps, provenance types."""
        block = self.schema.get("validation") if isinstance(self.schema, dict) else None
        if block is None:
            return set(), {}, set()
        if not isinstance(block, dict):
            raise KBSchemaError(f"KB schema at {self.schema_path}: validation must be a mapping")
        gaps = block.get("known_gaps") or {}
        if not isinstance(gaps, dict):
            raise KBSchemaError(f"KB schema at {self.schema_path}: validation.known_gaps must map target to reason")
        return (
            _str_set(block.get("external_references"), "external_references"),
            {str(k): "" if v is None else str(v) for k, v in gaps.items()},
            _str_set(block.get("provenance_relationship_types"), "provenance_relationship_types"),
        )

    def validate(self) -> list[dict[str, str]]:
        """Validate all entities against the schema. Returns list of errors.

        Beyond the schema checks: each file in ``parse_errors`` (its
        vault-relative path as ``entity``), and each relationship target that
        resolves to no entity, unless the schema's ``validation:`` block lists
        it. A dangling target listed under ``known_gaps`` lands in
        ``self.known_gaps`` (``entity``, ``type``, ``target``, ``reason``)
        instead of the errors.
        """
        errors: list[dict[str, str]] = []
        external, gaps, provenance = self._validation_config()
        self.known_gaps = []
        entity_types = self.schema.get("entity_types", {})
        rel_types = self.schema.get("relationship_types", {})

        for name, entity in self.entities.items():
            etype = entity.get("type", "")
            type_def = entity_types.get(etype)

            if not type_def:
                errors.append({"entity": name, "message": f"Unknown entity type: {etype}"})
                continue

            # Check required properties
            for prop in type_def.get("properties", {}).get("required", []):
                if prop not in entity:
                    errors.append({"entity": name, "message": f"Missing required property: {prop}"})

            # Check relationships from this entity
            for rel in self.relationships:
                if rel["source"] == name:
                    if rel["type"] not in rel_types:
                        errors.append({"entity": name, "message": f"Invalid relationship type: {rel['type']}"})

            # Check for orphaned entities (no relationships at all)
            has_rels = any(r["source"] == name or r["target"] == name for r in self.relationships)
            if not has_rels:
                errors.append({"entity": name, "message": "Orphaned entity — no relationships"})

        for pe in self.parse_errors:
            errors.append({"entity": pe["path"], "message": f"YAML parse failure: {pe['error']}"})

        seen: set[tuple[str, str, str]] = set()
        for rel in self.relationships:
            target = rel["target"]
            if rel["type"] in provenance or target in self.entities or target in external:
                continue
            key = (rel["source"], rel["type"], target)
            if key in seen:
                continue
            seen.add(key)
            if target in gaps:
                self.known_gaps.append(
                    {"entity": rel["source"], "type": rel["type"], "target": target, "reason": gaps[target]}
                )
            else:
                errors.append(
                    {
                        "entity": rel["source"],
                        "message": f"Dangling relationship target: {rel['type']} → '{target}' (no such entity)",
                    }
                )
        return errors

    def _matches_filters(self, entity: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check if an entity matches all provided filters."""
        for key, value in filters.items():
            if key == "deadline_before":
                deadline = entity.get("deadline", "")
                if not deadline or str(deadline) > str(value):
                    return False
            elif key == "birthday_month":
                birthday = entity.get("birthday", "")
                if not birthday:
                    return False
                try:
                    # YYYY-MM-DD, or MM-DD (--MM-DD) when the year isn't known.
                    parts = str(birthday).lstrip("-").split("-")
                    month = int(parts[1] if len(parts) >= 3 else parts[0])
                    if month != value:
                        return False
                except (IndexError, ValueError):
                    return False
            elif key == "exclude_status":
                # Negative filter — the entity's status must NOT be in this set.
                # Accepts a single string, a comma-separated string, or an iterable.
                excluded = {s.strip() for s in str(value).split(",")} if isinstance(value, str) else set(value)
                if str(entity.get("status", "")) in excluded:
                    return False
            else:
                if entity.get(key) != value:
                    return False
        return True

    def _get_inverse(self, rel_type: str) -> str | None:
        """Look up the inverse relationship type from the schema."""
        rel_def = self.schema.get("relationship_types", {}).get(rel_type)
        if rel_def:
            return rel_def.get("inverse")
        return None

    def name_lookup(
        self,
        token: str,
        max_results: int = 5,
        distance_threshold: float = 0.7,
        types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve a transcribed name token against entities of ``types``.

        ``types`` defaults to ``{"person"}``; an empty collection searches
        every type (``--lookup-type any``: speech-to-text garbles company and
        product names as often as people's). Each candidate carries its
        entity ``type``.

        Call before writing any transcript-derived name into action items, the
        KB or a DM. Returns ranked candidates: treat ``match_strength ==
        "exact"`` as authoritative, ``"fuzzy"`` as propose-with-marker, and an
        empty list as "route to the review queue, never elevate to a 🔴
        headline". Each candidate carries the person's ``_label``.

        Match strategy:
            1. Exact (case-insensitive) match against ``name`` or any ``aliases`` value.
            2. Fuzzy match via difflib ``SequenceMatcher.ratio()``. The default 0.7
               threshold ≈ Levenshtein-distance-2 on short strings.
        """
        token_lower = token.lower().strip()
        if not token_lower:
            return []
        wanted = {"person"} if types is None else set(types)

        candidates: list[dict[str, Any]] = []

        for entity_name, entity in self.entities.items():
            if wanted and entity.get("type") not in wanted:
                continue

            slug = Path(entity.get("_source_path", "")).stem
            field_pool: list[tuple[str, str]] = [("name", entity_name)]
            for alias in entity.get("aliases", []) or []:
                if alias:
                    field_pool.append(("alias", str(alias)))

            best_score = 0.0
            best_field = ""
            best_strength = ""

            for field_name, field_value in field_pool:
                value_lower = field_value.lower().strip()
                if not value_lower:
                    continue
                if value_lower == token_lower:
                    best_score = 1.0
                    best_field = field_name
                    best_strength = "exact"
                    break
                ratio = SequenceMatcher(None, value_lower, token_lower).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_field = field_name
                    best_strength = "fuzzy" if ratio >= distance_threshold else ""

            if best_strength:
                candidates.append(
                    {
                        "name": entity_name,
                        "slug": slug,
                        "type": entity.get("type", ""),
                        "match_strength": best_strength,
                        "matched_field": best_field,
                        "score": round(best_score, 3),
                        "_label": entity.get("_label"),
                    }
                )

        candidates.sort(key=lambda c: (c["match_strength"] != "exact", -c["score"]))
        return candidates[:max_results]

    # ------------------------------------------------------------------
    # Graph traversal — BFS over the entity graph. Inverse edges added by
    # load() are already in self.relationships, so these walk the full
    # reachability graph. In v1 a full reader sees every node, private ones
    # included (labelled); any other reader has an empty graph.
    # ------------------------------------------------------------------

    def _adjacency(self) -> dict[str, list[tuple[str, str]]]:
        """Build + cache ``{source: [(rel_type, target), ...]}`` (reset by ``load()``)."""
        if self._adj is None:
            adj: dict[str, list[tuple[str, str]]] = {}
            for r in self.relationships:
                adj.setdefault(r["source"], []).append((r["type"], r["target"]))
            self._adj = adj
        return self._adj

    def traverse(self, start: str, max_hops: int = 2, rel_types: Any = None) -> list[dict[str, Any]]:
        """BFS from ``start`` up to ``max_hops``.

        Returns each reachable node with its hop distance, the relationship it
        was first reached by (``via``), the shortest typed ``path`` and its
        ``_label`` (None for a relationship target that isn't a loaded
        entity). ``rel_types`` optionally restricts the walk to a set of
        relationship types.
        """
        if start not in self.entities:
            return []
        adj = self._adjacency()
        allow = set(rel_types) if rel_types else None
        seen = {start}
        queue: deque[tuple[str, int, list[tuple[str, str]]]] = deque([(start, 0, [])])
        out: list[dict[str, Any]] = []
        while queue:
            node, hops, path = queue.popleft()
            if hops >= max_hops:
                continue
            for rel_type, target in adj.get(node, []):
                if allow is not None and rel_type not in allow:
                    continue
                if target in seen:
                    continue
                seen.add(target)
                new_path = [*path, (rel_type, target)]
                out.append(
                    {
                        "entity": target,
                        "hops": hops + 1,
                        "via": rel_type,
                        "path": new_path,
                        "_label": self.label_of(target),
                    }
                )
                queue.append((target, hops + 1, new_path))
        return out

    def path(self, source: str, target: str, max_hops: int = 6) -> list[tuple[str, str]] | None:
        """Shortest typed path from ``source`` to ``target`` (BFS).

        A list of ``(rel_type, node)`` steps from the first hop, ``[]`` if
        source == target, or None if no path within ``max_hops``. Label each
        node with :meth:`label_of`.
        """
        if source not in self.entities or target not in self.entities:
            return None
        if source == target:
            return []
        adj = self._adjacency()
        seen = {source}
        queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(source, [])])
        while queue:
            node, p = queue.popleft()
            if len(p) >= max_hops:
                continue
            for rel_type, nxt in adj.get(node, []):
                if nxt in seen:
                    continue
                new_path = [*p, (rel_type, nxt)]
                if nxt == target:
                    return new_path
                seen.add(nxt)
                queue.append((nxt, new_path))
        return None

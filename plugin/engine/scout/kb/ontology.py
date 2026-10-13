"""SCOUT Knowledge Graph — ontology parser and query interface.

Loads the ontology schema and entity files from the knowledge base,
builds an in-memory graph, and exposes a query interface.

Usage::

    from scout.kb.ontology import KnowledgeGraph

    graph = KnowledgeGraph(schema_path="path/to/schema.yaml", kb_root="path/to/knowledge-base")
    graph.load()
    results = graph.query(type="task", domain="personal", status="open")

The future CLI surface is ``scoutctl kb query`` (Plan 4).
"""

from __future__ import annotations

import json as _json
import os
import pickle
import re
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from scout.errors import KBSchemaError
from scout.kb.frontmatter import frontmatter_block

# Per-process frontmatter cache (#77). ``load()`` used to read and YAML-parse
# every KB file on every call; a 3,000-file KB took ~1 s each time. Now each
# file is stat'ed, and only a file whose stat key changed is read and parsed
# again. The key is (mtime_ns, ctime_ns, size, inode): any write moves ctime,
# and an atomic replace changes the inode, so a hit is the same bytes.
#
# Values are pickled so every hit hands back fresh dicts: ``load()`` mutates
# what it gets, and callers may mutate the entities. Unparseable files are
# cached too (as None).
#
# A file modified within _RACY_WINDOW_NS of the read is not cached — on a
# coarse-timestamp filesystem (HFS+: 1 s) a same-size edit in the same tick
# would otherwise keep the old key. It is re-read until it settles (git's
# "racily clean" rule).
#
# Only within one process: nothing in the engine loads the graph from a fresh
# process often enough to pay for an on-disk cache and its cross-session
# invalidation.
_StatKey = tuple[int, int, int, int]
_FrontmatterCache = dict[str, tuple[_StatKey, bytes]]
_CACHE: dict[str, _FrontmatterCache] = {}
_CACHE_LOCK = threading.Lock()
_RACY_WINDOW_NS = 2_000_000_000


def clear_frontmatter_cache() -> None:
    """Forget every cached parse (tests; a caller that wants a cold load)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _stat_key(st: os.stat_result) -> _StatKey:
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


class KnowledgeGraph:
    """Markdown-native knowledge graph with YAML frontmatter entities."""

    def __init__(self, schema_path: str, kb_root: str) -> None:
        self.schema_path = schema_path
        self.kb_root = kb_root
        self.schema = self._load_schema()
        self.entities: dict[str, dict[str, Any]] = {}
        self.relationships: list[dict[str, str]] = []

    def _load_schema(self) -> dict[str, Any]:
        try:
            with open(self.schema_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except OSError as e:
            raise KBSchemaError(f"KB schema not found at {self.schema_path}: {e}") from e
        except yaml.YAMLError as e:
            raise KBSchemaError(f"KB schema at {self.schema_path} is malformed: {e}") from e

    def load(self) -> KnowledgeGraph:
        """Walk all .md files in kb_root, extract frontmatter, build graph."""
        self.entities = {}
        self.relationships = []

        root_key = os.path.abspath(self.kb_root)
        with _CACHE_LOCK:
            previous = _CACHE.get(root_key, {})
        # Rebuilt each load, so files deleted since the last one drop out.
        current: _FrontmatterCache = {}

        for md_file in Path(self.kb_root).rglob("*.md"):
            frontmatter = self._cached_frontmatter(md_file, previous, current)
            if not frontmatter or "name" not in frontmatter or "type" not in frontmatter:
                continue

            name = frontmatter["name"]
            frontmatter["_source_path"] = str(md_file)
            raw_relationships = frontmatter.pop("relationships", [])
            self.entities[name] = frontmatter

            for rel in raw_relationships or []:
                target = self._resolve_wikilink(rel.get("target", ""))
                rel_type = rel.get("type", "")
                if target and rel_type:
                    self.relationships.append({"source": name, "type": rel_type, "target": target})
                    inverse = self._get_inverse(rel_type)
                    if inverse:
                        self.relationships.append({"source": target, "type": inverse, "target": name})

        with _CACHE_LOCK:
            _CACHE[root_key] = current
        return self

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
            # safe_load yields only plain types (dict, list, str, numbers, dates), all picklable.
            current[str(path)] = (key, pickle.dumps(frontmatter, protocol=pickle.HIGHEST_PROTOCOL))
        return frontmatter

    def _extract_frontmatter(self, path: Path) -> dict[str, Any] | None:
        """Extract YAML frontmatter from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        block = frontmatter_block(text.split("\n"))
        if block is None:
            return None

        try:
            return yaml.safe_load("\n".join(block))
        except yaml.YAMLError:
            return None

    def _resolve_wikilink(self, text: str) -> str:
        """Extract entity name from '[[Name]]' syntax."""
        match = re.search(r"\[\[(.+?)]]", text)
        return match.group(1) if match else text

    def entity(self, name: str) -> dict[str, Any] | None:
        """Get an entity by name. Returns None if not found."""
        return self.entities.get(name)

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        """Query entities by property filters.

        Special filters:
            deadline_before: str (ISO date) — matches entities with deadline <= value
            birthday_month: int — matches entities with birthday in that month

        All other filters match exact property values.
        """
        results = []
        for entity in self.entities.values():
            if self._matches_filters(entity, filters):
                results.append(entity)
        return results

    def related(self, name: str) -> list[dict[str, str]]:
        """Get all relationships where ``name`` is the source."""
        return [r for r in self.relationships if r["source"] == name]

    def export_json(self, indent: int = 2) -> str:
        """Export the full knowledge graph as JSON."""
        clean_entities = {}
        for name, entity in self.entities.items():
            clean_entities[name] = {k: v for k, v in entity.items() if not k.startswith("_")}

        return _json.dumps(
            {"entities": clean_entities, "relationships": self.relationships},
            indent=indent,
            default=str,
        )

    def validate(self) -> list[dict[str, str]]:
        """Validate all entities against the schema. Returns list of errors."""
        errors: list[dict[str, str]] = []
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
                    month = int(str(birthday).split("-")[1])
                    if month != value:
                        return False
                except (IndexError, ValueError):
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

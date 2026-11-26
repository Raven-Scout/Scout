"""Pluggable domain-level knowledge-base backend interface (:class:`KBBackend`).

Scout's markdown files and git history remain the single source of truth. Any
graph database (such as RelateDB or SQLite+FTS5) runs strictly beside the vault
as a derived, rebuildable, read-only index behind :class:`KBBackend`.

:class:`FilesBackend` is the zero-dependency reference implementation wrapping
today's :class:`~scout.kb.ontology.KnowledgeGraph`, :mod:`scout.kb.read`, and
the lazy property-graph builder in :mod:`scout.kb.export_jsonl`. Nothing changes
for vaults that do not opt into an external backend, and every secondary backend
is parity-tested against :class:`FilesBackend`.
"""

from __future__ import annotations

import os
from collections import Counter, deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from scout.errors import ConfigError
from scout.kb.export_jsonl import PropertyGraph, build_property_graph
from scout.kb.lint_config import glob_match
from scout.kb.ontology import KnowledgeGraph
from scout.kb.privacy import normalize_rel, reads_any
from scout.kb.read import Match, load_graph, query_rows, resolve, search

BACKEND_ENV = "SCOUT_KB_BACKEND"
DEFAULT_BACKEND_KIND = "files"
SUPPORTED_BACKENDS = ("files",)


@dataclass(frozen=True)
class Capabilities:
    """Feature flags advertised by a :class:`KBBackend` so callers degrade explicitly."""

    pattern_queries: bool = False
    fulltext: bool = False
    regex_search: bool = True
    vector: bool = False
    edge_properties: bool = False
    body_links: bool = True
    transactions: bool = False

    def to_json(self) -> dict[str, bool]:
        return asdict(self)


@runtime_checkable
class KBBackend(Protocol):
    """Domain-level read interface over a Scout vault for a resolved ``reader``."""

    @property
    def kind(self) -> str: ...

    @property
    def reader(self) -> str: ...

    def capabilities(self) -> Capabilities: ...

    def build(self, *, snapshot_sha: str | None = None) -> dict[str, Any]: ...

    def entities(self, *, visibility: str | None = None, **filters: Any) -> list[dict[str, Any]]: ...

    def entity(self, name: str) -> dict[str, Any] | None: ...

    def related(self, name: str) -> list[dict[str, Any]]: ...

    def neighbors(
        self,
        names: str | Sequence[str],
        *,
        depth: int = 2,
        rel_types: Collection[str] | None = None,
        node_types: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def paths(
        self,
        source: str,
        target: str,
        *,
        max_hops: int = 6,
        rel_types: Collection[str] | None = None,
        k: int = 1,
    ) -> list[list[tuple[str, str]]]: ...

    def backlinks(self, path: str) -> list[dict[str, Any]]: ...

    def link_gaps(self, *, scope: str | None = None, limit: int = 25) -> list[dict[str, Any]]: ...

    def search(
        self,
        pattern: str,
        *,
        globs: Sequence[str] = (),
        fixed: bool = False,
        ignore_case: bool = False,
        k: int | None = None,
    ) -> list[Match]: ...

    def resolve(self, name: str) -> list[dict[str, Any]]: ...

    def name_lookup(
        self,
        token: str,
        *,
        max_results: int = 5,
        distance_threshold: float = 0.7,
        types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def stats(self) -> dict[str, Any]: ...

    def validate(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]: ...


FILES_CAPABILITIES = Capabilities(
    pattern_queries=False,
    fulltext=False,
    regex_search=True,
    vector=False,
    edge_properties=False,
    body_links=True,
    transactions=False,
)


def _matches_scope(rel_path: str, scope: str) -> bool:
    if any(ch in scope for ch in "*?"):
        return glob_match(scope, rel_path)
    prefix = scope.rstrip("/")
    return rel_path == prefix or rel_path.startswith(prefix + "/")


class FilesBackend:
    """Reference :class:`KBBackend` backed directly by markdown files and :class:`KnowledgeGraph`."""

    def __init__(
        self,
        vault: Path,
        reader: str,
        *,
        kb_root: str | None = None,
        schema: str | None = None,
        graph: KnowledgeGraph | None = None,
    ) -> None:
        self._vault = vault.resolve()
        self._reader = reader
        self._kb_root = kb_root
        self._schema = schema
        self._graph: KnowledgeGraph | None = graph
        self._prop_graph: PropertyGraph | None = None

    @property
    def kind(self) -> str:
        return "files"

    @property
    def vault(self) -> Path:
        return self._vault

    @property
    def reader(self) -> str:
        return self._reader

    def capabilities(self) -> Capabilities:
        return FILES_CAPABILITIES

    def _ensure_graph(self) -> KnowledgeGraph:
        if self._graph is None:
            self._graph = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        return self._graph

    def _ensure_property_graph(self) -> PropertyGraph:
        if self._prop_graph is None:
            g = self._ensure_graph()
            self._prop_graph = build_property_graph(
                self._vault,
                self._reader,
                graph=g,
                kb_root=self._kb_root,
                schema=self._schema,
                include_body=False,
            )
        return self._prop_graph

    def build(self, *, snapshot_sha: str | None = None) -> dict[str, Any]:
        """Refresh the in-memory ontology and body-link graph from disk."""
        self._graph = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        self._prop_graph = build_property_graph(
            self._vault,
            self._reader,
            graph=self._graph,
            kb_root=self._kb_root,
            schema=self._schema,
            include_body=False,
        )
        return {
            "kind": self.kind,
            "reader": self._reader,
            "requested_sha": snapshot_sha,
            "sha": self._prop_graph.meta["sha"],
            "notes": self._prop_graph.meta["notes"],
            "entities": len(self._graph.entities),
            "relationships": len(self._graph.relationships),
        }

    def entities(self, *, visibility: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        return query_rows(self._ensure_graph(), filters, visibility)

    def entity(self, name: str) -> dict[str, Any] | None:
        return self._ensure_graph().entity(name)

    def related(self, name: str) -> list[dict[str, Any]]:
        g = self._ensure_graph()
        return [{**r, "label": g.label_of(r["target"])} for r in g.related(name)]

    def neighbors(
        self,
        names: str | Sequence[str],
        *,
        depth: int = 2,
        rel_types: Collection[str] | None = None,
        node_types: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if depth <= 0 or (limit is not None and limit <= 0):
            return []
        g = self._ensure_graph()
        seeds = [names] if isinstance(names, str) else list(names)
        valid_seeds = [s for s in seeds if s in g.entities]
        if not valid_seeds:
            return []

        allow_rels = set(rel_types) if rel_types else None
        allow_nodes = {t.lower() for t in node_types} if node_types else None
        adj = g._adjacency()

        seen: set[str] = set(valid_seeds)
        queue: deque[tuple[str, int, list[tuple[str, str]]]] = deque((s, 0, []) for s in valid_seeds)
        out: list[dict[str, Any]] = []

        while queue:
            node, hops, path = queue.popleft()
            if hops >= depth:
                continue
            for rtype, target in adj.get(node, []):
                if allow_rels is not None and rtype not in allow_rels:
                    continue
                if target in seen:
                    continue
                seen.add(target)
                new_path = [*path, (rtype, target)]
                queue.append((target, hops + 1, new_path))

                if allow_nodes is not None:
                    ent = g.entities.get(target)
                    etype = str(ent.get("type", "")).lower() if ent else ""
                    if etype not in allow_nodes:
                        continue

                out.append(
                    {
                        "entity": target,
                        "hops": hops + 1,
                        "via": rtype,
                        "path": new_path,
                        "_label": g.label_of(target),
                    }
                )
                if limit is not None and len(out) >= limit:
                    return out
        return out

    def paths(
        self,
        source: str,
        target: str,
        *,
        max_hops: int = 6,
        rel_types: Collection[str] | None = None,
        k: int = 1,
    ) -> list[list[tuple[str, str]]]:
        if k <= 0 or max_hops < 0:
            return []
        g = self._ensure_graph()
        if source not in g.entities or target not in g.entities:
            return []
        if source == target:
            return [[]]
        if max_hops == 0:
            return []

        allow_rels = set(rel_types) if rel_types else None
        adj = g._adjacency()

        if k == 1:
            # Fast single-path BFS with visited set (exact match with KnowledgeGraph.path)
            seen = {source}
            q1: deque[tuple[str, list[tuple[str, str]]]] = deque([(source, [])])
            while q1:
                node, p = q1.popleft()
                if len(p) >= max_hops:
                    continue
                for rtype, nxt in adj.get(node, []):
                    if allow_rels is not None and rtype not in allow_rels:
                        continue
                    if nxt in seen:
                        continue
                    new_p = [*p, (rtype, nxt)]
                    if nxt == target:
                        return [new_p]
                    seen.add(nxt)
                    q1.append((nxt, new_p))
            return []

        # General k-shortest simple paths in BFS hop order
        found: list[list[tuple[str, str]]] = []
        qk: deque[tuple[str, list[tuple[str, str]], frozenset[str]]] = deque([(source, [], frozenset({source}))])
        while qk and len(found) < k:
            node, p, on_path = qk.popleft()
            if len(p) >= max_hops:
                continue
            for rtype, nxt in adj.get(node, []):
                if allow_rels is not None and rtype not in allow_rels:
                    continue
                if nxt in on_path:
                    continue
                new_p = [*p, (rtype, nxt)]
                if nxt == target:
                    found.append(new_p)
                    if len(found) >= k:
                        break
                else:
                    qk.append((nxt, new_p, on_path | {nxt}))
        return found

    def backlinks(self, path: str) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        try:
            rel = normalize_rel(self._vault, path)
        except ValueError:
            return []
        pg = self._ensure_property_graph()
        if rel not in pg.nodes:
            return []
        rows: list[dict[str, Any]] = []
        for edge in pg.edges:
            if edge["type"] == "LINKS_TO" and edge["dst"] == rel:
                src = edge["src"]
                src_node = pg.nodes.get(src, {})
                props = edge["props"]
                rows.append(
                    {
                        "source": src,
                        "count": props.get("count", 1),
                        "lines": list(props.get("lines", [])),
                        "sections": list(props.get("sections", [])),
                        "ambiguous": bool(props.get("ambiguous", False)),
                        "label": src_node.get("_label"),
                    }
                )
        rows.sort(key=lambda r: r["source"])
        return rows

    def link_gaps(self, *, scope: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        if limit <= 0 or not reads_any(self._reader):
            return []
        g = self._ensure_graph()
        pg = self._ensure_property_graph()
        note_to_entity = {rel_key: ename for ename, rel_key in pg.entity_keys.items()}
        typed_pairs = {(r["source"], r["target"]) for r in g.relationships} | {
            (r["target"], r["source"]) for r in g.relationships
        }

        gaps: list[dict[str, Any]] = []
        for edge in pg.edges:
            if edge["type"] != "LINKS_TO":
                continue
            src_rel, dst_rel = edge["src"], edge["dst"]
            src_ent = note_to_entity.get(src_rel)
            dst_ent = note_to_entity.get(dst_rel)
            if not src_ent or not dst_ent or src_ent == dst_ent:
                continue
            if (src_ent, dst_ent) in typed_pairs:
                continue
            if scope and not (_matches_scope(src_rel, scope) or _matches_scope(dst_rel, scope)):
                continue
            props = edge["props"]
            gaps.append(
                {
                    "source": src_ent,
                    "target": dst_ent,
                    "source_path": src_rel,
                    "target_path": dst_rel,
                    "count": props.get("count", 1),
                    "lines": list(props.get("lines", [])),
                    "source_label": g.label_of(src_ent),
                    "target_label": g.label_of(dst_ent),
                }
            )
        gaps.sort(key=lambda r: (-int(r["count"]), str(r["source"]), str(r["target"])))
        return gaps[:limit]

    def search(
        self,
        pattern: str,
        *,
        globs: Sequence[str] = (),
        fixed: bool = False,
        ignore_case: bool = False,
        k: int | None = None,
    ) -> list[Match]:
        hits = search(self._vault, self._reader, pattern, globs, fixed=fixed, ignore_case=ignore_case)
        if k is not None and k >= 0:
            return hits[:k]
        return hits

    def resolve(self, name: str) -> list[dict[str, Any]]:
        return resolve(self._vault, self._reader, self._ensure_graph(), name)

    def name_lookup(
        self,
        token: str,
        *,
        max_results: int = 5,
        distance_threshold: float = 0.7,
        types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._ensure_graph().name_lookup(
            token,
            max_results=max_results,
            distance_threshold=distance_threshold,
            types=types,
        )

    def stats(self) -> dict[str, Any]:
        g = self._ensure_graph()
        by_type = dict(sorted(Counter(str(e.get("type", "unknown")) for e in g.entities.values()).items()))
        by_rel = dict(sorted(Counter(r["type"] for r in g.relationships).items()))
        return {
            "entities": len(g.entities),
            "relationships": len(g.relationships),
            "by_type": by_type,
            "by_rel_type": by_rel,
        }

    def validate(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        g = self._ensure_graph()
        errors = g.validate()
        return errors, list(g.known_gaps)


def resolve_backend_kind(
    vault: Path,
    *,
    kind: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve active KB backend kind: explicit arg -> ``SCOUT_KB_BACKEND`` -> ``scout-config.yaml`` -> ``files``."""
    environ = os.environ if env is None else env
    chosen = kind or environ.get(BACKEND_ENV)
    if not chosen:
        from scout.config import load_config

        cfg = load_config(vault)
        kb_cfg = cfg.get("kb")
        if isinstance(kb_cfg, dict):
            backend_cfg = kb_cfg.get("backend")
            if isinstance(backend_cfg, str):
                chosen = backend_cfg
            elif isinstance(backend_cfg, dict) and isinstance(backend_cfg.get("kind"), str):
                chosen = backend_cfg["kind"]
    resolved = (chosen or DEFAULT_BACKEND_KIND).strip().lower() or DEFAULT_BACKEND_KIND
    if resolved not in SUPPORTED_BACKENDS:
        allowed = ", ".join(SUPPORTED_BACKENDS)
        raise ConfigError(f"Unsupported KB backend {resolved!r} (supported: {allowed})")
    return resolved


def open_backend(
    vault: Path,
    reader: str,
    *,
    kind: str | None = None,
    kb_root: str | None = None,
    schema: str | None = None,
    env: Mapping[str, str] | None = None,
) -> KBBackend:
    """Open the configured :class:`KBBackend` for ``(vault, reader)``."""
    resolve_backend_kind(vault, kind=kind, env=env)
    return FilesBackend(vault, reader, kb_root=kb_root, schema=schema)

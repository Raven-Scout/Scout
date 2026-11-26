"""RelateDB (:mod:`relate`) adapter and per-reader rebuild-and-swap snapshot manager.

Implements :class:`~scout.kb.backend.KBBackend` over derived, read-only RelateDB
(``.rl``) snapshots stored in an owner-only, local-only cache directory
(``<vault>/.scout-cache/relate/``, mode ``0700``). Markdown and git remain the
single source of truth; writes never go through RelateDB.

**Privacy & isolation invariants:**

1. Non-reading ``agent:*`` readers (:func:`~scout.kb.privacy.reads_any` is false)
   never create, open, or stat any store on disk and always get empty results.
2. Separate physical snapshots are built per reader class (``full`` for
   ``owner``/``autonomous``, ``elsewhere``, and ``delegate_<slug>_<hash>`` for
   each delegate), because RelateDB has no row-level access control.
3. Snapshots are built into a temporary ``0600`` ``.rl`` file, checkpointed on
   close, atomically moved into place with ``os.replace``, and activated by
   atomically writing ``<slot>-current.json`` (rebuild-and-swap), avoiding
   single-writer conflicts and incremental-sync drift.
4. ``RELATE_TELEMETRY=0`` and ``DO_NOT_TRACK=1`` are set in ``os.environ``
   before importing ``relate`` so telemetry stays disabled under every entry path.

**Cypher 25 rules (from Phase 0 spike findings on ``relate-db`` 1.3.x):**

- Never emit unbounded ``[*]`` (silently truncates at 32 hops); bound to ``<= 500``.
- Use ``shortestPath`` / ``allShortestPaths`` with ``WHERE all(r IN rs WHERE ...)``
  rather than filtered GQL ``SHORTEST`` path selectors (which regress to >24 s).
- Use quantified path patterns ``(()-[r WHERE ...]->()){1,N}`` for ``neighbors``.
- Keep exact/regex ``search`` on ripgrep (:func:`scout.kb.read.search`), exposing
  full-text index queries separately via :meth:`RelateBackend.ranked_search`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from scout import __version__, safe_write
from scout.errors import ConfigError
from scout.kb.backend import Capabilities, _matches_scope
from scout.kb.export_jsonl import PropertyGraph, build_property_graph
from scout.kb.ontology import KnowledgeGraph
from scout.kb.privacy import (
    CLASSIFIER_VERSION,
    FULL_READERS,
    Policy,
    load_policy,
    normalize_rel,
    reads_any,
)
from scout.kb.read import Match, _dec, _enc, _NotJSON, _Scope, _walk, load_graph, policy_hash, resolve, search
from scout.kb.reader import ELSEWHERE_READER, delegate_id

RELATE_CACHE_DIR = ".scout-cache/relate"
SNAPSHOT_SCHEMA_VERSION = 1
LOAD_BATCH = 500
MAX_CYPHER_HOPS = 500

RELATE_CAPABILITIES = Capabilities(
    pattern_queries=True,
    fulltext=True,
    regex_search=True,
    vector=False,
    edge_properties=True,
    body_links=True,
    transactions=True,
)

SCHEMA_DDL: tuple[str, ...] = (
    "CREATE CONSTRAINT node_key IF NOT EXISTS FOR (n:Node) REQUIRE n.key IS UNIQUE",
    "CREATE RANGE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE RANGE INDEX note_layer IF NOT EXISTS FOR (n:Note) ON (n.layer)",
    "CREATE FULLTEXT INDEX note_text IF NOT EXISTS FOR (n:Note) ON EACH [n.title, n.body]",
)

LOAD_NODES_CYPHER = """
UNWIND $rows AS row
CREATE (n:Node {key: row.key})
SET n += row.props, n:$(row.labels)
"""

LOAD_EDGES_CYPHER = """
UNWIND $rows AS e
MATCH (s:Node {key: e.src}), (t:Node {key: e.dst})
CREATE (s)-[r:$(e.type)]->(t)
SET r = e.props
"""

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def ensure_telemetry_disabled() -> None:
    """Enforce Scout's zero-telemetry guarantee before touching RelateDB."""
    os.environ.setdefault("RELATE_TELEMETRY", "0")
    os.environ.setdefault("DO_NOT_TRACK", "1")


def _import_relate() -> Any:
    ensure_telemetry_disabled()
    try:
        import relate
    except ImportError as exc:
        raise ConfigError(
            "RelateDB backend requires the optional 'relate' extra: pip install 'scout-engine[relate]'"
        ) from exc
    return relate


def reader_slot(reader: str) -> str:
    """Map a resolved ``reader`` to its isolated snapshot slot name."""
    if reader in FULL_READERS:
        return "full"
    if reader == ELSEWHERE_READER:
        return "elsewhere"
    did = delegate_id(reader)
    if did is not None:
        slug = _SAFE_SLUG_RE.sub("_", did).strip("_")[:32] or "id"
        digest = hashlib.sha256(did.encode("utf-8")).hexdigest()[:8]
        return f"delegate_{slug}_{digest}"
    raise PermissionError(f"Reader {reader!r} is not permitted to open a KB snapshot")


def vault_fingerprint(
    vault: Path,
    policy: Policy,
    *,
    kb_root: str | None = None,
    schema: str | None = None,
    include_body: bool = True,
) -> str:
    """Fast stat-keyed digest of the vault's visible/plumbing-filtered files and policy."""
    h = hashlib.sha256()
    header = json.dumps(
        {
            "snapshot_schema": SNAPSHOT_SCHEMA_VERSION,
            "classifier": CLASSIFIER_VERSION,
            "engine": __version__,
            "policy": policy_hash(policy),
            "kb_root": str(Path(kb_root).resolve()) if kb_root else "",
            "schema": str(Path(schema).resolve()) if schema else "",
            "include_body": include_body,
        },
        sort_keys=True,
    ).encode("utf-8")
    h.update(header)

    entries = sorted(_walk(vault, _Scope(policy, ())), key=lambda item: item[0])
    for rel, st in entries:
        h.update(f"\n{rel}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_size}:{st.st_ino}".encode())

    for extra in (kb_root, schema):
        if extra:
            p = Path(extra)
            if p.is_file():
                st = p.stat()
                h.update(f"\nextra:{p.resolve()}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()


def _prepare_node_rows(pg: PropertyGraph) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in pg.nodes.values():
        props: dict[str, Any] = {}
        for k, v in node["props"].items():
            if v is not None and isinstance(v, (str, int, float, bool)):
                props[k] = v
        lbl = node.get("_label")
        if lbl is not None:
            props["_label_json"] = json.dumps(lbl, sort_keys=True, default=str)
            if isinstance(lbl, Mapping) and isinstance(lbl.get("visibility"), str):
                props.setdefault("visibility", lbl["visibility"])
        ent = node.get("_entity")
        if ent is not None:
            try:
                props["_entity_json"] = json.dumps(_enc(ent), sort_keys=True)
            except (_NotJSON, TypeError, ValueError):
                props["_entity_json"] = json.dumps(ent, sort_keys=True, default=str)
        rows.append(
            {
                "key": node["key"],
                "labels": list(node["labels"]),
                "props": props,
            }
        )
    return rows


def _read_pointer(pointer_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("snapshot_schema") != SNAPSHOT_SCHEMA_VERSION:
        return None
    return data


def _prune_old_snapshots(cache_dir: Path, slot: str, keep_filename: str) -> None:
    prefix = f"{slot}-"
    for entry in cache_dir.iterdir():
        if not entry.name.startswith(prefix) or not entry.name.endswith(".rl"):
            continue
        if entry.name == keep_filename:
            continue
        with contextlib.suppress(OSError):
            entry.unlink()


def _decode_json_field(raw: Any, *, lossless: bool = False) -> Any:
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return _dec(parsed) if lossless else parsed
        except (ValueError, TypeError, KeyError):
            return None
    return None


class RelateBackend:
    """Derived read-only :class:`~scout.kb.backend.KBBackend` backed by RelateDB."""

    def __init__(
        self,
        vault: Path,
        reader: str,
        *,
        kb_root: str | None = None,
        schema: str | None = None,
        include_body: bool = True,
        driver: Any | None = None,
    ) -> None:
        self._vault = vault.resolve()
        self._reader = reader
        self._kb_root = kb_root
        self._schema = schema
        self._include_body = include_body
        self._driver = driver

    @property
    def kind(self) -> str:
        return "relate"

    @property
    def vault(self) -> Path:
        return self._vault

    @property
    def reader(self) -> str:
        return self._reader

    def capabilities(self) -> Capabilities:
        return RELATE_CAPABILITIES

    def _relate(self) -> Any:
        if self._driver is not None:
            ensure_telemetry_disabled()
            return self._driver
        self._driver = _import_relate()
        return self._driver

    def _cache_dir(self) -> Path:
        cdir = self._vault / RELATE_CACHE_DIR
        cdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            cdir.chmod(0o700)
        return cdir

    def _ensure_snapshot(self, *, force: bool = False) -> tuple[Path, dict[str, Any]]:
        """Return ``(store_path, pointer_meta)``, rebuilding atomically when missing or stale."""
        relate_mod = self._relate()
        # Delegate --kb-root / --schema refusal runs through load_graph before touching files
        slot = reader_slot(self._reader)
        policy = load_policy(self._vault)
        fp = vault_fingerprint(
            self._vault,
            policy,
            kb_root=self._kb_root,
            schema=self._schema,
            include_body=self._include_body,
        )
        cdir = self._cache_dir()
        pointer_path = cdir / f"{slot}-current.json"

        if not force:
            ptr = _read_pointer(pointer_path)
            if ptr is not None and ptr.get("fingerprint") == fp:
                store_name = ptr.get("store_file")
                if isinstance(store_name, str) and (cdir / store_name).is_file():
                    return cdir / store_name, ptr

        ont_graph = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        pg = build_property_graph(
            self._vault,
            self._reader,
            graph=ont_graph,
            kb_root=self._kb_root,
            schema=self._schema,
            include_body=self._include_body,
            body_scope="knowledge-base/",
        )
        node_rows = _prepare_node_rows(pg)
        edge_rows = pg.edges

        target_name = f"{slot}-{fp[:16]}.rl"
        target_path = cdir / target_name
        tmp_path = cdir / f".tmp-{slot}-{os.getpid()}-{time.time_ns()}.rl"

        try:
            conn = relate_mod.connect(str(tmp_path))
            try:
                for ddl in SCHEMA_DDL:
                    conn.execute(ddl)
                conn.autocommit = False
                for i in range(0, len(node_rows), LOAD_BATCH):
                    conn.execute(LOAD_NODES_CYPHER, {"rows": node_rows[i : i + LOAD_BATCH]})
                conn.commit()
                for i in range(0, len(edge_rows), LOAD_BATCH):
                    conn.execute(LOAD_EDGES_CYPHER, {"rows": edge_rows[i : i + LOAD_BATCH]})
                conn.commit()
            finally:
                conn.close()

            with contextlib.suppress(OSError):
                tmp_path.chmod(0o600)
            os.replace(tmp_path, target_path)
        finally:
            if tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

        pointer_meta: dict[str, Any] = {
            "snapshot_schema": SNAPSHOT_SCHEMA_VERSION,
            "slot": slot,
            "reader": self._reader,
            "fingerprint": fp,
            "sha": pg.meta["sha"],
            "store_file": target_name,
            "notes": pg.meta["notes"],
            "nodes": len(node_rows),
            "edges": len(edge_rows),
            "entities": len(ont_graph.entities),
            "relationships": len(ont_graph.relationships),
            "classifier": CLASSIFIER_VERSION,
            "engine": __version__,
        }
        safe_write.atomic_write_text(pointer_path, json.dumps(pointer_meta, indent=2), mode=0o600)
        _prune_old_snapshots(cdir, slot, target_name)
        return target_path, pointer_meta

    def _query_ro(self, cypher: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        relate_mod = self._relate()
        store_path, _ = self._ensure_snapshot(force=False)
        conn = relate_mod.connect(str(store_path), read_only=True)
        try:
            return list(conn.execute(cypher, dict(params or {})).data())
        finally:
            conn.close()

    def build(self, *, snapshot_sha: str | None = None) -> dict[str, Any]:
        """Force an atomic rebuild-and-swap of this reader's RelateDB snapshot."""
        if not reads_any(self._reader):
            return {
                "kind": self.kind,
                "reader": self._reader,
                "requested_sha": snapshot_sha,
                "sha": "",
                "store_path": "",
                "notes": 0,
                "entities": 0,
                "relationships": 0,
            }
        store_path, meta = self._ensure_snapshot(force=True)
        return {
            "kind": self.kind,
            "reader": self._reader,
            "requested_sha": snapshot_sha,
            "sha": meta["sha"],
            "store_path": str(store_path),
            "notes": meta["notes"],
            "entities": meta["entities"],
            "relationships": meta["relationships"],
        }

    def entity(self, name: str) -> dict[str, Any] | None:
        if not reads_any(self._reader):
            return None
        rows = self._query_ro(
            "MATCH (e:Entity {name: $name}) RETURN e._entity_json AS j LIMIT 1",
            {"name": name},
        )
        if not rows:
            return None
        ent = _decode_json_field(rows[0].get("j"), lossless=True)
        return ent if isinstance(ent, dict) else None

    def entities(self, *, visibility: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        active_filters = {k: v for k, v in filters.items() if v}
        etype = active_filters.get("type")
        params: dict[str, Any] = {
            "etype": str(etype) if isinstance(etype, str) else None,
            "vis": visibility,
        }
        rows = self._query_ro(
            "MATCH (e:Entity) "
            "WHERE ($etype IS NULL OR e.entity_type = $etype) "
            "  AND ($vis IS NULL OR e.visibility = $vis) "
            "RETURN e._entity_json AS j ORDER BY e.name ASC",
            params,
        )
        # Apply KnowledgeGraph's exact filter matcher for date/birthday/exclude_status parity
        helper = KnowledgeGraph.__new__(KnowledgeGraph)
        results: list[dict[str, Any]] = []
        for r in rows:
            ent = _decode_json_field(r.get("j"), lossless=True)
            if not isinstance(ent, dict):
                continue
            if visibility is not None and (ent.get("_label") or {}).get("visibility") != visibility:
                continue
            if helper._matches_filters(ent, active_filters):
                results.append(ent)
        return results

    def related(self, name: str) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        rows = self._query_ro(
            "MATCH (s:Entity {name: $name})-[r]->(t) "
            "WHERE r.origin = 'frontmatter' "
            "RETURN r.ontology_type AS type, coalesce(t.name, t.target) AS target, t._label_json AS lj",
            {"name": name},
        )
        return [
            {
                "source": name,
                "type": r["type"],
                "target": r["target"],
                "label": _decode_json_field(r.get("lj")),
            }
            for r in rows
        ]

    def neighbors(
        self,
        names: str | Sequence[str],
        *,
        depth: int = 2,
        rel_types: Collection[str] | None = None,
        node_types: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if depth <= 0 or (limit is not None and limit <= 0) or not reads_any(self._reader):
            return []
        seeds = [names] if isinstance(names, str) else [s for s in names if s]
        if not seeds:
            return []
        bounded_depth = min(int(depth), MAX_CYPHER_HOPS)
        rels_list = sorted(rel_types) if rel_types else None
        ntypes_list = sorted({t.lower() for t in node_types}) if node_types else None

        cypher = (
            "UNWIND $seeds AS seed "
            "MATCH (s:Entity {name: seed})"
            "(()-[r WHERE r.origin = 'frontmatter' AND ($rels IS NULL OR r.ontology_type IN $rels)]->())"
            f"{{1,{bounded_depth}}}(t) "
            "WHERE NOT coalesce(t.name, t.target) IN $seeds "
            "  AND ($ntypes IS NULL OR toLower(coalesce(t.entity_type, '')) IN $ntypes) "
            "WITH DISTINCT t "
            "MATCH (s0:Entity) WHERE s0.name IN $seeds "
            f"MATCH p = shortestPath((s0)-[rs*..{bounded_depth}]->(t)) "
            "WHERE all(r IN rs WHERE r.origin = 'frontmatter' AND ($rels IS NULL OR r.ontology_type IN $rels)) "
            "WITH t, p ORDER BY length(p) ASC "
            "WITH t, head(collect(p)) AS best "
            "RETURN coalesce(t.name, t.target) AS entity, "
            "       length(best) AS hops, "
            "       [r IN relationships(best) | r.ontology_type] AS rtypes, "
            "       [n IN nodes(best)[1..] | coalesce(n.name, n.target)] AS nnames, "
            "       t._label_json AS lj "
            "ORDER BY hops ASC, entity ASC"
        )
        rows = self._query_ro(cypher, {"seeds": seeds, "rels": rels_list, "ntypes": ntypes_list})
        out: list[dict[str, Any]] = []
        for r in rows:
            rtypes = list(r.get("rtypes") or [])
            nnames = list(r.get("nnames") or [])
            steps = list(zip(rtypes, nnames, strict=True))
            out.append(
                {
                    "entity": r["entity"],
                    "hops": int(r["hops"]),
                    "via": rtypes[-1] if rtypes else "",
                    "path": steps,
                    "_label": _decode_json_field(r.get("lj")),
                }
            )
            if limit is not None and len(out) >= limit:
                break
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
        if k <= 0 or max_hops < 0 or not reads_any(self._reader):
            return []
        present = {
            r["n"]
            for r in self._query_ro(
                "MATCH (e:Entity) WHERE e.name IN $endpoints RETURN e.name AS n",
                {"endpoints": [source, target]},
            )
        }
        if source not in present or target not in present:
            return []
        if source == target:
            return [[]]
        if max_hops == 0:
            return []

        bounded_hops = min(int(max_hops), MAX_CYPHER_HOPS)
        rels_list = sorted(rel_types) if rel_types else None
        fn = "shortestPath" if k == 1 else "allShortestPaths"
        cypher = (
            "MATCH (a:Entity {name: $a}), (b:Entity {name: $b}) "
            f"MATCH p = {fn}((a)-[rs*..{bounded_hops}]->(b)) "
            "WHERE all(r IN rs WHERE r.origin = 'frontmatter' AND ($rels IS NULL OR r.ontology_type IN $rels)) "
            "RETURN [r IN relationships(p) | r.ontology_type] AS rtypes, "
            "       [n IN nodes(p)[1..] | coalesce(n.name, n.target)] AS nnames"
        )
        rows = self._query_ro(cypher, {"a": source, "b": target, "rels": rels_list})
        result: list[list[tuple[str, str]]] = []
        for r in rows[:k]:
            rtypes = list(r.get("rtypes") or [])
            nnames = list(r.get("nnames") or [])
            result.append(list(zip(rtypes, nnames, strict=True)))
        return result

    def backlinks(self, path: str) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        try:
            rel = normalize_rel(self._vault, path)
        except ValueError:
            return []
        rows = self._query_ro(
            "MATCH (s:Note)-[l:LINKS_TO]->(:Note {key: $rel}) "
            "RETURN s.path AS source, l.count AS count, l.lines AS lines, "
            "       l.sections AS sections, l.ambiguous AS ambiguous, s._label_json AS lj "
            "ORDER BY source ASC",
            {"rel": rel},
        )
        return [
            {
                "source": r["source"],
                "count": int(r.get("count") or 1),
                "lines": list(r.get("lines") or []),
                "sections": list(r.get("sections") or []),
                "ambiguous": bool(r.get("ambiguous", False)),
                "label": _decode_json_field(r.get("lj")),
            }
            for r in rows
        ]

    def link_gaps(self, *, scope: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        if limit <= 0 or not reads_any(self._reader):
            return []
        rows = self._query_ro(
            "MATCH (a:Entity)-[l:LINKS_TO]->(b:Entity) "
            "WHERE a <> b "
            "  AND NOT EXISTS { MATCH (a)-[r]-(b) WHERE r.origin = 'frontmatter' } "
            "RETURN a.name AS source, b.name AS target, "
            "       a.path AS source_path, b.path AS target_path, "
            "       l.count AS count, l.lines AS lines, "
            "       a._label_json AS slj, b._label_json AS tlj "
            "ORDER BY count DESC, source ASC, target ASC"
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            src_rel, dst_rel = r["source_path"], r["target_path"]
            if scope and not (_matches_scope(src_rel, scope) or _matches_scope(dst_rel, scope)):
                continue
            out.append(
                {
                    "source": r["source"],
                    "target": r["target"],
                    "source_path": src_rel,
                    "target_path": dst_rel,
                    "count": int(r.get("count") or 1),
                    "lines": list(r.get("lines") or []),
                    "source_label": _decode_json_field(r.get("slj")),
                    "target_label": _decode_json_field(r.get("tlj")),
                }
            )
            if len(out) >= limit:
                break
        return out

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

    def ranked_search(
        self,
        query_text: str,
        *,
        project: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text ranked search over KB notes, optionally scoped to a project's neighbourhood."""
        if limit <= 0 or not query_text.strip() or not reads_any(self._reader):
            return []
        if project:
            rows = self._query_ro(
                "MATCH (proj:Project {name: $project})-[:LINKS_TO|ABOUT]-(n:Note) "
                "SEARCH n IN (FULLTEXT INDEX note_text FOR $q LIMIT $k) SCORE AS score "
                "RETURN DISTINCT n.path AS path, n.layer AS layer, score, n._label_json AS lj "
                "ORDER BY score DESC, path ASC",
                {"project": project, "q": query_text, "k": int(limit)},
            )
        else:
            rows = self._query_ro(
                "CALL db.index.fulltext.queryNodes('note_text', $q) YIELD node, score "
                "RETURN node.path AS path, node.layer AS layer, score, node._label_json AS lj "
                "ORDER BY score DESC, path ASC LIMIT $k",
                {"q": query_text, "k": int(limit)},
            )
        return [
            {
                "path": r["path"],
                "layer": r.get("layer", ""),
                "score": float(r.get("score") or 0.0),
                "label": _decode_json_field(r.get("lj")),
            }
            for r in rows
        ]

    def resolve(self, name: str) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        g = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        return resolve(self._vault, self._reader, g, name)

    def name_lookup(
        self,
        token: str,
        *,
        max_results: int = 5,
        distance_threshold: float = 0.7,
        types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not reads_any(self._reader):
            return []
        g = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        return g.name_lookup(
            token,
            max_results=max_results,
            distance_threshold=distance_threshold,
            types=types,
        )

    def stats(self) -> dict[str, Any]:
        if not reads_any(self._reader):
            return {"entities": 0, "relationships": 0, "by_type": {}, "by_rel_type": {}}
        ent_rows = self._query_ro("MATCH (e:Entity) RETURN coalesce(e.entity_type, 'unknown') AS k, count(*) AS c")
        rel_rows = self._query_ro(
            "MATCH ()-[r]->() WHERE r.origin = 'frontmatter' RETURN r.ontology_type AS k, count(*) AS c"
        )
        by_type = {str(r["k"]): int(r["c"]) for r in sorted(ent_rows, key=lambda x: str(x["k"]))}
        by_rel = {str(r["k"]): int(r["c"]) for r in sorted(rel_rows, key=lambda x: str(x["k"]))}
        return {
            "entities": sum(by_type.values()),
            "relationships": sum(by_rel.values()),
            "by_type": by_type,
            "by_rel_type": by_rel,
        }

    def validate(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        g = load_graph(self._vault, self._reader, self._kb_root, self._schema)
        errors = g.validate()
        return errors, list(g.known_gaps)

    def raw_cypher(self, query: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Read-only Cypher escape hatch strictly for the vault owner's interactive sessions."""
        if self._reader != "owner":
            raise PermissionError("raw_cypher is restricted to reader='owner' interactive sessions")
        return self._query_ro(query, params)

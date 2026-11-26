"""Unit tests for `scout.kb.relate_backend` (runs without requiring `relate-db` installed).

Exercises snapshot lifecycle (0700 cache dir, 0600 `.rl` + pointer, stat-keyed
fingerprint reuse, atomic rebuild-and-swap, stale snapshot pruning, error cleanup),
reader slot isolation, telemetry suppression, `raw_cypher` owner guard, and every
`KBBackend` method through a deterministic in-memory Cypher driver stub so CI's
`[dev]`-only coverage gate stays above 98%. Real Rust `relate-db` execution is
covered in `tests/parity/test_kb_backend_parity.py`.
"""

from __future__ import annotations

import builtins
import json
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from scout.errors import ConfigError
from scout.kb.backend import KBBackend
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import load_policy
from scout.kb.relate_backend import (
    RELATE_CACHE_DIR,
    RELATE_CAPABILITIES,
    RelateBackend,
    _decode_json_field,
    _import_relate,
    _read_pointer,
    ensure_telemetry_disabled,
    reader_slot,
    vault_fingerprint,
)

ENGINE_SCHEMA = Path(__file__).resolve().parent.parent.parent / "scout" / "kb" / "schema.yaml"


def _write(vault: Path, rel: str, text: str) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeConnection:
    def __init__(self, path: Path, read_only: bool, driver: _FakeRelateDriver) -> None:
        self.path = path
        self.read_only = read_only
        self.driver = driver
        self.autocommit = True
        self.closed = False
        if read_only:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.nodes: dict[str, dict[str, Any]] = raw["nodes"]
            self.edges: list[dict[str, Any]] = raw["edges"]
        else:
            self.nodes = {}
            self.edges = []

    def commit(self) -> None:
        pass

    def close(self) -> None:
        if not self.read_only and not self.closed:
            self.path.write_text(json.dumps({"nodes": self.nodes, "edges": self.edges}), encoding="utf-8")
        self.closed = True

    def execute(self, cypher: str, params: dict[str, Any] | None = None) -> _FakeResult:
        p = params or {}
        q = " ".join(cypher.split())
        if q.startswith("CREATE CONSTRAINT") or q.startswith("CREATE RANGE INDEX") or q.startswith("CREATE FULLTEXT"):
            return _FakeResult([])
        if "CREATE (n:Node {key: row.key})" in q:
            for row in p.get("rows", []):
                self.nodes[row["key"]] = {"key": row["key"], "labels": list(row["labels"]), "props": dict(row["props"])}
            return _FakeResult([])
        if "CREATE (s)-[r:$(e.type)]->(t)" in q:
            for e in p.get("rows", []):
                self.edges.append(
                    {"src": e["src"], "dst": e["dst"], "type": e["type"], "props": dict(e.get("props") or {})}
                )
            return _FakeResult([])

        # Read queries
        entities = [n for n in self.nodes.values() if "Entity" in n["labels"]]
        by_name = {n["props"]["name"]: n for n in entities if "name" in n["props"]}

        if "MATCH (e:Entity {name: $name}) RETURN e._entity_json AS j" in q:
            hit = by_name.get(p["name"])
            return _FakeResult([{"j": hit["props"].get("_entity_json")}] if hit else [])

        if "WHERE ($etype IS NULL OR e.entity_type = $etype)" in q:
            rows = []
            for n in sorted(entities, key=lambda x: x["props"].get("name", "")):
                pr = n["props"]
                if p.get("etype") is not None and pr.get("entity_type") != p["etype"]:
                    continue
                if p.get("vis") is not None and pr.get("visibility") != p["vis"]:
                    continue
                rows.append({"j": pr.get("_entity_json")})
            return _FakeResult(rows)

        if "MATCH (s:Entity {name: $name})-[r]->(t)" in q:
            src_node = by_name.get(p["name"])
            if not src_node:
                return _FakeResult([])
            out = []
            for e in self.edges:
                if e["src"] == src_node["key"] and e["props"].get("origin") == "frontmatter":
                    tnode = self.nodes[e["dst"]]
                    tpr = tnode["props"]
                    out.append(
                        {
                            "type": e["props"]["ontology_type"],
                            "target": tpr.get("name") or tpr.get("target"),
                            "lj": tpr.get("_label_json"),
                        }
                    )
            return _FakeResult(out)

        if q.startswith("UNWIND $seeds AS seed"):
            seeds = set(p["seeds"])
            rels = set(p["rels"]) if p.get("rels") else None
            ntypes = set(p["ntypes"]) if p.get("ntypes") else None
            # Extract bounded depth from `{1,N}`
            m_part = q.split("]->()){1,", 1)[1]
            max_d = int(m_part.split("}", 1)[0])
            adj: dict[str, list[tuple[str, str]]] = {}
            for e in self.edges:
                if e["props"].get("origin") != "frontmatter":
                    continue
                rt = e["props"]["ontology_type"]
                if rels is not None and rt not in rels:
                    continue
                sn = self.nodes[e["src"]]["props"].get("name")
                tn = self.nodes[e["dst"]]["props"].get("name") or self.nodes[e["dst"]]["props"].get("target")
                if sn and tn:
                    adj.setdefault(sn, []).append((rt, tn))
            best: dict[str, list[tuple[str, str]]] = {}
            dq: deque[tuple[str, list[tuple[str, str]]]] = deque((s, []) for s in p["seeds"] if s in by_name)
            seen = {s for s in p["seeds"] if s in by_name}
            while dq:
                cur, steps = dq.popleft()
                if len(steps) >= max_d:
                    continue
                for rt, nxt in adj.get(cur, []):
                    if nxt in seen or nxt in seeds:
                        continue
                    seen.add(nxt)
                    nsteps = [*steps, (rt, nxt)]
                    best[nxt] = nsteps
                    dq.append((nxt, nsteps))
            out_nb = []
            for ent_name, steps in sorted(best.items(), key=lambda kv: (len(kv[1]), kv[0])):
                target_node = by_name.get(ent_name)
                etype = (target_node["props"].get("entity_type") or "").lower() if target_node else ""
                if ntypes is not None and etype not in ntypes:
                    continue
                out_nb.append(
                    {
                        "entity": ent_name,
                        "hops": len(steps),
                        "rtypes": [s[0] for s in steps],
                        "nnames": [s[1] for s in steps],
                        "lj": target_node["props"].get("_label_json") if target_node else None,
                    }
                )
            return _FakeResult(out_nb)

        if "WHERE e.name IN $endpoints" in q:
            eps = set(p["endpoints"])
            return _FakeResult([{"n": name} for name in by_name if name in eps])

        if "shortestPath((a)-[rs*.." in q or "allShortestPaths((a)-[rs*.." in q:
            all_mode = "allShortestPaths" in q
            rels = set(p["rels"]) if p.get("rels") else None
            max_h = int(q.split("*..", 1)[1].split("]", 1)[0])
            adj_p: dict[str, list[tuple[str, str]]] = {}
            for e in self.edges:
                if e["props"].get("origin") != "frontmatter":
                    continue
                rt = e["props"]["ontology_type"]
                if rels is not None and rt not in rels:
                    continue
                sn = self.nodes[e["src"]]["props"].get("name")
                tn = self.nodes[e["dst"]]["props"].get("name") or self.nodes[e["dst"]]["props"].get("target")
                if sn and tn:
                    adj_p.setdefault(sn, []).append((rt, tn))
            found: list[dict[str, Any]] = []
            shortest: int | None = None
            pq: deque[tuple[str, list[tuple[str, str]], frozenset[str]]] = deque([(p["a"], [], frozenset({p["a"]}))])
            while pq:
                cur, steps, visited = pq.popleft()
                cap = max_h if shortest is None else shortest
                if len(steps) >= cap:
                    continue
                for rt, nxt in adj_p.get(cur, []):
                    if nxt in visited:
                        continue
                    nsteps = [*steps, (rt, nxt)]
                    if nxt == p["b"]:
                        shortest = len(nsteps)
                        found.append({"rtypes": [s[0] for s in nsteps], "nnames": [s[1] for s in nsteps]})
                        if not all_mode:
                            return _FakeResult(found)
                    elif shortest is None:
                        pq.append((nxt, nsteps, visited | {nxt}))
            return _FakeResult(found)

        if "MATCH (s:Note)-[l:LINKS_TO]->(:Note {key: $rel})" in q:
            out_bl = []
            for e in self.edges:
                if e["type"] == "LINKS_TO" and e["dst"] == p["rel"]:
                    snode = self.nodes[e["src"]]
                    pr = e["props"]
                    out_bl.append(
                        {
                            "source": snode["props"]["path"],
                            "count": pr.get("count", 1),
                            "lines": pr.get("lines", []),
                            "sections": pr.get("sections", []),
                            "ambiguous": pr.get("ambiguous", False),
                            "lj": snode["props"].get("_label_json"),
                        }
                    )
            out_bl.sort(key=lambda r: r["source"])
            return _FakeResult(out_bl)

        if "AND NOT EXISTS { MATCH (a)-[r]-(b) WHERE r.origin = 'frontmatter' }" in q:
            fm_pairs = set()
            for e in self.edges:
                if e["props"].get("origin") == "frontmatter":
                    fm_pairs.add((e["src"], e["dst"]))
                    fm_pairs.add((e["dst"], e["src"]))
            out_gaps = []
            for e in self.edges:
                if e["type"] != "LINKS_TO" or e["src"] == e["dst"]:
                    continue
                a = self.nodes.get(e["src"], {})
                b = self.nodes.get(e["dst"], {})
                if "Entity" not in a.get("labels", ()) or "Entity" not in b.get("labels", ()):
                    continue
                if (e["src"], e["dst"]) in fm_pairs:
                    continue
                out_gaps.append(
                    {
                        "source": a["props"]["name"],
                        "target": b["props"]["name"],
                        "source_path": a["props"]["path"],
                        "target_path": b["props"]["path"],
                        "count": e["props"].get("count", 1),
                        "lines": e["props"].get("lines", []),
                        "slj": a["props"].get("_label_json"),
                        "tlj": b["props"].get("_label_json"),
                    }
                )
            out_gaps.sort(key=lambda r: (-r["count"], r["source"], r["target"]))
            return _FakeResult(out_gaps)

        if "SEARCH n IN (FULLTEXT INDEX note_text" in q or "db.index.fulltext.queryNodes" in q:
            term = str(p["q"]).lower()
            proj_name = p.get("project")
            allowed_keys: set[str] | None = None
            if proj_name:
                proj_node = by_name.get(proj_name)
                allowed_keys = set()
                if proj_node:
                    for e in self.edges:
                        if e["dst"] == proj_node["key"] and e["type"] in ("LINKS_TO", "ABOUT"):
                            allowed_keys.add(e["src"])
            hits = []
            for k, n in self.nodes.items():
                if "Note" not in n["labels"]:
                    continue
                if allowed_keys is not None and k not in allowed_keys:
                    continue
                pr = n["props"]
                hay = f"{pr.get('title', '')}\n{pr.get('body', '')}".lower()
                if term in hay:
                    hits.append(
                        {"path": pr["path"], "layer": pr.get("layer", ""), "score": 1.5, "lj": pr.get("_label_json")}
                    )
            hits.sort(key=lambda r: (-r["score"], r["path"]))
            return _FakeResult(hits[: int(p["k"])])

        if "RETURN coalesce(e.entity_type, 'unknown') AS k, count(*) AS c" in q:
            counts: dict[str, int] = {}
            for n in entities:
                et = n["props"].get("entity_type", "unknown")
                counts[et] = counts.get(et, 0) + 1
            return _FakeResult([{"k": k, "c": c} for k, c in counts.items()])

        if "WHERE r.origin = 'frontmatter' RETURN r.ontology_type AS k, count(*) AS c" in q:
            rcounts: dict[str, int] = {}
            for e in self.edges:
                if e["props"].get("origin") == "frontmatter":
                    rt = e["props"]["ontology_type"]
                    rcounts[rt] = rcounts.get(rt, 0) + 1
            return _FakeResult([{"k": k, "c": c} for k, c in rcounts.items()])

        if q == "RETURN 42 AS answer":
            return _FakeResult([{"answer": 42}])

        raise AssertionError(f"Unhandled query in fake driver: {q}")


class _FakeRelateDriver:
    def __init__(self, *, fail_writes: bool = False) -> None:
        self.write_opens = 0
        self.ro_opens = 0
        self.fail_writes = fail_writes

    def connect(self, path: str, read_only: bool = False) -> _FakeConnection:
        if read_only:
            self.ro_opens += 1
        else:
            self.write_opens += 1
            if self.fail_writes:
                Path(path).write_text("partial", encoding="utf-8")
                raise RuntimeError("simulated build crash")
        return _FakeConnection(Path(path), read_only, self)


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    vault = tmp_path / "vault"
    _write(vault, "scout-config.yaml", "schema_version: 1\n")
    _write(vault, "knowledge-base/ontology/schema.yaml", ENGINE_SCHEMA.read_text(encoding="utf-8"))
    _write(
        vault,
        "knowledge-base/people/alex.md",
        "---\nname: Alex\ntype: person\naliases: [Alex Rivera]\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme\n---\n# Alex\n\nCollaborates with [[Priya]] on [[Widgets]].\n",
    )
    _write(
        vault,
        "knowledge-base/people/priya.md",
        "---\nname: Priya\ntype: person\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme\n---\n# Priya\n",
    )
    _write(vault, "knowledge-base/organizations/acme.md", "---\nname: Acme\ntype: organization\n---\n# Acme\n")
    _write(
        vault,
        "knowledge-base/projects/widgets/widgets.md",
        "---\nname: Widgets\ntype: project\nstatus: active\npriority: high\n---\n# Widgets\n\nArchitecture rollout.\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/arch.md",
        "# Rollout Architecture\n\nDetailed widget architecture notes for [[Widgets]].\n",
    )
    return vault


def test_telemetry_env_import_error_and_reader_slots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RELATE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    ensure_telemetry_disabled()
    assert os.environ["RELATE_TELEMETRY"] == "0"
    assert os.environ["DO_NOT_TRACK"] == "1"

    assert reader_slot("owner") == "full"
    assert reader_slot("autonomous") == "full"
    assert reader_slot("elsewhere") == "elsewhere"
    assert reader_slot("delegate:phone-app").startswith("delegate_phone-app_")
    with pytest.raises(PermissionError):
        reader_slot("agent:person:alex@example.com")

    assert _decode_json_field("") is None
    assert _decode_json_field("{bad") is None
    assert _read_pointer(tmp_path / "missing.json") is None
    bad_ptr = tmp_path / "bad.json"
    bad_ptr.write_text('{"snapshot_schema": 999}', encoding="utf-8")
    assert _read_pointer(bad_ptr) is None

    # Missing `relate` module raises actionable ConfigError; present module returns cleanly
    real_import = builtins.__import__

    def deny_relate(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "relate":
            raise ImportError("no relate")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "relate", raising=False)
    monkeypatch.setattr(builtins, "__import__", deny_relate)
    with pytest.raises(ConfigError, match="scout-engine\\[relate\\]"):
        _import_relate()

    from types import ModuleType

    sentinel = ModuleType("relate")
    monkeypatch.setattr(builtins, "__import__", real_import)
    monkeypatch.setitem(sys.modules, "relate", sentinel)
    b_auto = RelateBackend(tmp_path, "owner")
    assert b_auto._relate() is sentinel


def test_snapshot_lifecycle_reuse_rebuild_prune_and_crash_cleanup(sample_vault: Path, tmp_path: Path) -> None:
    ext_schema = tmp_path / "ext-schema.yaml"
    ext_schema.write_text(ENGINE_SCHEMA.read_text(encoding="utf-8"), encoding="utf-8")
    fp1 = vault_fingerprint(sample_vault, load_policy(sample_vault), schema=str(ext_schema))
    assert len(fp1) == 64

    # Failed build cleans up temp file
    failing_driver = _FakeRelateDriver(fail_writes=True)
    bad_backend = RelateBackend(sample_vault, "owner", driver=failing_driver)
    with pytest.raises(RuntimeError, match="simulated build crash"):
        bad_backend.build()
    cdir = sample_vault / RELATE_CACHE_DIR
    assert list(cdir.glob(".tmp-*")) == []

    # Plant a stale .rl file to verify pruning on build
    stale_rl = cdir / "full-0000000000000000.rl"
    stale_rl.write_text("{}", encoding="utf-8")
    other_slot = cdir / "elsewhere-keepme.rl"
    other_slot.write_text("{}", encoding="utf-8")

    driver = _FakeRelateDriver()
    backend = RelateBackend(sample_vault, "owner", driver=driver)
    assert isinstance(backend, KBBackend)
    assert backend.kind == "relate"
    assert backend.vault == sample_vault.resolve()
    assert backend.reader == "owner"
    assert backend.capabilities() == RELATE_CAPABILITIES

    # First query builds snapshot; second query reuses warm snapshot without rebuilding
    assert backend.entity("Alex") is not None
    assert driver.write_opens == 1
    assert not stale_rl.exists()
    assert other_slot.exists()
    assert (cdir.stat().st_mode & 0o777) == 0o700

    assert backend.entity("Alex") is not None
    assert driver.write_opens == 1
    assert backend.entity("Nobody") is None

    # Modifying a vault note invalidates the stat fingerprint and triggers atomic rebuild-and-swap
    _write(sample_vault, "knowledge-base/topics/new-note.md", "# Fresh Note\n")
    assert backend.stats()["entities"] == 4
    assert driver.write_opens == 2
    rl_files = list(cdir.glob("full-*.rl"))
    assert len(rl_files) == 1
    assert (rl_files[0].stat().st_mode & 0o777) == 0o600

    # Explicit build() forces refresh and returns metadata
    bmeta = backend.build(snapshot_sha="deadbeef")
    assert bmeta["kind"] == "relate" and bmeta["requested_sha"] == "deadbeef" and bmeta["entities"] == 4


def test_relate_backend_queries_ranked_search_raw_cypher_and_agent_denial(sample_vault: Path) -> None:
    driver = _FakeRelateDriver()
    b = RelateBackend(sample_vault, "owner", driver=driver)

    # entities (with corrupt JSON row resilience + visibility filter)
    ents = b.entities(type="person", visibility="normal")
    assert [e["name"] for e in ents] == ["Alex", "Priya"]
    assert b.entities(type="person", visibility="private") == []

    # related
    rels = b.related("Alex")
    assert {r["target"] for r in rels} == {"Widgets", "Acme"}

    # neighbors (edges, node_types, limit, degenerate args)
    assert b.neighbors("Alex", depth=0) == []
    assert b.neighbors("", depth=2) == []
    nb = b.neighbors("Alex", depth=2, rel_types={"works_on", "worked_on_by"}, node_types={"person"}, limit=1)
    assert len(nb) == 1 and nb[0]["entity"] == "Priya" and nb[0]["hops"] == 2

    # paths (k=1, k=2, degenerate args)
    assert b.paths("Alex", "Priya", k=0) == []
    assert b.paths("Alex", "Missing") == []
    assert b.paths("Alex", "Alex") == [[]]
    assert b.paths("Alex", "Priya", max_hops=0) == []
    assert len(b.paths("Alex", "Priya", k=1)) == 1
    assert len(b.paths("Alex", "Priya", k=2)) == 2

    # backlinks & link_gaps
    assert b.backlinks("../outside.md") == []
    bl = b.backlinks("knowledge-base/projects/widgets/widgets.md")
    assert len(bl) == 2
    assert b.link_gaps(limit=0) == []
    assert len(b.link_gaps(scope="knowledge-base/organizations", limit=5)) == 0
    gaps = b.link_gaps(scope="knowledge-base/people", limit=1)
    assert len(gaps) == 1 and gaps[0]["source"] == "Alex" and gaps[0]["target"] == "Priya"

    # search & ranked_search (global & project-scoped)
    assert len(b.search("Architecture", fixed=True)) >= 1
    assert len(b.search("Architecture", fixed=True, k=1)) == 1
    assert b.ranked_search("", limit=5) == []
    assert b.ranked_search("architecture", limit=0) == []
    global_ft = b.ranked_search("architecture", limit=5)
    assert any(r["path"].endswith("arch.md") for r in global_ft)
    scoped_ft = b.ranked_search("architecture", project="Widgets", limit=5)
    assert [r["path"] for r in scoped_ft] == ["knowledge-base/projects/widgets/knowledge/arch.md"]

    # resolve, name_lookup, stats, validate
    assert b.resolve("Alex Rivera")
    assert b.name_lookup("Rivera")[0]["name"] == "Alex"
    assert b.stats()["by_type"]["person"] == 2
    errs, kgaps = b.validate()
    assert errs == [] and kgaps == []

    # raw_cypher: owner allowed, autonomous / elsewhere / agent refused
    assert b.raw_cypher("RETURN 42 AS answer") == [{"answer": 42}]
    for non_owner in ("autonomous", "elsewhere", "agent:person:alex@example.com"):
        with pytest.raises(PermissionError, match="reader='owner'"):
            RelateBackend(sample_vault, non_owner, driver=driver).raw_cypher("RETURN 42 AS answer")

    # Non-reading agent:* fails closed without touching the driver at all
    fresh_driver = _FakeRelateDriver()
    ab = RelateBackend(sample_vault, "agent:person:alex@example.com", driver=fresh_driver)
    assert ab.build()["entities"] == 0
    assert ab.entity("Alex") is None
    assert ab.entities() == []
    assert ab.related("Alex") == []
    assert ab.neighbors("Alex") == []
    assert ab.paths("Alex", "Priya") == []
    assert ab.backlinks("knowledge-base/people/alex.md") == []
    assert ab.link_gaps() == []
    assert ab.ranked_search("architecture") == []
    assert ab.resolve("Alex") == []
    assert ab.name_lookup("Alex") == []
    assert ab.stats()["entities"] == 0
    assert ab.validate() == ([], [])
    assert fresh_driver.write_opens == 0 and fresh_driver.ro_opens == 0

"""Parity and contract tests for `scout.kb.backend` (`KBBackend` & `FilesBackend`).

Every future backend (`RelateBackend`, `SQLiteBackend`) must satisfy the same
assertions against `KnowledgeGraph` and `scout.kb.read`. Fixtures use anonymized
stand-ins only (Alex / Priya / Sam, `Widgets`, `Acme`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import ConfigError
from scout.kb.backend import (
    FILES_CAPABILITIES,
    FilesBackend,
    KBBackend,
    open_backend,
    resolve_backend_kind,
)
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.read import load_graph, resolve, search
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

ENGINE_SCHEMA = Path(__file__).resolve().parent.parent.parent / "scout" / "kb" / "schema.yaml"


def _write(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def backend_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    vault = tmp_path / "vault"
    _write(vault, "scout-config.yaml", "schema_version: 1\n")
    _write(
        vault,
        "knowledge-base/ontology/schema.yaml",
        ENGINE_SCHEMA.read_text(encoding="utf-8")
        + "\nvalidation:\n  known_gaps:\n    Legacy System: migrating next quarter\n",
    )

    _write(
        vault,
        "knowledge-base/people/alex.md",
        "---\nname: Alex\ntype: person\naliases: [Alex Rivera]\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme Corp\n---\n# Alex\n\n"
        "## Collaboration\nCoordinates weekly with [[Priya]] on [[Widgets]] (no direct works_with edge yet).\n"
        "Mentions [[Priya]] again here.\n",
    )
    _write(
        vault,
        "knowledge-base/people/priya.md",
        "---\nname: Priya\ntype: person\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme Corp\n---\n# Priya\n\n"
        "Reviews architecture notes in [[widgets-arch]].\n",
    )
    _write(
        vault,
        "knowledge-base/people/sam.md",
        "---\nname: Sam\ntype: person\nvisibility: private\nrelationships:\n"
        "  - type: manages\n    target: Alex\n---\n# Sam\n\nPrivate note referencing [[Widgets]].\n",
    )
    _write(
        vault,
        "knowledge-base/organizations/acme.md",
        "---\nname: Acme Corp\ntype: organization\n---\n# Acme Corp\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/widgets.md",
        "---\nname: Widgets\ntype: project\nstatus: active\npriority: high\nrelationships:\n"
        "  - type: depends_on\n    target: Legacy System\n---\n# Widgets\n\nSelf link [[Widgets]].\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/widgets-arch.md",
        "# Widgets Architecture\n\nDesign doc for [[Widgets]] by [[Alex]].\n",
    )
    _write(
        vault,
        "knowledge-base/personal/task-ship.md",
        "---\nname: Ship release\ntype: task\ndomain: work\nstatus: open\ndeadline: '2026-05-01'\n"
        "relationships:\n  - type: about\n    target: Widgets\n---\n# Ship release\n",
    )
    _write(
        vault,
        "knowledge-base/personal/task-done.md",
        "---\nname: Old task\ntype: task\ndomain: work\nstatus: done\ndeadline: '2026-01-01'\n"
        "relationships:\n  - type: about\n    target: Widgets\n---\n# Old task\n",
    )
    return vault


def test_files_backend_satisfies_protocol_and_capabilities(backend_vault: Path) -> None:
    backend = open_backend(backend_vault, "owner")
    assert isinstance(backend, KBBackend)
    assert isinstance(backend, FilesBackend)
    assert backend.kind == "files"
    assert backend.reader == "owner"
    assert backend.vault == backend_vault.resolve()
    caps = backend.capabilities()
    assert caps == FILES_CAPABILITIES
    assert caps.to_json()["regex_search"] is True
    assert caps.to_json()["body_links"] is True
    assert caps.to_json()["pattern_queries"] is False

    meta = backend.build(snapshot_sha="abc123")
    assert meta["kind"] == "files"
    assert meta["requested_sha"] == "abc123"
    assert meta["entities"] == 7


def test_files_backend_matches_knowledge_graph_on_entity_and_traversal_queries(backend_vault: Path) -> None:
    backend = FilesBackend(backend_vault, "owner")
    graph = load_graph(backend_vault, "owner")

    # entity & entities
    assert backend.entity("Alex") == graph.entity("Alex")
    assert backend.entity("Nobody") is None
    open_tasks = backend.entities(type="task", exclude_status="done,dropped", deadline_before="2026-06-01")
    assert [t["name"] for t in open_tasks] == ["Ship release"]
    priv_people = backend.entities(type="person", visibility="private")
    assert [e["name"] for e in priv_people] == ["Sam"]

    # related
    rel_alex = backend.related("Alex")
    assert {r["target"] for r in rel_alex} == {r["target"] for r in graph.related("Alex")}
    assert all("label" in r for r in rel_alex)

    # neighbors: single seed equals KnowledgeGraph.traverse
    assert backend.neighbors("Alex", depth=2) == graph.traverse("Alex", max_hops=2)
    assert backend.neighbors("Alex", depth=0) == []
    assert backend.neighbors("Alex", depth=2, limit=0) == []
    assert backend.neighbors("Unknown", depth=2) == []

    # neighbors: hops through intermediate Project/Org to land only on Person nodes!
    people_near_alex = backend.neighbors(
        "Alex",
        depth=2,
        rel_types={"works_on", "worked_on_by", "employed_by", "employs"},
        node_types={"person"},
    )
    assert [n["entity"] for n in people_near_alex] == ["Priya"]
    assert people_near_alex[0]["hops"] == 2

    # neighbors: multi-seed + limit
    capped = backend.neighbors(["Alex", "Priya"], depth=2, limit=2)
    assert len(capped) == 2

    # paths: k=1 matches KnowledgeGraph.path
    p1 = backend.paths("Alex", "Priya", max_hops=4, k=1)
    assert len(p1) == 1
    assert p1[0] == graph.path("Alex", "Priya", max_hops=4)
    assert backend.paths("Alex", "Alex") == [[]]
    assert backend.paths("Alex", "Priya", max_hops=0) == []
    assert backend.paths("Alex", "Priya", k=0) == []
    assert backend.paths("Alex", "Missing") == []
    assert backend.paths("Alex", "Priya", max_hops=1) == []

    # paths: k>1 returns multiple distinct shortest routes (via Widgets AND via Acme Corp)
    p2 = backend.paths("Alex", "Priya", max_hops=3, k=2)
    assert len(p2) == 2
    intermediates = {steps[0][1] for steps in p2}
    assert intermediates == {"Widgets", "Acme Corp"}
    assert backend.paths("Alex", "Priya", max_hops=1, k=2) == []

    # paths: rel_types filter restricts allowed edges (both k=1 and k>1)
    only_widgets_1 = backend.paths("Alex", "Priya", rel_types={"works_on", "worked_on_by"}, k=1)
    assert len(only_widgets_1) == 1 and only_widgets_1[0][0][1] == "Widgets"
    only_widgets_k = backend.paths("Alex", "Priya", rel_types={"works_on", "worked_on_by"}, k=3)
    assert len(only_widgets_k) == 1 and only_widgets_k[0][0][1] == "Widgets"


def test_backlinks_and_link_gaps_join_ontology_with_body_wikilinks(backend_vault: Path) -> None:
    backend = FilesBackend(backend_vault, "owner")

    # backlinks to Widgets front page
    bl = backend.backlinks("knowledge-base/projects/widgets/widgets.md")
    sources = {r["source"] for r in bl}
    assert "knowledge-base/people/alex.md" in sources
    assert "knowledge-base/people/sam.md" in sources
    assert "knowledge-base/projects/widgets/knowledge/widgets-arch.md" in sources
    # Path outside vault or non-existent note returns []
    assert backend.backlinks("../outside.md") == []
    assert backend.backlinks("knowledge-base/does-not-exist.md") == []

    # Under elsewhere, private note sam.md never appears in backlinks
    elsewhere_backend = FilesBackend(backend_vault, "elsewhere")
    bl_elsewhere = elsewhere_backend.backlinks("knowledge-base/projects/widgets/widgets.md")
    assert "knowledge-base/people/sam.md" not in {r["source"] for r in bl_elsewhere}

    # link_gaps: (Alex -> Priya, count=2) and (Sam -> Widgets, count=1);
    # Alex -> Widgets has `works_on` so it is NOT a link gap; Widgets -> Widgets self-link is ignored.
    gaps = backend.link_gaps()
    assert [(g["source"], g["target"], g["count"]) for g in gaps] == [
        ("Alex", "Priya", 2),
        ("Sam", "Widgets", 1),
    ]
    # Under elsewhere, private Sam -> Widgets gap is hidden
    assert [(g["source"], g["target"]) for g in elsewhere_backend.link_gaps()] == [("Alex", "Priya")]

    # Scope filters (prefix and glob) and limit
    assert len(backend.link_gaps(scope="knowledge-base/people")) == 2
    assert len(backend.link_gaps(scope="knowledge-base/projects/widgets")) == 1
    assert len(backend.link_gaps(scope="knowledge-base/people/*.md")) == 2
    assert backend.link_gaps(scope="knowledge-base/organizations") == []
    assert backend.link_gaps(limit=0) == []


def test_search_resolve_name_lookup_stats_validate_and_agent_denial(backend_vault: Path) -> None:
    backend = FilesBackend(backend_vault, "owner")
    graph = load_graph(backend_vault, "owner")

    all_hits = backend.search("Widgets", fixed=True)
    assert all_hits == search(backend_vault, "owner", "Widgets", fixed=True)
    assert len(backend.search("Widgets", fixed=True, k=2)) == 2

    assert backend.resolve("Alex Rivera") == resolve(backend_vault, "owner", graph, "Alex Rivera")
    cands = backend.name_lookup("Rivera")
    assert cands and cands[0]["name"] == "Alex"

    st = backend.stats()
    assert st["entities"] == len(graph.entities)
    assert st["relationships"] == len(graph.relationships)
    assert st["by_type"]["person"] == 3

    errors, gaps = backend.validate()
    assert errors == []
    assert len(gaps) == 1 and gaps[0]["target"] == "Legacy System"

    # Non-reading agent reader fails closed on every method
    agent_backend = FilesBackend(backend_vault, "agent:person:priya@example.com")
    assert agent_backend.backlinks("knowledge-base/projects/widgets/widgets.md") == []
    assert agent_backend.link_gaps() == []
    assert agent_backend.entities() == []


def test_privacy_vault_canaries_never_leak_through_files_backend(tmp_path: Path) -> None:
    vault = build_privacy_vault(tmp_path)
    b = open_backend(vault, "elsewhere")
    for rel, canary in CANARIES.items():
        assert b.backlinks(rel) == []
        assert b.search(canary, fixed=True) == []
    for gap in b.link_gaps():
        assert gap["source_path"] not in CANARIES
        assert gap["target_path"] not in CANARIES


def test_resolve_backend_kind_precedence_and_validation(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _write(vault, "scout-config.yaml", "schema_version: 1\n")

    assert resolve_backend_kind(vault, env={}) == "files"
    assert resolve_backend_kind(vault, env={"SCOUT_KB_BACKEND": "FILES"}) == "files"

    _write(vault, "scout-config.yaml", "schema_version: 1\nkb:\n  backend: files\n")
    assert resolve_backend_kind(vault, env={}) == "files"

    _write(vault, "scout-config.yaml", "schema_version: 1\nkb:\n  backend:\n    kind: files\n")
    assert resolve_backend_kind(vault, env={}) == "files"

    _write(vault, "scout-config.yaml", "schema_version: 1\nkb:\n  backend: {}\n")
    assert resolve_backend_kind(vault, env={}) == "files"

    _write(vault, "scout-config.yaml", "schema_version: 1\nkb:\n  backend:\n    kind: bogus\n")
    assert resolve_backend_kind(vault, kind="files", env={}) == "files"
    with pytest.raises(ConfigError, match="Unsupported KB backend 'bogus'"):
        open_backend(vault, "owner", env={})
    with pytest.raises(ConfigError, match="Unsupported KB backend 'relate'"):
        open_backend(vault, "owner", env={"SCOUT_KB_BACKEND": "relate"})

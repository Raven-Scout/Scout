"""Deterministic backend parity & Gate G0 suite (`FilesBackend` vs real `RelateBackend`).

Skipped cleanly in `[dev]`-only CI environments where the optional `relate`
(`relate-db>=1.3,<1.4`) extra is not installed; runs end-to-end against the real
Rust Cypher 25 engine whenever `relate-db` is available.

Covers the Phase 1 exit criteria:
1. 1-for-1 output parity across every `KBBackend` method (`stats`, `entity`,
   `entities`, `related`, `neighbors`, `paths`, `backlinks`, `link_gaps`,
   `search`, `resolve`, `name_lookup`, `validate`).
2. Read-only snapshot enforcement (`CREATE` refused by RelateDB) and `ranked_search`.
3. Gate G0 across 3 distinct vault git commits and all reader classes (`owner`,
   `autonomous`, `elsewhere`, `delegate:phone`), proving zero hidden-note or
   canary leakage inside per-reader `.rl` stores.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

relate = pytest.importorskip("relate")

from scout.kb.backend import FilesBackend, open_backend  # noqa: E402
from scout.kb.ontology import clear_frontmatter_cache  # noqa: E402
from scout.kb.relate_backend import RelateBackend  # noqa: E402
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault, git  # noqa: E402

ENGINE_SCHEMA = Path(__file__).resolve().parent.parent.parent / "scout" / "kb" / "schema.yaml"


def _write(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _sort_related(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((r["type"], r["target"]) for r in rows)


def _assert_backend_parity(files_b: FilesBackend, relate_b: RelateBackend, vault: Path) -> None:
    assert relate_b.stats() == files_b.stats()

    f_ents = sorted(files_b.entities(), key=lambda e: str(e["name"]))
    r_ents = sorted(relate_b.entities(), key=lambda e: str(e["name"]))
    assert r_ents == f_ents

    names = [str(e["name"]) for e in f_ents]
    for name in [*names, "Nonexistent Entity"]:
        assert relate_b.entity(name) == files_b.entity(name)
        assert _sort_related(relate_b.related(name)) == _sort_related(files_b.related(name))

    for depth in (1, 2, 3):
        for name in names:
            f_nb = {r["entity"]: r["hops"] for r in files_b.neighbors(name, depth=depth)}
            r_nb = {r["entity"]: r["hops"] for r in relate_b.neighbors(name, depth=depth)}
            assert r_nb == f_nb, f"neighbors mismatch for {name} at depth={depth}"

    for a in names:
        for b in names:
            f_p1 = files_b.paths(a, b, max_hops=6, k=1)
            r_p1 = relate_b.paths(a, b, max_hops=6, k=1)
            assert len(r_p1) == len(f_p1)
            if f_p1:
                assert len(r_p1[0]) == len(f_p1[0])

            f_pk = {tuple(p) for p in files_b.paths(a, b, max_hops=6, k=5)}
            r_pk = {tuple(p) for p in relate_b.paths(a, b, max_hops=6, k=5)}
            assert r_pk == f_pk

    for md_file in sorted(vault.rglob("*.md")):
        if ".git" in md_file.parts or ".scout-cache" in md_file.parts:
            continue
        rel = md_file.relative_to(vault).as_posix()
        assert relate_b.backlinks(rel) == files_b.backlinks(rel)

    assert relate_b.link_gaps(limit=50) == files_b.link_gaps(limit=50)
    assert relate_b.validate() == files_b.validate()


def test_full_parity_ranked_search_and_read_only_guard_on_real_relatedb(tmp_path: Path) -> None:
    clear_frontmatter_cache()
    vault = tmp_path / "vault"
    _write(vault, "scout-config.yaml", "schema_version: 1\nkb:\n  backend:\n    kind: relate\n")
    _write(vault, "knowledge-base/ontology/schema.yaml", ENGINE_SCHEMA.read_text(encoding="utf-8"))

    _write(
        vault,
        "knowledge-base/people/alex.md",
        "---\nname: Alex\ntype: person\naliases: [Alex Rivera]\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme Corp\n---\n# Alex\n\n"
        "Discussed latency telemetry with [[Priya]] and [[Sam]] on [[Widgets]].\n",
    )
    _write(
        vault,
        "knowledge-base/people/priya.md",
        "---\nname: Priya\ntype: person\nrelationships:\n"
        "  - type: works_on\n    target: Widgets\n"
        "  - type: employed_by\n    target: Acme Corp\n---\n# Priya\n\n"
        "See [[widgets-caching]] for cache benchmarks.\n",
    )
    _write(
        vault,
        "knowledge-base/people/sam.md",
        "---\nname: Sam\ntype: person\nvisibility: private\nrelationships:\n"
        "  - type: manages\n    target: Alex\n---\n# Sam\n",
    )
    _write(vault, "knowledge-base/organizations/acme.md", "---\nname: Acme Corp\ntype: organization\n---\n# Acme\n")
    _write(
        vault,
        "knowledge-base/projects/widgets/widgets.md",
        "---\nname: Widgets\ntype: project\nstatus: active\npriority: high\n---\n# Widgets\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/widgets-caching.md",
        "# Widgets Caching\n\nBenchmark results for quorum cache invalidation in [[Widgets]].\n",
    )
    _write(
        vault,
        "knowledge-base/personal/task-open.md",
        "---\nname: Tune cache TTL\ntype: task\ndomain: work\nstatus: open\ndeadline: '2026-06-01'\n"
        "relationships:\n  - type: about\n    target: Widgets\n---\n# Tune cache TTL\n",
    )

    files_b = FilesBackend(vault, "owner")
    relate_b = open_backend(vault, "owner")
    assert isinstance(relate_b, RelateBackend)

    _assert_backend_parity(files_b, relate_b, vault)

    # Filtered queries, typed neighbors, filtered paths
    assert [e["name"] for e in relate_b.entities(type="task", exclude_status="done")] == ["Tune cache TTL"]
    assert [
        r["entity"]
        for r in relate_b.neighbors("Alex", depth=2, rel_types={"works_on", "worked_on_by"}, node_types={"person"})
    ] == ["Priya"]
    assert relate_b.paths("Alex", "Priya", rel_types={"works_on", "worked_on_by"}, k=3) == [
        [("works_on", "Widgets"), ("worked_on_by", "Priya")]
    ]

    # Full-text ranked_search (global & scoped to Widgets neighbourhood)
    ft_global = relate_b.ranked_search("quorum", limit=5)
    assert [r["path"] for r in ft_global] == ["knowledge-base/projects/widgets/knowledge/widgets-caching.md"]
    ft_scoped = relate_b.ranked_search("quorum", project="Widgets", limit=5)
    assert [r["path"] for r in ft_scoped] == ["knowledge-base/projects/widgets/knowledge/widgets-caching.md"]

    # Read-only store refuses mutations even through owner's raw_cypher
    with pytest.raises(relate.Error):
        relate_b.raw_cypher("CREATE (:ForbiddenWrite)")


def test_g0_across_three_vault_commits_and_all_readers(tmp_path: Path) -> None:
    """Gate G0: parity and zero hidden-note leakage across 3 vault commits and 4 readers."""
    vault = build_privacy_vault(tmp_path)
    shas = [line.strip() for line in git(vault, "rev-list", "--max-count=3", "HEAD").splitlines() if line.strip()]
    assert len(shas) == 3

    for sha in reversed(shas):
        git(vault, "checkout", "-q", sha)
        clear_frontmatter_cache()

        # Collect hidden note paths and identifying names from owner view at this commit
        owner_files = FilesBackend(vault, "owner")
        owner_relate = RelateBackend(vault, "owner")
        owner_build = owner_relate.build(snapshot_sha=sha)
        assert owner_build["entities"] == owner_files.stats()["entities"]
        _assert_backend_parity(owner_files, owner_relate, vault)

        # autonomous shares full reader visibility
        auto_files = FilesBackend(vault, "autonomous")
        auto_relate = RelateBackend(vault, "autonomous")
        _assert_backend_parity(auto_files, auto_relate, vault)

        for narrowed in ("elsewhere", "delegate:phone"):
            nf = FilesBackend(vault, narrowed)
            nr = RelateBackend(vault, narrowed)
            binfo = nr.build(snapshot_sha=sha)
            _assert_backend_parity(nf, nr, vault)

            # Inspect the raw RelateDB store directly to prove no hidden node, target, or canary exists
            store_path = binfo["store_path"]
            assert store_path and Path(store_path).is_file()
            conn = relate.connect(store_path, read_only=True)
            try:
                raw_nodes = conn.execute(
                    "MATCH (n:Node) RETURN n.key AS key, n.path AS path, n.target AS target, "
                    "n.name AS name, n.body AS body"
                ).data()
            finally:
                conn.close()

            dumped = str(raw_nodes)
            for canary_path, canary_tok in CANARIES.items():
                assert canary_tok not in dumped
                for row in raw_nodes:
                    assert row.get("path") != canary_path
                    assert row.get("key") != canary_path
                if canary_path == "knowledge-base/people/sam.md":
                    for row in raw_nodes:
                        assert row.get("name") != "sam"
                        assert row.get("target") not in ("sam", "Sammy", "people/sam")

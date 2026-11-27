"""Unit tests for Phase 2 Arm C: FilesBackend.ranked_search and session_context_kb.graph_context."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from scout.errors import ConfigError
from scout.kb.backend import FilesBackend
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.read import visibility_map
from scout.scripts import pre_session_data as psd
from scout.scripts import session_context_kb as kbs

DAY = dt.date(2026, 4, 15)


def _write(vault: Path, rel: str, text: str) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def arm_c_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    v = tmp_path / "vault"
    _write(
        v,
        "scout-config.yaml",
        "schema_version: 1\n"
        "user:\n  name: Sam Rivera\n"
        "privacy:\n  delegates:\n    helper: {}\n"
        "kb:\n  backend:\n    kind: files\n  precompute_graph_context: true\n",
    )
    _write(
        v,
        "knowledge-base/projects/widgets/widgets.md",
        "---\n"
        "name: widgets\n"
        "type: project\n"
        "status: Active\n"
        "as_of: 2026-04-12\n"
        "---\n"
        "# Widgets\n\n"
        "Release lead is [[priya-shah]] and partner contact is [[sam-secret]].\n"
        "See [[widgets-rollout]] and [[gateway]] for rollout details.\n",
    )
    _write(
        v,
        "knowledge-base/projects/widgets/knowledge/widgets-rollout.md",
        "---\nas_of: 2026-04-14\n---\n# Widgets Rollout Canary\n\nCanary deployment checklist for widgets.\n",
    )
    _write(
        v,
        "knowledge-base/projects/widgets/knowledge/widgets-arch.md",
        "---\nas_of: 2026-04-01\n---\n# Architecture\n\nStorage notes.\n",
    )
    _write(
        v,
        "knowledge-base/projects/gateway/gateway.md",
        "---\nname: gateway\ntype: project\nstatus: Active\n---\n# Gateway\n\n API gateway service.\n",
    )
    _write(
        v,
        "knowledge-base/projects/legacy/legacy.md",
        "---\nname: legacy\ntype: project\nstatus: Archived\n---\n# Legacy\n",
    )
    _write(
        v,
        "knowledge-base/people/alex-chen.md",
        "---\n"
        "name: Alex Chen\n"
        "type: person\n"
        "role: Staff Engineer\n"
        "relationships:\n"
        "  - type: works_on\n    target: widgets\n"
        "---\n"
        "# Alex Chen\n",
    )
    _write(
        v,
        "knowledge-base/people/priya-shah.md",
        "---\nname: Priya Shah\ntype: person\nrole: Director\n---\n# Priya Shah\n\nReviewing [[widgets]].\n",
    )
    _write(
        v,
        "knowledge-base/people/sam-secret.md",
        "---\n"
        "name: Sam Secret\n"
        "type: person\n"
        "visibility: private\n"
        "relationships:\n"
        "  - type: works_on\n    target: widgets\n"
        "---\n"
        "# Sam Secret\n\nAdvising [[widgets]] and [[gateway]].\n",
    )
    _write(
        v,
        "knowledge-base/people/archive/old-person.md",
        "---\nname: Old Person\ntype: person\n---\n# Old Person\n",
    )
    _write(
        v,
        "knowledge-base/sources/2026-04/2026-04-14-sync.md",
        "---\ndate: 2026-04-14\n---\n# Sync Notes\n\nDiscussed [[widgets]] rollout canary.\n",
    )
    return v


def test_files_backend_ranked_search_scores_title_and_project_scope(arm_c_vault: Path) -> None:
    fb = FilesBackend(arm_c_vault, "owner")
    assert fb.ranked_search("", limit=5) == []
    assert fb.ranked_search("   ", limit=5) == []
    assert fb.ranked_search("!!!", limit=5) == []
    assert fb.ranked_search("rollout", limit=0) == []
    assert fb.ranked_search("rollout", project="nonexistent") == []
    assert FilesBackend(arm_c_vault, "agent:person:x@example.com").ranked_search("rollout") == []

    # Binary file and non-matching note are skipped cleanly
    (arm_c_vault / "knowledge-base/projects/widgets/knowledge/blob.md").write_bytes(b"\x00binary rollout")
    hits = fb.ranked_search("rollout canary", limit=5)
    assert hits
    assert hits[0]["path"] == "knowledge-base/projects/widgets/knowledge/widgets-rollout.md"
    assert hits[0]["layer"] == "knowledge"
    assert hits[0]["score"] > 2.5

    scoped = fb.ranked_search("rollout canary", project="widgets", limit=5)
    scoped_paths = {h["path"] for h in scoped}
    assert "knowledge-base/projects/widgets/knowledge/widgets-rollout.md" in scoped_paths
    assert "knowledge-base/sources/2026-04/2026-04-14-sync.md" in scoped_paths


def test_should_precompute_graph_context_env_and_config(arm_c_vault: Path, tmp_path: Path) -> None:
    assert kbs.should_precompute_graph_context(arm_c_vault, env={}) is True
    assert kbs.should_precompute_graph_context(arm_c_vault, env={kbs.GRAPH_CONTEXT_ENV: "0"}) is False
    assert kbs.should_precompute_graph_context(tmp_path, env={kbs.GRAPH_CONTEXT_ENV: "true"}) is True
    assert kbs.should_precompute_graph_context(tmp_path, env={}) is False

    bad = tmp_path / "bad-vault"
    _write(bad, "scout-config.yaml", ": invalid: yaml: [")
    assert kbs.should_precompute_graph_context(bad, env={}) is False


def test_graph_context_builds_people_notes_and_link_gaps_with_privacy(arm_c_vault: Path) -> None:
    owner_labels = visibility_map(arm_c_vault, "owner", ["knowledge-base/**"])
    ctx = kbs.graph_context(
        arm_c_vault,
        owner_labels,
        full=True,
        reader="owner",
        query_hint="canary deployment checklist",
    )
    assert ctx["backend"] == "files"
    assert "degraded_from" not in ctx
    assert ctx["total"] == 2
    by_proj = {p["project"]: p for p in ctx["projects"]}
    assert set(by_proj) == {"widgets", "gateway"}

    w = by_proj["widgets"]
    people = {p["name"]: p for p in w["people"]}
    assert people["Alex Chen"]["relation"] == "typed"
    assert people["Priya Shah"]["relation"] == "co_mentioned"
    assert people["Sam Secret"]["relation"] == "both"
    assert people["Sam Secret"]["visibility"] == "private"
    assert "Old Person" not in people

    note_paths = [n["path"] for n in w["relevant_notes"]]
    assert note_paths[0] == "knowledge-base/projects/widgets/knowledge/widgets-rollout.md"
    assert w["relevant_notes"][0]["score"] is not None
    assert "knowledge-base/sources/2026-04/2026-04-14-sync.md" in note_paths

    # widgets -> gateway and Sam -> gateway have body links without frontmatter relations
    gaps = {(g["source"], g["target"]): g for g in w["link_gaps"]}
    assert ("widgets", "gateway") in gaps
    assert gaps[("widgets", "gateway")]["visibility"] == "normal"
    gw_gaps = {(g["source"], g["target"]): g for g in by_proj["gateway"]["link_gaps"]}
    assert ("Sam Secret", "gateway") in gw_gaps
    assert gw_gaps[("Sam Secret", "gateway")]["visibility"] == "private"

    # Delegate view hides Sam Secret completely (both in people and in link_gaps)
    del_labels = visibility_map(arm_c_vault, "delegate:helper", ["knowledge-base/**"])
    del_ctx = kbs.graph_context(
        arm_c_vault,
        del_labels,
        full=False,
        reader="delegate:helper",
        query_hint="canary",
    )
    assert "Sam Secret" not in json.dumps(del_ctx)
    assert "sam-secret" not in json.dumps(del_ctx)

    # Non-reading agent gets {}
    assert kbs.graph_context(arm_c_vault, {}, full=False, reader="agent:person:x@example.com") == {}


def test_graph_context_degrades_cleanly_on_missing_or_failing_backend(
    arm_c_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = visibility_map(arm_c_vault, "owner", ["knowledge-base/**"])

    # Case 1: invalid backend kind in config
    _write(
        arm_c_vault,
        "scout-config.yaml",
        "schema_version: 1\nkb:\n  backend:\n    kind: unknown_db\n  precompute_graph_context: true\n",
    )
    deg_invalid = kbs.graph_context(arm_c_vault, labels, full=True, reader="owner")
    assert deg_invalid["backend"] == "files"
    assert deg_invalid["degraded_from"] == "invalid"
    assert deg_invalid["fallback_reason"] == "ConfigError"

    # Case 2: relate configured when open_backend raises ConfigError
    _write(
        arm_c_vault,
        "scout-config.yaml",
        "schema_version: 1\nkb:\n  backend:\n    kind: relate\n  precompute_graph_context: true\n",
    )
    import scout.kb.backend as kb_backend

    def fail_open(*_a: Any, **_kw: Any) -> Any:
        raise ConfigError("relate extra not installed")

    monkeypatch.setattr(kb_backend, "open_backend", fail_open)
    deg_open = kbs.graph_context(arm_c_vault, labels, full=True, reader="owner")
    assert deg_open["backend"] == "files"
    assert deg_open["degraded_from"] == "relate"
    assert deg_open["fallback_reason"] == "ConfigError"
    assert deg_open["total"] == 2

    # Case 3: runtime error mid-query on a non-files backend falls back to FilesBackend
    class BrokenRelate:
        kind = "relate"

        def entities(self, **_kw: Any) -> list[dict[str, Any]]:
            raise RuntimeError("corrupt snapshot")

    deg_mid = kbs.graph_context(arm_c_vault, labels, full=True, reader="owner", backend=BrokenRelate())
    assert deg_mid["backend"] == "files"
    assert deg_mid["degraded_from"] == "relate"
    assert deg_mid["fallback_reason"] == "RuntimeError"

    # Case 4: failure on files backend returns {}
    class BrokenFiles:
        kind = "files"

        def entities(self, **_kw: Any) -> list[dict[str, Any]]:
            raise RuntimeError("disk error")

    assert kbs.graph_context(arm_c_vault, labels, full=True, reader="owner", backend=BrokenFiles()) == {}

    # Case 5: missing projects directory, folder without front page, structure-config fallback, and empty front page
    assert kbs.graph_context(arm_c_vault, labels, full=True, reader="owner", projects_dir="nowhere")["projects"] == []
    (arm_c_vault / "knowledge-base/projects/orphan-dir").mkdir(parents=True, exist_ok=True)
    (arm_c_vault / "knowledge-base/projects/empty/empty.md").parent.mkdir(parents=True, exist_ok=True)
    (arm_c_vault / "knowledge-base/projects/empty/empty.md").write_text("", encoding="utf-8")
    import scout.kb.structure

    monkeypatch.setattr(
        scout.kb.structure, "load_structure_config", lambda *_a: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    # Delegate with widgets front page withheld skips widgets cleanly
    partial_labels = {"knowledge-base/projects/gateway/gateway.md": "normal"}
    assert kbs.graph_context(arm_c_vault, partial_labels, full=False, reader="delegate:helper")["total"] == 1

    # Cap limits on relevant_notes and link_gaps + double-failure fallback
    for idx in range(12):
        _write(arm_c_vault, f"knowledge-base/projects/widgets/knowledge/extra-{idx:02d}.md", "# Extra\nwidgets\n")
    capped = kbs.graph_context(
        arm_c_vault, visibility_map(arm_c_vault, "owner", ["knowledge-base/**"]), full=True, query_hint="widgets"
    )
    w_capped = next(p for p in capped["projects"] if p["project"] == "widgets")
    assert len(w_capped["relevant_notes"]) == kbs.GRAPH_PROJECT_NOTES_MAX

    monkeypatch.setattr(kbs, "_build_graph_projects", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("all bad")))
    assert kbs.graph_context(arm_c_vault, labels, full=True, reader="owner", backend=BrokenRelate()) == {}


def test_trim_lab_and_lab_block_coverage(arm_c_vault: Path) -> None:
    lab_data = {
        "schema_version": 1,
        "threads": [
            {
                "id": f"t-{i}",
                "status": "closed" if i % 2 == 0 else "active",
                "last_run": f"2026-04-{i + 1:02d}",
                "summary": "x" * 600,
            }
            for i in range(25)
        ],
    }
    trimmed = kbs._trim_lab(lab_data, 2_000)
    assert trimmed["truncated"] is True
    assert len(json.dumps(trimmed, indent=2).encode("utf-8")) <= 2_000
    assert isinstance(kbs.lab_block(arm_c_vault, {}, full=True), dict)


def test_trim_graph_context_and_gather_integration(arm_c_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert kbs._trim_graph_context({}, 100) == {}
    big_gc = {
        "backend": "relate",
        "projects": [
            {
                "project": f"proj-{i}",
                "path": f"knowledge-base/projects/proj-{i}/proj-{i}.md",
                "visibility": "normal",
                "people": [
                    {"name": f"Person {j}", "path": "p.md", "visibility": "normal", "relation": "typed"}
                    for j in range(20)
                ],
                "people_total": 20,
                "relevant_notes": [
                    {"path": f"n{j}.md", "as_of": "2026-04-01", "score": 1.0, "visibility": "normal"} for j in range(10)
                ],
                "link_gaps": [
                    {
                        "source": "a",
                        "target": "b",
                        "source_path": "a.md",
                        "target_path": "b.md",
                        "count": 2,
                        "visibility": "normal",
                    }
                    for _ in range(10)
                ],
            }
            for i in range(25)
        ],
        "total": 25,
        "truncated": False,
    }
    trimmed = kbs.fit_budget({"graph_context": big_gc})
    assert len(json.dumps(trimmed["graph_context"], indent=2).encode("utf-8")) <= kbs.GRAPH_CONTEXT_MAX_BYTES
    assert trimmed["graph_context"]["truncated"] is True
    assert kbs._trim_graph_context(big_gc, 50)["projects"] == []

    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])
    ctx = psd.gather("morning-briefing", scout_dir=arm_c_vault, now=dt.datetime(2026, 4, 15, 9, 0, tzinfo=dt.UTC))
    assert ctx.graph_context["backend"] == "files"
    assert ctx.graph_context["total"] == 2

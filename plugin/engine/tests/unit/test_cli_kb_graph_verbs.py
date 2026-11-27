"""Unit tests for Phase 2 Arm B CLI graph verbs: neighbors, paths, backlinks, link-gaps, search --ranked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from scout.cli import app, delegate_write_refusal
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.raw_read import raw_kb_read
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

runner = CliRunner()
SAM = "knowledge-base/people/sam.md"
AGENT = "agent:person:priya@example.com"


@pytest.fixture
def graph_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    v = build_privacy_vault(tmp_path / "vault")
    # Add body wikilinks so backlinks, link-gaps, and ranked search have rich edges
    priya = v / "knowledge-base" / "people" / "priya.md"
    priya.write_text(
        priya.read_text(encoding="utf-8")
        + "\n## Collaboration\nWorking closely with [[sam]] and [[widgets]] on gateway rollout.\n",
        encoding="utf-8",
    )
    widgets_dir = v / "knowledge-base" / "projects" / "widgets"
    widgets_dir.mkdir(parents=True, exist_ok=True)
    (widgets_dir / "widgets.md").write_text(
        "---\nname: widgets\ntype: project\nstatus: Active\n---\n# Widgets\n\n"
        "Owned by [[priya]] with secret input from [[sam]].\n",
        encoding="utf-8",
    )
    return v


def _kb(vault: Path, *args: str, reader: str | None = None) -> Result:
    env = {"SCOUT_READER": reader} if reader else {}
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def _kb_json(vault: Path, *args: str, reader: str | None = None) -> Any:
    res = _kb(vault, *args, "--json", reader=reader)
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_neighbors_cli_labels_private_and_filters_by_reader(graph_vault: Path) -> None:
    rows = _kb_json(graph_vault, "neighbors", "priya", "--depth", "2")
    by_ent = {r["entity"]: r for r in rows}
    assert "sam" in by_ent
    assert by_ent["sam"]["label"]["visibility"] == "private"

    txt = _kb(
        graph_vault, "neighbors", "priya", "--depth", "1", "--rels", "works_with", "--node-types", "person"
    ).stdout
    assert "[private] [1 hop] sam (via works_with)" in txt

    empty = _kb(graph_vault, "neighbors", "no-such-entity").stdout
    assert "No neighbors found" in empty

    # Outside / elsewhere hides sam
    out_elsewhere = _kb(graph_vault, "neighbors", "priya", "--reader", "elsewhere")
    assert out_elsewhere.exit_code == 0
    assert "sam" not in out_elsewhere.stdout and CANARIES[SAM] not in out_elsewhere.stdout

    # Agent sees nothing
    out_agent = _kb(graph_vault, "neighbors", "priya", reader=AGENT)
    assert out_agent.exit_code == 0 and "sam" not in out_agent.stdout

    # Usage errors
    assert _kb(graph_vault, "neighbors", "priya", "--depth", "0").exit_code == 2
    assert _kb(graph_vault, "neighbors", "priya", "--limit", "0").exit_code == 2


def test_paths_cli_labels_nodes_and_handles_same_or_missing(graph_vault: Path) -> None:
    data = _kb_json(graph_vault, "paths", "priya", "sam", "--hops", "4", "--k", "2")
    assert data["source"] == "priya" and data["target"] == "sam"
    assert data["paths"][0][-1]["node"] == "sam"
    assert data["paths"][0][-1]["label"]["visibility"] == "private"

    txt = _kb(graph_vault, "paths", "priya", "sam").stdout
    assert "priya --works_with--> [private] sam" in txt

    same = _kb(graph_vault, "paths", "priya", "priya")
    assert same.exit_code == 0 and "same entity" in same.stdout

    none_res = _kb(graph_vault, "paths", "priya", "sam", "--reader", "elsewhere")
    assert none_res.exit_code == 0 and "No path" in none_res.stdout

    assert _kb(graph_vault, "paths", "priya", "sam", "--hops", "-1").exit_code == 2
    assert _kb(graph_vault, "paths", "priya", "sam", "--k", "0").exit_code == 2


def test_backlinks_and_link_gaps_cli_respect_privacy(graph_vault: Path) -> None:
    bl_rows = _kb_json(graph_vault, "backlinks", SAM)
    sources = {r["source"] for r in bl_rows}
    assert "knowledge-base/people/priya.md" in sources
    assert "knowledge-base/projects/widgets/widgets.md" in sources

    bl_txt = _kb(graph_vault, "backlinks", SAM).stdout
    assert "knowledge-base/people/priya.md" in bl_txt

    # Elsewhere sees 0 backlinks to hidden private note SAM
    bl_elsewhere = _kb_json(graph_vault, "backlinks", SAM, reader="elsewhere")
    assert bl_elsewhere == []
    assert "No backlinks" in _kb(graph_vault, "backlinks", "inbox.md").stdout

    # Outside-vault and .git paths
    assert _kb(graph_vault, "backlinks", "../outside.md").exit_code == 2
    assert _kb(graph_vault, "backlinks", ".git/config").exit_code == 2
    assert _kb(graph_vault, "backlinks", ".git/config", reader="elsewhere").exit_code == 0

    # link-gaps
    gaps = _kb_json(graph_vault, "link-gaps", "--scope", "knowledge-base/projects/widgets")
    pairs = {(g["source"], g["target"]) for g in gaps}
    assert ("widgets", "priya") in pairs
    assert ("widgets", "sam") in pairs

    gaps_txt = _kb(graph_vault, "link-gaps").stdout
    assert "[private] sam" in gaps_txt

    gaps_elsewhere = _kb_json(graph_vault, "link-gaps", reader="elsewhere")
    assert all("sam" not in (g["source"], g["target"]) for g in gaps_elsewhere)
    assert "No open link gaps." in _kb(graph_vault, "link-gaps", "--scope", "knowledge-base/nonexistent").stdout

    assert _kb(graph_vault, "link-gaps", "--limit", "0").exit_code == 2


def test_search_ranked_cli_and_option_guards(graph_vault: Path) -> None:
    hits = _kb_json(graph_vault, "search", "gateway rollout", "--ranked", "--limit", "5")
    assert any(h["path"] == "knowledge-base/people/priya.md" for h in hits)

    txt = _kb(graph_vault, "search", "CANARY-PRIV", "--ranked").stdout
    assert any(line.startswith(f"[private] {SAM}") for line in txt.splitlines())

    elsewhere_txt = _kb(graph_vault, "search", "CANARY-PRIV", "--ranked", "--reader", "elsewhere").stdout
    assert SAM not in elsewhere_txt and CANARIES[SAM] not in elsewhere_txt

    # Incompatible flag combinations exit 2
    assert _kb(graph_vault, "search", "rollout", "--ranked", "--fixed").exit_code == 2
    assert _kb(graph_vault, "search", "rollout", "--project", "widgets").exit_code == 2
    assert _kb(graph_vault, "search", "rollout", "--limit", "5").exit_code == 2
    assert _kb(graph_vault, "search", "rollout", "--ranked", "--limit", "0").exit_code == 2
    assert _kb(graph_vault, "search", "   ", "--ranked").exit_code == 2


def test_delegate_allowlist_and_raw_read_gate_cover_new_verbs(graph_vault: Path) -> None:
    for verb in ("neighbors", "paths", "backlinks", "link-gaps"):
        assert delegate_write_refusal(["kb", verb], {"SCOUT_READER": "delegate:phone"}) is None
    # Delegate still refused --kb-root on new verbs
    res = runner.invoke(
        app,
        ["kb", "neighbors", "priya", "--kb-root", str(graph_vault / "knowledge-base"), "--repo", str(graph_vault)],
        env={"SCOUT_READER": "delegate:phone"},
    )
    assert res.exit_code == 2

    # Piping path-printing graph verbs into cat is caught by raw_kb_read
    assert raw_kb_read("cat $(scoutctl kb backlinks knowledge-base/people/priya.md)")
    assert raw_kb_read("cat $(scoutctl kb link-gaps)")

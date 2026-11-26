"""Unit and privacy tests for canonical property-graph export (`scout.kb.export_jsonl`).

All test fixtures use anonymized stand-ins per CLAUDE.md (Alex / Priya / Sam,
`example-org`, `PROJ-1234`, generic project names).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb.export_jsonl import (
    _frontmatter_head_info,
    build_property_graph,
    format_jsonl,
    layer_of,
    norm_target,
    split_target,
    strip_code,
)
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.read import UsageError, cmd_export, load_graph
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

ENGINE_SCHEMA = Path(__file__).resolve().parent.parent.parent / "scout" / "kb" / "schema.yaml"


def _write(vault: Path, rel: str, content: str | bytes) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    vault = tmp_path / "vault"
    _write(vault, "scout-config.yaml", "schema_version: 1\n")
    _write(vault, "knowledge-base/ontology/schema.yaml", ENGINE_SCHEMA.read_text(encoding="utf-8"))

    # Entities
    _write(
        vault,
        "knowledge-base/people/alex.md",
        "---\nname: Alex\ntype: person\naliases:\n  - Alex R # nickname\nrelationships:\n"
        '  - type: works_on\n    target: "[[widgets]]"\n'
        "  - type: works_with\n    target: Ghost Person\n---\n# Alex\n\n"
        "## Overview\nWorks on [[widgets]] and cites [[2026-04-kickoff]].\n"
        "See also [[#Overview]], [[PROJ-1234]], [[project_widgets_memory]], [[diagram.png]], and [[missing-page]].\n"
        "Table link: [[people/priya\\|Priya S]].\n"
        "```\n[[ignored-in-fence]]\n```\n"
        "Inline `[[ignored-inline]]` link.\n",
    )
    _write(
        vault,
        "knowledge-base/people/priya.md",
        "---\nname: Priya\ntype: person\naliases: [Priya Sharma, 'P. Sharma']\n---\n# Priya\n\n"
        "Collaborates with [[Alex R]] on [[shared-stem]].\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/widgets.md",
        "---\nname: Widgets\ntype: project\nstatus: Active\npriority: high\nas_of: 2026-04-10\n---\n"
        "# Widgets\n\nRead [[widgets-arch]].\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/widgets-arch.md",
        "---\nlast_verified: '2026-04-11'\n---\n# Architecture\n\nOwned by [[Alex]].\n",
    )
    _write(
        vault,
        "knowledge-base/sources/2026-04-kickoff.md",
        "---\ndate: 2026-04-01\n---\n# Kickoff Notes\n",
    )
    # Two notes sharing stem `shared-stem` to exercise ambiguity detection
    _write(vault, "knowledge-base/topics/alpha/shared-stem.md", "# Alpha Stem\n")
    _write(vault, "knowledge-base/topics/beta/shared-stem.md", "# Beta Stem\n")
    # Root & vault-level notes + attachment + binary + plumbing sidecar
    _write(vault, "knowledge-base/overview.md", "# Overview\n")
    _write(vault, "inbox.md", "# Inbox\n")
    _write(vault, "action-items/2026-04-15.md", "# Action Items\n- Follow up with [[Priya]]\n")
    _write(vault, "knowledge-base/assets/diagram.png", b"\x89PNG\r\n")
    _write(vault, "knowledge-base/assets/corrupt.md", b"bad\x00binary")
    _write(vault, ".scout-state/sidecar.md", "# Engine Sidecar (must be skipped)\n")
    return vault


def test_pure_helpers_cover_fences_pipes_layers_and_frontmatter() -> None:
    raw = "Line 1 `[[code]]` [[real]]\n~~~\n[[fenced]]\n```\nstill fenced\n~~~\nLine 7 [[after]]"
    stripped = strip_code(raw)
    assert stripped.count("\n") == raw.count("\n")
    assert "code" not in stripped and "fenced" not in stripped
    assert "[[real]]" in stripped and "[[after]]" in stripped

    assert split_target("people/alex\\|Alex#Bio") == "people/alex"
    assert split_target("#Heading Only") == ""
    assert norm_target("  Café & Co. ") == "cafe-co"

    assert layer_of("inbox.md") == "vault:inbox"
    assert layer_of("action-items/2026-04-15.md") == "vault:action-items"
    assert layer_of("knowledge-base/overview.md") == "kb-root"
    assert layer_of("knowledge-base/projects/widgets/widgets.md") == "front-page"
    assert layer_of("knowledge-base/projects/widgets/knowledge/arch.md") == "knowledge"
    assert layer_of("knowledge-base/people/alex.md") == "people"

    props, names = _frontmatter_head_info(
        '---\nname: \'Alex\'\naliases:\n  - Al\n  - ""\nstatus: open # comment\ndate: ""\n'
        "aka: [A.R., ]\ntitle: ~\n---\n# Heading One\n"
    )
    assert props == {"status": "open", "title": "Heading One"}
    assert "Alex" in names and "Al" in names and "A.R." in names
    # Unclosed frontmatter >80 lines with no H1
    props_unclosed, names_unclosed = _frontmatter_head_info("---\n" + "foo: bar\n" * 85)
    assert props_unclosed == {} and names_unclosed == []


def test_property_graph_joins_ontology_and_body_links_and_skips_plumbing(sample_vault: Path) -> None:
    pg = build_property_graph(sample_vault, "owner", include_body=True, body_scope="knowledge-base/")

    # Plumbing (.scout-state/) and NUL-byte .md files are excluded
    assert ".scout-state/sidecar.md" not in pg.nodes
    assert "knowledge-base/assets/corrupt.md" not in pg.nodes

    # Body scoping keeps KB text and omits action-items/ archive bodies
    assert "body" in pg.nodes["knowledge-base/people/alex.md"]["props"]
    assert "body" not in pg.nodes["action-items/2026-04-15.md"]["props"]

    # Entity labels & properties
    alex_node = pg.nodes["knowledge-base/people/alex.md"]
    assert set(alex_node["labels"]) == {"Note", "Entity", "Person"}
    assert alex_node["props"]["name"] == "Alex"
    assert alex_node["props"]["entity_type"] == "person"

    # Placeholders created for unresolved ontology target, attachment, issue ref, memory slug, real broken link
    assert "unresolved:Ghost Person" in pg.nodes
    assert "attachment:knowledge-base/assets/diagram.png" in pg.nodes
    assert "issuereﬁ" not in pg.nodes
    assert "issueref:PROJ-1234" in pg.nodes
    assert "memoryslug:project_widgets_memory" in pg.nodes
    assert "unresolved:missing-page" in pg.nodes

    # Buckets match every link in sample_vault (fenced & inline code links ignored)
    assert pg.meta["buckets"]["anchor-only"] == 1
    assert pg.meta["buckets"]["issue-id-convention"] == 1
    assert pg.meta["buckets"]["memory-slug"] == 1
    assert pg.meta["buckets"]["real"] == 1
    assert pg.meta["buckets"]["resolved"] >= 6

    # Source note gets both LINKS_TO and CITES
    cites = [e for e in pg.edges if e["type"] == "CITES"]
    assert len(cites) == 1
    assert cites[0]["src"] == "knowledge-base/people/alex.md"
    assert cites[0]["dst"] == "knowledge-base/sources/2026-04-kickoff.md"

    # Ambiguous bare stem [[shared-stem]] is flagged
    ambig_edges = [
        e
        for e in pg.edges
        if e["type"] == "LINKS_TO" and e["src"] == "knowledge-base/people/priya.md" and e["props"]["ambiguous"]
    ]
    assert len(ambig_edges) == 1

    # Serialization round-trips cleanly
    raw = format_jsonl(pg)
    parsed_lines = [json.loads(line) for line in raw.splitlines()]
    assert parsed_lines[0]["kind"] == "meta"
    assert len(parsed_lines) == 1 + len(pg.nodes) + len(pg.edges)


def test_narrowed_and_agent_readers_never_leak_hidden_notes_or_placeholders(tmp_path: Path) -> None:
    vault = build_privacy_vault(tmp_path)
    # Add links from a public note to private entity Sam (by name, alias, path, and private attachment)
    public_note = vault / "knowledge-base" / "people" / "priya.md"
    public_note.write_text(
        public_note.read_text(encoding="utf-8")
        + "\nLinks to hidden [[Sam]], [[Sammy]], [[people/sam]], and [[trading/positions]].\n",
        encoding="utf-8",
    )
    clear_frontmatter_cache()

    # 1. agent:* touches nothing and gets empty output
    agent_pg = build_property_graph(vault, "agent:person:priya@example.com")
    assert agent_pg.nodes == {} and agent_pg.edges == [] and agent_pg.labels == []

    # 2. elsewhere sees public notes, drops hidden notes AND drops body links targeting hidden notes
    elsewhere_pg = build_property_graph(vault, "elsewhere", include_body=True, body_scope="")
    dumped = format_jsonl(elsewhere_pg)
    assert elsewhere_pg.meta["dropped_hidden_links"] >= 4
    assert "knowledge-base/people/sam.md" not in elsewhere_pg.nodes
    assert "trading/positions.md" not in elsewhere_pg.nodes
    for canary_path, canary_tok in CANARIES.items():
        assert canary_path not in elsewhere_pg.nodes
        assert canary_tok not in dumped
    # Crucially: no Unresolved placeholder was created for Sam or Sammy!
    unresolved_targets = {n["props"].get("target") for n in elsewhere_pg.nodes.values() if "Unresolved" in n["labels"]}
    assert not (unresolved_targets & {"Sam", "Sammy", "people/sam", "trading/positions"})


def test_cli_kb_export_preserves_default_json_and_supports_jsonl(sample_vault: Path) -> None:
    runner = CliRunner()

    # Default JSON format is unchanged
    res_default = runner.invoke(app, ["kb", "export", "--repo", str(sample_vault)])
    assert res_default.exit_code == 0, res_default.output
    data_default = json.loads(res_default.stdout)
    assert set(data_default) == {"entities", "relationships"}
    assert "Alex" in data_default["entities"]

    # JSONL format
    res_jsonl = runner.invoke(app, ["kb", "export", "--format", "jsonl", "--no-body", "--repo", str(sample_vault)])
    assert res_jsonl.exit_code == 0, res_jsonl.output
    rows = [json.loads(line) for line in res_jsonl.stdout.splitlines()]
    assert rows[0]["kind"] == "meta"
    assert rows[0]["entities"] == 3
    note_rows = [r for r in rows if r["kind"] == "node" and "Note" in r["labels"]]
    assert all("body" not in r["props"] for r in note_rows)

    # --format jsonl combined with --json returns JSON list of records
    res_as_json = runner.invoke(
        app, ["kb", "export", "--format", "jsonl", "--body-scope", "", "--json", "--repo", str(sample_vault)]
    )
    assert res_as_json.exit_code == 0, res_as_json.output
    as_list = json.loads(res_as_json.stdout)
    assert isinstance(as_list, list) and as_list[0]["kind"] == "meta"

    # Bad --format raises UsageError (exit 2)
    res_bad = runner.invoke(app, ["kb", "export", "--format", "xml", "--repo", str(sample_vault)])
    assert res_bad.exit_code == 2

    g = load_graph(sample_vault, "owner")
    with pytest.raises(UsageError):
        cmd_export(g, sample_vault, fmt="csv")


def test_export_handles_unreadable_note_git_oserror_line_cap_and_orphan_entity(
    sample_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as sp
    from typing import Any

    from scout.kb import export_jsonl as ej

    # >20 occurrences of the same link caps recorded line numbers at 20
    many = "\n".join(f"Line {i} [[widgets]]" for i in range(25))
    _write(sample_vault, "knowledge-base/topics/many-links.md", f"# Many\n{many}\n")

    unreadable = _write(sample_vault, "knowledge-base/topics/unreadable.md", "# Secret\n")
    unreadable.chmod(0o000)
    try:
        g = load_graph(sample_vault, "elsewhere")
        # Inject a synthetic orphan entity (not backed by a visible node) and edge
        g.entities["Orphan"] = {"name": "Orphan", "type": "person", "_source_path": ""}
        g.relationships.append({"source": "Orphan", "type": "works_on", "target": "Widgets"})

        real_run = sp.run

        def raise_git(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else ()
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                raise OSError("git missing")
            return real_run(*args, **kwargs)

        monkeypatch.setattr(ej.subprocess, "run", raise_git)
        pg = build_property_graph(sample_vault, "elsewhere", graph=g)
        assert pg.meta["sha"] == ""
        assert "knowledge-base/topics/unreadable.md" not in pg.nodes
        assert "Orphan" not in pg.entity_keys
        many_edge = next(
            e for e in pg.edges if e["src"] == "knowledge-base/topics/many-links.md" and e["type"] == "LINKS_TO"
        )
        assert many_edge["props"]["count"] == 25
        assert len(many_edge["props"]["lines"]) == 20
    finally:
        unreadable.chmod(0o644)

"""The line-anchored frontmatter fence shared by the KB readers (#239, #240)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

from scout.kb.frontmatter import frontmatter_block
from scout.kb.ontology import KnowledgeGraph

# The vault parser before it became a shim over scoutctl (#453), kept as the reference.
LEGACY_PARSER = Path(__file__).resolve().parents[1] / "fixtures" / "vault-parser-pre-shim.py.txt"

# The KEYS-FIRST arrangement from #239: a provenance banner whose separator
# carries a `---` run, with `type:` below it.
PROVENANCE_ENTITY = (
    "---\n"
    'name: "example-suite"\n'
    "last_updated: 2026-09-16\n"
    "#\n"
    "# --- provenance log, oldest first ---------------------------------\n"
    "# 2026-09-16: Alex moved the keys up\n"
    "type: project\n"
    "status: active\n"
    "---\n"
    "\n"
    "Body.\n"
)


def test_block_is_the_lines_between_the_fences() -> None:
    assert frontmatter_block(["---", "a: 1", "b: 2", "---", "body"]) == ["a: 1", "b: 2"]


def test_block_needs_an_opening_fence_on_line_one() -> None:
    assert frontmatter_block(["# Title", "---", "a: 1", "---"]) is None
    assert frontmatter_block([]) is None


def test_block_ignores_a_dash_run_inside_a_comment() -> None:
    block = frontmatter_block(PROVENANCE_ENTITY.split("\n"))
    assert block is not None
    assert "type: project" in block


def test_block_ignores_an_indented_dash_line() -> None:
    # A `---` line inside a block scalar is content, not the closing fence.
    lines = ["---", "note: |", "  before", "  ---", "  after", "type: project", "---"]
    assert frontmatter_block(lines) == lines[1:-1]


def test_block_tolerates_trailing_space_and_cr_on_fences() -> None:
    assert frontmatter_block(["--- ", "a: 1", "---\r"]) == ["a: 1"]


def test_unclosed_block_is_none_unless_asked() -> None:
    lines = ["---", "a: 1", "b: 2"]
    assert frontmatter_block(lines) is None
    assert frontmatter_block(lines, require_close=False) == ["a: 1", "b: 2"]


def _schema(tmp_path: Path) -> Path:
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types:\n  project:\n    properties:\n      required: [name, type]\n")
    return schema


def _template_graph_class() -> type:
    """The vault's pre-shim `knowledge-base/ontology/parser.py`, which cannot import scout."""
    loader = importlib.machinery.SourceFileLoader("vault_ontology_parser", str(LEGACY_PARSER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KnowledgeGraph  # type: ignore[no-any-return]


GRAPH_CLASSES = pytest.mark.parametrize(
    "graph_class", [KnowledgeGraph, _template_graph_class()], ids=["engine", "template"]
)


@GRAPH_CLASSES
def test_entity_with_a_dashed_comment_stays_in_the_graph(tmp_path: Path, graph_class: type) -> None:
    kb = tmp_path / "knowledge-base"  # a vault layout: the engine finds the vault from it
    kb.mkdir()
    (kb / "example-suite.md").write_text(PROVENANCE_ENTITY)
    graph = graph_class(schema_path=str(_schema(tmp_path)), kb_root=str(kb)).load()
    entity = graph.entity("example-suite")
    assert entity is not None
    assert entity["type"] == "project"


@GRAPH_CLASSES
def test_unclosed_frontmatter_is_not_parsed(tmp_path: Path, graph_class: type) -> None:
    path = tmp_path / "open.md"
    path.write_text("---\nname: x\ntype: project\n\nBody, no closing fence.\n")
    graph = graph_class(schema_path=str(_schema(tmp_path)), kb_root=str(tmp_path))
    assert graph._extract_frontmatter(path) is None

"""Unit tests for scout.kb.ontology."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from scout.errors import KBError
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "kb-sample"


def test_knowledge_graph_loads_fixture() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    results = g.query(type="person", name="Alex")
    assert len(results) == 1
    # Adapt assertion shape to what query() returns (dict vs object).
    first = results[0]
    if hasattr(first, "name"):
        assert first.name == "Alex"
    else:
        assert first["name"] == "Alex"


def test_knowledge_graph_query_unknown_type_returns_empty() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    assert g.query(type="nonexistent") == []


def test_knowledge_graph_validate_returns_structured_errors() -> None:
    # Locks in the library-side validate() surface ahead of the Plan 4
    # `scoutctl kb validate` CLI port. ~/Scout users today reach this via
    # `python3 knowledge-base/ontology/parser.py validate`; once the engine
    # is the source of truth, that capability must stay reachable from the
    # library.
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    errors = g.validate()
    assert isinstance(errors, list)
    for err in errors:
        assert set(err.keys()) >= {"entity", "message"}
        assert isinstance(err["entity"], str)
        assert isinstance(err["message"], str)


def test_missing_schema_raises_kberror(tmp_path: Path) -> None:
    """A missing schema.yaml must raise a typed KBError, not a raw OSError
    out of the KnowledgeGraph constructor (#46)."""
    with pytest.raises(KBError):
        KnowledgeGraph(
            schema_path=str(tmp_path / "nope.yaml"),
            kb_root=str(tmp_path),
        )


def test_malformed_schema_raises_kberror(tmp_path: Path) -> None:
    """A syntactically invalid schema.yaml must raise KBError (#46)."""
    bad = tmp_path / "schema.yaml"
    bad.write_text("entity_types: [unclosed\n")
    with pytest.raises(KBError):
        KnowledgeGraph(
            schema_path=str(bad),
            kb_root=str(tmp_path),
        )


def test_extract_frontmatter_inline_triple_dash_in_value_not_closing_fence(tmp_path: Path) -> None:
    """#64: _extract_frontmatter must not close at `---` embedded mid-line in
    a frontmatter value. Only a line-initial `\\n---` (on its own line) closes.

    The bug: `text.find('---', 3)` matches the FIRST occurrence of `---`
    anywhere, including mid-line in a YAML value. This causes the YAML slice
    to be truncated before the closing fence, yielding a parse error or wrong
    data. The fix: `text.find('\\n---', 3)` requires the fence on its own line.
    """
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types:\n  person:\n    properties:\n      required: [name, type]\n")

    # The frontmatter value for `description` contains `---` mid-line.
    # With the bug, find("---", 3) hits the `---` inside the description value
    # and slices the YAML as `\nname: TestP` which is invalid → returns None.
    md_file = tmp_path / "person-inline-dash.md"
    md_file.write_text(
        "---\n"
        "name: TestPerson\n"
        "type: person\n"
        "description: 'range --- value'\n"  # inline --- in YAML value
        "---\n"
        "\n"
        "Body text.\n"
    )

    # Direct test of _extract_frontmatter to isolate the bug
    from scout.kb.ontology import KnowledgeGraph as KG

    # Use a minimal dummy schema to construct the graph
    g = KG(schema_path=str(schema), kb_root=str(tmp_path))
    fm = g._extract_frontmatter(md_file)
    assert fm is not None, "_extract_frontmatter returned None — inline `---` in value was mistaken for closing fence"
    assert fm.get("name") == "TestPerson", f"Frontmatter truncated; got: {fm}"
    assert fm.get("type") == "person"


def test_validate_entity_type_without_properties_key(tmp_path: Path) -> None:
    """An entity type defined with no `properties:` key must not raise
    KeyError in validate() (#46)."""
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types:\n  task: {}\n")
    kg = KnowledgeGraph(
        schema_path=str(schema),
        kb_root=str(tmp_path),
    )
    # No .load() needed — validate() iterates self.entities which starts empty.
    errors = kg.validate()
    assert isinstance(errors, list)


# ----- per-file stat cache (#77) -------------------------------------------

_SCHEMA = (
    "entity_types:\n  person:\n    properties:\n      required: [name, type]\n"
    "relationship_types:\n  works_with:\n    inverse: works_with\n"
)


def _settled(path: Path) -> None:
    """Backdate mtime past the racy window so the parse is cacheable."""
    old = path.stat().st_mtime - 3600
    os.utime(path, (old, old))


def _person(path: Path, name: str, extra: str = "") -> None:
    path.write_text(f"---\nname: {name}\ntype: person\n{extra}---\nBody.\n")
    _settled(path)


def _graph(tmp_path: Path) -> KnowledgeGraph:
    schema = tmp_path / "schema.yaml"
    if not schema.exists():
        schema.write_text(_SCHEMA)
    return KnowledgeGraph(schema_path=str(schema), kb_root=str(tmp_path / "kb"))


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    root = tmp_path / "kb"
    root.mkdir()
    _person(root / "alex.md", "Alex", 'relationships:\n  - type: works_with\n    target: "[[Priya]]"\n')
    _person(root / "priya.md", "Priya")
    (root / "notes.md").write_text("no frontmatter\n")
    _settled(root / "notes.md")
    return root


def _parses(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    seen: list[Path] = []
    real = KnowledgeGraph._extract_frontmatter

    def spy(self: KnowledgeGraph, path: Path) -> dict[str, Any] | None:
        seen.append(path)
        return real(self, path)

    monkeypatch.setattr(KnowledgeGraph, "_extract_frontmatter", spy)
    return seen


def test_cached_load_matches_uncached_and_skips_parsing(
    kb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cold = _graph(tmp_path).load()
    parses = _parses(monkeypatch)
    warm = _graph(tmp_path).load()  # a new instance shares the process cache
    assert parses == []
    assert warm.entities == cold.entities
    assert warm.relationships == cold.relationships
    assert warm.export_json() == cold.export_json()


def test_edit_reparses_only_that_file(kb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _graph(tmp_path).load()
    _person(kb / "priya.md", "Priya", "role: lead\n")
    parses = _parses(monkeypatch)
    g = _graph(tmp_path).load()
    assert parses == [kb / "priya.md"]
    assert g.entities["Priya"]["role"] == "lead"


def test_same_size_edit_with_restored_mtime_is_not_stale(kb: Path, tmp_path: Path) -> None:
    """Size and mtime alike, content not: ctime still moves, so it reparses."""
    _graph(tmp_path).load()
    target = kb / "priya.md"
    st = target.stat()
    target.write_text(target.read_text().replace("Priya", "Pryia"))
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert target.stat().st_size == st.st_size
    g = _graph(tmp_path).load()
    assert set(g.entities) == {"Alex", "Pryia"}


def test_deleted_and_added_files(kb: Path, tmp_path: Path) -> None:
    _graph(tmp_path).load()
    (kb / "priya.md").unlink()
    _person(kb / "sam.md", "Sam")
    g = _graph(tmp_path).load()
    assert set(g.entities) == {"Alex", "Sam"}


def test_recently_modified_file_is_reread_every_load(kb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside the racy window a parse is not cached (coarse-mtime filesystems)."""
    (kb / "sam.md").write_text("---\nname: Sam\ntype: person\n---\n")
    _graph(tmp_path).load()
    parses = _parses(monkeypatch)
    _graph(tmp_path).load()
    assert parses == [kb / "sam.md"]


def test_cache_hits_hand_out_fresh_objects(kb: Path, tmp_path: Path) -> None:
    first = _graph(tmp_path).load()
    first.entities["Alex"]["role"] = "mutated"
    second = _graph(tmp_path).load()
    assert "role" not in second.entities["Alex"]
    assert second.related("Alex") == [{"source": "Alex", "type": "works_with", "target": "Priya"}]


def test_unstattable_entry_is_skipped(kb: Path, tmp_path: Path) -> None:
    """A dangling symlink: rglob yields it, stat fails, the load goes on."""
    (kb / "gone.md").symlink_to(kb / "nowhere.md")
    g = _graph(tmp_path).load()
    assert set(g.entities) == {"Alex", "Priya"}

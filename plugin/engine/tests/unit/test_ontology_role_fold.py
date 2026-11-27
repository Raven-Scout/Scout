"""The role fold (layout v2, spec 2026-11-26 §3.2): role notes fold into their identity, strictest label wins."""

from __future__ import annotations

from pathlib import Path

from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _vault(tmp_path: Path, *, private_role: bool = False) -> Path:
    v = tmp_path / "v"
    _w(
        v / "scout-config.yaml",
        "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n",
    )
    kb = v / "knowledge-base"
    _w(
        kb / "people/alex.md",
        "---\nname: Alex\ntype: person\naliases: [Al]\nemail: a@example.com\nspaces: [work, personal]\n---\n# Alex\n",
    )
    _w(
        kb / "work/people/alex-work.md",
        '---\ntype: role\nrole_of: "[[people/alex]]"\nspace: work\nrole: Engineer\nrelationships:\n'
        '  - type: works_on\n    target: "[[work/projects/apollo/apollo]]"\n---\n',
    )
    vis = "visibility: private\n" if private_role else ""
    _w(
        kb / "personal/people/alex-personal.md",
        f'---\ntype: role\nrole_of: "[[people/alex]]"\nspace: personal\n{vis}'
        'role: Neighbour\nbirthday: "1990-01-01"\n---\n',
    )
    _w(kb / "work/projects/apollo/apollo.md", "---\nname: Apollo\ntype: project\n---\n")
    _w(kb / "work/people/ghost-work.md", '---\ntype: role\nrole_of: "[[people/ghost]]"\nspace: work\n---\n')
    schema = Path(__file__).resolve().parents[2] / "scout" / "kb" / "schema.yaml"
    _w(kb / "ontology/schema.yaml", schema.read_text())
    clear_frontmatter_cache()
    return v


def _graph(v: Path, reader: str = "owner") -> KnowledgeGraph:
    kb = v / "knowledge-base"
    return KnowledgeGraph(str(kb / "ontology/schema.yaml"), str(kb), vault=v, reader=reader).load()


def test_roles_fold_into_the_identity(tmp_path: Path) -> None:
    g = _graph(_vault(tmp_path))
    alex = g.entity("Alex")
    assert alex is not None and alex["type"] == "person"
    assert alex["role"] == "Engineer"  # the default space's role first
    assert alex["birthday"] == "1990-01-01"
    assert set(alex["_roles"]) == {"work", "personal"}
    assert alex["_roles"]["personal"]["role"] == "Neighbour"
    assert alex["_source_path"].endswith("people/alex.md")
    assert {"source": "Alex", "type": "works_on", "target": "Apollo"} == {
        k: v for k, v in g.related("Alex")[0].items() if k != "_label"
    }
    assert "role" not in {e["type"] for e in g.entities.values()}
    assert g.orphan_roles == ["knowledge-base/work/people/ghost-work.md"]


def test_folded_entity_takes_the_strictest_label(tmp_path: Path) -> None:
    g = _graph(_vault(tmp_path))
    # the personal space is private by default, so its role is: Alex reads as private
    assert g.label_of("Alex")["visibility"] == "private"
    edge = g.related("Alex")[0]
    assert edge["_label"]["path"] == "knowledge-base/work/people/alex-work.md"
    assert edge["_label"]["visibility"] == "normal"


def test_an_invisible_role_is_not_folded(tmp_path: Path) -> None:
    g = _graph(_vault(tmp_path, private_role=True), reader="elsewhere")
    alex = g.entity("Alex")
    assert alex is not None
    assert "personal" not in alex.get("_roles", {})
    assert "birthday" not in alex


def test_validate_does_not_see_roles(tmp_path: Path) -> None:
    g = _graph(_vault(tmp_path))
    errors = g.validate()
    assert not any("role" in e["message"].lower() and "type" in e["message"].lower() for e in errors)


def test_a_registry_problem_or_a_stray_relationship_does_not_stop_the_fold(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import scout.config

    v = _vault(tmp_path)
    role = v / "knowledge-base/work/people/alex-work.md"
    role.write_text(role.read_text().replace("relationships:\n", "relationships:\n  - just text\n"))

    def broken(*a: object, **k: object) -> object:
        raise ValueError("bad registry")

    monkeypatch.setattr(scout.config, "load_spaces", broken)
    g = _graph(v)
    alex = g.entity("Alex")
    assert alex is not None and set(alex["_roles"]) == {"work", "personal"}
    assert [r["target"] for r in g.related("Alex")] == ["Apollo"]


def test_an_edge_from_a_private_role_note_is_marked_private(tmp_path: Path) -> None:
    """The edge keeps its role file's label (§3.2): `related` and `traverse` mark it even when its target is a
    normal note (review of #832)."""
    from scout.kb.read import cmd_related, cmd_traverse

    v = _vault(tmp_path)
    _w(
        v / "knowledge-base/personal/people/alex-personal.md",
        '---\ntype: role\nrole_of: "[[people/alex]]"\nspace: personal\nrole: Neighbour\nrelationships:\n'
        '  - type: works_on\n    target: "[[work/projects/garden/garden]]"\n---\n',
    )
    _w(v / "knowledge-base/work/projects/garden/garden.md", "---\nname: Garden\ntype: project\n---\n")
    clear_frontmatter_cache()
    g = _graph(v)
    related = cmd_related(g, "Alex")
    garden = next(ln for ln in related.lines if "Garden" in ln)
    apollo = next(ln for ln in related.lines if "Apollo" in ln)
    assert garden.strip().startswith("[private]") and not apollo.strip().startswith("[private]")
    assert any(lb.get("visibility") == "private" for lb in related.labels)
    traverse = cmd_traverse(g, "Alex", 2, None)
    assert next(ln for ln in traverse.lines if "Garden" in ln).strip().startswith("[private]")
    assert not next(ln for ln in traverse.lines if "Apollo" in ln).strip().startswith("[private]")

"""`scoutctl kb validate` must exit non-zero when the graph is broken.

The exit code is the only part of this command another program reads. A vault's dreaming
preflight runs it as the `graph-validate` row and prints ✅ / 🟡 from the code alone, so a
command that printed "N validation error(s) found." and still returned 0 made every one of
those rows information-free — a vault ran for days with a live orphaned entity under a ✅.

A vault-side regression test has asserted this since 2026-10-30, but it asserts it of
`knowledge-base/ontology/parser.py`, which is now a shim that `exec`s this command. The
assertion has to live on this side of the shim or it protects nothing.
"""

from __future__ import annotations

from typer.testing import CliRunner

from scout import cli

runner = CliRunner()

SCHEMA = """\
document_kinds:
  - project-log

entity_types:
  person:
    properties:
      required: [name, type]
      optional: [role]
    relationships:
      - works_with
  project:
    properties:
      required: [name, type, status]
      optional: []
    relationships:
      - part_of

relationship_types:
  works_with:
    inverse: works_with
    symmetric: true
  part_of:
    inverse: has_part
  has_part:
    inverse: part_of
"""

# A pair of entities that reference each other — neither orphaned, both well typed.
ANN = '---\nname: Ann\ntype: person\nrelationships:\n  - type: works_with\n    target: "[[people/bo]]"\n---\n\n# Ann\n'
BO = "---\nname: Bo\ntype: person\n---\n\n# Bo\n"

KB = "knowledge-base"


def validate(kb_repo, **extra: str):
    """Build a throwaway graph of ANN + BO + `extra`, then run `kb validate` over it."""
    kb_repo.write(f"{KB}/ontology/schema.yaml", SCHEMA)
    kb_repo.write(f"{KB}/people/ann.md", ANN)
    kb_repo.write(f"{KB}/people/bo.md", BO)
    for rel, body in extra.items():
        kb_repo.write(rel, body)
    return runner.invoke(cli.app, ["kb", "validate", "--repo", str(kb_repo.root)])


def test_clean_graph_exits_zero(kb_repo) -> None:
    result = validate(kb_repo)
    assert result.exit_code == 0
    assert "No validation errors." in result.output


def test_unknown_entity_type_exits_three(kb_repo) -> None:
    """A note given a `type:` that is not an entity type enters the graph untyped."""
    result = validate(kb_repo, **{f"{KB}/topics/t.md": "---\nname: A Topic\ntype: topic\n---\n\n# A Topic\n"})
    assert result.exit_code == 3
    assert "Unknown entity type: topic" in result.output


def test_missing_required_property_exits_three(kb_repo) -> None:
    body = '---\nname: P\ntype: project\nrelationships:\n  - type: part_of\n    target: "[[people/ann]]"\n---\n\n# P\n'
    result = validate(kb_repo, **{f"{KB}/projects/p.md": body})
    assert result.exit_code == 3
    assert "Missing required property: status" in result.output


def test_orphaned_entity_exits_three(kb_repo) -> None:
    result = validate(kb_repo, **{f"{KB}/people/cy.md": "---\nname: Cy\ntype: person\n---\n\n# Cy\n"})
    assert result.exit_code == 3
    assert "Orphaned entity" in result.output


def test_registered_document_kind_stays_clean(kb_repo) -> None:
    """Log and source shards carry a document kind and no `name:`. They are not findings."""
    body = "---\ntype: project-log\ndate: 2026-01-01\n---\n\n# Log\n"
    result = validate(kb_repo, **{f"{KB}/projects/x/log/x-2026-01-01.md": body})
    assert result.exit_code == 0

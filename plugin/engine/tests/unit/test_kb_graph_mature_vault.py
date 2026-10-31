"""What a mature vault's hand-edited parser grew, in the engine's ``KnowledgeGraph``.

A vault that edited its ``knowledge-base/ontology/parser.py`` before the shim
(#453, #495) relies on behaviour the v0.18.2 template never had. The engine
carries the generic part, so such a vault can switch to the shim:

- two-pass target resolution (display name, ``[[slug|Display]]``, filename
  stem or path, declared ``aliases:``), collisions left unresolved;
- archived snapshots (``projects/**/archive/**``, or the schema's
  ``graph.exclude``) skipped;
- malformed frontmatter kept in ``parse_errors`` and reported by ``validate``;
- a dangling-target check whose exceptions come from the vault's schema
  ``validation:`` block, with accepted known gaps reported apart from errors;
- ``MM-DD`` birthdays;
- typed name lookup (``--lookup-type``), each candidate carrying its ``type``;
- name lookup by file slug and by each distinctive word of a multi-word name
  (corporate suffixes never match alone; the schema's ``graph.name_word_stoplist``
  adds more), exact matches ranked by field, then type.

Names are the CLAUDE.md stand-ins (Alex / Priya / Sam, ``example-org``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from scout.cli import app
from scout.errors import KBSchemaError
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache, parse_lookup_types

SAMPLE_SCHEMA = Path(__file__).parent.parent / "fixtures" / "kb-sample" / "schema.yaml"
runner = CliRunner()

VALIDATION = {
    "external_references": ["Hosted Warehouse"],
    "known_gaps": {"Sam": "reviewed: a contact with no entity file"},
    "provenance_relationship_types": ["confirmed_on"],
}


@pytest.fixture(autouse=True)
def _cold() -> None:
    clear_frontmatter_cache()


def _vault(tmp_path: Path, extra: dict[str, Any] | None = None) -> Path:
    """A vault whose own ``knowledge-base/ontology/schema.yaml`` is the sample schema plus ``extra``."""
    vault = tmp_path / "vault"
    schema = yaml.safe_load(SAMPLE_SCHEMA.read_text(encoding="utf-8"))
    schema["relationship_types"]["confirmed_on"] = {}
    schema.update(extra or {})
    path = vault / "knowledge-base" / "ontology" / "schema.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    return vault


def _kb(vault: Path) -> Path:
    return vault / "knowledge-base"


def _schema(vault: Path) -> str:
    return str(_kb(vault) / "ontology" / "schema.yaml")


def _note(vault: Path, rel: str, frontmatter: str, body: str = "Body.\n") -> Path:
    p = _kb(vault) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return p


def _graph(vault: Path) -> KnowledgeGraph:
    return KnowledgeGraph(schema_path=_schema(vault), kb_root=str(_kb(vault)), vault=vault).load()


def _rel(target: str, rel_type: str = "works_with") -> str:
    return f"relationships:\n  - type: {rel_type}\n    target: {json.dumps(target)}\n"


def _targets(g: KnowledgeGraph, source: str) -> list[str]:
    return [r["target"] for r in g.relationships if r["source"] == source]


def _settle(vault: Path) -> None:
    """Backdate every file past the caches' racy window."""
    old = time.time() - 86_400
    for p in vault.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))


def kb(vault: Path, *args: str) -> Any:
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env={"SCOUT_READER": "owner"})


# ----- two-pass target resolution -----------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "[[Sam Rivera]]",  # display name
        "[[sam-rivera]]",  # filename stem
        "[[people/sam-rivera]]",  # path form
        "[[people/sam-rivera.md]]",  # path form with the extension
        "[[people/sam-rivera|Sam Rivera]]",  # pipe alias: the right side is the name
        "[[people/sam-rivera|Sammy R.]]",  # pipe alias: the left side as a slug
        "[[elsewhere|Sammy]]",  # pipe alias: the right side as a declared alias
        "[[Sammy]]",  # declared alias
    ],
)
def test_targets_resolve_to_the_display_name(tmp_path: Path, target: str) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/sam-rivera.md", "name: Sam Rivera\ntype: person\naliases: [Sammy]\n")
    # Alex sorts first: the target is resolved after every entity is loaded, not as files are walked.
    _note(vault, "people/alex.md", "name: Alex\ntype: person\n" + _rel(target))
    g = _graph(vault)
    assert _targets(g, "Alex") == ["Sam Rivera"]
    assert ("Sam Rivera", "works_with", "Alex") in {(r["source"], r["type"], r["target"]) for r in g.relationships}
    assert not [e for e in g.validate() if "Dangling" in e["message"]]


def test_a_shared_stem_stays_unresolved(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/sam.md", "name: Sam Rivera\ntype: person\n")
    _note(vault, "projects/sam.md", "name: Project Sam\ntype: project\nstatus: active\n")
    _note(vault, "people/alex.md", "name: Alex\ntype: person\n" + _rel("[[sam]]"))
    g = _graph(vault)
    assert _targets(g, "Alex") == ["sam"]
    assert any(
        e["message"] == "Dangling relationship target: works_with → 'sam' (no such entity)" for e in g.validate()
    )


def test_a_shared_alias_stays_unresolved_and_names_beat_aliases(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/sam.md", "name: Sam\ntype: person\naliases: [S, Priya]\n")
    _note(vault, "people/sam-two.md", "name: Sam Two\ntype: person\naliases: [S]\n")
    _note(vault, "people/priya.md", "name: Priya\ntype: person\n")
    _note(
        vault,
        "people/alex.md",
        "name: Alex\ntype: person\n" + _rel("[[S]]") + "  - type: works_with\n    target: '[[Priya]]'\n",
    )
    g = _graph(vault)
    assert sorted(_targets(g, "Alex")) == ["Priya", "S"]


# ----- archived snapshots ------------------------------------------------------


def test_archived_snapshots_do_not_shadow_the_live_entity(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    live = _note(vault, "projects/widget/widget.md", "name: Widget\ntype: project\nstatus: active\n")
    _note(vault, "projects/widget/archive/widget.md", "name: Widget\ntype: project\nstatus: stale\n")
    _note(vault, "projects/archive/old.md", "name: Old\ntype: project\nstatus: done\n")
    g = _graph(vault)
    assert set(g.entities) == {"Widget"}
    assert g.entities["Widget"]["_source_path"] == str(live)


@pytest.mark.parametrize("cwd, kb_root", [("vault", "knowledge-base"), ("vault/knowledge-base", ".")])
def test_exclude_globs_match_with_a_relative_kb_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd: str, kb_root: str
) -> None:
    vault = _vault(tmp_path)
    _note(vault, "projects/widget/widget.md", "name: Widget\ntype: project\nstatus: active\n")
    _note(vault, "projects/widget/archive/old.md", "name: Old Widget\ntype: project\nstatus: stale\n")
    monkeypatch.chdir(tmp_path / cwd)
    g = KnowledgeGraph(schema_path=_schema(vault), kb_root=kb_root, vault=vault).load()
    assert set(g.entities) == {"Widget"}


def test_the_schema_sets_its_own_exclude_globs(tmp_path: Path) -> None:
    vault = _vault(tmp_path, {"graph": {"exclude": ["snapshots/**"]}})
    _note(vault, "projects/widget/archive/widget.md", "name: Widget\ntype: project\nstatus: stale\n")
    _note(vault, "snapshots/alex.md", "name: Alex\ntype: person\n")
    assert set(_graph(vault).entities) == {"Widget"}


def test_an_empty_exclude_list_skips_nothing(tmp_path: Path) -> None:
    vault = _vault(tmp_path, {"graph": {"exclude": []}})
    _note(vault, "projects/widget/archive/widget.md", "name: Widget\ntype: project\nstatus: stale\n")
    assert set(_graph(vault).entities) == {"Widget"}


def test_a_malformed_exclude_is_a_schema_error(tmp_path: Path) -> None:
    vault = _vault(tmp_path, {"graph": {"exclude": "projects/**"}})
    with pytest.raises(KBSchemaError, match="graph.exclude"):
        _graph(vault)


# ----- malformed frontmatter ---------------------------------------------------

BROKEN = 'name: "Priya CANARY-7Q2X\ntype: person\n'


def test_malformed_frontmatter_is_reported_not_dropped(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/priya.md", BROKEN)
    _note(vault, "people/alex.md", "name: Alex\ntype: person\n")
    g = _graph(vault)
    (pe,) = g.parse_errors
    assert pe["path"] == "knowledge-base/people/priya.md"
    assert pe["_label"]["visibility"] == "private"  # malformed frontmatter is private
    assert "line " in pe["error"]
    assert "CANARY" not in pe["error"]  # PyYAML's quoted snippet is the note's own text
    (row,) = [e for e in g.validate() if e["entity"] == pe["path"]]
    assert row["message"] == f"YAML parse failure: {pe['error']}"


def test_parse_errors_survive_the_in_process_cache(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/priya.md", BROKEN)
    _settle(vault)
    cold = _graph(vault).parse_errors
    warm = _graph(vault).parse_errors
    assert cold == warm and len(warm) == 1


def test_parse_errors_survive_the_cross_process_index(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/priya.md", BROKEN)
    _note(vault, "people/alex.md", "name: Alex\ntype: person\n")
    _settle(vault)
    first = kb(vault, "validate", "--json")
    clear_frontmatter_cache()
    second = kb(vault, "validate", "--json")
    assert first.exit_code == second.exit_code == 0, first.output
    assert json.loads(first.stdout) == json.loads(second.stdout)
    (row,) = [e for e in json.loads(second.stdout)["errors"] if "YAML parse failure" in e["message"]]
    assert row["entity"] == "knowledge-base/people/priya.md"
    assert row["label"]["visibility"] == "private"
    text = kb(vault, "validate").stdout
    assert "  [private] [knowledge-base/people/priya.md] YAML parse failure: " in text
    assert "CANARY" not in text


# ----- dangling targets and the vault's exceptions ----------------------------


def _dangling_vault(tmp_path: Path, validation: dict[str, Any] | None) -> Path:
    vault = _vault(tmp_path, {"validation": validation} if validation is not None else None)
    rels = (
        "relationships:\n"
        "  - {type: works_with, target: '[[Priya]]'}\n"
        "  - {type: works_with, target: '[[Nobody]]'}\n"
        "  - {type: about, target: '[[Hosted Warehouse]]'}\n"
        "  - {type: about, target: '[[Sam]]'}\n"
        "  - {type: confirmed_on, target: '[[action-items-2026-01-05]]'}\n"
    )
    _note(vault, "people/alex.md", "name: Alex\ntype: person\n" + rels)
    _note(vault, "people/priya.md", "name: Priya\ntype: person\n" + _rel("[[Sam]]", "about"))
    return vault


def _dangling(errors: list[dict[str, str]]) -> list[str]:
    return sorted(e["message"].split("'")[1] for e in errors if e["message"].startswith("Dangling"))


def test_every_unresolved_target_is_an_error_without_a_validation_block(tmp_path: Path) -> None:
    g = _graph(_dangling_vault(tmp_path, None))
    assert _dangling(g.validate()) == ["Hosted Warehouse", "Nobody", "Sam", "Sam", "action-items-2026-01-05"]
    assert g.known_gaps == []


def test_the_vault_schema_lists_externals_gaps_and_provenance(tmp_path: Path) -> None:
    g = _graph(_dangling_vault(tmp_path, VALIDATION))
    errors = g.validate()
    assert _dangling(errors) == ["Nobody"]
    assert g.known_gaps == [
        {"entity": "Alex", "type": "about", "target": "Sam", "reason": "reviewed: a contact with no entity file"},
        {"entity": "Priya", "type": "about", "target": "Sam", "reason": "reviewed: a contact with no entity file"},
    ]


def test_validate_cli_reports_known_gaps_apart(tmp_path: Path) -> None:
    vault = _dangling_vault(tmp_path, VALIDATION)
    text = kb(vault, "validate").stdout
    assert "  [Alex] Dangling relationship target: works_with → 'Nobody' (no such entity)" in text
    assert "\n1 known entity gap(s) — accepted backlog, not errors:\n" in text
    assert "  'Sam' — reviewed: a contact with no entity file  (referenced by: Alex, Priya)" in text
    data = json.loads(kb(vault, "validate", "--json").stdout)
    assert [e["message"] for e in data["errors"] if "Dangling" in e["message"]] == [
        "Dangling relationship target: works_with → 'Nobody' (no such entity)"
    ]
    assert [(g["entity"], g["target"]) for g in data["known_gaps"]] == [("Alex", "Sam"), ("Priya", "Sam")]
    assert all(g["label"]["path"].startswith("knowledge-base/people/") for g in data["known_gaps"])


@pytest.mark.parametrize(
    "validation",
    [["x"], {"external_references": "x"}, {"known_gaps": ["x"]}, {"provenance_relationship_types": "x"}],
)
def test_a_malformed_validation_block_is_a_schema_error(tmp_path: Path, validation: Any) -> None:
    g = _graph(_dangling_vault(tmp_path, validation))
    with pytest.raises(KBSchemaError, match="validation"):
        g.validate()


# ----- birthdays ------------------------------------------------------------------


@pytest.mark.parametrize(("birthday", "month"), [("1990-07-16", 7), ("07-16", 7), ("--07-16", 7), ("12-01", 12)])
def test_birthday_month_reads_dated_and_yearless_birthdays(tmp_path: Path, birthday: str, month: int) -> None:
    vault = _vault(tmp_path)
    _note(vault, "people/alex.md", f"name: Alex\ntype: person\nbirthday: '{birthday}'\n")
    g = _graph(vault)
    assert [e["name"] for e in g.query(birthday_month=month)] == ["Alex"]
    assert g.query(birthday_month=month % 12 + 1) == []


# ----- typed name lookup ---------------------------------------------------------


def _lookup_vault(tmp_path: Path) -> Path:
    vault = _vault(tmp_path)
    _note(vault, "people/priya.md", "name: Priya\ntype: person\nvisibility: private\n")
    _note(vault, "orgs/acme-widgets.md", "name: Acme Widgets\ntype: organization\naliases: [Acme]\n")
    _note(vault, "projects/acme-portal.md", "name: Acme Portal\ntype: project\nstatus: active\n")
    return vault


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (None, {"person"}),
        ("", {"person"}),
        ("any", set()),
        ("ANY", set()),
        ("organization", {"organization"}),
        ("person, organization", {"person", "organization"}),
    ],
)
def test_parse_lookup_types(raw: str | None, want: set[str]) -> None:
    assert parse_lookup_types(raw) == want


def test_name_lookup_searches_the_types_asked_for(tmp_path: Path) -> None:
    g = _graph(_lookup_vault(tmp_path))
    assert g.name_lookup("Acme") == []  # people by default, as before
    (org,) = g.name_lookup("Acme", types={"organization"})
    assert (org["name"], org["type"], org["match_strength"], org["matched_field"]) == (
        "Acme Widgets",
        "organization",
        "exact",
        "alias",
    )
    assert {c["name"] for c in g.name_lookup("Acme Portal", types=set())} >= {"Acme Portal"}
    (person,) = g.name_lookup("Priya")
    assert person["type"] == "person" and person["_label"]["visibility"] == "private"


def test_name_lookup_cli_lookup_type(tmp_path: Path) -> None:
    vault = _lookup_vault(tmp_path)
    miss = kb(vault, "name-lookup", "Acme").stdout
    assert "searched: person" in miss and "--lookup-type any" in miss
    hit = kb(vault, "name-lookup", "Acme", "--lookup-type", "any")
    assert hit.exit_code == 0, hit.output
    assert "  [exact] Acme Widgets (slug=acme-widgets, type=organization, matched_field=alias, score=1.0)" in hit.stdout
    assert "candidate(s) — searched: any type." in hit.stdout
    rows = json.loads(kb(vault, "name-lookup", "--token", "Acme", "--lookup-type", "organization", "--json").stdout)
    assert [(r["name"], r["type"], r["path"]) for r in rows] == [
        ("Acme Widgets", "organization", "knowledge-base/orgs/acme-widgets.md")
    ]
    private = kb(vault, "name-lookup", "Priya").stdout
    assert "  [private] [exact] Priya (slug=priya, type=person, " in private


# ----- name lookup by slug and name word -----------------------------------------
# Made-up names: a transcript says one word of a multi-word name ("Bramble" for
# "Bramble Holdings"), and the whole-name ratio alone misses it or, worse, picks a
# different entity whose short name happens to score higher.


def _words_vault(tmp_path: Path, graph: dict[str, Any] | None = None) -> Path:
    vault = _vault(tmp_path, {"graph": graph} if graph is not None else None)
    _note(vault, "orgs/bramble-holdings.md", "name: Bramble Holdings\ntype: organization\n")
    _note(vault, "orgs/larkspur-partners.md", "name: Larkspur Partners\ntype: organization\n")
    _note(vault, "projects/larkspur-star.md", "name: Larkspur Star\ntype: project\nstatus: active\n")
    _note(vault, "orgs/velora-foods.md", "name: Velora Foods\ntype: organization\n")
    _note(vault, "orgs/valora.md", "name: Valora\ntype: organization\n")
    _note(vault, "orgs/quillco.md", "name: The Quill Company\ntype: organization\n")
    _note(vault, "orgs/larch-corp.md", "name: Larch Corp.\ntype: organization\n")
    _note(vault, "people/alex-rivera.md", "name: Alex Rivera\ntype: person\n")
    _note(vault, "meetings/alex-lunch.md", "name: Alex Lunch Notes\ntype: meeting\n")
    _note(vault, "projects/juniper.md", "name: Juniper\ntype: project\nstatus: active\n")
    _note(vault, "orgs/juniper-labs.md", "name: Juniper Labs\ntype: organization\n")
    return vault


def _hits(g: KnowledgeGraph, token: str, types: set[str] | None = None) -> list[tuple[str, str, str]]:
    return [(c["name"], c["match_strength"], c["matched_field"]) for c in g.name_lookup(token, types=types)]


def test_one_word_of_a_multi_word_name_is_an_exact_match(tmp_path: Path) -> None:
    g = _graph(_words_vault(tmp_path))
    assert _hits(g, "bramble", {"organization"}) == [("Bramble Holdings", "exact", "name-word")]


def test_a_name_word_beats_a_higher_scoring_different_entity(tmp_path: Path) -> None:
    g = _graph(_words_vault(tmp_path))
    # "Valora" scores 0.83 against the whole name; "Velora Foods" only 0.67.
    assert _hits(g, "Velora", {"organization"})[0] == ("Velora Foods", "exact", "name-word")


def test_the_file_slug_is_an_exact_match(tmp_path: Path) -> None:
    g = _graph(_words_vault(tmp_path))
    assert _hits(g, "QuillCo", {"organization"}) == [("The Quill Company", "exact", "slug")]


@pytest.mark.parametrize("token", ["Partners", "holdings", "Company", "Corp", "corp."])
def test_corporate_suffixes_never_match_alone(tmp_path: Path, token: str) -> None:
    g = _graph(_words_vault(tmp_path))
    assert [h for h in _hits(g, token, set()) if h[1] == "exact"] == []


def test_short_words_never_match_alone(tmp_path: Path) -> None:
    vault = _words_vault(tmp_path)
    _note(vault, "orgs/oak-ridge.md", "name: Oak Ridge\ntype: organization\n")
    assert [h for h in _hits(_graph(vault), "Oak", set()) if h[1] == "exact"] == []


def test_people_and_orgs_outrank_other_types_on_the_same_field(tmp_path: Path) -> None:
    g = _graph(_words_vault(tmp_path))
    assert _hits(g, "Larkspur", set())[:2] == [
        ("Larkspur Partners", "exact", "name-word"),
        ("Larkspur Star", "exact", "name-word"),
    ]
    assert _hits(g, "Alex", set())[:2] == [
        ("Alex Rivera", "exact", "name-word"),
        ("Alex Lunch Notes", "exact", "name-word"),
    ]


def test_the_whole_name_outranks_a_name_word_whatever_the_type(tmp_path: Path) -> None:
    g = _graph(_words_vault(tmp_path))
    assert _hits(g, "Juniper", set())[:2] == [
        ("Juniper", "exact", "name"),
        ("Juniper Labs", "exact", "name-word"),
    ]


def test_an_alias_outranks_a_name_word(tmp_path: Path) -> None:
    vault = _words_vault(tmp_path)
    _note(vault, "orgs/hollow.md", "name: Hollow Works\ntype: organization\naliases: [Tamsin]\n")
    _note(vault, "orgs/tamsin-mills.md", "name: Tamsin Mills\ntype: organization\n")
    assert _hits(_graph(vault), "Tamsin", {"organization"}) == [
        ("Hollow Works", "exact", "alias"),
        ("Tamsin Mills", "exact", "name-word"),
    ]


def test_fuzzy_candidates_stay_ordered_by_score(tmp_path: Path) -> None:
    # Field rank breaks ties between exact matches only: a closer alias beats a
    # farther whole name.
    vault = _vault(tmp_path)
    _note(vault, "people/sam.md", "name: Sam\ntype: person\naliases: [Samwise]\n")
    _note(vault, "people/samwell.md", "name: Samwell\ntype: person\n")
    rows = _graph(vault).name_lookup("Samwyse")
    assert [(c["name"], c["matched_field"], c["score"]) for c in rows] == [
        ("Sam", "alias", 0.857),
        ("Samwell", "name", 0.714),
    ]


def test_the_schema_adds_name_words_that_never_match_alone(tmp_path: Path) -> None:
    assert _hits(_graph(_words_vault(tmp_path)), "Foods", {"organization"}) == [("Velora Foods", "exact", "name-word")]
    g = _graph(_words_vault(tmp_path / "b", {"name_word_stoplist": ["Foods"]}))
    assert [h for h in _hits(g, "Foods", {"organization"}) if h[1] == "exact"] == []
    # The defaults still apply alongside the vault's own words.
    assert [h for h in _hits(g, "Partners", {"organization"}) if h[1] == "exact"] == []


@pytest.mark.parametrize("stoplist", ["foods", {"foods": 1}, [["foods"]]])
def test_a_malformed_name_word_stoplist_is_a_schema_error(tmp_path: Path, stoplist: Any) -> None:
    g = _graph(_words_vault(tmp_path, {"name_word_stoplist": stoplist}))
    with pytest.raises(KBSchemaError, match="graph.name_word_stoplist"):
        g.name_lookup("Velora", types={"organization"})


def test_name_lookup_cli_reports_a_name_word_match(tmp_path: Path) -> None:
    out = kb(_words_vault(tmp_path), "name-lookup", "Bramble", "--lookup-type", "organization")
    assert out.exit_code == 0, out.output
    want = "  [exact] Bramble Holdings (slug=bramble-holdings, type=organization, matched_field=name-word, score=1.0)"
    assert want in out.stdout

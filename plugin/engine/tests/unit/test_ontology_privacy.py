"""Labelled KnowledgeGraph over the privacy fixture vault (#453, spec §6.1, §11.2 "Ontology").

The engine's ``KnowledgeGraph`` is the one implementation of the vault parser's
walks: ``name_lookup``, ``traverse``, ``path`` and ``export_json`` are ported
from ``templates/knowledge-base/ontology/parser.py`` (the reference here: the
parity tests run it on the same vault), and every entity carries ``_label``,
the same label ``scoutctl kb classify`` gives its file. Readers: ``owner`` and
``autonomous`` get every entity, labelled; any other reader loads nothing.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scout.errors import KBError
from scout.kb import ontology as ontology_mod
from scout.kb import privacy
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache
from scout.kb.privacy import label_file, load_policy
from tests.unit.privacy_vault_helpers import (
    CANARIES,
    COMMITS,
    ENGINE_SCHEMA,
    GROUP_CHAT_ID,
    PRIVATE_CHAT_ID,
    SEED_SUBJECT,
    STAND_IN_BOT_TOKEN,
    action_items_rel,
    build_privacy_vault,
    git,
    privacy_secrets,
)

TEMPLATE_PARSER = Path(__file__).resolve().parents[3] / "templates" / "knowledge-base" / "ontology" / "parser.py"
AGENT = "agent:person:priya@example.com"


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One built vault for the tests that only read it."""
    return build_privacy_vault(tmp_path_factory.mktemp("privacy"))


@pytest.fixture
def vault(shared_vault: Path) -> Path:
    clear_frontmatter_cache()
    return shared_vault


@pytest.fixture
def own_vault(tmp_path: Path) -> Path:
    """A vault of the test's own, for tests that change files."""
    clear_frontmatter_cache()
    return build_privacy_vault(tmp_path)


def _graph(vault: Path, reader: str = "owner", kb: str = "knowledge-base") -> KnowledgeGraph:
    return KnowledgeGraph(str(ENGINE_SCHEMA), str(vault / kb), reader=reader).load()


def _template(vault: Path) -> Any:
    spec = importlib.util.spec_from_file_location("privacy_template_parser", TEMPLATE_PARSER)
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KnowledgeGraph(str(ENGINE_SCHEMA), str(vault / "knowledge-base")).load()


def _strip(value: Any) -> Any:
    """``value`` without ``_label`` keys, at any depth (for parity checks)."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != "_label"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _settle(vault: Path) -> None:
    """Backdate every file past the cache's racy window."""
    old = time.time() - 86_400
    for p in vault.rglob("*"):
        if p.is_file() and not p.is_symlink():
            os.utime(p, (old, old))


# ----- the fixture vault ---------------------------------------------------------


def test_fixture_vault_history_and_dates(vault: Path) -> None:
    subjects = git(vault, "log", "--format=%s").splitlines()
    assert subjects == [s for s, _ in reversed(COMMITS)] + [SEED_SUBJECT]
    for n, (subject, rels) in enumerate(COMMITS):
        rev = f"HEAD~{len(COMMITS) - 1 - n}"
        changed = git(vault, "show", "--name-only", "--format=", rev).split()
        assert sorted(changed) == sorted(rels), subject
    assert git(vault, "status", "--porcelain") == ""

    today = date.today()
    assert (vault / action_items_rel(today)).is_file()
    permit = _graph(vault).entity("task-renew-permit")
    assert permit is not None
    (window,) = permit["surface_rule"]["windows"]
    assert window["from"] <= today <= window["to"]


def test_fixture_canaries_are_unique_and_in_place(vault: Path) -> None:
    assert len(set(CANARIES.values())) == len(CANARIES)
    every = {
        p.relative_to(vault).as_posix(): p
        for p in vault.rglob("*")
        if p.is_file() and p.relative_to(vault).parts[0] not in (".git", "knowledge_base")
    }
    assert set(CANARIES) <= set(every)
    for rel, path in every.items():
        text = path.read_text(encoding="utf-8")
        found = {c for c in CANARIES.values() if c in text}
        assert found == ({CANARIES[rel]} if rel in CANARIES else set()), rel
        if rel in CANARIES and rel.endswith(".md"):
            heading = next(line for line in text.splitlines() if line.startswith("# "))
            assert CANARIES[rel] in heading, rel  # title-adjacent
            assert text.count(CANARIES[rel]) >= 3, rel  # title, heading, body


def test_fixture_labels_match_the_spec_table(vault: Path) -> None:
    policy = load_policy(vault)
    expect = {
        "knowledge-base/personal/task-renew-permit.md": ("private", "path_default"),
        "knowledge-base/personal/task-buy-gift.md": ("normal", "frontmatter"),
        "knowledge-base/people/sam.md": ("private", "frontmatter"),
        "knowledge-base/people/priya.md": ("normal", "default"),
        "knowledge-base/topics/billing/billing.md": ("private", "frontmatter"),
        "knowledge-base/topics/billing/rollout.md": ("normal", "default"),
        "knowledge-base/projects/widget/widget.md": ("normal", "default"),
        "knowledge-base/projects/widget/notes.md": ("normal", "default"),
        "drafts/reply-to-priya.md": ("normal", "default"),
        "knowledge-base/topics/misc/draft.md": ("private", "frontmatter"),
        "knowledge-base/topics/misc/broken.md": ("private", "forced"),
        "knowledge-base/topics/misc/plain.md": ("normal", "default"),
        ".scout-inbox/phone-notifications-2026-10-01.jsonl": ("private", "path_default"),
        "inbox.md": ("normal", "default"),
        "Raw/scan.txt": ("normal", "default"),
    }
    for rel, (vis, source) in expect.items():
        label = label_file(vault, rel, policy)
        assert (label.visibility, label.visibility_source) == (vis, source), rel
        assert not label.local_only, rel
    assert label_file(vault, "trading/positions.md", policy).local_only
    assert label_file(vault, "drafts/reply-to-priya.md", policy).shared_file
    assert label_file(vault, "knowledge-base/projects/widget/widget.md", policy).shared_file
    assert label_file(vault, "knowledge-base/projects/widget/notes.md", policy).federation_scope_declared == (
        "person:priya@example.com",
    )


# ----- labels on entities --------------------------------------------------------


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
def test_personal_tasks_labelled_for_autonomous(vault: Path, reader: str) -> None:
    """#453's first acceptance line, as amended: both personal tasks, the permit one private."""
    results = _graph(vault, reader).query(type="task", domain="personal")
    by_name = {r["name"]: r["_label"] for r in results}
    assert set(by_name) == {"task-renew-permit", "task-buy-gift"}
    permit = by_name["task-renew-permit"]
    assert permit["visibility"] == "private"
    assert permit["visibility_source"] == "path_default"
    assert permit["path"] == "knowledge-base/personal/task-renew-permit.md"
    assert by_name["task-buy-gift"]["visibility"] == "normal"
    assert by_name["task-buy-gift"]["visibility_source"] == "frontmatter"


def test_relative_kb_root_labels_the_same(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {n: e["_label"] for n, e in _graph(vault).entities.items()}
    monkeypatch.chdir(vault / "knowledge-base")
    got = KnowledgeGraph(str(ENGINE_SCHEMA), ".").load().entities
    assert {n: e["_label"] for n, e in got.items()} == expected


def test_symlinked_note_is_labelled_by_its_target(own_vault: Path) -> None:
    """A note reached through a link gets its target's label, as ``kb classify`` resolves it."""
    kb = own_vault / "knowledge-base"
    (kb / "people" / "permit-link.md").symlink_to(kb / "personal" / "task-renew-permit.md")
    (kb / "linked").symlink_to(kb / "personal", target_is_directory=True)
    outside = own_vault.parent / "outside.md"
    outside.write_text("---\nname: x\ntype: person\n---\n", encoding="utf-8")
    (kb / "people" / "outside-link.md").symlink_to(outside)

    labeller = ontology_mod._Labeller(str(kb))
    target = "knowledge-base/personal/task-renew-permit.md"
    assert labeller.rel(kb / "people" / "permit-link.md") == target
    assert labeller.rel(kb / "linked" / "task-renew-permit.md") == target
    assert labeller.rel(kb / "personal" / "task-renew-permit.md") == target
    # A link out of the vault keeps its in-KB spelling (normalize_rel refuses it).
    assert labeller.rel(kb / "people" / "outside-link.md") == "knowledge-base/people/outside-link.md"
    # Whichever spelling the walk indexed last, the entity carries the target's label.
    label = _graph(own_vault).label_of("task-renew-permit")
    assert label is not None and label["path"] == target and label["visibility"] == "private"


@pytest.mark.parametrize("kb", ["knowledge-base", "knowledge_base"])
def test_every_entity_label_matches_kb_classify(vault: Path, kb: str) -> None:
    """The graph labels a file exactly as ``kb classify`` does, whichever spelling the KB root has."""
    g = _graph(vault, kb=kb)
    policy = load_policy(vault)
    assert set(g.entities) == {"task-renew-permit", "task-buy-gift", "sam", "priya", "widget"}
    for name, entity in g.entities.items():
        label = entity["_label"]
        assert label == label_file(vault, label["path"], policy).to_json(), name
        assert label["path"].startswith("knowledge-base/"), name
        assert g.label_of(name) == label


def test_query_visibility_filter(vault: Path) -> None:
    g = _graph(vault, "autonomous")
    assert {e["name"] for e in g.query(visibility="private")} == {"task-renew-permit", "sam"}
    assert {e["name"] for e in g.query(visibility="normal")} == {"task-buy-gift", "priya", "widget"}
    assert {e["name"] for e in g.query(visibility="private", type="person")} == {"sam"}
    assert len(g.query()) == 5
    assert g.query(visibility="secret") == []


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
def test_traverse_includes_private_node_with_label(vault: Path, reader: str) -> None:
    g = _graph(vault, reader)
    reached = {r["entity"]: r for r in g.traverse("priya", max_hops=2)}
    assert reached["sam"]["_label"]["visibility"] == "private"
    assert reached["sam"]["hops"] == 1
    assert reached["widget"]["_label"]["visibility"] == "normal"
    # From the private node itself, the walk goes on.
    assert {r["entity"] for r in g.traverse("sam", max_hops=1)} == {"widget", "priya"}


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
def test_related_and_path_include_private_node(vault: Path, reader: str) -> None:
    g = _graph(vault, reader)
    assert {"source": "priya", "type": "works_with", "target": "sam"} in g.related("priya")
    assert g.path("task-buy-gift", "sam") == [("about", "priya"), ("works_with", "sam")]
    label = g.label_of("sam")
    assert label is not None and label["visibility"] == "private"
    assert g.label_of("nobody") is None


def test_name_lookup_labels_candidates(vault: Path) -> None:
    g = _graph(vault)
    (hit,) = [c for c in g.name_lookup("Sammy") if c["name"] == "sam"]
    assert hit["match_strength"] == "exact"
    assert hit["matched_field"] == "alias"
    assert hit["slug"] == "sam"
    assert hit["_label"]["visibility"] == "private"


def test_export_json_carries_labels_not_source_paths(vault: Path) -> None:
    payload = json.loads(_graph(vault).export_json())
    assert set(payload) == {"entities", "relationships"}
    for name, entity in payload["entities"].items():
        assert entity["_label"]["path"].startswith("knowledge-base/"), name
        assert "_source_path" not in entity
    assert payload["entities"]["sam"]["_label"]["visibility"] == "private"


# ----- non-full readers ----------------------------------------------------------


@pytest.mark.parametrize("reader", [AGENT, "agent:group:team-1", "agent:invalid", "indexer", ""])
def test_agent_reader_loads_nothing(vault: Path, reader: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent reader gets an empty graph, and no file is opened or listed for it."""

    def refuse(*_a: object, **_k: object) -> Any:
        raise AssertionError("a file was touched for a reader that sees nothing")

    for target, attr in [
        (builtins, "open"),
        (io, "open"),
        (os, "scandir"),
        (os, "listdir"),
        (Path, "open"),
        (Path, "read_bytes"),
        (Path, "read_text"),
        (Path, "rglob"),
        (Path, "stat"),
        (privacy, "load_policy"),
        (ontology_mod, "load_policy"),
    ]:
        monkeypatch.setattr(target, attr, refuse)

    g = KnowledgeGraph(str(ENGINE_SCHEMA), str(vault / "knowledge-base"), reader=reader).load()
    out = [
        g.entities,
        g.relationships,
        g.query(),
        g.query(type="task", domain="personal"),
        g.query(visibility="private"),
        g.related("sam"),
        g.traverse("priya"),
        g.name_lookup("Sam"),
        g.validate(),
    ]
    monkeypatch.undo()
    assert g.path("priya", "widget") is None
    assert g.entity("sam") is None
    assert all(not o for o in out)
    exported = g.export_json()
    assert json.loads(exported) == {"entities": {}, "relationships": []}
    for canary in CANARIES.values():
        assert canary not in exported


# ----- the stat cache (#77) keeps labels cheap -----------------------------------


def _watch_kb_reads(monkeypatch: pytest.MonkeyPatch, vault: Path) -> list[str]:
    """Record every open of a file under the vault's KB, by any of the usual routes."""
    kb_dirs = (str(vault / "knowledge-base"), str((vault / "knowledge-base").resolve()), str(vault / "knowledge_base"))
    reads: list[str] = []

    def note(target: object) -> None:
        name = os.fspath(target) if isinstance(target, (str, os.PathLike)) else ""
        if isinstance(name, str) and name.startswith(kb_dirs):
            reads.append(name)

    real_builtin, real_io = builtins.open, io.open
    real_path_open, real_bytes, real_text = Path.open, Path.read_bytes, Path.read_text

    def b_open(file: Any, *a: Any, **k: Any) -> Any:
        note(file)
        return real_builtin(file, *a, **k)

    def i_open(file: Any, *a: Any, **k: Any) -> Any:
        note(file)
        return real_io(file, *a, **k)

    def p_open(self: Path, *a: Any, **k: Any) -> Any:
        note(self)
        return real_path_open(self, *a, **k)

    def p_bytes(self: Path) -> bytes:
        note(self)
        return real_bytes(self)

    def p_text(self: Path, *a: Any, **k: Any) -> str:
        note(self)
        return real_text(self, *a, **k)

    monkeypatch.setattr(builtins, "open", b_open)
    monkeypatch.setattr(io, "open", i_open)
    monkeypatch.setattr(Path, "open", p_open)
    monkeypatch.setattr(Path, "read_bytes", p_bytes)
    monkeypatch.setattr(Path, "read_text", p_text)
    return reads


def test_kb_read_watch_sees_every_route(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The spy below is only as good as its coverage: each route is seen."""
    note = own_vault / "knowledge-base" / "people" / "priya.md"
    reads = _watch_kb_reads(monkeypatch, own_vault)
    with open(note, "rb"):
        pass
    with note.open("rb"):
        pass
    note.read_bytes()
    note.read_text(encoding="utf-8")
    privacy.read_head(note)
    monkeypatch.undo()
    assert len(reads) >= 5


def test_warm_load_reads_no_file_and_keeps_labels(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _settle(own_vault)
    cold = _graph(own_vault)
    reads = _watch_kb_reads(monkeypatch, own_vault)
    warm = _graph(own_vault)
    monkeypatch.undo()
    assert reads == []
    assert warm.entities == cold.entities
    assert warm.export_json() == cold.export_json()


def test_policy_change_relabels_a_warm_load(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache holds the file's facts, not its label: a new overlay applies without a re-read."""
    _settle(own_vault)
    assert _graph(own_vault).entities["priya"]["_label"]["visibility"] == "normal"
    (own_vault / "scout-config.yaml").write_text(PEOPLE_PRIVATE_OVERLAY, encoding="utf-8")
    reads = _watch_kb_reads(monkeypatch, own_vault)
    label = _graph(own_vault).entities["priya"]["_label"]
    monkeypatch.undo()
    assert reads == []
    assert (label["visibility"], label["visibility_source"]) == ("private", "path_default")


# ----- the vault a graph labels against ------------------------------------------

PEOPLE_PRIVATE_OVERLAY = (
    "privacy:\n  local_only_add: ['trading/**']\n"
    "  visibility_defaults:\n    - {glob: 'knowledge-base/people/**', visibility: private}\n"
)


@pytest.mark.parametrize("kb", ["knowledge-base/personal", "knowledge_base/personal"])
def test_graph_rooted_at_a_subfolder_labels_against_the_vault(vault: Path, kb: str) -> None:
    g = _graph(vault, kb=kb)
    label = g.label_of("task-renew-permit")
    assert label is not None
    assert label == label_file(vault, "knowledge-base/personal/task-renew-permit.md", load_policy(vault)).to_json()
    assert (label["visibility"], label["visibility_source"]) == ("private", "path_default")


def test_graph_rooted_at_a_subfolder_applies_the_overlay(own_vault: Path) -> None:
    (own_vault / "scout-config.yaml").write_text(PEOPLE_PRIVATE_OVERLAY, encoding="utf-8")
    g = _graph(own_vault, kb="knowledge-base/people")
    assert set(g.entities) == {"sam", "priya"}
    policy = load_policy(own_vault)
    for name, entity in g.entities.items():
        assert entity["_label"] == label_file(own_vault, entity["_label"]["path"], policy).to_json(), name
    assert g.label_of("priya") == {**g.label_of("priya"), "visibility": "private", "visibility_source": "path_default"}


def test_find_vault(vault: Path, tmp_path: Path) -> None:
    assert ontology_mod.find_vault(vault / "knowledge-base") == vault.resolve()
    assert ontology_mod.find_vault(vault / "knowledge_base" / "people") == vault.resolve()
    assert ontology_mod.find_vault(vault / "knowledge-base" / "topics" / "billing") == vault.resolve()
    bare = tmp_path / "loose"
    (bare / "notes").mkdir(parents=True)
    with pytest.raises(KBError):
        ontology_mod.find_vault(bare / "notes")
    with pytest.raises(KBError):
        KnowledgeGraph(str(ENGINE_SCHEMA), str(bare / "notes")).load()
    # A vault named explicitly is used as given; one that doesn't hold the root is refused.
    assert KnowledgeGraph(str(ENGINE_SCHEMA), str(bare / "notes"), vault=bare).load().entities == {}
    with pytest.raises(ValueError):
        KnowledgeGraph(str(ENGINE_SCHEMA), str(vault / "knowledge-base"), vault=bare).load()


def test_scout_config_marks_a_vault_without_a_kb_dir(tmp_path: Path) -> None:
    (tmp_path / "v" / "kb").mkdir(parents=True)
    (tmp_path / "v" / "scout-config.yaml").write_text("user: {name: Alex}\n", encoding="utf-8")
    assert ontology_mod.find_vault(tmp_path / "v" / "kb") == (tmp_path / "v").resolve()


# ----- the graph labels edge-case files exactly as kb classify does -------------


@pytest.mark.parametrize(
    ("filename", "raw", "expect"),
    [
        ("crlf.md", b"---\r\nname: crlf\r\ntype: person\r\nvisibility: private\r\n---\r\nBody.\r\n", "frontmatter"),
        # The classifier reads 64 KiB: a fence closing past it is unparseable, so forced private.
        (
            "big.md",
            b"---\nname: big\ntype: person\nnotes: '" + b"x" * (70 * 1024) + b"'\n---\nBody.\n",
            "forced",
        ),
        ("cr.md", b'---\nname: cr\ntype: person\nnote: "a\rb"\nvisibility: normal\n---\n', "frontmatter"),
        # U+2028 splits a line for the classifier (splitlines) but not for the entity
        # parse, so the graph can't reuse its parse and must classify the head itself.
        (
            "ls.md",
            '---\nname: ls\ntype: person\ntitle: "a b"\nvisibility: private\n---\n'.encode(),
            "frontmatter",
        ),
        ("bad-byte.md", b"---\nname: bad\ntype: person\n---\n\xff\n", "default"),
    ],
)
def test_edge_case_entity_label_matches_label_file(tmp_path: Path, filename: str, raw: bytes, expect: str) -> None:
    clear_frontmatter_cache()
    own_vault = tmp_path / "vault"
    path = own_vault / "knowledge-base" / "people" / filename
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    g = _graph(own_vault)
    policy = load_policy(own_vault)
    rel = f"knowledge-base/people/{filename}"
    matches = [e for e in g.entities.values() if e["_source_path"] == str(path)]
    if filename == "bad-byte.md":
        assert matches == []  # strict UTF-8 for the entity: not indexed (unchanged behaviour)
        assert label_file(own_vault, rel, policy).visibility == "normal"
        return
    (entity,) = matches
    assert entity["_label"] == label_file(own_vault, rel, policy).to_json()
    assert entity["_label"]["visibility_source"] == expect
    if expect == "forced":
        assert entity["_label"]["visibility"] == "private"
        assert entity["_label"]["warnings"] == ["frontmatter-unparseable"]


def test_privacy_secrets_are_where_notify_telegram_reads_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import notify_telegram

    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = privacy_secrets(tmp_path, GROUP_CHAT_ID)
    assert secrets == notify_telegram.secrets_dir()
    assert notify_telegram._read_secret("telegram-chat-id") == GROUP_CHAT_ID
    assert notify_telegram._read_secret("telegram-bot-token") == STAND_IN_BOT_TOKEN
    privacy_secrets(tmp_path)
    assert notify_telegram._read_secret("telegram-chat-id") == PRIVATE_CHAT_ID
    assert int(PRIVATE_CHAT_ID) > 0 > int(GROUP_CHAT_ID)
    for f in secrets.iterdir():
        assert f.stat().st_mode & 0o777 == 0o600


def test_policy_is_loaded_once_per_load(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    real = ontology_mod.load_policy

    def counting(v: Path | None) -> privacy.Policy:
        calls.append(v)
        return real(v)

    monkeypatch.setattr(ontology_mod, "load_policy", counting)
    _graph(vault)
    assert calls == [vault.resolve()]


# ----- parity with the vault parser (the reference implementation) --------------


@pytest.fixture
def pair(vault: Path) -> tuple[KnowledgeGraph, Any]:
    return _graph(vault), _template(vault)


def test_parity_entities_relationships_validate(pair: tuple[KnowledgeGraph, Any]) -> None:
    engine, template = pair
    assert _strip(engine.entities) == template.entities
    assert engine.relationships == template.relationships
    assert engine.validate() == template.validate()


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"type": "task"},
        {"type": "task", "domain": "personal"},
        {"type": "person"},
        {"status": "open"},
        {"exclude_status": "open"},
        {"exclude_status": "completed,active"},
        {"exclude_status": ["open"]},
        {"deadline_before": (date.today() + timedelta(days=30)).isoformat()},
        {"deadline_before": "2000-01-01"},
    ],
)
def test_parity_query(pair: tuple[KnowledgeGraph, Any], filters: dict[str, Any]) -> None:
    engine, template = pair
    assert _strip(engine.query(**filters)) == template.query(**filters)


@pytest.mark.parametrize("token", ["Sam", "sammy", "Pryia", "priya", "Prya", "nobody", "", "  "])
@pytest.mark.parametrize("threshold", [0.7, 0.5])
def test_parity_name_lookup(pair: tuple[KnowledgeGraph, Any], token: str, threshold: float) -> None:
    engine, template = pair
    assert _strip(engine.name_lookup(token, distance_threshold=threshold)) == template.name_lookup(
        token, distance_threshold=threshold
    )
    assert _strip(engine.name_lookup(token, max_results=1)) == template.name_lookup(token, max_results=1)


@pytest.mark.parametrize("hops", [1, 2, 3])
@pytest.mark.parametrize("rels", [None, ["works_with"], ["works_on", "worked_on_by"]])
def test_parity_traverse(pair: tuple[KnowledgeGraph, Any], hops: int, rels: list[str] | None) -> None:
    engine, template = pair
    for start in [*engine.entities, "nobody"]:
        assert _strip(engine.traverse(start, max_hops=hops, rel_types=rels)) == template.traverse(
            start, max_hops=hops, rel_types=rels
        ), start


@pytest.mark.parametrize("hops", [1, 2, 6])
def test_parity_path(pair: tuple[KnowledgeGraph, Any], hops: int) -> None:
    engine, template = pair
    names = [*engine.entities, "nobody"]
    for source, target in itertools.product(names, names):
        assert engine.path(source, target, max_hops=hops) == template.path(source, target, max_hops=hops), (
            source,
            target,
        )


def test_parity_export(pair: tuple[KnowledgeGraph, Any]) -> None:
    engine, template = pair
    assert _strip(json.loads(engine.export_json())) == json.loads(template.export_json())
    assert _strip(json.loads(engine.export_json(indent=4))) == json.loads(template.export_json(indent=4))


def test_parity_cli_export_through_the_template(vault: Path) -> None:
    """The rendered parser's own CLI, run the way a vault runs it, agrees with the engine port."""
    out = subprocess.run(
        [
            sys.executable,
            str(TEMPLATE_PARSER),
            "export",
            "--schema",
            str(ENGINE_SCHEMA),
            "--kb-root",
            str(vault / "knowledge-base"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert _strip(json.loads(_graph(vault).export_json())) == json.loads(out)

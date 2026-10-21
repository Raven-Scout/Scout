"""The privacy contract corpus: ``classify`` is the judge (#453, spec §9).

``tests/fixtures/contract/privacy-corpus.json`` is the CANONICAL set of
``{path, frontmatter_text, policy_overlay, expected_label}`` cases. The Agnes
indexer runs the same file against its own classifier, so a label here is a
label there. The corpus is checksum-guarded like ``parser-corpus.json``.

Intentional corpus changes: edit the corpus, set ``EXPECTED_SHA256`` below to
its new ``shasum -a 256``, and regenerate ``scout/privacy.snapshot.json`` if
the policy changed -- all in one commit -- then tell the Agnes indexer owners.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from scout.kb import read as kb_read
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache
from scout.kb.privacy import classify, policy_from_overlay
from tests.unit.test_kb_privacy import _ROWS

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "privacy-corpus.json"

EXPECTED_SHA256 = "14fa35bd828e8fcaefa8295e8fc2caac44f35e8d36bc2a19f03fa37eb12b4f2e"

_CASES: list[dict[str, Any]] = json.loads(CORPUS.read_text(encoding="utf-8")) if CORPUS.exists() else []


def _id(case: dict[str, Any]) -> str:
    overlay = "+overlay" if case["policy_overlay"] is not None else ""
    return f"{case['path']}{overlay}"


@pytest.mark.parametrize("case", _CASES, ids=[_id(c) for c in _CASES])
def test_classify_matches_expected_label(case: dict[str, Any]) -> None:
    assert set(case) == {"path", "frontmatter_text", "policy_overlay", "expected_label"}
    policy = policy_from_overlay(case["policy_overlay"])
    label = classify(case["path"], case["frontmatter_text"], policy)
    assert label.to_json() == case["expected_label"]


def test_corpus_has_a_case_per_classifier_row() -> None:
    have = {(c["path"], c["frontmatter_text"]) for c in _CASES if c["policy_overlay"] is None}
    missing = [row_id for row_id, rel, text, _ in _ROWS if (rel, text) not in have]
    assert missing == []


def test_corpus_exercises_overlays() -> None:
    overlays = [c for c in _CASES if c["policy_overlay"] is not None]
    assert len(overlays) >= 6


def test_corpus_file_format() -> None:
    raw = CORPUS.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"


def test_corpus_matches_canonical_checksum() -> None:
    actual = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert actual == EXPECTED_SHA256, (
        f"privacy-corpus.json drifted from the canonical digest (got {actual}). If this "
        f"change is intentional: update EXPECTED_SHA256 here (and the snapshot, if the policy "
        f"changed) in the same commit; the Agnes indexer runs this corpus too."
    )


# ----- labels never use libyaml (ruling R15c) -----------------------------------------
#
# Labels are what pure ``yaml.safe_load`` gives (the Agnes indexer mirrors it).
# libyaml's CSafeLoader disagrees with it on inputs no guard can enumerate, so
# nothing on the label path may use it: with ``yaml.CSafeLoader`` made to raise,
# every corpus case and the graph's labels still come out as expected.

_LOADER_EDGE_CASES = [
    "knowledge-base/topics/misc/tab-after-colon.md",
    "knowledge-base/topics/misc/surrogate-escape.md",
    "knowledge-base/topics/misc/bom-line-start.md",
    "knowledge-base/topics/misc/bom-own-line.md",
    "knowledge-base/topics/misc/block-scalar-comment.md",
    "knowledge-base/topics/misc/flow-explicit-key.md",
]


class _NoLibyaml:
    def __init__(self, *_: Any, **__: Any) -> None:
        raise AssertionError("yaml.CSafeLoader used on the label path")


@pytest.fixture
def no_libyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yaml, "CSafeLoader", _NoLibyaml, raising=False)
    monkeypatch.setattr(yaml, "CLoader", _NoLibyaml, raising=False)


@pytest.mark.parametrize("case", _CASES, ids=[_id(c) for c in _CASES])
def test_labels_never_use_libyaml(case: dict[str, Any], no_libyaml: None) -> None:
    policy = policy_from_overlay(case["policy_overlay"])
    assert classify(case["path"], case["frontmatter_text"], policy).to_json() == case["expected_label"]


def test_no_engine_module_names_a_libyaml_loader() -> None:
    """A loader bound at import time (``X = yaml.CSafeLoader``) would escape the fixture above."""
    import scout

    pkg = Path(scout.__file__).parent
    names = ("CSafeLoader", "CLoader", "CBaseLoader", "CFullLoader", "CUnsafeLoader", "__with_libyaml__")
    hits = [
        f"{py.relative_to(pkg)}: {n}" for py in sorted(pkg.rglob("*.py")) for n in names if n in py.read_text("utf-8")
    ]
    assert hits == []


def test_loader_edge_cases_are_in_the_corpus() -> None:
    have = {c["path"] for c in _CASES}
    assert set(_LOADER_EDGE_CASES) <= have


@pytest.mark.parametrize("via_index", [False, True], ids=["process-cache", "privacy-index"])
@pytest.mark.parametrize("rel", _LOADER_EDGE_CASES)
def test_graph_parses_the_loader_edge_cases_like_the_classifier(
    tmp_path: Path, rel: str, via_index: bool, no_libyaml: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KnowledgeGraph parses as pure SafeLoader: an unparseable block isn't an entity, a parsed one is.

    Through the privacy index too, cold and then warm (the stored parse).
    """
    (case,) = [c for c in _CASES if c["path"] == rel]
    note = tmp_path / rel
    note.parent.mkdir(parents=True)
    note.write_text(case["frontmatter_text"], encoding="utf-8")
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types: {}\n", encoding="utf-8")
    (tmp_path / "scout-config.yaml").write_text("user:\n  name: Alex\n", encoding="utf-8")
    old = time.time() - 86_400  # settled, so the index stores it
    os.utime(note, (old, old))
    clear_frontmatter_cache()
    if via_index:
        kb_read.load_graph(tmp_path, "owner", schema=str(schema))  # cold: stores the parse
        clear_frontmatter_cache()

        def no_parse(self: KnowledgeGraph, path: Path) -> None:
            raise AssertionError(f"warm load parsed {path}")

        monkeypatch.setattr(KnowledgeGraph, "_extract_frontmatter", no_parse)
        graph = kb_read.load_graph(tmp_path, "owner", schema=str(schema))
    else:
        graph = KnowledgeGraph(str(schema), str(tmp_path / "knowledge-base")).load()
    labels = {e["_label"]["path"]: e["_label"] for e in graph.entities.values()}
    # The reference: an entity exactly when pure yaml.safe_load gives a mapping with name and type.
    try:
        block = case["frontmatter_text"].split("\n---\n", 1)[0].removeprefix("---\n")
        reference = yaml.safe_load(block)
    except yaml.YAMLError:
        reference = None
    is_entity = isinstance(reference, dict) and "name" in reference and "type" in reference
    assert (rel in labels) == is_entity
    if is_entity:
        assert labels[rel] == case["expected_label"]
        (entity,) = graph.entities.values()
        assert {k: v for k, v in entity.items() if not k.startswith("_")} == reference

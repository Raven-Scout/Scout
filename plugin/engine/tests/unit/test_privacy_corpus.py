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
from pathlib import Path
from typing import Any

import pytest
import yaml

from scout.kb import privacy
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache
from scout.kb.privacy import classify, policy_from_overlay
from tests.unit.test_kb_privacy import _ROWS

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "privacy-corpus.json"

EXPECTED_SHA256 = "81213ede818f4068511cd9541eca830a99978997a2ec9710b5c622cf3542f6b7"

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


# ----- labels don't depend on libyaml (ruling R15) ------------------------------------

_LIBYAML = getattr(yaml, "__with_libyaml__", False)


@pytest.mark.skipif(not _LIBYAML, reason="PyYAML built without libyaml")
@pytest.mark.parametrize("case", _CASES, ids=[_id(c) for c in _CASES])
def test_label_is_the_same_with_or_without_libyaml(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real decision function (``frontmatter_loader``), with libyaml on and off."""
    policy = policy_from_overlay(case["policy_overlay"])
    monkeypatch.setattr(privacy, "_FM_LOADER", yaml.CSafeLoader)
    with_c = classify(case["path"], case["frontmatter_text"], policy)
    monkeypatch.setattr(privacy, "_FM_LOADER", yaml.SafeLoader)
    pure = classify(case["path"], case["frontmatter_text"], policy)
    assert with_c == pure
    assert with_c.to_json() == case["expected_label"]


@pytest.mark.skipif(not _LIBYAML, reason="PyYAML built without libyaml")
def test_libyaml_is_used_only_where_the_loaders_agree() -> None:
    assert privacy.frontmatter_loader("name: x\ntype: topic\n") is yaml.CSafeLoader
    assert privacy.frontmatter_loader("visibility:\tnormal\n") is yaml.SafeLoader
    assert privacy.frontmatter_loader('title: "a\\ud800"\n') is yaml.SafeLoader


@pytest.mark.parametrize(
    "rel", ["knowledge-base/topics/misc/tab-after-colon.md", "knowledge-base/topics/misc/surrogate-escape.md"]
)
def test_graph_parses_the_loader_edge_cases_like_the_classifier(tmp_path: Path, rel: str) -> None:
    """KnowledgeGraph uses the same loader choice: the tab block isn't an entity, the escape one is."""
    (case,) = [c for c in _CASES if c["path"] == rel]
    note = tmp_path / rel
    note.parent.mkdir(parents=True)
    note.write_text(case["frontmatter_text"], encoding="utf-8")
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types: {}\n", encoding="utf-8")
    (tmp_path / "scout-config.yaml").write_text("user:\n  name: Alex\n", encoding="utf-8")
    clear_frontmatter_cache()
    graph = KnowledgeGraph(str(schema), str(tmp_path / "knowledge-base")).load()
    labels = {e["_label"]["path"]: e["_label"] for e in graph.entities.values()}
    if case["expected_label"]["visibility_source"] == "forced":
        assert rel not in labels
    else:
        assert labels[rel] == case["expected_label"]

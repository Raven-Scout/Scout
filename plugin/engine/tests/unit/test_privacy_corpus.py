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

from scout.kb.privacy import classify, policy_from_overlay
from tests.unit.test_kb_privacy import _ROWS

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "privacy-corpus.json"

EXPECTED_SHA256 = "31dc3e87a721378b5c4b552ca13b3697821eb95d65059d58a92fd33d8efb0473"

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

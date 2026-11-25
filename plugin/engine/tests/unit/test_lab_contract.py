"""``scoutctl lab list --json`` and ``lab show --json`` are a contract with the macOS app.

``tests/fixtures/contract/lab-list.json`` and ``lab-thread.json`` are the canonical copies. Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite them from the checked-in fixture thread; never edit them by hand."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scout.lab import paths, summary

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CANONICAL_LIST = FIXTURES / "contract" / "lab-list.json"
CANONICAL_THREAD = FIXTURES / "contract" / "lab-thread.json"
# The fixture thread's files a delegate sees: the front page and both (normal) experiment notes.
SEEN = (
    "knowledge-base/lab/summary-drift/summary-drift.md",
    "knowledge-base/lab/summary-drift/experiments/summary-drift-E1-baseline.md",
    "knowledge-base/lab/summary-drift/experiments/summary-drift-E2-section-hash.md",
)


def _vault(root: Path) -> Path:
    shutil.copytree(FIXTURES / "lab" / "summary-drift", paths.thread_dir(root, "summary-drift"))
    return root


def _text(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _check_or_regen(path: Path, text: str) -> None:
    if REGEN:
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, "regenerate with SCOUT_REGEN_CONTRACT=1"


def test_lab_list_contract(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    labels = {rel: "normal" for rel in SEEN}
    doc = summary.lab_list(vault, labels, full=False, now=NOW)
    assert doc["schema_version"] == 1 and len(doc["threads"]) == 1
    _check_or_regen(CANONICAL_LIST, _text(doc))


def test_lab_thread_contract(tmp_path: Path) -> None:
    doc = summary.lab_thread(_vault(tmp_path), "summary-drift", now=NOW)
    assert doc["schema_version"] == 1 and doc["slug"] == "summary-drift"
    _check_or_regen(CANONICAL_THREAD, _text(doc))


def test_contract_carries_no_absolute_paths(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    docs = (
        summary.lab_list(vault, {"knowledge-base/lab/summary-drift/summary-drift.md": "normal"}, full=False, now=NOW),
        summary.lab_thread(vault, "summary-drift", now=NOW),
    )
    for doc in docs:
        text = json.dumps(doc)
        assert str(vault) not in text and "/Users/" not in text

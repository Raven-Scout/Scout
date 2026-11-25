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


def test_contract_checkpoint_bytes(tmp_path: Path) -> None:
    """Every thread row carries ``checkpoint_bytes`` (0 for the fixture thread, which has no checkpoints), and
    the canonical fixture the app reads says so."""
    canonical = json.loads(CANONICAL_LIST.read_text(encoding="utf-8"))
    assert [t["checkpoint_bytes"] for t in canonical["threads"]] == [0]
    vault = _vault(tmp_path)
    folder = paths.thread_dir(vault, "summary-drift") / "experiments" / "summary-drift-E1-baseline" / "checkpoints"
    (folder / "deep").mkdir(parents=True)
    (folder / "a.bin").write_bytes(b"x" * 1000)
    (folder / "deep" / "b.bin").write_bytes(b"y" * 234)
    (folder / "link.bin").symlink_to(folder / "a.bin")  # a link is neither followed nor counted
    other = paths.thread_dir(vault, "summary-drift") / "experiments" / "summary-drift-E1-baseline" / "out.bin"
    other.write_bytes(b"z" * 77)  # not under checkpoints/: not counted
    [row] = summary.lab_list(vault, None, full=True, now=NOW)["threads"]
    assert row["checkpoint_bytes"] == 1000 + 234


def test_checkpoint_bytes_leave_out_what_a_partial_reader_may_not_see(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    thread = paths.thread_dir(vault, "summary-drift")
    for eid_dir, size in (("summary-drift-E1-baseline", 100), ("summary-drift-E2-section-hash", 50)):
        folder = thread / "experiments" / eid_dir / "checkpoints"
        folder.mkdir(parents=True)
        (folder / "w.bin").write_bytes(b"x" * size)
        os.link(folder / "w.bin", folder / "same-inode.bin")  # one inode: counted once
    front = "knowledge-base/lab/summary-drift/summary-drift.md"
    e1 = "knowledge-base/lab/summary-drift/experiments/summary-drift-E1-baseline.md"
    e2 = "knowledge-base/lab/summary-drift/experiments/summary-drift-E2-section-hash.md"
    both = summary.lab_list(vault, {front: "normal", e1: "normal", e2: "normal"}, full=False, now=NOW)
    assert both["threads"][0]["checkpoint_bytes"] == 150
    partial = summary.lab_list(vault, {front: "normal", e1: "normal", e2: "private"}, full=False, now=NOW)
    assert partial["threads"][0]["checkpoint_bytes"] == 100
    full = summary.lab_list(vault, None, full=True, now=NOW)
    assert full["threads"][0]["checkpoint_bytes"] == 150

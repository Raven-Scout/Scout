"""Budget for the labelled read gate on a mature-vault-sized vault (#453, spec §10).

The spec's budget: ``kb files`` and ``kb query`` under 300 ms warm on a 3,000-file
vault. "Warm" is the second call with nothing changed: ``visible_files`` reads its
labels from the stat-keyed ``.scout-cache/privacy-index.json`` (no note is opened),
and the query reuses ``KnowledgeGraph``'s per-process frontmatter cache.

CI asserts the work as well as the time: a warm listing opens no note. The
wall-clock budget is the spec's, timed in process (the CLI adds interpreter
start-up, which the spec doesn't count); the best of three runs is taken so one
stall on a loaded runner doesn't fail it.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

import pytest

from scout.kb import read as kb_read
from scout.kb.ontology import clear_frontmatter_cache

pytestmark = pytest.mark.perf

FILES = 3000
WARM_BUDGET_S = 0.300
SCHEMA = (
    "entity_types:\n"
    "  person: {properties: {required: [name, type]}}\n"
    "  project: {properties: {required: [name, type]}}\n"
    "  task: {properties: {required: [name, type]}}\n"
    "relationship_types:\n"
    "  works_with: {inverse: works_with}\n"
)


def _write_vault(root: Path, n: int) -> Path:
    rnd = random.Random(453)
    vault = root / "vault"
    kb = vault / "knowledge-base"
    (kb / "ontology").mkdir(parents=True)
    (kb / "ontology" / "schema.yaml").write_text(SCHEMA, encoding="utf-8")
    (vault / "scout-config.yaml").write_text("user:\n  name: Alex\n", encoding="utf-8")
    (vault / "knowledge_base").symlink_to("knowledge-base")
    folders = ["knowledge-base/people", "knowledge-base/projects", "knowledge-base/personal", "action-items", "Raw"]
    types = ["person", "project", "task"]
    settled = time.time() - 86_400
    for i in range(n):
        folder = vault / folders[i % len(folders)]
        folder.mkdir(parents=True, exist_ok=True)
        if folder.name == "Raw":
            path = folder / f"scan-{i}.txt"
            path.write_text("scan\n" * rnd.randint(1, 50), encoding="utf-8")
        else:
            vis = "visibility: private\n" if i % 11 == 0 else ""
            fm = "" if i % 7 == 0 else f"---\nname: Entity {i}\ntype: {types[i % 3]}\nstatus: open\n{vis}---\n"
            path = folder / f"note-{i}.md"
            path.write_text(fm + "Body text line.\n" * rnd.randint(20, 200), encoding="utf-8")
        os.utime(path, (settled, settled))
    return vault


def _best_of(fn: Any, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_warm_kb_files_and_query_within_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _write_vault(tmp_path, FILES)
    clear_frontmatter_cache()

    cold = kb_read.visible_files(vault, "owner")
    assert len(cold) >= FILES

    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *a: Any, **k: Any) -> Any:
        if self.suffix == ".md":
            opened.append(str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    assert kb_read.visible_files(vault, "owner") == cold
    monkeypatch.undo()
    assert opened == [], "a warm listing must not open any note"

    files_s = _best_of(lambda: kb_read.visible_files(vault, "owner"))

    def query() -> None:
        graph = kb_read.load_graph(vault, "owner")
        kb_read.query_rows(graph, {"type": "task"}, visibility=None)

    query()  # cold: fills the per-process frontmatter cache
    query_s = _best_of(query)
    print(f"\nwarm kb files: {files_s * 1000:.0f} ms, warm kb query: {query_s * 1000:.0f} ms")
    assert files_s < WARM_BUDGET_S, f"warm kb files took {files_s:.3f}s"
    assert query_s < WARM_BUDGET_S, f"warm kb query took {query_s:.3f}s"

"""Budget for `scoutctl kb structure` on a large vault history.

The staleness check needs the newest commit touching each project folder's
files. It reads that from ONE `git log --name-only` over the projects folder,
not a `git log -1` per folder. This builds a vault with 200 project folders and
20,000 commits (most outside the projects folder, as in a vault whose runs
commit action items and session logs all day) with `git fast-import`, then
checks the whole table takes one git call and stays inside the budget. Best of
three runs, so one stall on a loaded runner doesn't fail it.
"""

from __future__ import annotations

import random
import subprocess
import time
from pathlib import Path

import pytest

from scout.kb import structure

pytestmark = pytest.mark.perf

FOLDERS = 200
COMMITS = 20_000
BUDGET_S = 3.0
P = "knowledge-base/projects"
SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]


def _front(name: str) -> str:
    body = "".join(f"## {s}\nNone right now.\n\n" for s in SECTIONS)
    return f"---\nname: {name}\ntype: project\nas_of: 2026-01-01\n---\n# {name}\n\n{body}"


def _blob(path: str, text: str) -> bytes:
    data = text.encode()
    return f"M 100644 inline {path}\ndata {len(data)}\n".encode() + data + b"\n"


def _build(root: Path) -> Path:
    rnd = random.Random(42)
    vault = root / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=vault, check=True)
    names = [f"project-{i:03d}" for i in range(FOLDERS)]
    stream = bytearray()
    t0 = 1_767_225_600  # 2026-01-01
    for n in range(1, COMMITS + 1):
        msg = f"c{n}".encode()
        stream += f"commit refs/heads/main\nmark :{n}\ncommitter T <t@example.com> {t0 + n * 600} +0000\n".encode()
        stream += f"data {len(msg)}\n".encode() + msg + b"\n"
        if n > 1:
            stream += f"from :{n - 1}\n".encode()
        if n == 1:
            for name in names:
                stream += _blob(f"{P}/{name}/{name}.md", _front(name))
        elif n % 5 == 0:
            name = rnd.choice(names)
            sub = rnd.choice(["knowledge", "documents", "research", "archive"])
            stream += _blob(f"{P}/{name}/{sub}/{name}-{rnd.randrange(8)}.md", f"rev {n}\n")
        else:
            stream += _blob(f"action-items/action-items-{n % 300}.md", f"rev {n}\n")
    subprocess.run(["git", "fast-import", "--quiet"], cwd=vault, input=bytes(stream), check=True)
    subprocess.run(["git", "checkout", "-q", "-f", "main"], cwd=vault, check=True)
    return vault


def test_structure_reads_history_once_within_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _build(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(structure.subprocess, "run", spy)
    best = float("inf")
    for _ in range(3):
        calls.clear()
        start = time.perf_counter()
        rows = structure.structure_rows(vault)
        best = min(best, time.perf_counter() - start)
        assert len(rows) == FOLDERS
        assert len([c for c in calls if c[:1] == ["git"]]) == 1
    assert any(r["stale"] for r in rows)
    assert best < BUDGET_S, f"kb structure took {best:.2f}s over {COMMITS} commits (budget {BUDGET_S}s)"

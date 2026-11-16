"""`kb-shard-log.py split-oversized` (templates/scripts/kb-shard-log.py).

Covers three defects in the splitter:

  A. When the index file passed as `file` lives INSIDE `shard_dir`, the splitter split and
     unlinked it, then `_reindex()` read it and raised FileNotFoundError — the mutation
     completed, the command reported failure, and the index was never written.
  B. A split deletes `<stem>.md` and creates `<stem>-pNN.md`, orphaning every wikilink that
     resolves by that basename. The debt was not reported, so each split silently required
     a manual repoint pass afterwards.
  C. An already-parted file that outgrew the budget re-split onto its own stem, producing
     `<stem>-p01-p01.md` and destroying the basename for no gain.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "kb-shard-log.py"
BIG = "\n".join(f"line {i} padding padding padding padding" for i in range(2000))


def _fixture(root: Path, *, index_inside: bool) -> tuple[Path, Path]:
    shard_dir = root / "sd"
    shard_dir.mkdir()
    (shard_dir / "2026-11.md").write_text("# Nov\n\n" + BIG + "\n", encoding="utf-8")
    index = (shard_dir / "2026-11.md") if index_inside else (root / "index.md")
    if not index_inside:
        index.write_text("# Index\n\n## Shards\n\n", encoding="utf-8")
    return index, shard_dir


def _run(index: Path, shard_dir: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "split-oversized", str(index), str(shard_dir), "--max-bytes", "50000"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_refuses_to_split_its_own_index(tmp_path: Path) -> None:
    index, shard_dir = _fixture(tmp_path, index_inside=True)
    r = _run(index, shard_dir)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\n{r.stderr}"
    assert "Traceback" not in r.stderr, f"crashed instead of refusing:\n{r.stderr}"
    assert index.exists(), "refused but still deleted the index"
    assert not list(shard_dir.glob("*-p*.md")), "refused but still wrote parts"


def test_refusal_comes_before_any_other_shard_is_split(tmp_path: Path) -> None:
    """An over-budget shard that sorts before the index must not be split first."""
    index, shard_dir = _fixture(tmp_path, index_inside=True)
    (shard_dir / "2026-10.md").write_text("# Oct\n\n" + BIG + "\n", encoding="utf-8")
    r = _run(index, shard_dir)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\n{r.stderr}"
    assert sorted(p.name for p in shard_dir.iterdir()) == ["2026-10.md", "2026-11.md"]


def test_missing_index_is_caught_before_mutating(tmp_path: Path) -> None:
    _, shard_dir = _fixture(tmp_path, index_inside=False)
    r = _run(tmp_path / "nope.md", shard_dir)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\n{r.stderr}"
    assert (shard_dir / "2026-11.md").exists(), "mutated despite a missing index"


def test_split_succeeds_and_reports_repoint_debt(tmp_path: Path) -> None:
    index, shard_dir = _fixture(tmp_path, index_inside=False)
    before = (shard_dir / "2026-11.md").read_text(encoding="utf-8")
    r = _run(index, shard_dir)
    assert r.returncode == 3, f"expected exit 3 (repoint owed), got {r.returncode}\n{r.stderr}"
    parts = sorted(shard_dir.glob("2026-11-p*.md"))
    assert len(parts) >= 2, f"expected >=2 parts, got {parts}"
    assert not (shard_dir / "2026-11.md").exists(), "source shard survived the split"
    assert "REPOINT OWED" in r.stdout, f"debt not reported:\n{r.stdout}"
    assert "[[2026-11]]" in r.stdout, f"orphaned stem not named:\n{r.stdout}"
    # No content may be lost: every body line survives across the parts.
    body = [ln for ln in before.splitlines() if ln.startswith("line ")]
    joined = "\n".join(p.read_text(encoding="utf-8") for p in parts)
    missing = [ln for ln in body if ln not in joined]
    assert not missing, f"{len(missing)} body line(s) lost, first: {missing[:1]}"
    # The index now lists the parts.
    assert "[[sd/2026-11-p01]]" in index.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("grep") is None, reason="needs grep")
def test_printed_grep_finds_the_links_the_split_orphaned(tmp_path: Path) -> None:
    """The REPOINT block's command must find every link form Obsidian resolves by basename."""
    index, shard_dir = _fixture(tmp_path, index_inside=False)
    note = tmp_path / "note.md"
    note.write_text(
        "a [[2026-11]]\nb [[sd/2026-11|Nov]]\nc [[2026-11#Week 1]]\nd [[2026-11-p01]]\ne [[2026-110]]\n",
        encoding="utf-8",
    )
    r = _run(index, shard_dir, cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    cmd = next(ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("grep "))
    found = subprocess.run(cmd, shell=True, cwd=tmp_path, capture_output=True, text=True)
    assert found.stderr == "", found.stderr
    hits = sorted(re.findall(r"note\.md:\d+:(\w)", found.stdout))
    assert hits == ["a", "b", "c"], found.stdout


def test_resplitting_a_part_continues_its_series(tmp_path: Path) -> None:
    """An over-budget `-p01` must become the next free part of its own series.

    Using the part's own stem produces `<stem>-p01-p01.md`, which destroys the basename
    every inbound link resolves by and gains nothing.
    """
    shard_dir = tmp_path / "sd"
    shard_dir.mkdir()
    index = tmp_path / "index.md"
    index.write_text("# Index\n\n## Shards\n\n", encoding="utf-8")
    (shard_dir / "2026-09-p01.md").write_text("# S\n\n" + BIG + "\n", encoding="utf-8")
    (shard_dir / "2026-09-p02.md").write_text("small\n", encoding="utf-8")
    r = _run(index, shard_dir)
    assert r.returncode == 3, f"expected exit 3, got {r.returncode}\n{r.stderr}"
    names = sorted(x.name for x in shard_dir.glob("*.md"))
    assert not any("-p01-p" in n for n in names), f"double-suffixed part written: {names}"
    assert "2026-09-p03.md" in names and "2026-09-p04.md" in names, names
    assert "2026-09-p02.md" in names, "clobbered an existing part"

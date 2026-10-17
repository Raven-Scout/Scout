#!/usr/bin/env python3
"""Self-test for kb-shard-log.py `split-oversized`.

Covers two defects in the splitter:

  A. When the index file passed as `file` lives INSIDE `shard_dir`, the splitter split and
     unlinked it, then `_reindex()` read it and raised FileNotFoundError — the mutation
     completed, the command reported failure, and the index was never written.
  B. A split deletes `<stem>.md` and creates `<stem>-pNN.md`, orphaning every wikilink that
     resolves by that basename. The debt was not reported, so each split silently required
     a manual repoint pass afterwards.

Run: python3 scripts/test_kb_shard_log_split.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "kb-shard-log.py"


def _fixture(root: Path, *, index_inside: bool) -> tuple[Path, Path]:
    shard_dir = root / "sd"
    shard_dir.mkdir()
    big = "\n".join(f"line {i} padding padding padding padding" for i in range(2000))
    (shard_dir / "2026-11.md").write_text("# Nov\n\n" + big + "\n", encoding="utf-8")
    index = (shard_dir / "2026-11.md") if index_inside else (root / "index.md")
    if not index_inside:
        index.write_text("# Index\n\n## Shards\n\n", encoding="utf-8")
    return index, shard_dir


def _run(index: Path, shard_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "split-oversized", str(index), str(shard_dir),
         "--max-bytes", "50000"],
        capture_output=True, text=True,
    )


def test_refuses_to_split_its_own_index() -> None:
    with tempfile.TemporaryDirectory() as td:
        index, shard_dir = _fixture(Path(td), index_inside=True)
        r = _run(index, shard_dir)
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}\n{r.stderr}"
        assert "Traceback" not in r.stderr, f"crashed instead of refusing:\n{r.stderr}"
        assert index.exists(), "refused but still deleted the index"
        assert not list(shard_dir.glob("*-p*.md")), "refused but still wrote parts"
    print("  ✓ A1 refuses to split the file it must reindex — no mutation, no traceback")


def test_missing_index_is_caught_before_mutating() -> None:
    with tempfile.TemporaryDirectory() as td:
        _, shard_dir = _fixture(Path(td), index_inside=False)
        r = _run(Path(td) / "nope.md", shard_dir)
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}\n{r.stderr}"
        assert (shard_dir / "2026-11.md").exists(), "mutated despite a missing index"
    print("  ✓ A2 a missing index fails before any file is touched")


def test_split_succeeds_and_reports_repoint_debt() -> None:
    with tempfile.TemporaryDirectory() as td:
        index, shard_dir = _fixture(Path(td), index_inside=False)
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
    print("  ✓ B  splits losslessly, exits 3, and names the stem whose links now dangle")


def main() -> int:
    print("kb-shard-log.py split-oversized —")
    for t in (test_refuses_to_split_its_own_index,
              test_missing_index_is_caught_before_mutating,
              test_split_succeeds_and_reports_repoint_debt):
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            return 1
    print("all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""An over-complex glob must not hang the commit hook through the page checks.

A glob with more than ``MAX_GLOB_STARS`` ``*`` backtracks for hours on a long matching
path, and killing ``git commit`` doesn't stop the hook's child. So these run the real
thing in its own process group, and kill the group if it overruns: a regression fails
here instead of leaving a process spinning.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import install_hook
from scout.kb.lint_config import MAX_GLOB_STARS, lint_any
from scout.kb.structure import _excluder

GLOB = "**a" * 10 + "**b"
LONG = "a" * 60
DEADLINE_S = 60
PAGE = f"knowledge-base/projects/{LONG}/{LONG}.md"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        cmd, cwd=cwd, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        out, _ = proc.communicate(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        pytest.fail(f"{cmd[:4]} still running after {DEADLINE_S}s")
    return subprocess.CompletedProcess(cmd, proc.returncode, out)


def _scoutctl(tmp_path: Path) -> Path:
    """The engine under test as the hook's scoutctl, whatever this environment calls it."""
    path = tmp_path / "scoutctl"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scout "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _lists(kb_repo, where: str) -> None:
    if where == "exclude_globs":
        kb_repo.write("scout-config.yaml", f"kb_lint:\n  exclude_globs: ['{GLOB}']\n")
    else:
        kb_repo.write(
            "knowledge-base/ontology/schema.yaml",
            f"entity_types: {{}}\nrelationship_types: {{}}\ngraph:\n  exclude: ['{GLOB}']\n",
        )


def test_the_glob_really_is_over_the_cap() -> None:
    assert GLOB.count("*") > MAX_GLOB_STARS
    assert not lint_any((GLOB,), PAGE)


@pytest.mark.parametrize("where", ["exclude_globs", "graph.exclude"])
def test_a_real_commit_through_the_hook_finishes(kb_repo, tmp_path: Path, where: str) -> None:
    _lists(kb_repo, where)
    install_hook(kb_repo.root, scoutctl=str(_scoutctl(tmp_path)))
    kb_repo.stage(PAGE, "# A page with no sections\n")
    result = _run(["git", "commit", "-q", "-m", "a new page"], kb_repo.root)
    assert result.returncode == 0, result.stdout
    assert "[page-sections]" in result.stdout  # the glob matched nothing, so the page was still checked


@pytest.mark.parametrize("where", ["exclude_globs", "graph.exclude"])
def test_kb_lint_staged_and_kb_structure_finish(kb_repo, where: str) -> None:
    _lists(kb_repo, where)
    kb_repo.stage(PAGE, "# A page with no sections\n")
    scout = [sys.executable, "-m", "scout", "kb"]
    assert _run([*scout, "lint", "--staged"], kb_repo.root).returncode == 0
    structure = _run([*scout, "structure", "--next"], kb_repo.root)
    assert structure.returncode == 0 and structure.stdout.strip() == LONG


def test_the_excluder_drops_it_from_both_lists(kb_repo) -> None:
    _lists(kb_repo, "graph.exclude")
    excluded = _excluder(kb_repo.root, (GLOB,))
    assert not excluded(PAGE)

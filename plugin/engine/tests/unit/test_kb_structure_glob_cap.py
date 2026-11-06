"""A glob with many ``*`` is obeyed by the page checks, and still can't hang the commit hook.

kb-lint used to drop any of its globs with more than 6 ``*``, because the regex behind
``glob_match`` could backtrack for hours on one (#565). ``glob_match`` can't now, so
such a glob excludes what it says, and ``kb structure`` agrees with ``kb stats`` and
``kb validate`` on the schema's ``graph.exclude``. These run the real thing in its own
process group and kill the group if it overruns: a regression fails here instead of
leaving a process spinning.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scout.kb.hook import install_hook
from scout.kb.read import ENGINE_SCHEMA
from scout.kb.structure import _excluder

LONG = "a" * 60
GLOB = "**a" * 10 + "**.md"  # 22 `*`, and it matches PAGE: kb-lint once dropped it
DEADLINE_S = 60
PAGE = f"knowledge-base/projects/{LONG}/{LONG}.md"
OTHER = "knowledge-base/projects/widget/widget.md"  # too few `a`s for GLOB


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


def _schema(kb_repo, exclude: list[str]) -> None:
    """The engine's schema with this ``graph.exclude``."""
    schema = yaml.safe_load(ENGINE_SCHEMA.read_text(encoding="utf-8"))
    schema.setdefault("graph", {})["exclude"] = exclude
    kb_repo.write("knowledge-base/ontology/schema.yaml", yaml.safe_dump(schema))


def _lists(kb_repo, where: str) -> None:
    if where == "exclude_globs":
        kb_repo.write("scout-config.yaml", f"kb_lint:\n  exclude_globs: ['{GLOB}']\n")
    else:
        _schema(kb_repo, [GLOB])


def _pages(kb_repo, body: str = "# A page with no sections\n") -> None:
    kb_repo.stage(PAGE, body)
    kb_repo.stage(OTHER, body)


@pytest.mark.parametrize("where", ["exclude_globs", "graph.exclude"])
def test_a_real_commit_through_the_hook_finishes_and_obeys_the_glob(kb_repo, tmp_path: Path, where: str) -> None:
    _lists(kb_repo, where)
    install_hook(kb_repo.root, scoutctl=str(_scoutctl(tmp_path)))
    _pages(kb_repo)
    result = _run(["git", "commit", "-q", "-m", "two new pages"], kb_repo.root)
    assert result.returncode == 0, result.stdout
    assert "[page-sections]" in result.stdout and "widget" in result.stdout  # the page the glob misses
    assert LONG not in result.stdout  # the page the glob matches is left out


@pytest.mark.parametrize("where", ["exclude_globs", "graph.exclude"])
def test_kb_lint_staged_and_kb_structure_finish_and_obey_the_glob(kb_repo, where: str) -> None:
    _lists(kb_repo, where)
    _pages(kb_repo)
    scout = [sys.executable, "-m", "scout", "kb"]
    assert _run([*scout, "lint", "--staged"], kb_repo.root).returncode == 0
    structure = _run([*scout, "structure", "--next"], kb_repo.root)
    assert structure.returncode == 0 and structure.stdout.strip() == "widget"


def test_the_excluder_obeys_it_in_both_lists(kb_repo) -> None:
    _schema(kb_repo, [GLOB])
    assert _excluder(kb_repo.root, ())(PAGE)
    assert not _excluder(kb_repo.root, ())(OTHER)
    _schema(kb_repo, [])
    assert _excluder(kb_repo.root, (GLOB,))(PAGE)
    assert not _excluder(kb_repo.root, (GLOB,))(OTHER)


def test_kb_structure_stats_and_validate_agree_on_a_long_graph_exclude(kb_repo) -> None:
    """The gap #555 left: stats and validate obeyed this glob while structure ignored it."""
    # An unknown type is a validation error, so each page is in validate's report until it's excluded.
    for rel, name in ((PAGE, "Long Page"), (OTHER, "Widget Page")):
        kb_repo.stage(rel, f"---\nname: {name}\ntype: not-a-type\n---\n# A page with no sections\n")
    scout = [sys.executable, "-m", "scout", "kb"]

    def views() -> tuple[str, str, int]:
        structure = _run([*scout, "structure"], kb_repo.root)
        validate = _run([*scout, "validate", "--repo", str(kb_repo.root)], kb_repo.root)
        stats = _run([*scout, "stats", "--repo", str(kb_repo.root), "--json"], kb_repo.root)
        assert structure.returncode == 0 and stats.returncode == 0, structure.stdout + stats.stdout
        return structure.stdout, validate.stdout, int(json.loads(stats.stdout)["entities"])

    _schema(kb_repo, [])
    structure, validate, everything = views()
    assert LONG in structure and "widget" in structure
    assert "Long Page" in validate and "Widget Page" in validate
    _schema(kb_repo, [GLOB])
    structure, validate, entities = views()
    assert LONG not in structure and "widget" in structure
    assert "Long Page" not in validate and "Widget Page" in validate
    assert entities == everything - 1

"""A glob with many ``**`` can't hang the commit hook or the graph commands (#565).

The regex behind ``glob_match`` used to backtrack without end on these inputs: a
``git commit`` stuck in its hook, and ``kb stats`` / ``kb validate`` stuck in
``KnowledgeGraph.load``. Each test also shows the glob is still obeyed, because
dropping or capping a glob would stop the hang too and quietly lift a privacy rule.

Every timed command runs in its own process group, and the whole group is killed
at the deadline. Killing only ``git commit`` leaves its hook's ``kb lint`` running,
which is how two of them once ran for 12 hours. A CPU limit that the children
inherit stops them even if pytest itself is killed before it can clean up.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from scout.kb.hook import default_scoutctl, install_hook
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git, git_env

GLOB = "**a" * 10 + "**b"
FOLDER = "a" * 60
DEADLINE_S = 30.0
CPU_LIMIT_S = 120


def _limit_cpu() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S))


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # gone; macOS says EPERM for a group of zombies
        pass


def _run_bounded(argv: list[str], cwd: Path, deadline_s: float = DEADLINE_S) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` in a new process group; fail the test at the deadline, leaving nothing running."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        preexec_fn=_limit_cpu,
    )
    try:
        out, _ = proc.communicate(timeout=deadline_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        proc.communicate()
        pytest.fail(f"{argv[1:4]} still running after {deadline_s:.0f} s")
    finally:
        _kill_group(proc.pid)
    return subprocess.CompletedProcess(argv, proc.returncode, out)


def test_the_bounded_runner_kills_the_child_a_killed_parent_would_leave(tmp_path: Path) -> None:
    """The guard itself: a parent shell's child, as a hook's ``kb lint`` is ``git commit``'s, must die too."""
    pid_file = tmp_path / "child.pid"
    script = f"sleep 300 & echo $! > {pid_file}; wait"
    with pytest.raises(pytest.fail.Exception, match="still running"):
        _run_bounded(["sh", "-c", script], tmp_path, deadline_s=3.0)
    child = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _is_running(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _is_running(child)


def _is_running(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    return build_privacy_vault(tmp_path)


def _share_with_glob(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(f'  shared_files_add:\n    - "{GLOB}"\n')
    git(vault, "add", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "a shared glob")


def _stage(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(vault, "add", "--", rel)


def _commit_through_hook(vault: Path) -> subprocess.CompletedProcess[str]:
    argv = ["git", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false", "commit", "-q", "-m", "a note"]
    return _run_bounded(argv, vault)


def test_kb_lint_staged_returns_on_a_many_star_shared_files_glob(vault: Path) -> None:
    _share_with_glob(vault)
    _stage(vault, f"outbox/{FOLDER}/note.txt", "Plain words.\n")
    proc = _run_bounded([default_scoutctl(), "kb", "lint", "--staged"], vault)
    assert proc.returncode == 0, proc.stdout


def test_a_real_commit_returns_through_the_installed_hook(vault: Path) -> None:
    _share_with_glob(vault)
    install_hook(vault)
    _stage(vault, f"outbox/{FOLDER}/note.txt", "Plain words.\n")
    proc = _commit_through_hook(vault)
    assert proc.returncode == 0, proc.stdout
    assert "a note" in git(vault, "log", "-1", "--format=%s")


def test_the_many_star_glob_still_guards_its_shared_file_in_a_real_commit(vault: Path) -> None:
    """Capping would stop the hang too, and let this private mention through (#552's near miss)."""
    _share_with_glob(vault)
    install_hook(vault)
    _stage(vault, f"outbox/{FOLDER}/note.b", "Ask Sammy.\n")
    proc = _commit_through_hook(vault)
    assert proc.returncode == 1, proc.stdout
    assert "[private-mention]" in proc.stdout
    assert "a note" not in git(vault, "log", "-1", "--format=%s")


def _note(vault: Path, rel: str, name: str, etype: str = "project") -> None:
    _stage(vault, rel, f"---\nname: {name}\ntype: {etype}\n---\n# Stand-in\n")


def _exclude_in_schema(vault: Path, globs: list[str]) -> None:
    path = vault / "knowledge-base" / "ontology" / "schema.yaml"
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema.setdefault("graph", {})["exclude"] = globs
    path.write_text(yaml.safe_dump(schema), encoding="utf-8")


def _entity_total(vault: Path) -> int:
    proc = _run_bounded([default_scoutctl(), "kb", "stats", "--repo", str(vault), "--json"], vault)
    assert proc.returncode == 0, proc.stdout
    return int(json.loads(proc.stdout)["entities"])


def test_kb_stats_returns_on_a_many_star_graph_exclude_and_still_excludes(vault: Path) -> None:
    _note(vault, f"knowledge-base/{FOLDER}/keep.md", "Kept Note")
    _note(vault, f"knowledge-base/{FOLDER}/notb.md", "Excluded Note")
    _exclude_in_schema(vault, [])
    everything = _entity_total(vault)
    _exclude_in_schema(vault, [GLOB, GLOB + ".md"])
    assert _entity_total(vault) == everything - 1


def test_kb_validate_returns_on_a_many_star_graph_exclude_and_still_excludes(vault: Path) -> None:
    # An unknown type is a validation error, so each note is in the report until it's excluded.
    _note(vault, f"knowledge-base/{FOLDER}/keep.md", "Kept Note", "not-a-type")
    _note(vault, f"knowledge-base/{FOLDER}/notb.md", "Excluded Note", "not-a-type")
    argv = [default_scoutctl(), "kb", "validate", "--repo", str(vault)]
    _exclude_in_schema(vault, [])
    report = _run_bounded(argv, vault).stdout
    assert "Kept Note" in report and "Excluded Note" in report
    _exclude_in_schema(vault, [GLOB, GLOB + ".md"])
    report = _run_bounded(argv, vault).stdout
    assert "Kept Note" in report and "Excluded Note" not in report


def test_the_matcher_returns_on_far_worse_input_than_the_issues(tmp_path: Path) -> None:
    """The bound is the glob's length times the path's, whatever the wildcards: no input gets to choose."""
    script = textwrap.dedent(
        """
        from scout.kb.lint_config import glob_match
        path = "a" * 4096
        for glob in ("**a" * 200 + "**b", "*a" * 200 + "*b", "**/a" * 200 + "**/b", "*" * 1000 + "b", "?*" * 500 + "b"):
            assert not glob_match(glob, path), glob
            assert not glob_match(glob, "/".join(path[:2000]))
        assert glob_match("**a" * 200 + "**b", path + "b")
        """
    )
    proc = _run_bounded([sys.executable, "-c", script], tmp_path)
    assert proc.returncode == 0, proc.stdout

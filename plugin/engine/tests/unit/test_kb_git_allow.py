"""Unit tests for the run's git allowlist helpers (scout.kb.git_allow) and the pieces of the gate that judge where an
allowed form runs (review of #817, rounds 6 and 7). The gate-level cases are in test_kb_read_gate_git.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import git_allow, git_read, live_run

SCOUT = git_allow.SCOUT_REMOTES[0]


@pytest.mark.parametrize(
    ("args", "ok"),
    [
        (["-m", "x"], True),
        (["--message=x"], True),
        (["--message", "x", "--file", "f"], True),
        (["--file=f"], True),
        (["-qam", "x"], True),
        (["-qmx"], True),
        (["-qoF", "f", "--", "a"], True),
        (["-m", "x", "a", "b"], True),
        (["-m"], False),
        (["-qm"], False),  # its value is missing
        (["-qz", "-m", "x"], False),
        (["--foo", "-m", "x"], False),
        (["a"], False),  # no message: an editor
        (["--", "a"], False),
    ],
)
def test_vault_commit(args: list[str], ok: bool) -> None:
    assert git_allow.vault_commit(args) is ok


@pytest.mark.parametrize(
    ("args", "files"),
    [
        (["-F", "f"], ["f"]),
        (["-Ff"], ["f"]),
        (["--file", "f"], ["f"]),
        (["--file=f"], ["f"]),
        (["-qF", "f"], ["f"]),
        (["-aFf"], ["f"]),
        (["-F", "-"], []),
        (["-m", "x", "--", "-Fx"], []),
        (["-xF", "f"], []),  # not a cluster commit takes
    ],
)
def test_commit_files(args: list[str], files: list[str]) -> None:
    assert git_allow.commit_files(args) == files


@pytest.mark.parametrize(
    ("sub", "args", "kind"),
    [
        ("clone", ["-q", SCOUT, "d"], git_allow.CLONE),
        ("clone", ["https://example.com/x.git", "d"], None),
        ("clone", [SCOUT, "-d"], None),
        ("switch", ["-c", "scout/x"], git_allow.WORKTREE),
        ("switch", ["--create", "scout/a/b"], git_allow.WORKTREE),
        ("switch", ["-c", "main"], None),
        ("checkout", ["-b", "scout/x"], git_allow.WORKTREE),
        ("checkout", ["-c", "scout/x"], None),
        ("commit", ["-m", "x"], git_allow.WORKTREE),
        ("commit", ["-mx"], git_allow.WORKTREE),
        ("commit", ["--message=x", "--", "p"], git_allow.WORKTREE),
        ("commit", ["-m"], None),
        ("commit", ["-a", "-m", "x"], None),
        ("commit", ["-m", "x", "p"], None),
        ("commit", [], None),
        ("push", ["-u", "origin", "scout/x"], git_allow.PUSH),
        ("push", ["origin", "main"], None),
        ("log", [], None),
    ],
)
def test_engine_form(sub: str, args: list[str], kind: str | None) -> None:
    assert git_allow.engine_form(sub, args) == kind


@pytest.mark.parametrize(
    ("args", "values"),
    [
        (["-x", "cmd"], ["cmd"]),
        (["-xcmd"], ["cmd"]),
        (["-qx"], []),  # no word follows
        (["--exec", "c"], ["c"]),
        (["--exe=c"], ["c"]),
        (["--exec"], []),
        (["-sx", "c"], []),  # `-s` stops the cluster: `x` is its value
    ],
)
def test_option_values(args: list[str], values: list[str]) -> None:
    assert git_read.option_values(args, {"exec"}, {"x"}, frozenset("s")) == values


def _config(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config").write_text(text)
    return folder


def test_config_runs(tmp_path: Path) -> None:
    assert git_read.config_runs(_config(tmp_path / "a", "[core\n"), "status") == "an unreadable config"
    assert git_read.config_runs(_config(tmp_path / "b", '[includeIf "gitdir:/"]\n\tpath = x\n'), "log") is not None
    helper = _config(tmp_path / "c", "[credential]\n\thelper = x\n")
    assert git_read.config_runs(helper, "status") is None
    assert git_read.config_runs(helper, "remote") == "credential.helper"
    assert git_read.config_runs(_config(tmp_path / "d", "[core]\n\tfsmonitor = true\n"), "status") is None
    assert git_read.config_runs(_config(tmp_path / "e", "[core]\n\tfsmonitor = x\n"), "status") == "core.fsmonitor"
    lfs = _config(tmp_path / "f", '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n')
    assert git_read.config_runs(lfs, "add") is None
    assert git_read.config_runs(_config(tmp_path / "g", '[submodule "x"]\n\tpath = x\n'), "status") is None
    assert git_read.config_runs(tmp_path / "none", "status") is None


def test_read_config_when_git_cant_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config").write_text("[core]\n\tbare = false\n")

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", broken)
    assert git_read.read_config(tmp_path / "config") is None


def test_find_repo_is_unsure_of_a_config_that_includes_another(tmp_path: Path) -> None:
    git = tmp_path / "r" / ".git"
    for sub in ("objects", "refs"):
        (git / sub).mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "config").write_text(f"[include]\n\tpath = {tmp_path / 'other'}\n")
    found = git_read.find_repo(tmp_path / "r", None, None)
    assert found is not None and found[2] is True


def test_git_programs_lists_the_commands_git_runs() -> None:
    live_run._STATE.update(vars={})
    assert live_run._git_programs(["git", "bisect", "run", "make", "x"], "") == ["make x"]
    assert live_run._git_programs(["git", "submodule", "--quiet", "foreach", "ls", "-a"], "/w") == ["cd /w && ls -a"]
    assert live_run._git_programs(["GIT_PAGER=less", "git", "log"], "") == ["less"]
    assert live_run._git_programs(["git", "-C", "a", "submodule", "foreach"], "") == []
    assert live_run._git_programs(["ls"], "") == []


@pytest.fixture
def home_state(tmp_path: Path):
    old = dict(live_run._STATE)
    live_run._STATE.update(home=str(tmp_path), vars={}, command="")
    yield tmp_path
    live_run._STATE.clear()
    live_run._STATE.update(old)


def test_path_keeps_git(home_state: Path) -> None:
    """Round 7: a PATH change may only reorder the run's own PATH folders or add fixed system ones."""
    keeps = live_run._path_keeps_git
    live_run._STATE.update(run_path=f"{home_state}/.local/bin:/usr/bin:/bin")
    assert keeps("PATH=~/.local/bin:$PATH") is True
    assert keeps("PATH=$HOME/.local/bin:$PATH") is True
    assert keeps("PATH=/usr/bin:/bin") is True
    assert keeps("PATH=/opt/homebrew/bin:$PATH") is True
    assert keeps("path=(/usr/sbin $path)") is True
    assert keeps("echo ${PATH}") is True
    assert keeps("grep path x") is True
    assert keeps("PATH=/nonexistent-folder") is False
    assert keeps("PATH=/usr/local/bin:$PATH") is False
    assert keeps("PATH=relative:$PATH") is False
    assert keeps("PATH+=:/x") is False
    assert keeps("path=x") is False
    assert keeps("path[1]=/x") is False
    assert keeps("path[1]=/usr/bin") is True
    assert keeps(": ${PATH::=/x:$PATH}") is False
    assert keeps(": ${PATH:=/usr/bin}") is True
    assert keeps(": ${(P)n::=/x}") is False
    assert keeps("export PATH") is False
    assert keeps("export PATH='/usr/bin:$PATH'") is True
    assert live_run._system_git("/usr/bin/git") is True
    (home_state / "git").write_text("#!/bin/sh\n")
    assert live_run._system_git(str(home_state / "git")) is False


def test_sources_text() -> None:
    assert live_run._sources_text("source /dev/stdin <<EOF") is True
    assert live_run._sources_text(". <(printf x)") is True
    assert live_run._sources_text("source $F") is True
    assert live_run._sources_text("source scripts/env.sh") is False
    assert live_run._sources_text("git -C . log") is False


def test_engine_kind_needs_a_home(home_state: Path) -> None:
    assert live_run._engine_kind("clone", [SCOUT, "~/.scout-worktrees/x"]) == git_allow.CLONE
    assert live_run._engine_kind("clone", [SCOUT, "~/.scout-worktrees/a/../b"]) is None
    assert live_run._engine_kind("clone", [SCOUT, "/elsewhere/x"]) is None
    live_run._STATE.update(home=None)
    assert live_run._engine_kind("clone", [SCOUT, "~/.scout-worktrees/x"]) is None


def test_engine_output_without_an_engine_json(home_state: Path) -> None:
    assert live_run._engine_root() is None
    # Round 7: only the engine checkout's own origin, by $ROOT; a literal folder is the run's to choose.
    assert live_run._engine_output("git -C /nonexistent remote get-url origin || echo " + SCOUT) is None
    assert live_run._engine_output('git -C "$ROOT" remote get-url origin') is None  # no ROOT from engine.json
    assert live_run._engine_output("ls") is None
    live_run._STATE.update(home=None)
    assert live_run._engine_root() is None


def test_engine_repo_refuses_what_isnt_a_clone_of_its_own(tmp_path: Path) -> None:
    read = git_read.GitRead(sub="commit", reads=False, engine=git_allow.WORKTREE)
    env = {"HOME": str(tmp_path)}
    vault = tmp_path / "Vault"
    vault.mkdir()
    assert "can't work out" in (gate._engine_repo(read, None, vault, env) or "")
    assert "can't work out" in (gate._engine_repo(read, str(tmp_path), vault, {}) or "")
    # A clone in ~/.scout-worktrees whose work tree holds the vault: the vault's repository, not a clone of Scout's.
    wt = tmp_path / ".scout-worktrees" / "w"
    for sub in ("objects", "refs"):
        (wt / ".git" / sub).mkdir(parents=True)
    (wt / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (wt / ".git" / "config").write_text("")
    inside = wt / "Vault"
    inside.mkdir()
    assert "vault's repository" in (gate._engine_repo(read, str(wt), inside, env) or "")


def test_engine_config_odd() -> None:
    odd = gate._engine_config_odd
    assert odd([("core.bare", "false"), ("remote.origin.url", SCOUT)]) is None
    assert odd([("core.bare", "true")]) == "core.bare"
    assert odd([("remote.origin.url", "https://example.com/x.git")]) == "remote.origin.url"
    assert odd([("remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")]) == "remote.origin.fetch"
    assert odd([("branch.scout/a.remote", "elsewhere")]) == "branch.scout/a.remote"
    assert odd([("branch.scout/a.merge", "refs/heads/scout/a")]) is None
    assert odd([("push.default", "upstream")]) == "push.default"


def _init(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path / ".git"


def test_engine_push_checks(tmp_path: Path) -> None:
    git_dir = _init(tmp_path / "r")
    assert "can't read" in (gate._engine_push(git_dir, "main") or "")
    assert "can't read" in (gate._engine_push(git_dir, "scout/../main") or "")
    assert "no origin/main" in (gate._engine_push(git_dir, "scout/a") or "")


def test_engine_repo_refuses_linked_worktrees_and_odd_configs(tmp_path: Path) -> None:
    read = git_read.GitRead(sub="commit", reads=False, engine=git_allow.WORKTREE)
    env = {"HOME": str(tmp_path)}
    vault = tmp_path / "Vault"
    vault.mkdir()
    clone = tmp_path / ".scout-worktrees" / "c"
    git_dir = _init(clone)
    (git_dir / "worktrees").mkdir()
    assert "linked worktrees" in (gate._engine_repo(read, str(clone), vault, env) or "")
    (git_dir / "worktrees").rmdir()
    (git_dir / "config").write_text("[core\n")
    assert "unreadable" in (gate._engine_repo(read, str(clone), vault, env) or "")
    (git_dir / "config").write_text("[core]\n\tbare = false\n")
    assert "origin isn't Scout's" in (gate._engine_repo(read, str(clone), vault, env) or "")


def test_git_read_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    git_dir = _init(tmp_path / "r")
    assert git_read.object_type(git_dir, "-x") is None
    assert git_read.object_type(git_dir, "HEAD") is None  # no commit yet
    hooks = tmp_path / "h"
    hooks.mkdir()
    (hooks / "post-index-change").write_text("")
    strict = git_read.hooks_run(git_dir, None, "add", [("core.hookspath", str(hooks))], strict=True)
    assert strict == "core.hooksPath"
    assert git_read.hooks_run(git_dir, tmp_path, "add", [("core.hookspath", "h")], strict=False) is not None
    assert git_read.hooks_run(git_dir, tmp_path, "log", [("core.hookspath", "h")], strict=False) is None
    vault_git = _init(tmp_path / "v")
    other = _init(tmp_path / "o")
    info = other / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_text("# a comment\n\n")
    assert git_read.holds_history(other, vault_git) is False
    (info / "alternates").write_text(str(tmp_path / "not-yet") + "\n")
    assert git_read.holds_history(other, vault_git) is True
    linked = _init(tmp_path / "l")
    shutil.rmtree(linked / "objects")
    (linked / "objects").symlink_to(vault_git / "objects")
    assert git_read.holds_history(linked, vault_git) is True

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", broken)
    assert git_read.git_out(git_dir, ["rev-parse", "HEAD"]) is None

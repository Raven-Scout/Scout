"""Which vault the changes map counts for, and which folders are that vault (its git worktrees)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout.changes import vault as vault_mod
from scout.changes.vault import installed_vault, vault_roots
from scout.scripts.engine_pointer import EnginePointer, pointer_path, write_pointer


def _pointer(home: Path, vault: str) -> None:
    write_pointer(
        EnginePointer(
            version="0.0.0",
            engine_root="/engine",
            python="/engine/.venv/bin/python",
            scoutctl="/engine/bin/scoutctl",
            vault=vault,
            managed_by="dev",
            written_at="2026-11-17T00:00:00Z",
        ),
        home=home,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_scout_data_dir_wins(tmp_path, home):
    _pointer(home, str(tmp_path / "pointed"))
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(tmp_path / "explicit")}
    assert installed_vault(env) == (tmp_path / "explicit").resolve()


def test_engine_pointer_vault_is_next(tmp_path, home, monkeypatch):
    monkeypatch.chdir(tmp_path)  # never the cwd
    _pointer(home, str(tmp_path / "pointed"))
    assert installed_vault({"HOME": str(home)}) == (tmp_path / "pointed").resolve()


def test_home_scout_is_the_fallback(home):
    assert installed_vault({"HOME": str(home)}) == (home / "Scout").resolve()


def test_malformed_or_empty_pointer_falls_back(home):
    pointer_path(home).parent.mkdir(parents=True)
    pointer_path(home).write_text("{not json")
    assert installed_vault({"HOME": str(home)}) == (home / "Scout").resolve()
    _pointer(home, "")
    assert installed_vault({"HOME": str(home)}) == (home / "Scout").resolve()


def test_cwd_inside_another_vault_is_ignored(tmp_path, home, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "scout-config.yaml").write_text("x: 1\n")
    monkeypatch.chdir(other)
    assert installed_vault({"HOME": str(home)}) == (home / "Scout").resolve()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Scout Test")
    _git(path, "config", "user.email", "scout@example.invalid")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "scout-config.yaml").write_text("x: 1\n")
    _git(path, "add", "scout-config.yaml")
    _git(path, "commit", "-q", "-m", "init")
    return path


def test_roots_are_the_vault_and_its_worktrees(tmp_path):
    vault = _repo(tmp_path / "Scout")
    inside = vault / ".claude" / "worktrees" / "a"
    outside = tmp_path / "elsewhere" / "b"
    for wt in (inside, outside):
        _git(vault, "worktree", "add", "-q", "--detach", str(wt))
    assert set(vault_roots(vault)) == {vault.resolve(), inside.resolve(), outside.resolve()}
    assert vault_roots(vault)[0] == vault.resolve()
    # From a worktree, the same set: they share one repository.
    assert set(vault_roots(outside)) == set(vault_roots(vault))


def test_a_vault_in_a_repo_subfolder_maps_into_each_worktree(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "vault").mkdir()
    (repo / "vault" / "scout-config.yaml").write_text("x: 1\n")
    _git(repo, "add", "vault")
    _git(repo, "commit", "-q", "-m", "vault")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    assert set(vault_roots(repo / "vault")) == {(repo / "vault").resolve(), (wt / "vault").resolve()}


def test_not_a_repo_or_missing_is_just_the_vault(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert vault_roots(plain) == (plain.resolve(),)
    assert vault_roots(tmp_path / "missing") == ((tmp_path / "missing").resolve(),)


@pytest.mark.parametrize("error", [OSError("no git"), subprocess.TimeoutExpired("git", 2)])
def test_git_failure_never_raises(tmp_path, monkeypatch, error):
    vault = _repo(tmp_path / "Scout")

    def boom(*a, **k):
        raise error

    monkeypatch.setattr(vault_mod.subprocess, "run", boom)
    assert vault_roots(vault) == (vault.resolve(),)


def test_unresolvable_vault_falls_back_to_an_absolute_path(tmp_path, monkeypatch):
    def boom(_p):
        raise OSError("symlink loop")

    monkeypatch.setattr(vault_mod.paths, "resolve_path", boom)
    target = tmp_path / "explicit"
    assert installed_vault({"HOME": str(tmp_path), "SCOUT_DATA_DIR": str(target)}) == target.absolute()


# --- a vault worktree stands for the vault (I4) ----------------------------------------------------------------------


def test_a_linked_worktree_as_scout_data_dir_is_the_main_vault(tmp_path, home):
    vault = _repo(tmp_path / "Scout")
    wt = vault / ".claude" / "worktrees" / "a"
    _git(vault, "worktree", "add", "-q", "--detach", str(wt))
    assert installed_vault({"HOME": str(home), "SCOUT_DATA_DIR": str(wt)}) == vault.resolve()
    assert installed_vault({"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}) == vault.resolve()
    _pointer(home, str(wt))
    assert installed_vault({"HOME": str(home)}) == vault.resolve()


def test_a_vault_subfolder_of_a_linked_worktree_maps_into_the_main_one(tmp_path, home):
    repo = _repo(tmp_path / "repo")
    (repo / "vault").mkdir()
    (repo / "vault" / "scout-config.yaml").write_text("x: 1\n")
    _git(repo, "add", "vault")
    _git(repo, "commit", "-q", "-m", "vault")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    assert installed_vault({"HOME": str(home), "SCOUT_DATA_DIR": str(wt / "vault")}) == (repo / "vault").resolve()


@pytest.mark.parametrize("error", [OSError("no git"), subprocess.TimeoutExpired("git", 2)])
def test_main_worktree_never_raises(tmp_path, monkeypatch, error):
    vault = _repo(tmp_path / "Scout")

    def boom(*a, **k):
        raise error

    monkeypatch.setattr(vault_mod.subprocess, "run", boom)
    assert vault_mod.main_worktree(vault) == vault


def test_main_worktree_keeps_odd_git_answers_as_given(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert vault_mod.main_worktree(plain) == plain  # not a repository

    def answer(stdout: bytes):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(vault_mod.subprocess, "run", answer(b"/x\n"))
    assert vault_mod.main_worktree(plain) == plain  # not two lines
    monkeypatch.setattr(vault_mod.subprocess, "run", answer(f"{tmp_path}/wt\n{tmp_path}/bare.git\n".encode()))
    assert vault_mod.main_worktree(plain) == plain  # a bare repository's worktree
    monkeypatch.setattr(vault_mod.subprocess, "run", answer(f"{tmp_path}/wt\n{tmp_path}/main/.git\n".encode()))
    assert vault_mod.main_worktree(plain) == plain  # the path isn't inside the reported worktree


def test_owner_commands_from_a_vault_worktree_file_under_the_main_vault(tmp_path, monkeypatch):
    """I4: `undo` run from a vault worktree (its scripts/scoutctl names the worktree in SCOUT_DATA_DIR) finds the
    change in the main vault's ledger and keys the row to the main vault, where `changes due` reads it."""
    import json
    import os

    from typer.testing import CliRunner

    from scout.changes.store import owner_dir, vault_key
    from scout.cli import app

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    vault = _repo(tmp_path / "Scout")
    (vault / ".gitignore").write_text(".scout-logs/\n")
    (vault / "SKILL.md").write_text("v1\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "seed")
    (vault / "SKILL.md").write_text("v2\n")
    _git(vault, "commit", "-qam", "dreaming [22:12]: SKILL.md self-edit — x")
    wt = vault / ".claude" / "worktrees" / "a"
    _git(vault, "worktree", "add", "-q", "--detach", str(wt))
    runner = CliRunner()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    assert runner.invoke(app, ["changes", "scan"]).exit_code == 0
    changes = json.loads(runner.invoke(app, ["changes", "--json"]).stdout)["changes"]
    cid = next(c["id"] for c in changes if c["author_lane"] == "dreaming")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(wt))
    assert not (wt / ".scout-logs").exists()
    r = runner.invoke(app, ["changes", "undo", cid])
    assert r.exit_code == 0, r.output
    [row] = [json.loads(x) for x in (owner_dir(os.environ) / "engagement.jsonl").read_text().splitlines()]
    assert row["vault_key"] == vault_key(vault)
    due = json.loads(runner.invoke(app, ["changes", "due", "--json"]).stdout)
    assert [r["id"] for r in due["rollbacks"]] == [cid]

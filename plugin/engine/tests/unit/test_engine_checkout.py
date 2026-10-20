"""scout.engine_checkout: the state of the git checkout an engine runs from.

A dev-managed engine is an editable install, so its checkout's working tree IS the code
scheduled runs execute. These tests build real repos: the probe is a handful of git calls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import engine_checkout
from scout.engine_checkout import CheckoutState, checkout_state, live_engine_root, refuse_live_engine_write
from scout.scripts.engine_pointer import EnginePointer, write_pointer


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout on `main` with one commit and the monorepo's plugin/ subdirectory."""
    r = tmp_path / "engine-clone"
    (r / "plugin").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "alex@example.com")
    _git(r, "config", "user.name", "Alex")
    (r / "plugin" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_a_clean_main_checkout_is_neither_dirty_nor_off_release(repo: Path) -> None:
    st = checkout_state(repo / "plugin")
    assert st == CheckoutState(root=repo.resolve(), branch="main", release_tag=None, changes=())
    assert not st.off_release


def test_tracked_edits_and_untracked_files_are_changes(repo: Path) -> None:
    (repo / "plugin" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "plugin" / "new_test.py").write_text("", encoding="utf-8")
    st = checkout_state(repo / "plugin")
    assert st is not None
    assert st.changes == ("plugin/a.py", "plugin/new_test.py")


def test_ignored_files_are_not_changes(repo: Path) -> None:
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "python").write_text("", encoding="utf-8")
    st = checkout_state(repo)
    assert st is not None and st.changes == ()


def test_a_feature_branch_is_off_release(repo: Path) -> None:
    _git(repo, "switch", "-q", "-c", "fix/some-connector")
    st = checkout_state(repo)
    assert st is not None and st.branch == "fix/some-connector" and st.off_release


def test_a_feature_branch_at_a_release_commit_is_still_off_release(repo: Path) -> None:
    """The 10-16 shape: a branch cut at the release tag. Upgrades can't fast-forward it."""
    _git(repo, "tag", "v0.1.0")
    _git(repo, "switch", "-q", "-c", "fix/some-connector")
    st = checkout_state(repo)
    assert st is not None and st.release_tag is None and st.off_release


def test_a_detached_release_tag_is_on_release(repo: Path) -> None:
    _git(repo, "tag", "v0.1.0")
    _git(repo, "switch", "-q", "--detach", "v0.1.0")
    st = checkout_state(repo)
    assert st is not None and st.branch is None and st.release_tag == "v0.1.0" and not st.off_release


def test_a_detached_head_with_no_release_tag_is_off_release(repo: Path) -> None:
    _git(repo, "tag", "not-a-release")
    _git(repo, "switch", "-q", "--detach", "HEAD")
    st = checkout_state(repo)
    assert st is not None and st.branch is None and st.release_tag is None and st.off_release


def test_a_directory_outside_any_repo_has_no_state(tmp_path: Path) -> None:
    assert checkout_state(tmp_path) is None


def test_a_missing_directory_has_no_state(tmp_path: Path) -> None:
    assert checkout_state(tmp_path / "gone") is None


@pytest.mark.parametrize("exc", [OSError("no git"), subprocess.TimeoutExpired(cmd="git", timeout=1)])
def test_git_that_cannot_run_means_no_state(repo: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(engine_checkout.subprocess, "run", boom)
    assert checkout_state(repo) is None


def test_the_probe_takes_no_optional_locks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`git status` refreshes the index under index.lock unless told not to; the probe runs
    from the app and every scheduled run, beside sessions that commit to that checkout."""
    seen: list[dict[str, str]] = []
    real = subprocess.run

    def spy(*a, **k):
        seen.append(k["env"])
        return real(*a, **k)

    monkeypatch.setattr(engine_checkout.subprocess, "run", spy)
    checkout_state(repo)
    assert seen and all(env.get("GIT_OPTIONAL_LOCKS") == "0" for env in seen)


def _pointer(home: Path, engine_root: Path, managed_by: str = "dev") -> None:
    write_pointer(
        EnginePointer(
            version="0.1.0",
            engine_root=str(engine_root),
            python=str(engine_root / ".venv" / "bin" / "python"),
            scoutctl=str(engine_root / ".venv" / "bin" / "scoutctl"),
            vault=str(home / "Scout"),
            managed_by=managed_by,
            written_at="2026-10-20T00:00:00Z",
        ),
        home=home,
    )


def test_live_engine_root_comes_from_the_pointer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert live_engine_root(home=home) is None
    _pointer(home, tmp_path / "anywhere" / "plugin")
    assert live_engine_root(home=home) == tmp_path / "anywhere" / "plugin"


def test_refuses_a_write_into_the_live_engine_root(tmp_path: Path) -> None:
    home, live = tmp_path / "home", tmp_path / "engine" / "plugin"
    (live / "phases").mkdir(parents=True)
    _pointer(home, live)
    why = refuse_live_engine_write(live / "phases", home=home)
    assert why is not None
    assert str(live) in why and "~/.scout-worktrees/" in why


def test_refuses_through_a_symlinked_path(tmp_path: Path) -> None:
    home, live = tmp_path / "home", tmp_path / "engine" / "plugin"
    live.mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "engine")
    _pointer(home, live)
    assert refuse_live_engine_write(tmp_path / "alias" / "plugin", home=home) is not None


def test_refuses_anywhere_in_the_checkout_that_holds_the_live_engine(repo: Path, tmp_path: Path) -> None:
    """The live engine root is the checkout's plugin/; its sibling dirs run live just the same."""
    home = tmp_path / "home"
    (repo / "docs").mkdir()
    _pointer(home, repo / "plugin")
    assert refuse_live_engine_write(repo / "docs", home=home) is not None


def test_still_refuses_the_engine_root_when_git_cannot_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, live = tmp_path / "home", tmp_path / "engine" / "plugin"
    live.mkdir(parents=True)
    _pointer(home, live)

    def boom(*_a, **_k):
        raise OSError("no git")

    monkeypatch.setattr(engine_checkout.subprocess, "run", boom)
    assert refuse_live_engine_write(live, home=home) is not None
    assert refuse_live_engine_write(tmp_path / "elsewhere", home=home) is None


def test_allows_a_write_into_another_checkout(tmp_path: Path) -> None:
    home, live, clone = tmp_path / "home", tmp_path / "engine" / "plugin", tmp_path / "clone" / "plugin"
    live.mkdir(parents=True)
    clone.mkdir(parents=True)
    _pointer(home, live)
    assert refuse_live_engine_write(clone, home=home) is None


def test_allows_any_write_when_there_is_no_pointer(tmp_path: Path) -> None:
    assert refuse_live_engine_write(tmp_path, home=tmp_path / "home") is None

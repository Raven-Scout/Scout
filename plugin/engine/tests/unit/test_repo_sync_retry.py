"""A sync that stops after a replay: the retry must never take Agnes's change back off Agnes (#609).

A replay moves HEAD and the link state's ``last_mirror``; ``last_laptop`` moves
only when the sync finishes. These tests stop a sync between the two, with a
refused push and with a real SIGKILL, then sync again. They walk the mirror's
history as well as its tip: a mirror commit that drops Agnes's file and a later
one that restores it still reverted it on Agnes for a while.
"""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scout import repo_sync as rs

from .privacy_vault_helpers import git
from .test_repo_sync import V0_18_10_STATE_KEYS, FakeHost, _bare_git, _commit, _commit_on_mirror, _link
from .test_repo_sync import host as host  # noqa: F401  (fixture)
from .test_repo_sync import vault as vault  # noqa: F401  (fixture)
from .test_repo_sync_only import AGNES_NOTE, _linked, _tip_paths

AGNES_FILE = "knowledge-base/topics/misc/cloud.md"
LAPTOP_FILE = "knowledge-base/people/priya.md"


def _refuse_pushes(host: FakeHost) -> None:
    hook = host.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'try again later' >&2\nexit 1\n")
    hook.chmod(0o755)


def _allow_pushes(host: FakeHost) -> None:
    (host.bare / "hooks" / "pre-receive").unlink()


def _tip(host: FakeHost, rel: str) -> str:
    return _bare_git(host.bare, "show", f"main:{rel}")


def _stays_on_agnes_after(host: FakeHost, agnes_sha: str, rel: str = AGNES_FILE) -> bool:
    """Whether every mirror commit since ``agnes_sha`` still has ``rel``."""
    return all(
        rel in _bare_git(host.bare, "ls-tree", "-r", "--name-only", rev).splitlines()
        for rev in _bare_git(host.bare, "rev-list", f"{agnes_sha}..main").split()
    )


def _replay_commits(vault: Path, agnes_sha: str) -> int:
    return git(vault, "log", "--format=%B").count(f"{rs.REPLAY_TRAILER}: {agnes_sha}")


def _failed_after_a_replay(vault: Path, host: FakeHost, tmp_path: Path) -> str:
    """A laptop commit and an Agnes commit, then a sync whose push is refused. The Agnes commit's sha."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "laptop edit\n", "laptop edit")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "push_failed"
    _allow_pushes(host)
    return agnes_sha


# ----- a refused push ------------------------------------------------------------------------


def test_a_retry_after_a_refused_push_keeps_the_agnes_commit(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    assert (vault / AGNES_FILE).exists()
    assert _bare_git(host.bare, "rev-parse", "main").strip() == agnes_sha

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _tip(host, LAPTOP_FILE) == "laptop edit\n"
    assert _stays_on_agnes_after(host, agnes_sha)
    assert _replay_commits(vault, agnes_sha) == 1
    assert "Scout sync: 1 commit(s)" in _bare_git(host.bare, "log", "-1", "--format=%B", "main")
    assert rs.sync(vault, host).pushed == []


def test_a_push_refused_twice_still_keeps_the_agnes_commit(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_laptop_commits_after_the_failure_go_up_with_the_rest(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    _commit(vault, "knowledge-base/people/sam.md", "later edit\n", "later edit")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert _stays_on_agnes_after(host, agnes_sha)
    message = _bare_git(host.bare, "log", "-1", "--format=%B", "main")
    assert "Scout sync: 2 commit(s)" in message and "laptop edit" in message and "later edit" in message
    assert _tip(host, "knowledge-base/people/sam.md") == "later edit\n"


def test_an_edit_of_the_agnes_file_after_the_failure_does_not_stop_the_retry(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    """The replay isn't redone: added by Agnes and then edited here would look added on both sides."""
    _failed_after_a_replay(vault, host, tmp_path)
    _commit(vault, AGNES_FILE, "edited on the laptop\n", "edit the agnes note")

    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "edited on the laptop\n"


def test_removing_the_agnes_file_after_the_failure_is_the_laptops_call(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    (vault / AGNES_FILE).unlink()
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "drop the agnes note")

    rs.sync(vault, host)

    assert not (vault / AGNES_FILE).exists()
    assert not _stays_on_agnes_after(host, agnes_sha)
    assert _replay_commits(vault, agnes_sha) == 1


def test_a_new_agnes_commit_before_the_retry_replays_and_both_survive(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    second = _commit_on_mirror(tmp_path, host, {"knowledge-base/topics/misc/more.md": "second\n"}, "agnes second")

    result = rs.sync(vault, host)

    assert len(result.replayed) == 1
    assert _replay_commits(vault, second) == 1
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _tip(host, "knowledge-base/topics/misc/more.md") == "second\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_replay_stopped_by_a_conflict_keeps_the_commit_it_already_replayed(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "laptop version\n", "laptop")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "agnes version\n"}, "agnes edit")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "replay_conflict"
    assert _replay_commits(vault, agnes_sha) == 1

    (vault / f"{LAPTOP_FILE}.proposed-merge").unlink()
    _commit(vault, LAPTOP_FILE, "agnes version\n", "take the agnes version")
    rs.sync(vault, host)

    assert _tip(host, LAPTOP_FILE) == "agnes version\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_failed_sync_leaves_a_state_an_older_engine_reads(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _failed_after_a_replay(vault, host, tmp_path)
    data = json.loads(rs.state_path(vault).read_text(encoding="utf-8"))
    assert set(data) <= V0_18_10_STATE_KEYS and data["schema_version"] == 1


# ----- SIGKILL ---------------------------------------------------------------------------------


def _kill_before_head_moves(real: Callable[..., Any], *a: Any, **k: Any) -> Any:
    if a[1] == "update-ref" and "HEAD" in a:
        os.kill(os.getpid(), signal.SIGKILL)
    return real(*a, **k)


def _sync_killed_at(vault: Path, host: FakeHost, point: str) -> None:
    """Run a sync in a child process that gets a real SIGKILL at ``point``: nothing after it runs."""

    def die(*_a: Any, **_k: Any) -> None:
        os.kill(os.getpid(), signal.SIGKILL)

    pid = os.fork()
    if pid == 0:
        try:
            if point == "head":
                real_git = rs.git
                rs.git = lambda *a, **k: _kill_before_head_moves(real_git, *a, **k)  # type: ignore[assignment]
            elif point == "state":
                rs.save_state = die  # type: ignore[assignment]
            elif point == "push":
                rs.push = die  # type: ignore[assignment]
            elif point == "after-push":
                real_push = rs.push

                def push_then_die(*a: Any, **k: Any) -> None:
                    real_push(*a, **k)
                    die()

                rs.push = push_then_die  # type: ignore[assignment]
            rs.sync(vault, host)
        finally:
            os._exit(3)  # the sync never got to the crash point
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL


@pytest.mark.parametrize("point", ["head", "state", "push", "after-push"])
def test_a_sync_killed_after_the_replay_loses_nothing(vault: Path, host: FakeHost, tmp_path: Path, point: str) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "laptop edit\n", "laptop edit")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")

    _sync_killed_at(vault, host, point)
    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _tip(host, LAPTOP_FILE) == "laptop edit\n"
    assert _stays_on_agnes_after(host, agnes_sha)
    assert _replay_commits(vault, agnes_sha) == 1
    assert git(vault, "status", "--porcelain", "--untracked-files=no").strip() == ""
    assert rs.sync(vault, host).pushed == []


def test_a_replay_the_state_never_recorded_is_not_redone(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    """Killed between moving HEAD and saving the state: the commit's trailer is what's left to go on."""
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _sync_killed_at(vault, host, "state")
    state = rs.load_state(vault)
    assert state is not None and state.last_mirror != agnes_sha
    replay = git(vault, "rev-parse", "HEAD").strip()
    _commit(vault, AGNES_FILE, "edited on the laptop\n", "edit the agnes note")

    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "edited on the laptop\n"
    assert _replay_commits(vault, agnes_sha) == 1
    state = rs.load_state(vault)
    assert state is not None
    assert {"laptop": replay, "mirror": agnes_sha, "kind": "replay"} in state.pairs


# ----- what an ordinary sync does ----------------------------------------------------------------


def test_after_a_finished_replay_laptop_commits_go_up_one_by_one(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    assert len(rs.sync(vault, host).replayed) == 1
    _commit(vault, LAPTOP_FILE, "first\n", "first edit")
    _commit(vault, LAPTOP_FILE, "second\n", "second edit")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 2
    assert _bare_git(host.bare, "log", "-2", "--format=%s", "main").split("\n")[:2] == ["second edit", "first edit"]
    assert not result.notes


def test_a_refused_push_with_no_replay_is_retried_one_by_one(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first edit")
    _commit(vault, LAPTOP_FILE, "second\n", "second edit")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    assert len(rs.sync(vault, host).pushed) == 2


# ----- privacy.sync_only -------------------------------------------------------------------------


def test_the_retry_still_warns_about_a_file_it_takes_off_agnes(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    """The failed sync's warning went with its failure; the file only leaves Agnes's tip on the retry."""
    _linked(vault, host)
    sha = _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "written in agnes\n"}, "note in agnes")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    result = rs.sync(vault, host)

    assert result.replay_skipped == [{"path": AGNES_NOTE, "mirror_commit": sha}]
    assert any(AGNES_NOTE in n for n in result.notes)
    assert AGNES_NOTE not in _tip_paths(host)
    assert rs.sync(vault, host).replay_skipped == []


def test_a_skipped_file_agnes_already_removed_gets_no_warning_on_the_retry(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _linked(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "written in agnes\n"}, "note in agnes")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)
    _commit_on_mirror(tmp_path, host, {AGNES_NOTE: None}, "remove it again")

    assert rs.sync(vault, host).replay_skipped == []

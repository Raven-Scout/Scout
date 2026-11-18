"""``repo sync`` pulls before it pushes when Agnes says it wrote on top (Shared Areas S1b, #6090 §5.6).

A8b's repository status carries ``pending_server_commits``: the commits Agnes
made on top of the laptop's last push (a stop's write-back, say). Above 0, sync
fetches and replays them first, whatever ``ls-remote`` answered. The host is the
fake over a local bare repository from ``test_repo_sync``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import repo_sync as rs

from .test_repo_sync import FakeHost, _bare_git, _commit, _commit_on_mirror, _link, mirror_history_paths
from .test_repo_sync import vault as vault  # noqa: F401  (the fixture)

AGNES_NOTE = "knowledge-base/topics/misc/agnes-note.md"
LAPTOP_NOTE = "knowledge-base/topics/misc/laptop.md"


class PendingHost(FakeHost):
    """A host whose status says Agnes made ``pending`` commits on top of the laptop's last push."""

    pending = 0

    def status(self) -> rs.HostStatus:
        return rs.HostStatus(self.exists, True, str(self.bare), self.local_only, self.pending)


@pytest.fixture
def host(tmp_path: Path) -> PendingHost:
    bare = tmp_path / "mirror.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return PendingHost(bare)


def _stale_ls_remote(monkeypatch: pytest.MonkeyPatch, vault: Path) -> None:
    """ls-remote answers the last synced commit, as if Agnes's write hadn't shown up there yet."""

    def stale(_vault: Path, _host: object, _url: str) -> str:
        state = rs.load_state(vault)
        assert state is not None
        return state.last_mirror

    monkeypatch.setattr(rs, "remote_head", stale)


def _agnes_and_laptop_commits(tmp_path: Path, host: PendingHost, vault: Path) -> str:
    _link(vault, host)
    sha = _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "Agnes wrote this.\n"}, "Agnes: a commit")
    _commit(vault, LAPTOP_NOTE, "Laptop.\n", "laptop: a note")
    return sha


def test_sync_pulls_first_when_agnes_says_it_wrote_on_top(
    vault: Path, host: PendingHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agnes_sha = _agnes_and_laptop_commits(tmp_path, host, vault)
    host.pending = 1
    _stale_ls_remote(monkeypatch, vault)

    result = rs.sync(vault, host)

    assert len(result.replayed) == 1
    assert (vault / AGNES_NOTE).read_text() == "Agnes wrote this.\n"
    assert (
        "Agnes reported 1 commit(s) on top of the last push, so this sync fetched first and replayed 1 before pushing"
        in result.notes
    )
    assert _bare_git(host.bare, "merge-base", "--is-ancestor", agnes_sha, "main") == ""  # pushed on top of it
    assert LAPTOP_NOTE in mirror_history_paths(host.bare)


def test_without_pending_commits_a_stale_ls_remote_decides(
    vault: Path, host: PendingHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The control: an Agnes before A8b reports no pending_server_commits, so only ls-remote says to pull.
    _agnes_and_laptop_commits(tmp_path, host, vault)
    _stale_ls_remote(monkeypatch, vault)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "push_failed"  # not a fast-forward: nothing was pulled first
    assert LAPTOP_NOTE not in mirror_history_paths(host.bare)


def test_pending_commits_already_replayed_push_on_top(vault: Path, host: PendingHost, tmp_path: Path) -> None:
    _agnes_and_laptop_commits(tmp_path, host, vault)
    host.pending = 1
    assert len(rs.sync(vault, host).replayed) == 1
    # Agnes's count lags the push: a second sync fetches again, finds nothing new and pushes nothing.
    again = rs.sync(vault, host)
    assert again.replayed == [] and again.pushed == []


@pytest.mark.parametrize(("raw", "want"), [(3, 3), (0, 0), (None, 0), ("2", 0), (True, 0)])
def test_the_status_reads_pending_server_commits(raw: object, want: int) -> None:
    data = {"exists": True, "clone_url": "x", "pending_server_commits": raw}
    assert rs.AgnesHost._status(data).pending_server_commits == want
    assert rs.AgnesHost._status({"exists": True}).pending_server_commits == 0  # an Agnes before A8b

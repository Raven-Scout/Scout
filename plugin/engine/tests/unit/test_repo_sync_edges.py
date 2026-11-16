"""The edge cases around replay, retry and squash left after #609 (#613).

- A sync killed after its push landed must not replay the laptop's own mirror commit.
- A sync judges policy from the commit it mirrors, not an uncommitted config edit.
- A recorded replay that ``git reset`` drops comes off Agnes's tip with a warning.
- ``_skips_since_last_push`` stays cheap however many commits it has to walk.
- A rewrite or a second-parent merge between attempts leaves no mirror commit without Agnes's file.

Same harness as ``test_repo_sync_retry``: a fake host over a local bare repository and real SIGKILLs.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from scout import repo_sync as rs
from scout.cli import app
from scout.kb.privacy import classify, load_policy, policy_from_overlay
from scout.scripts import privacy_migrate

from .privacy_vault_helpers import git
from .test_repo_sync import (
    V0_18_10_STATE_KEYS,
    FakeHost,
    _bare_git,
    _commit,
    _commit_on_mirror,
    _link,
    mirror_history_paths,
    mirror_policy,
)
from .test_repo_sync import host as host  # noqa: F401  (fixture)
from .test_repo_sync import vault as vault  # noqa: F401  (fixture)
from .test_repo_sync_only import AGNES_NOTE, _linked, _set_privacy, _tip_paths, _write_privacy
from .test_repo_sync_retry import (
    AGNES_FILE,
    LAPTOP_FILE,
    _allow_pushes,
    _failed_after_a_replay,
    _refuse_pushes,
    _replay_commits,
    _stays_on_agnes_after,
    _sync_killed_at,
    _tip,
)

# Written out, not rs.PENDING_REL: a forged note is a file anyone could write at this path.
NOTE = ".scout-state/repo-sync-pending.json"
SAM = "knowledge-base/people/sam.md"
NEW_FILE = "knowledge-base/people/new-person.md"


def _head(vault: Path) -> str:
    return git(vault, "rev-parse", "HEAD").strip()


def _replayed_commits(vault: Path) -> int:
    return git(vault, "log", "--format=%B").count(rs.REPLAY_TRAILER)


def _remove(vault: Path, rel: str, subject: str) -> None:
    (vault / rel).unlink()
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def _edited_then_killed(vault: Path, host: FakeHost, point: str = "after-push") -> None:
    """A synced file, then an edit whose sync dies after the push landed, before the state is saved."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first version")
    rs.sync(vault, host)
    _commit(vault, LAPTOP_FILE, "second\n", "second version")
    _sync_killed_at(vault, host, point)
    assert _tip(host, LAPTOP_FILE) == "second\n"
    state = rs.load_state(vault)
    assert state is not None and state.last_mirror != _bare_git(host.bare, "rev-parse", "main").strip()


# ----- 1. a kill after the push landed -----------------------------------------------------------


@pytest.mark.parametrize("point", ["before-ref", "after-push"])
def test_an_undo_after_the_kill_stays_undone(vault: Path, host: FakeHost, point: str) -> None:
    _edited_then_killed(vault, host, point)
    _commit(vault, LAPTOP_FILE, "first\n", "undo")
    head = _head(vault)

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert _head(vault) == head
    assert (vault / LAPTOP_FILE).read_text() == "first\n"
    assert _tip(host, LAPTOP_FILE) == "first\n"
    assert _replayed_commits(vault) == 0


def test_a_file_deleted_after_the_kill_stays_deleted(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, NEW_FILE, "new\n", "add a file")
    _sync_killed_at(vault, host, "after-push")
    _remove(vault, NEW_FILE, "drop it again")

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert not (vault / NEW_FILE).exists()
    assert NEW_FILE not in _tip_paths(host)


def test_two_commits_on_one_line_do_not_conflict_on_the_retry(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first version")
    _commit(vault, LAPTOP_FILE, "second\n", "second version")
    _sync_killed_at(vault, host, "after-push")
    head = _head(vault)

    result = rs.sync(vault, host)

    assert result.replayed == [] and result.pushed == []
    assert _head(vault) == head
    assert rs.sync(vault, host).pushed == []


def test_a_push_reported_as_failed_that_still_landed_is_not_replayed(
    vault: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection drops after Agnes took the push: the laptop thinks it failed, and it didn't."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first version")
    rs.sync(vault, host)
    _commit(vault, LAPTOP_FILE, "second\n", "second version")
    real_push = rs.push

    def landed_then_failed(*a: Any, **k: Any) -> None:
        real_push(*a, **k)
        raise rs.RepoSyncError("push_failed", "the connection dropped")

    monkeypatch.setattr(rs, "push", landed_then_failed)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    monkeypatch.setattr(rs, "push", real_push)
    _commit(vault, LAPTOP_FILE, "first\n", "undo")

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert (vault / LAPTOP_FILE).read_text() == "first\n"
    assert _tip(host, LAPTOP_FILE) == "first\n"


def test_a_reset_after_the_kill_is_not_undone_and_says_what_leaves_agnes(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, NEW_FILE, "new\n", "add a file")
    _sync_killed_at(vault, host, "after-push")
    pushed = _bare_git(host.bare, "rev-parse", "main").strip()
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    head = _head(vault)

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert _head(vault) == head
    assert not (vault / NEW_FILE).exists()
    assert NEW_FILE not in _tip_paths(host)
    assert any(NEW_FILE in n and pushed[:12] in n for n in result.notes)


def test_an_edit_amended_after_the_kill_warns_with_the_right_words(vault: Path, host: FakeHost) -> None:
    """The tip keeps the path with new content, so "removes" alone would be wrong."""
    _edited_then_killed(vault, host)
    (vault / LAPTOP_FILE).write_text("amended\n", encoding="utf-8")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "amended")

    result = rs.sync(vault, host)

    assert _tip(host, LAPTOP_FILE) == "amended\n"
    assert any(LAPTOP_FILE in n and "removes or replaces" in n for n in result.notes)


def test_an_amend_after_the_kill_is_not_replayed(vault: Path, host: FakeHost) -> None:
    _edited_then_killed(vault, host)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "reworded")

    result = rs.sync(vault, host)

    assert result.replayed == [] and result.pushed == []
    assert _replayed_commits(vault) == 0


def test_a_kill_after_a_squashed_push_recognises_the_squash(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first version")
    rs.sync(vault, host)
    _commit(vault, LAPTOP_FILE, "second\n", "second version")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _sync_killed_at(vault, host, "after-push")
    _commit(vault, LAPTOP_FILE, "first\n", "undo")

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert _replay_commits(vault, agnes_sha) == 1
    assert _replayed_commits(vault) == 1
    assert _tip(host, LAPTOP_FILE) == "first\n"
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_new_agnes_commit_on_top_of_the_landed_push_still_replays(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _edited_then_killed(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _commit(vault, LAPTOP_FILE, "first\n", "undo")

    result = rs.sync(vault, host)

    assert len(result.replayed) == 1
    assert _replay_commits(vault, agnes_sha) == 1
    assert _replayed_commits(vault) == 1
    assert (vault / LAPTOP_FILE).read_text() == "first\n"
    assert _tip(host, LAPTOP_FILE) == "first\n"
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_commits_after_the_kill_go_up_one_by_one(vault: Path, host: FakeHost) -> None:
    _edited_then_killed(vault, host)
    _commit(vault, SAM, "a\n", "third")
    _commit(vault, SAM, "b\n", "fourth")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 2
    assert _bare_git(host.bare, "log", "-2", "--format=%s", "main").split("\n")[:2] == ["fourth", "third"]
    assert not result.notes


def test_a_commit_the_landed_push_skipped_is_not_pushed_over_it(vault: Path, host: FakeHost) -> None:
    """The middle commit touches only a Local-Only file, so it has no mirror commit, and it's older than the tip."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "first\n", "first version")
    _commit(vault, "trading/more.md", "held\n", "local-only edit")
    _commit(vault, LAPTOP_FILE, "second\n", "second version")
    _sync_killed_at(vault, host, "after-push")
    tip = _bare_git(host.bare, "rev-parse", "main").strip()

    result = rs.sync(vault, host)

    assert result.pushed == []
    assert _bare_git(host.bare, "rev-parse", "main").strip() == tip


def test_a_failed_retry_keeps_the_recognised_push(vault: Path, host: FakeHost) -> None:
    _edited_then_killed(vault, host)
    _commit(vault, SAM, "later\n", "later")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert len(result.pushed) == 1
    assert _bare_git(host.bare, "log", "-1", "--format=%s", "main").strip() == "later"
    assert not (vault / NOTE).exists()


def test_the_note_is_ignored_while_it_exists_and_gone_after_a_finished_sync(vault: Path, host: FakeHost) -> None:
    _edited_then_killed(vault, host)
    assert (vault / NOTE).exists()
    assert git(vault, "check-ignore", NOTE).strip() == NOTE
    assert git(vault, "status", "--porcelain", "--", NOTE).strip() == ""

    rs.sync(vault, host)

    assert not (vault / NOTE).exists()
    data = json.loads(rs.state_path(vault).read_text(encoding="utf-8"))
    assert set(data) <= V0_18_10_STATE_KEYS and data["schema_version"] == 1


def test_a_sync_with_nothing_to_push_clears_a_note_a_refused_push_left(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "edit\n", "edit")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)
    assert (vault / NOTE).exists()
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    assert rs.sync(vault, host).pushed == []

    assert not (vault / NOTE).exists()


def test_unlink_forgets_the_note(vault: Path, host: FakeHost) -> None:
    _edited_then_killed(vault, host)

    rs.unlink(vault, host)

    assert not (vault / NOTE).exists()


# ----- 1. a note is only a claim -----------------------------------------------------------------


def _write_note(vault: Path, *pushes: tuple[str, str]) -> None:
    doc = {"schema_version": 1, "pushes": [{"laptop": c, "mirror": m} for c, m in pushes]}
    (vault / NOTE).parent.mkdir(parents=True, exist_ok=True)
    (vault / NOTE).write_text(json.dumps(doc), encoding="utf-8")


def _crafted(
    vault: Path,
    host: FakeHost,
    agnes_sha: str,
    edits: dict[str, str],
    *,
    message: str | None = None,
    on_head: bool = True,
) -> str:
    """A laptop commit built to pass for ``agnes_sha``'s push: its identities, and ``edits`` as its changes."""
    git(vault, "fetch", "-q", str(host.bare), "main")
    meta = rs.commit_meta(vault, agnes_sha)
    for rel, text in edits.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text, encoding="utf-8")
    git(vault, "add", "-A")
    tree = git(vault, "write-tree").strip()
    crafted = rs._commit_tree(vault, tree, [_head(vault)], message or meta.message, meta.env())
    if on_head:
        git(vault, "update-ref", "HEAD", crafted)
    else:
        git(vault, "reset", "-q", "--hard", "HEAD")
    return crafted


def _assert_replayed(vault: Path, host: FakeHost, result: rs.Result, agnes_sha: str) -> None:
    assert len(result.replayed) == 1
    assert (vault / AGNES_FILE).read_text() == "written in agnes\n"
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_note_naming_an_ordinary_commit_does_not_skip_the_agnes_commit(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _commit(vault, LAPTOP_FILE, "edit\n", "edit")
    _write_note(vault, (_head(vault), agnes_sha))

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_a_note_naming_a_commit_that_does_not_exist_does_not_skip_the_agnes_commit(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _write_note(vault, ("0" * 40, agnes_sha))

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        '{"schema_version": 2, "pushes": []}',
        '{"schema_version": 1}',
        '{"schema_version": 1, "pushes": {"a": "b"}}',
        '{"schema_version": 1, "pushes": ["a"]}',
        '{"schema_version": 1, "pushes": [{"laptop": "a"}]}',
        '{"schema_version": 1, "pushes": [{"laptop": 1, "mirror": 2}]}',
    ],
)
def test_an_unreadable_note_counts_for_nothing(vault: Path, host: FakeHost, tmp_path: Path, body: str) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    (vault / NOTE).parent.mkdir(parents=True, exist_ok=True)
    (vault / NOTE).write_text(body, encoding="utf-8")

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_identities_are_not_enough_the_commit_must_hold_the_change(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    crafted = _crafted(vault, host, agnes_sha, {LAPTOP_FILE: "edit\n"})
    _write_note(vault, (crafted, agnes_sha))

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_a_message_no_sync_writes_is_not_a_landed_push(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    crafted = _crafted(vault, host, agnes_sha, {AGNES_FILE: "written in agnes\n"}, message="something else\n")
    _write_note(vault, (crafted, agnes_sha))

    rs.sync(vault, host)

    state = rs.load_state(vault)
    assert state is not None
    assert not [p for p in state.pairs if p["mirror"] == agnes_sha]
    assert _tip(host, AGNES_FILE) == "written in agnes\n"


def test_other_identities_are_not_a_landed_push(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    """The harness pins every commit's dates, so this one sets its own: the only thing that differs from the push."""
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    crafted = _crafted(vault, host, agnes_sha, {AGNES_FILE: "written in agnes\n"}, on_head=False)
    meta = rs.commit_meta(vault, crafted)
    other = rs._commit_tree(vault, meta.tree, [_head(vault)], meta.message, {**meta.env(), "GIT_AUTHOR_NAME": "Sam"})
    git(vault, "reset", "-q", "--hard", other)
    _write_note(vault, (other, agnes_sha))

    rs.sync(vault, host)

    state = rs.load_state(vault)
    assert state is not None
    assert not [p for p in state.pairs if p.get("kind") == rs.RECOVERED_PUSH_KIND]


def test_a_trailer_on_a_commit_with_the_replays_identities_still_has_to_hold_the_change(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    git(vault, "fetch", "-q", str(host.bare), "main")
    message = rs._replay_message(rs.commit_meta(vault, agnes_sha), agnes_sha)
    _crafted(vault, host, agnes_sha, {LAPTOP_FILE: "edit\n"}, message=message)

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_a_new_agnes_commit_after_the_history_was_rewritten_still_replays(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    """The commits since the last sync can't be listed, so no trailer is read at all."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "edit\n", "edit")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_a_crafted_commit_in_head_keeps_the_agnes_file_on_both_sides(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    """Passing for the push takes holding every change it made, so nothing is lost by not replaying it."""
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    crafted = _crafted(vault, host, agnes_sha, {AGNES_FILE: "written in agnes\n"})
    _write_note(vault, (crafted, agnes_sha))

    rs.sync(vault, host)

    assert (vault / AGNES_FILE).read_text() == "written in agnes\n"
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_crafted_commit_outside_head_cannot_drop_the_agnes_file_silently(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    crafted = _crafted(vault, host, agnes_sha, {AGNES_FILE: "written in agnes\n"}, on_head=False)
    _write_note(vault, (crafted, agnes_sha))

    result = rs.sync(vault, host)

    assert any(AGNES_FILE in n and agnes_sha[:12] in n for n in result.notes)
    assert _bare_git(host.bare, "show", f"{agnes_sha}:{AGNES_FILE}") == "written in agnes\n"


def test_a_crafted_commit_cannot_lose_a_file_outside_the_allowlist_silently(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _linked(vault, host)
    inside = "knowledge-base/projects/from-agnes.md"
    agnes_sha = _commit_on_mirror(tmp_path, host, {inside: "allowed\n", AGNES_NOTE: "outside\n"}, "two notes")
    crafted = _crafted(vault, host, agnes_sha, {inside: "allowed\n"})
    _write_note(vault, (crafted, agnes_sha))

    result = rs.sync(vault, host)

    assert result.replay_skipped == [{"path": AGNES_NOTE, "mirror_commit": agnes_sha}]
    assert AGNES_NOTE not in _tip_paths(host)


def _forged_move_saved_by_a_failed_sync(vault: Path, host: FakeHost, tmp_path: Path) -> str:
    """A note and a commit built to pass for a landed push, which also moves a file out of ``hold/``."""
    _link(vault, host)
    _commit(vault, "hold/x.md", "kept here\n", "hold a file")
    rs.sync(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    (vault / "open").mkdir()
    git(vault, "mv", "hold/x.md", "open/x.md")
    crafted = _crafted(vault, host, agnes_sha, {AGNES_FILE: "written in agnes\n"})
    _write_note(vault, (crafted, agnes_sha))

    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "push_failed"
    _allow_pushes(host)
    state = rs.load_state(vault)
    assert state is not None
    assert {"laptop": crafted, "mirror": agnes_sha, "kind": rs.RECOVERED_PUSH_KIND} in state.pairs
    return crafted


def test_a_commit_recognised_from_the_note_is_not_trusted_by_the_move_gate(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _forged_move_saved_by_a_failed_sync(vault, host, tmp_path)
    host.local_only = (*host.local_only, "hold/")

    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)

    assert e.value.code == "local_only_move"
    assert "open/x.md" not in _bare_git(host.bare, "ls-tree", "-r", "--name-only", "main").splitlines()


def test_a_commit_recognised_from_the_note_is_no_base_for_the_move_gate(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _forged_move_saved_by_a_failed_sync(vault, host, tmp_path)
    host.local_only = (*host.local_only, "hold/")
    state = rs.load_state(vault)
    assert state is not None
    state.last_laptop = "0" * 40
    rs.save_state(vault, state)

    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)

    assert e.value.code == "local_only_move"


def test_the_move_gate_runs_before_a_landed_push_is_recognised(vault: Path, host: FakeHost) -> None:
    """A refused sync must leave the link state as it was, the landed push still unrecorded."""
    _link(vault, host)
    _commit(vault, "hold/x.md", "kept here\n", "hold a file")
    rs.sync(vault, host)
    (vault / "open").mkdir()
    git(vault, "mv", "hold/x.md", "open/x.md")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "move")
    _sync_killed_at(vault, host, "after-push")
    before = rs.state_path(vault).read_text(encoding="utf-8")
    host.local_only = (*host.local_only, "hold/")

    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)

    assert e.value.code == "local_only_move"
    assert rs.state_path(vault).read_text(encoding="utf-8") == before


def test_a_note_with_a_null_commit_counts_for_nothing(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    doc = {"schema_version": 1, "pushes": [{"laptop": None, "mirror": agnes_sha}]}
    (vault / NOTE).parent.mkdir(parents=True, exist_ok=True)
    (vault / NOTE).write_text(json.dumps(doc), encoding="utf-8")

    _assert_replayed(vault, host, rs.sync(vault, host), agnes_sha)


def test_a_crafted_commit_outside_head_cannot_hide_behind_heads_own_edit(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    """HEAD isn't back to what Agnes replaced, so only the note's claim says this came from a commit now gone."""
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "base\n", "base")
    rs.sync(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "edited in agnes\n"}, "agnes edit")
    _commit(vault, LAPTOP_FILE, "edited here\n", "laptop edit")
    crafted = _crafted(vault, host, agnes_sha, {LAPTOP_FILE: "edited in agnes\n"}, on_head=False)
    _write_note(vault, (crafted, agnes_sha))

    result = rs.sync(vault, host)

    assert result.dropped == [{"path": LAPTOP_FILE, "mirror_commit": agnes_sha}]
    assert _tip(host, LAPTOP_FILE) == "edited here\n"


def test_hiding_an_agnes_deletion_behind_a_note_loses_nothing(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, SAM, "kept\n", "edit")
    rs.sync(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {SAM: None}, "agnes removes it")
    crafted = _crafted(vault, host, agnes_sha, {LAPTOP_FILE: "edit\n"})
    _write_note(vault, (crafted, agnes_sha))

    rs.sync(vault, host)

    assert (vault / SAM).read_text() == "kept\n"
    assert _tip(host, SAM) == "kept\n"


# ----- 1. a push that stripped a file the rules no longer refuse -----------------------------------

STRIPPED = "notes/held/x.md"


def _uploaded_then_stripped(vault: Path, host: FakeHost) -> list[str]:
    """A file on Agnes, then a commit whose rule takes it off the tip. The list as it was before the rule."""
    _link(vault, host)
    _commit(vault, STRIPPED, "on both sides\n", "a file")
    rs.sync(vault, host)
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    before = list(config["privacy"].get("local_only_add") or [])
    _set_privacy(vault, local_only_add=[*before, "**/held/**"])
    return before


def _assert_laptop_keeps_it(vault: Path, result: rs.Result) -> None:
    assert result.replayed == []
    assert _replayed_commits(vault) == 0
    assert (vault / STRIPPED).read_text() == "on both sides\n"
    assert not list(vault.rglob("*.proposed-merge"))


def test_a_rule_removed_after_the_kill_does_not_delete_the_laptops_file(vault: Path, host: FakeHost) -> None:
    before = _uploaded_then_stripped(vault, host)
    _sync_killed_at(vault, host, "after-push")
    assert STRIPPED not in _tip_paths(host)
    _write_privacy(vault, local_only_add=before)

    result = rs.sync(vault, host)

    _assert_laptop_keeps_it(vault, result)
    assert STRIPPED not in _tip_paths(host)


def test_a_plain_retry_does_not_delete_the_laptops_file(vault: Path, host: FakeHost) -> None:
    """The commit holds the rule and the live config doesn't, from before the kill: no owner step after it."""
    before = _uploaded_then_stripped(vault, host)
    _write_privacy(vault, local_only_add=before)
    _sync_killed_at(vault, host, "after-push")
    assert STRIPPED not in _tip_paths(host)

    result = rs.sync(vault, host)

    _assert_laptop_keeps_it(vault, result)
    assert STRIPPED not in _tip_paths(host)


def test_an_agnes_entry_removed_after_the_kill_does_not_delete_the_laptops_file(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, "hold/x.md", "on both sides\n", "a file")
    rs.sync(vault, host)
    host.local_only = (*host.local_only, "hold/")
    _commit(vault, LAPTOP_FILE, "edit\n", "edit")
    _sync_killed_at(vault, host, "after-push")
    assert "hold/x.md" not in _tip_paths(host)
    host.local_only = tuple(e for e in host.local_only if e != "hold/")

    result = rs.sync(vault, host)

    assert result.replayed == []
    assert (vault / "hold/x.md").read_text() == "on both sides\n"
    assert _tip(host, "hold/x.md") == "on both sides\n"


def test_a_rule_removal_committed_after_the_kill_is_no_conflict(vault: Path, host: FakeHost) -> None:
    before = _uploaded_then_stripped(vault, host)
    _sync_killed_at(vault, host, "after-push")
    _set_privacy(vault, subject="drop the rule", local_only_add=before)

    result = rs.sync(vault, host)

    _assert_laptop_keeps_it(vault, result)
    assert _tip(host, STRIPPED) == "on both sides\n"


def test_a_rule_on_and_off_in_two_commits_is_no_conflict_on_the_retry(vault: Path, host: FakeHost) -> None:
    before = _uploaded_then_stripped(vault, host)
    _set_privacy(vault, subject="drop the rule", local_only_add=before)
    _sync_killed_at(vault, host, "after-push")

    result = rs.sync(vault, host)

    _assert_laptop_keeps_it(vault, result)
    assert _tip(host, STRIPPED) == "on both sides\n"


# ----- 2. an uncommitted config edit --------------------------------------------------------------

HELD_GLOB = "**/held/**"  # not a plain path, so the host's list never carries it
HELD = "notes/held/x.md"
PLAIN_NOTE = "notes/a.md"
NOTES_PRIVATE = [{"glob": "notes/**", "visibility": "private"}]


def _label(host: FakeHost, rel: str = PLAIN_NOTE) -> str:
    return next(lb["visibility"] for lb in mirror_policy(host.bare)["labels"] if lb["path"] == rel)


def test_link_labels_by_an_uncommitted_tightening(vault: Path, host: FakeHost) -> None:
    _commit(vault, PLAIN_NOTE, "a note\n", "a note")
    _write_privacy(vault, visibility_defaults=NOTES_PRIVATE)
    privacy_migrate.accept(vault)

    result = _link(vault, host)

    assert _label(host) == "private"
    assert {"glob": "notes/**", "visibility": "private"} in mirror_policy(host.bare)["policy"]["visibility_defaults"]
    assert any("scout-config.yaml" in n for n in result.notes)


def test_a_refresh_sync_labels_by_an_uncommitted_tightening(vault: Path, host: FakeHost) -> None:
    _commit(vault, PLAIN_NOTE, "a note\n", "a note")
    _link(vault, host)
    assert _label(host) == "normal"
    _write_privacy(vault, visibility_defaults=NOTES_PRIVATE)

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert _label(host) == "private"


def test_a_commit_by_commit_push_labels_by_an_uncommitted_tightening(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _write_privacy(vault, visibility_defaults=NOTES_PRIVATE)
    (vault / PLAIN_NOTE).parent.mkdir(parents=True, exist_ok=True)
    (vault / PLAIN_NOTE).write_text("a note\n", encoding="utf-8")
    git(vault, "add", PLAIN_NOTE)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "a note")

    assert len(rs.sync(vault, host).pushed) == 1

    assert _label(host) == "private"
    assert rs.sync(vault, host).pushed == []


def test_an_uncommitted_loosening_does_not_loosen_the_label(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _set_privacy(vault, visibility_defaults=NOTES_PRIVATE)
    _commit(vault, PLAIN_NOTE, "a note\n", "a note")
    rs.sync(vault, host)
    assert _label(host) == "private"
    _write_privacy(vault, visibility_defaults=None)

    result = rs.sync(vault, host)

    assert _label(host) == "private"
    assert any("scout-config.yaml" in n for n in result.notes)


def test_preview_counts_what_either_policy_marks_private(vault: Path) -> None:
    _commit(vault, PLAIN_NOTE, "a note\n", "a note")
    before = rs.preview(vault).private
    _write_privacy(vault, visibility_defaults=NOTES_PRIVATE)

    assert rs.preview(vault).private == before + 1


def test_a_committed_config_needs_no_note(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _set_privacy(vault, visibility_defaults=NOTES_PRIVATE)
    _commit(vault, PLAIN_NOTE, "a note\n", "a note")

    assert not [n for n in rs.sync(vault, host).notes if "scout-config.yaml" in n]


def test_a_file_an_earlier_sync_uploaded_under_the_old_hole_says_why_it_leaves(vault: Path, host: FakeHost) -> None:
    """v0.18.11's refresh uploaded it. The first sync with the fix takes it off the tip, which needs saying."""
    _commit(vault, HELD, "uploaded\n", "a file")
    _link(vault, host)
    assert HELD in _tip_paths(host)
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    before = list(config["privacy"].get("local_only_add") or [])
    _set_privacy(vault, local_only_add=[*before, HELD_GLOB])
    rs.sync(vault, host)
    state = rs.load_state(vault)
    assert state is not None
    # Back to the tip v0.18.11 would have left, with the commit's rule still in HEAD.
    _write_privacy(vault, local_only_add=before)
    _commit_on_tip_with_file(vault, host, state, HELD, "uploaded\n")

    result = rs.sync(vault, host)

    assert HELD not in _tip_paths(host)
    assert any("scout-config.yaml" in n and "comes off" in n for n in result.notes)


def _commit_on_tip_with_file(vault: Path, host: FakeHost, state: rs.LinkState, rel: str, text: str) -> None:
    """Put ``rel`` back on the mirror's tip as an older engine's push would have, and record it as pushed."""
    blob = rs.git(vault, "hash-object", "-w", "--stdin", input=text).stdout.strip()
    env = {"GIT_INDEX_FILE": str(vault / ".git" / "edge-index")}
    rs.git(vault, "read-tree", state.last_mirror, env=env)
    rs.git(vault, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
    tree = rs.git(vault, "write-tree", env=env).stdout.strip()
    meta = rs.commit_meta(vault, state.last_mirror)
    tip = rs._commit_tree(vault, tree, [state.last_mirror], "older push\n", meta.env())
    rs.push(vault, host, str(host.bare), tip, ("token", 1))
    state.last_mirror = tip
    state.pairs.append({"laptop": _head(vault), "mirror": tip, "kind": "push"})
    rs.save_state(vault, state)


def _committed_hold(vault: Path) -> list[str]:
    """Commit a non-plain Local-Only glob and a file it holds back. The list as it was before."""
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    before = list(config["privacy"].get("local_only_add") or [])
    _set_privacy(vault, local_only_add=[*before, HELD_GLOB])
    _commit(vault, HELD, "held back\n", "a held file")
    return before


def test_a_refresh_sync_keeps_what_the_commit_holds_back(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    before = _committed_hold(vault)
    rs.sync(vault, host)
    assert HELD not in mirror_history_paths(host.bare)
    _write_privacy(vault, local_only_add=before)  # uncommitted, so no hook sees it

    rs.sync(vault, host)

    assert HELD not in mirror_history_paths(host.bare)


def test_a_sync_with_a_replay_keeps_what_the_commit_holds_back(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    before = _committed_hold(vault)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _write_privacy(vault, local_only_add=before)

    result = rs.sync(vault, host)

    assert len(result.replayed) == 1
    assert HELD not in mirror_history_paths(host.bare)
    assert _tip(host, AGNES_FILE) == "written in agnes\n"


def test_the_retry_of_a_refused_push_keeps_what_the_first_attempt_held_back(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    before = _committed_hold(vault)
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)
    _write_privacy(vault, local_only_add=before)

    rs.sync(vault, host)

    assert HELD not in mirror_history_paths(host.bare)


def test_a_squashed_mirror_commit_still_records_what_the_commit_holds_back(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    before = _committed_hold(vault)
    rs.sync(vault, host)
    _write_privacy(vault, local_only_add=before)

    rs.sync(vault, host)

    assert HELD_GLOB in mirror_policy(host.bare)["policy"]["local_only"]["default"]


def test_link_keeps_what_the_commit_holds_back(vault: Path, host: FakeHost) -> None:
    before = _committed_hold(vault)
    _write_privacy(vault, local_only_add=before)
    privacy_migrate.accept(vault)

    _link(vault, host)

    assert HELD not in mirror_history_paths(host.bare)


def test_preview_says_what_a_sync_would_do(vault: Path) -> None:
    before = _committed_hold(vault)
    _write_privacy(vault, local_only_add=before)

    assert HELD not in rs.preview(vault).paths


def test_committing_the_removal_is_what_lets_the_file_up(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    before = _committed_hold(vault)
    rs.sync(vault, host)
    _set_privacy(vault, subject="drop the glob", local_only_add=before)

    rs.sync(vault, host)

    assert HELD in mirror_history_paths(host.bare)


def test_an_uncommitted_addition_is_still_held_back(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    _commit(vault, HELD, "held back\n", "a file not held yet")
    _write_privacy(vault, local_only_add=[*config["privacy"].get("local_only_add", []), HELD_GLOB])

    rs.sync(vault, host)

    assert HELD not in mirror_history_paths(host.bare)


def test_a_broken_committed_config_falls_back_on_the_fixed_working_tree(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    good = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    _commit(vault, "scout-config.yaml", "privacy: [unclosed\n", "broken config")
    (vault / "scout-config.yaml").write_text(good, encoding="utf-8")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert "trading/positions.md" not in mirror_history_paths(host.bare)
    assert mirror_policy(host.bare)["policy"] == load_policy(vault).to_json()


def test_a_refresh_of_a_commit_with_no_config_goes_up_under_the_live_one(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    git(vault, "rm", "-q", "--cached", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "stop tracking the config")
    rs.sync(vault, host)

    rs.sync(vault, host)

    assert mirror_policy(host.bare)["policy"] == load_policy(vault).to_json()


def test_a_reorder_of_local_only_add_is_no_difference(vault: Path, host: FakeHost) -> None:
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    before = list(config["privacy"].get("local_only_add") or [])
    _set_privacy(vault, local_only_add=[*before, "one/**", "two/**"])
    _link(vault, host)
    _write_privacy(vault, local_only_add=[*before, "two/**", "one/**"])

    result = rs.sync(vault, host)

    assert result.pushed == []
    assert not [n for n in result.notes if "scout-config.yaml" in n]


# ----- 2. uncommitted sharing: refused, because Agnes applies one policy ------------------------------

SHARED = "knowledge-base/projects/widget"
SHARED_NOTE = f"{SHARED}/plan.md"  # the fixture's widget.md sets its own scope, so it can't show a folder share
GADGET = "knowledge-base/projects/gadget"
PRIYA = "person:priya@example.com"
SAM_PERSON = "person:sam@example.com"
WIDGET_SHARE = {"glob": f"{SHARED}/**", "scope": [PRIYA]}
GADGET_SHARE = {"glob": f"{GADGET}/**", "scope": [PRIYA]}


def _kb(vault: Path, *args: str) -> Any:
    result = CliRunner().invoke(app, ["kb", *args, "--repo", str(vault)])
    assert result.exit_code == 0, result.output
    return result


def _commit_config(vault: Path, subject: str = "privacy: update shares") -> None:
    git(vault, "add", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def _synced_policy(host: FakeHost) -> dict[str, Any]:
    return mirror_policy(host.bare)["policy"]


def _assert_sharing_refusal(vault: Path, error: rs.RepoSyncError) -> None:
    assert error.code == "sharing_uncommitted"
    assert "Nothing was pushed" in error.message
    assert "commit scout-config.yaml" in error.message
    assert str(vault) in error.message


def _sync_refused(vault: Path, host: FakeHost) -> None:
    tip = _bare_git(host.bare, "rev-parse", "main")
    head = _head(vault)
    state = rs.state_path(vault).read_text(encoding="utf-8")
    leases = len(host.__dict__.get("lease_calls", []))

    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)

    _assert_sharing_refusal(vault, e.value)
    assert _bare_git(host.bare, "rev-parse", "main") == tip
    assert _head(vault) == head
    assert rs.state_path(vault).read_text(encoding="utf-8") == state
    assert len(host.__dict__.get("lease_calls", [])) == leases  # stopped before it took the lease or fetched


def test_an_uncommitted_share_is_refused_then_goes_up_once_committed(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    _commit(vault, SHARED_NOTE, "a plan\n", "plan")
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")

    _sync_refused(vault, host)
    assert _replay_commits(vault, agnes_sha) == 0

    _commit_config(vault)
    rs.sync(vault, host)

    assert _synced_policy(host)["federation_defaults"] == [WIDGET_SHARE]
    assert _synced_policy(host)["federation"]["enabled"] is True
    label = next(lb for lb in mirror_policy(host.bare)["labels"] if lb["path"] == SHARED_NOTE)
    assert label["federation_scope"] == [PRIYA]
    assert _tip(host, AGNES_FILE) == "written in agnes\n"


def _both_shared(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, SHARED_NOTE, "a plan\n", "plan")
    _commit(vault, f"{GADGET}/gadget.md", "a gadget\n", "gadget")
    _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")
    _kb(vault, "share", GADGET, "--with", "priya@example.com", "--yes")
    _commit_config(vault)
    rs.sync(vault, host)
    assert {e["glob"] for e in _synced_policy(host)["federation_defaults"]} == {f"{SHARED}/**", f"{GADGET}/**"}


def test_an_uncommitted_unshare_is_refused_and_every_share_stays_until_committed(vault: Path, host: FakeHost) -> None:
    _both_shared(vault, host)
    _kb(vault, "unshare", GADGET)

    _sync_refused(vault, host)
    assert GADGET_SHARE in _synced_policy(host)["federation_defaults"]
    assert WIDGET_SHARE in _synced_policy(host)["federation_defaults"]

    _commit_config(vault)
    rs.sync(vault, host)

    assert _synced_policy(host)["federation_defaults"] == [WIDGET_SHARE]


def test_an_uncommitted_carve_out_is_refused_and_keeps_the_folder_shared(vault: Path, host: FakeHost) -> None:
    _both_shared(vault, host)
    sub = f"{SHARED}/design"
    _commit(vault, f"{sub}/notes.md", "design notes\n", "design")
    rs.sync(vault, host)
    _kb(vault, "unshare", sub)

    _sync_refused(vault, host)
    assert WIDGET_SHARE in _synced_policy(host)["federation_defaults"]

    _commit_config(vault)
    rs.sync(vault, host)

    labels = {lb["path"]: lb for lb in mirror_policy(host.bare)["labels"]}
    assert labels[f"{sub}/notes.md"]["federation_scope"] == "none"
    assert labels[SHARED_NOTE]["federation_scope"] == [PRIYA]


def test_adding_a_person_uncommitted_is_refused(vault: Path, host: FakeHost) -> None:
    _both_shared(vault, host)
    _kb(vault, "share", SHARED, "--with", "sam@example.com", "--yes")

    _sync_refused(vault, host)
    assert WIDGET_SHARE in _synced_policy(host)["federation_defaults"]

    _commit_config(vault)
    rs.sync(vault, host)

    [widget] = [e for e in _synced_policy(host)["federation_defaults"] if e["glob"] == f"{SHARED}/**"]
    assert set(widget["scope"]) == {PRIYA, SAM_PERSON}


def test_a_reorder_of_shares_is_refused_because_the_first_match_decides(vault: Path, host: FakeHost) -> None:
    _both_shared(vault, host)
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    _write_privacy(vault, federation_defaults=list(reversed(config["privacy"]["federation_defaults"])))

    _sync_refused(vault, host)


def test_reverting_an_uncommitted_share_lets_the_sync_through(vault: Path, host: FakeHost) -> None:
    _both_shared(vault, host)
    config = vault / "scout-config.yaml"
    committed = config.read_text(encoding="utf-8")
    _kb(vault, "unshare", GADGET)
    _sync_refused(vault, host)

    config.write_text(committed, encoding="utf-8")

    assert rs.sync(vault, host).pushed == []
    assert GADGET_SHARE in _synced_policy(host)["federation_defaults"]


def test_turning_sharing_on_or_off_uncommitted_is_refused(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _write_privacy(vault, federation={"enabled": True})
    _sync_refused(vault, host)

    _commit_config(vault, "sharing on")
    rs.sync(vault, host)
    assert _synced_policy(host)["federation"]["enabled"] is True

    _write_privacy(vault, federation={"enabled": False})
    _sync_refused(vault, host)


def test_link_refuses_an_uncommitted_share_before_it_reaches_agnes(vault: Path, host: FakeHost) -> None:
    _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")
    privacy_migrate.accept(vault)

    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)

    _assert_sharing_refusal(vault, e.value)
    assert not host.exists
    assert rs.load_state(vault) is None

    _commit_config(vault)
    privacy_migrate.accept(vault)
    _link(vault, host)
    assert _synced_policy(host)["federation_defaults"] == [WIDGET_SHARE]


def test_preview_says_a_sync_would_be_refused(vault: Path) -> None:
    assert rs.preview(vault).uncommitted_sharing is False
    _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")

    preview = rs.preview(vault)

    assert preview.uncommitted_sharing is True
    assert any("sharing" in line and "commit" in line for line in preview.lines())
    assert preview.to_json(with_paths=False)["uncommitted_sharing"] is True

    _commit_config(vault)
    preview = rs.preview(vault)
    assert preview.uncommitted_sharing is False
    assert not any("uncommitted sharing" in line for line in preview.lines())


def test_a_share_committed_in_a_span_goes_up_correct_at_the_tip(vault: Path, host: FakeHost) -> None:
    """An older commit's mirror can't state both versions, but the push is atomic and only the tip is in force."""
    _link(vault, host)
    _commit(vault, f"{GADGET}/gadget.md", "a gadget\n", "gadget")
    _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")
    _commit_config(vault, "share widget")
    _kb(vault, "share", GADGET, "--with", "priya@example.com", "--yes")
    _commit_config(vault, "share gadget")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 3
    assert {e["glob"] for e in _synced_policy(host)["federation_defaults"]} == {f"{SHARED}/**", f"{GADGET}/**"}
    older = mirror_policy(host.bare, "main~1")["policy"]["federation_defaults"]
    assert older in ([], [WIDGET_SHARE])  # never more open than the commit it mirrors


def test_share_and_unshare_tell_the_owner_to_commit_first(vault: Path) -> None:
    shared = _kb(vault, "share", SHARED, "--with", "priya@example.com", "--yes")
    assert "commit scout-config.yaml" in shared.output and "scoutctl repo sync" in shared.output

    unshared = _kb(vault, "unshare", SHARED)
    assert "commit scout-config.yaml" in unshared.output and "scoutctl repo sync" in unshared.output


def test_the_stricter_policy_is_no_more_open_than_either() -> None:
    live = policy_from_overlay(
        {
            "local_only_add": ["live/**"],
            "visibility_defaults": [
                {"glob": "a/pub/**", "visibility": "normal"},
                {"glob": "a/**", "visibility": "private"},
            ],
            "shared_files_add": ["both/**", "live-shared/**"],
            "surfaces": {"owner_only": ["custom"]},
            "federation": {"enabled": True},
            "federation_ceilings": [{"glob": "c/**", "ceiling": "org"}],
            "federation_defaults": [{"glob": "d/**", "scope": "org"}],
            "sync_only": ["a/**"],
        }
    )
    committed = policy_from_overlay(
        {
            "local_only_add": ["committed/**"],
            "visibility_defaults": [{"glob": "b/**", "visibility": "private"}],
            "shared_files_add": ["both/**"],
            "surfaces": {"shared": ["custom"]},
            "federation": {"enabled": False},
            "federation_ceilings": [{"glob": "c/**", "ceiling": "none"}],
            "sync_only": ["a/x/**"],
        }
    )

    merged = rs._stricter_policy(live, committed)

    assert {"live/**", "committed/**"} <= set(merged.local_only)
    for rel in ("a/note.md", "a/pub/note.md", "b/note.md"):
        assert classify(rel, None, merged).visibility == "private"
    assert classify("elsewhere/note.md", None, merged).visibility == "normal"
    assert "both/**" in merged.shared_files and "live-shared/**" not in merged.shared_files
    assert "custom" in merged.shared_surfaces and "custom" not in merged.owner_only_surfaces
    assert merged.federation_enabled is False
    assert [r.ceiling for r in merged.federation_ceilings_overlay] == ["none", "org"]
    assert merged.sync_only == ("a/x/**",)  # the live list is enforced by the rules already; a path must be on both
    assert rs._stricter_policy(live, live) is live


def test_the_stricter_policy_keeps_what_is_the_same_as_it_was() -> None:
    live = policy_from_overlay({"visibility_defaults": [{"glob": "a/pub/**", "visibility": "normal"}]})
    committed = dataclasses.replace(live, warnings=("privacy-overlay-invalid",), sync_only=None)

    merged = rs._stricter_policy(dataclasses.replace(live, sync_only=("a/**",)), committed)

    assert merged.visibility_defaults == live.visibility_defaults  # order is meaning; nothing to reorder
    assert merged.federation_defaults == live.federation_defaults
    assert merged.warnings == ("privacy-overlay-invalid",)
    assert merged.sync_only == ("a/**",)


# ----- 3. a reset that drops a recorded replay ----------------------------------------------------


def _warnings_about(result: rs.Result, rel: str) -> list[str]:
    return [n for n in result.notes if rel in n]


def test_a_reset_after_a_refused_push_warns_about_the_file_it_takes_off_agnes(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert not (vault / AGNES_FILE).exists()
    assert AGNES_FILE not in _tip_paths(host)
    [warning] = _warnings_about(result, AGNES_FILE)
    assert "warning" in warning and "history" in warning
    assert f"git show {agnes_sha[:12]}:{AGNES_FILE}" in warning
    assert not _warnings_about(rs.sync(vault, host), AGNES_FILE)


def test_a_reset_after_a_finished_sync_warns_too(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert AGNES_FILE not in _tip_paths(host)
    assert any(f"git show {agnes_sha[:12]}:{AGNES_FILE}" in n for n in result.notes)


def test_a_reset_that_drops_an_agnes_edit_warns_too(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "laptop\n", "laptop")
    rs.sync(vault, host)
    agnes_sha = _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "edited in agnes\n"}, "agnes edit")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert _tip(host, LAPTOP_FILE) == "laptop\n"
    assert any(f"git show {agnes_sha[:12]}:{LAPTOP_FILE}" in n for n in result.notes)


def test_the_warning_survives_the_refused_push_that_comes_first(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _failed_after_a_replay(vault, host, tmp_path)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    assert _warnings_about(rs.sync(vault, host), AGNES_FILE)


def test_a_replay_rewritten_with_its_content_kept_gets_no_warning(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _failed_after_a_replay(vault, host, tmp_path)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "reworded")

    assert not _warnings_about(rs.sync(vault, host), AGNES_FILE)


def test_a_rewritten_replay_the_laptop_then_edited_gets_no_warning(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _failed_after_a_replay(vault, host, tmp_path)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "reworded")
    _commit(vault, AGNES_FILE, "edited here\n", "edit it")

    result = rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "edited here\n"
    assert not _warnings_about(result, AGNES_FILE)


def test_a_dropped_replay_whose_commit_git_has_since_pruned_still_warns(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    state = rs.load_state(vault)
    assert state is not None
    for pair in state.pairs:
        if pair["mirror"] == agnes_sha:
            pair["laptop"] = "1" * 40
    rs.save_state(vault, state)

    assert _warnings_about(rs.sync(vault, host), AGNES_FILE)


def test_a_long_list_of_dropped_files_is_cut_short(
    vault: Path, host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rs, "REPLAY_SKIPPED_SHOWN", 1)
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "one\n", SAM: "two\n"}, "two files")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert any("and 1 more" in n for n in result.notes)


def test_a_sync_that_changes_no_file_never_looks_for_dropped_replays(
    vault: Path, host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "written in agnes\n"}, "agnes note")
    rs.sync(vault, host)
    state = rs.load_state(vault)
    assert state is not None
    calls: list[str] = []
    real = rs.git

    def counting(v: Path, *a: str, **k: Any) -> Any:
        calls.append(a[0])
        return real(v, *a, **k)

    monkeypatch.setattr(rs, "git", counting)

    assert rs._dropped_from_head(vault, rs.LocalOnlyRules.build([], ()), state, state.last_mirror) == []
    assert calls == ["diff-tree"]


def test_a_reset_over_an_add_then_an_edit_warns(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "first\n"}, "agnes adds")
    newest = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "second\n"}, "agnes edits")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~2")

    result = rs.sync(vault, host)

    assert result.dropped == [{"path": AGNES_FILE, "mirror_commit": newest}]
    assert AGNES_FILE not in _tip_paths(host)


def test_a_reset_over_two_agnes_edits_warns(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "v0\n", "v0")
    rs.sync(vault, host)
    _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "v1\n"}, "agnes v1")
    newest = _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "v2\n"}, "agnes v2")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~2")

    result = rs.sync(vault, host)

    assert result.dropped == [{"path": LAPTOP_FILE, "mirror_commit": newest}]
    assert _tip(host, LAPTOP_FILE) == "v0\n"


def test_a_reset_over_the_newer_of_two_agnes_edits_warns(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, LAPTOP_FILE, "v0\n", "v0")
    rs.sync(vault, host)
    _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "v1\n"}, "agnes v1")
    newest = _commit_on_mirror(tmp_path, host, {LAPTOP_FILE: "v2\n"}, "agnes v2")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert result.dropped == [{"path": LAPTOP_FILE, "mirror_commit": newest}]
    assert _tip(host, LAPTOP_FILE) == "v1\n"


def test_an_older_rewrite_does_not_hide_a_later_dropped_replay(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    """The first replay was reworded long ago, so it stays gone from HEAD and comes up in every later look."""
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "first\n"}, "agnes adds")
    rs.sync(vault, host)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "reworded")
    rs.sync(vault, host)
    newest = _commit_on_mirror(tmp_path, host, {AGNES_FILE: "second\n"}, "agnes edits")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")

    result = rs.sync(vault, host)

    assert result.dropped == [{"path": AGNES_FILE, "mirror_commit": newest}]
    assert _tip(host, AGNES_FILE) == "first\n"


def test_a_later_agnes_removal_is_not_called_a_dropped_file(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: "first\n"}, "agnes adds")
    _commit_on_mirror(tmp_path, host, {AGNES_FILE: None}, "agnes removes")
    rs.sync(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    _commit(vault, LAPTOP_FILE, "edit\n", "edit")

    assert rs.sync(vault, host).dropped == []


# ----- 4. what _skips_since_last_push costs --------------------------------------------------------


def _unpushed_skips(vault: Path, host: FakeHost, tmp_path: Path, count: int) -> tuple[rs.LinkState, rs.LocalOnlyRules]:
    """``count`` Agnes commits outside the allowlist, seen by a sync whose push was refused."""
    _linked(vault, host)
    for i in range(count):
        _commit_on_mirror(tmp_path, host, {f"knowledge-base/topics/note-{i}.md": "outside\n"}, f"note {i}")
    _refuse_pushes(host)
    try:
        rs.sync(vault, host)
    except rs.RepoSyncError as e:
        assert e.code == "push_failed"
    _allow_pushes(host)
    state = rs.load_state(vault)
    assert state is not None
    return state, rs.LocalOnlyRules.build([load_policy(vault)], (), pinned=state.sync_only)


def _git_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = rs.git

    def counting(v: Path, *a: str, **k: Any) -> Any:
        calls.append(a[0])
        return real(v, *a, **k)

    monkeypatch.setattr(rs, "git", counting)
    return calls


def test_the_skip_walk_runs_one_git_log_whatever_it_walks(
    vault: Path, host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, rules = _unpushed_skips(vault, host, tmp_path, 4)
    calls = _git_calls(monkeypatch)

    skips = rs._skips_since_last_push(vault, rules, state, state.last_mirror)

    assert len(skips) == 4
    assert calls.count("log") == 1
    assert calls.count("diff-tree") == 0
    assert len(calls) <= 5


def test_the_skip_walk_is_not_run_when_nothing_outside_the_list_is_on_the_tip(
    vault: Path, host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unpushed_skips(vault, host, tmp_path, 2)
    rs.sync(vault, host)
    state = rs.load_state(vault)
    assert state is not None
    rules = rs.LocalOnlyRules.build([load_policy(vault)], (), pinned=state.sync_only)
    calls = _git_calls(monkeypatch)

    assert rs._skips_since_last_push(vault, rules, state, state.last_mirror) == []
    assert "log" not in calls


def test_the_skip_walk_is_bounded_and_still_warns_about_what_it_did_not_reach(
    vault: Path, host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, rules = _unpushed_skips(vault, host, tmp_path, 3)
    monkeypatch.setattr(rs, "SKIP_WALK_LIMIT", 1)

    skips = rs._skips_since_last_push(vault, rules, state, state.last_mirror)

    assert {s["path"] for s in skips} == {f"knowledge-base/topics/note-{i}.md" for i in range(3)}
    by_path = {s["path"]: s["mirror_commit"] for s in skips}
    assert by_path["knowledge-base/topics/note-2.md"] == state.last_mirror
    assert _bare_git(host.bare, "cat-file", "-t", by_path["knowledge-base/topics/note-0.md"]).strip() == "commit"


def test_the_skip_walk_handles_paths_git_would_otherwise_quote(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _linked(vault, host)
    odd = 'knowledge-base/topics/ünï "x"\tnote.md'
    sha = _commit_on_mirror(tmp_path, host, {odd: "outside\n"}, "odd name")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    result = rs.sync(vault, host)

    assert result.replay_skipped == [{"path": odd, "mirror_commit": sha}]


def test_the_skip_walk_passes_over_what_needs_no_warning(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    """An allowed edit and a removal in the span, and a file the laptop already has, all say nothing."""
    _linked(vault, host)
    wanted = "knowledge-base/topics/kept-by-hand.md"
    _commit(vault, wanted, "same\n", "already here")
    rs.sync(vault, host)
    _commit_on_mirror(tmp_path, host, {wanted: "same\n", "knowledge-base/projects/from-agnes.md": "allowed\n"}, "a")
    _commit_on_mirror(tmp_path, host, {"knowledge-base/projects/from-agnes.md": None}, "b")
    sha = _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "outside\n"}, "c")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    assert rs.sync(vault, host).replay_skipped == [{"path": AGNES_NOTE, "mirror_commit": sha}]


def test_the_skip_walk_has_nothing_to_do_without_a_push_to_start_from(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    state = rs.load_state(vault)
    assert state is not None
    state.pairs = []

    rules = rs.LocalOnlyRules.build([load_policy(vault)], (), pinned=state.sync_only)
    assert rs._skips_since_last_push(vault, rules, state, state.last_mirror) == []


def test_a_commit_with_no_changes_does_not_stop_the_skip_walk(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _linked(vault, host)
    sha = _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "outside\n"}, "note")
    work = tmp_path / "agnes-side"
    git(work, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "nothing")
    git(work, "push", "-q", "origin", "HEAD:main")
    _refuse_pushes(host)
    with pytest.raises(rs.RepoSyncError):
        rs.sync(vault, host)
    _allow_pushes(host)

    assert rs.sync(vault, host).replay_skipped == [{"path": AGNES_NOTE, "mirror_commit": sha}]


# ----- 5. a rewrite or a merge between attempts -----------------------------------------------------


@pytest.mark.parametrize("message", [None, "reworded, trailer gone"])
def test_rewording_the_replay_between_attempts_leaves_no_commit_without_the_file(
    vault: Path, host: FakeHost, tmp_path: Path, message: str | None
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    body = message or git(vault, "log", "-1", "--format=%B").rstrip("\n") + "\n\nA longer body.\n"
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", body)

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _tip(host, LAPTOP_FILE) == "laptop edit\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_rebasing_under_the_replay_between_attempts_leaves_no_commit_without_the_file(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    replay = _head(vault)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--amend", "-m", "laptop edit, reworded")
    git(vault, "-c", "commit.gpgsign=false", "cherry-pick", replay)

    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _stays_on_agnes_after(host, agnes_sha)


def test_a_merge_with_the_replay_as_second_parent_leaves_no_commit_without_the_file(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    agnes_sha = _failed_after_a_replay(vault, host, tmp_path)
    replay = _head(vault)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    _commit(vault, SAM, "later\n", "later")
    git(vault, "-c", "commit.gpgsign=false", "merge", "-q", "--no-ff", "--no-edit", replay)

    rs.sync(vault, host)

    assert _tip(host, AGNES_FILE) == "written in agnes\n"
    assert _tip(host, SAM) == "later\n"
    assert _stays_on_agnes_after(host, agnes_sha)

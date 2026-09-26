"""Table-driven tests for scout.sessions.derive (spec §4.2, §4.3, §4.6 choice, §4.7 rules)."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.derive import (
    choose_pr,
    derive_state,
    fmt_ago,
    git_toplevel,
    is_scout_run,
    resolve_project_key,
    strip_worktree,
)
from scout.sessions.model import AgentSession, LastTurn, PRInfo, TranscriptInfo, WorktreeInfo

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
STALE = timedelta(days=3)
RUNNING = timedelta(seconds=120)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(**over: object) -> PRInfo:
    base = dict(
        number=98,
        repo="example-org/example-repo",
        url=None,
        state="OPEN",
        is_draft=False,
        review_decision="",
        review_requested=False,
        checks="passing",
        merge_state="CLEAN",
        fetched_at=_iso(NOW),
        stale=False,
        updated_at=_iso(NOW - timedelta(days=5)),
    )
    base.update(over)
    return PRInfo(**base)  # type: ignore[arg-type]


def _session(*, last_active: datetime = NOW - timedelta(hours=2), **over: object) -> AgentSession:
    base = dict(
        id="local_x",
        cli_session_id="u",
        title="t",
        title_source="auto",
        project_key="/r",
        group_name=None,
        cwd="/r",
        origin_cwd="/r",
        worktree=None,
        created_at=_iso(NOW - timedelta(days=9)),
        last_activity_at=_iso(last_active),
        model="claude-opus-5",
        effort="high",
        turns=3,
        is_archived=False,
        is_open=False,
        is_scout_run=False,
        parent_session_id=None,
        spawned_task_id=None,
        scheduled_task_id=None,
        prs=[],
        pr=None,
        transcript=None,
    )
    base.update(over)
    return AgentSession(**base)  # type: ignore[arg-type]


def _question_transcript() -> TranscriptInfo:
    return TranscriptInfo(
        path="p",
        first_prompt="x",
        files_touched=[],
        tool_calls=1,
        last_turn=LastTurn(at=_iso(NOW), kind="question"),
        mtime_ns=1,
    )


# ----- project + scout-run -----------------------------------------------------


def test_strip_worktree() -> None:
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1/sub") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo") == "/Users/alex/code/repo"


def test_resolve_project_key_prefers_git_toplevel_then_stripping() -> None:
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: "/a/b") == "/a/b"
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: None) == "/a/b"
    assert resolve_project_key("/plain", toplevel=lambda p: None) == "/plain"


def test_resolve_project_key_of_an_empty_cwd_never_asks_git() -> None:
    def boom(path: str) -> str | None:
        raise AssertionError(f"toplevel called for {path!r}")

    assert resolve_project_key("", toplevel=boom) == ""


def test_git_toplevel_of_an_empty_path_is_none() -> None:
    # Path("") is ".", which used to resolve the caller's own repo.
    assert git_toplevel("") is None


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Alex",
            "-c",
            "user.email=alex@example.com",
            "-c",
            "init.defaultBranch=main",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git binary")
def test_git_toplevel_resolves_a_linked_worktree_to_its_main_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    repo = tmp_path / "example-repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    worktree = repo / ".claude" / "worktrees" / "w1"
    _git("worktree", "add", "-q", "-b", "claude/w1", str(worktree), cwd=repo)
    (repo / "sub").mkdir()
    assert git_toplevel(str(worktree)) == str(repo.resolve())  # not the worktree root
    assert git_toplevel(str(repo)) == str(repo.resolve())
    assert git_toplevel(str(repo / "sub")) == str(repo.resolve())


def test_is_scout_run_needs_vault_cwd_and_a_run_signature(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert is_scout_run(
        origin_cwd=str(vault), title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault
    )
    assert is_scout_run(origin_cwd=str(vault), title="Scout research", scheduled_task_id="scout-research", vault=vault)
    assert not is_scout_run(origin_cwd=str(vault), title="Tidy the release notes", scheduled_task_id=None, vault=vault)
    assert not is_scout_run(
        origin_cwd="/elsewhere", title="scout-dreaming-20260908-1830", scheduled_task_id=None, vault=vault
    )


# ----- PR choice -------------------------------------------------------------------


def test_choose_pr_prefers_most_recently_updated_open() -> None:
    old_open = _pr(number=1, updated_at=_iso(NOW - timedelta(days=9)))
    new_open = _pr(number=2, updated_at=_iso(NOW - timedelta(days=1)))
    merged = _pr(number=3, state="MERGED", updated_at=_iso(NOW))
    assert choose_pr([old_open, merged, new_open]) is new_open
    assert choose_pr([merged]) is merged
    assert choose_pr([]) is None


# ----- state rules, first match wins -----------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected_state", "expected_reason_fragment"),
    [
        pytest.param(
            _session(is_archived=True, is_open=True, last_active=NOW), "done", "archived", id="1-archived-wins"
        ),
        pytest.param(
            _session(is_open=True, last_active=NOW - timedelta(seconds=40)), "running", "active 40s ago", id="2-running"
        ),
        pytest.param(
            _session(is_open=True, last_active=NOW - timedelta(minutes=12)),
            "parked",
            "open, idle 12m",
            id="2b-open-idle-is-parked",
        ),
        pytest.param(_session(pr=_pr(state="MERGED")), "done", "PR #98 merged", id="3-merged"),
        pytest.param(_session(pr=_pr(state="CLOSED")), "done", "PR #98 closed", id="3-closed"),
        pytest.param(
            _session(pr=_pr(review_decision="CHANGES_REQUESTED")),
            "needs_you",
            "changes requested on PR #98",
            id="4-changes-requested",
        ),
        pytest.param(
            _session(pr=_pr(checks="failing", review_requested=True)), "needs_you", "CI failing", id="4-ci-failing"
        ),
        pytest.param(
            _session(pr=_pr(merge_state="DIRTY", review_requested=True)), "needs_you", "merge conflict", id="4-conflict"
        ),
        pytest.param(_session(transcript=_question_transcript()), "needs_you", "ended on a question", id="4-question"),
        pytest.param(_session(pr=_pr()), "needs_you", "PR #98 ready to merge", id="4-ready-no-review-requested"),
        pytest.param(
            _session(pr=_pr(review_decision="APPROVED", review_requested=True)),
            "needs_you",
            "PR #98 ready to merge",
            id="4-ready-approved",
        ),
        pytest.param(
            _session(pr=_pr(review_requested=True)), "waiting", "PR #98 awaiting review 5d", id="5-awaiting-review"
        ),
        pytest.param(
            _session(pr=_pr(review_decision="REVIEW_REQUIRED", review_requested=True)),
            "waiting",
            "awaiting review",
            id="5-review-required",
        ),
        pytest.param(_session(pr=_pr(checks="pending")), "waiting", "checks pending", id="5-checks-pending"),
        pytest.param(
            _session(pr=_pr(is_draft=True), last_active=NOW - timedelta(hours=1)),
            "parked",
            "draft PR #98",
            id="draft-falls-through",
        ),
        pytest.param(_session(last_active=NOW - timedelta(days=4)), "stale", "idle 4d", id="6-stale"),
        pytest.param(
            _session(
                last_active=NOW - timedelta(days=6),
                worktree=WorktreeInfo(path="/w", name="w", branch="b", source_branch="main", dirty=True),
            ),
            "stale",
            "dirty worktree, idle 6d",
            id="6-dirty-worktree",
        ),
        pytest.param(
            _session(pr=_pr(state="unknown", checks="unknown", merge_state="unknown", review_decision="unknown")),
            "parked",
            "PR #98 open (state unknown)",
            id="7-unknown-pr-parked",
        ),
        pytest.param(_session(), "parked", "last active 2h ago", id="7-parked-closed"),
    ],
)
def test_derive_state_table(session: AgentSession, expected_state: str, expected_reason_fragment: str) -> None:
    state, reasons = derive_state(session, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == expected_state, reasons
    assert any(expected_reason_fragment in r for r in reasons), reasons


def test_needs_you_lists_every_matched_signal() -> None:
    s = _session(pr=_pr(review_decision="CHANGES_REQUESTED", checks="failing"), transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["changes requested on PR #98", "CI failing", "ended on a question"]


def test_waiting_beats_stale_for_an_old_pr() -> None:
    s = _session(pr=_pr(review_requested=True), last_active=NOW - timedelta(days=10))
    assert derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)[0] == "waiting"


# §4.7: state_reasons lists every matched signal, not only the deciding rule's.


def test_running_also_lists_needs_you_signals() -> None:
    s = _session(is_open=True, last_active=NOW - timedelta(seconds=40), pr=_pr(review_decision="CHANGES_REQUESTED"))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "running"
    assert reasons == ["active 40s ago", "changes requested on PR #98"]


def test_needs_you_also_lists_awaiting_review() -> None:
    s = _session(pr=_pr(checks="failing", review_requested=True))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["CI failing", "PR #98 awaiting review 5d"]


def test_waiting_also_lists_idle_past_the_stale_threshold() -> None:
    s = _session(pr=_pr(review_requested=True), last_active=NOW - timedelta(days=10))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "waiting"
    assert reasons == ["PR #98 awaiting review 5d", "idle 10d"]


def test_needs_you_lists_waiting_and_stale_signals_too() -> None:
    s = _session(
        pr=_pr(merge_state="DIRTY", checks="pending", review_requested=True),
        last_active=NOW - timedelta(days=6),
        worktree=WorktreeInfo(path="/w", name="w", branch="b", source_branch="main", dirty=True),
    )
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["merge conflict", "PR #98 awaiting review 5d", "checks pending", "dirty worktree, idle 6d"]


def test_a_draft_pr_adds_no_pr_signals_but_a_question_still_counts() -> None:
    s = _session(
        pr=_pr(is_draft=True, checks="failing", review_requested=True),
        transcript=_question_transcript(),
        last_active=NOW - timedelta(days=5),
    )
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["ended on a question", "idle 5d"]


def test_fmt_ago() -> None:
    assert fmt_ago(timedelta(seconds=40)) == "40s ago"
    assert fmt_ago(timedelta(minutes=12)) == "12m ago"
    assert fmt_ago(timedelta(hours=3, minutes=5)) == "3h ago"
    assert fmt_ago(timedelta(days=4, hours=2)) == "4d ago"
    assert fmt_ago(None) == "unknown"

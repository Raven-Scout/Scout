"""Table-driven tests for scout.sessions.derive (spec §4.2, §4.3, §4.6 choice, §4.7 rules)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.derive import (
    choose_pr,
    derive_state,
    fmt_ago,
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


def test_is_scout_run_needs_vault_cwd_and_a_run_signature(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert is_scout_run(
        origin_cwd=str(vault), title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault
    )
    assert is_scout_run(origin_cwd=str(vault), title="Scout research", scheduled_task_id="scout-research", vault=vault)
    assert not is_scout_run(
        origin_cwd=str(vault), title="Lightning detection in videos", scheduled_task_id=None, vault=vault
    )
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


def test_fmt_ago() -> None:
    assert fmt_ago(timedelta(seconds=40)) == "40s ago"
    assert fmt_ago(timedelta(minutes=12)) == "12m ago"
    assert fmt_ago(timedelta(hours=3, minutes=5)) == "3h ago"
    assert fmt_ago(timedelta(days=4, hours=2)) == "4d ago"
    assert fmt_ago(None) == "unknown"

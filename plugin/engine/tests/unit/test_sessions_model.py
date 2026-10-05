"""Unit tests for scout.sessions.model."""

from __future__ import annotations

from datetime import UTC, datetime

from scout.sessions.model import (
    SCHEMA_VERSION,
    STATES,
    AgentSession,
    Index,
    LastTurn,
    PRInfo,
    Project,
    SourceError,
    TranscriptInfo,
    WorktreeInfo,
    ms_to_iso,
    ns_to_iso,
    parse_iso,
)


def _session(**over: object) -> AgentSession:
    base = dict(
        id="local_abc",
        cli_session_id="11111111-1111-1111-1111-111111111111",
        title="Fix the parser",
        title_source="auto",
        project_key="/Users/alex/code/example-repo",
        group_name="Example Repo",
        cwd="/Users/alex/code/example-repo/.claude/worktrees/w1",
        origin_cwd="/Users/alex/code/example-repo",
        worktree=WorktreeInfo(
            path="/Users/alex/code/example-repo/.claude/worktrees/w1",
            name="w1",
            branch="claude/w1",
            source_branch="main",
            dirty=False,
        ),
        created_at="2026-09-01T10:00:00Z",
        last_activity_at="2026-09-08T10:00:00Z",
        model="claude-opus-5",
        effort="high",
        turns=4,
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


def test_states_are_in_severity_order() -> None:
    assert STATES == ("needs_you", "running", "waiting", "parked", "stale", "done")


def test_time_helpers_round_trip_utc() -> None:
    assert ms_to_iso(1_788_895_143_313) == "2026-09-08T19:19:03Z"
    assert ms_to_iso(None) is None
    assert ns_to_iso(1_788_895_143_000_000_000) == "2026-09-08T19:19:03Z"
    assert parse_iso("2026-09-08T19:19:03Z") == datetime(2026, 9, 8, 19, 19, 3, tzinfo=UTC)
    assert parse_iso("garbage") is None and parse_iso(None) is None


def test_index_to_dict_puts_schema_version_first_and_nests_dataclasses() -> None:
    pr = PRInfo(
        number=7,
        repo="example-org/example-repo",
        url="https://github.com/example-org/example-repo/pull/7",
        state="OPEN",
        is_draft=False,
        review_decision="",
        review_requested=False,
        checks="passing",
        merge_state="CLEAN",
        fetched_at="2026-09-08T10:00:00Z",
        stale=False,
        updated_at=None,
    )
    tr = TranscriptInfo(
        path="~/.claude/projects/x/1.jsonl",
        first_prompt="hi",
        files_touched=["~/a.py"],
        tool_calls=3,
        last_turn=LastTurn(at="2026-09-08T09:59:00Z", kind="end_turn"),
        mtime_ns=1,
    )
    s = _session(pr=pr, prs=[pr], transcript=tr, state="waiting", state_reasons=["PR #7 awaiting review"])
    idx = Index(
        generated_at="2026-09-08T10:00:00Z",
        source_counts={"desktop": 1},
        source_errors=[SourceError(source="gh", message="not found")],
        display={"done_visible_hours": 24, "stale_after_days": 3},
        projects=[Project(key=s.project_key, name="Example Repo", group_id="cg-1", counts={"waiting": 1})],
        sessions=[s],
    )
    d = idx.to_dict()
    assert list(d)[0] == "schema_version" and d["schema_version"] == SCHEMA_VERSION
    assert d["sessions"][0]["pr"]["checks"] == "passing"
    assert d["sessions"][0]["transcript"]["last_turn"] == {"at": "2026-09-08T09:59:00Z", "kind": "end_turn"}
    assert d["source_errors"] == [{"source": "gh", "message": "not found"}]
    assert d["sessions"][0]["state_reasons"] == ["PR #7 awaiting review"]

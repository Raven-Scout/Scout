"""Unit tests for scout.sessions.desktop (read-only loaders over the desktop app store)."""

from __future__ import annotations

import json
from pathlib import Path

from scout.sessions.desktop import (
    default_support_dir,
    load_desktop_records,
    load_groups,
    load_worktree_leases,
)
from tests.unit.sessions_helpers import (
    support_dir,
    write_desktop_config,
    write_desktop_record,
    write_worktrees,
)


def test_default_support_dir_is_under_home() -> None:
    assert default_support_dir() == Path.home() / "Library" / "Application Support" / "Claude"


def test_missing_store_is_not_an_error() -> None:
    records, errors = load_desktop_records(support_dir())
    assert records == [] and errors == []


def test_loads_records_and_skips_tombstones() -> None:
    s = support_dir()
    write_desktop_record(
        s,
        "local_aaa",
        prs=[
            {
                "prNumber": 98,
                "repo": "example-org/example-repo",
                "url": "https://github.com/example-org/example-repo/pull/98",
            }
        ],
    )
    write_desktop_record(
        s,
        "local_bbb",
        prNumber=64,
        prRepository="example-org/other",
        prState="MERGED",
        prUrl="https://github.com/example-org/other/pull/64",
        spawnedFrom={"sessionId": "local_aaa", "taskId": "task_1"},
        worktreePath="/Users/alex/code/other/.claude/worktrees/w9",
        worktreeName="w9",
        branch="claude/w9",
        sourceBranch="main",
        keptDirtyWorktree=True,
        transcriptUnavailable=True,
        scheduledTaskId="scout-research",
    )
    tomb = s / "claude-code-sessions" / "org-0000" / "user-0000" / "deleted_ccc"
    tomb.write_text("1788800000000", encoding="utf-8")

    records, errors = load_desktop_records(s)
    assert errors == []
    by_id = {r.session_id: r for r in records}
    assert set(by_id) == {"local_aaa", "local_bbb"}

    a = by_id["local_aaa"]
    assert a.title == "Fix the parser" and a.model == "claude-opus-5" and a.completed_turns == 4
    assert a.prs[0].number == 98 and a.prs[0].repo == "example-org/example-repo" and a.prs[0].legacy_state is None
    assert a.worktree_path is None and a.kept_dirty_worktree is False

    b = by_id["local_bbb"]
    assert b.prs[0].number == 64 and b.prs[0].repo == "example-org/other" and b.prs[0].legacy_state == "MERGED"
    assert b.parent_session_id == "local_aaa" and b.spawned_task_id == "task_1"
    assert b.worktree_name == "w9" and b.branch == "claude/w9" and b.source_branch == "main"
    assert b.kept_dirty_worktree is True and b.transcript_unavailable is True
    assert b.scheduled_task_id == "scout-research"


def test_malformed_record_is_reported_not_fatal() -> None:
    s = support_dir()
    write_desktop_record(s, "local_ok")
    bad = s / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json"
    bad.write_text("{not json", encoding="utf-8")
    records, errors = load_desktop_records(s)
    assert [r.session_id for r in records] == ["local_ok"]
    assert len(errors) == 1 and errors[0].source == "desktop" and "local_bad.json" in errors[0].message


def test_load_groups_reads_names_and_strips_code_prefix() -> None:
    s = support_dir()
    write_desktop_config(s, {"cg-1": "Example Repo", "cg-2": "Archived"}, {"local_aaa": "cg-1", "local_bbb": "cg-2"})
    groups, errors = load_groups(s)
    assert errors == []
    assert groups.names == {"cg-1": "Example Repo", "cg-2": "Archived"}
    assert groups.assignments == {"local_aaa": "cg-1", "local_bbb": "cg-2"}


def test_load_groups_without_config_is_empty_and_quiet() -> None:
    groups, errors = load_groups(support_dir())
    assert groups.names == {} and groups.assignments == {} and errors == []


def test_load_worktree_leases_keyed_by_leasing_session() -> None:
    s = support_dir()
    write_worktrees(
        s,
        {
            "w9": {
                "name": "w9",
                "path": "/Users/alex/code/other/.claude/worktrees/w9",
                "baseRepo": "/Users/alex/code/other",
                "branch": "claude/w9",
                "sourceBranch": "main",
                "leasedBy": "local_bbb",
            },
            "orphan": {"name": "orphan", "path": "/tmp/x", "baseRepo": "/tmp"},
        },
    )
    leases, errors = load_worktree_leases(s)
    assert errors == []
    assert set(leases) == {"local_bbb"}
    assert leases["local_bbb"].path.endswith("/w9") and leases["local_bbb"].branch == "claude/w9"
    assert leases["local_bbb"].base_repo == "/Users/alex/code/other"


def test_structurally_malformed_record_is_reported_not_fatal() -> None:
    s = support_dir()
    write_desktop_record(s, "local_ok")
    write_desktop_record(s, "local_bad2", prs=5)  # valid JSON, wrong shape
    records, errors = load_desktop_records(s)
    assert [r.session_id for r in records] == ["local_ok"]
    assert len(errors) == 1 and errors[0].source == "desktop" and "local_bad2.json" in errors[0].message


def test_load_groups_reports_wrong_inner_shapes() -> None:
    s = support_dir()
    s.mkdir(parents=True, exist_ok=True)
    payload = {
        "preferences": {
            "epitaxyPrefs": {
                "dframe-group-scopes": {
                    "org-0000/user-0000": {
                        "groups": "nope",
                        "assignments": ["a", "b"],
                    }
                }
            }
        }
    }
    (s / "claude_desktop_config.json").write_text(json.dumps(payload), encoding="utf-8")
    groups, errors = load_groups(s)
    assert groups.names == {} and groups.assignments == {}
    assert sorted(e.message for e in errors) == [
        "org-0000/user-0000: assignments is not an object",
        "org-0000/user-0000: groups is not a list",
    ]
    assert all(e.source == "desktop-config" for e in errors)


def test_load_worktree_leases_reports_wrong_shape() -> None:
    s = support_dir()
    s.mkdir(parents=True, exist_ok=True)
    (s / "git-worktrees.json").write_text(json.dumps({"worktrees": ["not", "a", "dict"]}), encoding="utf-8")
    leases, errors = load_worktree_leases(s)
    assert leases == {}
    assert [e.source for e in errors] == ["desktop-worktrees"] and "not an object" in errors[0].message


def test_non_object_top_level_json_is_reported() -> None:
    s = support_dir()
    s.mkdir(parents=True, exist_ok=True)
    (s / "claude_desktop_config.json").write_text("[]", encoding="utf-8")
    groups, errors = load_groups(s)
    assert groups.names == {} and groups.assignments == {}
    assert len(errors) == 1 and errors[0].source == "desktop-config" and "not a JSON object" in errors[0].message

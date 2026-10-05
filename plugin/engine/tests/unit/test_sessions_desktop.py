"""Unit tests for scout.sessions.desktop (read-only loaders over the desktop app store)."""

from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from scout.sessions.desktop import (
    _RECORD_FIELDS,
    DESKTOP_CACHE_FILENAME,
    CachedRecord,
    DesktopRecord,
    PRRef,
    default_support_dir,
    load_desktop_cache,
    load_desktop_records,
    load_groups,
    load_worktree_leases,
    write_desktop_cache,
)
from scout.sessions.stats import BuildStats
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


def test_explicit_null_containers_are_absent_but_wrong_shapes_still_report() -> None:
    s = support_dir()
    s.mkdir(parents=True, exist_ok=True)
    payload = {
        "preferences": {
            "epitaxyPrefs": {
                "dframe-group-scopes": {
                    "org-0000/user-0000": {"groups": None, "assignments": {"code:local_aaa": "cg-1"}},
                    "org-0000/user-0001": {"groups": [{"id": "cg-1", "name": "Example Repo"}], "assignments": None},
                    "org-0000/user-0002": {"groups": 5, "assignments": "x"},
                }
            }
        }
    }
    (s / "claude_desktop_config.json").write_text(json.dumps(payload), encoding="utf-8")
    groups, errors = load_groups(s)
    assert groups.names == {"cg-1": "Example Repo"} and groups.assignments == {"local_aaa": "cg-1"}
    assert sorted(e.message for e in errors) == [
        "org-0000/user-0002: assignments is not an object",
        "org-0000/user-0002: groups is not a list",
    ]

    (s / "git-worktrees.json").write_text(json.dumps({"worktrees": None}), encoding="utf-8")
    assert load_worktree_leases(s) == ({}, [])
    (s / "git-worktrees.json").write_text(json.dumps({"worktrees": 7}), encoding="utf-8")
    leases, errors = load_worktree_leases(s)
    assert leases == {} and [e.message for e in errors] == ["worktrees is not an object"]


# ----- desktop record cache (1b spec §3.1) ------------------------------------------


def _bump(path: Path, seconds: int = 1) -> None:
    """Move a file's mtime forward, so a rewrite shows even on a coarse-mtime filesystem."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def _rec(**overrides: Any) -> DesktopRecord:
    base: dict[str, Any] = {
        "session_id": "local_aaa",
        "cli_session_id": None,
        "title": "Fix the parser",
        "title_source": "auto",
        "cwd": "/Users/alex/code/example-repo",
        "origin_cwd": "/Users/alex/code/example-repo",
        "worktree_path": None,
        "worktree_name": None,
        "branch": None,
        "source_branch": None,
        "created_at_ms": 1_788_400_000_000,
        "last_activity_at_ms": 1_788_800_000_000,
        "model": "claude-opus-5",
        "effort": "high",
        "is_archived": False,
        "completed_turns": 4,
        "prs": [PRRef(number=98, repo="example-org/example-repo", url=None, legacy_state=None)],
        "parent_session_id": None,
        "spawned_task_id": None,
        "scheduled_task_id": None,
        "kept_dirty_worktree": False,
        "transcript_unavailable": False,
    }
    base.update(overrides)
    return DesktopRecord(**base)


def test_unchanged_records_are_served_from_the_cache() -> None:
    s = support_dir()
    write_desktop_record(s, "local_aaa")
    write_desktop_record(s, "local_bbb", title="Tidy the release notes")
    cache: dict[str, CachedRecord] = {}
    cold = BuildStats()
    first, errors = load_desktop_records(s, cache=cache, stats=cold)
    assert errors == [] and cold.desktop_decoded == 2 and len(cache) == 2

    warm = BuildStats()
    second, errors = load_desktop_records(s, cache=cache, stats=warm)
    assert errors == [] and warm.desktop_decoded == 0
    assert second == first


def test_a_rewritten_record_is_decoded_again() -> None:
    s = support_dir()
    path = write_desktop_record(s, "local_aaa")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    write_desktop_record(s, "local_aaa", title="Renamed")
    _bump(path)
    stats = BuildStats()
    records, _ = load_desktop_records(s, cache=cache, stats=stats)
    assert stats.desktop_decoded == 1 and [r.title for r in records] == ["Renamed"]


def test_a_record_caught_mid_write_is_served_from_its_last_good_version() -> None:
    s = support_dir()
    path = write_desktop_record(s, "local_aaa")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)

    path.write_text('{"sessionId": "local_aaa", "title": "Half wr', encoding="utf-8")  # the app mid-rewrite
    _bump(path)
    first = BuildStats()
    records, errors = load_desktop_records(s, cache=cache, stats=first)
    assert [r.title for r in records] == ["Fix the parser"] and errors == []  # silent the first time
    assert (first.desktop_served_last_good, first.desktop_decoded) == (1, 0)

    records, errors = load_desktop_records(s, cache=cache)  # the same broken version on the next build
    assert [r.title for r in records] == ["Fix the parser"]
    assert len(errors) == 1 and errors[0].source == "desktop" and "local_aaa.json" in errors[0].message

    write_desktop_record(s, "local_aaa", title="Finished the rewrite")
    _bump(path, 2)
    records, errors = load_desktop_records(s, cache=cache)
    assert [r.title for r in records] == ["Finished the rewrite"] and errors == []
    assert cache[str(path)].failed is None


def test_a_broken_record_with_no_cached_version_is_reported() -> None:
    s = support_dir()
    bad = s / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    cache: dict[str, CachedRecord] = {}
    records, errors = load_desktop_records(s, cache=cache)
    assert records == [] and cache == {}
    assert len(errors) == 1 and "local_bad.json" in errors[0].message


def test_a_deleted_record_drops_out_of_the_cache() -> None:
    s = support_dir()
    gone = write_desktop_record(s, "local_aaa")
    kept = write_desktop_record(s, "local_bbb")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    gone.unlink()
    records, _ = load_desktop_records(s, cache=cache)
    assert [r.session_id for r in records] == ["local_bbb"] and set(cache) == {str(kept)}


def test_a_missing_store_empties_the_cache() -> None:
    cache = {"/gone/local_aaa.json": CachedRecord(size=1, mtime_ns=1, record=_rec())}
    assert load_desktop_records(support_dir(), cache=cache) == ([], []) and cache == {}


def test_desktop_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    cache = {
        "/s/local_aaa.json": CachedRecord(size=10, mtime_ns=20, record=_rec()),
        "/s/local_bbb.json": CachedRecord(
            size=1, mtime_ns=2, record=_rec(session_id="local_bbb", prs=[]), failed=(3, 4)
        ),
    }
    assert write_desktop_cache(path, cache) is True
    assert load_desktop_cache(path) == cache


@pytest.mark.parametrize(
    "breakage",
    [
        lambda e: e.pop("size"),
        lambda e: e.update(size=True),
        lambda e: e.update(mtime_ns="1"),
        lambda e: e.update(failed=[1]),
        lambda e: e.update(failed=[1, "2"]),
        lambda e: e.update(record=None),
        lambda e: e["record"].pop("title"),
        lambda e: e["record"].update(session_id=None),
        lambda e: e["record"].update(title=5),
        lambda e: e["record"].update(last_activity_at_ms=True),
        lambda e: e["record"].update(is_archived="no"),
        lambda e: e["record"].update(prs={}),
        lambda e: e["record"]["prs"][0].update(number="98"),
        lambda e: e["record"]["prs"][0].update(url=5),
        lambda e: e["record"]["prs"][0].pop("legacy_state"),
    ],
)
def test_desktop_cache_skips_a_wrongly_typed_entry(tmp_path: Path, breakage: Any) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    entry = CachedRecord(size=1, mtime_ns=2, record=_rec())
    write_desktop_cache(path, {"good": entry, "bad": entry})
    raw = json.loads(path.read_text(encoding="utf-8"))
    breakage(raw["entries"]["bad"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert set(load_desktop_cache(path)) == {"good"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[1,2",
        "[]",
        '{"entries": {}}',
        '{"version": 2, "entries": {}}',
        '{"version": true, "entries": {}}',
        '{"version": 1, "entries": []}',
    ],
)
def test_a_desktop_cache_of_another_version_or_shape_is_empty(tmp_path: Path, text: str) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    path.write_text(text, encoding="utf-8")
    assert load_desktop_cache(path) == {}


def test_a_missing_desktop_cache_is_empty(tmp_path: Path) -> None:
    assert load_desktop_cache(tmp_path / DESKTOP_CACHE_FILENAME) == {}


def test_the_desktop_cache_never_stores_unlisted_fields(tmp_path: Path) -> None:
    s = support_dir()
    secret = {"server": {"headers": {"authorization": "Bearer not-a-real-token"}}}
    write_desktop_record(s, "local_aaa", remoteMcpServersConfig=secret)
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    path = tmp_path / DESKTOP_CACHE_FILENAME
    write_desktop_cache(path, cache)
    text = path.read_text(encoding="utf-8")
    assert "not-a-real-token" not in text and "remoteMcpServersConfig" not in text


def test_the_cached_record_fields_cover_the_dataclass() -> None:
    # Adding a DesktopRecord field without teaching the cache validator about it fails here.
    assert {*_RECORD_FIELDS, "prs"} == {f.name for f in fields(DesktopRecord)}


def test_write_desktop_cache_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    assert write_desktop_cache(blocker / DESKTOP_CACHE_FILENAME, {}) is False
    assert not list(tmp_path.glob("*.tmp"))

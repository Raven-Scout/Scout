"""Unit tests for scout.sessions.github — never calls the real gh."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.desktop import PRRef
from scout.sessions.github import (
    PR_CACHE_FILENAME,
    load_pr_cache,
    pr_info_from_payload,
    refresh_pr_states,
    summarize_checks,
    terminal_pr_info,
    unknown_pr_info,
    write_pr_cache,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TTL = timedelta(minutes=10)
REF = PRRef(
    number=98,
    repo="example-org/example-repo",
    url="https://github.com/example-org/example-repo/pull/98",
    legacy_state=None,
)


def _payload(**over: object) -> str:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "reviewRequests": [],
        "statusCheckRollup": [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "mergeStateStatus": "CLEAN",
        "updatedAt": "2026-09-08T11:00:00Z",
        "url": REF.url,
    }
    base.update(over)
    return json.dumps(base)


def test_summarize_checks() -> None:
    assert summarize_checks([]) == "none"
    assert (
        summarize_checks(
            [{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "COMPLETED", "conclusion": "SKIPPED"}]
        )
        == "passing"
    )
    assert (
        summarize_checks(
            [{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": None}]
        )
        == "pending"
    )
    assert summarize_checks([{"status": "COMPLETED", "conclusion": "FAILURE"}, {"status": "IN_PROGRESS"}]) == "failing"
    # Older gh payloads use `state` instead of status/conclusion.
    assert summarize_checks([{"state": "SUCCESS"}, {"state": "ERROR"}]) == "failing"


def test_pr_info_from_payload_maps_fields() -> None:
    info = pr_info_from_payload(
        REF, json.loads(_payload(reviewRequests=[{"login": "priya"}])), fetched_at="2026-09-08T12:00:00Z"
    )
    assert info.key == "example-org/example-repo#98"
    assert (info.state, info.is_draft, info.review_decision) == ("OPEN", False, "CHANGES_REQUESTED")
    assert info.review_requested is True and info.checks == "passing" and info.merge_state == "CLEAN"
    assert info.updated_at == "2026-09-08T11:00:00Z" and info.stale is False


def test_missing_or_uncomputed_state_is_one_lowercase_unknown() -> None:
    at = "2026-09-08T12:00:00Z"
    bare = pr_info_from_payload(REF, {}, fetched_at=at)
    assert (bare.state, bare.merge_state) == ("unknown", "unknown")
    empty = pr_info_from_payload(REF, {"state": "", "mergeStateStatus": ""}, fetched_at=at)
    assert (empty.state, empty.merge_state) == ("unknown", "unknown")
    # GitHub reports mergeStateStatus "UNKNOWN" while it has not computed mergeability yet.
    uncomputed = pr_info_from_payload(REF, json.loads(_payload(state="UNKNOWN", mergeStateStatus="UNKNOWN")), at)
    assert (uncomputed.state, uncomputed.merge_state) == ("unknown", "unknown")
    lower = pr_info_from_payload(REF, json.loads(_payload(state="open", mergeStateStatus="dirty")), fetched_at=at)
    assert (lower.state, lower.merge_state) == ("OPEN", "DIRTY")
    assert (unknown_pr_info(REF).state, unknown_pr_info(REF).merge_state) == ("unknown", "unknown")


def test_refresh_fetches_uncached_and_caches_result() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv)
        return _payload()

    cache: dict = {}
    out, errors, fetched = refresh_pr_states([REF], cache=cache, now=NOW, ttl=TTL, cap=25, runner=runner)
    assert fetched == 1 and errors == []
    assert calls == [
        [
            "pr",
            "view",
            "98",
            "--repo",
            "example-org/example-repo",
            "--json",
            "state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url",
        ]
    ]
    assert out[REF.key].review_decision == "CHANGES_REQUESTED"
    assert cache[REF.key] == out[REF.key]


def test_refresh_respects_ttl_and_never_refetches_terminal() -> None:
    fresh = pr_info_from_payload(REF, json.loads(_payload()), fetched_at="2026-09-08T11:55:00Z")
    merged_ref = PRRef(number=7, repo="example-org/example-repo", url=None, legacy_state=None)
    merged = terminal_pr_info(merged_ref, "MERGED")
    old_ref = PRRef(number=8, repo="example-org/example-repo", url=None, legacy_state=None)
    old = pr_info_from_payload(old_ref, json.loads(_payload()), fetched_at="2026-09-08T09:00:00Z")
    cache = {fresh.key: fresh, merged.key: merged, old.key: old}
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv)
        return _payload(state="MERGED")

    out, errors, fetched = refresh_pr_states(
        [REF, merged_ref, old_ref], cache=cache, now=NOW, ttl=TTL, cap=25, runner=runner
    )
    assert fetched == 1 and [c[2] for c in calls] == ["8"]  # only the stale open one
    assert out[REF.key] is fresh and out[merged.key] is merged
    assert out[old.key].state == "MERGED"


def test_refresh_failure_keeps_cached_value_marked_stale_or_unknown() -> None:
    cached = pr_info_from_payload(REF, json.loads(_payload()), fetched_at="2026-09-08T09:00:00Z")
    other = PRRef(number=9, repo="example-org/example-repo", url=None, legacy_state=None)
    cache = {cached.key: cached}
    out, errors, fetched = refresh_pr_states(
        [REF, other], cache=cache, now=NOW, ttl=TTL, cap=25, runner=lambda argv: None
    )
    assert fetched == 0
    assert out[REF.key].stale is True and out[REF.key].review_decision == "CHANGES_REQUESTED"
    assert out[other.key].state == "unknown" and out[other.key].checks == "unknown"
    assert len(errors) == 2 and all(e.source == "gh" for e in errors)


def test_refresh_honours_cap_oldest_first() -> None:
    refs = [PRRef(number=n, repo="example-org/example-repo", url=None, legacy_state=None) for n in (1, 2, 3)]
    cache = {
        refs[0].key: pr_info_from_payload(refs[0], json.loads(_payload()), fetched_at="2026-09-08T10:00:00Z"),
        refs[1].key: pr_info_from_payload(refs[1], json.loads(_payload()), fetched_at="2026-09-08T08:00:00Z"),
    }  # refs[2] never fetched → oldest of all
    calls: list[str] = []
    out, _, fetched = refresh_pr_states(
        refs, cache=cache, now=NOW, ttl=TTL, cap=2, runner=lambda a: (calls.append(a[2]), _payload())[1]
    )
    assert fetched == 2 and calls == ["3", "2"]
    assert out[refs[0].key].stale is False  # not refetched, still within-cache value, not marked stale by cap


def test_legacy_terminal_state_seeds_without_gh() -> None:
    legacy = PRRef(number=64, repo="example-org/other", url=None, legacy_state="MERGED")
    out, errors, fetched = refresh_pr_states([legacy], cache={}, now=NOW, ttl=TTL, cap=25, runner=lambda a: None)
    assert fetched == 0 and errors == [] and out[legacy.key].state == "MERGED"


def test_unknown_and_cache_round_trip(tmp_path: Path) -> None:
    info = unknown_pr_info(REF)
    assert info.state == "unknown" and info.fetched_at is None
    path = tmp_path / PR_CACHE_FILENAME
    write_pr_cache(path, {info.key: info})
    assert load_pr_cache(path)[info.key] == info
    assert load_pr_cache(tmp_path / "missing.json") == {}


def test_load_pr_cache_skips_entries_whose_fields_have_the_wrong_type(tmp_path: Path) -> None:
    good = asdict(pr_info_from_payload(REF, json.loads(_payload()), fetched_at="2026-09-08T11:00:00Z"))
    bad = {
        "state_list": {**good, "state": []},  # unhashable: used to raise TypeError in refresh_pr_states
        "number_bool": {**good, "number": True},
        "number_str": {**good, "number": "98"},
        "repo_none": {**good, "repo": None},
        "url_int": {**good, "url": 5},
        "is_draft_str": {**good, "is_draft": "no"},
        "review_requested_int": {**good, "review_requested": 1},
        "checks_none": {**good, "checks": None},
        "fetched_at_int": {**good, "fetched_at": 5},
        "stale_none": {**good, "stale": None},
        "updated_at_list": {**good, "updated_at": []},
        "missing_state": {k: v for k, v in good.items() if k != "state"},
    }
    path = tmp_path / PR_CACHE_FILENAME
    path.write_text(json.dumps({"good": good, **bad}), encoding="utf-8")
    loaded = load_pr_cache(path)
    assert set(loaded) == {"good"}
    assert asdict(loaded["good"]) == good


def test_interleaved_pr_cache_writes_never_share_a_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / PR_CACHE_FILENAME
    first, second = unknown_pr_info(REF), terminal_pr_info(REF, "MERGED")
    sources: list[str] = []
    real_replace = os.replace

    def replace_after_a_concurrent_writer(src: str, dst: str) -> None:
        sources.append(os.fspath(src))
        if len(sources) == 1:  # a second build writes the same cache while the first is mid-write
            write_pr_cache(path, {"b": second})
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_after_a_concurrent_writer)
    write_pr_cache(path, {"a": first})
    assert len(sources) == 2 and sources[0] != sources[1]
    assert load_pr_cache(path) == {"a": first}  # the outer write landed whole, last
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_write_pr_cache_never_raises_when_parent_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    write_pr_cache(blocker / PR_CACHE_FILENAME, {})  # must not raise
    assert blocker.read_text(encoding="utf-8") == "not a dir"


def test_three_consecutive_failures_stop_calling_gh() -> None:
    refs = [PRRef(number=n, repo="example-org/example-repo", url=None, legacy_state=None) for n in range(1, 7)]
    calls: list[str] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv[2])
        return None

    out, errors, fetched = refresh_pr_states(refs, cache={}, now=NOW, ttl=TTL, cap=25, runner=runner)
    assert calls == ["1", "2", "3"]  # stopped after MAX_CONSECUTIVE_FAILURES
    assert fetched == 0 and len(errors) == 3
    assert all(out[f"{r.repo}#{r.number}"].state == "unknown" for r in refs)

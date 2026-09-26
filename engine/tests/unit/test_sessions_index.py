"""Integration-style unit tests for scout.sessions.index against a fake HOME tree."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scout.sessions.github as gh
import scout.sessions.index as index_mod
from scout.sessions import cli_home
from scout.sessions.index import INDEX_FILENAME, BuildOptions, build_index, index_path, run, write_index
from scout.sessions.model import Index, dt_to_iso
from scout.sessions.settings import AgentSessionsSettings
from tests.unit.sessions_helpers import (
    claude_home,
    support_dir,
    write_desktop_config,
    write_desktop_record,
    write_pid_file,
    write_transcript,
)

# Transcript mtimes come from the real clock (write_transcript), so the fixed
# "now" must be the real clock too — otherwise a test run weeks later would see
# every transcript as newer than the desktop records.
NOW = datetime.now(tz=UTC).replace(microsecond=0)
MS = int(NOW.timestamp() * 1000)
UA = "aaaaaaaa-0000-0000-0000-000000000000"
UB = "bbbbbbbb-0000-0000-0000-000000000000"
UC = "cccccccc-0000-0000-0000-000000000000"
UE = "eeeeeeee-0000-0000-0000-000000000000"
UF = "ffffffff-0000-0000-0000-000000000000"
UG = "99999999-0000-0000-0000-000000000000"
UH = "88888888-0000-0000-0000-000000000000"
REPO_DIR = "-Users-alex-code-example-repo"


def _cc_encode(path: str) -> str:
    """Claude Code names a project dir by replacing every non-alphanumeric character of the cwd with "-"."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _user(text: str, ts: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _assistant_text(text: str, ts: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _gh(argv: list[str]) -> str | None:
    number = argv[2]
    if number == "98":
        return json.dumps(
            {
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": "CHANGES_REQUESTED",
                "reviewRequests": [],
                "statusCheckRollup": [],
                "mergeStateStatus": "CLEAN",
                "updatedAt": "2026-09-08T11:00:00Z",
            }
        )
    return None


def _world(fake_data_dir: Path, *, gh_ok: bool = True, use_gh: bool = True) -> BuildOptions:
    s, h = support_dir(), claude_home()
    # A: open PR with changes requested, live process, transcript ended on a question.
    write_desktop_record(
        s,
        "local_A",
        cliSessionId=UA,
        lastActivityAt=MS - 3_600_000,
        prs=[
            {
                "prNumber": 98,
                "repo": "example-org/example-repo",
                "url": "https://github.com/example-org/example-repo/pull/98",
            }
        ],
        cwd="/Users/alex/code/example-repo/.claude/worktrees/w1",
        worktreePath="/Users/alex/code/example-repo/.claude/worktrees/w1",
        worktreeName="w1",
        branch="claude/w1",
        sourceBranch="main",
    )
    write_transcript(
        h,
        REPO_DIR + "--claude-worktrees-w1",
        UA,
        [_user("fix it", "2026-09-08T10:00:00.000Z"), _assistant_text("Which file?", "2026-09-08T10:00:05.000Z")],
        mtime_ago_hours=2,
    )
    write_pid_file(h, 4242, UA, "/Users/alex/code/example-repo/.claude/worktrees/w1")
    # B: spawned child of A, in the Archived group.
    write_desktop_record(
        s,
        "local_B",
        cliSessionId=UB,
        spawnedFrom={"sessionId": "local_A", "taskId": "task_9"},
        lastActivityAt=MS - 7_200_000,
    )
    # C: transcript unavailable, legacy merged PR.
    write_desktop_record(
        s,
        "local_C",
        cliSessionId=UC,
        transcriptUnavailable=True,
        prNumber=64,
        prRepository="example-org/example-repo",
        prState="MERGED",
        lastActivityAt=MS - 86_400_000,
    )
    # D: fork sharing A's cli id, older — must be deduped away.
    write_desktop_record(s, "local_D", cliSessionId=UA, title="older fork", lastActivityAt=MS - 90_000_000)
    # Groups: A → "Example Repo", B → "Archived".
    write_desktop_config(s, {"cg-1": "Example Repo", "cg-arch": "Archived"}, {"local_A": "cg-1", "local_B": "cg-arch"})
    # E: CLI-only session, recent.
    write_transcript(
        h, "-Users-alex-code-other", UE, [_user("cli only work", "2026-09-08T09:00:00.000Z")], mtime_ago_hours=3
    )
    # F: Scout's own scheduled run inside the vault (custom-title first line).
    vault_dir = _cc_encode(str(fake_data_dir))
    write_transcript(
        h,
        vault_dir,
        UF,
        [
            {"type": "custom-title", "customTitle": "scout-morning-briefing-20260908-1150", "sessionId": UF},
            _user("You are Scout…", "2026-09-08T11:50:00.000Z"),
        ],
        mtime_ago_hours=0.2,
    )
    # G: CLI-only but 20 days old → outside transcript window, excluded.
    write_transcript(
        h, "-Users-alex-code-old", UG, [_user("ancient", "2026-08-19T09:00:00.000Z")], mtime_ago_hours=20 * 24
    )
    return BuildOptions(
        data_dir=fake_data_dir,
        settings=AgentSessionsSettings(),
        claude_home=h,
        support_dir=s,
        now=NOW,
        use_gh=use_gh,
        gh_runner=_gh,
        gh_available=lambda: gh_ok,
        toplevel=lambda p: None,
        pid_alive=lambda pid: pid == 4242,
    )


def test_build_index_merges_sources_and_derives_state(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir))
    by_id = {s.id: s for s in idx.sessions}
    assert set(by_id) == {"local_A", "local_B", "local_C", f"cli:{UE}", f"cli:{UF}"}  # D deduped, G too old

    a = by_id["local_A"]
    assert a.is_open is True and a.state == "needs_you"
    assert a.state_reasons == ["changes requested on PR #98", "ended on a question"]
    assert a.project_key == "/Users/alex/code/example-repo" and a.group_name == "Example Repo"
    assert a.worktree is not None and a.worktree.name == "w1" and a.transcript is not None
    assert a.last_activity_at == dt_to_iso(
        NOW - timedelta(hours=1)
    )  # desktop lastActivityAt beats the older transcript mtime

    b = by_id["local_B"]
    assert b.parent_session_id == "local_A" and b.is_archived is True and b.state == "done"

    c = by_id["local_C"]
    assert c.transcript is None and c.pr is not None and c.pr.state == "MERGED" and c.state == "done"

    e = by_id[f"cli:{UE}"]
    assert e.title is None and e.origin_cwd == "/Users/alex/code/other" and e.transcript is not None
    assert e.transcript.first_prompt == "cli only work" and e.state == "parked"

    f = by_id[f"cli:{UF}"]
    assert f.origin_cwd == str(fake_data_dir)  # matched by encoded name against the vault, not lossily decoded
    assert f.is_scout_run is True and f.title == "scout-morning-briefing-20260908-1150"

    assert idx.source_errors == []
    assert idx.source_counts["desktop"] == 4 and idx.source_counts["cli_only"] == 2
    assert (
        idx.source_counts["open"] == 1 and idx.source_counts["running"] == 0 and idx.source_counts["prs_refreshed"] == 1
    )
    assert idx.display == {"done_visible_hours": 24, "stale_after_days": 3}

    projects = {p.key: p for p in idx.projects}
    repo = projects["/Users/alex/code/example-repo"]
    assert repo.name == "Example Repo" and repo.group_id == "cg-1"
    assert repo.counts["needs_you"] == 1 and repo.counts["done"] == 2
    assert projects["/Users/alex/code/other"].name == "other" and projects["/Users/alex/code/other"].group_id is None

    # Sorted by severity then recency: needs_you first, done last.
    assert idx.sessions[0].id == "local_A" and idx.sessions[-1].state == "done"


def test_vault_dir_with_a_space_and_underscore_still_matches_its_transcripts(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    vault = fake_data_dir.parent / "My_Scout vault"
    (vault / ".scout-cache").mkdir(parents=True)
    write_transcript(
        claude_home(),
        _cc_encode(str(vault)),
        UH,
        [
            {"type": "custom-title", "customTitle": "scout-dreaming-20260908-1830", "sessionId": UH},
            _user("You are Scout…", "2026-09-08T18:30:00.000Z"),
        ],
        mtime_ago_hours=0.5,
    )
    opts.data_dir = vault
    idx = build_index(opts)
    h = next(s for s in idx.sessions if s.id == f"cli:{UH}")
    assert h.origin_cwd == str(vault)  # exact match against the vault, not the lossy decode
    assert h.is_scout_run is True


def test_gh_missing_marks_prs_unknown_with_one_error(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir, gh_ok=False))
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.pr is not None and a.pr.state == "unknown"
    assert a.state == "needs_you" and a.state_reasons == ["ended on a question"]  # question still detected
    assert [e.source for e in idx.source_errors] == ["gh"] and "not found" in idx.source_errors[0].message
    assert idx.source_counts["prs_refreshed"] == 0


def test_index_json_contract_key_sets(fake_data_dir: Path) -> None:
    """The JSON shape scout-app decodes (spec §4.8). Adding or removing a field must fail here."""
    d = json.loads(json.dumps(build_index(_world(fake_data_dir, gh_ok=False)).to_dict()))
    assert set(d) == {
        "schema_version",
        "generated_at",
        "source_counts",
        "source_errors",
        "display",
        "projects",
        "sessions",
    }
    a = next(s for s in d["sessions"] if s["id"] == "local_A")
    assert set(a) == {
        "id",
        "cli_session_id",
        "title",
        "title_source",
        "project_key",
        "group_name",
        "cwd",
        "origin_cwd",
        "worktree",
        "created_at",
        "last_activity_at",
        "model",
        "effort",
        "turns",
        "is_archived",
        "is_open",
        "is_scout_run",
        "parent_session_id",
        "spawned_task_id",
        "scheduled_task_id",
        "prs",
        "pr",
        "transcript",
        "state",
        "state_reasons",
    }
    assert set(a["worktree"]) == {"path", "name", "branch", "source_branch", "dirty"}
    pr_keys = {
        "number",
        "repo",
        "url",
        "state",
        "is_draft",
        "review_decision",
        "review_requested",
        "checks",
        "merge_state",
        "fetched_at",
        "stale",
        "updated_at",
    }
    assert set(a["pr"]) == pr_keys and all(set(p) == pr_keys for p in a["prs"])
    assert set(a["transcript"]) == {"path", "first_prompt", "files_touched", "tool_calls", "last_turn", "mtime_ns"}
    assert set(a["transcript"]["last_turn"]) == {"at", "kind"}
    project = d["projects"][0]
    assert set(project) == {"key", "name", "group_id", "counts"}
    assert set(project["counts"]) == {"needs_you", "running", "waiting", "parked", "stale", "done"}
    assert d["source_errors"] and all(set(e) == {"source", "message"} for e in d["source_errors"])
    assert set(d["display"]) == {"done_visible_hours", "stale_after_days"}
    assert set(d["source_counts"]) == {"desktop", "cli_only", "open", "running", "prs_refreshed"}


def test_use_gh_false_is_silent(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir, use_gh=False))
    assert idx.source_errors == []


def test_malformed_desktop_record_is_a_source_error_not_a_crash(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    (support_dir() / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json").write_text(
        "{", encoding="utf-8"
    )
    idx = build_index(opts)
    assert any(e.source == "desktop" and "local_bad.json" in e.message for e in idx.source_errors)
    assert len(idx.sessions) == 5


# ----- spec §4.12: no single malformed input aborts the build ---------------------------


def _ids(idx: Index) -> set[str]:
    return {s.id for s in idx.sessions}


ALL_IDS = {"local_A", "local_B", "local_C", f"cli:{UE}", f"cli:{UF}"}


def test_bad_pid_files_never_abort_the_build(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    h = claude_home()
    write_pid_file(h, 0, UE, "/Users/alex/code/other")  # process-group pid: malformed, not "alive"
    write_pid_file(h, 2**40, UB, "/Users/alex/code/example-repo")  # os.kill raises OverflowError
    opts.pid_alive = lambda pid: pid == 4242 or cli_home.pid_alive(pid)
    idx = build_index(opts)
    assert _ids(idx) == ALL_IDS
    assert [(e.source, e.message) for e in idx.source_errors] == [
        ("claude-home", "0.json: pid or sessionId missing or malformed")
    ]
    by_id = {s.id: s for s in idx.sessions}
    assert by_id["local_A"].is_open is True
    assert by_id[f"cli:{UE}"].is_open is False and by_id["local_B"].is_open is False


def test_non_string_text_part_in_a_transcript_never_aborts_the_build(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    write_transcript(
        claude_home(),
        "-Users-alex-code-other",
        UE,
        [
            {"type": "user", "message": {"content": [{"type": "text", "text": {"nested": "x"}}]}},
            _user("cli only work", "2026-09-08T09:00:00.000Z"),
        ],
        mtime_ago_hours=3,
    )
    idx = build_index(opts)
    assert _ids(idx) == ALL_IDS and idx.source_errors == []
    e = next(s for s in idx.sessions if s.id == f"cli:{UE}")
    assert e.transcript is not None and e.transcript.first_prompt == "cli only work"


def test_wrongly_typed_transcript_cache_entry_is_ignored(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    a_path = claude_home() / "projects" / (REPO_DIR + "--claude-worktrees-w1") / f"{UA}.jsonl"
    entry = {
        "path": str(a_path),
        "first_prompt": "stale",
        "files_touched": [],
        "tool_calls": 0,
        "last_turn": "x",  # used to raise AttributeError out of load_transcript_cache
        "mtime_ns": a_path.stat().st_mtime_ns,
    }
    (fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json").write_text(
        json.dumps({str(a_path): entry}), encoding="utf-8"
    )
    idx = build_index(opts)
    assert _ids(idx) == ALL_IDS and idx.source_errors == []
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.transcript is not None and a.transcript.first_prompt == "fix it"  # re-parsed, not the bad entry


def test_wrongly_typed_pr_cache_entry_is_ignored(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    key = "example-org/example-repo#98"
    entry = {
        "number": 98,
        "repo": "example-org/example-repo",
        "url": None,
        "state": [],  # unhashable: used to raise TypeError in refresh_pr_states
        "is_draft": False,
        "review_decision": "",
        "review_requested": False,
        "checks": "none",
        "merge_state": "CLEAN",
        "fetched_at": dt_to_iso(NOW),
        "stale": False,
        "updated_at": None,
    }
    (fake_data_dir / ".scout-cache" / "sessions-pr.cache.json").write_text(json.dumps({key: entry}), encoding="utf-8")
    idx = build_index(opts)
    assert _ids(idx) == ALL_IDS and idx.source_errors == []
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.pr is not None and a.pr.state == "OPEN" and a.pr.review_decision == "CHANGES_REQUESTED"  # refetched


def test_a_transcript_that_fails_to_parse_is_a_source_error(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = index_mod.transcript_info

    def flaky(path: Path, **kw: object) -> object:
        if path.stem == UE:
            raise ValueError("unexpected row shape")
        return real(path, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(index_mod, "transcript_info", flaky)
    idx = build_index(_world(fake_data_dir))
    assert _ids(idx) == ALL_IDS
    assert [(e.source, e.message) for e in idx.source_errors] == [("transcript", f"{UE}.jsonl: unexpected row shape")]
    by_id = {s.id: s for s in idx.sessions}
    assert by_id[f"cli:{UE}"].transcript is None and by_id["local_A"].transcript is not None


def test_a_cli_only_transcript_that_fails_is_a_source_error(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = index_mod._custom_title

    def flaky(path: Path) -> str | None:
        if path.stem == UE:
            raise RuntimeError("head unreadable")
        return real(path)

    monkeypatch.setattr(index_mod, "_custom_title", flaky)
    idx = build_index(_world(fake_data_dir))
    assert _ids(idx) == ALL_IDS - {f"cli:{UE}"}
    assert [(e.source, e.message) for e in idx.source_errors] == [("transcript", f"{UE}.jsonl: head unreadable")]
    assert idx.source_counts["cli_only"] == 1


def test_run_writes_index_and_caches_atomically(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    idx, path = run(opts=opts)
    assert path == index_path(fake_data_dir) == fake_data_dir / ".scout-cache" / INDEX_FILENAME
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert list(on_disk)[0] == "schema_version" and on_disk["schema_version"] == 1
    assert len(on_disk["sessions"]) == len(idx.sessions)
    assert not list((fake_data_dir / ".scout-cache").glob("*.tmp"))
    assert (fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json").exists()
    assert (fake_data_dir / ".scout-cache" / "sessions-pr.cache.json").exists()


def test_run_removes_legacy_transcript_cache(fake_data_dir: Path) -> None:
    legacy = fake_data_dir / ".scout-cache" / "cc-sessions.cache.json"
    legacy.write_text("{}", encoding="utf-8")
    run(opts=_world(fake_data_dir))
    assert not legacy.exists()


def test_second_run_reuses_pr_cache_within_ttl(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    run(opts=opts)
    calls: list[list[str]] = []

    def counting(argv: list[str]) -> str | None:
        calls.append(argv)
        return _gh(argv)

    opts.gh_runner = counting
    opts.now = NOW + timedelta(minutes=5)
    idx, _ = run(opts=opts)
    assert calls == [] and idx.source_counts["prs_refreshed"] == 0


def test_write_index_raises_when_directory_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    idx = Index(generated_at="x", source_counts={}, source_errors=[], display={}, projects=[], sessions=[])
    with pytest.raises(OSError):
        write_index(idx, blocker / INDEX_FILENAME)


def test_run_with_render_writes_digest(fake_data_dir: Path) -> None:
    run(opts=_world(fake_data_dir), render=True, tz_name="UTC")
    digest = (fake_data_dir / ".scout-cache" / "cc-sessions.md").read_text(encoding="utf-8")
    assert digest.startswith("# Claude Code Sessions — state digest")
    assert "## Needs you (1)" in digest
    # Session A: record title "Fix the parser", group "Example Repo", both matched signals, PR url, 1h idle.
    assert (
        "- **Fix the parser** — Example Repo — changes requested on PR #98; ended on a question"
        " — https://github.com/example-org/example-repo/pull/98 — last active 1h ago"
    ) in digest
    assert "scout-morning-briefing" not in digest  # Scout's own run excluded from the digest


@pytest.mark.parametrize(("cap", "expected"), [(1, ["2"]), (2, ["2", "1"]), (3, ["2", "1", "3"])])
def test_cold_cache_fetches_recent_live_sessions_prs_first(fake_data_dir: Path, cap: int, expected: list[str]) -> None:
    s = support_dir()
    repo = "example-org/example-repo"
    # The desktop store lists local_A first, but A is the older of the two live sessions.
    write_desktop_record(s, "local_A", lastActivityAt=MS - 5 * 3_600_000, prs=[{"prNumber": 1, "repo": repo}])
    write_desktop_record(s, "local_B", lastActivityAt=MS - 3_600_000, prs=[{"prNumber": 2, "repo": repo}])
    # C is the most recently active but archived: its PR is fetched last.
    write_desktop_record(s, "local_C", isArchived=True, lastActivityAt=MS - 60_000, prs=[{"prNumber": 3, "repo": repo}])
    calls: list[str] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv[2])
        return json.dumps({"state": "OPEN", "mergeStateStatus": "BLOCKED"})

    opts = BuildOptions(
        data_dir=fake_data_dir,
        settings=AgentSessionsSettings(pr_fetch_cap=cap),
        claude_home=claude_home(),
        support_dir=s,
        now=NOW,
        gh_runner=runner,
        gh_available=lambda: True,
        toplevel=lambda p: None,
        pid_alive=lambda pid: False,
    )
    build_index(opts)
    assert calls == expected


def test_digest_is_written_atomically(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    run(opts=_world(fake_data_dir), render=True, tz_name="UTC")
    digest = fake_data_dir / ".scout-cache" / "cc-sessions.md"
    temps = [src for src, dst in replaced if dst == digest]
    assert len(temps) == 1 and temps[0].parent == digest.parent and temps[0].name.startswith(".cc-sessions.md.")
    assert stat.S_IMODE(digest.stat().st_mode) == 0o600
    assert not list(digest.parent.glob("*.tmp"))


def test_digest_write_failure_propagates_and_main_returns_1(
    fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    digest = fake_data_dir / ".scout-cache" / "cc-sessions.md"
    digest.mkdir()  # a directory where the digest goes: the replace fails
    with pytest.raises(OSError):
        run(opts=_world(fake_data_dir), render=True, tz_name="UTC")
    assert not list(digest.parent.glob("*.tmp"))
    assert index_mod.main(render=True, use_gh=False, tz_name="UTC") == 1
    assert "could not write" in capsys.readouterr().err


def test_a_deleted_transcript_drops_out_of_the_transcript_cache(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    run(opts=opts)
    cache_file = fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json"
    e_path = claude_home() / "projects" / "-Users-alex-code-other" / f"{UE}.jsonl"
    a_path = claude_home() / "projects" / (REPO_DIR + "--claude-worktrees-w1") / f"{UA}.jsonl"
    assert {str(e_path), str(a_path)} <= set(json.loads(cache_file.read_text(encoding="utf-8")))
    e_path.unlink()
    run(opts=opts)
    cached = set(json.loads(cache_file.read_text(encoding="utf-8")))
    assert str(e_path) not in cached and str(a_path) in cached


def test_a_pr_no_longer_referenced_drops_out_of_the_pr_cache(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    run(opts=opts)
    pr_file = fake_data_dir / ".scout-cache" / "sessions-pr.cache.json"
    assert set(json.loads(pr_file.read_text(encoding="utf-8"))) == {
        "example-org/example-repo#98",
        "example-org/example-repo#64",
    }
    write_desktop_record(support_dir(), "local_A", cliSessionId=UA, lastActivityAt=MS - 3_600_000)  # PR link gone
    run(opts=opts)
    assert set(json.loads(pr_file.read_text(encoding="utf-8"))) == {"example-org/example-repo#64"}


def test_build_options_defaults_honour_monkeypatched_gh(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(gh, "gh_available", lambda: False)
    monkeypatch.setattr(gh, "default_runner", lambda argv: calls.append(argv))
    from scout.sessions.index import default_options

    opts = default_options(fake_data_dir)
    assert opts.gh_available() is False
    opts.gh_runner(["pr", "view", "1"])
    assert calls == [["pr", "view", "1"]]


def test_build_options_toplevel_honors_monkeypatch(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.sessions.derive as derive_mod
    from scout.sessions.index import default_options

    monkeypatch.setattr(derive_mod, "git_toplevel", lambda p: "/patched")
    assert default_options(fake_data_dir).toplevel("/anything") == "/patched"

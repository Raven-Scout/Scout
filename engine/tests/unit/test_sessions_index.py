"""Integration-style unit tests for scout.sessions.index against a fake HOME tree."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scout.sessions.github as gh
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
REPO_DIR = "-Users-alex-code-example-repo"


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
    # Claude Code encodes a cwd by replacing both "/" and "." with "-".
    vault_dir = re.sub(r"[/.]", "-", str(fake_data_dir))
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


def test_gh_missing_marks_prs_unknown_with_one_error(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir, gh_ok=False))
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.pr is not None and a.pr.state == "unknown"
    assert a.state == "needs_you" and a.state_reasons == ["ended on a question"]  # question still detected
    assert [e.source for e in idx.source_errors] == ["gh"] and "not found" in idx.source_errors[0].message
    assert idx.source_counts["prs_refreshed"] == 0


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


def test_build_options_defaults_honour_monkeypatched_gh(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(gh, "gh_available", lambda: False)
    monkeypatch.setattr(gh, "default_runner", lambda argv: calls.append(argv))
    from scout.sessions.index import default_options

    opts = default_options(fake_data_dir)
    assert opts.gh_available() is False
    opts.gh_runner(["pr", "view", "1"])
    assert calls == [["pr", "view", "1"]]

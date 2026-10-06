"""Unit tests for scout.sessions.cli_home."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout.sessions.cli_home import (
    default_claude_home,
    load_live_processes,
    pid_alive,
    project_path_from_dirname,
    transcript_paths,
)
from tests.unit.sessions_helpers import claude_home, write_pid_file, write_transcript

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


def test_default_claude_home() -> None:
    assert default_claude_home() == Path.home() / ".claude"


def test_pid_alive_for_self_and_dead_pid() -> None:
    assert pid_alive(os.getpid()) is True
    assert pid_alive(2**22 - 1) is False  # far above pid_max on macOS/Linux


def test_pid_alive_rejects_non_positive_and_out_of_range_pids() -> None:
    # 0 and -1 address a process group / every process, so os.kill(pid, 0) "succeeds" on them.
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    assert pid_alive(2**40) is False  # os.kill raises OverflowError on an out-of-range pid


def test_load_live_processes_reports_non_positive_pids() -> None:
    h = claude_home()
    write_pid_file(h, 0, U1, "/Users/alex/code/example-repo")
    write_pid_file(h, -1, U2, "/Users/alex/code/other")
    live, errors = load_live_processes(h, is_alive=lambda pid: True)
    assert live == {}
    assert sorted(e.message for e in errors) == [
        "-1.json: pid or sessionId missing or malformed",
        "0.json: pid or sessionId missing or malformed",
    ]


def test_load_live_processes_drops_dead_pids_and_bad_files() -> None:
    h = claude_home()
    write_pid_file(h, 111, U1, "/Users/alex/code/example-repo")
    write_pid_file(h, 222, U2, "/Users/alex/code/other")
    (h / "sessions" / "333.json").write_text("nope", encoding="utf-8")
    live, errors = load_live_processes(h, is_alive=lambda pid: pid == 111)
    assert set(live) == {U1}
    assert live[U1].pid == 111 and live[U1].cwd == "/Users/alex/code/example-repo"
    assert len(errors) == 1 and errors[0].source == "claude-home" and "333.json" in errors[0].message


def test_load_live_processes_missing_dir_is_quiet() -> None:
    live, errors = load_live_processes(claude_home())
    assert live == {} and errors == []


def test_transcript_paths_maps_uuid_to_newest_file() -> None:
    h = claude_home()
    old = write_transcript(h, "-Users-alex-code-example-repo", U1, [{"type": "user"}], mtime_ago_hours=30)
    new = write_transcript(h, "-Users-alex-code-example-repo--claude-worktrees-w1", U1, [{"type": "user"}])
    write_transcript(h, "-Users-alex-code-other", U2, [{"type": "user"}])
    paths = transcript_paths(h)
    assert paths[U1] == new and paths[U1] != old
    assert paths[U2].parent.name == "-Users-alex-code-other"


def test_pid_alive_counts_another_users_process_as_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def eperm(pid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")  # the process exists; we may not signal it

    monkeypatch.setattr(os, "kill", eperm)
    assert pid_alive(4242) is True


def test_transcript_paths_picks_the_newest_copy_whatever_the_listing_order() -> None:
    h = claude_home()
    a = write_transcript(h, "-Users-alex-code-example-repo", U1, [{"type": "user"}])
    b = write_transcript(h, "-Users-alex-code-example-repo--claude-worktrees-w1", U1, [{"type": "user"}])
    # Make each copy the newest in turn; one of the two runs lists the newer copy second.
    for newer, older in ((a, b), (b, a)):
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_003_600, 1_700_003_600))
        assert transcript_paths(h)[U1] == newer


def test_project_path_from_dirname() -> None:
    assert project_path_from_dirname("-Users-alex-code-repo") == "/Users/alex/code/repo"
    assert project_path_from_dirname("opaque") == "opaque"


def test_load_live_processes_reports_wrong_shapes() -> None:
    h = claude_home()
    (h / "sessions").mkdir(parents=True, exist_ok=True)
    (h / "sessions" / "1.json").write_text("[]", encoding="utf-8")  # not an object
    (h / "sessions" / "2.json").write_text(json.dumps({"pid": "x", "sessionId": U1}), encoding="utf-8")  # pid is str
    (h / "sessions" / "3.json").write_text(json.dumps({"pid": 3, "cwd": "/x"}), encoding="utf-8")  # missing sessionId
    (h / "sessions" / "4.json").write_text(json.dumps({"pid": True, "sessionId": U2}), encoding="utf-8")  # pid is bool
    live, errors = load_live_processes(h, is_alive=lambda pid: True)
    assert live == {}
    assert sorted(e.message for e in errors) == [
        "1.json: not a JSON object",
        "2.json: pid or sessionId missing or malformed",
        "3.json: pid or sessionId missing or malformed",
        "4.json: pid or sessionId missing or malformed",
    ]
    assert all(e.source == "claude-home" for e in errors)

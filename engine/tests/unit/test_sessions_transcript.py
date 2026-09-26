"""Unit tests for scout.sessions.transcript."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scout.sessions.model import LastTurn, TranscriptInfo
from scout.sessions.transcript import (
    TRANSCRIPT_CACHE_FILENAME,
    extract_files_touched,
    extract_first_message,
    load_transcript_cache,
    parse_transcript,
    transcript_info,
    write_transcript_cache,
)
from tests.unit.sessions_helpers import claude_home, write_transcript

U1 = "11111111-1111-1111-1111-111111111111"


def _user(text: str, ts: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _assistant(blocks: list[dict], ts: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": blocks}}


def _tool_result(tool_use_id: str, ts: str) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]},
    }


def test_parse_transcript_end_turn_counts_tools_and_files() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            {"type": "custom-title", "customTitle": "scratch"},
            _user("please fix the parser", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/Users/alex/code/example-repo/a.py"},
                    }
                ],
                "2026-09-08T10:00:05.000Z",
            ),
            _tool_result("t1", "2026-09-08T10:00:06.000Z"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Edit",
                        "input": {"file_path": "/Users/alex/code/example-repo/a.py"},
                    }
                ],
                "2026-09-08T10:00:07.000Z",
            ),
            _tool_result("t2", "2026-09-08T10:00:08.000Z"),
            _assistant([{"type": "text", "text": "Done. The parser now handles blanks."}], "2026-09-08T10:00:09.000Z"),
        ],
    )
    info = parse_transcript(p)
    assert info.first_prompt == "please fix the parser"
    assert info.files_touched == ["/Users/alex/code/example-repo/a.py"]
    assert info.tool_calls == 2
    assert info.last_turn.kind == "end_turn"
    assert info.last_turn.at == "2026-09-08T10:00:09Z"
    assert info.mtime_ns == p.stat().st_mtime_ns


def test_parse_transcript_question_via_ask_user_question_tool() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            _user("go", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": []}}],
                "2026-09-08T10:00:01.000Z",
            ),
        ],
    )
    assert parse_transcript(p).last_turn.kind == "question"


def test_parse_transcript_answered_question_is_tool_use_not_question() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            _user("go", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": []}}],
                "2026-09-08T10:00:01.000Z",
            ),
            _tool_result("q1", "2026-09-08T10:00:30.000Z"),
        ],
    )
    assert parse_transcript(p).last_turn.kind == "tool_use"


def test_parse_transcript_question_via_trailing_question_mark() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            _user("go", "2026-09-08T10:00:00.000Z"),
            _assistant([{"type": "text", "text": "Which repo do you mean?"}], "2026-09-08T10:00:01.000Z"),
        ],
    )
    assert parse_transcript(p).last_turn.kind == "question"


def test_parse_transcript_without_assistant_is_unknown_and_tolerates_garbage() -> None:
    p = write_transcript(claude_home(), "-Users-alex-code-example-repo", U1, [_user("hi", "2026-09-08T10:00:00.000Z")])
    p.write_text(p.read_text(encoding="utf-8") + "not json at all\n", encoding="utf-8")
    info = parse_transcript(p)
    assert info.last_turn.kind == "unknown" and info.tool_calls == 0


def test_moved_extractors_keep_their_contract(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "user", "message": {"content": "found it"}})
        + "\n"
        + '{"file_path":"/Users/me/.claude/plugins/cache/abc"}\n'
        + '{"file_path":"/Users/me/repo/src/main.py"}\n',
        encoding="utf-8",
    )
    assert extract_first_message(jsonl) == "found it"
    assert extract_files_touched(jsonl) == ["/Users/me/repo/src/main.py"]


def test_transcript_cache_round_trip_and_mtime_reuse(tmp_path: Path) -> None:
    p = write_transcript(
        claude_home(), "-Users-alex-code-example-repo", U1, [_user("warm one", "2026-09-08T10:00:00.000Z")]
    )
    cache: dict[str, TranscriptInfo] = {}
    first = transcript_info(p, cache=cache)
    assert first.first_prompt == "warm one" and str(p) in cache

    cache_path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    write_transcript_cache(cache_path, cache)
    reloaded = load_transcript_cache(cache_path)
    assert reloaded[str(p)] == first

    # Corrupt the file but keep mtime: the cached entry must be served.
    mtime = p.stat().st_mtime_ns
    p.write_bytes(b"garbage\n")
    os.utime(p, ns=(mtime, mtime))
    assert transcript_info(p, cache=reloaded).first_prompt == "warm one"

    # Bump mtime: re-parse.
    os.utime(p, ns=(mtime + 1_000_000_000, mtime + 1_000_000_000))
    assert "could not extract" in transcript_info(p, cache=reloaded).first_prompt


def test_load_transcript_cache_missing_or_corrupt_is_empty(tmp_path: Path) -> None:
    assert load_transcript_cache(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2", encoding="utf-8")
    assert load_transcript_cache(bad) == {}


def test_load_transcript_cache_skips_wrongly_typed_entries(tmp_path: Path) -> None:
    good = {
        "path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo/good.jsonl",
        "first_prompt": "hi",
        "files_touched": ["~/code/example-repo/a.py"],
        "tool_calls": 2,
        "last_turn": {"at": "2026-09-08T10:00:00Z", "kind": "end_turn"},
        "mtime_ns": 5,
    }
    bad = {
        "last_turn_str": {**good, "last_turn": "x"},
        "last_turn_kind_int": {**good, "last_turn": {"at": None, "kind": 3}},
        "last_turn_at_int": {**good, "last_turn": {"at": 5, "kind": "end_turn"}},
        "path_int": {**good, "path": 5},
        "first_prompt_none": {**good, "first_prompt": None},
        "files_not_a_list": {**good, "files_touched": "~/a.py"},
        "files_not_str": {**good, "files_touched": [1]},
        "tool_calls_bool": {**good, "tool_calls": True},
        "tool_calls_str": {**good, "tool_calls": "2"},
        "mtime_bool": {**good, "mtime_ns": True},
        "mtime_float": {**good, "mtime_ns": 5.0},
        "missing_last_turn": {k: v for k, v in good.items() if k != "last_turn"},
    }
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(json.dumps({"good": good, **bad}), encoding="utf-8")
    loaded = load_transcript_cache(path)
    assert set(loaded) == {"good"}
    assert loaded["good"].last_turn.kind == "end_turn" and loaded["good"].tool_calls == 2


def _entry(prompt: str) -> TranscriptInfo:
    return TranscriptInfo(
        path=f"/Users/alex/.claude/projects/-x/{prompt}.jsonl",
        first_prompt=prompt,
        files_touched=[],
        tool_calls=0,
        last_turn=LastTurn(at=None, kind="end_turn"),
        mtime_ns=1,
    )


def test_interleaved_transcript_cache_writes_never_share_a_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    sources: list[str] = []
    real_replace = os.replace

    def replace_after_a_concurrent_writer(src: str, dst: str) -> None:
        sources.append(os.fspath(src))
        if len(sources) == 1:  # a second build writes the same cache while the first is mid-write
            write_transcript_cache(path, {"b": _entry("b")})
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_after_a_concurrent_writer)
    write_transcript_cache(path, {"a": _entry("a")})
    assert len(sources) == 2 and sources[0] != sources[1]
    assert set(load_transcript_cache(path)) == {"a"}  # the outer write landed whole, last
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_write_transcript_cache_never_raises_when_parent_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    write_transcript_cache(blocker / TRANSCRIPT_CACHE_FILENAME, {})  # must not raise
    assert blocker.read_text(encoding="utf-8") == "not a dir"
    assert not list(tmp_path.glob("*.tmp"))

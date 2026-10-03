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
    CachedTranscript,
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


def test_transcript_cache_round_trip_and_unchanged_reuse(tmp_path: Path) -> None:
    p = write_transcript(
        claude_home(), "-Users-alex-code-example-repo", U1, [_user("warm one", "2026-09-08T10:00:00.000Z")]
    )
    cache: dict[str, CachedTranscript] = {}
    first = transcript_info(p, cache=cache)
    assert first.first_prompt == "warm one" and cache[str(p)].checkpoint is not None

    cache_path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    assert write_transcript_cache(cache_path, cache) is True
    reloaded = load_transcript_cache(cache_path)
    assert reloaded == cache

    # Same file, size and mtime but different bytes: served from the cache without reading.
    st = p.stat()
    p.write_bytes(b"x" * (st.st_size - 1) + b"\n")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert transcript_info(p, cache=reloaded).first_prompt == "warm one"

    # A new mtime at the same size is not an append: full re-parse.
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert "could not extract" in transcript_info(p, cache=reloaded).first_prompt


@pytest.mark.parametrize(
    "text",
    [
        "[1,2",
        "[]",
        '{"version": 1, "entries": {}}',
        '{"version": true, "entries": {}}',
        '{"version": 2, "entries": []}',
    ],
)
def test_a_transcript_cache_of_another_version_or_shape_is_empty(tmp_path: Path, text: str) -> None:
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(text, encoding="utf-8")
    assert load_transcript_cache(path) == {}


def test_a_missing_transcript_cache_is_empty(tmp_path: Path) -> None:
    assert load_transcript_cache(tmp_path / "nope.json") == {}


_GOOD_INFO = {
    "path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo/good.jsonl",
    "first_prompt": "hi",
    "files_touched": ["~/code/example-repo/a.py"],
    "tool_calls": 2,
    "last_turn": {"at": "2026-09-08T10:00:00Z", "kind": "end_turn"},
    "mtime_ns": 5,
}


def test_a_plan_1_transcript_cache_is_empty(tmp_path: Path) -> None:
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(json.dumps({_GOOD_INFO["path"]: _GOOD_INFO}), encoding="utf-8")  # plan 1's flat layout
    assert load_transcript_cache(path) == {}


def _checkpoint(**overrides: object) -> dict:
    base: dict = {
        "dev": 1,
        "ino": 2,
        "size": 10,
        "mtime_ns": 5,
        "offset": 10,
        "head_sha1": "0" * 40,
        "lines": 1,
        "first_prompt_final": True,
        "files_smallest": [],
        "tool_calls": 0,
        "last_assistant": None,
        "pending_questions": [],
        "last_ts": None,
    }
    base.update(overrides)
    return base


def test_load_transcript_cache_skips_wrongly_typed_entries(tmp_path: Path) -> None:
    good = _GOOD_INFO
    bad_info = {
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
        "info_not_a_dict": "x",
    }
    bad_checkpoints = {
        "cp_not_a_dict": "x",
        "cp_offset_past_size": _checkpoint(offset=11),
        "cp_negative_offset": _checkpoint(offset=-1),
        "cp_dev_bool": _checkpoint(dev=True),
        "cp_sha_int": _checkpoint(head_sha1=5),
        "cp_final_int": _checkpoint(first_prompt_final=1),
        "cp_too_many_files": _checkpoint(files_smallest=[f"f{n}" for n in range(11)]),
        "cp_pending_not_str": _checkpoint(pending_questions=[1]),
        "cp_unknown_kind": _checkpoint(last_assistant="thinking"),
        "cp_ts_int": _checkpoint(last_ts=5),
        "cp_missing_last_ts": {k: v for k, v in _checkpoint().items() if k != "last_ts"},
    }
    entries: dict = {
        "good": {"info": good, "checkpoint": _checkpoint()},
        "good_unread": {"info": good, "checkpoint": None},
        "no_checkpoint_key": {"info": good},
    }
    entries.update({k: {"info": v, "checkpoint": _checkpoint()} for k, v in bad_info.items()})
    entries.update({k: {"info": good, "checkpoint": v} for k, v in bad_checkpoints.items()})
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(json.dumps({"version": 2, "entries": entries}), encoding="utf-8")
    loaded = load_transcript_cache(path)
    assert set(loaded) == {"good", "good_unread"}
    assert loaded["good"].info.tool_calls == 2 and loaded["good_unread"].checkpoint is None


def _entry(prompt: str) -> CachedTranscript:
    info = TranscriptInfo(
        path=f"/Users/alex/.claude/projects/-x/{prompt}.jsonl",
        first_prompt=prompt,
        files_touched=[],
        tool_calls=0,
        last_turn=LastTurn(at=None, kind="end_turn"),
        mtime_ns=1,
    )
    return CachedTranscript(info=info, checkpoint=None)


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
    assert write_transcript_cache(blocker / TRANSCRIPT_CACHE_FILENAME, {}) is False  # must not raise
    assert blocker.read_text(encoding="utf-8") == "not a dir"
    assert not list(tmp_path.glob("*.tmp"))

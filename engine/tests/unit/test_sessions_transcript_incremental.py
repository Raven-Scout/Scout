"""Checkpointed transcript parsing (1b spec §3.3): unchanged, grown and rewritten transcripts.

Every result is checked against the frozen plan 1 parser on the file as it stands.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.sessions import transcript as tr
from scout.sessions.model import TranscriptInfo
from scout.sessions.stats import BuildStats
from scout.sessions.transcript import CachedTranscript, transcript_info
from tests.unit.sessions_reference_parse import reference_parse
from tests.unit.sessions_transcript_cases import HOME, REPO, call, cases, jsonl, prompt, result, say

BASE_NS = 1_788_800_000_000_000_000


def _write(p: Path, data: bytes, step: int) -> None:
    """Write (step 0) or append, then move the mtime forward so every step is a visible change."""
    if step == 0:
        p.write_bytes(data)
    else:
        with p.open("ab") as f:
            f.write(data)
    t = BASE_NS + step * 1_000_000_000
    os.utime(p, ns=(t, t))


def _lookup(p: Path, cache: dict[str, CachedTranscript]) -> tuple[TranscriptInfo, BuildStats]:
    stats = BuildStats()
    return transcript_info(p, cache=cache, home=HOME, stats=stats), stats


def test_an_unchanged_transcript_is_not_read_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), say("done", 1)), 0)
    cache: dict[str, CachedTranscript] = {}
    first, cold = _lookup(p, cache)
    assert (cold.transcripts_full_parsed, cold.transcript_bytes_read) == (1, p.stat().st_size)

    def no_reads(*_a: object, **_k: object) -> object:
        raise AssertionError("an unchanged transcript was opened")

    monkeypatch.setattr(Path, "open", no_reads)
    again, warm = _lookup(p, cache)
    assert again == first and warm == BuildStats()


def test_appended_rows_are_parsed_from_the_checkpoint(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), call("t1", "Read", 1, file_path=f"{REPO}/a.py")), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    more = jsonl(result("t1", 2), call("t2", "Edit", 3, file_path=f"{REPO}/b.py"), result("t2", 4), say("Done.", 5))
    _write(p, more, 1)
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (0, 1)
    assert stats.transcript_bytes_read == len(more)
    assert info == reference_parse(p, home=HOME)
    assert info.tool_calls == 2 and info.files_touched == ["~/code/example-repo/a.py", "~/code/example-repo/b.py"]


def test_a_half_written_last_line_is_read_whole_on_the_next_build(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line = jsonl(call("t2", "Read", 3, file_path=f"{REPO}/late.py"))
    head, tail = line[: len(line) // 2], line[len(line) // 2 :]
    _write(p, jsonl(prompt("go", 0), say("thinking", 1)) + head, 0)
    cache: dict[str, CachedTranscript] = {}
    info, _ = _lookup(p, cache)
    assert info == reference_parse(p, home=HOME) and info.tool_calls == 0
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.offset == p.stat().st_size - len(head)  # the torn line is not consumed

    _write(p, tail, 1)
    info, stats = _lookup(p, cache)
    assert stats.transcripts_tail_parsed == 1 and stats.transcript_bytes_read == len(head) + len(tail)
    assert info == reference_parse(p, home=HOME)
    assert info.tool_calls == 1 and info.last_turn.kind == "tool_use"


def test_a_question_answered_in_a_later_tail(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[])), 0)
    cache: dict[str, CachedTranscript] = {}
    assert _lookup(p, cache)[0].last_turn.kind == "question"
    _write(p, jsonl(result("q1", 30)), 1)
    info, stats = _lookup(p, cache)
    assert stats.transcripts_tail_parsed == 1 and info.last_turn.kind == "tool_use"
    assert info == reference_parse(p, home=HOME)


@pytest.mark.parametrize("change", ["truncated", "replaced", "head_rewritten", "touched"])
def test_anything_but_an_append_is_parsed_again_in_full(tmp_path: Path, change: str) -> None:
    p = tmp_path / "s.jsonl"
    first_row = jsonl(prompt("go", 0))
    original = first_row + jsonl(call("t1", "Read", 1, file_path=f"{REPO}/a.py"), result("t1", 2))
    _write(p, original, 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    if change == "truncated":
        p.write_bytes(original[: len(original) // 2])
    elif change == "replaced":  # a different file at the same path, larger than before
        other = tmp_path / "other.jsonl"
        other.write_bytes(jsonl(prompt("something else", 0)) + original)
        os.replace(other, p)
    elif change == "head_rewritten":  # the same file, grown, but its first bytes changed
        p.write_bytes(jsonl(prompt("GO", 0)) + original[len(first_row) :] + jsonl(say("done", 3)))
    t = BASE_NS + 5_000_000_000  # "touched": same bytes, new mtime
    os.utime(p, ns=(t, t))
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (1, 0)
    assert info == reference_parse(p, home=HOME)


def test_a_failing_tail_parse_falls_back_to_a_full_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), say("thinking", 1)), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    _write(p, jsonl(say("done", 2)), 1)

    def broken(*_a: object, **_k: object) -> None:
        raise ValueError("unexpected checkpoint")

    monkeypatch.setattr(tr, "_tail", broken)
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (1, 0)
    assert info == reference_parse(p, home=HOME)


def test_the_first_prompt_is_found_once_its_line_is_complete(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line = jsonl(prompt("the real ask", 0))
    _write(p, jsonl({"type": "custom-title", "customTitle": "scratch"}) + line[:10], 0)
    cache: dict[str, CachedTranscript] = {}
    assert "could not extract" in _lookup(p, cache)[0].first_prompt
    _write(p, line[10:] + jsonl(say("ok", 1)), 1)
    info, _ = _lookup(p, cache)
    assert info.first_prompt == "the real ask" and info == reference_parse(p, home=HOME)
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.first_prompt_final


def test_the_first_prompt_is_final_after_fifty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(*({"type": "system", "content": f"note {n}"} for n in range(55))), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.first_prompt_final

    def not_again(_path: Path) -> str:
        raise AssertionError("the head was read again for a first prompt that is final")

    monkeypatch.setattr(tr, "extract_first_message", not_again)
    _write(p, jsonl(prompt("too late", 0)), 1)
    info, _ = _lookup(p, cache)
    assert "could not extract" in info.first_prompt
    assert info == reference_parse(p, home=HOME)  # the reference has its own extractor


def _split_points(data: bytes) -> list[int]:
    """Every line boundary, the middle of every line, and between every \\r\\n pair."""
    points: set[int] = set()
    start = 0
    while start < len(data):
        nl = data.find(b"\n", start)
        end = len(data) if nl == -1 else nl + 1
        points.update({start + (end - start) // 2, end})
        start = end
    points.update(i + 1 for i in range(len(data) - 1) if data[i : i + 2] == b"\r\n")
    return sorted(x for x in points if 0 < x < len(data))


@pytest.mark.parametrize("spaced", [False, True], ids=["compact", "spaced"])
@pytest.mark.parametrize("name", sorted(cases(spaced=False)))
def test_tail_parses_in_steps_equal_one_full_parse(tmp_path: Path, name: str, spaced: bool) -> None:
    data = cases(spaced=spaced)[name]
    p = tmp_path / "s.jsonl"
    for cut in _split_points(data):
        mid = (cut + len(data)) // 2
        pieces = [x for x in (data[:cut], data[cut:mid], data[mid:]) if x]
        cache: dict[str, CachedTranscript] = {}
        tails = 0
        for step, piece in enumerate(pieces):
            _write(p, piece, step)
            info, stats = _lookup(p, cache)
            tails += stats.transcripts_tail_parsed
        assert tails == len(pieces) - 1, (name, cut)
        assert info == reference_parse(p, home=HOME), (name, cut)

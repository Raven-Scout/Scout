"""Tolerance branches in `scout.sessions.transcript`'s extractors.

`test_sessions_transcript.py` covers the one-pass parse and the transcript
cache. What's left is every path the extractors must degrade quietly on: a
session JSONL with blank/malformed/non-user rows, truncation and file caps,
noise filtering, and an unreadable file — these run against
`~/.claude/projects`, a directory another process is actively writing, so an
untested tolerance branch is one that first executes at 03:00.

(Migrated from the retired `scout.scripts.cc_session_cache` module's edge
tests — Agent Sessions plan 1, task 10 — the extractors themselves moved to
`scout.sessions.transcript` in an earlier task.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.sessions import transcript as tr

# ---------------------------------------------------------------------------
# extract_first_message
# ---------------------------------------------------------------------------


def test_first_message_reads_a_nested_text_block(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "review OPS-1234"}]}}) + "\n",
        encoding="utf-8",
    )
    assert tr.extract_first_message(p) == "review OPS-1234"


def test_first_message_reads_a_top_level_string_content(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"role": "human", "content": "hello there"}) + "\n", encoding="utf-8")
    assert tr.extract_first_message(p) == "hello there"


def test_first_message_skips_blank_malformed_and_non_user_rows(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(
            [
                "",
                "   ",
                "{torn",
                '"a bare string"',
                json.dumps({"type": "assistant", "content": "not the user"}),
                json.dumps({"type": "user", "content": "   "}),  # blank -> keep looking
                json.dumps({"type": "user", "message": {"content": [{"type": "image"}]}}),  # no text
                json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": ""}]}}),
                json.dumps({"type": "user", "content": "the real first message"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert tr.extract_first_message(p) == "the real first message"


def test_first_message_only_scans_the_head_of_the_file(tmp_path: Path) -> None:
    """Scanning a 100 MB transcript per file would blow the preamble budget;
    a session's first prompt is always near the top."""
    p = tmp_path / "s.jsonl"
    filler = [json.dumps({"type": "assistant", "content": "x"})] * 60
    p.write_text("\n".join([*filler, json.dumps({"type": "user", "content": "too late"})]) + "\n", encoding="utf-8")
    assert tr.extract_first_message(p) == "(could not extract first message)"


def test_first_message_is_truncated(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "user", "content": "x" * 900}) + "\n", encoding="utf-8")
    assert len(tr.extract_first_message(p)) == tr._FIRST_MSG_MAX_CHARS


def test_first_message_reports_a_parse_error_for_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "user", "content": "hi"}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert tr.extract_first_message(p) == "(parse error)"


def test_first_message_sentinel_for_a_session_with_no_user_row(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "assistant", "content": "hello"}) + "\n", encoding="utf-8")
    assert tr.extract_first_message(p) == "(could not extract first message)"


# ---------------------------------------------------------------------------
# extract_files_touched
# ---------------------------------------------------------------------------


def test_files_touched_collapses_home_and_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"tool_input": {"file_path": "/Users/alex/work/a.py"}}),
                json.dumps({"tool_input": {"file_path": "/Users/alex/work/a.py"}}),
                json.dumps({"tool_input": {"file_path": "/etc/hosts"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert tr.extract_files_touched(p, home=Path("/Users/alex")) == ["/etc/hosts", "~/work/a.py"]


@pytest.mark.parametrize(
    "noisy",
    [
        "/Users/alex/.claude/projects/-x/tool-results/r.json",
        "/Users/alex/.claude/projects/-x/tasks/t.json",
        "/Users/alex/.claude/plugins/cache/p.js",
        "/Users/alex/work/node_modules/dep/index.js",
        "/private/tmp/claude-501/scratch.txt",
        "/Users/alex/.claude/projects/-x/memory/m.md",
    ],
)
def test_files_touched_drops_agent_internal_noise(tmp_path: Path, noisy: str) -> None:
    """These are the agent's own scratch files; surfacing them buries the
    user-meaningful edits the briefing is supposed to notice."""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"tool_input": {"file_path": noisy}}) + "\n", encoding="utf-8")
    assert tr.extract_files_touched(p, home=Path("/Users/alex")) == []


def test_files_touched_is_capped(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(json.dumps({"tool_input": {"file_path": f"/w/f{n:03d}.py"}}) for n in range(25)) + "\n",
        encoding="utf-8",
    )
    assert len(tr.extract_files_touched(p, home=Path("/Users/alex"))) == tr._MAX_FILES_TOUCHED


def test_files_touched_is_empty_for_an_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"tool_input": {"file_path": "/w/a.py"}}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert tr.extract_files_touched(p) == []

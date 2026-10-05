"""Synthetic transcripts that exercise every branch of the transcript parser (1b spec §3.4, §5).

Each case is a file's exact bytes: Claude Code's compact rows by default, json.dumps' spaced
rows with ``spaced=True``. Identifiers are synthetic (Alex, example-repo) per CLAUDE.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HOME = Path("/Users/alex")
REPO = "/Users/alex/code/example-repo"


def ts(second: int) -> str:
    return f"2026-09-08T10:{second // 60:02d}:{second % 60:02d}.000Z"


def prompt(text: str, second: int) -> dict[str, Any]:
    return {"type": "user", "timestamp": ts(second), "message": {"role": "user", "content": text}}


def say(text: str, second: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts(second),
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def call(tool_id: str, name: str, second: int, **tool_input: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts(second),
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def result(tool_id: str, second: int, content: str = "ok") -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": ts(second),
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]},
    }


def without_timestamp(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "timestamp"}


def jsonl(*rows: dict[str, Any] | bytes, spaced: bool = False, newline: bytes = b"\n") -> bytes:
    """One line per row; bytes rows are written as given."""
    seps = (", ", ": ") if spaced else (",", ":")
    return b"".join((r if isinstance(r, bytes) else json.dumps(r, separators=seps).encode()) + newline for r in rows)


def _compact(row: dict[str, Any]) -> bytes:
    return json.dumps(row, separators=(",", ":")).encode()


def cases(*, spaced: bool) -> dict[str, bytes]:
    def j(*rows: dict[str, Any] | bytes) -> bytes:
        return jsonl(*rows, spaced=spaced)

    def read(n: int, second: int) -> dict[str, Any]:
        return call(f"t{n}", "Read", second, file_path=f"{REPO}/src/f{n:02d}.py")

    many_paths = [f"{REPO}/src/m{n:02d}.py" for n in range(14, 0, -1)] + [
        "/etc/hosts",
        "/Users/alex/.claude/plugins/cache/plugin.js",
        f"{REPO}/node_modules/dep/index.js",
        f"{REPO}/src/m03.py",  # a repeat
    ]
    two_calls = {
        "type": "assistant",
        "timestamp": ts(1),
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": f"{REPO}/a.py"}},
                {"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {}},
            ],
        },
    }
    torn = (
        b'{"type":"assistant","timestamp":"2026-09-08T10:00:03.000Z","message":{"content":[{"type":"tool_use",'
        b'"id":"t2","name":"Read","input":{"file_path":"' + f"{REPO}/src/late.py".encode() + b'"'
    )
    return {
        "tool_loop": j(
            prompt("Tidy the parser", 0), read(1, 1), result("t1", 2), read(2, 3), result("t2", 4), say("Done.", 5)
        ),
        "question_tool_pending": j(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[])),
        "question_tool_answered": j(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[]), result("q1", 30)),
        "question_mark": j(prompt("go", 0), say("Which repo do you mean?", 1)),
        "answered_then_asks_in_text": j(
            prompt("go", 0),
            call("q1", "AskUserQuestion", 1, questions=[]),
            result("q1", 2),
            say("And which branch?", 3),
        ),
        "question_beside_another_call": j(prompt("go", 0), two_calls, result("t1", 2)),
        "assistant_without_timestamp": j(
            prompt("go", 0), read(1, 1), result("t1", 2), without_timestamp(say("done", 3))
        ),
        "assistant_with_a_bad_timestamp": j(
            prompt("go", 0), read(1, 1), result("t1", 2), {**say("done", 3), "timestamp": "not a time"}
        ),
        "user_row_with_a_bad_timestamp_last": j(
            prompt("go", 0), say("done", 1), {**prompt("thanks", 2), "timestamp": 5}
        ),
        "no_assistant_row": j(prompt("hi", 0)),
        "odd_assistant_shapes": j(
            prompt("go", 0),
            {"type": "assistant", "timestamp": ts(1), "message": {"content": "plain string"}},
            {"type": "assistant", "timestamp": ts(2), "message": "x"},
            {"type": "assistant", "timestamp": ts(3), "message": {"content": [5, "x", {"type": "text", "text": {}}]}},
        ),
        "garbage_and_non_objects": j(
            prompt("go", 0),
            b"not json at all",
            b'"a bare string"',
            b"[1, 2]",
            b"",
            b"   ",
            b'{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"',
            read(1, 1),
            say("done", 2),
        ),
        "user_row_that_mentions_the_assistant": j(
            prompt("go", 0),
            call("q1", "AskUserQuestion", 1, questions=[]),
            {**result("q1", 2), "toolUseResult": {"answers": {"role": "assistant"}}},
        ),
        "held_rows_before_a_decoded_user_row": j(
            prompt("go", 0),
            call("q1", "AskUserQuestion", 1, questions=[]),
            result("q1", 2),
            {**prompt("and then", 3), "toolUseResult": {"role": "assistant"}},
        ),
        "rows_neither_user_nor_assistant": j(
            prompt("go", 0),
            {"type": "progress", "data": {"message": {"type": "tool_use", "name": "Read"}}},
            {"type": "progress", "data": {"message": {"type": "assistant", "content": [{"type": "tool_use"}]}}},
            {"type": "file-history-snapshot", "snapshot": {}},
            say("done", 1),
        ),
        "file_paths_on_user_rows": j(
            prompt("go", 0),
            read(1, 1),
            {**result("t1", 2), "toolUseResult": {"file_path": f"{REPO}/src/from_result.py"}},
            say("done", 3),
        ),
        "more_than_ten_files": j(
            prompt("go", 0),
            *(call(f"t{n}", "Read", n, file_path=p) for n, p in enumerate(many_paths, 1)),
            say("ok", 30),
        ),
        "first_prompt_after_other_rows": j(
            {"type": "custom-title", "customTitle": "scratch"},
            {"type": "system", "content": "hook ran"},
            prompt("the real ask", 0),
            say("ok", 1),
        ),
        "first_prompt_past_line_fifty": j(
            *({"type": "system", "content": f"note {n}"} for n in range(55)), prompt("too late", 0), say("ok", 1)
        ),
        "half_written_last_line": j(prompt("go", 0), read(1, 1), result("t1", 2)) + torn,
        "crlf_line_endings": jsonl(
            prompt("go", 0), read(1, 1), result("t1", 2), say("done?", 3), spaced=spaced, newline=b"\r\n"
        ),
        "lone_carriage_return": j(prompt("go", 0))
        + _compact(read(1, 1))
        + b"\r"
        + _compact(result("t1", 2))
        + b"\n"
        + j(say("done", 3)),
        "invalid_utf8": j(prompt("go", 0))
        + b'{"type":"assistant","timestamp":"2026-09-08T10:00:01.000Z","message":{"content":[{"type":"tool_use",'
        b'"id":"t1","name":"Read","input":{"file_path":"/Users/alex/code/\xffbad.py"}}]}}\n'
        + b'{"type":"user","timestamp":"2026-09-08T10:00:02.000Z","message":{"content":[{"type":"tool_result",'
        b'"tool_use_id":"t1","content":"\xfe\xfd"}]}}\n',
        "empty_file": b"",
    }

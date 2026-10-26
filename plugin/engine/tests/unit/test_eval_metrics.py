"""Pure process metrics from a stream-json transcript (spec 2026-10-12 §6.2)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.eval.harness.metrics import (
    conforms,
    longest_search_run,
    metrics_from_dict,
    outside_reads,
    parse_stream,
    position_buckets,
    repeated_calls,
    whole_reads,
)

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden" / "stream-basic.jsonl"
VAULT = "/tmp/eval/vault"
NEW_FIELDS = {"cache_creation_tokens", "brain_tokens", "brain_token_turns", "api_retries", "api_error"}


@pytest.fixture(scope="module")
def m():
    return parse_stream(GOLDEN.read_text().splitlines())


def test_counts_come_from_the_tool_calls(m) -> None:
    assert m.tool_call_count == len(m.calls) == 14
    assert m.error_count == 1
    assert [c.name for c in m.calls[:3]] == ["Read", "Read", "Grep"]


def test_result_fields(m) -> None:
    assert (m.turns, m.cost_usd, m.duration_s) == (8, 0.42, 125.0)
    assert (m.input_tokens, m.output_tokens, m.cache_read_tokens) == (1200, 3400, 90000)
    assert m.denials == ("Read",)
    assert m.result_subtype == "success"


def test_init_fields(m) -> None:
    assert m.model == "claude-sonnet-5"
    assert m.mcp_servers == ("claude_ai_Slack",)


def test_unknown_events_are_ignored_and_a_truncated_line_is_counted(m) -> None:
    assert m.skipped_lines == 1


def test_positions_run_from_zero_to_one(m) -> None:
    assert m.calls[0].position == 0.0 and m.calls[-1].position == 1.0
    assert conforms(m) == []


def test_single_call_sits_in_the_middle() -> None:
    line = '{"type":"assistant","message":{"id":"a","content":[{"type":"tool_use","id":"t","name":"Read","input":{}}]}}'
    assert parse_stream([line]).calls[0].position == 0.5


def test_grep_spiral_is_the_longest_run_of_searches_without_a_read(m) -> None:
    assert longest_search_run(m.calls) == 7


def test_whole_read_of_the_big_index_is_counted_but_a_limited_read_is_not(m) -> None:
    assert whole_reads(m.calls, "knowledge-base.md") == 1
    assert whole_reads(m.calls, "SKILL.md") == 0


def test_repeated_identical_calls(m) -> None:
    assert repeated_calls(m.calls) == 1


def test_reads_outside_the_allowed_roots(m) -> None:
    assert outside_reads(m.calls, [VAULT]) == 1


def test_position_buckets_for_one_tool(m) -> None:
    assert position_buckets(m.calls, "Edit") == {"early": 0, "early_mid": 0, "late_mid": 0, "late": 1}
    assert sum(position_buckets(m.calls, "Grep").values()) == 4


def test_conforms_reports_a_count_that_disagrees_with_the_calls(m) -> None:
    from dataclasses import replace

    assert conforms(replace(m, tool_call_count=99)) == ["tool_call_count 99 != len(calls) 14"]
    assert conforms(replace(m, error_count=0)) == ["error_count 0 != errors in calls 1"]


def test_an_empty_stream_is_valid_and_empty() -> None:
    e = parse_stream([])
    assert (e.tool_call_count, e.turns, e.model, e.cost_usd) == (0, 0, None, None)
    assert (e.brain_tokens, e.brain_token_turns) == (0, 0)
    assert conforms(e) == []


def test_cache_creation_tokens_come_from_the_result(m) -> None:
    assert m.cache_creation_tokens == 5000


# --- brain-file context (spec 2026-10-13-static-instruction-trim §8.1) --------


def _use(msg: str, tid: str, path: str) -> str:
    block = {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}
    return json.dumps({"type": "assistant", "message": {"id": msg, "content": [block]}})


def _result(tid: str, content: object, *, error: bool = False) -> str:
    block = {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": error}
    return json.dumps({"type": "user", "message": {"content": [block]}})


def _text(msg: str) -> str:
    return json.dumps({"type": "assistant", "message": {"id": msg, "content": [{"type": "text", "text": "."}]}})


def test_brain_reads_count_tokens_and_the_turns_that_carry_them() -> None:
    lines = [
        _use("m1", "t1", "/v/SKILL.md"),
        _result("t1", "x" * 4000),  # 1000 tokens, carried by m2, m3, m4
        _use("m2", "t2", "/v/knowledge-base/people/alex.md"),
        _result("t2", "y" * 4000),  # not a brain file
        _use("m3", "t3", "/v/.scout-cache/brain/deferred/digest.md"),
        _result("t3", [{"type": "text", "text": "z" * 400}]),  # 100 tokens, carried by m4
        _text("m4"),
    ]
    m = parse_stream(lines)
    assert m.brain_tokens == 1100
    assert m.brain_token_turns == 1000 * 3 + 100 * 1


def test_every_brain_kind_counts_and_a_failed_read_does_not() -> None:
    lines = [
        _use("m1", "t1", "/v/DREAMING.md"),
        _result("t1", "a" * 40),
        _use("m1", "t2", "/v/RESEARCH.md"),
        _result("t2", "b" * 40),
        _use("m2", "t3", "/v/SKILL.md"),
        _result("t3", "c" * 4000, error=True),
        _use("m2", "t4", "/v/notes/SKILL.md.bak"),
        _result("t4", "d" * 4000),
        _text("m3"),
    ]
    m = parse_stream(lines)
    assert m.brain_tokens == 20
    assert m.brain_token_turns == 20 * 2  # m2 and m3 follow both results


def test_metrics_stored_before_the_brain_fields_still_load() -> None:
    old = {k: v for k, v in asdict(parse_stream([])).items() if k not in NEW_FIELDS}
    loaded = metrics_from_dict(old)
    assert (loaded.cache_creation_tokens, loaded.brain_tokens, loaded.brain_token_turns) == (None, None, None)
    assert (loaded.api_retries, loaded.api_error) == (None, None)


def test_a_session_that_gave_up_on_the_api_says_so() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "mcp_servers": []}),
        *(json.dumps({"type": "system", "subtype": "api_retry", "attempt": n, "max_retries": 10}) for n in (1, 2, 3)),
        json.dumps({"type": "assistant", "message": {"id": "s1", "model": "<synthetic>",
                    "content": [{"type": "text", "text": "API Error: Can't reach the API server"}]}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                    "total_cost_usd": 0, "result": "API Error: Can't reach the API server"}),
    ]  # fmt: skip
    m = parse_stream(lines)
    assert m.result_subtype == "success"  # what claude reports, so the subtype alone can't tell
    assert (m.api_retries, m.api_error) == (3, "API Error: Can't reach the API server")


def test_a_clean_session_has_no_api_error(m) -> None:
    assert (m.api_retries, m.api_error) == (0, None)

"""Pure process metrics from a stream-json transcript (spec 2026-10-12 §6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.harness.metrics import (
    conforms,
    longest_search_run,
    outside_reads,
    parse_stream,
    position_buckets,
    repeated_calls,
    whole_reads,
)

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden" / "stream-basic.jsonl"
VAULT = "/tmp/eval/vault"


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
    assert conforms(e) == []

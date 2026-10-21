"""Unit tests for scout.hooks.session_tokens.

Mirrors the bash original at ~/Scout/scripts/sum-session-tokens.sh. The Swift
SessionTokenEntry decoder consumes the same JSONL — schema MUST stay byte-stable
on field names + types.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.events import Event
from scout.hooks.session_tokens import _model_family, run

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _make_payload(tmp_path: Path, *, transcript: str | None, cwd: str = "/work") -> str:
    """Build a Stop-hook JSON payload string."""
    body: dict[str, object] = {"session_id": "abc-123", "cwd": cwd}
    if transcript is not None:
        body["transcript_path"] = transcript
    return json.dumps(body)


def _read_one_row(tracker: Path) -> dict:
    rows = [json.loads(line) for line in tracker.read_text().splitlines() if line.strip()]
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    return rows[0]


# -- model family helper -----------------------------------------------------


def test_model_family_classifies_known_prefixes():
    assert _model_family("claude-opus-4-5-20251015") == "claude-opus"
    assert _model_family("claude-sonnet-4-5-20251022") == "claude-sonnet"
    assert _model_family("claude-haiku-4-20250101") == "claude-haiku"


def test_model_family_falls_back_to_opus_for_unknown_or_empty():
    assert _model_family(None) == "claude-opus"
    assert _model_family("") == "claude-opus"
    assert _model_family("gpt-4-turbo") == "claude-opus"


# -- happy path: 2 Opus + 1 Sonnet fixture -----------------------------------


def test_run_decodes_3_turn_mixed_model_fixture(tmp_path, monkeypatch):
    """Decodes 3-turn fixture (2 Opus + 1 Sonnet) with deterministic token totals.

    Verifies all schema fields except ts/ts_et (timestamps).
    """
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = FIXTURES / "transcript-mixed-models.jsonl"
    payload = _make_payload(tmp_path, transcript=str(transcript))
    event = run(stdin=io.StringIO(payload))

    assert isinstance(event, Event)
    assert event.kind == "session.tokens.summed"
    assert event.source == "hook:session-tokens"

    row = _read_one_row(tracker)
    # Schema fields (all present, all required by Swift decoder).
    assert set(row.keys()) == {
        "ts",
        "ts_et",
        "session_id",
        "scout_mode",
        "cwd",
        "primary_model",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cost_usd",
        "num_turns",
        "duration_ms",
        "error",
    }
    assert row["session_id"] == "abc-123"
    assert row["scout_mode"] == "morning-briefing"
    assert row["cwd"] == "/work"
    # 2 Opus + 1 Sonnet → primary = Opus.
    assert row["primary_model"] == "claude-opus-4-5-20251015"
    assert row["input_tokens"] == 6000
    assert row["output_tokens"] == 3000
    assert row["cache_read_input_tokens"] == 350
    assert row["cache_creation_input_tokens"] == 700
    assert row["num_turns"] == 3
    assert row["duration_ms"] == 0
    assert row["error"] is None
    # Per-turn cost (Opus 4.5 $5/$25, Sonnet 4.5 $3/$15; 5-minute cache writes at 1.25x):
    #   Opus 1: (1000*5 + 500*25 + 100*0.5 + 200*6.25)/1e6 = 0.0188
    #   Opus 2: (2000*5 + 1000*25 + 50*0.5 + 100*6.25)/1e6 = 0.03565
    #   Sonnet: (3000*3 + 1500*15 + 200*0.3 + 400*3.75)/1e6 = 0.03306
    #   total = 0.08751
    assert row["cost_usd"] == pytest.approx(0.08751, abs=1e-9)
    # ts is UTC ISO with Z suffix at seconds precision.
    assert row["ts"].endswith("Z")
    assert "." not in row["ts"]  # seconds precision, no fractional
    # ts_et has a TZ token at the end.
    assert row["ts_et"].endswith(("EDT", "EST"))


def test_run_returns_event_with_payload(tmp_path, monkeypatch):
    """Returns Event(kind=session.tokens.summed, source=hook:session-tokens, payload=record)."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = FIXTURES / "transcript-mixed-models.jsonl"
    payload = _make_payload(tmp_path, transcript=str(transcript))
    event = run(stdin=io.StringIO(payload))

    assert isinstance(event, Event)
    assert event.kind == "session.tokens.summed"
    assert event.source == "hook:session-tokens"
    # Payload mirrors the JSONL row.
    row = _read_one_row(tracker)
    assert event.payload == row


# -- error path: transcript missing ------------------------------------------


def test_run_emits_zero_row_when_transcript_path_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    payload = _make_payload(tmp_path, transcript=None)
    event = run(stdin=io.StringIO(payload))

    assert isinstance(event, Event)
    assert event.kind == "session.tokens.summed"
    row = _read_one_row(tracker)
    assert row["error"] == "transcript_not_found"
    assert row["primary_model"] is None
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["cache_read_input_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0
    assert row["cost_usd"] == 0
    assert row["num_turns"] == 0
    assert row["duration_ms"] == 0


def test_run_emits_zero_row_when_transcript_path_empty_string(tmp_path, monkeypatch):
    """Bash treats `""` and missing identically (line 40: `[ -z ... ]`)."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    payload = json.dumps({"session_id": "x", "transcript_path": "", "cwd": "/w"})
    event = run(stdin=io.StringIO(payload))
    assert event is not None
    row = _read_one_row(tracker)
    assert row["error"] == "transcript_not_found"


def test_run_emits_zero_row_when_transcript_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    payload = json.dumps({"session_id": "x", "transcript_path": str(tmp_path / "does-not-exist.jsonl"), "cwd": "/w"})
    run(stdin=io.StringIO(payload))
    row = _read_one_row(tracker)
    assert row["error"] == "transcript_not_found"


# -- error path: file exists but no usage turns ------------------------------


def test_run_emits_zero_row_when_no_usage_turns(tmp_path, monkeypatch):
    """All-non-usage turns → zero-row with error: 'no_usage_turns'."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": []}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}}),
                "{not valid json",  # skipped silently
            ]
        )
    )

    # Patch the polling to be instant — don't actually wait 3s in tests.
    import scout.hooks.session_tokens as st

    monkeypatch.setattr(st, "_POLL_ATTEMPTS", 1)
    monkeypatch.setattr(st, "_POLL_INTERVAL_S", 0.0)

    payload = _make_payload(tmp_path, transcript=str(transcript))
    event = run(stdin=io.StringIO(payload))
    assert event is not None
    row = _read_one_row(tracker)
    assert row["error"] == "no_usage_turns"
    assert row["primary_model"] is None
    assert row["input_tokens"] == 0
    assert row["num_turns"] == 0


def test_run_skips_malformed_jsonl_lines(tmp_path, monkeypatch):
    """Lines that fail JSON parsing are silently skipped (jq fromjson? equivalent)."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                "{not valid json",  # silently skipped
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-5-20251015",
                            "usage": {"input_tokens": 100, "output_tokens": 50},
                        },
                    }
                ),
                "",  # blank line — also skipped
            ]
        )
    )
    payload = _make_payload(tmp_path, transcript=str(transcript))
    run(stdin=io.StringIO(payload))
    row = _read_one_row(tracker)
    assert row["num_turns"] == 1
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50


# -- mixed-model cost path ---------------------------------------------------


def test_mixed_model_cost_computed_per_turn_not_primary(tmp_path, monkeypatch):
    """Mixed-model fixture: cost uses each turn's pricing tier, not primary's.

    Two Opus + one Haiku. If cost were calculated using primary (Opus) for ALL
    turns, the Haiku turn's cost would be much higher — assert the cheap path.
    """
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = tmp_path / "transcript.jsonl"
    # 2 Opus, each with 1 input + 1 output token (negligible) so they don't
    # dominate. 1 Haiku with 1,000,000 inputs + 1,000,000 outputs.
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-5-20251015",
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-5-20251015",
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-haiku-4-20250101",
                            "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
                        },
                    }
                ),
            ]
        )
    )
    payload = _make_payload(tmp_path, transcript=str(transcript))
    run(stdin=io.StringIO(payload))
    row = _read_one_row(tracker)
    # Primary = Opus (2 vs 1 turn count).
    assert row["primary_model"] == "claude-opus-4-5-20251015"
    # Per-turn pricing (an unpriced Haiku takes the Haiku family row, $1/$5):
    #   Opus 1: (1*5 + 1*25)/1e6 = 3.0e-5
    #   Opus 2: same = 3.0e-5
    #   Haiku:  (1e6*1 + 1e6*5)/1e6 = 6.0
    #   total ≈ 6.00006
    expected = (5 + 25) / 1e6 * 2 + (1_000_000 * 1 + 1_000_000 * 5) / 1e6
    assert row["cost_usd"] == pytest.approx(expected, abs=1e-9)
    # If the implementation incorrectly used primary's pricing on Haiku:
    #   Haiku-as-Opus: (1e6*5 + 1e6*25)/1e6 = 30 — way more.
    assert row["cost_usd"] < 7.0


def test_unknown_model_recorded_in_error_field(tmp_path, monkeypatch):
    """Bash lines 109-116: any turn with model not matching the 3 families →
    error: "unknown_model:<that_model_name>", pricing falls back to Opus."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "gpt-4-turbo",  # unknown
                            "usage": {"input_tokens": 100, "output_tokens": 50},
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-5-20251015",
                            "usage": {"input_tokens": 100, "output_tokens": 50},
                        },
                    }
                ),
            ]
        )
    )
    payload = _make_payload(tmp_path, transcript=str(transcript))
    run(stdin=io.StringIO(payload))
    row = _read_one_row(tracker)
    assert row["error"] == "unknown_model:gpt-4-turbo"
    # Cost falls back to the Opus family row for the unknown turn (same rates as Opus 4.5).
    expected = ((100 * 5 + 50 * 25) / 1e6) * 2
    assert row["cost_usd"] == pytest.approx(expected, abs=1e-9)


# -- one turn per API message ------------------------------------------------


def _assistant_row(msg_id: str | None, model: str, usage: dict, block: str = "text") -> str:
    message: dict[str, object] = {"role": "assistant", "model": model, "content": [{"type": block}], "usage": usage}
    if msg_id is not None:
        message["id"] = msg_id
    return json.dumps({"type": "assistant", "message": message})


def test_rows_of_one_message_count_once(tmp_path, monkeypatch):
    """Claude Code writes a row per content block, each repeating the message's usage."""
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    usage = {"input_tokens": 10, "output_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant_row("msg_a", "claude-opus-5", usage, "thinking"),
                _assistant_row("msg_a", "claude-opus-5", usage, "text"),
                _assistant_row("msg_a", "claude-opus-5", usage, "tool_use"),
                _assistant_row("msg_b", "claude-opus-5", usage),
                _assistant_row(None, "claude-opus-5", usage),  # no id: its own turn
                _assistant_row(None, "claude-opus-5", usage),
            ]
        )
    )
    run(stdin=io.StringIO(_make_payload(tmp_path, transcript=str(transcript))))
    row = _read_one_row(tracker)
    assert row["num_turns"] == 4
    assert row["input_tokens"] == 40
    assert row["output_tokens"] == 4000
    assert row["cost_usd"] == pytest.approx(4 * (10 * 5 + 1000 * 25) / 1e6, abs=1e-12)


def test_one_hour_cache_writes_cost_twice_input(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 1_000_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 600_000, "ephemeral_5m_input_tokens": 400_000},
    }
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(_assistant_row("msg_a", "claude-sonnet-5", usage))
    run(stdin=io.StringIO(_make_payload(tmp_path, transcript=str(transcript))))
    # Sonnet 5 input $2: 600K at 2x ($4/M) + 400K at 1.25x ($2.50/M).
    assert _read_one_row(tracker)["cost_usd"] == pytest.approx(0.6 * 4 + 0.4 * 2.5, abs=1e-12)


def test_longest_model_prefix_wins():
    from scout.hooks.session_tokens import _model_rates

    assert _model_rates("claude-opus-5-5")["input"] == 4.00
    assert _model_rates("claude-opus-5")["input"] == 5.00
    assert _model_rates("claude-sonnet-5-5[1m]")["output"] == 10.00
    assert _model_rates("claude-opus-4-20250514")["input"] == 15.00
    assert _model_rates("claude-opus-9")["input"] == 5.00  # family row
    assert _model_rates(None)["input"] == 5.00


# -- pinned to claude's own figure on real runs --------------------------------
#
# tests/fixtures/usage-pinning/ holds three real research runs, anonymised:
# only model, a synthetic message id, content-block types and usage survive,
# plus the transcript's final `cost-state` row — claude's own totalCostUSD and
# per-model token counts, written at process exit. The estimate must match it.

PINNING = FIXTURES / "usage-pinning"


@pytest.mark.parametrize(
    ("name", "messages", "rows", "cost"),
    [
        ("opus-5-research", 34, 74, 4.4961095),
        ("sonnet-5-research", 3, 8, 0.3443072),
        ("haiku-4-5-research", 5, 12, 0.1154384),
    ],
)
def test_estimate_matches_claudes_total_cost(tmp_path, monkeypatch, name, messages, rows, cost):
    monkeypatch.setenv("SCOUT_MODE", "research")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = PINNING / f"{name}.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
    assert sum(1 for r in lines if (r.get("message") or {}).get("usage")) == rows  # the duplicates are there
    claude = next(r for r in lines if r.get("type") == "cost-state")
    (usage,) = claude["modelUsage"].values()

    run(stdin=io.StringIO(_make_payload(tmp_path, transcript=str(transcript))))
    row = _read_one_row(tracker)

    assert row["error"] is None
    assert row["num_turns"] == messages
    assert row["input_tokens"] == usage["inputTokens"]
    assert row["output_tokens"] == usage["outputTokens"]
    assert row["cache_read_input_tokens"] == usage["cacheReadInputTokens"]
    assert row["cache_creation_input_tokens"] == usage["cacheCreationInputTokens"]
    assert row["cost_usd"] == pytest.approx(claude["totalCostUSD"], abs=1e-6)
    assert row["cost_usd"] == pytest.approx(cost, abs=1e-6)


# -- defensive ---------------------------------------------------------------


def test_run_falls_back_to_manual_when_scout_mode_unset(tmp_path, monkeypatch):
    """Bash line 34 falls back to 'manual' (does NOT short-circuit like connector-log)."""
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    transcript = FIXTURES / "transcript-mixed-models.jsonl"
    payload = _make_payload(tmp_path, transcript=str(transcript))
    event = run(stdin=io.StringIO(payload))
    assert event is not None
    row = _read_one_row(tracker)
    assert row["scout_mode"] == "manual"


def test_run_handles_malformed_payload_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))

    # Hooks must NEVER raise — malformed payload returns None, no row written.
    result = run(stdin=io.StringIO("{not json"))
    assert result is None
    assert not tracker.exists()


def test_main_returns_zero_even_on_garbage_stdin(monkeypatch, tmp_path):
    """Hooks contract: main() catches everything and returns 0."""
    from scout.hooks.session_tokens import main

    monkeypatch.setenv("SCOUT_MODE", "manual")
    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))
    monkeypatch.setattr("sys.stdin", io.StringIO("not even json"))

    rc = main()
    assert rc == 0

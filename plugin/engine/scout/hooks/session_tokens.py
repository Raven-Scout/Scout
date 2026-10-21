"""Stop-hook port — sums message.usage across a session transcript.

Direct port of ~/Scout/scripts/sum-session-tokens.sh. Reads a Stop-hook JSON
payload on stdin (session_id / transcript_path / cwd), opens the transcript
JSONL, sums per-turn token counts, computes per-turn cost against each turn's
model, and appends one row to .scout-logs/session-tokens.jsonl.

The Swift `SessionTokenEntry` decoder (scout-app/Scout/Models/SessionTokenEntry.swift)
consumes this JSONL — schema MUST stay byte-stable on field names and types.

Phase 1 of the usage-and-connector-health design. Pricing is a static table
(see MODEL_PRICING_USD_PER_M_TOKENS); Phase 2 will replace dollar display with
quota utilization and the table becomes irrelevant. The estimate reproduces
claude's own total_cost_usd, which the hook can't read: claude writes it to the
transcript (the `cost-state` row) only when the process exits, after Stop.

Environment:
  SCOUT_MODE             — set by runner scripts. Without it, the mode named in
                           the run prompt when the session runs in a vault
                           (scout.hooks.run_context), else "manual" (note:
                           differs from connector_log which short-circuits when
                           unset).
  SESSION_TOKENS_TRACKER — override tracker path (used by tests / parity bats).

The vault is $SCOUT_DATA_DIR, else the one containing the payload's ``cwd``,
else ~/Scout. A run whose mode came from its prompt has no runner to record its
cost, so this hook also writes its spend to .scout-logs/usage-tracker.jsonl,
where the budget gate reads it (``record_run_cost``).

Hooks must NEVER raise — main() catches all exceptions and returns 0.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from scout import paths
from scout.config import resolve_timezone
from scout.events import Event, now_iso
from scout.hooks import run_context
from scout.ids import new_ulid


def _rates(input_: float, output: float, cache_read: float) -> dict[str, float]:
    # Cache writes: 1.25x input for the 5-minute TTL, 2x for the 1-hour TTL.
    return {
        "input": input_,
        "output": output,
        "cache_read": cache_read,
        "cache_create": input_ * 1.25,
        "cache_create_1h": input_ * 2,
    }


# PRICING: $ per 1M tokens, by model family. These price a model no row of
# MODEL_PRICING_USD_PER_M_TOKENS names: the dearest current rates in each
# family, so an unpriced model is over- rather than under-estimated. Phase 2
# makes this table irrelevant — quota utilization replaces dollar display.
PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "claude-opus": _rates(5.00, 25.00, 0.50),
    "claude-sonnet": _rates(3.00, 15.00, 0.30),
    "claude-haiku": _rates(1.00, 5.00, 0.10),
}

# Per-model rates, $ per 1M tokens, from Anthropic's first-party API price list
# (checked 2026-10-21). Cache reads are 0.1x input unless the list says
# otherwise. The longest matching prefix wins, so `claude-opus-5` doesn't price
# `claude-opus-5-5`. Pinned in tests against claude's own totalCostUSD for real
# Opus 5, Sonnet 5 and Haiku 4.5 runs (tests/fixtures/usage-pinning/).
MODEL_PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "claude-fable-5-1": _rates(10.00, 50.00, 0.25),
    "claude-mythos-5-1": _rates(10.00, 50.00, 0.25),
    "claude-fable-5": _rates(10.00, 50.00, 1.00),
    "claude-mythos-5": _rates(10.00, 50.00, 1.00),
    "claude-opus-5-5": _rates(4.00, 20.00, 0.20),
    "claude-opus-5": _rates(5.00, 25.00, 0.50),
    "claude-opus-4-8": _rates(5.00, 25.00, 0.50),
    "claude-opus-4-7": _rates(5.00, 25.00, 0.50),
    "claude-opus-4-6": _rates(5.00, 25.00, 0.50),
    "claude-opus-4-5": _rates(5.00, 25.00, 0.50),
    "claude-opus-4-1": _rates(15.00, 75.00, 1.50),
    "claude-opus-4-2": _rates(15.00, 75.00, 1.50),  # claude-opus-4-2025xxxx (Opus 4)
    "claude-sonnet-5-5": _rates(2.00, 10.00, 0.20),
    "claude-sonnet-5": _rates(2.00, 10.00, 0.20),
    "claude-sonnet-4": _rates(3.00, 15.00, 0.30),
    "claude-haiku-5-5": _rates(0.10, 0.50, 0.01),  # prompts up to 100K tokens
    "claude-haiku-4-5": _rates(1.00, 5.00, 0.10),
    "claude-3-5-haiku": _rates(0.80, 4.00, 0.08),
}

# Polling for transcript flush. Bash line 61: 6 attempts × 0.5s = 3s ceiling.
# On the happy path the first attempt finds usage and we never sleep.
# Module-level so tests can monkeypatch them down to zero.
_POLL_ATTEMPTS = 6
_POLL_INTERVAL_S = 0.5


def _model_family(model: str | None) -> str:
    """Map a Claude Code model string to its pricing family.

    Mirrors bash lines 99-102: prefix-match against {claude-opus, claude-sonnet,
    claude-haiku}; unknown / empty / None falls back to claude-opus
    (conservative — bash line 102 `else [$oi,$oo,$ocr,$occ]`).
    """
    if not model:
        return "claude-opus"
    for prefix in PRICING_USD_PER_M_TOKENS:
        if model.startswith(prefix):
            return prefix
    return "claude-opus"


def _model_rates(model: str | None) -> dict[str, float]:
    """The rates for one turn's model: the longest matching model prefix, else its family row."""
    if model:
        matches = [p for p in MODEL_PRICING_USD_PER_M_TOKENS if model.startswith(p)]
        if matches:
            return MODEL_PRICING_USD_PER_M_TOKENS[max(matches, key=len)]
    return PRICING_USD_PER_M_TOKENS[_model_family(model)]


def _is_known_model(model: str) -> bool:
    """Whether `model` matches a known family or a priced model (non-empty)."""
    if not model:
        return False
    return any(model.startswith(p) for p in (*PRICING_USD_PER_M_TOKENS, *MODEL_PRICING_USD_PER_M_TOKENS))


def _utc_ts() -> str:
    """UTC ISO-8601 with `Z` suffix at seconds precision (matches bash line 36)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local_ts(vault: Path | None = None) -> str:
    """Configured-zone timestamp like '2026-04-28 16:00 EDT' (#207).

    The emitted JSONL field stays named ``ts_et`` — it is a persisted schema
    key that downstream consumers grep for; only the zone became configurable.
    """
    return datetime.now(resolve_timezone(vault)).strftime("%Y-%m-%d %H:%M %Z")


def _tracker_path(vault: Path | None = None) -> Path:
    """Resolve the session-tokens.jsonl path.

    Precedence:
      1. SESSION_TOKENS_TRACKER env var (override; used by parity tests).
      2. <vault> / .scout-logs / session-tokens.jsonl (default; vault defaults to paths.data_dir()).
    """
    override = os.environ.get("SESSION_TOKENS_TRACKER")
    if override:
        return Path(override).expanduser()
    return (vault or paths.data_dir()) / ".scout-logs" / "session-tokens.jsonl"


def _zero_row(
    *,
    session_id: str,
    scout_mode: str,
    cwd: str,
    error: str,
) -> dict[str, Any]:
    """Build the error / no-data row (bash lines 41-48 and 67-74).

    Same shape as the success row, all numeric fields = 0, primary_model=null,
    error string set to `error`.
    """
    return {
        "ts": _utc_ts(),
        "ts_et": _local_ts(),
        "session_id": session_id,
        "scout_mode": scout_mode,
        "cwd": cwd,
        "primary_model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0,
        "num_turns": 0,
        "duration_ms": 0,
        "error": error,
    }


def _read_usage_turns(transcript_path: Path) -> list[dict[str, Any]]:
    """Read the transcript JSONL and return one turn per API message that has message.usage.

    Claude Code writes a transcript row per content block (thinking, text,
    tool_use), and every row of one API message repeats that message's usage.
    Rows are therefore keyed by message.id — the last row of each message wins,
    in the position of its first — or summing them counts each message once per
    block (2-4x on real runs). A row without an id is its own turn.

    Lines that fail JSON parsing are silently skipped (jq `fromjson?` equivalent
    — see bash line 62). Same for blank lines.
    """
    turns: dict[object, dict[str, Any]] = {}
    try:
        with transcript_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message")
                if isinstance(msg, dict) and msg.get("usage") is not None:
                    turns[msg.get("id") or len(turns)] = obj
    except OSError:
        # Treated upstream as transcript_not_found; here we just return empty.
        return []
    return list(turns.values())


def _poll_for_usage_turns(transcript_path: Path) -> list[dict[str, Any]]:
    """Poll up to _POLL_ATTEMPTS × _POLL_INTERVAL_S for usage turns to appear.

    Bash lines 60-65: on very short sessions Claude Code can fire Stop before
    the final assistant turn lands on disk; a brief poll lets the write flush.
    Zero latency on the happy path (first attempt finds usage and returns).
    """
    for attempt in range(_POLL_ATTEMPTS):
        turns = _read_usage_turns(transcript_path)
        if turns:
            return turns
        if attempt < _POLL_ATTEMPTS - 1 and _POLL_INTERVAL_S > 0:
            time.sleep(_POLL_INTERVAL_S)
    return []


def _compute_cost_usd(turns: list[dict[str, Any]]) -> float:
    """Per-turn cost summation (bash lines 89-107).

    Each turn is priced against ITS OWN model's rates, not the primary's.
    Unknown models fall back to the Opus family row. Order of operations matches
    the bash: sum(per_turn_dollar_amounts) where each per_turn_dollar_amount is
    (i*price_in + o*price_out + cr*price_cr + cc*price_cc) / 1_000_000, with
    cache writes split by TTL.
    """
    total = 0.0
    for t in turns:
        msg = t.get("message") or {}
        usage = msg.get("usage") or {}
        rates = _model_rates(msg.get("model"))
        i = usage.get("input_tokens") or 0
        o = usage.get("output_tokens") or 0
        cr = usage.get("cache_read_input_tokens") or 0
        cc = usage.get("cache_creation_input_tokens") or 0
        # A 1-hour cache write costs more than a 5-minute one; Claude Code's usage
        # splits them under cache_creation. Without the split, all are 5-minute.
        split = usage.get("cache_creation")
        cc_1h = (split.get("ephemeral_1h_input_tokens") or 0) if isinstance(split, dict) else 0
        cc_1h = min(cc_1h, cc)
        total += (
            i * rates["input"]
            + o * rates["output"]
            + cr * rates["cache_read"]
            + (cc - cc_1h) * rates["cache_create"]
            + cc_1h * rates["cache_create_1h"]
        ) / 1_000_000
    return total


def _primary_model(turns: list[dict[str, Any]]) -> str:
    """Most-frequent model across usage turns (bash lines 86-87).

    Tie-break: Counter.most_common is stable in iteration order. The bash uses
    jq `group_by | max_by(length)`, which picks the first by sort order — so on
    ties the two implementations may diverge. The fixture deliberately uses
    2 Opus + 1 Sonnet so primary is unambiguous.
    """
    models = [(t.get("message") or {}).get("model") or "" for t in turns]
    counts = Counter(m for m in models if m)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _first_unknown_model(turns: list[dict[str, Any]]) -> str:
    """First turn whose model is not in {opus, sonnet, haiku} (bash lines 112-116).

    jq selects all unknowns then takes `.[0]` — so iteration order = transcript
    order. Returns "" if all turns use known models.
    """
    for t in turns:
        model = (t.get("message") or {}).get("model") or ""
        if not _is_known_model(model):
            return model
    return ""


def _append_row(record: dict[str, Any], vault: Path | None = None) -> None:
    """Append one compact JSON line to the tracker. Best-effort — never raises."""
    tracker = _tracker_path(vault)
    try:
        tracker.parent.mkdir(parents=True, exist_ok=True)
        with tracker.open("a", encoding="utf-8") as f:
            # Compact output to match `jq -nc` (bash line 41 etc.).
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


# usage-tracker.jsonl rows this hook writes carry this source (write-session-cost.sh writes
# "session" and "runner").
USAGE_SOURCE = "session-tokens"


def record_run_cost(vault: Path, *, mode: str, session_id: str, cost_usd: float, model: str) -> float:
    """Add this session's spend to the vault's usage tracker; return the amount added.

    For runs with no runner around them (the mode came from the prompt): nothing else
    records what they cost, and the budget gate (scout.scripts.budget_check) sums
    ``budget_spent`` over this file. The Stop hook fires at the end of every turn with the
    session's running total, so each row carries only what is new since this session's
    earlier rows. Best-effort — never raises.
    """
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    already = 0.0
    try:
        with tracker.open("r", encoding="utf-8") as f:
            for raw in f:
                try:
                    prior = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(prior, dict)
                    and prior.get("source") == USAGE_SOURCE
                    and prior.get("session_id") == session_id
                ):
                    already += float(prior.get("budget_spent") or 0)
    except (OSError, TypeError, ValueError):
        pass
    delta = round(cost_usd - already, 6)
    if delta <= 0:
        return 0.0
    row: dict[str, Any] = {
        "ts": _utc_ts(),
        "ts_local": _local_ts(),
        "type": mode,
        "budget_cap": 0,
        "budget_spent": delta,
        "exit_code": 0,
        "source": USAGE_SOURCE,
        "session_id": session_id,
    }
    if model:
        row["model"] = model
    try:
        tracker.parent.mkdir(parents=True, exist_ok=True)
        with tracker.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        return 0.0
    return delta


def _make_event(record: dict[str, Any]) -> Event:
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="session.tokens.summed",
        source="hook:session-tokens",
        payload=record,
    )


def run(*, stdin: IO[str] | None = None) -> Event | None:
    """Read Stop-hook payload, sum transcript usage, append one row, return Event.

    Returns:
        - Event in all success and recoverable-error paths (transcript_not_found,
          no_usage_turns, unknown_model, success).
        - None only when the stdin payload itself is unparseable (malformed JSON).
    """
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
    except (json.JSONDecodeError, ValueError):
        return None  # malformed stdin — never raise from a hook

    if not isinstance(payload, dict):
        return None

    session_id = str(payload.get("session_id") or "")
    transcript_path_str = str(payload.get("transcript_path") or "")
    cwd = str(payload.get("cwd") or "")
    transcript_path = Path(transcript_path_str).expanduser() if transcript_path_str else None
    if transcript_path is not None and not transcript_path.is_file():
        transcript_path = None
    ctx = run_context.resolve(payload, transcript_path)
    scout_mode = ctx.mode or "manual"

    # Guard 1 + 2: missing / empty transcript path (bash line 40 `[ -z ... ]`), or no such
    # file. Don't poll — go straight to the error row.
    if transcript_path is None:
        record = _zero_row(
            session_id=session_id,
            scout_mode=scout_mode,
            cwd=cwd,
            error="transcript_not_found",
        )
        _append_row(record, ctx.vault)
        return _make_event(record)

    # File exists but may still be flushing; poll up to ~3s for usage turns.
    turns = _poll_for_usage_turns(transcript_path)
    if not turns:
        record = _zero_row(
            session_id=session_id,
            scout_mode=scout_mode,
            cwd=cwd,
            error="no_usage_turns",
        )
        _append_row(record, ctx.vault)
        return _make_event(record)

    # Vectorize the four token sums in a single pass.
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_create = 0
    for t in turns:
        usage = (t.get("message") or {}).get("usage") or {}
        input_tokens += usage.get("input_tokens") or 0
        output_tokens += usage.get("output_tokens") or 0
        cache_read += usage.get("cache_read_input_tokens") or 0
        cache_create += usage.get("cache_creation_input_tokens") or 0

    primary = _primary_model(turns)
    cost_usd = _compute_cost_usd(turns)
    unknown = _first_unknown_model(turns)
    error = f"unknown_model:{unknown}" if unknown else None

    record = {
        "ts": _utc_ts(),
        "ts_et": _local_ts(ctx.vault),
        "session_id": session_id,
        "scout_mode": scout_mode,
        "cwd": cwd,
        "primary_model": primary,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        "cost_usd": cost_usd,
        "num_turns": len(turns),
        "duration_ms": 0,  # reserved; bash always writes 0
        "error": error,
    }
    _append_row(record, ctx.vault)
    if ctx.mode and ctx.mode_source == run_context.SOURCE_PROMPT and session_id:
        record_run_cost(ctx.vault, mode=ctx.mode, session_id=session_id, cost_usd=cost_usd, model=primary)
    return _make_event(record)


def main() -> int:
    """CLI entry point: scoutctl hook session-tokens. Always returns 0."""
    try:
        run()
    except Exception:
        # Hooks must never break a session.
        pass
    return 0

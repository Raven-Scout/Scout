"""Per-runtime run limits: a wall-clock timeout and a daily token ceiling (spec 2026-11-13 §8.6, Q6).

Both are **on by default for a runtime that can't cap its own spend** (no
``budget_cap`` capability): a 45-minute timeout per run, and a daily token ceiling.
Claude Code caps itself (``--max-budget-usd``), so for it both are off unless the
owner sets them, and its runs are unchanged.

``scout-config.yaml``::

    budget:
      per_runtime:
        <runtime id>:
          run_timeout_minutes: 45      # int > 0, or 0 / off for none
          max_tokens_per_day: auto     # int > 0, auto (the default formula), or 0 / off

The ceiling counts the usage tracker's rows for that runtime (``agent_runtime``, a
missing key meaning ``claude-code``) over the last 24 hours, summing their
``tokens`` (or ``input_tokens`` + ``output_tokens``). A run that would start past it
is skipped as ``skipped_budget``, like a USD overrun.

**The default ceiling is a placeholder.** Phase 0 derives the formula from the
owner's Claude Code ledgers. Until it reports, ``default_daily_token_ceiling``
returns ``PLACEHOLDER_DAILY_TOKENS``; the formula replaces that one function.
No runtime registered in Phase 1 lacks ``budget_cap``, so the placeholder can't
skip a run yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from scout import paths
from scout.agent_runtimes.base import AgentRuntime

DEFAULT_RUN_TIMEOUT_MINUTES = 45
# TODO(Phase 0, spec §8.6): replace with the formula from the owner's Claude Code ledgers.
PLACEHOLDER_DAILY_TOKENS = 5_000_000
CEILING_WINDOW = timedelta(hours=24)

Source = Literal["config", "default", "off"]


@dataclass(frozen=True)
class RuntimeLimits:
    runtime: str
    run_timeout_s: int | None
    timeout_source: Source
    max_tokens_per_day: int | None
    ceiling_source: Source


def default_daily_token_ceiling(data_dir: Path | None, runtime_id: str) -> int:
    """The default daily token ceiling for *runtime_id* (``max_tokens_per_day: auto``). Placeholder until Phase 0."""
    return PLACEHOLDER_DAILY_TOKENS


def _block(data_dir: Path | None, runtime_id: str) -> dict[str, Any]:
    """budget.per_runtime.<id>, or {} when absent or unreadable (limits never fail a run's start)."""
    try:
        from scout.config import load_config

        budget = load_config(data_dir).get("budget")
    except Exception:  # noqa: BLE001 - an unreadable config means the defaults
        return {}
    if not isinstance(budget, dict):
        return {}
    per = budget.get("per_runtime")
    if not isinstance(per, dict):
        return {}
    block = per.get(runtime_id)
    return block if isinstance(block, dict) else {}


def _off(value: Any) -> bool:
    return value is False or value == 0 or (isinstance(value, str) and value.strip().lower() in {"off", "0", "none"})


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
        return int(value.strip())
    return None


def limits_for(data_dir: Path | None, adapter: AgentRuntime) -> RuntimeLimits:
    """The limits for a run on *adapter*.

    A value that isn't valid is ignored in favour of the default, which is the safe
    side for a runtime without a cap (the default is on) and today's behaviour for
    Claude Code (the default is off).
    """
    self_capped = adapter.caps_own_spend
    block = _block(data_dir, adapter.id)

    raw_t = block.get("run_timeout_minutes")
    timeout_s: int | None
    t_src: Source
    if raw_t is not None and _off(raw_t):
        timeout_s, t_src = None, "off"
    elif (minutes := _positive_int(raw_t)) is not None:
        timeout_s, t_src = minutes * 60, "config"
    elif self_capped:
        timeout_s, t_src = None, "off"
    else:
        timeout_s, t_src = DEFAULT_RUN_TIMEOUT_MINUTES * 60, "default"

    raw_c = block.get("max_tokens_per_day")
    ceiling: int | None
    c_src: Source
    if raw_c is not None and _off(raw_c):
        ceiling, c_src = None, "off"
    elif (n := _positive_int(raw_c)) is not None:
        ceiling, c_src = n, "config"
    elif isinstance(raw_c, str) and raw_c.strip().lower() == "auto":
        ceiling, c_src = default_daily_token_ceiling(data_dir, adapter.id), "config"
    elif self_capped:
        ceiling, c_src = None, "off"
    else:
        ceiling, c_src = default_daily_token_ceiling(data_dir, adapter.id), "default"
    return RuntimeLimits(adapter.id, timeout_s, t_src, ceiling, c_src)


def _row_tokens(row: dict[str, Any]) -> int:
    total = row.get("tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        return total
    n = 0
    for key in ("input_tokens", "output_tokens"):
        v = row.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            n += v
    return n


def tokens_used(data_dir: Path | None, runtime_id: str, *, now: datetime | None = None) -> int:
    """Tokens the usage tracker records for *runtime_id* in the last 24 hours."""
    from scout.scripts.budget_check import _row_ts, iter_tracker_rows

    tracker = paths.logs_dir(data_dir) / "usage-tracker.jsonl"
    if not tracker.exists():
        return 0
    since = (now or datetime.now(UTC)) - CEILING_WINDOW
    total = 0
    for row in iter_tracker_rows(tracker):
        if not isinstance(row, dict) or (row.get("agent_runtime") or "claude-code") != runtime_id:
            continue
        ts = _row_ts(row)
        if ts is None or ts < since:
            continue
        total += _row_tokens(row)
    return total


def over_ceiling(limits: RuntimeLimits, data_dir: Path | None, *, now: datetime | None = None) -> str | None:
    """A reason when the runtime has used its daily tokens, else None."""
    if limits.max_tokens_per_day is None:
        return None
    used = tokens_used(data_dir, limits.runtime, now=now)
    if used >= limits.max_tokens_per_day:
        return (
            f"{limits.runtime} used {used:,} tokens in the last 24 h, at or past its daily ceiling of "
            f"{limits.max_tokens_per_day:,} (budget.per_runtime.{limits.runtime}.max_tokens_per_day)"
        )
    return None


def _json(limits: RuntimeLimits) -> dict[str, Any]:
    return {
        "runtime": limits.runtime,
        "run_timeout_s": limits.run_timeout_s,
        "timeout_source": limits.timeout_source,
        "max_tokens_per_day": limits.max_tokens_per_day,
        "ceiling_source": limits.ceiling_source,
    }


def dumps(limits: RuntimeLimits) -> str:
    return json.dumps(_json(limits), sort_keys=True)


__all__ = [
    "CEILING_WINDOW",
    "DEFAULT_RUN_TIMEOUT_MINUTES",
    "PLACEHOLDER_DAILY_TOKENS",
    "RuntimeLimits",
    "default_daily_token_ceiling",
    "dumps",
    "limits_for",
    "over_ceiling",
    "tokens_used",
]

"""Per-runtime run limits: a wall-clock timeout and a daily token ceiling (spec 2026-11-13 §8.6, Q6).

Both are **on by default for a runtime that can't cap its own spend** (no
``budget_cap`` capability): a 45-minute timeout per run (90 for a research slot), and a daily token ceiling.
Claude Code caps itself (``--max-budget-usd``), so for it both are off unless the
owner sets them, and its runs are unchanged.

``scout-config.yaml``::

    budget:
      per_runtime:
        <runtime id>:
          run_timeout_minutes: 45      # int > 0, or 0 / off for none (default 45; 90 for research)
          max_tokens_per_day: auto     # int > 0, auto (the default formula), or 0 / off

The ceiling counts the usage tracker's rows for that runtime (``agent_runtime``, a
missing key meaning ``claude-code``) over the last 24 hours, summing their
``tokens`` (or ``input_tokens`` + ``output_tokens``). A run that would start past it
is skipped as ``skipped_budget``, like a USD overrun.

**The default ceiling** (``auto``) is Phase 0's formula (spec §8.6; the Phase 0
findings, #650 §3), pending Jordan's OK, so every constant is a module constant::

    ceiling = max(FLOOR, H * sum over this runtime's slots s of T(kind of s))

``T`` is one run's tokens at the 90th percentile over the last 14 days of the usage
tracker. While the runtime has under 7 days of its own rows, it is bootstrapped from
Claude Code's measured runs (``source: runner``, ``cost_source: transcript``) as
``p90(budget_spent) * K``. Rows are grouped by slot kind (dreaming, research,
briefing, consolidation), and a kind without rows uses all of them. A slot counts
one run per day. No rows at all gives ``FLOOR``. No runtime registered in Phase 1
lacks ``budget_cap``, so the ceiling can't skip a run yet.
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
# A slot kind whose runs usually take longer starts higher (spec §8.6, Q6 as amended 2026-11-16: Claude research runs'
# p90 is 111 min).
DEFAULT_TIMEOUT_BY_KIND: dict[str, int] = {"research": 90}
CEILING_WINDOW = timedelta(hours=24)
# The default ceiling's formula (Phase 0, #650 §3). K is derived from USD at list prices, not measured
# (about ±25%), which is why a runtime's own rows replace the bootstrap after OWN_ROWS_AFTER.
K_TOKENS_PER_USD = 1_000_000
H_HEADROOM = 2
FLOOR_TOKENS = 10_000_000
FORMULA_WINDOW = timedelta(days=14)
OWN_ROWS_AFTER = timedelta(days=7)
PCTL = 0.90
SLOT_KINDS = ("dreaming", "research", "briefing", "consolidation")

Source = Literal["config", "default", "off"]


@dataclass(frozen=True)
class RuntimeLimits:
    runtime: str
    run_timeout_s: int | None
    timeout_source: Source
    max_tokens_per_day: int | None
    ceiling_source: Source


def _percentile(values: list[float], q: float = PCTL) -> float:
    """Nearest-rank percentile; 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 1))  # ceil(n * q), at least 1
    return float(ordered[int(rank) - 1])


def kind_of(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    for kind in SLOT_KINDS:
        if kind in text:
            return kind
    return None


def _runtime_slot_kinds(data_dir: Path | None, runtime_id: str) -> list[str]:
    """The slot kind of every scheduled slot that runs on *runtime_id* (its own agent_runtime, else the default)."""
    from scout.agent_runtimes.resolve import default_runtime
    from scout.schedule import load_default_schedule, load_schedule

    vault_schedule = paths.state_dir(data_dir) / "schedule.yaml"
    schedule = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
    fallback = default_runtime(data_dir).runtime
    return [slot.type.value for slot in schedule.values() if (slot.agent_runtime or fallback) == runtime_id]


def default_daily_token_ceiling(data_dir: Path | None, runtime_id: str, *, now: datetime | None = None) -> int:
    """The default daily token ceiling for *runtime_id* (``max_tokens_per_day: auto``): Phase 0's formula.

    Any error (an unreadable schedule or tracker) gives FLOOR, so the ceiling stays on.
    """
    from scout.scripts.budget_check import _row_ts, iter_tracker_rows

    try:
        kinds = _runtime_slot_kinds(data_dir, runtime_id)
        now = now or datetime.now(UTC)
        since = now - FORMULA_WINDOW
        tracker = paths.logs_dir(data_dir) / "usage-tracker.jsonl"
        own: dict[str | None, list[float]] = {}
        boot: dict[str | None, list[float]] = {}
        oldest_own: datetime | None = None
        if tracker.exists():
            for row in iter_tracker_rows(tracker):
                if not isinstance(row, dict):
                    continue
                ts = _row_ts(row)
                if ts is None or ts < since:
                    continue
                kind = kind_of(row.get("type"))
                if row.get("agent_runtime") == runtime_id and runtime_id != "claude-code":
                    if (n := _row_tokens(row)) > 0:
                        own.setdefault(kind, []).append(float(n))
                        oldest_own = ts if oldest_own is None or ts < oldest_own else oldest_own
                elif row.get("source") == "runner" and row.get("cost_source") == "transcript":
                    usd = row.get("budget_spent")
                    if isinstance(usd, int | float) and not isinstance(usd, bool) and usd > 0:
                        boot.setdefault(kind, []).append(float(usd) * K_TOKENS_PER_USD)
        use_own = oldest_own is not None and now - oldest_own >= OWN_ROWS_AFTER
        rows = own if use_own else boot
        everything = [v for values in rows.values() for v in values]
        total = sum(_percentile(rows.get(kind) or everything) for kind in kinds)
        return max(FLOOR_TOKENS, int(H_HEADROOM * total))
    except Exception:  # noqa: BLE001 - the ceiling stays on at its floor
        return FLOOR_TOKENS


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


def slot_kind(data_dir: Path | None, slot: str | None) -> str | None:
    """The kind (dreaming, research, briefing, consolidation) of the run's slot: the schedule's ``type``, else the
    slot key's own words (``research-manual``), else None. Never raises."""
    if not slot:
        return None
    try:
        from scout.schedule import load_default_schedule, load_schedule

        vault_schedule = paths.state_dir(data_dir) / "schedule.yaml"
        schedule = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
        if slot in schedule:
            return schedule[slot].type.value
    except Exception:  # noqa: BLE001 - fall back to the key's words
        pass
    return kind_of(slot)


def default_timeout_minutes(kind: str | None) -> int:
    return DEFAULT_TIMEOUT_BY_KIND.get(kind or "", DEFAULT_RUN_TIMEOUT_MINUTES)


def limits_for(data_dir: Path | None, adapter: AgentRuntime, slot: str | None = None) -> RuntimeLimits:
    """The limits for a run on *adapter*, of *slot* (the slot key, the runners' ``$MODE``) when given.

    The timeout's default depends on the slot's kind (``DEFAULT_TIMEOUT_BY_KIND``); a configured
    ``run_timeout_minutes`` applies to every slot.

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
        timeout_s, t_src = default_timeout_minutes(slot_kind(data_dir, slot)) * 60, "default"

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
    "DEFAULT_TIMEOUT_BY_KIND",
    "default_timeout_minutes",
    "kind_of",
    "slot_kind",
    "FLOOR_TOKENS",
    "H_HEADROOM",
    "K_TOKENS_PER_USD",
    "RuntimeLimits",
    "default_daily_token_ceiling",
    "dumps",
    "limits_for",
    "over_ceiling",
    "tokens_used",
]

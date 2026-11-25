"""Per-runtime run limits: a run timeout in awake time and a daily token ceiling (spec 2026-11-13 §8.6, Q6).

Both are **on by default for a runtime that can't cap its own spend** (no
``budget_cap`` capability): a 45-minute timeout per run (90 for a research slot), and a daily token ceiling.
Claude Code caps itself (``--max-budget-usd``), so for it both are off by default (Jordan, 2026-11-16: its
timeout comes on, at the same 45/90, once the doctor's awake-time measurements say runs fit).

The timeout counts **awake** time (``scout.agent_runtimes.awake``; time the host sleeps doesn't count).

Three layers, per runtime, each a value or ``off`` (``auto`` for the ceiling, meaning the formula below):

1. **The default** above.
2. **The owner's limits**, ``~/.local/state/scout/run-limits.json`` (``scoutctl agent-runtime set-limit``,
   owner only), which a run can't write (the write gate and the OS sandbox protect it). They replace the
   default: this is the only way to raise a limit or turn it off.
3. **scout-config.yaml**, which a run *can* write, so it can only make a limit that is on stricter, and
   never below a floor (``CONFIG_FLOOR_TIMEOUT_MINUTES`` of awake time, ``FLOOR_TOKENS``). It can't turn a
   limit on where the default or the owner has it off (Claude Code's timeout today), so a run can't set
   later runs a tiny limit::

       budget:
         per_runtime:
           <runtime id>:
             run_timeout_minutes: 30     # applies only below the limit above
             max_tokens_per_day: 2000000 # likewise; `auto` applies where it is lower

   A value that would loosen a limit, turn one on, or go below its floor, or isn't a whole number (``0.5``,
   ``"5m"``, ``"²"``), is ignored, and says so in
   ``agent-runtime show``, the run's log and ``scoutctl bootstrap doctor``.

The ceiling counts the usage tracker's **runner rows** for that runtime (``agent_runtime``, a
missing key meaning ``claude-code``) over the last 24 hours, summing their ``tokens``: every token
the model processed in the run, each message once, which ``budget record-run`` measures from the
run's transcripts and their subagents' (only the runner's row carries them, so attempts aren't counted
twice). A row dated in the future (beyond ``CLOCK_SKEW``) is skipped, since it would never age out. A run
that would start past it is skipped as ``skipped_budget``, like a USD overrun.

**The default ceiling** (``auto``) is Phase 0's formula (spec §8.6; the Phase 0
findings, #650 §3), approved by Jordan on 2026-11-16. Every constant is a module constant::

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
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from scout import paths
from scout.agent_runtimes.base import AgentRuntime, caps_own_spend
from scout.agent_runtimes.registry import BUILTIN_DEFAULT

DEFAULT_RUN_TIMEOUT_MINUTES = 45
# A slot kind whose runs usually take longer starts higher (spec §8.6, Q6 as amended 2026-11-16: Claude research runs'
# p90 is 111 min).
DEFAULT_TIMEOUT_BY_KIND: dict[str, int] = {"research": 90}
# The least a scout-config.yaml value may narrow a run timeout to (review of #653): below it, a run that
# writes the config could stop the next runs before they do anything.
CONFIG_FLOOR_TIMEOUT_MINUTES = 30
CEILING_WINDOW = timedelta(hours=24)
# A usage-tracker row dated later than this past now is skipped (review of #654): the tracker isn't protected,
# and a future-dated row would never age out of the window.
CLOCK_SKEW = timedelta(minutes=5)
# The default ceiling's formula (Phase 0, #650 §3). K is derived from USD at list prices, not measured
# (about ±25%), which is why a runtime's own rows replace the bootstrap after OWN_ROWS_AFTER.
K_TOKENS_PER_USD = 1_000_000
H_HEADROOM = 2
FLOOR_TOKENS = 10_000_000
FORMULA_WINDOW = timedelta(days=14)
OWN_ROWS_AFTER = timedelta(days=7)
PCTL = 0.90
SLOT_KINDS = ("dreaming", "research", "briefing", "consolidation")

Source = Literal["config", "default", "off", "owner"]
RUN_LIMITS_SCHEMA = 1


@dataclass(frozen=True)
class RuntimeLimits:
    runtime: str
    run_timeout_s: int | None
    timeout_source: Source
    max_tokens_per_day: int | None
    ceiling_source: Source
    ignored: tuple[str, ...] = ()  # scout-config.yaml values that would have loosened a limit


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
    from scout.scripts.budget_check import iter_tracker_rows, row_ts

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
                ts = row_ts(row)
                if ts is None or ts < since or ts > now + CLOCK_SKEW:
                    continue
                kind = kind_of(row.get("type"))
                if row.get("agent_runtime") == runtime_id and runtime_id != BUILTIN_DEFAULT:
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
    """A whole number above 0, as an int or ASCII digits, else None. Never raises: ``str.isdigit`` alone is
    true for "²" or "①", which ``int()`` refuses (review of #654)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            n = int(text)
            return n if n > 0 else None
    return None


def _auto(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "auto"


SHOWN_MAX_CHARS = 60


def _shown(value: Any, *, quote: bool = False) -> str:
    """A config value as an ``ignored`` entry shows it (in quotes with *quote*, for a string), cut to
    ``SHOWN_MAX_CHARS`` so a huge value a run wrote can't bloat the run log and the doctor (review of #654)."""
    text = repr(value) if quote and isinstance(value, str) else str(value)
    return text if len(text) <= SHOWN_MAX_CHARS else text[: SHOWN_MAX_CHARS - 1] + "…"


def slot_kind(data_dir: Path | None, slot: str | None, run_type: str | None = None) -> str | None:
    """The kind (dreaming, research, briefing, consolidation) of the run: the runner's own ``SCOUT_RUN_TYPE``
    first, then the schedule's ``type`` for the slot, then the slot key's own words (``research-manual``),
    else None. Never raises."""
    # The runner's own run type names the kind only when it is exactly one (run-dreaming and run-research pass
    # "dreaming" / "research"); run-scout passes the slot key, whose words prove nothing (a `research-digest`
    # slot may be `type: briefing`), so the schedule decides those.
    if run_type in SLOT_KINDS:
        return run_type
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


# -- the owner's limits (protected) -----------------------------------------------------------------------------


def run_limits_path(env: Mapping[str, str] | None = None) -> Path:
    from scout.kb.hook_paths import RUN_LIMITS_NAME

    env = os.environ if env is None else env
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "scout" / RUN_LIMITS_NAME


def _vault_key(data_dir: Path | None) -> str:
    vault = paths.data_dir(data_dir)
    try:
        return str(vault.resolve())
    except OSError:
        return str(vault.absolute())


def _read_owner(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": RUN_LIMITS_SCHEMA, "vaults": {}}
    if (
        not isinstance(data, dict)
        or data.get("schema") != RUN_LIMITS_SCHEMA
        or not isinstance(data.get("vaults"), dict)
    ):
        return {"schema": RUN_LIMITS_SCHEMA, "vaults": {}}
    return data


def owner_limits(data_dir: Path | None, runtime_id: str, *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The owner's limits for *runtime_id* on this vault: ``{run_timeout_minutes?, max_tokens_per_day?}``."""
    try:
        entry = _read_owner(run_limits_path(env))["vaults"].get(_vault_key(data_dir), {})
        block = entry.get(runtime_id) if isinstance(entry, dict) else None
    except Exception:  # noqa: BLE001 - an unreadable record means the defaults
        return {}
    return dict(block) if isinstance(block, dict) else {}


def _valid_timeout(value: Any) -> bool:
    return _off(value) or _positive_int(value) is not None


def _valid_ceiling(value: Any) -> bool:
    return _valid_timeout(value) or _auto(value)


def _stored(value: Any) -> Any:
    """A number as an int; ``off`` / ``auto`` as lower-case words."""
    if (n := _positive_int(value)) is not None:
        return n
    if _off(value):
        return "off"
    return str(value).strip().lower()


def set_owner_limit(
    data_dir: Path | None,
    runtime_id: str,
    *,
    run_timeout_minutes: Any = None,
    max_tokens_per_day: Any = None,
    clear: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write the owner's limits for *runtime_id* on this vault; return what is recorded now. Raises ValueError."""
    if run_timeout_minutes is not None and not _valid_timeout(run_timeout_minutes):
        raise ValueError(f"run timeout {run_timeout_minutes!r} is not a number of minutes or off")
    if max_tokens_per_day is not None and not _valid_ceiling(max_tokens_per_day):
        raise ValueError(f"token ceiling {max_tokens_per_day!r} is not a number of tokens, auto or off")
    path = run_limits_path(env)
    data = _read_owner(path)
    vaults = data.setdefault("vaults", {})
    entry = vaults.setdefault(_vault_key(data_dir), {})
    block = {} if clear else dict(entry.get(runtime_id) or {})
    if run_timeout_minutes is not None:
        block["run_timeout_minutes"] = _stored(run_timeout_minutes)
    if max_tokens_per_day is not None:
        block["max_tokens_per_day"] = _stored(max_tokens_per_day)
    if block:
        entry[runtime_id] = block
    else:
        entry.pop(runtime_id, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return block


# -- the three layers -------------------------------------------------------------------------------------------


def limits_for(
    data_dir: Path | None,
    adapter: AgentRuntime,
    slot: str | None = None,
    run_type: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> RuntimeLimits:
    """The limits for a run on *adapter*, of *slot* (the runners' ``$MODE``) and *run_type*
    (``SCOUT_RUN_TYPE``) when given: the default, replaced by the owner's limits, narrowed by
    scout-config.yaml. Never raises: a config value it can't use is listed in ``ignored``, and if the
    config can't be applied at all, the limits are those without it."""
    try:
        return _limits_for(data_dir, adapter, slot, run_type, env=env, use_config=True)
    except Exception as e:  # noqa: BLE001 - a run's start never fails on its limits; the config is dropped
        base = _limits_for(data_dir, adapter, slot, run_type, env=env, use_config=False)
        note = f"budget.per_runtime.{adapter.id} ({type(e).__name__}: could not be applied)"
        return RuntimeLimits(
            base.runtime,
            base.run_timeout_s,
            base.timeout_source,
            base.max_tokens_per_day,
            base.ceiling_source,
            (note,),
        )


def _limits_for(
    data_dir: Path | None,
    adapter: AgentRuntime,
    slot: str | None,
    run_type: str | None,
    *,
    env: Mapping[str, str] | None,
    use_config: bool,
) -> RuntimeLimits:
    self_capped = caps_own_spend(adapter)
    owner = owner_limits(data_dir, adapter.id, env=env)
    config = _block(data_dir, adapter.id) if use_config else {}
    ignored: list[str] = []

    # The timeout: default, then the owner's value, then the config's if stricter.
    timeout_s: int | None
    t_src: Source
    if self_capped:
        timeout_s, t_src = None, "off"
    else:
        timeout_s, t_src = default_timeout_minutes(slot_kind(data_dir, slot, run_type)) * 60, "default"
    raw = owner.get("run_timeout_minutes")
    if raw is not None and _off(raw):
        timeout_s, t_src = None, "owner"
    elif (minutes := _positive_int(raw)) is not None:
        timeout_s, t_src = minutes * 60, "owner"
    raw = config.get("run_timeout_minutes")
    if (minutes := _positive_int(raw)) is not None:
        if timeout_s is None:
            ignored.append(f"run_timeout_minutes: {_shown(minutes)} (the timeout is off; only the owner turns it on)")
        elif minutes < CONFIG_FLOOR_TIMEOUT_MINUTES:
            ignored.append(f"run_timeout_minutes: {minutes} (below the {CONFIG_FLOOR_TIMEOUT_MINUTES}-minute floor)")
        elif minutes * 60 < timeout_s:
            timeout_s, t_src = minutes * 60, "config"
        elif minutes * 60 > timeout_s:
            ignored.append(f"run_timeout_minutes: {_shown(minutes)} (looser than {timeout_s // 60})")
    elif raw is not None and _off(raw):
        if timeout_s is not None:
            ignored.append("run_timeout_minutes: off (only the owner turns it off)")
    elif raw is not None:
        ignored.append(f"run_timeout_minutes: {_shown(raw, quote=True)} (not a whole number of minutes)")

    # The ceiling: likewise.
    ceiling: int | None
    c_src: Source
    if self_capped:
        ceiling, c_src = None, "off"
    else:
        ceiling, c_src = default_daily_token_ceiling(data_dir, adapter.id), "default"
    raw = owner.get("max_tokens_per_day")
    if raw is not None and _off(raw):
        ceiling, c_src = None, "owner"
    elif (n := _positive_int(raw)) is not None:
        ceiling, c_src = n, "owner"
    elif _auto(raw):
        ceiling, c_src = default_daily_token_ceiling(data_dir, adapter.id), "owner"
    raw = config.get("max_tokens_per_day")
    wanted: int | None = None
    if (n := _positive_int(raw)) is not None:
        wanted = n
    elif _auto(raw):
        wanted = default_daily_token_ceiling(data_dir, adapter.id)
    elif raw is not None and _off(raw):
        if ceiling is not None:
            ignored.append("max_tokens_per_day: off (only the owner turns it off)")
    elif raw is not None:
        ignored.append(f"max_tokens_per_day: {_shown(raw, quote=True)} (not a whole number of tokens, auto or off)")
    if wanted is not None:
        if ceiling is None:
            ignored.append(f"max_tokens_per_day: {_shown(raw)} (the ceiling is off; only the owner turns it on)")
        elif wanted < FLOOR_TOKENS:
            ignored.append(f"max_tokens_per_day: {_shown(raw)} (below the {FLOOR_TOKENS:,}-token floor)")
        elif wanted < ceiling:
            ceiling, c_src = wanted, "config"
        elif wanted > ceiling:
            ignored.append(f"max_tokens_per_day: {_shown(raw)} (looser than {ceiling:,})")
    return RuntimeLimits(adapter.id, timeout_s, t_src, ceiling, c_src, tuple(ignored))


def _row_tokens(row: dict[str, Any]) -> int:
    """A runner row's tokens (``budget record-run`` writes ``tokens``, every attempt included). Other rows (the
    session's own, attempt rows) are 0: the runner's row already counts them."""
    if row.get("source") != "runner":
        return 0
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
    from scout.scripts.budget_check import iter_tracker_rows, row_ts

    tracker = paths.logs_dir(data_dir) / "usage-tracker.jsonl"
    if not tracker.exists():
        return 0
    now = now or datetime.now(UTC)
    since, until = now - CEILING_WINDOW, now + CLOCK_SKEW
    total = 0
    for row in iter_tracker_rows(tracker):
        if not isinstance(row, dict) or (row.get("agent_runtime") or BUILTIN_DEFAULT) != runtime_id:
            continue
        ts = row_ts(row)
        if ts is None or ts < since or ts > until:  # a future-dated row would never age out
            continue
        total += _row_tokens(row)
    return total


def future_rows(data_dir: Path | None, *, now: datetime | None = None) -> int:
    """Runner rows in the usage tracker dated later than now (beyond ``CLOCK_SKEW``), which the ceiling and its
    formula skip. Only runner rows: they are all the ceiling and the formula read, so another source's rows (written
    in whatever clock that source uses) could only raise a false alarm.

    The tracker isn't protected, so a run can append one; counted, it would never age out of the 24 hours and
    would skip every later run (review of #654). The doctor warns about them. Never raises.
    """
    from scout.scripts.budget_check import iter_tracker_rows, row_ts

    try:
        tracker = paths.logs_dir(data_dir) / "usage-tracker.jsonl"
        if not tracker.exists():
            return 0
        until = (now or datetime.now(UTC)) + CLOCK_SKEW
        return sum(
            1
            for row in iter_tracker_rows(tracker)
            if isinstance(row, dict) and row.get("source") == "runner" and (ts := row_ts(row)) and ts > until
        )
    except Exception:  # noqa: BLE001 - a count for the doctor only
        return 0


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
    "CONFIG_FLOOR_TIMEOUT_MINUTES",
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
    "owner_limits",
    "run_limits_path",
    "set_owner_limit",
    "over_ceiling",
    "tokens_used",
    "future_rows",
    "CLOCK_SKEW",
]

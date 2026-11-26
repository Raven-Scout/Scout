"""What Scout's own scheduled runs used today and this week: the app's usage card.

The macOS app's "Today's usage" card used to sum a per-session token log on its
own and showed hundreds of billions of tokens a day on a real vault. This module
gives it the engine's figures instead, from ``.scout-logs/usage-tracker.jsonl``:

- **Scope.** Only Scout's scheduled runs count: a ``source: "runner"`` row is one
  finished run, and its ``sessions`` list (written by :mod:`scout.scripts.run_cost`
  since #561) names the Claude Code transcripts that run wrote. ``attempt`` rows
  add the sessions of failed attempts. Interactive sessions, in the vault or
  anywhere else on the Mac, never reach the tracker, so they are not counted. A
  run still in progress has no runner row yet, so it is not counted either.
- **Tokens.** Read from those transcripts only, opened by session id in the
  vault's Claude Code project dirs (plus any ``<id>/subagents/`` transcripts).
  Claude Code writes one row per content block, each repeating its message's
  usage, so rows are deduped by ``message.id`` (the last row of a message wins),
  as :func:`scout.scripts.run_cost._usage_cost` does. Cache reads and cache
  writes are kept apart from input and output.
- **Cost.** The same runs as the tokens: the period's runner rows, plus the
  ``attempt`` rows whose session a runner row lists, counted by
  :func:`scout.scripts.budget_check.window_cost_of_rows` (the budget gate's rule,
  each session once). An attempt of a run still retrying, and ``session`` or
  ``session-tokens`` rows, are left out, so the cost and its ``cost_kind``
  always describe the same runs.
- **How the cost was found** (``cost_kind``): each runner row's ``cost_kind``
  when it carries one (Phase 1 D2: ``reported``, ``priced``, ``unpriced``), else
  derived by D2's rule: ``cost_source: "transcript"`` with any estimated attempt
  is ``priced``, otherwise ``reported`` (Claude Code's own cost-state), and no
  ``cost_source`` is ``unpriced`` (the row's figure is not a measurement). The
  period's kind is ``none`` (no runs), one of those three when every run agrees
  (``priced`` when the rest are ``reported``), else ``partial``.

The JSON shape is a contract with the app (``tests/fixtures/contract/usage-summary.json``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from scout import paths
from scout.config import resolve_timezone
from scout.plural import count
from scout.scripts import budget_check
from scout.scripts.run_cost import SOURCE_ATTEMPT, TRACKER_FILENAME, projects_dir, transcript_dirs

SCHEMA_VERSION = 1
SCOPE = "scheduled-runs"

COST_KINDS = ("reported", "priced", "unpriced")

# Claude Code session ids are UUIDs; anything else in a tracker row is not
# opened, so a hand-edited row can't point the reader outside the project dir.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

_USAGE_KEYS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_read", "cache_read_input_tokens"),
    ("cache_creation", "cache_creation_input_tokens"),
)


@dataclass
class Tokens:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    def add_usage(self, usage: dict[str, Any]) -> None:
        for attr, key in _USAGE_KEYS:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                setattr(self, attr, getattr(self, attr) + value)

    def as_dict(self) -> dict[str, int]:
        return {attr: getattr(self, attr) for attr, _ in _USAGE_KEYS}


@dataclass
class _Period:
    start: datetime
    end: datetime
    rows: list[tuple[datetime, dict]] = field(default_factory=list)


def _transcripts(session_id: str, dirs: list[Path]) -> list[Path]:
    """The session's transcript, and its subagents' transcripts, in the first dir that has it."""
    for d in dirs:
        main = d / f"{session_id}.jsonl"
        if main.is_file():
            found = [main]
            sub = d / session_id / "subagents"
            if sub.is_dir():
                found.extend(sorted(sub.glob("*.jsonl")))
            return found
    return []


def message_usage(paths_: list[Path]) -> dict[str, dict[str, Any]]:
    """Each assistant message's usage across *paths_*, keyed by ``message.id``.

    A message's usage repeats on every content-block row; the last row wins. A
    row without an id is kept on its own (keyed by file and line), as run_cost does.
    """
    turns: dict[str, dict[str, Any]] = {}
    for path in paths_:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f):
                    if '"usage"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = row.get("message") if isinstance(row, dict) else None
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    key = message.get("id")
                    turns[key if isinstance(key, str) and key else f"{path}:{lineno}"] = usage
        except OSError:
            continue
    return turns


def row_cost_kind(row: dict) -> str:
    """How a runner row's cost was found: ``reported``, ``priced`` or ``unpriced``."""
    kind = row.get("cost_kind")
    if isinstance(kind, str) and kind in COST_KINDS:
        return kind
    if row.get("cost_source") == "transcript":
        estimated = row.get("estimated_attempts")
        return "priced" if isinstance(estimated, int) and estimated > 0 else "reported"
    return "unpriced"


def _period_kind(kinds: list[str]) -> str:
    if not kinds:
        return "none"
    seen = set(kinds)
    if seen == {"reported"}:
        return "reported"
    if seen <= {"reported", "priced"}:
        return "priced"
    if seen == {"unpriced"}:
        return "unpriced"
    return "partial"


def _row_sessions(row: dict) -> list[str]:
    if row.get("source") == SOURCE_ATTEMPT:
        sid = row.get("session_id")
        listed = [sid] if isinstance(sid, str) else []
    else:
        raw = row.get("sessions")
        listed = [s for s in raw if isinstance(s, str)] if isinstance(raw, list) else []
    return [s for s in listed if _SESSION_ID_RE.match(s)]


def _run_rows(period: _Period) -> list[tuple[datetime, dict]]:
    """The period's finished runs: runner rows, and the attempt rows they list."""
    runner = [(ts, row) for ts, row in period.rows if row.get("source") == "runner"]
    listed = {sid for _ts, row in runner for sid in _row_sessions(row)}
    attempts = [
        (ts, row)
        for ts, row in period.rows
        if row.get("source") == SOURCE_ATTEMPT and any(sid in listed for sid in _row_sessions(row))
    ]
    return runner + attempts


def _summarize_period(period: _Period, dirs: list[Path]) -> dict[str, Any]:
    runner_rows = [row for _ts, row in period.rows if row.get("source") == "runner"]
    kinds = [row_cost_kind(row) for row in runner_rows]

    # Tokens come from the sessions finished runs list; an attempt row only
    # counts once its runner row lists it, so it adds no session of its own.
    sessions: list[str] = []
    runs_with_tokens = 0
    files: list[Path] = []
    seen: set[str] = set()
    for row in runner_rows:
        found_any = False
        for sid in _row_sessions(row):
            if sid in seen:
                found_any = found_any or sid in sessions
                continue
            seen.add(sid)
            transcripts = _transcripts(sid, dirs)
            if transcripts:
                sessions.append(sid)
                files.extend(transcripts)
                found_any = True
        if found_any:
            runs_with_tokens += 1

    tokens = Tokens()
    for usage in message_usage(files).values():
        tokens.add_usage(usage)

    return {
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "runs": len(runner_rows),
        "runs_with_tokens": runs_with_tokens,
        "sessions": len(sessions),
        "tokens": tokens.as_dict(),
        "cost_usd": round(budget_check.window_cost_of_rows(_run_rows(period)), 4),
        "cost_kind": _period_kind(kinds),
        "priced_runs": sum(1 for k in kinds if k != "unpriced"),
    }


def summarize(vault: Path | None = None, *, now: datetime | None = None, home: Path | None = None) -> dict[str, Any]:
    """Scout's scheduled-run usage for the vault's today and this week (Monday first)."""
    target = vault or paths.data_dir()
    tz = resolve_timezone(target)
    now = now or datetime.now(UTC)
    local_now = now.astimezone(tz)
    day_start = datetime.combine(local_now.date(), time(0), tzinfo=tz)
    day_end = datetime.combine(local_now.date() + timedelta(days=1), time(0), tzinfo=tz)
    week_start = datetime.combine(local_now.date() - timedelta(days=local_now.weekday()), time(0), tzinfo=tz)
    week_end = datetime.combine(week_start.date() + timedelta(days=7), time(0), tzinfo=tz)

    today = _Period(day_start, day_end)
    week = _Period(week_start, week_end)
    tracker = paths.logs_dir(target) / TRACKER_FILENAME
    for row in budget_check.iter_tracker_rows(tracker):
        ts = budget_check.row_ts(row)
        if ts is None:
            continue
        for period in (today, week):
            if period.start <= ts < period.end:
                period.rows.append((ts, row))

    dirs = transcript_dirs(target, projects_dir(home))
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": SCOPE,
        "timezone": str(tz),
        "generated_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": _summarize_period(today, dirs),
        "week": _summarize_period(week, dirs),
    }


def dumps(summary: dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True) + "\n"


def describe(summary: dict[str, Any]) -> str:
    """A few lines for a person: what the card shows."""
    lines = [f"Scout's scheduled runs ({summary['timezone']}):"]
    for label in ("today", "week"):
        p = summary[label]
        t = p["tokens"]
        cost = f"${p['cost_usd']:.2f}"
        if p["cost_kind"] not in ("reported", "priced"):
            cost += f" ({p['cost_kind']})"
        lines.append(
            f"  {label}: {count(p['runs'], 'run')}, tokens from {p['runs_with_tokens']}; "
            f"in {t['input']:,} · out {t['output']:,} · cache read {t['cache_read']:,} · "
            f"cache write {t['cache_creation']:,}; cost {cost}"
        )
    return "\n".join(lines)


__all__ = [
    "COST_KINDS",
    "SCHEMA_VERSION",
    "SCOPE",
    "Tokens",
    "describe",
    "dumps",
    "message_usage",
    "row_cost_kind",
    "summarize",
]

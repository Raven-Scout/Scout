"""What a scheduled run cost, by claude's own figure, recorded by the runner.

A scheduled session can't know its spend: the phases log it with
``write-session-cost.sh ... "${CLAUDE_BUDGET_SPENT_USD:-0}"``, a variable Claude
Code never sets, and the runner's fallback row is $0. So every scheduled run
reached ``usage-tracker.jsonl`` as $0 and the budget gate's window total
stayed at $0.00, however much the runs spent.

The runner knows more once claude has exited. Claude Code writes a
``cost-state`` row (``totalCostUSD``, the figure ``--max-budget-usd`` is checked
against) to the transcript when the process exits, and the transcript carries
the ``--name`` the runner gave the session as a ``custom-title`` row. The retry
wrapper reuses that name for every attempt, so summing the transcripts that
carry it counts a failed attempt's spend too (#561).

An attempt killed before it exits never writes ``cost-state``. Its usage rows
are priced instead, each message once (Claude Code repeats a message's usage on
every content-block row), with the session-tokens hook's rates.

The row this writes is the runner's row, with ``cost_source: "transcript"``,
``run_started`` and the ``sessions`` it measured (each transcript's session id).
:func:`scout.scripts.budget_check.decide` counts it in place of any row written
during the run for one of those sessions, so neither the session's own $0 (or a
guess) nor an attempt row is added on top.

When an attempt fails, ``claude-with-retry.sh`` calls :func:`record_attempt`
first: it writes a ``source: "attempt"`` row for each attempt not yet recorded,
so the attempt's spend reaches the budget gate even if no runner row follows,
and says what the run has left for a retry.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout import paths
from scout.config import resolve_timezone
from scout.hooks.session_tokens import _compute_cost_usd
from scout.scripts import budget_check

COST_SOURCE_TRANSCRIPT = "transcript"
TRACKER_FILENAME = "usage-tracker.jsonl"
SOURCE_ATTEMPT = "attempt"

# How an attempt's spend was found: claude's own cost-state, or its usage rows priced.
PRICED_BY_CLAUDE = "transcript"
PRICED_BY_USAGE = "usage"

# `scoutctl budget record-attempt`'s exit code when no retry should follow.
EXIT_NO_RETRY = 3

# claude checks --max-budget-usd after a turn, so a retry runs at least one, and
# a scheduled run's first turn (system prompt, phase reads, cache write) costs
# far more than a cent. A retry needs this much left: $1, or 10% of the run's cap,
# but never more than the cap itself, so a run with a cap under $1 still retries.
_MIN_RETRY_USD = 1.00
_MIN_RETRY_FRACTION = 0.10


def _retry_floor(cap: float) -> float:
    return min(max(_MIN_RETRY_USD, _MIN_RETRY_FRACTION * cap), cap)


# The same guard write-session-cost.sh puts on SCOUT_MODEL: only a plain model
# name is written.
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


@dataclass(frozen=True)
class AttemptCost:
    session_id: str  # the transcript's file stem, Claude Code's session id
    usd: float
    priced_by: str | None  # PRICED_BY_CLAUDE, PRICED_BY_USAGE, or None: nothing to price


@dataclass(frozen=True)
class RunCost:
    usd: float
    attempts: int  # transcripts that carry the run's name
    measured: int  # of those, the ones with claude's cost-state row
    estimated: int = 0  # the ones priced from their usage rows instead
    sessions: tuple[AttemptCost, ...] = ()

    @property
    def priced(self) -> bool:
        return bool(self.measured or self.estimated)


def projects_dir(home: Path | None = None) -> Path:
    """Where Claude Code keeps transcripts: ``$CLAUDE_CONFIG_DIR/projects``, else ``~/.claude/projects``."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(raw).expanduser() if raw else (home or Path.home()) / ".claude"
    return base / "projects"


def transcript_dirs(vault: Path, projects: Path) -> list[Path]:
    """The project dirs a session run in *vault* writes to.

    Claude Code names the dir after the session's cwd with every character that
    isn't a letter or digit replaced by ``-``. The runner ``cd``s into the vault,
    so both its spelling and its resolved path are tried.
    """
    found: list[Path] = []
    for cwd in (vault, vault.resolve()):
        d = projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
        if d.is_dir() and d not in found:
            found.append(d)
    return found


def _title_and_cost(path: Path) -> tuple[str | None, float | None]:
    """The session's ``custom-title`` and its last ``cost-state`` total, from one transcript.

    Only the lines that can hold either are decoded; a transcript runs to
    megabytes of message rows.
    """
    title: str | None = None
    cost: float | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"custom-title"' not in line and '"cost-state"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("type") == "custom-title" and isinstance(row.get("customTitle"), str):
                    title = row["customTitle"]
                elif row.get("type") == "cost-state":
                    value = row.get("totalCostUSD")
                    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
                        cost = float(value)
    except OSError:
        return None, None
    return title, cost


def _usage_cost(path: Path) -> float:
    """What a transcript's usage rows cost, each message counted once.

    Claude Code writes one row per content block, each repeating its message's
    usage, so rows are deduped by ``message.id`` (the last row of a message wins).
    It is a floor: subagent turns live in their own transcripts and aren't read.
    """
    turns: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = row.get("message") if isinstance(row, dict) else None
                if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                    continue
                key = message.get("id")
                turns[key if isinstance(key, str) and key else f"line-{len(turns)}"] = row
    except OSError:
        return 0.0
    return _compute_cost_usd(list(turns.values()))


def measure(vault: Path, name: str, *, started: datetime, home: Path | None = None) -> RunCost:
    """Sum claude's cost over every transcript named *name* written since *started*.

    A transcript without claude's ``cost-state`` (an attempt killed before exit)
    is priced from its usage rows.
    """
    sessions: list[AttemptCost] = []
    floor = started.timestamp() - 60  # the transcript is created as the session starts
    for d in transcript_dirs(vault, projects_dir(home)):
        for path in d.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < floor:
                    continue
            except OSError:
                continue
            title, cost = _title_and_cost(path)
            if title != name:
                continue
            if cost is not None:
                sessions.append(AttemptCost(path.stem, round(cost, 4), PRICED_BY_CLAUDE))
                continue
            estimate = _usage_cost(path)
            if estimate > 0:
                sessions.append(AttemptCost(path.stem, round(estimate, 4), PRICED_BY_USAGE))
            else:
                sessions.append(AttemptCost(path.stem, 0.0, None))
    return RunCost(
        usd=round(sum(s.usd for s in sessions), 4),
        attempts=len(sessions),
        measured=sum(1 for s in sessions if s.priced_by == PRICED_BY_CLAUDE),
        estimated=sum(1 for s in sessions if s.priced_by == PRICED_BY_USAGE),
        sessions=tuple(sessions),
    )


def tracker_row(
    *,
    run_type: str,
    cost: RunCost,
    cap: float,
    exit_code: int,
    started: datetime,
    now: datetime,
    vault: Path,
    model: str | None,
) -> dict:
    """The runner's usage-tracker row, in write-session-cost.sh's shape plus what was measured."""
    local = now.astimezone(resolve_timezone(vault))
    row: dict = {
        "ts": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_local": local.strftime("%Y-%m-%d %H:%M %Z"),
        "type": run_type,
        "budget_cap": cap,
        "budget_spent": cost.usd,
        "exit_code": exit_code,
        "source": "runner",
    }
    if model and _MODEL_RE.match(model):
        row["model"] = model
    row["run_started"] = _utc(started)
    row["attempts"] = cost.attempts
    if cost.priced:
        # Only a row priced from the transcripts stands in for the session's own row.
        row["cost_source"] = COST_SOURCE_TRANSCRIPT
        row["measured_attempts"] = cost.measured
        if cost.estimated:
            row["estimated_attempts"] = cost.estimated
    if cost.sessions:
        # Every attempt this row accounts for, so their own rows aren't counted again.
        row["sessions"] = [s.session_id for s in cost.sessions]
    return row


def _utc(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(tracker: Path, rows: list[dict]) -> None:
    tracker.parent.mkdir(parents=True, exist_ok=True)
    with tracker.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def record(
    run_type: str,
    *,
    name: str,
    started: datetime,
    cap: float,
    exit_code: int,
    vault: Path | None = None,
    now: datetime | None = None,
    home: Path | None = None,
) -> tuple[dict, RunCost]:
    """Measure the run and append the runner's row to ``usage-tracker.jsonl``."""
    target = vault or paths.data_dir()
    cost = measure(target, name, started=started, home=home)
    row = tracker_row(
        run_type=run_type,
        cost=cost,
        cap=cap,
        exit_code=exit_code,
        started=started,
        now=now or datetime.now(UTC),
        vault=target,
        model=os.environ.get("SCOUT_MODEL"),
    )
    _append(paths.logs_dir(target) / TRACKER_FILENAME, [row])
    return row, cost


@dataclass(frozen=True)
class AttemptOutcome:
    cost: RunCost  # every attempt of the run so far
    recorded: tuple[AttemptCost, ...]  # the attempts this call wrote a row for
    retry: bool
    next_cap: float | None  # the retry's --max-budget-usd; None when the run has no cap
    reason: str


def _recorded_attempts(tracker: Path) -> set[str]:
    return {
        row["session_id"]
        for row in budget_check.iter_tracker_rows(tracker)
        if row.get("source") == SOURCE_ATTEMPT and isinstance(row.get("session_id"), str)
    }


def record_attempt(
    run_type: str,
    *,
    name: str,
    started: datetime,
    cap: float,
    exit_code: int,
    vault: Path | None = None,
    now: datetime | None = None,
    home: Path | None = None,
) -> AttemptOutcome:
    """Record a failed attempt's spend and say whether a retry may follow, with what budget.

    Writes a ``source: "attempt"`` row for each of the run's attempts not yet
    recorded that spent anything. The retry may follow while the run's attempts
    have left some of *cap* and the window is under the skip threshold; its cap
    is what is left, rounded down to the cent.
    """
    target = vault or paths.data_dir()
    now = now or datetime.now(UTC)
    tracker = paths.logs_dir(target) / TRACKER_FILENAME
    cost = measure(target, name, started=started, home=home)

    seen = _recorded_attempts(tracker)
    new = tuple(s for s in cost.sessions if s.usd > 0 and s.session_id not in seen)
    local = now.astimezone(resolve_timezone(target))
    model = os.environ.get("SCOUT_MODEL")
    rows = []
    for attempt in new:
        row: dict = {
            "ts": _utc(now),
            "ts_local": local.strftime("%Y-%m-%d %H:%M %Z"),
            "type": run_type,
            "budget_cap": cap,
            "budget_spent": attempt.usd,
            "exit_code": exit_code,
            "source": SOURCE_ATTEMPT,
            "session_id": attempt.session_id,
            "cost_source": attempt.priced_by,
            "run_started": _utc(started),
        }
        if model and _MODEL_RE.match(model):
            row["model"] = model
        rows.append(row)
    if rows:
        _append(tracker, rows)

    next_cap = None
    if cap > 0:
        # Rounded before the floor, so float error (16.599999…) doesn't cost a cent.
        next_cap = math.floor(round(max(cap - cost.usd, 0.0) * 100, 6)) / 100
        floor = _retry_floor(cap)
        if next_cap < floor:
            return AttemptOutcome(
                cost,
                new,
                False,
                next_cap,
                f"this run's attempts spent ${cost.usd:.2f} of its ${cap:.2f} cap; "
                f"${next_cap:.2f} left, under the ${floor:.2f} a retry needs",
            )

    config = budget_check.load_config(paths.config_path(target))
    spent = budget_check.window_cost(tracker, config, now=now)
    if spent >= config.skip_threshold_usd:
        return AttemptOutcome(
            cost,
            new,
            False,
            next_cap,
            f"window cost ${spent:.2f} >= skip threshold ${config.skip_threshold_usd:.2f}",
        )
    return AttemptOutcome(cost, new, True, next_cap, f"window cost ${spent:.2f}")


__all__ = [
    "COST_SOURCE_TRANSCRIPT",
    "EXIT_NO_RETRY",
    "PRICED_BY_CLAUDE",
    "PRICED_BY_USAGE",
    "SOURCE_ATTEMPT",
    "AttemptCost",
    "AttemptOutcome",
    "RunCost",
    "measure",
    "projects_dir",
    "record",
    "record_attempt",
    "tracker_row",
    "transcript_dirs",
]

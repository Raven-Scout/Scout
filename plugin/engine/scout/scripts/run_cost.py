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

The row this writes is the runner's row, with ``cost_source: "transcript"`` and
``run_started``. :func:`scout.scripts.budget_check.decide` counts it in place of
any ``source: "session"`` row written during the run, so the session's own $0
(or a guess) is never added on top.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout import paths
from scout.config import resolve_timezone

COST_SOURCE_TRANSCRIPT = "transcript"
TRACKER_FILENAME = "usage-tracker.jsonl"

# The same guard write-session-cost.sh puts on SCOUT_MODEL: only a plain model
# name is written.
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


@dataclass(frozen=True)
class RunCost:
    usd: float
    attempts: int  # transcripts that carry the run's name
    measured: int  # of those, the ones with claude's cost-state row


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


def measure(vault: Path, name: str, *, started: datetime, home: Path | None = None) -> RunCost:
    """Sum claude's cost over every transcript named *name* written since *started*."""
    total = 0.0
    attempts = measured = 0
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
            attempts += 1
            if cost is not None:
                measured += 1
                total += cost
    return RunCost(usd=round(total, 4), attempts=attempts, measured=measured)


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
    row["run_started"] = started.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["attempts"] = cost.attempts
    if cost.measured:
        # Only a row that holds claude's figure stands in for the session's own row.
        row["cost_source"] = COST_SOURCE_TRANSCRIPT
        row["measured_attempts"] = cost.measured
    return row


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
    tracker = paths.logs_dir(target) / TRACKER_FILENAME
    tracker.parent.mkdir(parents=True, exist_ok=True)
    with tracker.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row, cost


__all__ = [
    "COST_SOURCE_TRANSCRIPT",
    "RunCost",
    "measure",
    "projects_dir",
    "record",
    "tracker_row",
    "transcript_dirs",
]

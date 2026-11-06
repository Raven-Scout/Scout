"""scout.scripts.run_cost: the runner records what a run cost by claude's own figure.

The transcripts here are synthetic: a ``custom-title`` row (the runner's
``--name``), filler message rows, and claude's ``cost-state`` row at exit.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import budget_check, run_cost

STARTED = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
NAME = "scout-midday-consolidation-20261102-1300"


def _transcript(
    projects: Path, vault: Path, session: str, *, name: str | None, cost: float | None, mtime: datetime | None = None
) -> Path:
    d = projects / str(vault).replace("/", "-").replace(".", "-").replace("_", "-")
    d.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [{"type": "user", "message": {"role": "user", "content": "Your run mode is `x`"}}]
    if name is not None:
        rows.append({"type": "custom-title", "customTitle": name, "sessionId": session})
    rows.append({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "content": []}})
    if name is not None:
        rows.append({"type": "custom-title", "customTitle": name, "sessionId": session})
    if cost is not None:
        rows.append({"type": "cost-state", "sessionId": session, "totalCostUSD": cost, "modelUsage": {}})
    path = d / f"{session}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    when = (mtime or STARTED + timedelta(minutes=15)).timestamp()
    os.utime(path, (when, when))
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    vault = tmp_path / "my_vault.d" / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.delenv("SCOUT_MODEL", raising=False)
    return vault, config / "projects"


def test_one_attempt_is_claudes_own_figure(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=20.139903)
    cost = run_cost.measure(vault, NAME, started=STARTED)
    assert cost == run_cost.RunCost(usd=20.1399, attempts=1, measured=1)


def test_retry_attempts_share_the_name_and_are_summed(env):
    """claude-with-retry.sh reuses the run's --name, so a failed attempt's spend is counted (#561)."""
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=3.25)
    _transcript(projects, vault, "a2", name=NAME, cost=17.5)
    assert run_cost.measure(vault, NAME, started=STARTED) == run_cost.RunCost(usd=20.75, attempts=2, measured=2)


def test_other_sessions_and_older_transcripts_are_not_this_run(env):
    vault, projects = env
    _transcript(projects, vault, "other", name="scout-research-20261102-1400", cost=9.0)
    _transcript(projects, vault, "untitled", name=None, cost=4.0)
    _transcript(projects, vault, "old", name=NAME, cost=19.0, mtime=STARTED - timedelta(days=1))
    assert run_cost.measure(vault, NAME, started=STARTED) == run_cost.RunCost(usd=0.0, attempts=0, measured=0)


def test_an_attempt_killed_before_exit_is_counted_but_not_measured(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=None)
    _transcript(projects, vault, "a2", name=NAME, cost=6.5)
    assert run_cost.measure(vault, NAME, started=STARTED) == run_cost.RunCost(usd=6.5, attempts=2, measured=1)


def test_without_claude_config_dir_transcripts_are_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert run_cost.projects_dir(tmp_path) == tmp_path / ".claude" / "projects"


def test_the_measured_row_stands_in_for_the_session_row(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=20.14)
    now = STARTED + timedelta(minutes=16)
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    # The session's own row, written during the run: $0 (the env var it reads is never set).
    session_row = {
        "ts": "2026-11-02T18:15:00Z",
        "type": "consolidation",
        "budget_cap": 0,
        "budget_spent": 0,
        "exit_code": 0,
        "source": "session",
    }
    tracker.write_text(json.dumps(session_row) + "\n", encoding="utf-8")

    row, _cost = run_cost.record(
        "midday-consolidation", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=now
    )

    assert row["budget_spent"] == 20.14 and row["source"] == "runner" and row["cost_source"] == "transcript"
    assert row["run_started"] == "2026-11-02T18:00:00Z" and row["attempts"] == 1 and row["measured_attempts"] == 1
    lines = tracker.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1]) == row
    decision = budget_check.decide(
        tracker, budget_check.BudgetConfig(daily_budget_usd=1000), now=now + timedelta(hours=2)
    )
    assert "$20.14 spent" in decision.reason


def test_an_unmeasured_run_records_zero_as_before_and_keeps_the_session_row(env):
    vault, _projects = env
    now = STARTED + timedelta(minutes=16)
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    tracker.write_text(
        json.dumps({"ts": "2026-11-02T18:15:00Z", "budget_spent": 7.5, "exit_code": 0, "source": "session"}) + "\n",
        encoding="utf-8",
    )
    row, _cost = run_cost.record("research", name=NAME, started=STARTED, cap=20.0, exit_code=0, vault=vault, now=now)
    assert row["budget_spent"] == 0.0 and "cost_source" not in row and row["attempts"] == 0
    decision = budget_check.decide(
        tracker, budget_check.BudgetConfig(daily_budget_usd=1000), now=now + timedelta(hours=1)
    )
    assert "$7.50 spent" in decision.reason


def test_model_is_written_only_when_plain(env, monkeypatch):
    vault, _projects = env
    monkeypatch.setenv("SCOUT_MODEL", 'opus","x":"y')
    row, _ = run_cost.record("research", name=NAME, started=STARTED, cap=5.0, exit_code=0, vault=vault, now=STARTED)
    assert "model" not in row
    monkeypatch.setenv("SCOUT_MODEL", "claude-opus-5")
    row, _ = run_cost.record("research", name=NAME, started=STARTED, cap=5.0, exit_code=0, vault=vault, now=STARTED)
    assert row["model"] == "claude-opus-5"


def test_cli_record_run(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=18.97)
    result = CliRunner().invoke(
        app,
        [
            "budget",
            "record-run",
            "midday-consolidation",
            "--name",
            NAME,
            "--started",
            str(int(STARTED.timestamp())),
            "--cap",
            "20",
            "--exit-code",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "run cost: $18.97 by claude's figure (1 of 1 attempt(s) measured)" in result.output
    row = json.loads((vault / ".scout-logs" / "usage-tracker.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["type"] == "midday-consolidation" and row["budget_spent"] == 18.97 and row["budget_cap"] == 20.0


def test_cli_record_run_without_a_transcript_says_so(env):
    result = CliRunner().invoke(
        app, ["budget", "record-run", "research", "--name", NAME, "--started", str(int(STARTED.timestamp()))]
    )
    assert result.exit_code == 0, result.output
    assert f"run cost: not measured (0 transcript(s) named {NAME}) — recorded $0" in result.output

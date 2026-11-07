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
from scout.hooks import session_tokens
from scout.scripts import budget_check, run_cost

STARTED = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
NAME = "scout-midday-consolidation-20261102-1300"


def _transcript(
    projects: Path,
    vault: Path,
    session: str,
    *,
    name: str | None,
    cost: float | None,
    mtime: datetime | None = None,
    usage: list[tuple[str, dict]] | None = None,
) -> Path:
    d = projects / str(vault).replace("/", "-").replace(".", "-").replace("_", "-")
    d.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [{"type": "user", "message": {"role": "user", "content": "Your run mode is `x`"}}]
    if name is not None:
        rows.append({"type": "custom-title", "customTitle": name, "sessionId": session})
    rows.append({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "content": []}})
    if name is not None:
        rows.append({"type": "custom-title", "customTitle": name, "sessionId": session})
    # Claude Code writes one row per content block, each repeating its message's usage.
    for message_id, tokens in usage or []:
        rows.append({"type": "assistant", "message": {"id": message_id, "model": "claude-opus-5", "usage": tokens}})
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
    # A window budget far above these runs, so only the run's own cap stops a retry.
    (vault / "scout-config.yaml").write_text("budget:\n  daily_usd: 1000\n", encoding="utf-8")
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.delenv("SCOUT_MODEL", raising=False)
    return vault, config / "projects"


def test_one_attempt_is_claudes_own_figure(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=20.139903)
    cost = run_cost.measure(vault, NAME, started=STARTED)
    assert (cost.usd, cost.attempts, cost.measured) == (20.1399, 1, 1)
    assert cost.sessions == (run_cost.AttemptCost("a1", 20.1399, run_cost.PRICED_BY_CLAUDE),)


def test_retry_attempts_share_the_name_and_are_summed(env):
    """claude-with-retry.sh reuses the run's --name, so a failed attempt's spend is counted (#561)."""
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=3.25)
    _transcript(projects, vault, "a2", name=NAME, cost=17.5)
    cost = run_cost.measure(vault, NAME, started=STARTED)
    assert (cost.usd, cost.attempts, cost.measured) == (20.75, 2, 2)
    assert sorted(s.session_id for s in cost.sessions) == ["a1", "a2"]


def test_other_sessions_and_older_transcripts_are_not_this_run(env):
    vault, projects = env
    _transcript(projects, vault, "other", name="scout-research-20261102-1400", cost=9.0)
    _transcript(projects, vault, "untitled", name=None, cost=4.0)
    _transcript(projects, vault, "old", name=NAME, cost=19.0, mtime=STARTED - timedelta(days=1))
    cost = run_cost.measure(vault, NAME, started=STARTED)
    assert (cost.usd, cost.attempts, cost.measured, cost.sessions) == (0.0, 0, 0, ())


def test_an_attempt_killed_before_exit_is_counted_but_not_measured(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=None)
    _transcript(projects, vault, "a2", name=NAME, cost=6.5)
    cost = run_cost.measure(vault, NAME, started=STARTED)
    assert (cost.usd, cost.attempts, cost.measured, cost.estimated) == (6.5, 2, 1, 0)


def test_without_claude_config_dir_transcripts_are_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert run_cost.projects_dir(tmp_path) == tmp_path / ".claude" / "projects"


def test_the_measured_row_stands_in_for_the_session_row(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=20.14)
    now = STARTED + timedelta(minutes=16)
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    # The session's own row, written during the run: a guess, since the env var it reads is never set.
    session_row = {
        "ts": "2026-11-02T18:15:00Z",
        "type": "consolidation",
        "budget_cap": 0,
        "budget_spent": 19.5,
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


# ----- an attempt claude never priced, and per-attempt rows (#561) ----------

TOKENS = {
    "input_tokens": 1200,
    "output_tokens": 800,
    "cache_read_input_tokens": 90000,
    "cache_creation_input_tokens": 0,
}
TOKENS_2 = {
    "input_tokens": 300,
    "output_tokens": 2500,
    "cache_read_input_tokens": 120000,
    "cache_creation_input_tokens": 0,
}


def _usage_cost(*turns: dict) -> float:
    return session_tokens._compute_cost_usd(
        [{"message": {"model": "claude-opus-5", "usage": tokens}} for tokens in turns]
    )


def _tracker_rows(vault: Path) -> list[dict]:
    path = vault / ".scout-logs" / "usage-tracker.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _decide_later(vault: Path, now: datetime) -> budget_check.BudgetDecision:
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    return budget_check.decide(tracker, budget_check.BudgetConfig(daily_budget_usd=1000), now=now + timedelta(hours=1))


def test_an_attempt_without_claudes_figure_is_priced_from_its_usage_once_per_message(env):
    """A killed attempt never writes cost-state; its usage rows are priced, each message once."""
    vault, projects = env
    # msg_a was written as two content-block rows that repeat its usage; it costs once.
    usage = [("msg_a", TOKENS), ("msg_a", TOKENS), ("msg_b", TOKENS_2)]
    _transcript(projects, vault, "a1", name=NAME, cost=None, usage=usage)
    _transcript(projects, vault, "a2", name=NAME, cost=6.5)
    cost = run_cost.measure(vault, NAME, started=STARTED)
    expected = _usage_cost(TOKENS, TOKENS_2)
    assert expected > 0
    assert (cost.attempts, cost.measured, cost.estimated) == (2, 1, 1)
    assert cost.usd == pytest.approx(round(expected + 6.5, 4))
    by_id = {s.session_id: s for s in cost.sessions}
    assert by_id["a1"].priced_by == run_cost.PRICED_BY_USAGE
    assert by_id["a1"].usd == pytest.approx(round(expected, 4))


def test_the_runner_row_lists_the_sessions_it_measured(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=4.0)
    _transcript(projects, vault, "a2", name=NAME, cost=None, usage=[("msg_a", TOKENS)])
    now = STARTED + timedelta(minutes=20)
    row, _cost = run_cost.record("research", name=NAME, started=STARTED, cap=20.0, exit_code=0, vault=vault, now=now)
    assert sorted(row["sessions"]) == ["a1", "a2"]
    assert row["cost_source"] == "transcript"
    assert row["measured_attempts"] == 1 and row["estimated_attempts"] == 1


def test_a_failed_attempt_is_recorded_before_the_retry(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=8.4)
    now = STARTED + timedelta(minutes=10)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=now
    )
    [row] = _tracker_rows(vault)
    assert row["source"] == "attempt" and row["session_id"] == "a1" and row["budget_spent"] == 8.4
    assert row["exit_code"] == 1 and row["type"] == "research" and row["run_started"] == "2026-11-02T18:00:00Z"
    assert outcome.retry and outcome.next_cap == 11.6
    assert "$8.40 spent" in _decide_later(vault, now).reason


def test_a_failed_attempt_with_no_retry_still_counts(env):
    """The attempt row is the run's only cost row if the runner never writes its own."""
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=None, usage=[("msg_a", TOKENS), ("msg_b", TOKENS_2)])
    now = STARTED + timedelta(minutes=10)
    run_cost.record_attempt("research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=now)
    [row] = _tracker_rows(vault)
    assert row["cost_source"] == run_cost.PRICED_BY_USAGE
    assert f"${_usage_cost(TOKENS, TOKENS_2):.2f} spent" in _decide_later(vault, now).reason


def test_a_second_failure_records_only_the_new_attempt(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=8.4)
    run_cost.record_attempt("research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED)
    _transcript(projects, vault, "a2", name=NAME, cost=3.1)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED
    )
    assert [r["session_id"] for r in _tracker_rows(vault)] == ["a1", "a2"]
    assert outcome.next_cap == 8.5


def test_an_attempt_that_spent_nothing_writes_no_row(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=0.0)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED
    )
    assert _tracker_rows(vault) == [] and outcome.retry and outcome.next_cap == 20.0


def test_no_retry_once_the_runs_cap_is_spent(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=19.995)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED
    )
    assert not outcome.retry and "cap" in outcome.reason


@pytest.mark.parametrize(
    ("cap", "spent", "retry", "next_cap"),
    [
        # claude checks its cap after a turn, so a retry needs enough for one: $1, or 10% of the cap.
        (20.0, 18.5, False, 1.5),  # $1.50 left, under 10% of $20
        (20.0, 17.5, True, 2.5),
        (5.0, 4.2, False, 0.8),  # $0.80 left, under the $1 floor
        (5.0, 3.9, True, 1.1),
        (0.5, 0.0, True, 0.5),  # a cap under $1: the floor is the cap, so a first retry still runs
    ],
)
def test_no_retry_without_enough_left_to_buy_a_turn(env, cap, spent, retry, next_cap):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=spent)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=cap, exit_code=1, vault=vault, now=STARTED
    )
    assert outcome.retry is retry and outcome.next_cap == next_cap
    if not retry:
        assert "a retry needs" in outcome.reason


def test_no_retry_once_the_window_reaches_the_skip_threshold(env):
    vault, projects = env
    # A $6 window budget, skipped at $3.00.
    (vault / "scout-config.yaml").write_text(
        "budget:\n  daily_usd: 48\n  window_hours: 3\n  skip_at_pct: 50\n", encoding="utf-8"
    )
    _transcript(projects, vault, "a1", name=NAME, cost=3.5)
    outcome = run_cost.record_attempt(
        "research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED + timedelta(minutes=10)
    )
    assert not outcome.retry and "skip threshold" in outcome.reason


def test_runner_row_plus_attempt_rows_count_each_attempt_once(env):
    """The attempt row written before the retry, then the runner's row that sums every attempt."""
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=8.4)
    run_cost.record_attempt("research", name=NAME, started=STARTED, cap=20.0, exit_code=1, vault=vault, now=STARTED)
    _transcript(projects, vault, "a2", name=NAME, cost=11.0)
    now = STARTED + timedelta(minutes=30)
    run_cost.record("research", name=NAME, started=STARTED, cap=20.0, exit_code=0, vault=vault, now=now)
    assert "$19.40 spent" in _decide_later(vault, now).reason


def _record_attempt_cli(cap: str) -> list[str]:
    started = str(int(STARTED.timestamp()))
    return ["budget", "record-attempt", "research", "--name", NAME, "--started", started, "--cap", cap] + [
        "--exit-code",
        "1",
    ]


def test_cli_record_attempt_prints_the_next_attempts_cap(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=8.4)
    result = CliRunner().invoke(app, _record_attempt_cli("20"))
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "11.60"
    assert "attempt cost: $8.40" in result.stderr


def test_cli_record_attempt_exits_3_when_no_retry_should_follow(env):
    vault, projects = env
    _transcript(projects, vault, "a1", name=NAME, cost=20.0)
    result = CliRunner().invoke(app, _record_attempt_cli("20"))
    assert result.exit_code == run_cost.EXIT_NO_RETRY == 3
    assert result.stdout.strip() == ""
    assert "no retry" in result.stderr

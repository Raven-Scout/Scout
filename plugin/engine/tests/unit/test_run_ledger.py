"""`scoutctl runs --json` and scout.run_ledger: the run ledger as the app reads it."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import health, run_ledger
from scout.cli import app

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
runner = CliRunner()


def _row(minutes_ago: int, **extra) -> dict:
    finished = int((NOW - timedelta(minutes=minutes_ago)).timestamp())
    row = {
        "slot": "morning-briefing",
        "mode": "scout",
        "started_at": finished - 120,
        "finished_at": finished,
        "duration_s": 120,
        "exit_code": 0,
        "failure_class": "ok",
        "outcome": "ok",
        "log": f"scout-{minutes_ago}.log",
    }
    row.update(extra)
    return row


def _vault(tmp_path: Path, rows: Sequence[dict | str]) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    lines = [r if isinstance(r, str) else json.dumps(r) for r in rows]
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return v


@pytest.fixture(autouse=True)
def _quiet_health(monkeypatch):
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    monkeypatch.setattr(health, "recent_commits", lambda *a, **k: [])


def test_rows_parse_every_phase1_field(tmp_path):
    v = _vault(
        tmp_path,
        [
            _row(
                5,
                awake_s=100,
                agent_runtime="claude-code",
                model="sonnet",
                runner="scout",
                trigger="schedule",
                sandbox="enforced",
                integrity="clean",
            )
        ],
    )
    (row,) = health.read_outcomes(v)
    assert (row.duration_s, row.awake_s, row.agent_runtime, row.model, row.runner, row.trigger) == (
        120,
        100,
        "claude-code",
        "sonnet",
        "scout",
        "schedule",
    )


def test_odd_values_never_reach_a_client(tmp_path):
    v = _vault(
        tmp_path,
        [
            _row(
                5,
                awake_s=-3,
                agent_runtime="Claude Code; rm -rf",
                model="a model with spaces",
                runner="evil",
                trigger="cron",
                sandbox="maybe",
                integrity="fine",
                failure_class="Some Note Text",
                log="/Users/alex/Scout/projects/secret.md",
                newest_commit="not-a-sha",
                slot="morning briefing: notes/secret",
            )
        ],
    )
    (run,) = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")["runs"]
    for key in ("awake_s", "agent_runtime", "model", "runner", "trigger", "sandbox", "integrity", "failure_class"):
        assert run[key] is None, key
    assert run["log"] is None and run["newest_commit"] is None and run["slot"] == "unknown"


def test_log_is_a_basename_only(tmp_path):
    v = _vault(tmp_path, [_row(5, log="sub/dir/scout-2026-11-16_08-00.log")])
    (run,) = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")["runs"]
    assert run["log"] == "scout-2026-11-16_08-00.log"


def test_newest_first_since_and_limit(tmp_path):
    v = _vault(tmp_path, [_row(60 * 24 * 10), _row(90), _row(30), "not json", "{}", _row(10)])
    doc = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")
    assert [r["log"] for r in doc["runs"]] == ["scout-10.log", "scout-30.log", "scout-90.log"]
    assert doc["truncated"] is False and doc["since"] == "2026-11-09T12:00:00Z"
    doc = run_ledger.ledger_report(v, now=NOW, since=NOW - timedelta(hours=1), limit=1, home=tmp_path / "home")
    assert [r["log"] for r in doc["runs"]] == ["scout-10.log"] and doc["truncated"] is True


def test_every_key_is_always_present(tmp_path):
    v = _vault(tmp_path, [_row(5)])
    (run,) = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")["runs"]
    assert set(run) == {
        "slot", "mode", "runner", "trigger", "started_at", "finished_at", "duration_s", "awake_s", "exit_code",
        "outcome", "failure_class", "resets_at", "sandbox", "sandbox_source", "integrity", "agent_runtime",
        "model", "log", "commits_in_window", "newest_commit",
    }  # fmt: skip


def test_skips_and_timeouts_keep_their_reason(tmp_path):
    v = _vault(
        tmp_path,
        [
            _row(40, outcome="skipped_gates", failure_class="gates_unsafe_endpoint"),
            _row(30, outcome="skipped_sandbox", failure_class="ok"),
            _row(20, outcome="failed", exit_code=124, failure_class="timeout", awake_s=5400),
            _row(10, outcome="skipped_lease", failure_class="lease_held"),
        ],
    )
    runs = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")["runs"]
    assert [(r["outcome"], r["failure_class"]) for r in runs] == [
        ("skipped_lease", "lease_held"),
        ("failed", "timeout"),
        ("skipped_sandbox", "ok"),
        ("skipped_gates", "gates_unsafe_endpoint"),
    ]


def test_health_is_the_same_report_as_scoutctl_health(tmp_path):
    v = _vault(tmp_path, [_row(5, exit_code=1, outcome="failed", failure_class="unknown")])
    doc = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")
    assert doc["health"] == health.evaluate(v, now=NOW, home=tmp_path / "home").to_dict()
    assert doc["schema_version"] == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", NOW - timedelta(days=7)),
        ("48h", NOW - timedelta(hours=48)),
        ("90m", NOW - timedelta(minutes=90)),
        ("2026-11-15T08:00:00Z", datetime(2026, 11, 15, 8, tzinfo=UTC)),
        ("2026-11-15T08:00:00", datetime(2026, 11, 15, 8, tzinfo=UTC)),
    ],
)
def test_parse_since(text, expected):
    assert run_ledger.parse_since(text, now=NOW) == expected


def test_parse_since_rejects_junk():
    with pytest.raises(ValueError):
        run_ledger.parse_since("last tuesday", now=NOW)


def test_cli_json_exits_zero_even_when_health_is_red(tmp_path, monkeypatch):
    rows = [_row(m, exit_code=1, outcome="failed", failure_class="unknown") for m in (5, 15, 25)]
    v = _vault(tmp_path, rows)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    result = runner.invoke(app, ["runs", "--json", "--since", "3650d"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["health"]["severity"] == "red" and len(doc["runs"]) == 3


def test_cli_text_lists_runs(tmp_path, monkeypatch):
    v = _vault(tmp_path, [_row(5, sandbox="off", sandbox_source="switch")])
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    result = runner.invoke(app, ["runs", "--since", "3650d"])
    assert result.exit_code == 0, result.output
    assert "morning-briefing" in result.stdout and "sandbox=off" in result.stdout


def test_cli_bad_since_and_missing_vault_exit_2(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "nope"))
    assert runner.invoke(app, ["runs", "--since", "soon"]).exit_code == 2
    result = runner.invoke(app, ["runs", "--json"])
    assert result.exit_code == 2 and "error" in json.loads(result.stdout)

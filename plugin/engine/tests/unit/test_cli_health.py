"""CLI tests for `scoutctl health [--json]`."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _fail_rows(v: Path, count: int = 2) -> None:
    now = int(time.time())
    rows = [
        {
            "slot": "morning-briefing",
            "mode": "scout",
            "started_at": now - 600 * (i + 1),
            "finished_at": now - 600 * (i + 1) + 60,
            "duration_s": 60,
            "exit_code": 1,
            "failure_class": "unknown",
            "log": f"scout-{i}.log",
        }
        for i in range(count)
    ]
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_health_green_text_exits_zero(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0, result.output
    assert "run health: green" in result.stdout


def test_health_red_json(tmp_path, monkeypatch):
    _fail_rows(_vault(tmp_path, monkeypatch))
    result = runner.invoke(app, ["health", "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["severity"] == "red"


def test_health_red_text_lists_finding_and_fix(tmp_path, monkeypatch):
    _fail_rows(_vault(tmp_path, monkeypatch))
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 2, result.output
    assert "run health: red" in result.stdout
    assert "red  runs.failing:" in result.stdout
    assert "    fix: " in result.stdout


def test_health_text_quotes_the_error_line_and_the_log(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    now = int(time.time())
    row = {
        "slot": "morning-briefing",
        "mode": "scout",
        "started_at": now - 600,
        "finished_at": now - 540,
        "exit_code": 1,
        "failure_class": "network",
        "log": "scout-net.log",
    }
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text(json.dumps(row) + "\n")
    (v / ".scout-logs" / "scout-net.log").write_text("API Error: Connection error (ECONNRESET)\n")

    result = runner.invoke(app, ["health"])

    assert "    error: API Error: Connection error (ECONNRESET)\n" in result.stdout
    assert f"    log: {v / '.scout-logs' / 'scout-net.log'}\n" in result.stdout


def test_health_yellow_text_exits_one(tmp_path, monkeypatch):
    """M7: one failed run is yellow, exit 1."""
    _fail_rows(_vault(tmp_path, monkeypatch), count=1)
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 1, result.output
    assert "run health: yellow" in result.stdout
    assert "yellow  runs.failing:" in result.stdout


def test_health_missing_vault_text_is_unknown_exit_two(tmp_path, monkeypatch):
    """M4: a missing vault directory is not "green"."""
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(missing))
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert result.stderr.strip() == f"run health: unknown — vault directory missing: {missing}"


def test_health_missing_vault_json_is_unknown_exit_two(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(missing))
    result = runner.invoke(app, ["health", "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout) == {"severity": "unknown", "error": f"vault directory missing: {missing}"}
    assert "run health: unknown — vault directory missing:" in result.stderr

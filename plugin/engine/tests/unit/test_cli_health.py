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


def _fail_rows(v: Path) -> None:
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
        for i in range(2)
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

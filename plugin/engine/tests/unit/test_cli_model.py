"""`scoutctl model resolve|show|set-default` and the `model` field in `schedule list/show`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.manifest import build_manifest

runner = CliRunner()

SCHEDULE = """\
schema_version: 1
slots:
  midday-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "13:00"
    weekdays: [Mon]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90
    model: sonnet
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    (v / "scout-config.yaml").write_text("# my notes\nuser:\n  name: Alex\n", encoding="utf-8")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.delenv("SCOUT_MODEL", raising=False)
    return v


def test_resolve_prints_the_slot_model(vault: Path) -> None:
    result = runner.invoke(app, ["model", "resolve", "--slot", "midday-consolidation"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "sonnet\n"


def test_resolve_without_slot_prints_the_default(vault: Path) -> None:
    result = runner.invoke(app, ["model", "resolve"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "opus\n"


def test_resolve_json_names_the_source(vault: Path) -> None:
    result = runner.invoke(app, ["model", "resolve", "--slot", "morning-briefing", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"model": "opus", "source": "builtin"}


def test_resolve_honours_the_env_override(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODEL", "haiku")
    result = runner.invoke(app, ["model", "resolve", "--slot", "midday-consolidation", "--json"])
    assert json.loads(result.stdout) == {"model": "haiku", "source": "env"}


def test_resolve_fails_loudly_on_a_bad_schedule(vault: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text(SCHEDULE.replace("model: sonnet", "model: sonet"))
    result = runner.invoke(app, ["model", "resolve", "--slot", "morning-briefing"])
    assert result.exit_code == 1
    assert result.stdout == ""  # the runner treats empty stdout as "fall back to opus"
    assert "midday-consolidation" in result.stderr


def test_show_json_lists_default_and_every_slot(vault: Path) -> None:
    result = runner.invoke(app, ["model", "show", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "default": {"model": "opus", "source": "builtin"},
        "slots": [
            {"key": "midday-consolidation", "model": "sonnet", "source": "slot"},
            {"key": "morning-briefing", "model": "opus", "source": "builtin"},
        ],
        "aliases": ["opus", "sonnet", "haiku"],
    }


def test_show_text_is_readable(vault: Path) -> None:
    result = runner.invoke(app, ["model", "show"])
    assert result.exit_code == 0, result.output
    assert "default: opus (built-in)" in result.stdout
    assert "midday-consolidation" in result.stdout and "sonnet" in result.stdout


def test_set_default_writes_config_and_keeps_comments(vault: Path) -> None:
    result = runner.invoke(app, ["model", "set-default", "Sonnet", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["default"] == "sonnet"
    assert payload["changed"] is True
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert text.startswith("# my notes\nuser:\n  name: Alex\n")
    shown = json.loads(runner.invoke(app, ["model", "show", "--json"]).stdout)
    assert shown["default"] == {"model": "sonnet", "source": "config"}
    assert shown["slots"][1] == {"key": "morning-briefing", "model": "sonnet", "source": "config"}


def test_set_default_rejects_a_bad_model(vault: Path) -> None:
    before = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    result = runner.invoke(app, ["model", "set-default", "sonet"])
    assert result.exit_code == 1
    assert "sonet" in result.stderr
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == before


def test_schedule_list_and_show_carry_model(vault: Path) -> None:
    records = json.loads(runner.invoke(app, ["schedule", "list", "--json"]).stdout)
    assert {r["key"]: r["model"] for r in records} == {"midday-consolidation": "sonnet", "morning-briefing": None}
    shown = json.loads(runner.invoke(app, ["schedule", "show", "midday-consolidation"]).stdout)
    assert shown["model"] == "sonnet"


def test_manifest_advertises_slot_model() -> None:
    # Scout.app shows its model pickers only on engines that advertise this.
    assert build_manifest().features["slot_model_v1"] is True

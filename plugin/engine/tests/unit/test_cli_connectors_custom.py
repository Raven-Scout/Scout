"""CLI surface for `scoutctl connectors custom …` and `connectors presets`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.bootstrap import BootstrapConfig, install

runner = CliRunner()
PLUGIN = Path(__file__).parent.parent.parent.parent
DEF = """\
key: suite_mail
display_name: Mail suite
server: example_suite
probe: mcp__example_suite__list_folders
preset: mail
inbound:
  tools: [mcp__example_suite__search_messages]
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors=set(),
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_add_from_stdin_prints_json_and_exits_0(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input=DEF)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "applied"


def test_add_invalid_exits_2_with_issues(vault: Path, tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(DEF.replace("server: example_suite", "server: other"))
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", str(bad)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["issues"][0]["path"] == "connectors.suite_mail.server"


def test_add_unparseable_file_exits_2(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input="key: [unclosed\n")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "invalid"


def test_malformed_input_flag_exits_2(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-", "--input", "novalue"], input=DEF)
    assert result.exit_code == 2


def test_validate_list_remove_round_trip(vault: Path):
    assert runner.invoke(app, ["connectors", "custom", "validate", "--file", "-"], input=DEF).exit_code == 0
    runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input=DEF)
    listing = json.loads(runner.invoke(app, ["connectors", "custom", "list"]).stdout)
    assert [c["key"] for c in listing["connectors"]] == ["suite_mail"]
    removed = runner.invoke(app, ["connectors", "custom", "remove", "suite_mail"])
    assert removed.exit_code == 0 and json.loads(removed.stdout)["status"] == "applied"


def test_presets_command_prints_json():
    result = runner.invoke(app, ["connectors", "presets"])
    assert result.exit_code == 0
    assert "mail" in json.loads(result.stdout)["presets"]

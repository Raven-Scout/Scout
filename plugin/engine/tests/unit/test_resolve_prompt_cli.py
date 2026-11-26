"""CLI tests for `scoutctl health resolve-prompt KIND KEY [--json]`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.manifest import build_manifest

runner = CliRunner()
KEYS = {"schema_version", "kind", "key", "current", "title", "cwd", "prompt"}
LOG = "scout-2026-11-19_17-03.log"


def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    (v / ".scout-logs" / LOG).write_text("=== Scout run starting ===\n", encoding="utf-8")
    alerts = {
        "schema_version": 1,
        "generated_at": "2026-11-19T17:09:00Z",
        "mode": "morning-briefing",
        "alerts": [
            {
                "connector_key": "mcp:example_tracker",
                "level": "WARNING",
                "name": "Example tracker",
                "reason": "1/2 calls errored this run (50%)",
                "ts": "2026-11-19T17:09:00Z",
                "first_seen": "2026-11-19T17:09:00Z",
            }
        ],
    }
    (v / ".scout-logs" / "connector-alerts.json").write_text(json.dumps(alerts), encoding="utf-8")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_json_shape(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health", "resolve-prompt", "connector", "mcp:example_tracker", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert set(doc) == KEYS
    assert doc["schema_version"] == 1 and doc["kind"] == "connector" and doc["key"] == "mcp:example_tracker"
    assert doc["current"] is True and Path(doc["cwd"]) == v


def test_plain_prints_prompt_only(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health", "resolve-prompt", "run", LOG])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("# ")
    assert "## Guardrails" in result.stdout and not result.stdout.lstrip().startswith("{")


@pytest.mark.parametrize(
    ("kind", "key"),
    [("run", "../x.log"), ("run", "run/../../etc.log"), ("finding", "a b"), ("bogus", "x"), ("run", "absent.log")],
)
def test_bad_keys_exit_2(tmp_path, monkeypatch, kind, key):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health", "resolve-prompt", kind, key, "--json"])
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["schema_version"] == 1 and doc["error"]


def test_missing_vault_exit_2(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "nowhere"))
    result = runner.invoke(app, ["health", "resolve-prompt", "run", LOG, "--json"])
    assert result.exit_code == 2
    assert "vault directory missing" in json.loads(result.stdout)["error"]


def test_manifest_advertises_resolve_prompt():
    assert build_manifest().features["resolve_prompt_v1"] is True

"""CLI tests for `scoutctl connectors probe-registry`."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def _overlay(data_dir: Path, body: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "connector-probes.local.yaml").write_text(dedent(body))


def test_probe_registry_json_lists_shipped_connectors():
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    # Shipped registry ships these (templates/connector-probes.yaml).
    assert "slack" in data
    assert "github" in data
    assert data["slack"]["kind"] == "mcp_tool"
    assert data["github"]["kind"] == "bash"


def test_probe_registry_json_includes_overlay(tmp_path, monkeypatch):
    """A vault overlay adds a connector the wizard will then probe (#97)."""
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        devin:
          primary: mcp__devin__devin_session_search
          fallbacks: []
        """,
    )
    # SCOUT_DATA_DIR steers resolve_registry's default data_dir at the
    # overlay; the shipped half comes from the real repo templates/.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert "devin" in data
    assert data["devin"]["tool_chain"] == ["mcp__devin__devin_session_search"]
    assert "slack" in data  # shipped still present


def test_probe_registry_default_is_tab_separated():
    result = runner.invoke(app, ["connectors", "probe-registry"])
    assert result.exit_code == 0, result.stdout + result.stderr
    first = next(line for line in result.stdout.splitlines() if line.strip())
    assert not first.startswith("{")  # not JSON
    parts = first.split("\t")
    assert len(parts) == 3
    assert parts[1] in ("bash", "mcp_tool")


def test_detect_json_is_unknown_for_mcp_probes_when_claude_is_missing(monkeypatch):
    """A claude binary that does not exist must degrade to `unknown`, never crash.

    The bash probes (``claude_sessions``, ``github``) still run for real, so
    ``run_bash_probe`` is faked deterministically here: it must not shell out
    to the real `gh` CLI (whose auth state varies by machine/CI). The fake
    targets ``scout.scripts.connector_detect.run_bash_probe`` — the CLI
    imports that name inside the command body at call time, so patching the
    module attribute is what actually takes effect.
    """
    import scout.scripts.connector_detect as connector_detect

    def fake_bash_probe(command: str) -> int:
        return 0 if command.startswith("test -d") else 1

    monkeypatch.setattr(connector_detect, "run_bash_probe", fake_bash_probe)

    result = runner.invoke(app, ["connectors", "detect", "--json", "--claude-bin", "/nonexistent/claude"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["slack"]["status"] == "unknown"
    assert data["claude_sessions"]["status"] == "connected"
    assert data["github"]["status"] == "unavailable"
    assert set(data["github"]) == {"status", "needs_user_input", "evidence"}


def test_detect_text_mode_prints_tab_separated_lines():
    """No --json: one tab-separated `name\\tstatus\\tevidence` line per connector."""
    result = runner.invoke(app, ["connectors", "detect", "--claude-bin", "/nonexistent/claude"])
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert any(line.startswith("slack\tunknown\t") for line in lines)
    for line in lines:
        assert not line.startswith("{")
        assert len(line.split("\t", 2)) == 3

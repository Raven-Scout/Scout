"""connectors uncovered: connected MCP servers that no connector reads (spec §4.1)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import connector_uncovered as cu
from scout.scripts.connector_probes import Probe, ProbeKind

MCP_LIST = """\
Checking MCP server health...

claude.ai Example Suite: https://mcp.example.com/suite - ✔ Connected
claude.ai Example Calendar: https://mcp.example.com/cal - ✔ Connected
claude.ai Example CRM: https://mcp.example.com/crm - ! Needs authentication
plugin:example-kit:search-tool: npx example-kit - ✘ Failed to connect
plugin:playwright:playwright: npx @playwright/mcp - ✔ Connected
Claude_Browser: builtin - ✔ Connected
ccd_session: builtin - ✔ Connected
custom_tickets: https://mcp.example.com/tix - ✔ Connected
[mcp-sdk] warning: something noisy
"""

REGISTRY = {
    "calendar": Probe("calendar", ProbeKind.MCP_TOOL, tool_chain=["mcp__claude_ai_Example_Calendar__list_calendars"]),
    "tickets": Probe(
        "tickets", ProbeKind.MCP_TOOL, tool_chain=["mcp__custom_tickets__whoami"]
    ),  # a custom connector's probe
    "github": Probe("github", ProbeKind.BASH, bash_command="gh auth status"),
}


def test_lists_only_uncovered_work_servers_sorted():
    out = cu.find_uncovered(MCP_LIST, registry=REGISTRY)
    assert out["error"] is None and out["schema_version"] == 1
    assert [s["name"] for s in out["servers"]] == [
        "claude.ai Example CRM",
        "claude.ai Example Suite",
        "plugin:example-kit:search-tool",
    ]
    suite = next(s for s in out["servers"] if s["name"] == "claude.ai Example Suite")
    assert suite == {
        "name": "claude.ai Example Suite",
        "slug": "claude_ai_Example_Suite",
        "status": "connected",
        "evidence": "claude.ai Example Suite: https://mcp.example.com/suite - ✔ Connected",
    }
    statuses = {s["name"]: s["status"] for s in out["servers"]}
    assert statuses["claude.ai Example CRM"] == "needs_auth"
    assert statuses["plugin:example-kit:search-tool"] == "unavailable"


def test_a_failed_mcp_list_is_an_error_never_an_empty_success():
    out = cu.find_uncovered(None, registry=REGISTRY)
    assert out["servers"] == []
    assert out["error"] and "claude mcp list" in out["error"]


def test_hyphenated_plugin_server_is_covered_by_its_tool_slug():
    reg = {"kit": Probe("kit", ProbeKind.MCP_TOOL, tool_chain=["mcp__plugin_example-kit_search-tool__find"])}
    out = cu.find_uncovered(MCP_LIST, registry=reg)
    assert "plugin:example-kit:search-tool" not in [s["name"] for s in out["servers"]]


def test_tooling_names():
    for name in ("Claude_Browser", "plugin:playwright:playwright", "ccd_session", "claude-in-chrome", "Terminal"):
        assert cu.is_tooling(name), name
    for name in ("claude.ai Example Suite", "claude.ai Gmail", "plugin:linear:linear"):
        assert not cu.is_tooling(name), name


def test_covered_server_keys_skips_a_tool_chain_entry_with_no_server_slug():
    # "bash" is not an `mcp__<server>__<tool>` name, so tool_server_slug
    # returns None for it; covered_server_keys must skip it rather than
    # crash, while still picking up the chain's other, well-formed entry.
    reg = {
        "mixed": Probe(
            "mixed",
            ProbeKind.MCP_TOOL,
            tool_chain=["bash", "mcp__custom_tickets__whoami"],
        )
    }
    assert cu.covered_server_keys(reg) == {"custom_tickets"}


def test_cli_emits_json(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: MCP_LIST)
    monkeypatch.setattr("scout.scripts.connector_probes.resolve_registry", lambda **k: REGISTRY)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--json", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 0, result.output
    assert [s["slug"] for s in json.loads(result.output)["servers"]][0] == "claude_ai_Example_CRM"


def test_cli_text_output_lists_name_and_status(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: MCP_LIST)
    monkeypatch.setattr("scout.scripts.connector_probes.resolve_registry", lambda **k: REGISTRY)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 0, result.output
    assert "claude.ai Example CRM\tneeds_auth" in result.output
    assert "claude.ai Example Suite\tconnected" in result.output
    assert "plugin:example-kit:search-tool\tunavailable" in result.output


def test_cli_exits_1_when_listing_failed(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--json", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]


def test_cli_text_mode_error_goes_to_stderr(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: None)
    monkeypatch.setattr("scout.scripts.connector_probes.resolve_registry", lambda **k: REGISTRY)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 1
    assert "error:" in result.output and "claude mcp list" in result.output

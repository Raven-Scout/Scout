"""restrict_tools: the custom-connector field, tool_gate(), and the PreToolUse hook that enforces it."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc
from scout.hooks import mcp_tool_gate as gate_hook

PLUGIN = Path(__file__).parent.parent.parent.parent
T = "mcp__knowledge__"

READER = {
    "display_name": "Company knowledge",
    "server": "knowledge",
    "probe": f"{T}whoami",
    "restrict_tools": True,
    "lookup": {
        "tools": [f"{T}knowledge_search", f"{T}document_read"],
        "when": "Before researching a company topic, search the company knowledge base.",
    },
}


def _parse(body: object) -> tuple[cc.CustomConnector | None, list[cc.Issue]]:
    return cc.parse_connector("company_kb", body, reserved=set(), presets={})


def _write(vault: Path, connectors: dict) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / cc.CUSTOM_FILE).write_text(yaml.safe_dump({"schema_version": 1, "connectors": connectors}))


# --- the field ----------------------------------------------------------------


def test_restrict_tools_parses_and_defaults_off():
    c, issues = _parse(READER)
    assert issues == [] and c is not None and c.restrict_tools is True
    c, issues = _parse({k: v for k, v in READER.items() if k != "restrict_tools"})
    assert issues == [] and c is not None and c.restrict_tools is False


@pytest.mark.parametrize("value", ["yes", 1, None, ["true"]])
def test_restrict_tools_must_be_a_bool(value):
    c, issues = _parse({**READER, "restrict_tools": value})
    if value is None:  # YAML null reads as absent
        assert issues == [] and c is not None and c.restrict_tools is False
        return
    assert c is None
    assert [i.path for i in issues] == ["connectors.company_kb.restrict_tools"]


def test_restrict_tools_needs_an_mcp_server():
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "restrict_tools": True,
        "lookup": {"tools": [{"bash": "tix show"}], "when": "Look up a ticket."},
    }
    c, issues = _parse(body)
    assert c is None
    assert "needs an MCP server" in issues[0].message


def test_restrict_tools_survives_a_write_and_load_round_trip(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    cc.write(vault, {"company_kb": READER})
    loaded = cc.load(vault, plugin_root=PLUGIN)
    assert loaded.issues == []
    assert loaded.connectors["company_kb"].restrict_tools is True


# --- tool_gate ------------------------------------------------------------------


def test_gate_lists_probe_and_activity_tools_of_restricted_entries(tmp_path):
    other = {
        "display_name": "Mail",
        "server": "suite",
        "probe": "mcp__suite__list_folders",
        "inbound": {"tools": ["mcp__suite__search"], "focus": "New mail."},
    }
    _write(tmp_path, {"company_kb": READER, "mail": other})
    assert cc.tool_gate(tmp_path) == {
        "knowledge": frozenset({f"{T}whoami", f"{T}knowledge_search", f"{T}document_read"})
    }


def test_no_file_or_no_restricted_entry_means_no_gate(tmp_path):
    assert cc.tool_gate(tmp_path) == {}
    _write(tmp_path, {"company_kb": {**READER, "restrict_tools": False}})
    assert cc.tool_gate(tmp_path) == {}
    _write(tmp_path, {"company_kb": {k: v for k, v in READER.items() if k != "restrict_tools"}})
    assert cc.tool_gate(tmp_path) == {}


def test_invalid_restricted_entry_stays_gated(tmp_path):
    # `load` drops this entry (no display_name), but its restriction must not lift.
    body = {k: v for k, v in READER.items() if k != "display_name"}
    _write(tmp_path, {"company_kb": {**body, "restrict_tools": "yes"}})
    assert cc.load(tmp_path, plugin_root=PLUGIN).connectors == {}
    assert set(cc.tool_gate(tmp_path)) == {"knowledge"}


def test_restricted_entry_naming_no_server_gates_everything(tmp_path):
    _write(tmp_path, {"broken": {"restrict_tools": True, "probe": "not a tool"}})
    assert cc.tool_gate(tmp_path) == {cc.GATE_ALL: frozenset()}


def test_non_yaml_file_gates_every_server_named_in_it(tmp_path):
    (tmp_path / cc.CUSTOM_FILE).write_text(
        "connectors:\n  company_kb:\n    server: knowledge\n    restrict_tools: true\n"
        "    probe: mcp__knowledge__whoami\n    lookup: [unclosed\n"
    )
    assert cc.tool_gate(tmp_path) == {"knowledge": frozenset()}


@pytest.mark.parametrize("text", ["restrict_tools: true\n: : [", "- restrict_tools\n- x\n"])
def test_unparseable_or_odd_shape_without_servers_gates_everything(tmp_path, text):
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    assert cc.tool_gate(tmp_path) == {cc.GATE_ALL: frozenset()}


def test_unreadable_file_gates_everything(tmp_path):
    (tmp_path / cc.CUSTOM_FILE).write_bytes(b"\xff\xfe restrict_tools")
    assert cc.tool_gate(tmp_path) == {cc.GATE_ALL: frozenset()}


# --- decide -----------------------------------------------------------------------

GATE = {"knowledge": frozenset({f"{T}whoami", f"{T}knowledge_search"})}


def test_listed_tool_and_other_servers_pass():
    assert gate_hook.decide(f"{T}knowledge_search", GATE) is None
    assert gate_hook.decide("mcp__suite__send_message", GATE) is None
    assert gate_hook.decide("Bash", GATE) is None


@pytest.mark.parametrize("tool", [f"{T}remember", f"{T}post_to_conversation", "mcp__Knowledge__remember"])
def test_unlisted_tool_of_restricted_server_is_denied(tool):
    reason = gate_hook.decide(tool, GATE)
    assert reason is not None and tool in reason and f"{T}knowledge_search" in reason


def test_gate_all_denies_every_mcp_tool():
    assert gate_hook.decide("mcp__suite__search", {cc.GATE_ALL: frozenset()}) is not None
    assert gate_hook.decide("Read", {cc.GATE_ALL: frozenset()}) is None


# --- run / main -------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    _write(v, {"company_kb": READER})
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv("SCOUT_MODE", "research")
    return v


def _payload(tool: str) -> io.StringIO:
    return io.StringIO(json.dumps({"tool_name": tool, "tool_input": {}, "session_id": "s1"}))


def test_run_denies_an_unlisted_tool(vault):
    out = gate_hook.run(stdin=_payload(f"{T}remember"))
    assert out is not None
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"


def test_run_allows_a_listed_tool(vault):
    assert gate_hook.run(stdin=_payload(f"{T}knowledge_search")) is None


def test_run_is_silent_outside_scheduled_runs(vault, monkeypatch):
    monkeypatch.delenv("SCOUT_MODE")
    assert gate_hook.run(stdin=_payload(f"{T}remember")) is None


@pytest.mark.parametrize("raw", ["not json", "[]", '{"tool_name": 3}', '{"tool_name": "Read"}'])
def test_run_ignores_non_mcp_or_malformed_payloads(vault, raw):
    assert gate_hook.run(stdin=io.StringIO(raw)) is None


def test_run_fails_closed_when_the_gate_errors(vault, monkeypatch):
    def boom(_vault):
        raise RuntimeError("bug")

    monkeypatch.setattr(gate_hook, "tool_gate", boom)
    out = gate_hook.run(stdin=_payload("mcp__suite__search"))
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_prints_the_decision(vault, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _payload(f"{T}remember"))
    assert gate_hook.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", _payload(f"{T}whoami"))
    assert gate_hook.main() == 0
    assert capsys.readouterr().out == ""


# --- hooks.json wiring --------------------------------------------------------------


def _pre_tool_use_command() -> str:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if h.get("matcher") == "mcp__.*"]
    (cmd,) = entry["hooks"]
    return cmd["command"]


def test_hooks_json_runs_the_gate_on_mcp_calls():
    assert 'scoutctl" hook mcp-tool-gate' in _pre_tool_use_command()


def test_hook_command_skips_python_outside_scheduled_runs(tmp_path):
    # With SCOUT_MODE unset the shell guard exits before exec — CLAUDE_PLUGIN_ROOT points nowhere.
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_MODE"}
    env["CLAUDE_PLUGIN_ROOT"] = str(tmp_path / "missing")
    res = subprocess.run(["sh", "-c", _pre_tool_use_command()], env=env, input="{}", capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout == ""


@pytest.mark.parametrize("custom_file", [None, "schema_version: 1\nconnectors: {}\n"])
def test_hook_command_skips_python_when_no_connector_restricts(tmp_path, custom_file):
    v = tmp_path / "vault"
    v.mkdir()
    if custom_file is not None:
        (v / cc.CUSTOM_FILE).write_text(custom_file)
    env = {**os.environ, "SCOUT_MODE": "research", "SCOUT_DATA_DIR": str(v)}
    env["CLAUDE_PLUGIN_ROOT"] = str(tmp_path / "missing")
    res = subprocess.run(["sh", "-c", _pre_tool_use_command()], env=env, input="{}", capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout == ""


def test_hook_command_denies_through_the_real_cli(vault):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    res = subprocess.run(
        ["sh", "-c", _pre_tool_use_command()],
        env=env,
        input=json.dumps({"tool_name": f"{T}remember"}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

"""The engine-side gate on a model's connector drafts (spec §4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import connector_draft as cd

PLUGIN = Path(__file__).parent.parent.parent.parent
SERVER = "claude.ai Example Suite"
T = "mcp__claude_ai_Example_Suite__"


def _mail(**over):
    d = {
        "key": "suite_mail",
        "display_name": "Mail suite",
        "probe": f"{T}list_folders",
        "preset": "mail",
        "inbound": {"tools": [f"{T}search_messages", f"{T}get_message"]},
    }
    d.update(over)
    return d


@pytest.mark.parametrize(
    "tool",
    [f"{T}send_message", f"{T}sendMessage", f"{T}create-issue", f"{T}mark_read", f"{T}archive_thread", f"{T}Delete"],
)
def test_write_tools_are_detected(tool):
    assert cd.is_write_tool(tool)


@pytest.mark.parametrize("tool", [f"{T}list_labels", f"{T}get_message", f"{T}search_threads", f"{T}listFolders"])
def test_read_tools_pass(tool):
    assert not cd.is_write_tool(tool)


def test_a_valid_draft_comes_back_with_the_server_set():
    defs, issues = cd.check_definitions([_mail()], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert issues == []
    assert defs[0]["server"] == "claude_ai_Example_Suite"
    assert defs[0]["key"] == "suite_mail"


def test_a_write_tool_rejects_the_whole_draft():
    bad = _mail(inbound={"tools": [f"{T}search_messages", f"{T}send_message"]})
    defs, issues = cd.check_definitions([bad], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == []
    assert any("send_message" in i.message for i in issues)


def test_a_tool_of_another_server_is_rejected():
    bad = _mail(inbound={"tools": ["mcp__claude_ai_Other__search"]})
    defs, issues = cd.check_definitions([bad], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == [] and any("not a tool of" in i.message for i in issues)


def test_hyphenated_plugin_server_tools_are_accepted():
    t = "mcp__plugin_example-kit_search-tool__"
    d = {
        "key": "kit_search",
        "display_name": "Kit",
        "probe": f"{t}whoami",
        "lookup": {"tools": [f"{t}find"], "when": "When a question names a kit item."},
    }
    defs, issues = cd.check_definitions(
        [d], server_name="plugin:example-kit:search-tool", plugin_root=PLUGIN, taken=set()
    )
    assert issues == [] and defs[0]["server"] == "plugin_example-kit_search-tool"


def test_bash_tools_and_missing_keys_are_rejected():
    bad = _mail(probe={"bash": "suite whoami"})
    _, issues = cd.check_definitions([bad, {"display_name": "x"}], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    paths = [i.path for i in issues]
    assert any(p.startswith("connectors.suite_mail") for p in paths)
    assert "definitions[1]" in paths


def test_a_taken_key_and_a_duplicate_key_are_rejected():
    _, issues = cd.check_definitions([_mail()], server_name=SERVER, plugin_root=PLUGIN, taken={"suite_mail"})
    assert issues
    _, issues = cd.check_definitions([_mail(), _mail()], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert issues


def test_an_empty_draft_is_an_issue():
    defs, issues = cd.check_definitions([], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == [] and issues


def test_a_draft_with_no_valid_tool_refs_leaves_server_unset():
    bad = _mail(probe="mcp__claude_ai_Other__whoami", inbound={"tools": ["mcp__claude_ai_Other__search"]})
    defs, issues = cd.check_definitions([bad], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == [] and issues


def test_schema_lists_the_shipped_presets():
    schema = cd.draft_schema(["calendar", "chat", "mail"])
    preset = schema["properties"]["definitions"]["items"]["properties"]["preset"]
    assert preset["enum"] == ["calendar", "chat", "mail"]

"""Custom connectors in the health roster, the probe registry, and the connector-log hook."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from scout import custom_connectors as cc
from scout.cli import app
from scout.connectors import Tier, load_registry
from scout.hooks.connector_log import classify
from scout.scripts.connector_detect import DetectStatus, detect
from scout.scripts.connector_probes import ProbeKind, resolve_registry
from scout.scripts.connectors_snapshot import build_snapshot

runner = CliRunner()


def _enable(vault: Path, *keys: str) -> None:
    """Minimal scout-config.yaml enabling the given custom connector keys."""
    (vault / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": {"enabled": list(keys)}}))


DEFS = {
    "suite_mail": {
        "display_name": "Mail suite",
        "server": "example_suite",
        "probe": "mcp__example_suite__list_folders",
        "preset": "mail",
        "inbound": {"tools": ["mcp__example_suite__search_messages"]},
        "required_in_types": ["briefing"],
    },
    "suite_chat": {
        "display_name": "Chat suite",
        "server": "example_suite",
        "probe": "mcp__example_suite__list_chats",
        "preset": "chat",
        "inbound": {"tools": ["mcp__example_suite__search_chats"]},
    },
    "tickets": {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
    },
    "webhook": {
        "display_name": "Webhook feed",
        "probe": {"bash": "curl -sf https://example.com/health"},
        "inbound": {"tools": [{"bash": "curl -s https://example.com/items"}], "focus": "New items."},
    },
}


def test_roster_gets_one_custom_row_per_server_and_one_per_bash_connector(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    reg = load_registry()
    row = reg["mcp:example_suite"]
    assert row.tier is Tier.CUSTOM
    assert row.display_name == "Chat suite, Mail suite"
    assert [t.value for t in row.required_in_types] == ["briefing"]
    assert reg["tickets"].tier is Tier.CUSTOM
    assert len(reg["tickets"].remediation.first_fix) <= 180


def test_custom_rows_name_only_commands_that_exist(fake_data_dir: Path):
    """F6: /scout-connect is not shipped yet; remediation must name the real CLI."""
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    reg = load_registry()
    for key in ("tickets", "mcp:example_suite"):
        remediation = reg[key].remediation
        assert "/scout-connect" not in remediation.first_fix + remediation.detail
        assert "scoutctl connectors custom add" in remediation.detail
        assert len(remediation.first_fix) <= 180
    assert "scoutctl connectors custom add --file" in reg["tickets"].remediation.first_fix


def test_remediation_for_a_generic_probe_binary_names_the_connector_instead(fake_data_dir: Path):
    """Task 4: `webhook`'s probe runs through `curl`, a generic binary that works for
    everyone — telling the user to "check that curl runs" doesn't diagnose anything.
    The honest fix names the connector instead."""
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, "webhook")
    reg = load_registry()
    first_fix = reg["webhook"].remediation.first_fix
    assert "Webhook feed" in first_fix
    assert "`curl`" not in first_fix
    assert "scoutctl connectors custom add --file" in first_fix
    assert len(first_fix) <= 180


def test_shipped_rows_win_and_broken_file_yields_no_custom_rows(fake_data_dir: Path):
    (fake_data_dir / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    reg = load_registry()
    assert reg["mcp:claude_ai_Slack"].tier is Tier.OFFICIAL
    assert not any(reg[k].tier is Tier.CUSTOM for k in reg.keys())


def test_snapshot_never_contains_custom_rows(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    keys = {row["key"] for row in build_snapshot()["connectors"]}
    assert "mcp:example_suite" not in keys and "tickets" not in keys


def test_probe_registry_includes_custom_probes(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    reg = resolve_registry(data_dir=fake_data_dir)
    assert reg["suite_mail"].kind is ProbeKind.MCP_TOOL
    assert reg["suite_mail"].tool_chain == ["mcp__example_suite__list_folders"]
    assert reg["tickets"].kind is ProbeKind.BASH
    assert reg["tickets"].bash_command == "tix whoami"
    assert reg["slack"].kind is ProbeKind.MCP_TOOL  # shipped untouched


def test_probe_registry_survives_a_broken_custom_file(fake_data_dir: Path):
    (fake_data_dir / cc.CUSTOM_FILE).write_text(":::\n")
    assert "slack" in resolve_registry(data_dir=fake_data_dir)


def test_connectors_detect_covers_custom_connectors(fake_data_dir: Path):
    """Scout.app onboarding (`connectors detect`) sees custom connectors with no extra code."""
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    dets = detect(resolve_registry(data_dir=fake_data_dir), mcp_list_output=None, run_bash=lambda cmd: 0)
    assert dets["tickets"].status is DetectStatus.CONNECTED
    assert dets["suite_mail"].status is DetectStatus.UNKNOWN  # no `claude mcp list` output to match against


def test_detect_evidence_includes_custom_probe_command(fake_data_dir: Path):
    """A custom bash probe's command appears in `connectors detect`'s evidence string
    (already true of any bash probe; this just pins it for a custom one, task-3 brief)."""
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, "tickets")
    dets = detect(resolve_registry(data_dir=fake_data_dir), mcp_list_output=None, run_bash=lambda cmd: 0)
    assert "tix whoami" in dets["tickets"].evidence


def test_hook_labels_custom_binaries_but_not_generic_ones(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    assert classify("Bash", {"command": "cd ~/Scout && tix list"}) == "tickets"
    assert classify("Bash", {"command": "curl -s https://example.com/items"}) == "bash:curl"
    assert classify("Bash", {"command": "gh pr list"}) == "github"
    assert classify("mcp__example_suite__search_messages", {}) == "mcp:example_suite"


def test_connectors_list_json_includes_custom_rows(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, *DEFS)
    result = runner.invoke(app, ["connectors", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = {r["key"]: r for r in json.loads(result.stdout)["connectors"]}
    assert rows["mcp:example_suite"]["tier"] == "custom"
    assert rows["mcp:claude_ai_Slack"]["tier"] == "official"


def test_disabled_custom_connector_is_absent_from_roster_and_probes(fake_data_dir: Path):
    """Task 3: health roster and probes follow connectors.enabled."""
    cc.write(fake_data_dir, DEFS)
    _enable(fake_data_dir, "tickets")  # webhook defined but never enabled
    reg = load_registry()
    assert reg["tickets"].tier is Tier.CUSTOM
    assert "webhook" not in reg.keys()
    probes = resolve_registry(data_dir=fake_data_dir)
    assert "tickets" in probes
    assert "webhook" not in probes


def test_missing_scout_config_yields_no_custom_rows_but_keeps_shipped(fake_data_dir: Path):
    """Fail closed (global-constraints #3): no scout-config.yaml at all → no custom
    rows/probes, but the shipped roster and probe registry are untouched."""
    cc.write(fake_data_dir, DEFS)
    assert not (fake_data_dir / "scout-config.yaml").exists()
    reg = load_registry()
    assert reg["mcp:claude_ai_Slack"].tier is Tier.OFFICIAL
    assert not any(reg[k].tier is Tier.CUSTOM for k in reg.keys())
    probes = resolve_registry(data_dir=fake_data_dir)
    assert "slack" in probes
    assert "tickets" not in probes and "suite_mail" not in probes


def test_unreadable_scout_config_yields_no_custom_rows_but_keeps_shipped(fake_data_dir: Path):
    """Fail closed (global-constraints #3): a malformed scout-config.yaml behaves
    the same as a missing one — no custom rows/probes, shipped rows survive."""
    cc.write(fake_data_dir, DEFS)
    (fake_data_dir / "scout-config.yaml").write_text("connectors: [unclosed\n")
    reg = load_registry()
    assert reg["mcp:claude_ai_Slack"].tier is Tier.OFFICIAL
    assert not any(reg[k].tier is Tier.CUSTOM for k in reg.keys())
    probes = resolve_registry(data_dir=fake_data_dir)
    assert "slack" in probes
    assert "tickets" not in probes and "suite_mail" not in probes

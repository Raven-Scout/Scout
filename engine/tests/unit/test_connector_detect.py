"""Unit tests for engine/scout/scripts/connector_detect.py (spec E4)."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.connector_detect import (
    DetectStatus,
    detect,
    parse_mcp_list,
    run_bash_probe,
    run_claude_mcp_list,
    server_slug,
    to_json_dict,
    tool_server_slug,
)
from scout.scripts.connector_probes import Probe, ProbeKind

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "claude-mcp-list.txt").read_text(encoding="utf-8")


def test_parse_mcp_list_reads_one_status_per_server():
    servers = parse_mcp_list(FIXTURE)
    assert servers["claude.ai Gmail"][0] is DetectStatus.CONNECTED
    assert servers["claude.ai Slack"][0] is DetectStatus.NEEDS_AUTH
    assert servers["Some Internal Tool"][0] is DetectStatus.UNAVAILABLE
    assert "Checking MCP server health…" not in servers
    assert len(servers) == 5


def test_server_slug_matches_claude_codes_tool_namespace():
    assert server_slug("claude.ai Google Calendar") == "claude_ai_Google_Calendar"
    assert server_slug("claude.ai Gmail") == "claude_ai_Gmail"
    assert server_slug("fathom") == "fathom"


def test_tool_server_slug_extracts_the_middle_segment():
    assert tool_server_slug("mcp__claude_ai_Gmail__list_labels") == "claude_ai_Gmail"
    assert tool_server_slug("mcp__plugin_slack_slack__slack_read_user_profile") == "plugin_slack_slack"
    assert tool_server_slug("bash") is None


def _mcp(name: str, chain: list[str], needs: list[str] | None = None) -> Probe:
    return Probe(name=name, kind=ProbeKind.MCP_TOOL, tool_chain=chain, needs_user_input=needs or [])


def test_detect_maps_mcp_probes_to_server_status():
    reg = {
        "email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"]),
        "slack": _mcp(
            "slack",
            ["mcp__plugin_slack_slack__slack_read_user_profile", "mcp__claude_ai_Slack__slack_read_user_profile"],
            ["user_slack_id"],
        ),
        "fathom": _mcp("fathom", ["mcp__fathom__list_meetings"]),
    }
    dets = detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1)
    assert dets["email"].status is DetectStatus.CONNECTED
    assert dets["slack"].status is DetectStatus.NEEDS_AUTH  # fallback tool matched the claude.ai server
    assert dets["slack"].needs_user_input == ["user_slack_id"]
    assert dets["fathom"].status is DetectStatus.UNKNOWN  # no such server listed


def test_detect_runs_bash_probes_directly():
    reg = {
        "github": Probe(
            name="github", kind=ProbeKind.BASH, bash_command="gh auth status", needs_user_input=["github_username"]
        )
    }
    calls: list[str] = []

    def fake_bash(cmd: str) -> int:
        calls.append(cmd)
        return 0

    dets = detect(reg, mcp_list_output=None, run_bash=fake_bash)
    assert calls == ["gh auth status"]
    assert dets["github"].status is DetectStatus.CONNECTED
    assert dets["github"].evidence == "`gh auth status` exit 0"


def test_detect_is_unknown_not_unavailable_when_mcp_list_failed():
    reg = {"email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"])}
    dets = detect(reg, mcp_list_output=None, run_bash=lambda cmd: 1)
    assert dets["email"].status is DetectStatus.UNKNOWN
    assert "unavailable" in dets["email"].evidence


def test_to_json_dict_shape():
    reg = {"email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"])}
    payload = to_json_dict(detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1))
    assert payload == {
        "email": {
            "status": "connected",
            "needs_user_input": [],
            "evidence": "claude.ai Gmail: https://mcp.example.invalid/gmail - ✔ Connected",
        }
    }


def test_detect_tie_break_first_connected_in_chain_wins_over_earlier_needs_auth():
    """slack's fixture entry is NEEDS_AUTH; put a CONNECTED server later in the
    chain and confirm detect() prefers it over the earlier NEEDS_AUTH match,
    per the controller's tie-break rule (first CONNECTED wins; otherwise
    first match)."""
    reg = {
        "combo": _mcp(
            "combo",
            [
                "mcp__claude_ai_Slack__slack_read_user_profile",  # NEEDS_AUTH (fixture)
                "mcp__claude_ai_Gmail__list_labels",  # CONNECTED (fixture) — later, but wins
            ],
        ),
    }
    dets = detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1)
    assert dets["combo"].status is DetectStatus.CONNECTED


def test_detect_tie_break_first_match_wins_when_neither_is_connected():
    """Two non-CONNECTED hits in a chain: the first one found stays `best`
    (covers the branch where `best is not None` and the later hit is not
    CONNECTED, so the update on line 111 is skipped)."""
    reg = {
        "combo": _mcp(
            "combo",
            [
                "mcp__claude_ai_Slack__slack_read_user_profile",  # NEEDS_AUTH (fixture) — first, wins
                "mcp__Some_Internal_Tool__probe",  # UNAVAILABLE (fixture) — later, ignored
            ],
        ),
    }
    dets = detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1)
    assert dets["combo"].status is DetectStatus.NEEDS_AUTH


def test_detect_ignores_a_malformed_mcp_tool_name_in_the_chain():
    """A tool_chain entry shaped like ``mcp____x`` starts with ``mcp__`` but,
    once that prefix is stripped, rpartitions to an empty server slug — an
    edge case in ``_match_server``'s tool_server_slug(tool) guard. It should
    be skipped (no match), falling through to the next, well-formed entry."""
    reg = {
        "weird": _mcp("weird", ["mcp____x", "mcp__claude_ai_Gmail__list_labels"]),
    }
    dets = detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1)
    assert dets["weird"].status is DetectStatus.CONNECTED


def test_run_claude_mcp_list_returns_stdout_on_success(tmp_path: Path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho 'hello mcp list'\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    assert run_claude_mcp_list(str(fake)) == "hello mcp list\n"


def test_run_claude_mcp_list_returns_none_on_nonzero_exit(tmp_path: Path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    assert run_claude_mcp_list(str(fake)) is None


def test_run_claude_mcp_list_returns_none_on_timeout(tmp_path: Path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    fake.chmod(0o755)
    assert run_claude_mcp_list(str(fake), timeout=0.2) is None


def test_run_claude_mcp_list_returns_none_when_binary_missing():
    assert run_claude_mcp_list("/nonexistent/claude-binary-xyz") is None


def test_run_bash_probe_returns_exit_code_for_true():
    assert run_bash_probe("true") == 0


def test_run_bash_probe_returns_exit_code_for_false():
    assert run_bash_probe("false") != 0


def test_run_bash_probe_returns_one_on_timeout():
    assert run_bash_probe("sleep 5", timeout=0.2) == 1

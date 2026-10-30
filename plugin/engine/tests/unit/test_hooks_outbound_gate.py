"""outbound-gate: check-outbound on a Slack / connector write tool's own input (hard-rule audit row 2b, #453).

Over the privacy fixture vault: ``task-renew-permit`` and ``sam`` (alias ``Sammy``) are private notes,
so a message naming them may not reach a shared surface. Runs are denied, the owner is warned (#504
option B; #517: any reader but exactly ``owner`` is a run).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import outbound_gate as gate
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}
SEND = "mcp__claude_ai_Slack__slack_send_message"
PRIVATE = "Scout briefing ready.\n- Remember task-renew-permit before Friday."
CLEAN = "Scout briefing ready.\n- Review the PROJ-1234 rollout with Priya."


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("outbound-gate"))


@pytest.fixture
def vault(shared_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    for log in (shared_vault / ".scout-logs").glob("*.jsonl"):
        log.unlink()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(shared_vault))
    return shared_vault


def _payload(tool: str, tool_input: dict, cwd: Path) -> io.StringIO:
    return io.StringIO(json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}))


def _decide(vault: Path, tool: str, tool_input: dict, env=RUN):
    return gate.run(stdin=_payload(tool, tool_input, vault), env=env)


def _reason(out) -> str:
    assert out is not None, "expected a deny"
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith(gate.MARK)
    return spec["permissionDecisionReason"]


def _no_leak(text: str) -> None:
    for term in ("renew", "permit", "sammy"):
        assert term not in text.lower()


def _rows(vault: Path) -> list[dict]:
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


# --- which tools ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "surface"),
    [
        (SEND, "slack"),
        ("mcp__claude_ai_Slack__slack_send_message_draft", "slack"),
        ("mcp__claude_ai_Slack__slack_schedule_message", "slack"),
        ("mcp__plugin_slack_slack__slack_update_canvas", "slack"),
        ("mcp__slack__slack_create_list", "slack"),
        ("mcp__slack__slack_add_list_record", "slack"),
        ("mcp__claude_ai_Gmail__create_draft", "connector_write"),
        ("mcp__claude_ai_Linear__save_comment", "connector_write"),
        ("mcp__linear__linear_create_issue", "connector_write"),
        ("mcp__calendar__gcal_update_event", "connector_write"),
    ],
)
def test_write_tools_map_to_their_shared_surface(tool, surface):
    assert gate.surface_for(tool) == surface


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__claude_ai_Slack__slack_read_channel",
        "mcp__claude_ai_Slack__slack_search_public_and_private",
        "mcp__claude_ai_Slack__slack_get_file_upload_url",
        "mcp__claude_ai_Linear__list_issues",
        "mcp__claude_ai_Gmail__get_thread",
        "mcp__gcal__gcal_list_events",
        "Bash",
        "mcp__bare",
    ],
)
def test_reads_and_non_mcp_tools_are_not_gated(tool):
    assert gate.surface_for(tool) is None


# --- scheduled runs: blocked ---------------------------------------------------------------


def test_run_blocks_a_slack_send_naming_a_private_note(vault):
    reason = _reason(_decide(vault, SEND, {"channel_id": "U0123456789", "message": PRIVATE}))
    assert "`message` line 2" in reason and "slack" in reason
    _no_leak(reason)
    (row,) = _rows(vault)
    assert row["blocked"] is True and row["command"] == "hook outbound-gate" and row["surface"] == "slack"
    events = "".join(p.read_text() for p in (vault / ".scout-logs").glob("schedule-events-*.jsonl"))
    assert '"privacy.outbound_blocked"' in events and "renew" not in events


def test_run_checks_every_string_field_nested_too(vault):
    tool_input = {"channel": "C0123456789", "blocks": [{"type": "section", "text": {"text": "See [[sam]]."}}]}
    reason = _reason(_decide(vault, "mcp__claude_ai_Slack__slack_send_message_draft", tool_input))
    assert "`blocks.0.text.text` line 1" in reason


def test_run_blocks_a_connector_write(vault):
    reason = _reason(_decide(vault, "mcp__claude_ai_Linear__save_comment", {"body": "Ask Sammy first."}))
    assert "connector_write" in reason


def test_run_blocks_a_scheduled_slack_message(vault):
    _reason(_decide(vault, "mcp__claude_ai_Slack__slack_schedule_message", {"text": PRIVATE, "post_at": 1}))


# --- allowed -------------------------------------------------------------------------------


def test_run_lets_a_clean_send_through(vault):
    assert _decide(vault, SEND, {"channel_id": "U0123456789", "message": CLEAN}) is None
    assert _rows(vault) == []


def test_reads_never_start_the_check(vault, monkeypatch):
    monkeypatch.setattr(gate, "flagged_fields", lambda *a: pytest.fail("a read was checked"))
    assert _decide(vault, "mcp__claude_ai_Slack__slack_search_public", {"query": PRIVATE}) is None


def test_owner_only_surface_overlay_lets_it_through(vault, monkeypatch):
    # A vault that made Slack owner-only (policy overlay) has nothing to hold back there.
    monkeypatch.setattr(gate, "flagged_fields", lambda v, i, s: {})
    assert _decide(vault, SEND, {"message": PRIVATE}) is None


# --- the owner's own session: warned, never blocked -----------------------------------------


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}])
def test_owner_is_warned_not_blocked(vault, env):
    out = _decide(vault, SEND, {"message": PRIVATE}, env=env)
    assert out is not None
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert out["systemMessage"].startswith(gate.MARK) and "Privacy warning" in out["systemMessage"]
    assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"]
    _no_leak(out["systemMessage"])
    (row,) = _rows(vault)
    assert row["blocked"] is False and row["event"] == "privacy.outbound_warned" and row["reader"] == "owner"


def test_owner_clean_send_is_silent(vault):
    assert _decide(vault, SEND, {"message": CLEAN}, env={}) is None


@pytest.mark.parametrize("env", [{"SCOUT_READER": ""}, {"SCOUT_READER": "Owner"}, {"SCOUT_READER": "agent:group:ops"}])
def test_any_reader_but_exactly_owner_is_blocked(vault, env):
    _reason(_decide(vault, SEND, {"message": PRIVATE}, env=env))


# --- no vault --------------------------------------------------------------------------------


def test_owner_outside_any_vault_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    assert gate.run(stdin=_payload(SEND, {"message": PRIVATE}, tmp_path), env={}) is None


def test_run_with_no_vault_to_check_against_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "not-a-vault"))
    assert "could not run" in _reason(gate.run(stdin=_payload(SEND, {"message": CLEAN}, tmp_path), env=RUN))


# --- malformed input -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "{}", json.dumps({"tool_name": SEND}), json.dumps({"tool_name": SEND, "tool_input": "x"})],
)
def test_malformed_payload_fails_closed_in_a_run(vault, raw):
    assert "could not run" in _reason(gate.run(stdin=io.StringIO(raw), env=RUN))


@pytest.mark.parametrize("raw", ["not json", json.dumps({"tool_name": SEND})])
def test_malformed_payload_only_warns_the_owner(vault, raw):
    out = gate.run(stdin=io.StringIO(raw), env={})
    assert out is not None and "permissionDecision" not in out["hookSpecificOutput"]
    assert "could not run" in out["systemMessage"]


def test_a_check_error_fails_closed_in_a_run(vault, monkeypatch):
    def boom(*_a):
        raise RuntimeError("bug")

    monkeypatch.setattr(gate, "flagged_fields", boom)
    assert "RuntimeError" in _reason(_decide(vault, SEND, {"message": CLEAN}))


def test_main_prints_the_decision(vault, monkeypatch, capsys):
    for k, v in RUN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", _payload(SEND, {"message": PRIVATE}, vault))
    assert gate.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", _payload(SEND, {"message": CLEAN}, vault))
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


# --- hooks.json wiring -----------------------------------------------------------------------


def _entry() -> dict:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if "outbound-gate" in h["hooks"][0]["command"]]
    return entry


def _sh(env: dict, payload: str, cwd: Path, root: Path = PLUGIN) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_DATA_DIR")}
    full = {**clean, **env, "CLAUDE_PLUGIN_ROOT": str(root)}
    cmd = _entry()["hooks"][0]["command"]
    return subprocess.run(
        ["sh", "-c", cmd], env=full, cwd=cwd, input=payload, capture_output=True, text=True, timeout=60
    )


def test_matcher_is_an_anchored_regex_covering_every_write_tool():
    import re

    matcher = re.compile(_entry()["matcher"])
    for tool in (SEND, "mcp__claude_ai_Slack__slack_schedule_message", "mcp__claude_ai_Linear__save_comment"):
        assert matcher.search(tool) and gate.surface_for(tool)
    assert not matcher.search("Bash") and not matcher.search("mcp__claude_ai_Slack__slack_read_channel")


def test_owner_outside_a_vault_exits_in_the_shell_without_python(tmp_path):
    payload = _payload(SEND, {"message": PRIVATE}, tmp_path).getvalue()
    res = _sh({}, payload, tmp_path, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_run_send_is_denied_through_the_real_cli(vault):
    payload = _payload(SEND, {"message": PRIVATE}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_fails_closed_when_scoutctl_cannot_run(vault, tmp_path):
    payload = _payload(SEND, {"message": CLEAN}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2 and gate.MARK in res.stderr


def test_owner_in_a_vault_is_only_warned_when_scoutctl_cannot_run(vault, tmp_path):
    payload = _payload(SEND, {"message": CLEAN}, vault).getvalue()
    res = _sh({}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 1 and "Warning" in res.stderr

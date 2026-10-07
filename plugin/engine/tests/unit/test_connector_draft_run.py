"""connectors custom draft: two locked-down headless calls, then the gate (spec §4.2)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import connector_draft as cd

PLUGIN = Path(__file__).parent.parent.parent.parent
SERVER = "claude.ai Example Suite"
T = "mcp__claude_ai_Example_Suite__"
GOOD = {
    "definitions": [
        {
            "key": "suite_mail",
            "display_name": "Mail suite",
            "probe": f"{T}list_folders",
            "preset": "mail",
            "inbound": {"tools": [f"{T}search_messages"]},
        }
    ],
    "summary": [{"key": "suite_mail", "scans": "new mail that may need a reply", "looks_up": "nothing"}],
}


def _envelope(structured, *, is_error=False):
    return json.dumps(
        {"type": "result", "is_error": is_error, "result": json.dumps(structured), "structured_output": structured}
    )


class FakeClaude:
    """Answers draft calls from `drafts` in order and probe calls with `probe_ok`."""

    def __init__(self, drafts, probe_ok=True):
        self.drafts = list(drafts)
        self.probe_ok = probe_ok
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append((argv, stdin))
        if "haiku" in argv:  # the probe call
            return cd.ClaudeResult(0, _envelope({"ok": self.probe_ok, "error": "" if self.probe_ok else "401"}))
        nxt = self.drafts.pop(0)
        return nxt if nxt is None or isinstance(nxt, cd.ClaudeResult) else cd.ClaudeResult(0, _envelope(nxt))


def _draft(fake, tmp_path):
    return cd.draft(SERVER, plugin_root=PLUGIN, vault=tmp_path / "Scout", claude_bin="claude", runner=fake)


def test_drafted(tmp_path):
    fake = FakeClaude([GOOD])
    out = _draft(fake, tmp_path)
    assert out["status"] == "drafted", out
    assert out["definitions"][0]["server"] == "claude_ai_Example_Suite"
    assert out["summary"][0]["scans"] == "new mail that may need a reply"
    assert out["server"] == SERVER and out["schema_version"] == 1


def test_the_draft_call_is_locked_down_and_takes_the_prompt_on_stdin(tmp_path):
    fake = FakeClaude([GOOD])
    _draft(fake, tmp_path)
    argv, stdin = fake.calls[0]
    joined = " ".join(argv)
    for flag in (
        "--permission-mode dontAsk",
        "--allowedTools ToolSearch",
        "--tools ToolSearch",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--model sonnet",
        "--max-budget-usd",
    ):
        assert flag in joined, flag
    assert "claude_ai_Example_Suite" in stdin and SERVER in stdin
    probe_argv, _ = fake.calls[1]
    assert probe_argv[probe_argv.index("--allowedTools") + 2] == f"{T}list_folders"


def test_one_retry_with_feedback_then_invalid(tmp_path):
    bad = {**GOOD, "definitions": [{**GOOD["definitions"][0], "inbound": {"tools": [f"{T}send_message"]}}]}
    fake = FakeClaude([bad, bad])
    out = _draft(fake, tmp_path)
    assert out["status"] == "invalid" and out["issues"]
    assert "send_message" in fake.calls[1][1]  # the retry prompt carries the issues
    assert len(fake.calls) == 2  # no probe call after an invalid draft


def test_retry_can_succeed(tmp_path):
    bad = {**GOOD, "definitions": [{**GOOD["definitions"][0], "inbound": {"tools": [f"{T}send_message"]}}]}
    assert _draft(FakeClaude([bad, GOOD]), tmp_path)["status"] == "drafted"


def test_failing_probe_is_needs_auth(tmp_path):
    out = _draft(FakeClaude([GOOD], probe_ok=False), tmp_path)
    assert out["status"] == "needs_auth" and "401" in out["message"]


def test_no_read_tools(tmp_path):
    out = _draft(FakeClaude([{"no_read_tools": True, "definitions": [], "summary": []}]), tmp_path)
    assert out["status"] == "no_read_tools"


def test_timeout_and_error(tmp_path):
    assert _draft(FakeClaude([None]), tmp_path)["status"] == "timeout"
    err = cd.ClaudeResult(
        1, json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "errors": ["Reached maximum budget"]})
    )
    out = _draft(FakeClaude([err]), tmp_path)
    assert out["status"] == "error" and "maximum budget" in out["message"]


def test_keys_already_in_the_vault_are_taken(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "connectors.custom.yaml").write_text("schema_version: 1\nconnectors:\n  suite_mail: {display_name: X}\n")
    fake = FakeClaude([GOOD, GOOD])
    out = _draft(fake, tmp_path)
    assert out["status"] == "invalid"
    assert "suite_mail" in fake.calls[0][1]  # the prompt lists taken keys


def test_prompt_template_carries_the_rules():
    text = cd.render_prompt(SERVER, plugin_root=PLUGIN, taken={"slack"})
    assert "+claude_ai_Example_Suite" in text and "1–4 read tools per activity" in text and "slack" in text
    assert "on one line" in text and "markdown headings" in text  # #324's validator rules
    assert "contains a read verb" in text  # the Task 3 gate's rule
    assert "{{" not in text


def test_cli_emits_json_and_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(cd, "run_claude", FakeClaude([GOOD], probe_ok=False))
    result = CliRunner().invoke(
        app, ["connectors", "custom", "draft", "--server", SERVER, "--json", "--claude-bin", "/bin/echo"]
    )
    assert result.exit_code == 3
    assert json.loads(result.stdout)["status"] == "needs_auth"


# ---------------------------------------------------------------------------
# `run_claude` itself — the only thing that ever touches a real subprocess.
# Every case below stubs `subprocess.run`; none runs a real `claude`.
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_claude_wraps_a_successful_subprocess(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess(0, "out", "err")

    monkeypatch.setattr(cd.subprocess, "run", fake_run)
    result = cd.run_claude(["claude", "-p"], "the prompt", 5.0)
    assert result == cd.ClaudeResult(0, "out", "err")
    assert captured["argv"] == ["claude", "-p"]
    assert captured["kwargs"]["input"] == "the prompt"
    assert captured["kwargs"]["timeout"] == 5.0


def test_run_claude_timeout_is_none(monkeypatch):
    def fake_run(argv, **kwargs):
        raise cd.subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(cd.subprocess, "run", fake_run)
    assert cd.run_claude(["claude"], "x", 1.0) is None


def test_run_claude_missing_binary_is_returncode_127(monkeypatch):
    def fake_run(argv, **kwargs):
        raise OSError("no such file or directory: 'claude'")

    monkeypatch.setattr(cd.subprocess, "run", fake_run)
    result = cd.run_claude(["claude"], "x", 1.0)
    assert result.returncode == 127
    assert "no such file" in result.stderr


# ---------------------------------------------------------------------------
# `_structured` / `_error_text` — envelope-parsing edge cases the happy-path
# fixtures above never exercise.
# ---------------------------------------------------------------------------


def test_structured_is_none_for_non_json_stdout():
    assert cd._structured(cd.ClaudeResult(0, "not json at all")) is None


def test_structured_is_none_for_a_non_dict_envelope():
    assert cd._structured(cd.ClaudeResult(0, json.dumps(["not", "a", "dict"]))) is None


def test_structured_falls_back_to_parsing_the_result_text():
    env = {"is_error": False, "result": json.dumps({"definitions": [], "summary": []})}
    out = cd._structured(cd.ClaudeResult(0, json.dumps(env)))
    assert out == {"definitions": [], "summary": []}


def test_structured_is_none_when_result_text_is_not_json():
    env = {"is_error": False, "result": "not json"}
    assert cd._structured(cd.ClaudeResult(0, json.dumps(env))) is None


def test_structured_is_none_when_result_text_is_not_a_dict():
    env = {"is_error": False, "result": json.dumps([1, 2])}
    assert cd._structured(cd.ClaudeResult(0, json.dumps(env))) is None


def test_error_text_uses_subtype_when_there_is_no_errors_list():
    env = {"is_error": True, "subtype": "error_rate_limited"}
    assert cd._error_text(cd.ClaudeResult(1, json.dumps(env))) == "error_rate_limited"


def test_error_text_falls_back_to_stderr_for_non_json_output():
    assert cd._error_text(cd.ClaudeResult(1, "", "boom")) == "boom"


def test_error_text_falls_back_to_stdout_when_stderr_is_empty():
    assert cd._error_text(cd.ClaudeResult(1, "raw crash text", "")) == "raw crash text"


def test_error_text_falls_back_to_the_exit_code_when_output_is_empty():
    assert cd._error_text(cd.ClaudeResult(7, "", "")) == "claude exited 7"

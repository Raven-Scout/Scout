"""kb-read-gate: a scheduled run reads the KB only through ``scoutctl kb`` (hard-rule audit row 2, #453)."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.raw_read import raw_kb_read, search_reads_kb

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    (v / "knowledge-base" / "people").mkdir(parents=True)
    (v / "knowledge-base" / "ontology").mkdir()
    (v / "action-items" / "context").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("user: {}\n")
    (v / "knowledge-base" / "people.md").write_text("# People\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v.resolve()


def _payload(tool: str, tool_input: dict, cwd: Path, transcript: Path | None = None) -> io.StringIO:
    body = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd), "session_id": "s1"}
    if transcript is not None:
        body["transcript_path"] = str(transcript)
    return io.StringIO(json.dumps(body))


def _decision(vault: Path, tool: str, tool_input: dict, *, cwd: Path | None = None, env=RUN, transcript=None):
    return gate.run(stdin=_payload(tool, tool_input, cwd or vault, transcript), env=env)


def _denied(out) -> str:
    assert out is not None, "expected a deny"
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith(gate.MARK)
    return spec["permissionDecisionReason"]


# --- the shared classifier ---------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat knowledge-base/people.md",
        "cat knowledge-base.md",
        'echo "--- people ---" && cat knowledge-base/people.md',
        "ls action-items/context/",
        "find knowledge-base -name '*.md'",
        "git show HEAD:knowledge-base/people.md",
        "cd knowledge-base && grep -r Priya .",
        "xargs cat < list.txt knowledge-base/x.md",
    ],
)
def test_raw_reads_are_flagged(command):
    assert raw_kb_read(command)


@pytest.mark.parametrize(
    "command",
    [
        "scripts/scoutctl kb read knowledge-base/people.md",
        "scripts/scoutctl kb files knowledge-base/people | head -20",
        "python3 scripts/recurring-task-status.py --date 2026-10-29 --json",
        'git add knowledge-base/people.md && git commit -m "update knowledge-base/people.md"',
        "ls action-items/",
        "cat .scout-cache/session-context.json",
    ],
)
def test_engine_scripts_and_scoutctl_are_not_flagged(command):
    assert not raw_kb_read(command)


def test_a_shell_already_in_the_kb_flags_any_read():
    assert raw_kb_read("ls", in_kb=True)
    assert not raw_kb_read("cd /tmp && ls", in_kb=True)


def test_search_classifier_matches_the_eval_rule():
    assert search_reads_kb("Grep", {"pattern": "x", "path": "knowledge-base"})
    assert search_reads_kb("Grep", {"pattern": "x"})
    assert not search_reads_kb("Grep", {"pattern": "x", "glob": "action-items/*.md"})
    assert search_reads_kb("Glob", {"pattern": "**/*.md"})
    assert not search_reads_kb("Glob", {"pattern": "action-items/*.md"})


# --- blocked -------------------------------------------------------------------------


def test_run_denies_a_shell_read_and_names_scoutctl_kb(vault):
    reason = _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}))
    assert "scoutctl kb" in reason and "kb read <path>" in reason


def test_run_denies_a_shell_read_from_a_cwd_inside_the_kb(vault):
    _denied(_decision(vault, "Bash", {"command": "ls"}, cwd=vault / "knowledge-base" / "people"))


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "Priya", "path": "knowledge-base"}),
        ("Grep", {"pattern": "Priya"}),  # the whole vault
        ("Grep", {"pattern": "Priya", "path": "."}),
        ("Grep", {"pattern": "Priya", "glob": "knowledge-base/**/*.md"}),
        ("Glob", {"pattern": "**/*.md"}),
        ("Glob", {"pattern": "*.md", "path": "action-items/context"}),
        ("Glob", {"pattern": "knowledge-base/people/*.md"}),
        ("Grep", {"pattern": "Priya", "path": "knowledge-base.md"}),  # the KB's index
    ],
)
def test_run_denies_a_search_that_reaches_the_kb(vault, tool, tool_input):
    assert "search other folders" in _denied(_decision(vault, tool, tool_input))


def test_run_denies_a_search_from_a_folder_above_the_vault(vault):
    _denied(_decision(vault, "Grep", {"pattern": "Priya", "path": str(vault.parent)}))


AGENT = {"SCOUT_READER": "agent:person:priya@example.com"}


@pytest.mark.parametrize("env", [AGENT, {"SCOUT_READER": ""}, {"SCOUT_READER": "Owner"}])
@pytest.mark.parametrize("rel", ["knowledge-base/people.md", "knowledge-base.md"])
def test_a_reader_kb_shows_nothing_is_denied_a_kb_read(vault, env, rel):
    # #453 v1: scoutctl kb shows an agent:* reader (and a malformed one, agent:invalid) nothing.
    note = str(vault / rel)
    assert "scoutctl kb" in _denied(_decision(vault, "Read", {"file_path": note}, env=env))


# --- allowed -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Bash", {"command": "scripts/scoutctl kb search Priya"}),
        ("Bash", {"command": "scripts/scoutctl kb read knowledge-base/people.md"}),
        ("Bash", {"command": "git add knowledge-base/people.md && git commit -m 'kb: people'"}),
        ("Bash", {"command": "python3 scripts/recurring-task-status.py --json"}),
        ("Bash", {"command": "ls action-items/"}),
        ("Grep", {"pattern": "PROJ-1234", "path": "action-items", "glob": "action-items-*.md"}),
        ("Grep", {"pattern": "PROJ-1234", "glob": "action-items/*.md"}),
        ("Glob", {"pattern": "action-items/*.md"}),
        ("Read", {"file_path": "SKILL.md"}),
        # Read stays allowed in a run: the kb-read-label PostToolUse hook labels it (Jordan, 2026-10-29).
        ("Read", {"file_path": "knowledge-base/people.md"}),
        ("Read", {"file_path": "knowledge-base/personal/task.md"}),
        ("Read", {"file_path": "action-items/context/ROLL.md"}),
        ("Read", {"file_path": "knowledge-base/ontology/schema.yaml"}),
        ("Edit", {"file_path": "knowledge-base/people.md", "old_string": "a", "new_string": "b"}),
    ],
)
def test_run_allows_calls_that_stay_off_the_kb_or_go_through_scoutctl(vault, tool, tool_input):
    assert _decision(vault, tool, tool_input) is None


def test_a_search_outside_the_vault_is_allowed(vault, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _decision(vault, "Grep", {"pattern": "x", "path": str(elsewhere)}) is None


# --- owner and non-vault sessions -----------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": ""}])
def test_owner_session_is_never_gated(vault, env):
    note = str(vault / "knowledge-base" / "people.md")
    assert _decision(vault, "Bash", {"command": f"cat {note}"}, env=env) is None
    assert _decision(vault, "Read", {"file_path": note}, env=env) is None
    assert gate.run(stdin=io.StringIO("not json"), env=env) is None


@pytest.mark.parametrize("env", [{"SCOUT_READER": ""}, {"SCOUT_READER": "Owner"}, {"SCOUT_READER": "autonomous"}])
def test_any_reader_but_exactly_owner_is_a_run(vault, env):
    _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}, env=env))


def test_run_outside_any_vault_is_not_gated(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    repo = tmp_path / "other-repo"
    (repo / "knowledge-base").mkdir(parents=True)
    out = gate.run(stdin=_payload("Bash", {"command": "cat knowledge-base/x.md"}, repo), env=RUN)
    assert out is None


# --- malformed input -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"tool_name": "Bash"}',
        '{"tool_name": "Bash", "tool_input": {"command": 3}}',
        '{"tool_name": "Read", "tool_input": {}}',
        '{"tool_name": 7, "tool_input": {}}',
    ],
)
def test_malformed_payload_fails_closed_in_a_run(vault, raw):
    reason = _denied(gate.run(stdin=io.StringIO(raw), env=RUN))
    assert "could not check" in reason


def test_a_gate_error_fails_closed_in_a_run(vault, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(gate, "raw_kb_read", boom)
    _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}))


def test_main_prints_the_decision(vault, monkeypatch, capsys):
    for k, v in RUN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault))
    assert gate.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", _payload("Bash", {"command": "ls action-items"}, vault))
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


# --- hooks.json wiring: the shell short-circuit ----------------------------------------


def _entry(matcher: str = "Grep|Glob|Bash") -> dict:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if "kb-read-gate" in h["hooks"][0]["command"] and h["matcher"] == matcher]
    return entry


def _sh(
    env: dict, payload: str, cwd: Path, root: Path = PLUGIN, matcher: str = "Grep|Glob|Bash"
) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_DATA_DIR")}
    full = {**clean, **env, "CLAUDE_PLUGIN_ROOT": str(root)}
    cmd = _entry(matcher)["hooks"][0]["command"]
    return subprocess.run(
        ["sh", "-c", cmd], env=full, cwd=cwd, input=payload, capture_output=True, text=True, timeout=60
    )


def test_matchers_are_exact_tool_lists():
    # Letters and `|` only: Claude Code matches them as exact lists, so MultiEdit or ReadMcpResource never hit.
    assert _entry()["matcher"] == "Grep|Glob|Bash" and _entry("Read")["matcher"] == "Read"


def test_a_runs_read_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Read", {"file_path": str(vault / "knowledge-base" / "people.md")}, vault).getvalue()
    for env in (RUN, {"SCOUT_MODE": "morning-briefing"}, {}):
        res = _sh({**env, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing", matcher="Read")
        assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_an_agent_readers_kb_read_is_denied_through_the_real_cli(vault):
    payload = _payload("Read", {"file_path": str(vault / "knowledge-base" / "people.md")}, vault).getvalue()
    res = _sh({**AGENT, "SCOUT_DATA_DIR": str(vault)}, payload, vault, matcher="Read")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_owner_session_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_run_call_naming_no_kb_path_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Bash", {"command": "ls action-items"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_run_kb_read_is_denied_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_fails_closed_when_scoutctl_cannot_run(vault, tmp_path):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2 and gate.MARK in res.stderr


def test_run_grep_always_reaches_python(vault, tmp_path):
    # Grep with no path names no KB word, but searches the whole vault: the shell can't tell, so Python decides.
    payload = _payload("Grep", {"pattern": "Priya"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2

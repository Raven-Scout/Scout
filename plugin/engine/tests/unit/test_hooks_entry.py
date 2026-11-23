"""The thin per-tool-call hook entry ``python -m scout.hooks`` (#536, #542): never typer, never scout.cli."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scout.hooks import __main__ as entry
from scout.hooks.__main__ import HOOKS
from tests.unit.privacy_vault_helpers import build_privacy_vault

ENGINE = Path(__file__).resolve().parents[2]
LAUNCHER = ENGINE / "bin" / "scoutctl"

# Each hook's real decision path, including its lazy imports: a deny, a label, a block.
PROBE = """
import io, json, os, sys
from pathlib import Path
vault = Path(os.environ["SCOUT_DATA_DIR"])
def run(name, body):
    sys.stdin = io.StringIO(json.dumps({**body, "cwd": str(vault)}))
    from scout.hooks.__main__ import main
    assert main([name]) == 0
run("kb-read-gate", {"tool_name": "Bash", "tool_input": {"command": "cat knowledge-base/people/sam.md"}})
run("kb-read-gate", {"tool_name": "Grep", "tool_input": {"pattern": "x"}})
run("kb-read-gate", {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m x"}})
run("kb-read-label", {"tool_name": "Read", "tool_input": {"file_path": str(vault / "knowledge-base/people/sam.md")}})
run("outbound-gate", {"tool_name": "mcp__s__slack_send_message", "tool_input": {"message": "Ask Sammy."}})
run("shared-read-record", {"tool_name": "mcp__s__shared_with_me", "tool_response": {"owners": []}})
# mcp-tool-gate (#542) on its own vaults: a deny, an allow, and a file it can't parse (every server gated).
for gate_vault in (os.environ["GATE_VAULT"], os.environ["BROKEN_GATE_VAULT"]):
    os.environ["SCOUT_DATA_DIR"] = gate_vault
    for tool in ("mcp__knowledge__remember", "mcp__knowledge__whoami"):
        sys.stdin = io.StringIO(json.dumps({"tool_name": tool, "tool_input": {}, "cwd": gate_vault}))
        from scout.hooks.__main__ import main
        assert main(["mcp-tool-gate"]) == 0
heavy = sorted(m for m in sys.modules if m == "typer" or m.startswith("typer.") or m in ("click", "scout.cli"))
print("HEAVY=" + json.dumps(heavy))
"""


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("hooks-entry"))


RESTRICTED = (
    "schema_version: 1\nconnectors:\n  company_kb:\n    display_name: Company knowledge\n"
    "    server: knowledge\n    probe: mcp__knowledge__whoami\n    restrict_tools: true\n"
    "    lookup:\n      tools: [mcp__knowledge__knowledge_search]\n      when: Search the company notes first.\n"
)


def _gate_vault(root: Path, custom_file: str) -> Path:
    root.mkdir()
    (root / "scout-config.yaml").write_text("user: {}\n")
    (root / "connectors.custom.yaml").write_text(custom_file)
    return root


@pytest.fixture(scope="module")
def gate_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A vault whose custom connector uses ``restrict_tools``: the one place mcp-tool-gate starts Python."""
    return _gate_vault(tmp_path_factory.mktemp("gate-entry") / "vault", RESTRICTED)


@pytest.fixture(scope="module")
def broken_gate_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _gate_vault(tmp_path_factory.mktemp("gate-entry-broken") / "vault", RESTRICTED + "    x: [unclosed\n")


def _env(vault: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    return {**env, "SCOUT_DATA_DIR": str(vault), **extra}


@pytest.mark.parametrize(
    "env",
    [{"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}, {}],
    ids=["run", "owner"],
)
def test_no_hook_on_the_entry_imports_typer_or_scout_cli(vault, gate_vault, broken_gate_vault, env):
    env = {**env, "GATE_VAULT": str(gate_vault), "BROKEN_GATE_VAULT": str(broken_gate_vault)}
    res = subprocess.run(
        [sys.executable, "-c", PROBE], env=_env(vault, **env), capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0, res.stderr
    (line,) = [x for x in res.stdout.splitlines() if x.startswith("HEAVY=")]
    assert json.loads(line[len("HEAVY=") :]) == []
    # The gate's three denies (the unlisted tool, then both tools under the unparseable file) were printed.
    denies = [x for x in res.stdout.splitlines() if "restrict_tools" in x or "blocks every MCP tool" in x]
    assert len(denies) == 3, res.stdout


def test_importing_the_mcp_tool_gate_imports_neither_typer_nor_scout_cli():
    code = (
        "import json, sys\nimport scout.hooks.mcp_tool_gate\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.split('.')[0] in ('typer', 'click', 'rich') or m == 'scout.cli')))"
    )
    res = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == []


def test_the_launcher_routes_exactly_the_entrys_hooks_to_it():
    text = LAUNCHER.read_text()
    (case,) = re.findall(r'case "\$\{2:-\}" in\s+([^)]+)\)\s+MODULE=scout\.hooks', text)
    assert {name.strip() for name in case.split("|")} == set(HOOKS)
    assert text.count('-P -m "$MODULE" "$@"') == 3 and "-m scout.cli" not in text


def test_the_launcher_runs_a_hook_through_the_entry(vault):
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat knowledge-base/people/sam.md"}, "cwd": str(vault)}
    res = subprocess.run(
        [str(LAUNCHER), "hook", "kb-read-gate"],
        input=json.dumps(payload),
        env=_env(vault, SCOUT_MODE="morning-briefing", PYTHONVERBOSE="1"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    imported = re.findall(r"^import '([\w.]+)'", res.stderr, flags=re.MULTILINE)
    assert "scout.hooks.kb_read_gate" in imported
    assert not [m for m in imported if m == "scout.cli" or m.split(".")[0] in ("typer", "click")]


def _gate_call(cmd: list[str], vault: Path, tool: str, **extra: str) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": tool, "tool_input": {}, "cwd": str(vault)}
    return subprocess.run(
        cmd, input=json.dumps(payload), env=_env(vault, **extra), capture_output=True, text=True, timeout=120
    )


def test_the_launcher_runs_the_mcp_tool_gate_through_the_entry(gate_vault):
    # hooks.json in installed vaults says `scoutctl hook mcp-tool-gate`: same command, now the thin entry (#542).
    cmd = [str(LAUNCHER), "hook", "mcp-tool-gate"]
    res = _gate_call(cmd, gate_vault, "mcp__knowledge__remember", PYTHONVERBOSE="1")
    assert res.returncode == 0, res.stderr[-2000:]
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    imported = re.findall(r"^import '([\w.]+)'", res.stderr, flags=re.MULTILINE)
    assert "scout.hooks.mcp_tool_gate" in imported
    assert not [m for m in imported if m == "scout.cli" or m.split(".")[0] in ("typer", "click")]


@pytest.mark.parametrize("tool", ["mcp__knowledge__remember", "mcp__knowledge__whoami", "mcp__other__ping", "Read"])
@pytest.mark.parametrize("which", ["restricted", "broken"])
def test_the_entry_and_the_cli_command_give_the_same_mcp_tool_gate_answer(gate_vault, broken_gate_vault, which, tool):
    vault = gate_vault if which == "restricted" else broken_gate_vault
    thin = _gate_call([sys.executable, "-P", "-m", "scout.hooks", "mcp-tool-gate"], vault, tool)
    cli = _gate_call([sys.executable, "-P", "-m", "scout.cli", "hook", "mcp-tool-gate"], vault, tool)
    via_launcher = _gate_call([str(LAUNCHER), "hook", "mcp-tool-gate"], vault, tool)
    assert (thin.returncode, thin.stdout, thin.stderr) == (0, cli.stdout, "") and cli.returncode == 0
    assert (via_launcher.returncode, via_launcher.stdout) == (0, cli.stdout)
    denied = which == "broken" or tool == "mcp__knowledge__remember"
    assert bool(thin.stdout) == (denied and tool != "Read")


def test_other_commands_still_go_through_scout_cli(vault):
    res = subprocess.run(
        [str(LAUNCHER), "hook", "--help"], env=_env(vault), capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0 and "mcp-tool-gate" in res.stdout


def test_an_unknown_hook_name_is_a_usage_error():
    res = subprocess.run([sys.executable, "-m", "scout.hooks", "nope"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 2 and "usage" in res.stderr


def _broken_import(monkeypatch, name: str) -> None:
    monkeypatch.setitem(HOOKS, name, "scout.hooks.no_such_module")


def test_the_mcp_tool_gate_denies_when_its_module_cannot_be_imported(monkeypatch, capsys):
    # hooks.json execs it with no shell fallback, and exit 1 lets the call through: the entry must deny (#542).
    _broken_import(monkeypatch, "mcp-tool-gate")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "mcp__knowledge__remember"})))
    assert entry.main(["mcp-tool-gate"]) == 0
    spec = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    reason = spec["permissionDecisionReason"]
    assert "ModuleNotFoundError" in reason and "mcp__knowledge__remember" in reason


@pytest.mark.parametrize("raw", ["not json", "[]", '{"tool_name": 3}', '{"tool_name": "Read"}'])
def test_a_broken_mcp_tool_gate_leaves_what_is_not_an_mcp_call_alone(monkeypatch, capsys, raw):
    # The same payloads mcp_tool_gate.run ignores.
    _broken_import(monkeypatch, "mcp-tool-gate")
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert entry.main(["mcp-tool-gate"]) == 0
    assert capsys.readouterr().out == ""


def test_a_broken_import_of_another_hook_still_raises(monkeypatch):
    # Their hooks.json commands have their own fallback (exit 2 for a run, a private label): unchanged.
    _broken_import(monkeypatch, "kb-read-gate")
    with pytest.raises(ModuleNotFoundError):
        entry.main(["kb-read-gate"])

"""The thin per-tool-call hook entry ``python -m scout.hooks`` (#536): never typer, never scout.cli."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
run("kb-read-label", {"tool_name": "Read", "tool_input": {"file_path": str(vault / "knowledge-base/people/sam.md")}})
run("outbound-gate", {"tool_name": "mcp__s__slack_send_message", "tool_input": {"message": "Ask Sammy."}})
heavy = sorted(m for m in sys.modules if m == "typer" or m.startswith("typer.") or m in ("click", "scout.cli"))
print("HEAVY=" + json.dumps(heavy))
"""


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("hooks-entry"))


def _env(vault: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    return {**env, "SCOUT_DATA_DIR": str(vault), **extra}


@pytest.mark.parametrize(
    "env",
    [{"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}, {}],
    ids=["run", "owner"],
)
def test_no_hook_on_the_entry_imports_typer_or_scout_cli(vault, env):
    res = subprocess.run(
        [sys.executable, "-c", PROBE], env=_env(vault, **env), capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0, res.stderr
    (line,) = [x for x in res.stdout.splitlines() if x.startswith("HEAVY=")]
    assert json.loads(line[len("HEAVY=") :]) == []


def test_the_launcher_routes_exactly_the_entrys_hooks_to_it():
    text = LAUNCHER.read_text()
    (case,) = re.findall(r'case "\$\{2:-\}" in\s+([^)]+)\)\s+MODULE=scout\.hooks', text)
    assert {name.strip() for name in case.split("|")} == set(HOOKS)
    assert text.count('-m "$MODULE" "$@"') == 3 and "-m scout.cli" not in text


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


def test_other_commands_still_go_through_scout_cli(vault):
    res = subprocess.run(
        [str(LAUNCHER), "hook", "--help"], env=_env(vault), capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0 and "mcp-tool-gate" in res.stdout


def test_an_unknown_hook_name_is_a_usage_error():
    res = subprocess.run([sys.executable, "-m", "scout.hooks", "nope"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 2 and "usage" in res.stderr

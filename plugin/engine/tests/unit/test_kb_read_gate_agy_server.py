"""A run can't name agy's local-server variables (runtime-agnostic Phase 2A.3, spec §8.5, findings §1.8 and §7.2).

agy hands every shell child the address and CSRF token of its local server, the agentapi shim and seven more
ANTIGRAVITY_* names. Until a reviewed PR shows that server can't start tool calls around the hooks, the read gate
denies a run's shell command that names any of them, by the shared prefix and in any case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import hook_paths
from tests.unit import test_hooks_kb_read_gate as base
from tests.unit.test_hooks_kb_read_gate import RUN, _decision, _denied

vault = base.vault  # the read gate's own fixture vault

# Every ANTIGRAVITY_* name a run_command child saw in the 2A.2 spike (agy 1.5.16), names only.
SEEN_IN_A_SHELL_CHILD = (
    "ANTIGRAVITY_AGENT",
    "ANTIGRAVITY_AGENTAPI_EXE",
    "ANTIGRAVITY_APP_DATA_DIR",
    "ANTIGRAVITY_CONVERSATION_ID",
    "ANTIGRAVITY_CSRF_TOKEN",
    "ANTIGRAVITY_LS_ADDRESS",
    "ANTIGRAVITY_LS_VERSION",
    "ANTIGRAVITY_PROJECT_ID",
    "ANTIGRAVITY_SOURCE_METADATA",
    "ANTIGRAVITY_TRAJECTORY_ID",
)


def _bash(cmd: str, vault: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(vault), "session_id": "s1"}


@pytest.mark.parametrize(
    "cmd",
    [
        'curl -H "x-csrf: $ANTIGRAVITY_CSRF_TOKEN" "$ANTIGRAVITY_LS_ADDRESS/api"',
        "printenv ANTIGRAVITY_CSRF_TOKEN",
        "python3 -c \"import os; print(os.environ['ANTIGRAVITY_LS_ADDRESS'])\"",
        "env | grep ANTIGRAVITY_",
        '"$ANTIGRAVITY_AGENTAPI_EXE" --help',
        "env | grep -i antigravity_",
        "echo ${Antigravity_Ls_Address}",
        "cd notes && echo ok; printenv ANTIGRAVITY_PROJECT_ID > /tmp/x",
    ],
)
def test_a_run_cant_reach_agys_local_server(vault: Path, cmd: str) -> None:
    reason = gate.decide(_bash(cmd, vault), reader="autonomous", mode="dreaming", env=RUN)
    assert reason is not None and reason.startswith("[scout:kb-read-gate]")
    assert "ANTIGRAVITY_" in reason


def test_every_name_a_shell_child_sees_is_covered(vault: Path) -> None:
    assert set(hook_paths.AGY_SERVER_ENV) <= set(SEEN_IN_A_SHELL_CHILD)
    for name in SEEN_IN_A_SHELL_CHILD:
        assert hook_paths.names_agy_server_env(f"printenv {name}"), name
        assert gate.decide(_bash(f"printenv {name}", vault), reader="autonomous", mode="dreaming", env=RUN)


def test_the_full_hook_denies_it_in_a_run(vault: Path) -> None:
    _denied(_decision(vault, "Bash", {"command": "printenv ANTIGRAVITY_CSRF_TOKEN"}))


@pytest.mark.parametrize(
    "cmd",
    [
        "git status --porcelain",
        "scripts/scoutctl kb read projects/example/example.md",
        "echo antigravity is a word, not a variable",
        "grep -c GRAVITY notes/a.md",
    ],
)
def test_other_commands_are_unchanged(vault: Path, cmd: str) -> None:
    assert gate.decide(_bash(cmd, vault), reader="autonomous", mode="dreaming", env=RUN) is None


def test_the_owners_own_session_is_never_gated(vault: Path) -> None:
    assert _decision(vault, "Bash", {"command": "printenv ANTIGRAVITY_CSRF_TOKEN"}, env={}) is None

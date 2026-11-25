"""The restrict_tools hook fails closed in the shell when the engine cannot start.

``scoutctl hook mcp-tool-gate`` is the only thing that enforces ``restrict_tools``. Claude Code lets a
PreToolUse call through when its hook exits with anything but 0 or 2, so a ``scoutctl`` that cannot
start (no venv, a shim naming a missing interpreter, a Python crash before the gate, an engine
half-replaced mid-upgrade) used to lift every restriction without a word. ``hooks/prefilter.sh`` and,
when the script is missing or broken, the hooks.json entry now block the call instead, but only for a
session that may be in a vault, by the rule of ``_may_be_in_a_vault`` in ``scout/hooks/__main__.py``:
hooks.json is plugin-wide, so MCP must keep working everywhere else.

Every case runs the real hooks.json command, with ``sh`` and with ``/bin/bash`` (3.2 on macOS), against
a plugin root whose ``scoutctl`` is broken in one of several ways.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

ENGINE = Path(__file__).resolve().parents[2]
PLUGIN = ENGINE.parent
PREFILTER = PLUGIN / "hooks" / "prefilter.sh"
TOOL = "mcp__knowledge__remember"
SHELLS = ("sh", "/bin/bash")
RUN = {"SCOUT_MODE": "research"}
OWNER: dict[str, str] = {}

# scoutctl stand-ins that cannot run the gate, with the exits each one may give. A missing interpreter
# is 126 on macOS ("bad interpreter") and 127 on Linux (execve says ENOENT, as for a missing file).
Codes = tuple[int, ...]
BROKEN_ENGINES: dict[str, tuple[str | None, Codes]] = {
    "a shim naming a missing interpreter": ("#!/nonexistent/python3\n", (126, 127)),
    "no scoutctl at all": (None, (127,)),
    "python crashes before the gate": ("#!/bin/sh\ncat >/dev/null\necho Traceback >&2\nexit 1\n", (1,)),
    "a usage error": ("#!/bin/sh\necho 'Unknown option: -P' >&2\nexit 2\n", (2,)),
    "killed": ("#!/bin/sh\nkill -9 $$\n", (137,)),
}
# Records its argv and stdin, then answers like the real gate: a deny on stdout, exit 0.
DENY = '{"hookSpecificOutput": {"permissionDecision": "deny"}}'
WORKING = "#!/bin/sh\n" + '{ printf \'%s\\n\' "$*"; cat; } >"$STUB_LOG"\n' + f"printf '%s\\n' '{DENY}'\n"


def _command() -> str:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if h.get("matcher") == "mcp__.*"]
    (hook,) = entry["hooks"]
    return str(hook["command"])


class Outcome(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class World:
    """A vault that uses restrict_tools, a folder outside it, and a plugin root with a given scoutctl."""

    def __init__(self, tmp: Path, scoutctl: str | None, *, prefilter: bool = True) -> None:
        self.vault = tmp / "vault"
        (self.vault / "projects" / "repo").mkdir(parents=True)
        (self.vault / "scout-config.yaml").write_text("x: 1\n")
        (self.vault / "connectors.custom.yaml").write_text("connectors:\n  kb:\n    restrict_tools: true\n")
        self.outside = tmp / "outside"
        self.outside.mkdir()
        (self.outside / "into-vault").symlink_to(self.vault / "projects")
        self.home = tmp / "home"
        self.home.mkdir()
        self.root = tmp / "plugin"
        (self.root / "engine" / "bin").mkdir(parents=True)
        (self.root / "hooks").mkdir()
        if prefilter:
            (self.root / "hooks" / "prefilter.sh").symlink_to(PREFILTER)
        if scoutctl is not None:
            stub = self.root / "engine" / "bin" / "scoutctl"
            stub.write_text(scoutctl)
            stub.chmod(0o755)
        self.log = tmp / "stub.log"

    def run(self, payload: str, *, cwd: Path, shell: str = "sh", env: dict[str, str] | None = None) -> Outcome:
        out = subprocess.run(
            [shell, "-c", _command()],
            env={
                "HOME": str(self.home),
                "PATH": "/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(self.root),
                "STUB_LOG": str(self.log),
                **(env or {}),
            },
            cwd=cwd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return Outcome(out.returncode, out.stdout, out.stderr)


def _payload(cwd: object = "<none>", tool: str = TOOL, tool_input: object = None) -> str:
    data: dict[str, object] = {"session_id": "s1", "hook_event_name": "PreToolUse"}
    if cwd != "<none>":
        data["cwd"] = cwd
    data["tool_name"] = tool
    data["tool_input"] = tool_input if tool_input is not None else {}
    return json.dumps(data)


def _status_in(stderr: str, codes: Codes) -> bool:
    return any(f"(status {code})" in stderr for code in codes)


def _denied(outcome: Outcome, codes: Codes, tool: str = TOOL) -> bool:
    return (
        outcome.returncode == 2
        and f"so {tool} is blocked" in outcome.stderr
        and _status_in(outcome.stderr, codes)
        and "/scout-update" in outcome.stderr
        and "scoutctl bootstrap doctor" in outcome.stderr
    )


def _not_blocked(outcome: Outcome) -> bool:
    return outcome.returncode == 1 and outcome.stdout == "" and "[scout:mcp-tool-gate]" not in outcome.stderr


@pytest.fixture(params=list(BROKEN_ENGINES), ids=list(BROKEN_ENGINES))
def broken(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[World, Codes]:
    text, code = BROKEN_ENGINES[request.param]
    return World(tmp_path, text), code


# ----- a session in a vault: blocked ------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_an_mcp_call_in_a_vault_is_blocked_when_the_engine_cannot_start(
    broken: tuple[World, Codes], shell: str
) -> None:
    world, code = broken
    for cwd in (world.vault, world.vault / "projects" / "repo"):
        for env in (OWNER, RUN):
            outcome = world.run(_payload(str(cwd)), cwd=cwd, shell=shell, env=env)
            assert _denied(outcome, code), (cwd, env, outcome)


@pytest.mark.parametrize("shell", SHELLS)
def test_scout_data_dir_blocks_whatever_the_payload_cwd(broken: tuple[World, Codes], shell: str) -> None:
    world, code = broken
    env = {"SCOUT_DATA_DIR": str(world.vault)}
    outcome = world.run(_payload(str(world.outside)), cwd=world.outside, shell=shell, env=env)
    assert _denied(outcome, code), outcome


def test_a_cwd_reaching_the_vault_through_a_symlink_is_blocked(broken: tuple[World, Codes]) -> None:
    world, code = broken
    outcome = world.run(_payload(str(world.outside / "into-vault" / "repo")), cwd=world.vault)
    assert _denied(outcome, code), outcome


def test_any_cwd_in_the_payload_that_is_in_a_vault_blocks(broken: tuple[World, Codes]) -> None:
    # Only the top-level cwd is the session's, but the shell does not parse JSON: it checks them all.
    world, code = broken
    payload = _payload(str(world.outside), tool_input={"cwd": str(world.vault)})
    assert _denied(world.run(payload, cwd=world.vault), code)


def test_a_compact_payload_is_read_the_same(broken: tuple[World, Codes]) -> None:
    world, code = broken
    compact = json.dumps({"cwd": str(world.vault), "tool_name": TOOL}, separators=(",", ":"))
    assert _denied(world.run(compact, cwd=world.vault), code)
    compact = json.dumps({"cwd": str(world.outside), "tool_name": TOOL}, separators=(",", ":"))
    assert _not_blocked(world.run(compact, cwd=world.vault))


# ----- a cwd the shell cannot read for certain: blocked -----------------------------------------

UNKNOWN_CWDS = {
    "no cwd": "<none>",
    "a null cwd": None,
    "a number": 7,
    "an empty cwd": "",
    "a relative cwd": "outside",
    "a home-relative cwd": "~/outside",
    "a JSON escape": "/tmp/café",  # json.dumps writes é
}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("cwd", list(UNKNOWN_CWDS.values()), ids=list(UNKNOWN_CWDS))
def test_an_unknown_cwd_is_blocked(broken: tuple[World, Codes], cwd: object, shell: str) -> None:
    world, code = broken
    assert _denied(world.run(_payload(cwd), cwd=world.vault, shell=shell), code)


# ----- outside a vault, or not an MCP call: never blocked ---------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_payload_cwd_outside_any_vault_is_not_blocked(broken: tuple[World, Codes], shell: str) -> None:
    # The hook's own cwd is in the vault (so the filter starts the engine); the session's is not.
    world, _ = broken
    for env in (OWNER, RUN):
        assert _not_blocked(world.run(_payload(str(world.outside)), cwd=world.vault, shell=shell, env=env))


@pytest.mark.parametrize("shell", SHELLS)
def test_a_session_outside_any_vault_never_starts_the_engine(broken: tuple[World, Codes], shell: str) -> None:
    world, _ = broken
    assert world.run(_payload(str(world.outside)), cwd=world.outside, shell=shell) == Outcome(0, "", "")


@pytest.mark.parametrize(
    "payload",
    [_payload("/", tool="Bash"), json.dumps({"tool_name": "Read"}), "{}", "", "not json\n"],
    ids=["bash", "read", "empty-object", "empty", "not-json"],
)
def test_what_is_not_an_mcp_call_is_not_blocked(broken: tuple[World, Codes], payload: str) -> None:
    world, _ = broken
    assert _not_blocked(world.run(payload, cwd=world.vault)), payload


# ----- a working engine: unchanged --------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("payload", [_payload("/x"), _payload("/x") + "\n", "{}"], ids=["plain", "newline", "{}"])
def test_a_working_engine_gets_the_payload_and_answers(tmp_path: Path, shell: str, payload: str) -> None:
    world = World(tmp_path, WORKING)
    outcome = world.run(payload, cwd=world.vault, shell=shell)
    assert outcome == Outcome(0, DENY + "\n", "")
    # The filter reads the payload once (a trailing newline goes, as with every filter's read_payload).
    assert world.log.read_text() == f"hook mcp-tool-gate\n{payload.rstrip(chr(10))}"


# ----- the pre-filter missing too: hooks.json's own fallback ------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("name", list(BROKEN_ENGINES))
def test_without_the_prefilter_a_vault_session_is_blocked_and_others_are_not(
    tmp_path: Path, shell: str, name: str
) -> None:
    # The engine has the payload by then, so the entry decides on SCOUT_DATA_DIR and its own cwd.
    text, code = BROKEN_ENGINES[name]
    world = World(tmp_path, text, prefilter=False)
    for env in (OWNER, RUN):
        blocked = world.run(_payload(str(world.vault)), cwd=world.vault / "projects", shell=shell, env=env)
        assert blocked.returncode == 2 and _status_in(blocked.stderr, code), (env, blocked)
        assert "/scout-update" in blocked.stderr and "scoutctl bootstrap doctor" in blocked.stderr
    data_dir = world.run(_payload(), cwd=world.outside, shell=shell, env={"SCOUT_DATA_DIR": str(world.vault)})
    assert data_dir.returncode == 2, data_dir
    assert _not_blocked(world.run(_payload(str(world.outside)), cwd=world.outside, shell=shell))


@pytest.mark.parametrize("shell", SHELLS)
def test_without_the_prefilter_a_working_engine_answers(tmp_path: Path, shell: str) -> None:
    world = World(tmp_path, WORKING, prefilter=False)
    payload = _payload(str(world.outside))
    assert world.run(payload, cwd=world.outside, shell=shell) == Outcome(0, DENY + "\n", "")
    assert world.log.read_text() == f"hook mcp-tool-gate\n{payload}"

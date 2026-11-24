"""The pre-filters find the session's vault from the payload's cwd, as the Python hooks do.

Claude Code hands each hook the session's cwd in the payload, and every Python hook (``session_vault``
in each, ``_may_be_in_a_vault`` in ``scout/hooks/__main__.py``) goes by it, never by its own working
directory. The shell stage of four hooks walked up from its own ``$PWD`` instead (``find_vault``), so a
session whose payload cwd was in a vault, while the hook process ran outside every vault, left the
shell at ``verdict 0`` and was never judged. For mcp-tool-gate that meant a ``restrict_tools`` vault
went unenforced; for outbound-gate, kb-read-label and shared-read-record, an owner session lost its
outbound warning, its privacy label or its shared-read record.

Each case runs the real hooks.json command against a plugin root whose ``scoutctl`` only records that
it started (its arguments and stdin), in every shell the hooks meet: ``sh``, macOS ``/bin/bash`` 3.2
and dash.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

ENGINE = Path(__file__).resolve().parents[2]
PLUGIN = ENGINE.parent
PREFILTER = PLUGIN / "hooks" / "prefilter.sh"
SHELLS = tuple(s for s in ("sh", "/bin/bash", "/bin/dash") if s == "sh" or Path(s).exists())
RUN = {"SCOUT_MODE": "research"}
MCP = "mcp__kb__remember"

# (event, matcher or matcher prefix) of each entry whose filter goes by the payload's cwd.
ENTRIES = {
    "mcp-tool-gate": ("PreToolUse", "mcp__.*"),
    "outbound-gate": ("PreToolUse", "^mcp__.+__.*(send|"),
    "kb-read-label": ("PostToolUse", "Read"),
    "shared-read-record": ("PostToolUse", "^mcp__.+__(shared_with_me|"),
}
# A tool call each filter sends on in a vault session (kb-read-label wants a KB path in the payload).
TOOLS = {
    "mcp-tool-gate": (MCP, {"text": "x"}),
    "outbound-gate": ("mcp__slack__send_message", {"text": "hello"}),
    "kb-read-label": ("Read", {"file_path": "knowledge-base/people/a.md"}),
    "shared-read-record": ("mcp__agnes__shared_with_me", {}),
}
STUB = '#!/bin/sh\n{ printf \'%s\\n\' "$*"; /bin/cat; } >"$STUB_LOG"\n'


def _command(name: str) -> str:
    event, matcher = ENTRIES[name]
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"][event]
    (entry,) = [
        e for e in hooks if e["matcher"] == matcher or (matcher[-1] == "|" and e["matcher"].startswith(matcher))
    ]
    (hook,) = entry["hooks"]
    assert f'/bin/sh "$h" {name} ' in hook["command"]
    return str(hook["command"])


class Outcome(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    python: str  # what the stub recorded ("" if it never started)


class World:
    """A vault that uses restrict_tools, a vault that does not, a folder outside both, and symlinks."""

    def __init__(self, tmp: Path) -> None:
        self.vault = tmp / "vault"
        (self.vault / "projects" / "repo").mkdir(parents=True)
        (self.vault / "scout-config.yaml").write_text("x: 1\n")
        (self.vault / "connectors.custom.yaml").write_text("connectors:\n  kb:\n    restrict_tools: true\n")
        self.plain = tmp / "plain"
        (self.plain / "notes").mkdir(parents=True)
        (self.plain / "scout-config.yaml").write_text("x: 1\n")
        (self.plain / "connectors.custom.yaml").write_text("connectors:\n  kb:\n    tools: [a]\n")
        self.outside = tmp / "outside"
        self.outside.mkdir()
        (self.outside / "into-vault").symlink_to(self.vault / "projects")
        (self.outside / "into-plain").symlink_to(self.plain / "notes")
        self.home = tmp / "home"
        self.home.mkdir()
        self.root = tmp / "plugin"
        (self.root / "engine" / "bin").mkdir(parents=True)
        (self.root / "hooks").mkdir()
        (self.root / "hooks" / "prefilter.sh").symlink_to(PREFILTER)
        stub = self.root / "engine" / "bin" / "scoutctl"
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.log = tmp / "stub.log"

    def run(
        self, name: str, payload: str, *, cwd: Path, shell: str = "sh", env: dict[str, str] | None = None
    ) -> Outcome:
        self.log.unlink(missing_ok=True)
        out = subprocess.run(
            [shell, "-c", _command(name)],
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
            timeout=60,
            check=False,
        )
        python = self.log.read_text() if self.log.exists() else ""
        return Outcome(out.returncode, out.stdout, out.stderr, python)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _payload(name: str, cwd: object = "<none>", *, compact: bool = False, **extra: object) -> str:
    """A payload shaped as Claude Code sends it: session fields, then cwd, then the tool call."""
    tool, tool_input = TOOLS[name]
    data: dict[str, object] = {"session_id": "s1", "transcript_path": "/home/u/.claude/projects/p/s1.jsonl"}
    if cwd != "<none>":
        data["cwd"] = cwd
    data |= {"permission_mode": "default", "hook_event_name": ENTRIES[name][0], "tool_name": tool}
    data["tool_input"] = tool_input
    data |= extra
    return json.dumps(data, separators=(",", ":")) if compact else json.dumps(data)


def _started(outcome: Outcome, name: str, payload: str) -> bool:
    """The stub started as hook ``name`` and got ``payload``, byte for byte."""
    return outcome.python == f"hook {name}\n{payload}"


NEVER = Outcome(0, "", "", "")


# ----- mcp-tool-gate: the payload's cwd decides, not the hook's -----------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_payload_cwd_in_a_restrict_tools_vault_starts_the_gate_wherever_the_hook_runs(
    world: World, shell: str
) -> None:
    for cwd in (world.vault, world.vault / "projects" / "repo"):
        for payload in (_payload("mcp-tool-gate", str(cwd)), _payload("mcp-tool-gate", str(cwd), compact=True)):
            for here in (world.outside, world.plain, world.vault):
                out = world.run("mcp-tool-gate", payload, cwd=here, shell=shell)
                assert out.returncode == 0 and _started(out, "mcp-tool-gate", payload), (cwd, here, out)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_payload_cwd_outside_a_restrict_tools_vault_never_starts_it_even_from_inside_one(
    world: World, shell: str
) -> None:
    # The reverse: the hook's own $PWD is in the restricted vault, the session is elsewhere.
    for cwd in (world.outside, world.plain, world.plain / "notes", Path("/")):
        payload = _payload("mcp-tool-gate", str(cwd))
        for here in (world.vault, world.vault / "projects" / "repo"):
            assert world.run("mcp-tool-gate", payload, cwd=here, shell=shell) == NEVER, (cwd, here)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cwd_reaching_a_vault_through_a_symlink_is_resolved(world: World, shell: str) -> None:
    into_vault = _payload("mcp-tool-gate", str(world.outside / "into-vault" / "repo"))
    out = world.run("mcp-tool-gate", into_vault, cwd=world.outside, shell=shell)
    assert _started(out, "mcp-tool-gate", into_vault), out
    into_plain = _payload("mcp-tool-gate", str(world.outside / "into-plain"))
    assert world.run("mcp-tool-gate", into_plain, cwd=world.vault, shell=shell) == NEVER


def test_any_cwd_key_in_a_restrict_tools_vault_starts_the_gate(world: World) -> None:
    # Only the top-level cwd is the session's, but the shell does not parse JSON: it checks them all.
    payload = _payload("mcp-tool-gate", str(world.outside), tool_input={"cwd": str(world.vault)})
    assert _started(world.run("mcp-tool-gate", payload, cwd=world.outside), "mcp-tool-gate", payload)


def _unknown_cwds(world: World) -> dict[str, str]:
    """Payloads whose session cwd the shell cannot read for certain, by name."""
    p = "mcp-tool-gate"
    return {
        "no cwd": _payload(p),
        "a null cwd": _payload(p, None),
        "a number": _payload(p, 7),
        "an object": _payload(p, {"path": str(world.outside)}),
        "an empty cwd": _payload(p, ""),
        "a relative cwd": _payload(p, "outside"),
        "a home-relative cwd": _payload(p, "~/outside"),
        "a JSON escape": _payload(p, "/tmp/café"),  # json.dumps writes é
        "an escaped quote": _payload(p, str(world.outside) + '"x'),
        "a directory that is not there": _payload(p, str(world.outside / "gone")),
        "a cwd over 4096 bytes": _payload(p, str(world.outside) + "/" + "a" * 4100),
        "only a tool input's cwd": _payload(p, tool_input={"cwd": str(world.outside)}),
        "cwd after the tool input": json.dumps({"tool_name": MCP, "tool_input": {}, "cwd": str(world.outside)}),
        "a pretty-printed payload": json.dumps({"cwd": str(world.outside), "tool_name": MCP}, indent=2),
        "not JSON": "not json\n",
        "empty": "",
    }


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cwd_the_shell_cannot_read_for_certain_starts_the_gate(world: World, shell: str) -> None:
    for why, payload in _unknown_cwds(world).items():
        out = world.run("mcp-tool-gate", payload, cwd=world.outside, shell=shell)
        assert out.returncode == 0 and out.python == f"hook mcp-tool-gate\n{payload.rstrip(chr(10))}", (why, out)


@pytest.mark.parametrize("shell", SHELLS)
def test_scout_data_dir_names_the_vault_whatever_the_payload_says(world: World, shell: str) -> None:
    payload = _payload("mcp-tool-gate", str(world.outside))

    def run(data_dir: str, body: str, here: Path) -> Outcome:
        return world.run("mcp-tool-gate", body, cwd=here, shell=shell, env={"SCOUT_DATA_DIR": data_dir})

    assert _started(run(str(world.vault), payload, world.outside), "mcp-tool-gate", payload)
    assert run(str(world.plain), _payload("mcp-tool-gate", str(world.vault)), world.vault) == NEVER
    # A relative one is read against a working directory the shell would have to guess: Python decides.
    assert _started(run("plain", payload, world.outside), "mcp-tool-gate", payload)


@pytest.mark.parametrize("shell", SHELLS)
def test_outside_every_vault_no_gate_starts(world: World, shell: str) -> None:
    for cwd in (world.outside, Path("/"), world.home):
        for compact in (False, True):
            payload = _payload("mcp-tool-gate", str(cwd), compact=compact)
            for env in ({}, RUN):
                for here in (world.outside, world.vault):
                    assert world.run("mcp-tool-gate", payload, cwd=here, shell=shell, env=env) == NEVER


@pytest.mark.parametrize("shell", SHELLS)
def test_a_large_tool_input_is_decided_in_the_shell_without_a_quadratic_scan(world: World, shell: str) -> None:
    # bash 3.2's ${x#*pat} takes over a minute on 100 KB, so nothing may run it over the payload.
    big = "x" * 1_000_000
    payload = _payload("mcp-tool-gate", str(world.outside), tool_input={"text": big, "cwd": str(world.outside)})
    start = time.monotonic()
    assert world.run("mcp-tool-gate", payload, cwd=world.vault, shell=shell) == NEVER
    assert time.monotonic() - start < 20


# ----- the privacy filters: an owner session goes by the payload's cwd too -----------------------


PRIVACY = ("outbound-gate", "kb-read-label", "shared-read-record")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("name", PRIVACY)
def test_an_owner_session_whose_payload_cwd_is_in_a_vault_reaches_python(world: World, name: str, shell: str) -> None:
    for vault in (world.vault, world.plain):
        payload = _payload(name, str(vault))
        out = world.run(name, payload, cwd=world.outside, shell=shell)
        assert out.returncode == 0 and _started(out, name, payload), (vault, out)
    linked = _payload(name, str(world.outside / "into-plain"))
    assert _started(world.run(name, linked, cwd=world.outside, shell=shell), name, linked)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("name", PRIVACY)
def test_an_owner_session_whose_payload_cwd_is_outside_never_starts_python(world: World, name: str, shell: str) -> None:
    payload = _payload(name, str(world.outside))
    for here in (world.vault, world.plain / "notes", world.outside):
        assert world.run(name, payload, cwd=here, shell=shell) == NEVER, here


@pytest.mark.parametrize("name", PRIVACY)
def test_an_owner_session_with_an_unknown_cwd_reaches_python(world: World, name: str) -> None:
    for payload in (_payload(name), _payload(name, "rel/dir"), _payload(name, "/tmp/café")):
        out = world.run(name, payload, cwd=world.outside)
        assert out.returncode == 0 and _started(out, name, payload), (payload, out)


@pytest.mark.parametrize("name", PRIVACY)
def test_a_run_always_reaches_python_wherever_its_cwd(world: World, name: str) -> None:
    payload = _payload(name, str(world.outside))
    out = world.run(name, payload, cwd=world.outside, env=RUN)
    assert out.returncode == 0 and _started(out, name, payload), out


def test_outbound_gate_and_shared_read_record_hand_a_run_its_payload_unread(world: World) -> None:
    # A run's call goes straight to Python with stdin as it came: byte for byte, trailing newlines too.
    for name in ("outbound-gate", "shared-read-record"):
        payload = _payload(name, str(world.outside)) + "\n\n"
        assert world.run(name, payload, cwd=world.outside, env=RUN).python == f"hook {name}\n{payload}"


def test_kb_read_label_still_leaves_at_once_for_a_read_that_names_no_kb_path(world: World) -> None:
    payload = _payload("kb-read-label", str(world.vault), tool_input={"file_path": "README.md"})
    assert world.run("kb-read-label", payload, cwd=world.vault) == NEVER


def test_an_unreadable_payload_goes_by_the_hooks_own_directory(world: World) -> None:
    # With stdin closed there is no cwd to read, so mcp-tool-gate falls back to where the hook runs.
    def closed(here: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", "-c", "exec 0<&-; " + _command("mcp-tool-gate")],
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(world.root), "STUB_LOG": str(world.log)},
            cwd=here,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    blocked = closed(world.vault / "projects")
    assert blocked.returncode == 2 and "could not read this call" in blocked.stderr, blocked
    assert closed(world.outside).returncode == 0 and closed(world.plain).returncode == 0
    assert not world.log.exists()


def test_find_vault_is_only_the_fallback_for_an_unreadable_payload() -> None:
    script = PREFILTER.read_text(encoding="utf-8")
    calls = [line.strip() for line in script.splitlines() if line.strip() == "find_vault"]
    assert calls == ["find_vault"], calls  # mcp-tool-gate's `read_payload` failure branch, and nothing else

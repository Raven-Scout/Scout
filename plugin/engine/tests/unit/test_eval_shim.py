"""The isolating claude shim and its init-event guard (spec 2026-10-12 §4.1), driven with a fake claude."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._waits import wait_until
from tests.eval.harness.claude_shim import SHIM, build_command, session_plugin_root, settings, tool_hooks
from tests.eval.harness.runner_env import build_env

ENGINE = Path(__file__).resolve().parents[2]
RUNNER_ARGV = ["--permission-mode", "auto", "--model", "opus", "--max-budget-usd", "5", "--name", "scout-x", "-p", "GO"]

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, time
open(os.environ["FAKE_ARGV"], "w").write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(),
    "config": os.environ.get("CLAUDE_CONFIG_DIR"), "secure": os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR"),
    "api_key": os.environ.get("ANTHROPIC_API_KEY"), "gh_token": os.environ.get("GH_TOKEN"),
    "tmpdir": os.environ.get("CLAUDE_CODE_TMPDIR")}))
for line in open(os.environ["FAKE_STREAM"]):
    print(line, end="", flush=True)
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
"""


def _init(servers: list[str], source: str = "none") -> str:
    return json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "apiKeySource": source,
                       "mcp_servers": [{"name": s, "status": "connected"} for s in servers]})  # fmt: skip


RESULT = json.dumps({"type": "result", "subtype": "success", "result": "All done.", "num_turns": 1})


def _run(
    tmp_path: Path,
    stream: list[str],
    *,
    servers: str = "claude_ai_Slack",
    timeout: str = "30",
    sleep: str = "",
    extra_env: dict[str, str] | None = None,
    argv: list[str] | None = None,
    fake_source: str = FAKE_CLAUDE,
):
    fake = tmp_path / "fake-claude"
    fake.write_text(fake_source)
    fake.chmod(0o755)
    (tmp_path / "stream.in").write_text("\n".join(stream) + "\n")
    result_dir = tmp_path / "result"
    env = {
        **os.environ,
        "SCOUT_EVAL_RESULT_DIR": str(result_dir),
        "SCOUT_EVAL_REAL_CLAUDE": str(fake),
        "SCOUT_EVAL_MCP_CONFIG": str(tmp_path / "mcp.json"),
        "SCOUT_EVAL_ALLOWED_SERVERS": servers,
        "SCOUT_EVAL_ALLOWED_TOOLS": "Read,Bash(git *)",
        "SCOUT_EVAL_MODEL": "sonnet",
        "SCOUT_EVAL_TIMEOUT_S": timeout,
        "SCOUT_EVAL_PYTHON": sys.executable,
        "SCOUT_EVAL_ENGINE": str(ENGINE),
        "FAKE_ARGV": str(tmp_path / "argv.json"),
        "FAKE_STREAM": str(tmp_path / "stream.in"),
        "FAKE_SLEEP": sleep,
        **(extra_env or {}),
    }
    proc = subprocess.run([str(SHIM), *(argv or RUNNER_ARGV)], env=env, capture_output=True, text=True, timeout=60)
    return proc, result_dir


def test_command_drops_the_runner_permission_and_model_and_adds_isolation() -> None:
    cmd, prompt = build_command(
        RUNNER_ARGV,
        real_claude="/x/claude",
        model="sonnet",
        mcp_config="/r/mcp.json",
        allowed_tools="Read,Edit",
        result_dir="/r",
    )
    assert prompt == "GO"
    sandbox = (
        '{"disableAllHooks": true, "sandbox": {"enabled": true, "failIfUnavailable": true, '
        '"autoAllowBashIfSandboxed": false, "allowUnsandboxedCommands": false, '
        '"filesystem": {"allowWrite": ["/r"], "denyWrite": ["/tmp/claude*/-*", "/private/tmp/claude*/-*"]}, '
        '"network": {"allowedDomains": [], "strictAllowlist": true}}}'
    )
    assert cmd == [
        "/x/claude", "-p", "--model", "sonnet", "--permission-mode", "dontAsk", "--allowedTools", "Read,Edit",
        "--setting-sources", "", "--settings", sandbox, "--strict-mcp-config",
        "--mcp-config", "/r/mcp.json", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--disable-slash-commands", "--max-budget-usd", "5", "--name", "scout-x",
    ]  # fmt: skip


def test_a_clean_stream_is_teed_and_the_result_text_printed(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack"]), RESULT])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "All done."
    assert (result / "stream.jsonl").read_text().splitlines() == [_init(["claude_ai_Slack"]), RESULT]
    seen = json.loads((tmp_path / "argv.json").read_text())
    assert seen["stdin"] == "GO"
    assert seen["config"] == str(result / "claude-config")
    assert seen["secure"] == ""
    assert not (result / "aborted.json").exists()


def test_bash_is_sandboxed_to_the_result_dir_with_no_network(tmp_path: Path) -> None:
    # spec §12 R2a′: what the real CLI does with these keys is verified by hand per CLI version (§4.3);
    # this pins that the shim hands claude the sandbox for this sample's result dir.
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack"]), RESULT])
    assert proc.returncode == 0, proc.stderr
    seen = json.loads((tmp_path / "argv.json").read_text())
    passed = json.loads(seen["argv"][seen["argv"].index("--settings") + 1])
    assert passed == settings(str(result.resolve()), tool_hooks(ENGINE.parent))
    box = passed["sandbox"]
    assert box["enabled"] and box["failIfUnavailable"]
    assert not box["autoAllowBashIfSandboxed"] and not box["allowUnsandboxedCommands"]
    assert box["filesystem"]["allowWrite"] == [str(result.resolve())]
    assert box["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert seen["tmpdir"] == str(result.resolve() / "tmp") and (result / "tmp").is_dir()


def test_a_non_stub_mcp_server_kills_the_session(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack", "claude.ai Slack"]), RESULT])
    assert proc.returncode == 3
    assert json.loads((result / "aborted.json").read_text()) == {"reason": "mcp_leak", "servers": ["claude.ai Slack"]}
    assert RESULT not in (result / "stream.jsonl").read_text()


def test_an_expected_stub_that_is_not_connected_kills_the_session(tmp_path: Path) -> None:
    # A stub that crashed at start would leave the scenario silently connector-less,
    # indistinguishable from the connector-down scenario.
    failed = json.dumps({"type": "system", "subtype": "init", "model": "m", "apiKeySource": "none",
                         "mcp_servers": [{"name": "claude_ai_Slack", "status": "connected"},
                                         {"name": "claude_ai_Linear", "status": "failed"}]})  # fmt: skip
    proc, result = _run(tmp_path, [failed, RESULT], servers="claude_ai_Linear,claude_ai_Slack")
    assert proc.returncode == 3
    assert json.loads((result / "aborted.json").read_text()) == {
        "reason": "stub_not_connected",
        "servers": ["claude_ai_Linear"],
    }


def test_a_timeout_kills_the_session(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack"]), RESULT, RESULT], timeout="1", sleep="3")
    assert proc.returncode == 4
    assert json.loads((result / "aborted.json").read_text())["reason"] == "timeout"


def test_env_isolates_path_git_and_gh(tmp_path: Path) -> None:
    env = build_env(
        base={
            "HOME": "/home/x",
            "PATH": "/usr/local/bin:/usr/bin",
            "GIT_DIR": "/bad",
            "SCOUT_DATA_DIR": "/bad",
            "SCOUTCTL_BIN": "/live/scoutctl",
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_AUTH_TOKEN": "k",
            "ANTHROPIC_BASE_URL": "u",
            "GH_TOKEN": "t",
            "GITHUB_TOKEN": "t",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
        },  # fmt: skip
        mode="morning-consolidation",
        result_dir=tmp_path / "r",
        mcp_config=tmp_path / "mcp.json",
        allowed_servers={"claude_ai_Slack", "claude_ai_Linear"},
        allowed_tools=["Read", "Bash(git *)"],
        model="sonnet",
        real_claude="/opt/claude/bin/claude",
        script=tmp_path / "connectors.yaml",
        today="2026-10-14",
        timeout_s=1200,
    )
    path = env["PATH"].split(":")
    assert path[0].endswith("tests/eval/stubs/bin") and path[1].endswith(".venv/bin")
    assert "/opt/claude/bin" in path and "/usr/local/bin" not in path
    assert "GIT_DIR" not in env and "SCOUT_DATA_DIR" not in env and "SCOUTCTL_BIN" not in env
    for secret in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                   "CLAUDE_CODE_USE_VERTEX", "GH_TOKEN", "GITHUB_TOKEN"):  # fmt: skip
        assert secret not in env
    assert env["HOME"] == "/home/x"  # isolating HOME loses the keychain login (spec §4.3)
    assert env["SCOUT_FORCE_MODE"] == "morning-consolidation"
    assert env["SCOUT_RETRY_DISABLE"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GH_CONFIG_DIR"] == str(tmp_path / "r" / "gh-config")
    assert env["SCOUT_EVAL_ALLOWED_SERVERS"] == "claude_ai_Linear,claude_ai_Slack"
    assert env["SCOUT_EVAL_ALLOWED_TOOLS"] == "Read,Bash(git *)"
    assert (env["TZ"], env["SCOUT_USER_TIMEZONE"]) == ("UTC", "UTC")


@pytest.mark.parametrize("missing", ["SCOUT_EVAL_RESULT_DIR", "SCOUT_EVAL_REAL_CLAUDE"])
def test_the_shim_refuses_to_run_without_its_eval_env(tmp_path: Path, missing: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_EVAL_")}
    env.update({"SCOUT_EVAL_PYTHON": sys.executable, "SCOUT_EVAL_ENGINE": str(ENGINE)})
    if missing != "SCOUT_EVAL_RESULT_DIR":
        env["SCOUT_EVAL_RESULT_DIR"] = str(tmp_path)
    proc = subprocess.run([str(SHIM), *RUNNER_ARGV], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and missing in proc.stderr


def test_an_api_key_in_the_environment_never_reaches_claude(tmp_path: Path) -> None:
    proc, _ = _run(
        tmp_path,
        [_init(["claude_ai_Slack"]), RESULT],
        extra_env={"ANTHROPIC_API_KEY": "sk-test-not-real", "GH_TOKEN": "gh-test-not-real"},
    )
    assert proc.returncode == 0, proc.stderr
    seen = json.loads((tmp_path / "argv.json").read_text())
    assert seen["api_key"] is None and seen["gh_token"] is None


def test_a_session_billed_to_an_api_key_is_killed(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack"], source="ANTHROPIC_API_KEY"), RESULT])
    assert proc.returncode == 3
    assert json.loads((result / "aborted.json").read_text()) == {"reason": "api_key", "source": "ANTHROPIC_API_KEY"}


def test_a_stream_that_does_not_start_with_init_is_killed(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [RESULT])
    assert proc.returncode == 3
    assert json.loads((result / "aborted.json").read_text())["reason"] == "no_init"


def test_a_child_that_dies_before_reading_the_prompt_does_not_hang_the_shim(tmp_path: Path) -> None:
    import time

    # Over the 64 KB pipe buffer, under Linux's 128 KB limit on a single argument.
    argv = [*RUNNER_ARGV[:-1], "x" * 100_000]
    start = time.monotonic()
    proc, _ = _run(tmp_path, [], timeout="30", argv=argv, fake_source="#!/bin/bash\nexit 1\n")
    assert time.monotonic() - start < 15
    assert proc.returncode != 0


def test_terminating_the_shim_takes_its_claude_session_down(tmp_path: Path) -> None:
    """The runner stops an overrunning sample by signalling the shim's group. claude runs in a
    group of its own, so the shim must kill it on SIGTERM, or it outlives the run."""
    import signal

    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    (tmp_path / "stream.in").write_text(_init(["claude_ai_Slack"]) + "\n")
    env = {
        **os.environ,
        "SCOUT_EVAL_RESULT_DIR": str(tmp_path / "result"),
        "SCOUT_EVAL_REAL_CLAUDE": str(fake),
        "SCOUT_EVAL_MCP_CONFIG": str(tmp_path / "mcp.json"),
        "SCOUT_EVAL_ALLOWED_SERVERS": "claude_ai_Slack",
        "SCOUT_EVAL_ALLOWED_TOOLS": "Read",
        "SCOUT_EVAL_MODEL": "sonnet",
        "SCOUT_EVAL_TIMEOUT_S": "120",
        "SCOUT_EVAL_PYTHON": sys.executable,
        "SCOUT_EVAL_ENGINE": str(ENGINE),
        "FAKE_ARGV": str(tmp_path / "argv.json"),
        "FAKE_STREAM": str(tmp_path / "stream.in"),
        "FAKE_SLEEP": "120",
    }
    shim = subprocess.Popen([str(SHIM), *RUNNER_ARGV], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pgid_file, started = tmp_path / "result" / "claude.pgid", tmp_path / "argv.json"
    # the fake writes argv.json once it is running and has read its prompt
    wait_until(
        lambda: pgid_file.exists() and started.exists(),
        timeout=30,
        interval=0.05,
        message="the shim never started its claude session",
    )
    claude_pid = int(pgid_file.read_text())
    shim.send_signal(signal.SIGTERM)
    shim.wait(timeout=30)

    def claude_gone() -> bool:
        try:
            os.kill(claude_pid, 0)
        except ProcessLookupError:
            return True
        return False

    try:
        wait_until(
            claude_gone, timeout=10, interval=0.05, message=f"claude ({claude_pid}) outlived its terminated shim"
        )
    except AssertionError:
        os.killpg(claude_pid, signal.SIGKILL)
        raise


# --- the arm's own PreToolUse gates ---------------------------------------------------


def test_the_session_gets_its_arms_tool_hooks_and_no_stop_hooks(tmp_path: Path) -> None:
    proc, result = _run(tmp_path, [_init(["claude_ai_Slack"]), RESULT])
    assert proc.returncode == 0, proc.stderr
    seen = json.loads((tmp_path / "argv.json").read_text())
    passed = json.loads(seen["argv"][seen["argv"].index("--settings") + 1])
    assert "disableAllHooks" not in passed and set(passed["hooks"]) == {"PreToolUse", "PostToolUse"}
    commands = [h["command"] for entries in passed["hooks"].values() for entry in entries for h in entry["hooks"]]
    for hook in ("hook kb-read-gate", "hook outbound-gate", "hook kb-read-label"):
        assert any(hook in c for c in commands)
    assert all("${CLAUDE_PLUGIN_ROOT}" not in c and str(ENGINE.parent / "engine" / "bin" / "scoutctl") in c
               for c in commands)  # fmt: skip


def test_a_base_arm_gets_its_own_hooks_json(tmp_path: Path) -> None:
    base = tmp_path / "base" / "plugin"
    (base / "hooks").mkdir(parents=True)
    entry = {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/x"}]}
    (base / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [entry], "Stop": [entry]}}))
    env = {"SCOUT_EVAL_SESSION_PYTHONPATH": str(base / "engine"), "SCOUT_EVAL_ENGINE": str(ENGINE)}
    assert session_plugin_root(env) == base
    assert tool_hooks(base) == {"PreToolUse": [{**entry, "hooks": [{"type": "command", "command": f"{base}/x"}]}]}


def test_an_arm_without_tool_hooks_keeps_every_hook_off(tmp_path: Path) -> None:
    assert tool_hooks(tmp_path) == {}
    assert settings("/r", {})["disableAllHooks"] is True

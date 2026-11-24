"""Integration: the runners launch through `scoutctl agent-runtime` (spec 2026-11-13 §8.2; plan Task 1.4).

Two harnesses:

- **Stub scoutctl** (answers per test) with a stub ``claude-with-retry.sh`` that
  records its argv: resolve, check and the refusal and fallback branches.
- **The real thing, end to end**: each rendered runner, the real wrapper, the real
  engine (``python -P -m scout.cli``) and a stub ``claude`` that records its argv,
  environment and cwd. Only ``gates probe`` is stubbed (a stub claude can't fire the
  real hooks). The run through ``agent-runtime exec`` must reach claude exactly as the
  runner's direct launch does (the engine-without-the-command branch, today's line).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2].parent
TEMPLATES = PLUGIN / "templates"
ENGINE = PLUGIN / "engine"

RUNNERS = [
    ("run-scout.sh.tmpl", "midday-consolidation"),
    ("run-dreaming.sh.tmpl", "dreaming-nightly"),
    ("run-research.sh.tmpl", "research"),
]


def _render(tmpl: Path, scout_dir: Path, scoutctl: str, claude: str) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": claude,
        "{{SCOUTCTL_BIN}}": scoutctl,
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text, f"unrendered placeholder left in {tmpl.name}"
    out = scout_dir / tmpl.name.removesuffix(".tmpl")
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    scripts = scout_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("scout-lock.sh", "run-outcome.sh"):
        body = (TEMPLATES / "scripts" / f"{name}.tmpl").read_text(encoding="utf-8")
        for placeholder, value in {
            "{{INSTANCE_NAME}}": "Scout",
            "{{SCOUT_DIR}}": str(scout_dir),
            "{{SCOUTCTL_BIN}}": scoutctl,
        }.items():
            body = body.replace(placeholder, value)
        (scripts / name).write_text(body, encoding="utf-8")
        (scripts / name).chmod(0o755)
    return out


def _env(slot_key: str, **extra: str) -> dict[str, str]:
    # No CLAUDE_* from the caller: a Claude Code shell sets some (CLAUDE_CODE_DISABLE_TERMINAL_TITLE …), a
    # scheduler doesn't, and the gate probe's cache is keyed on them.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SCOUT_", "PYTEST_", "CLAUDE_", "CLAUDECODE"))}
    env.update(
        SCOUT_RUN_SANDBOX="off",  # the write sandbox has its own kernel tests; here it would need real claude
        SCOUT_FORCE_MODE=slot_key,
        SCOUT_NO_CAFFEINATE="1",
        SCOUT_RETRY_DISABLE="1",
    )
    env.update(extra)
    return env


def _log(scout_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((scout_dir / ".scout-logs").glob("*.log")))


def _outcomes(scout_dir: Path) -> list[dict[str, object]]:
    path = scout_dir / ".scout-logs" / "run-outcomes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# -- stub scoutctl ------------------------------------------------------------------------------------------


def _stub_scoutctl(tmp_path: Path, *, resolve: str | None = "claude-code", check_rc: int = 0) -> tuple[Path, Path]:
    calls = tmp_path / "scoutctl.calls"
    script = tmp_path / "bin" / "scoutctl"
    script.parent.mkdir(parents=True, exist_ok=True)
    answer = f'echo "{resolve}"; exit 0' if resolve is not None else 'echo "error: bad config" >&2; exit 1'
    script.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{calls}"\n'
        f'if [ "$1 $2" = "agent-runtime resolve" ]; then {answer}; fi\n'
        f'if [ "$1 $2" = "agent-runtime check" ]; then echo "[scout:agent-runtime] stub"; exit {check_rc}; fi\n'
        'if [ "$1 $2" = "model resolve" ]; then echo sonnet; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, calls


def _stub_wrapper(scout_dir: Path) -> Path:
    argv_file = scout_dir / "wrapper.argv"
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$@" > "{argv_file}"\n'
        f'printf \'%s\\n\' "${{SCOUT_AGENT_BIN-<unset>}}" "${{SCOUT_AGENT_RUNTIME-<unset>}}" '
        f'"${{SCOUT_RUN_AWAKE_STARTED-<unset>}}" > "{argv_file}.env"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return argv_file


def _setup(tmp_path: Path, name: str, **stub: object) -> tuple[Path, Path, Path, Path]:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    scoutctl, calls = _stub_scoutctl(tmp_path, **stub)  # type: ignore[arg-type]
    script = _render(TEMPLATES / name, scout_dir, str(scoutctl), "/opt/claude/bin/claude")
    argv_file = _stub_wrapper(scout_dir)
    return scout_dir, script, calls, argv_file


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_launches_through_exec(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir, script, calls, argv_file = _setup(tmp_path, name)
    result = subprocess.run([str(script)], env=_env(slot_key), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + _log(scout_dir)
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    scoutctl = str(tmp_path / "bin" / "scoutctl")
    assert argv[1:11] == [
        scoutctl,
        "agent-runtime",
        "exec",
        "--runtime",
        "claude-code",
        "--bin",
        "/opt/claude/bin/claude",
        "--model",
        "sonnet",
        "--max-budget-usd",
    ]
    assert argv[11] == "25"
    assert argv[12] == "--name" and argv[13].startswith(f"scout-{slot_key}-")
    assert argv[14] == "-p"
    agent_bin, runtime, awake_start = Path(f"{argv_file}.env").read_text(encoding="utf-8").splitlines()
    assert (agent_bin, runtime) == ("/opt/claude/bin/claude", "claude-code")
    assert awake_start.split()[0] == "awake" and awake_start.split()[1].isdigit()
    log_calls = calls.read_text(encoding="utf-8")
    assert f"agent-runtime resolve --slot {slot_key}" in log_calls
    assert f"agent-runtime check --slot {slot_key} --runtime claude-code --bin /opt/claude/bin/claude" in log_calls
    # The runtime is resolved before the gate probe and checked after it, and the model for claude-code is
    # resolved as before. Claude Code's probe is the same call as before #652 (no --runtime).
    assert log_calls.index("agent-runtime resolve") < log_calls.index("gates probe")
    assert log_calls.index("gates probe") < log_calls.index("agent-runtime check")
    assert f"gates probe --cached --vault {scout_dir} --claude-bin /opt/claude/bin/claude\n" in log_calls
    assert f"model resolve --slot {slot_key}\n" in log_calls
    assert "=== agent runtime: claude-code ===" in _log(scout_dir)
    # The run-outcome row names the runtime and the run's awake length (spec §8.2, §8.6).
    row = _outcomes(scout_dir)[-1]
    assert row["agent_runtime"] == "claude-code"
    assert "agent_runtime_fallback" not in row
    assert isinstance(row["awake_s"], int) and 0 <= row["awake_s"] < 120


@pytest.mark.parametrize(("name", "slot_key"), [*RUNNERS, ("run-respond.sh.tmpl", "respond")])
def test_runner_names_another_runtime_to_the_gate_probe_and_the_sandbox(
    tmp_path: Path, name: str, slot_key: str
) -> None:
    """#652 S6: the probe and the sandbox profile are the run's runtime's, not claude's."""
    scout_dir, script, calls, _argv = _setup(tmp_path, name, resolve="antigravity")
    env = _env(slot_key)
    env.pop("SCOUT_RUN_SANDBOX")  # so the runner asks for a profile (the stub's empty one is then a skip)
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    log_calls = calls.read_text(encoding="utf-8")
    probe = f"gates probe --cached --vault {scout_dir} --claude-bin /opt/claude/bin/claude --runtime antigravity\n"
    assert probe in log_calls, log_calls
    assert f"sandbox profile --vault {scout_dir} --runtime antigravity\n" in log_calls, log_calls


@pytest.mark.parametrize(("name", "slot_key"), [*RUNNERS, ("run-respond.sh.tmpl", "respond")])
def test_runner_asks_for_claude_codes_sandbox_as_before(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir, script, calls, _argv = _setup(tmp_path, name)
    env = _env(slot_key)
    env.pop("SCOUT_RUN_SANDBOX")
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert f"sandbox profile --vault {scout_dir}\n" in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_resolve_failure_falls_back_to_claude_code(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir, script, calls, argv_file = _setup(tmp_path, name, resolve=None)
    result = subprocess.run([str(script)], env=_env(slot_key), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    log = _log(scout_dir)
    assert "agent runtime resolve failed — running on claude-code" in log
    assert "error: bad config" in log
    assert "--runtime claude-code" in calls.read_text(encoding="utf-8")
    assert argv_file.exists()
    # #652 S7: the row says so, for run health, not only the log.
    row = _outcomes(scout_dir)[-1]
    assert (row["agent_runtime"], row["agent_runtime_fallback"]) == ("claude-code", "resolve_failed")


def test_a_resolve_answer_that_isnt_a_runtime_id_falls_back_to_claude_code(tmp_path: Path) -> None:
    scout_dir, script, calls, argv_file = _setup(tmp_path, "run-scout.sh.tmpl", resolve="connector-health: 2 sessions")
    result = subprocess.run([str(script)], env=_env("x"), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "gave no runtime id" in _log(scout_dir)
    assert "--runtime claude-code --bin" in calls.read_text(encoding="utf-8")
    assert argv_file.exists()
    assert _outcomes(scout_dir)[-1]["agent_runtime_fallback"] == "resolve_failed"


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_check_refusal_records_skipped_runtime_and_never_launches(
    tmp_path: Path, name: str, slot_key: str
) -> None:
    scout_dir, script, _calls, argv_file = _setup(tmp_path, name, resolve="claude-code", check_rc=3)
    result = subprocess.run([str(script)], env=_env(slot_key), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not argv_file.exists(), "a refused runtime must never launch, nor fall back to another"
    assert "refused here (exit 3" in _log(scout_dir)
    rows = _outcomes(scout_dir)
    assert rows and rows[-1]["outcome"] == "skipped_runtime"


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_token_ceiling_is_skipped_budget(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir, script, _calls, argv_file = _setup(tmp_path, name, check_rc=4)
    result = subprocess.run([str(script)], env=_env(slot_key), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not argv_file.exists()
    assert "past its daily token ceiling" in _log(scout_dir)
    assert _outcomes(scout_dir)[-1]["outcome"] == "skipped_budget"


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_an_engine_without_the_command_launches_claude_directly(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir, script, _calls, argv_file = _setup(tmp_path, name, check_rc=2)
    result = subprocess.run([str(script)], env=_env(slot_key), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert argv[1:7] == ["/opt/claude/bin/claude", "--permission-mode", "auto", "--model", "sonnet", "--max-budget-usd"]
    assert "this engine has no agent-runtime command" in _log(scout_dir)


def test_another_runtime_whose_check_says_2_is_refused_not_launched_as_claude(tmp_path: Path) -> None:
    scout_dir, script, _calls, argv_file = _setup(tmp_path, "run-dreaming.sh.tmpl", resolve="antigravity", check_rc=2)
    result = subprocess.run([str(script)], env=_env("dreaming-nightly"), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not argv_file.exists()
    assert _outcomes(scout_dir)[-1]["outcome"] == "skipped_runtime"


# -- the real engine, end to end ------------------------------------------------------------------------------


# What a passing `gates probe --cached` leaves behind, written in the runner's own environment, so the
# claude-code adapter's capabilities (which read that cache) see the probe the runner just passed.
PASSED_PROBE = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from scout.agent_runtimes import gate_probe as g
vault, claude = Path(sys.argv[1]), Path(sys.argv[2])
g._write_cache(vault, g.ProbeResult(
    ok=True, reason="test: passed", claude_version=g.claude_version(claude),
    hooks_sha256=g.hooks_sha256(g.default_plugin_root()), flags=g.PROBE_FLAGS, env_key=g.env_key(),
    config_sha256=g.config_sha256(vault), checked_at=datetime.now(UTC).isoformat()))
"""


def _engine_scoutctl(tmp_path: Path, scout_dir: Path, claude: Path) -> Path:
    """The real engine, except `gates probe`: a stub claude can't fire Scout's hooks, so it records a pass."""
    script = tmp_path / "bin" / "scoutctl"
    script.parent.mkdir(parents=True, exist_ok=True)
    probe = tmp_path / "passed_probe.py"
    probe.write_text(PASSED_PROBE, encoding="utf-8")
    script.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = gates ]; then exec "{sys.executable}" -P "{probe}" "{scout_dir}" "{claude}"; fi\n'
        f'if [ "${{SCOUT_TEST_OLD_ENGINE:-}}" = 1 ] && [ "$1" = agent-runtime ]; then exit 2; fi\n'
        f'exec "{sys.executable}" -P -m scout.cli "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _stub_claude(tmp_path: Path) -> Path:
    stub = tmp_path / "claude-install" / "claude"
    stub.parent.mkdir(parents=True)
    stub.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = --version ]; then echo "2.1.0 (Claude Code)"; exit 0; fi\n'
        'printf "%s\\0" "$@" > "$CLAUDE_OUT.argv"\n'
        'env -0 > "$CLAUDE_OUT.env"\n'
        'pwd > "$CLAUDE_OUT.cwd"\n'
        "echo 'session complete'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _seen(prefix: Path) -> tuple[list[str], dict[str, str], str]:
    argv = [a.decode() for a in Path(f"{prefix}.argv").read_bytes().split(b"\0")[:-1]]
    env = dict(e.decode().split("=", 1) for e in Path(f"{prefix}.env").read_bytes().split(b"\0") if e)
    return argv, env, Path(f"{prefix}.cwd").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_live_run_still_matches_exec_launched_claude(tmp_path: Path, name: str, slot_key: str) -> None:
    """Through the real engine's exec, claude gets the direct launch's argv, environment and cwd."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(scout_dir)], check=True)
    claude = _stub_claude(tmp_path)
    scoutctl = _engine_scoutctl(tmp_path, scout_dir, claude)
    script = _render(TEMPLATES / name, scout_dir, str(scoutctl), str(claude))
    wrapper = (TEMPLATES / "scripts" / "claude-with-retry.sh.tmpl").read_text(encoding="utf-8")
    (scout_dir / "scripts" / "claude-with-retry.sh").write_text(wrapper.replace("{{INSTANCE_NAME}}", "Scout"))
    (scout_dir / "scripts" / "claude-with-retry.sh").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()

    seen = {}
    for label, extra in (("exec", {}), ("direct", {"SCOUT_TEST_OLD_ENGINE": "1"})):
        env = _env(slot_key, HOME=str(home), CLAUDE_OUT=str(tmp_path / label), **extra)
        env["PYTHONPATH"] = str(ENGINE)
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr + _log(scout_dir)
        seen[label] = _seen(tmp_path / label)
    log = _log(scout_dir)
    assert "=== agent runtime: claude-code ===" in log
    assert "this engine has no agent-runtime command" in log

    (x_argv, x_env, x_cwd), (d_argv, d_env, d_cwd) = seen["exec"], seen["direct"]
    # The run name carries the minute; everything else is word for word.
    i = d_argv.index("--name")
    x_argv[i + 1] = d_argv[i + 1] = "<name>"
    assert x_argv == d_argv
    assert d_argv[:2] == ["--permission-mode", "auto"]  # bash's "$@": argv[0] is the stub itself
    assert x_cwd == d_cwd == str(scout_dir)
    for env in (x_env, d_env):
        for noise in ("_", "SCOUT_RUN_STARTED", "SCOUT_RUN_AWAKE_STARTED", "SCOUT_TEST_OLD_ENGINE"):
            env.pop(noise, None)
        env.pop("CLAUDE_OUT")
    assert x_env == d_env
    assert x_env["SCOUT_AGENT_RUNTIME"] == "claude-code"
    assert "SCOUT_AGENT_BIN" not in x_env  # the wrapper drops it before any attempt
    assert x_env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"

"""`scoutctl agent-runtime` and `exec` (spec 2026-11-13 §8.2; plan Task 1.4)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.agent_runtimes import exec as rt_exec
from scout.agent_runtimes.base import TIER_F_REQUIRES, Launch
from tests._agent_runtimes import DummyRuntime, registered

ENGINE = Path(__file__).resolve().parents[2]
PROMPT = (
    "You are Scout, an autonomous knowledge management system.\nStep 1: Read SKILL.md — in full. 'quotes' $HOME `x`"
)


class Execd(Exception):
    pass


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCOUT_AGENT_RUNTIME", raising=False)
    return v


def _cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, BaseException | None]:
    from scout.cli import app

    res = CliRunner().invoke(app, list(args), env=env)
    return res.exit_code, res.output, res.exception


def _exec_args(cap: str = "5.00", runtime: str = "claude-code") -> list[str]:
    return [
        "agent-runtime",
        "exec",
        "--runtime",
        runtime,
        "--bin",
        "/opt/claude/bin/claude",
        "--model",
        "opus",
        "--max-budget-usd",
        cap,
        "--name",
        "scout-morning-briefing-20261116-0800",
        "-p",
        PROMPT,
    ]


GOLDEN = [
    "/opt/claude/bin/claude",
    "--permission-mode",
    "auto",
    "--model",
    "opus",
    "--max-budget-usd",
    "5.00",
    "--name",
    "scout-morning-briefing-20261116-0800",
    "-p",
    PROMPT,
]


def _capture_execve(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], dict[str, str]]]:
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake(path: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((path, list(argv), dict(env)))
        raise Execd

    monkeypatch.setattr(rt_exec.os, "execve", fake)
    return calls


def test_exec_argv_for_claude_code_equals_golden(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_execve(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "1")
    old = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        _, _, exc = _cli(*_exec_args())
        assert isinstance(exc, Execd)
        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_DFL  # CPython's SIG_IGN isn't passed on
    finally:
        signal.signal(signal.SIGPIPE, old)
    path, argv, env = calls[0]
    assert path == GOLDEN[0]
    assert argv == GOLDEN
    assert env == dict(os.environ)  # the runner already exports the marker: nothing added, nothing dropped


def test_exec_honours_retry_rewritten_cap(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_execve(monkeypatch)
    _cli(*_exec_args(cap="1.37"))
    argv = calls[0][1]
    assert argv[argv.index("--max-budget-usd") + 1] == "1.37"


def test_exec_print_argv(vault: Path) -> None:
    code, out, _ = _cli(*_exec_args(), "--print-argv")
    assert code == 0
    assert json.loads(out) == GOLDEN


def test_exec_unknown_runtime_never_falls_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_execve(monkeypatch)
    code, out, _ = _cli(*_exec_args(runtime="codex"))
    assert code == 2 and "unknown agent runtime 'codex'" in out
    assert calls == []


def test_exec_missing_binary_is_127(vault: Path) -> None:
    args = _exec_args()
    args[args.index("--bin") + 1] = str(vault / "no-such-claude")
    code, out, _ = _cli(*args)
    assert code == 127 and "can't start Claude Code" in out


def _stub(tmp_path: Path) -> Path:
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\n"
        'printf "%s\\0" "$@" > "$STUB_OUT.argv"\n'
        'env -0 > "$STUB_OUT.env"\n'
        'pwd > "$STUB_OUT.cwd"\n'
        "exit 7\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _read(out: Path) -> tuple[list[str], dict[str, str], str]:
    argv = Path(f"{out}.argv").read_bytes().split(b"\0")[:-1]
    env_items = [e.split(b"=", 1) for e in Path(f"{out}.env").read_bytes().split(b"\0") if e]
    env = {k.decode(): v.decode() for k, v in env_items}
    return [a.decode() for a in argv], env, Path(f"{out}.cwd").read_text().strip()


def test_a_real_exec_matches_a_direct_launch(tmp_path: Path) -> None:
    """argv, environment, cwd and exit code: scoutctl agent-runtime exec vs the runner's direct claude line."""
    stub = _stub(tmp_path)
    vault = tmp_path / "Scout"
    vault.mkdir()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    base.update(SCOUT_DATA_DIR=str(vault), CLAUDE_CODE_DISABLE_TERMINAL_TITLE="1", SCOUT_AGENT_RUNTIME="claude-code")
    flags = ["--model", "sonnet", "--max-budget-usd", "2.50", "--name", "scout-x-1", "-p", PROMPT]

    direct = subprocess.run(
        [str(stub), "--permission-mode", "auto", *flags],
        env={**base, "STUB_OUT": str(tmp_path / "direct.x")},
        cwd=vault,
        check=False,
    )
    via = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "scout.cli",
            "agent-runtime",
            "exec",
            "--runtime",
            "claude-code",
            "--bin",
            str(stub),
            *flags,
        ],
        env={**base, "STUB_OUT": str(tmp_path / "via.x")},
        cwd=vault,
        check=False,
    )
    assert direct.returncode == via.returncode == 7
    d_argv, d_env, d_cwd = _read(tmp_path / "direct.x")
    v_argv, v_env, v_cwd = _read(tmp_path / "via.x")
    assert v_argv == d_argv
    assert v_cwd == d_cwd
    d_env.pop("STUB_OUT")
    v_env.pop("STUB_OUT")
    # bash sets `_` to the program it runs; everything else must match exactly.
    for noise in ("_",):
        d_env.pop(noise, None)
        v_env.pop(noise, None)
    assert v_env == d_env


def test_run_with_timeout_stops_the_process_group(tmp_path: Path) -> None:
    script = tmp_path / "slow.sh"
    pidfile = tmp_path / "child.pid"
    script.write_text(f"#!/bin/bash\nsleep 300 &\necho $! > {pidfile}\nwait\n", encoding="utf-8")
    script.chmod(0o755)
    env = {**os.environ, "SCOUT_RUN_STARTED": str(int(time.time()))}
    t0 = time.monotonic()
    code = rt_exec.run_with_timeout(Launch(argv=(str(script),)), env, timeout_s=2, runtime_name="Slow", grace_s=2)
    assert code == rt_exec.TIMEOUT_EXIT
    assert time.monotonic() - t0 < 30
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the CLI's own child outlived the timeout")


def test_run_with_timeout_passes_the_exit_code_and_shares_the_deadline(tmp_path: Path) -> None:
    script = tmp_path / "quick.sh"
    script.write_text("#!/bin/bash\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    ok = {**os.environ, "SCOUT_RUN_STARTED": str(int(time.time()))}
    assert rt_exec.run_with_timeout(Launch(argv=(str(script),)), ok, timeout_s=60, runtime_name="Q") == 3
    # A retry after the run's deadline doesn't start at all.
    late = {**os.environ, "SCOUT_RUN_STARTED": str(int(time.time()) - 3600)}
    assert rt_exec.run_with_timeout(Launch(argv=(str(script),)), late, timeout_s=60, runtime_name="Q") == 124


def test_a_runtime_without_a_cap_runs_under_the_timeout(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(launch: Launch, env: dict[str, str], *, timeout_s: int, runtime_name: str) -> int:
        seen.update(timeout_s=timeout_s, argv=launch.argv, mark=env.get("DUMMY_MARK"))
        return 0

    monkeypatch.setattr(rt_exec, "run_with_timeout", fake_run)
    calls = _capture_execve(monkeypatch)
    with registered(DummyRuntime("dummy", binary=Path("/bin/dummy"))):
        code, _, _ = _cli(*_exec_args(runtime="dummy"))
    assert code == 0 and calls == []
    assert seen["timeout_s"] == 45 * 60
    assert seen["mark"] == "1"
    # The run's slot (SCOUT_MODE, which the runners export) picks the kind's default: research starts at 90.
    with registered(DummyRuntime("dummy", binary=Path("/bin/dummy"))):
        code, _, _ = _cli(*_exec_args(runtime="dummy"), env={"SCOUT_MODE": "research-manual"})
    assert code == 0 and seen["timeout_s"] == 90 * 60


# -- resolve / check / list / show ----------------------------------------------------------------------------


def test_resolve(vault: Path) -> None:
    code, out, _ = _cli("agent-runtime", "resolve", "--slot", "morning-briefing", "--json")
    assert code == 0 and json.loads(out) == {"runtime": "claude-code", "source": "builtin"}
    code, out, _ = _cli("agent-runtime", "resolve", env={"SCOUT_AGENT_RUNTIME": "codex"})
    assert code == 1 and "codex" in out


def test_check_exit_codes(vault: Path) -> None:
    with registered(DummyRuntime("dummy", provider="openai", caps=TIER_F_REQUIRES, binary=Path("/bin/true"))):
        code, out, _ = _cli("agent-runtime", "check", "--runtime", "dummy", "--json")
        assert code == 3
        payload = json.loads(out.splitlines()[0])
        assert payload["code"] == "provider_not_consented" and payload["allowed"] is False
        assert "[scout:agent-runtime] provider_not_consented" in out
    with registered(DummyRuntime("dummy", provider="anthropic", caps=TIER_F_REQUIRES, binary=Path("/bin/true"))):
        (vault / "scout-config.yaml").write_text(
            "budget:\n  per_runtime:\n    dummy:\n      max_tokens_per_day: 10\n", encoding="utf-8"
        )
        (vault / ".scout-logs").mkdir()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (vault / ".scout-logs" / "usage-tracker.jsonl").write_text(
            json.dumps({"ts": ts, "agent_runtime": "dummy", "tokens": 11}) + "\n", encoding="utf-8"
        )
        code, out, _ = _cli("agent-runtime", "check", "--runtime", "dummy")
        assert code == 4 and "token_ceiling" in out
    code, _, _ = _cli("agent-runtime", "check", "--runtime", "nope")
    assert code == 2
    code, out, _ = _cli("agent-runtime", "check", "--slot", "x", env={"SCOUT_AGENT_RUNTIME": "nope"})
    assert code == 3 and "check_failed" in out


def test_list_and_show(vault: Path) -> None:
    code, out, _ = _cli("agent-runtime", "list", "--json")
    rows = json.loads(out)
    assert code == 0 and [r["id"] for r in rows] == ["claude-code"]
    assert rows[0]["provider"] == "anthropic" and rows[0]["consented"] is True
    code, out, _ = _cli("agent-runtime", "show", "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["runtime"] == "claude-code" and payload["model"] == "opus"
    assert payload["run_timeout_s"] is None and payload["max_tokens_per_day"] is None

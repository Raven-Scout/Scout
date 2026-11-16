"""`scoutctl agent-runtime` and `exec` (spec 2026-11-13 §8.2; plan Task 1.4)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
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


_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGPIPE", "SIGXFSZ", "SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, name)
)


@pytest.fixture(autouse=True)
def _keep_the_workers_signals() -> Iterator[None]:
    """exec resets SIGPIPE before its execve; with execve patched out the reset would stay in this worker.

    A worker left with SIGPIPE at default dies on the next write to a closed pipe (a hook test feeding a
    shell that exits early): xdist then reports a "crashed worker" in some unrelated test.
    """
    saved = {sig: signal.getsignal(sig) for sig in _SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


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
        from scout.agent_runtimes import limits

        limits.set_owner_limit(vault, "dummy", max_tokens_per_day="10")
        (vault / ".scout-logs").mkdir()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (vault / ".scout-logs" / "usage-tracker.jsonl").write_text(
            json.dumps({"ts": ts, "agent_runtime": "dummy", "tokens": 11}) + "\n", encoding="utf-8"
        )
        code, out, _ = _cli("agent-runtime", "check", "--runtime", "dummy")
        assert code == 4 and "token_ceiling" in out
    code, out, _ = _cli("agent-runtime", "check", "--runtime", "nope")
    assert code == 3 and "check_failed" in out  # never 2: the runners read 2 as an engine without the command
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


def test_run_with_timeout_puts_the_signal_handlers_back(tmp_path: Path) -> None:
    script = tmp_path / "quick.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    env = {**os.environ, "SCOUT_RUN_STARTED": str(int(time.time()))}
    assert rt_exec.run_with_timeout(Launch(argv=(str(script),)), env, timeout_s=60, runtime_name="Q") == 0
    assert {sig: signal.getsignal(sig) for sig in before} == before


def test_the_worker_keeps_sigpipe_ignored_after_an_exec_test() -> None:
    """Pins the fixture above: CPython ignores SIGPIPE, and a test must not leave it at default."""
    assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN


def test_model_resolve_for_another_runtime(vault: Path) -> None:
    code, out, _ = _cli("model", "resolve", "--slot", "x", "--runtime", "claude-code")
    assert code == 0 and out.strip() == "opus"
    with registered(DummyRuntime("dummy")):
        code, out, _ = _cli("model", "resolve", "--runtime", "dummy", "--json")
        assert code == 0 and json.loads(out) == {"model": "dm-default", "source": "builtin"}
    code, out, _ = _cli("model", "resolve", "--runtime", "nope")
    assert code == 1 and "nope" in out


# -- the run timeout counts awake time (Jordan, 2026-11-16, option A) --------------------------------------------


def _script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "cli.sh"
    script.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_time_asleep_doesnt_count(tmp_path: Path) -> None:
    """The host "sleeps" for hours on the wall clock while the awake clock stands still: the run isn't stopped."""
    script = _script(tmp_path, "sleep 1\nexit 5\n")
    env = {**os.environ, "SCOUT_RUN_AWAKE_STARTED": "awake 1000", "SCOUT_RUN_STARTED": str(int(time.time()))}
    wall = iter(range(0, 10**9, 3600))  # every look at the wall clock is an hour later
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)),
        env,
        timeout_s=60,
        runtime_name="Q",
        awake_now=lambda: ("awake", 1010),
        wall_now=lambda: float(next(wall)),
        poll_s=0.05,
    )
    assert code == 5


def test_awake_time_does_count(tmp_path: Path) -> None:
    script = _script(tmp_path, "sleep 30\n")
    env = {**os.environ, "SCOUT_RUN_AWAKE_STARTED": "awake 1000"}
    ticks = iter(range(1000, 10**6, 20))  # 20 s of awake time per look
    t0 = time.monotonic()
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)),
        env,
        timeout_s=60,
        runtime_name="Q",
        awake_now=lambda: ("awake", next(ticks)),
        poll_s=0.05,
        grace_s=2,
    )
    assert code == rt_exec.TIMEOUT_EXIT
    assert time.monotonic() - t0 < 10


def test_spent_falls_back_to_wall_time_and_never_mixes_clocks() -> None:
    assert rt_exec.spent_s({"SCOUT_RUN_AWAKE_STARTED": "awake 100"}, awake_now=lambda: ("awake", 160)) == 60
    assert rt_exec.spent_s({"SCOUT_RUN_AWAKE_STARTED": "wall 100"}, wall_now=lambda: 400.0) == 300
    assert rt_exec.spent_s({"SCOUT_RUN_STARTED": "100"}, wall_now=lambda: 130.0) == 30
    assert (
        rt_exec.spent_s({"SCOUT_RUN_AWAKE_STARTED": "junk", "SCOUT_RUN_STARTED": "100"}, wall_now=lambda: 130.0) == 30
    )
    assert rt_exec.spent_s({}) == 0


def test_the_awake_clock_matches_the_runners_shell() -> None:
    """scout-lock.sh's _scout_awake_now and scout.agent_runtimes.awake read the same clock, in the same format."""
    from scout.agent_runtimes import awake

    lock = (Path(__file__).resolve().parents[3] / "templates" / "scripts" / "scout-lock.sh.tmpl").read_text()
    assert 'getattr(time, "CLOCK_UPTIME_RAW", time.CLOCK_MONOTONIC)' in lock
    assert 'print("awake", int(time.clock_gettime(c)))' in lock
    shell = subprocess.run(
        [sys.executable, "-I", "-c", lock.split("python3 -I -c '")[1].split("'")[0]], capture_output=True, text=True
    ).stdout.split()
    clock, here = awake.now()
    assert shell[0] == clock == "awake"
    assert abs(int(shell[1]) - here) <= 2


def test_set_limit_is_the_owners_alone(vault: Path) -> None:
    from scout.agent_runtimes import limits

    code, out, _ = _cli("agent-runtime", "set-limit", "claude-code", "--run-timeout-minutes", "45")
    assert code == 0 and '"run_timeout_minutes": 45' in out
    assert limits.owner_limits(vault, "claude-code") == {"run_timeout_minutes": 45}
    for env in ({"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "autonomous"}):
        code, out, _ = _cli("agent-runtime", "set-limit", "claude-code", "--run-timeout-minutes", "off", env=env)
        assert code == 2 and "refused" in out
    assert limits.owner_limits(vault, "claude-code") == {"run_timeout_minutes": 45}
    code, _, _ = _cli("agent-runtime", "set-limit", "claude-code")
    assert code == 1
    code, out, _ = _cli("agent-runtime", "set-limit", "claude-code", "--clear")
    assert code == 0 and "defaults" in out


def test_exec_reads_the_slot_kind_from_the_runners_run_type(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(launch: Launch, env: dict[str, str], *, timeout_s: int, runtime_name: str) -> int:
        seen["timeout_s"] = timeout_s
        return 0

    monkeypatch.setattr(rt_exec, "run_with_timeout", fake_run)
    _capture_execve(monkeypatch)
    with registered(DummyRuntime("dummy", binary=Path("/bin/dummy"))):
        _cli(*_exec_args(runtime="dummy"), env={"SCOUT_MODE": "midday-x", "SCOUT_RUN_TYPE": "research"})
    assert seen["timeout_s"] == 90 * 60


def test_exec_logs_config_values_the_limits_ignored(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_execve(monkeypatch)
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    claude-code:\n      run_timeout_minutes: 5\n", encoding="utf-8"
    )
    _, out, exc = _cli(*_exec_args())
    assert isinstance(exc, Execd)
    assert "=== run limits: scout-config.yaml budget.per_runtime.claude-code ignored: run_timeout_minutes: 5" in out

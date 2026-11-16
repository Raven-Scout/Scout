"""``scoutctl agent-runtime exec``: start a run's CLI in this process's place (spec 2026-11-13 §8.2).

The retry wrapper (``claude-with-retry.sh``) runs ``scoutctl agent-runtime exec …``
where it ran ``claude …``, inside the same write sandbox. This builds the adapter's
argv and ``execve``s it, so the wrapper's child *is* the CLI, with the argv,
environment, working directory and file descriptors it had before. The flag names are
the wrapper's on purpose: it reads ``--name`` and ``--max-budget-usd`` and rewrites
the cap for a retry (#561), and the rewritten text reaches the CLI unchanged.

Two things Python changes about its own process are put back before the exec:
SIGPIPE and SIGXFSZ, which CPython ignores at startup and an exec would pass on.

**The run timeout** (``scout.agent_runtimes.limits``). With none (Claude Code's
default) the exec is the whole story. With one, this process stays as the CLI's
parent: it starts the CLI in its own process group, forwards SIGTERM, SIGINT and
SIGHUP to it, and at the run's deadline (``SCOUT_RUN_STARTED`` + the timeout, so
retries share one budget) stops the group and exits 124.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from scout.agent_runtimes.base import AgentRuntime, Launch, RunRequest

TIMEOUT_EXIT = 124
KILL_GRACE_S = 10.0


def build_request(
    *,
    vault: Path,
    model: str,
    budget_cap: str | None,
    run_name: str,
    prompt: str,
    binary: Path | None,
    env: Mapping[str, str],
) -> RunRequest:
    return RunRequest(
        vault=vault,
        mode=env.get("SCOUT_MODE", ""),
        run_name=run_name,
        prompt=prompt,
        model=model,
        budget_cap=budget_cap,
        reader=env.get("SCOUT_READER", ""),
        binary=binary,
    )


def launch_env(launch: Launch, base: Mapping[str, str]) -> dict[str, str]:
    """The CLI's environment: this process's, plus what the adapter adds."""
    env = dict(base)
    env.update(launch.env)
    return env


def _restore_signals() -> None:
    for name in ("SIGPIPE", "SIGXFSZ"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)


def exec_launch(launch: Launch, env: Mapping[str, str]) -> None:
    """Replace this process with the CLI. Only returns by raising (OSError when it can't start)."""
    _restore_signals()
    sys.stdout.flush()
    sys.stderr.flush()
    argv = list(launch.argv)
    if os.sep in argv[0]:
        os.execve(argv[0], argv, dict(env))
    os.execvpe(argv[0], argv, dict(env))


def deadline(timeout_s: int, env: Mapping[str, str], *, now: float | None = None) -> float:
    """When the run must end: its start (the runner's SCOUT_RUN_STARTED) plus the timeout."""
    raw = env.get("SCOUT_RUN_STARTED", "")
    start = float(raw) if raw.isdigit() else (time.time() if now is None else now)
    return start + timeout_s


def run_with_timeout(
    launch: Launch,
    env: Mapping[str, str],
    *,
    timeout_s: int,
    runtime_name: str,
    now: float | None = None,
    grace_s: float = KILL_GRACE_S,
) -> int:
    """Run the CLI as a child, stopping it at the run's deadline. Returns the exit code to exit with."""
    end = deadline(timeout_s, env, now=now)
    minutes = timeout_s // 60
    if end - time.time() <= 0:
        print(f"=== Scout run timeout: {minutes} min already spent, not starting {runtime_name} ===", file=sys.stderr)
        return TIMEOUT_EXIT
    proc = subprocess.Popen(  # noqa: S603 - the adapter's argv
        list(launch.argv),
        env=dict(env),
        stdin=subprocess.PIPE if launch.stdin is not None else None,
        start_new_session=True,
        restore_signals=True,
    )

    def forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    if launch.stdin is not None and proc.stdin is not None:
        try:
            proc.stdin.write(launch.stdin.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass
    try:
        code = proc.wait(timeout=max(0.0, end - time.time()))
    except subprocess.TimeoutExpired:
        print(
            f"=== Scout run timeout: {runtime_name} ran past {minutes} min "
            f"(budget.per_runtime.<id>.run_timeout_minutes) — stopping it ===",
            file=sys.stderr,
        )
        for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, grace_s)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=wait_s)
                break
            except subprocess.TimeoutExpired:
                continue
        return TIMEOUT_EXIT
    return 128 - code if code < 0 else code


def run(
    runtime: AgentRuntime,
    req: RunRequest,
    *,
    timeout_s: int | None,
    env: Mapping[str, str] | None = None,
    execute: bool = True,
) -> Sequence[str] | int:
    """Exec the run (no timeout), or run it under the timeout and return its exit code.

    ``execute=False`` returns the argv instead (``--print-argv``, tests).
    """
    launch = runtime.headless_argv(req)
    full_env = launch_env(launch, os.environ if env is None else env)
    if not execute:
        return launch.argv
    if timeout_s is None:
        exec_launch(launch, full_env)
        raise AssertionError("unreachable: exec returned")  # pragma: no cover
    return run_with_timeout(launch, full_env, timeout_s=timeout_s, runtime_name=runtime.display_name)


__all__ = [
    "KILL_GRACE_S",
    "TIMEOUT_EXIT",
    "build_request",
    "deadline",
    "exec_launch",
    "launch_env",
    "run",
    "run_with_timeout",
]

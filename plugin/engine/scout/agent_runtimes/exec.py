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
SIGHUP to it, and once the run has used the timeout in *awake* time (since the
runner's ``SCOUT_RUN_AWAKE_STARTED``, so retries share one budget and a sleeping Mac
spends none of it) stops the group and exits 124.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from scout.agent_runtimes import awake, registry
from scout.agent_runtimes.base import ANTHROPIC_ENV, NON_ANTHROPIC_ENV_ALLOW, AgentRuntime, Launch, RunRequest

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
    timeout_s: int | None = None,
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
        timeout_s=timeout_s,
    )


def is_anthropic(runtime: AgentRuntime) -> bool:
    """Whether the runtime is Anthropic's, by its registered id (``registry.ANTHROPIC_RUNTIMES``), never by the
    adapter's own ``provider`` (review of #806: an adapter saying "anthropic" mustn't keep the Claude token)."""
    return runtime.id in registry.ANTHROPIC_RUNTIMES


def _matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def launch_env(runtime: AgentRuntime, launch: Launch, base: Mapping[str, str]) -> dict[str, str]:
    """The CLI's environment (#652 S5, review of #806).

    Claude Code's is the runner's, minus the adapter's ``env_unset``, plus its ``env``, as before. Any other
    runtime gets only :data:`base.NON_ANTHROPIC_ENV_ALLOW` of the runner's, minus its ``env_unset``, plus its
    ``env`` minus every :data:`base.ANTHROPIC_ENV` name: no adapter can hand it the Claude token.
    """
    if is_anthropic(runtime):
        env = {k: v for k, v in base.items() if not _matches(k, launch.env_unset)}
        env.update(launch.env)
        return env
    env = {k: v for k, v in base.items() if _matches(k, NON_ANTHROPIC_ENV_ALLOW) and not _matches(k, launch.env_unset)}
    env.update({k: v for k, v in launch.env.items() if not _matches(k, ANTHROPIC_ENV)})
    return env


def _restore_signals() -> None:
    for name in ("SIGPIPE", "SIGXFSZ"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)


def exec_launch(launch: Launch, env: Mapping[str, str]) -> None:
    """Replace this process with the CLI. Only returns by raising (OSError when it can't start)."""
    if launch.cwd is not None:
        os.chdir(launch.cwd)
    _restore_signals()
    sys.stdout.flush()
    sys.stderr.flush()
    argv = list(launch.argv)
    if os.sep in argv[0]:
        os.execve(argv[0], argv, dict(env))
    os.execvpe(argv[0], argv, dict(env))


POLL_S = 5.0


def spent_s(
    env: Mapping[str, str],
    *,
    awake_now: Callable[[], tuple[str, int]] = awake.now,
    wall_now: Callable[[], float] = time.time,
) -> int:
    """Seconds of the run's time budget already used: awake time since ``SCOUT_RUN_AWAKE_STARTED`` (Jordan's
    option A, 2026-11-16: time asleep doesn't count), else wall time since ``SCOUT_RUN_STARTED`` (an older
    runner), else 0. Retries share the run's budget because both are the runner's, taken once."""
    awake_spent = awake.elapsed(env.get("SCOUT_RUN_AWAKE_STARTED"), now_fn=awake_now, wall_fn=wall_now)
    if awake_spent is not None:
        return awake_spent
    raw = env.get("SCOUT_RUN_STARTED", "")
    return max(0, int(wall_now()) - int(raw)) if raw.isdigit() else 0


def run_with_timeout(
    launch: Launch,
    env: Mapping[str, str],
    *,
    timeout_s: int,
    runtime_name: str,
    grace_s: float = KILL_GRACE_S,
    awake_now: Callable[[], tuple[str, int]] = awake.now,
    wall_now: Callable[[], float] = time.time,
    poll_s: float = POLL_S,
) -> int:
    """Run the CLI as a child, stopping it once the run has used ``timeout_s`` of awake time (exit 124).

    The budget is re-read from the awake clock every ``poll_s`` (never from ``wait``'s own timer), so a
    host that sleeps mid-run neither spends the budget asleep nor overshoots it on wake.
    """
    minutes = timeout_s // 60

    def remaining() -> float:
        return timeout_s - spent_s(env, awake_now=awake_now, wall_now=wall_now)

    if remaining() <= 0:
        print(f"=== Scout run timeout: {minutes} min already spent, not starting {runtime_name} ===", file=sys.stderr)
        return TIMEOUT_EXIT

    # Handlers go in before the child starts, so no signal falls between the two; one that arrives
    # before the child exists is passed on as soon as it does.
    child: list[int] = []
    pending: list[int] = []

    def forward(signum: int, _frame: object) -> None:
        if not child:
            pending.append(signum)
            return
        try:
            os.killpg(child[0], signum)
        except ProcessLookupError:
            pass

    forwarded = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {sig: signal.signal(sig, forward) for sig in forwarded}
    try:
        proc = subprocess.Popen(  # noqa: S603 - the adapter's argv
            list(launch.argv),
            env=dict(env),
            cwd=launch.cwd,
            stdin=subprocess.PIPE if launch.stdin is not None else None,
            start_new_session=True,
            restore_signals=True,
        )
        child.append(proc.pid)
        for signum in pending:
            forward(signum, None)
        if launch.stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(launch.stdin.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass
        while True:
            left = remaining()
            if left <= 0:
                break
            try:
                code = proc.wait(timeout=min(left, poll_s))
            except subprocess.TimeoutExpired:
                continue
            return 128 - code if code < 0 else code
        print(
            f"=== Scout run timeout: {runtime_name} ran past {minutes} min of awake time "
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
    finally:
        # This process usually exits next; put the handlers back anyway (an in-process caller, a test).
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run(
    runtime: AgentRuntime,
    req: RunRequest,
    *,
    env: Mapping[str, str] | None = None,
    execute: bool = True,
) -> Sequence[str] | int:
    """Exec the run (``req.timeout_s`` None), or run it under that timeout and return its exit code.

    ``execute=False`` returns the argv instead (``--print-argv``, tests).
    """
    launch = runtime.headless_argv(req)
    full_env = launch_env(runtime, launch, os.environ if env is None else env)
    if not execute:
        return launch.argv
    if req.timeout_s is None:
        exec_launch(launch, full_env)
        raise AssertionError("unreachable: exec returned")  # pragma: no cover
    return run_with_timeout(launch, full_env, timeout_s=req.timeout_s, runtime_name=runtime.display_name)


__all__ = [
    "KILL_GRACE_S",
    "TIMEOUT_EXIT",
    "build_request",
    "spent_s",
    "exec_launch",
    "is_anthropic",
    "launch_env",
    "run",
    "run_with_timeout",
]

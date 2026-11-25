"""A detached experiment run's supervisor: ``python -m scout.lab.supervise <inputs.json>`` (spec §6.4).

A research run can't wait more than 10 minutes for a script, so the run broker starts this process instead
(:func:`start`) and returns. It runs outside every sandbox, in a session of its own, and is nobody's child: an
intermediate process starts it and exits, so launchd reaps it, and the broker (or the run) ending doesn't end it.

It does what an attached run does, through the attached runner whole (ruling P2-h:
:func:`scout.lab.runner.run_experiment` with its ``on_started`` and ``should_stop`` hooks):

1. It takes the experiment's run lock and holds it for its whole life (:func:`scout.lab.runner.hold_run_lock`),
   so an attached run of the same experiment is refused "already running" and a checker can tell it is alive
   (:func:`scout.lab.runs.alive`).
2. It writes the state record (:mod:`scout.lab.runs`): started, deadline, its pid. :func:`start` returns then.
3. It fetches the experiment's model weights, if any (:func:`fetch_weights`; the weights cache, Task 8). The
   script's gate hasn't opened, so nothing of the script has run.
4. It runs the script. Once the gate's kernel checks pass, the record gains the sweep's ``marker`` and ``outside``,
   so a later collect can find and kill what the script started if this process dies.
5. It writes the outcome to the record, then lets the lock go.

**What stops it early** (:class:`Stopper`, asked at every poll, and before the script starts): the owner's veto
(the owner's stores, :func:`scout.sandbox.broker.owner_veto`) or ``lab experiment stop`` (a stop request beside the
record, then SIGTERM): ``stopped by the owner``. A bare SIGTERM (a logout, a shutdown) is
``the supervisor was terminated``. Both are ``not run``. The deadline is wall-clock time, so a Mac that sleeps uses
it up: past it the run is killed and the outcome is the script's exit, ``timed_out``, as for an attached run.

The inputs (vault, experiment, argv, commit, cap, deadline, the owner's limits for the run, the env it runs with)
come in a 0600 file in the owner's folder (:func:`scout.lab.runs.inputs_path`), never on the command line, and the
supervisor removes it once read. Nothing secret is in it or in the record: the meter takes no token.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout import config
from scout.errors import LabError
from scout.lab import runs
from scout.sandbox import broker

STOPPED_BY_OWNER = "stopped by the owner"
TERMINATED = "the supervisor was terminated (a logout, a shutdown or a kill)"
VETO_UNREADABLE = "the owner's veto store can't be read; a veto can't be ruled out"
DEADLINE_PASSED = "the deadline passed"
# Stops that end the experiment "not run": the run abandons it rather than finishing it.
NOT_RUN_STOPS = (STOPPED_BY_OWNER, TERMINATED, VETO_UNREADABLE)
START_WAIT_S = 30.0
# What reaches the supervisor of the run's env: what the runner and the owner's folders read.
ENV_KEYS = ("PATH", "HOME", "LANG", "USER", "LOGNAME", "TMPDIR")
# The owner's limits the runner holds the script to (scout.sandbox.broker.LabLimits fields).
LIMIT_KEYS = ("max_processes", "max_total_bytes", "max_open_files", "disk_free_floor_bytes", "max_memory_bytes")
# The intermediate process (stdlib only): starts the supervisor in a session of its own, its output appended to the
# log file, prints its pid and exits at once, so the supervisor is nobody's child.
_LAUNCHER = """
import os, subprocess, sys
log = os.open(sys.argv[1], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
p = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                     close_fds=True, cwd="/")
print(p.pid, flush=True)
os._exit(0)
"""
_ENGINE_ROOT = str(Path(__file__).resolve().parents[2])


@dataclass(frozen=True)
class Inputs:
    vault: Path
    slug: str
    eid: str
    argv: list[str]
    commit: str | None
    cap_usd: float
    deadline_s: float
    limits: dict[str, int]
    env: dict[str, str]
    claude_argv: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault": str(self.vault),
            "slug": self.slug,
            "eid": self.eid,
            "argv": list(self.argv),
            "commit": self.commit,
            "cap_usd": self.cap_usd,
            "deadline_s": self.deadline_s,
            "limits": dict(self.limits),
            "env": dict(self.env),
            "claude_argv": list(self.claude_argv),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Inputs:
        try:
            limits = {k: int(data["limits"][k]) for k in LIMIT_KEYS}
            return cls(
                vault=Path(str(data["vault"])),
                slug=str(data["slug"]),
                eid=str(data["eid"]),
                argv=[str(a) for a in data["argv"]],
                commit=None if data["commit"] is None else str(data["commit"]),
                cap_usd=float(data["cap_usd"]),
                deadline_s=float(data["deadline_s"]),
                limits=limits,
                env={str(k): str(v) for k, v in data["env"].items()},
                claude_argv=[str(a) for a in data["claude_argv"]],
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LabError(f"the supervisor's inputs aren't usable ({exc.__class__.__name__})") from None


class Stopper:
    """The supervisor's ``should_stop``: why the run must stop now, or None."""

    def __init__(
        self,
        env: Mapping[str, str],
        slug: str,
        eid: str,
        deadline: datetime,
        stop_file: Path,
        terminated: Callable[[], bool],
        now: Callable[[], datetime],
    ) -> None:
        self.env, self.slug, self.eid, self.deadline = env, slug, eid, deadline
        self.stop_file, self.terminated, self.now = stop_file, terminated, now

    def __call__(self) -> str | None:
        if self.stop_file.exists():
            return STOPPED_BY_OWNER
        if self.terminated():
            return TERMINATED
        try:
            if broker.owner_veto(self.env, self.slug, self.eid) is not None:
                return STOPPED_BY_OWNER
        except broker.Refused:
            return VETO_UNREADABLE
        if self.now() >= self.deadline:
            return DEADLINE_PASSED
        return None


def fetch_weights(inputs: Inputs, should_stop: Callable[[], str | None]) -> None:
    """Fetch the experiment's model weights into the owner's cache before the gate opens (Task 8). Nothing yet."""


def _one_line(reason: object) -> str:
    return " ".join(str(reason).split())


def _not_run(reason: object) -> dict[str, Any]:
    return {"status": "not run", "reason": _one_line(reason)}


def _outcome(inputs: Inputs, path: Path, stopper: Stopper, deadline: datetime) -> dict[str, Any]:
    from scout.lab import runner

    vault = inputs.vault
    if (reason := stopper()) is not None:
        return _not_run(reason)
    try:
        fetch_weights(inputs, stopper)
    except LabError as exc:
        return _not_run(exc)
    if (reason := stopper()) is not None:
        return _not_run(reason)
    left = (deadline - config.now(vault)).total_seconds()
    if left <= 0:
        return _not_run(f"{DEADLINE_PASSED} before the script started")

    def on_started(marker: str, outside: str) -> None:
        current = runs.read(path)
        if current is None:
            raise LabError("the run's state record is gone; not run")
        try:
            runs.write(path, replace(current, marker=marker, outside=outside))
        except OSError as exc:
            raise LabError(f"cannot update the run's state record ({exc.__class__.__name__}); not run") from None

    try:
        out = runner.run_experiment(
            vault,
            inputs.slug,
            inputs.eid,
            inputs.argv,
            cap_usd=inputs.cap_usd,
            timeout_s=left,
            env=inputs.env,
            claude_argv=inputs.claude_argv,
            commit=inputs.commit,
            on_started=on_started,
            should_stop=stopper,
            **inputs.limits,
        )
    except runner.DiskFloorError:
        return _not_run(runner.FLOOR_REASON)
    except LabError as exc:
        return _not_run(exc)
    except Exception as exc:  # noqa: BLE001 - whatever failed, the record must say so, never stay "running"
        return _not_run(f"the supervisor failed: {exc.__class__.__name__}: {exc}")
    owner_stop = out.stopped in NOT_RUN_STOPS
    return {
        "status": "not run" if owner_stop else "ran",
        "reason": out.stopped if owner_stop else None,
        "exit": out.exit,
        "timed_out": out.timed_out or out.stopped == DEADLINE_PASSED,
        "stopped": out.stopped,
        "over_bytes": out.over_bytes,
        "over_processes": out.over_processes,
        "over_disk_floor": out.over_disk_floor,
        "over_memory": out.over_memory,
        "spent_usd": out.spent_usd,
        "stdout_tail": out.stdout_tail,
        "stderr_tail": out.stderr_tail,
    }


def supervise(inputs: Inputs, terminated: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Hold the run lock, write the record, run the experiment and record its outcome (the module docstring).
    Raises :class:`LabError` without writing a record when the run lock is held (another run of the experiment)."""
    from scout.lab import runner

    vault, slug, eid, env = inputs.vault, inputs.slug, inputs.eid, inputs.env
    path = runs.record_path(vault, slug, eid, env)
    stop_file = runs.stop_path(path)
    with runner.hold_run_lock(vault, slug, eid, env):
        started = config.now(vault)
        deadline = started + timedelta(seconds=inputs.deadline_s)
        record = runs.RunRecord(started.isoformat(), deadline.isoformat(), os.getpid())
        runs.write(path, record)
        stopper = Stopper(env, slug, eid, deadline, stop_file, terminated, lambda: config.now(vault))
        outcome = {**_outcome(inputs, path, stopper, deadline), "finished": config.now(vault).isoformat()}
        runs.write(path, replace(runs.read(path) or record, outcome=outcome))  # before the lock goes
        with contextlib.suppress(OSError):
            stop_file.unlink()
    return outcome


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m scout.lab.supervise <inputs.json>", file=sys.stderr)
        return 2
    path = Path(args[0])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"supervise: cannot read the inputs ({exc.__class__.__name__})", file=sys.stderr)
        return 1
    finally:
        with contextlib.suppress(OSError):
            path.unlink()
    terminated = False

    def on_term(_sig: int, _frame: Any) -> None:
        nonlocal terminated
        terminated = True

    signal.signal(signal.SIGTERM, on_term)
    try:
        supervise(Inputs.from_dict(data), lambda: terminated)
    except LabError as exc:
        print(f"supervise: {_one_line(exc)}", file=sys.stderr)
        return 1
    return 0


# --- starting one ----------------------------------------------------------------------------------------------


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _last_line(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return _one_line(text.rsplit("\n", 1)[-1].removeprefix("supervise: ")) if text else ""


def _new_private_file(path: Path, text: str = "") -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def start(
    vault: Path,
    slug: str,
    eid: str,
    argv: Sequence[str],
    *,
    commit: str | None,
    cap_usd: float,
    deadline_s: float,
    limits: broker.LabLimits,
    env: Mapping[str, str],
    claude_argv: Sequence[str] = ("claude",),
    command: Sequence[str] | None = None,
) -> None:
    """Start a supervisor for ``slug`` ``eid`` in a session of its own, outside every sandbox, and return once its
    state record exists (started, deadline, pid). It doesn't wait for the script's gate: a weights fetch comes first.

    ``limits`` are the owner's, with ``max_total_bytes`` already the plan's disk budget. ``command`` is what runs
    before the inputs file (default: this interpreter, ``-m scout.lab.supervise``). Raises :class:`LabError` when an
    earlier detached run of the experiment is alive or was never collected, when the supervisor exits before
    writing its record (its reason, from its log), or when no record appears within 30 s (the supervisor is killed).
    """
    path = runs.record_path(vault, slug, eid, env)
    runs.ensure_dir(env)
    old = runs.read(path)
    if old is not None and old.outcome is None:
        if runs.alive(vault, slug, eid, old, env):
            raise LabError(f"experiment {slug} {eid} is already running")
        raise LabError(
            f"an earlier detached run of {slug} {eid} stopped without a result; "
            f"`scoutctl lab experiment collect {slug} {eid}` first"
        )
    for stale in (path, runs.stop_path(path)):
        with contextlib.suppress(FileNotFoundError):
            stale.unlink()
    inputs = Inputs(
        vault=Path(vault),
        slug=slug,
        eid=eid,
        argv=list(argv),
        commit=commit,
        cap_usd=cap_usd,
        deadline_s=deadline_s,
        limits={k: int(getattr(limits, k)) for k in LIMIT_KEYS},
        env={k: env[k] for k in ENV_KEYS if env.get(k)},
        claude_argv=list(claude_argv),
    )
    inputs_file = runs.inputs_path(path)
    log = runs.log_path(path)
    _new_private_file(inputs_file, json.dumps(inputs.to_dict()))
    _new_private_file(log)
    cmd = list(command) if command else [sys.executable, "-m", "scout.lab.supervise"]
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(p for p in (_ENGINE_ROOT, child_env.get("PYTHONPATH")) if p)
    try:
        launched = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _LAUNCHER, str(log), *cmd, str(inputs_file)],
            env=child_env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=START_WAIT_S, check=False,
        )  # fmt: skip
        pid = int(launched.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        with contextlib.suppress(OSError):
            inputs_file.unlink()
        raise LabError("cannot start the experiment's supervisor; not run") from None
    deadline = time.monotonic() + START_WAIT_S
    while True:
        if runs.read(path) is not None:
            return
        if not _pid_exists(pid):
            if runs.read(path) is not None:
                return
            with contextlib.suppress(OSError):
                inputs_file.unlink()
            why = _last_line(log) or "no reason given"
            raise LabError(f"the experiment's supervisor stopped before it started: {why}")
        if time.monotonic() >= deadline:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                inputs_file.unlink()
            raise LabError(f"the experiment's supervisor wrote no record within {START_WAIT_S:.0f} s; not run")
        time.sleep(0.05)


if __name__ == "__main__":
    sys.exit(main())

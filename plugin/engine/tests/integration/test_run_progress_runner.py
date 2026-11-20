"""A hung claude behind a live runner shell is not progress (review of #701).

The real ``run-scout.sh`` and ``scout-lock.sh`` templates run a fake ``claude``
in a temporary vault linked to Agnes. ``scoutctl`` is a stub, except that
``progress watch`` is the real engine watcher, and its ``repo lease keepalive``
appends a line to the run log several times a second: the run log moves all the
time, as it did on a linked vault, whatever claude does. The reaper, ``scoutctl
health`` and the app must all read the watcher's line instead. Nothing here
starts a real session; the engine side of the line is
``tests/unit/test_run_progress.py``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout import health
from scout import run_progress as rp

from .test_lock_reaper_progress import (
    _detached,
    _env,
    _failures,
    _group_alive,
    _hold_lock,
    _kill_group,
    _lock,
    _script,
    _wait_for,
    _waiter,
    make_vault,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

STUB = f"""#!/bin/bash
case "$1 $2" in
    "agent-runtime exec")
        shift 2
        bin=""
        args=()
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --runtime) shift 2 ;;
                --bin) bin="$2"; shift 2 ;;
                *) args+=("$1"); shift ;;
            esac
        done
        exec "$bin" --permission-mode auto "${{args[@]}}" ;;
    "progress watch")
        [ -z "${{FAKE_WATCH_LOCKSTEP:-}}" ] || exec "{sys.executable}" "${{0%/*}}/lockstep-watch" "$@"
        exec "{sys.executable}" -m scout "$@" ;;
esac
case "$1 $2 $3" in
    "repo lease acquire") exit 0 ;;
    "repo lease keepalive")
        # The real keepalive logs each renewal to the run log; this one never stops.
        while :; do echo "lease keepalive: renewed run x's lease"; sleep 0.2; done ;;
esac
exit 0
"""

# FAKE_CLAUDE: hung (sleeps, does nothing) | working (starts a tool every half second) | stepping (moves for
# LOCKSTEP_WATCH: each move starts a new child before it ends the last, so one is always alive, then counts the
# move in claude-moves).
FAKE_CLAUDE = """#!/bin/bash
echo "$$" >> "{vault}/claude-pids"
case "${{FAKE_CLAUDE:-hung}}" in
    working) end=$(( $(date +%s) + ${{FAKE_CLAUDE_HOLD_S:-0}} ))
             while [ "$(date +%s)" -lt "$end" ]; do /bin/sleep 0.5; done ;;
    stepping) end=$(( $(date +%s) + ${{FAKE_CLAUDE_HOLD_S:-0}} )); moves=0; prev=""
              while [ "$(date +%s)" -lt "$end" ]; do
                  /bin/sleep 300 & cur=$!
                  if [ -n "$prev" ]; then kill "$prev" 2>/dev/null; wait "$prev" 2>/dev/null; fi
                  prev=$cur; moves=$((moves + 1))
                  echo "$moves" > "{vault}/claude-moves.tmp" && mv -f "{vault}/claude-moves.tmp" "{vault}/claude-moves"
                  /bin/sleep 0.2
              done
              [ -z "$prev" ] || kill "$prev" 2>/dev/null ;;
    *) exec /bin/sleep "${{FAKE_CLAUDE_HOLD_S:-0}}" ;;
esac
"""

# The real `scoutctl progress watch` (the CLI, the real ps, the real clocks) with one change: each sample waits,
# after its sleep, until the stepping claude has moved twice since the last sample. The move that ends the wait
# started its child after that sample, and a child that new is alive at this one, so every sample sees a new
# process list whatever the host's load. Without it a loaded runner could starve the fake for a few seconds while
# the watcher still sampled, and the watcher rightly reported a stall of 3 s against a 3 s ceiling.
LOCKSTEP_WATCH = """
import sys
import time
from pathlib import Path

from scout import cli, run_progress

args = sys.argv[1:]
opts = dict(zip(args[2::2], args[3::2], strict=False))  # progress watch --vault V --runner-pid P --log L
moves_file = Path(opts["--vault"]) / "claude-moves"
runner = int(opts["--runner-pid"])
real_watch = run_progress.watch


def moves() -> int:
    try:
        return int(moves_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0


def gated_sleep(secs: float) -> None:
    seen = moves()
    time.sleep(secs)
    deadline = time.monotonic() + 60  # a fake that stopped moving fails the test on its stall, never hangs it
    while moves() < seen + 2 and run_progress._pid_alive(runner) and time.monotonic() < deadline:
        time.sleep(0.05)


def watch(*a, **kw):
    return real_watch(*a, **kw, sleep=gated_sleep)


run_progress.watch = watch
sys.argv = ["scoutctl", *args]
cli.main()
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    base_vault = make_vault(tmp_path)
    _script(base_vault / "bin" / "scoutctl", STUB)
    _script(base_vault / "bin" / "claude", FAKE_CLAUDE.format(vault=base_vault))
    _script(base_vault / "bin" / "lockstep-watch", LOCKSTEP_WATCH)
    state = base_vault / ".scout-state"
    state.mkdir(exist_ok=True)
    (state / "repo-sync.json").write_text("{}\n", encoding="utf-8")  # linked: the keepalive runs
    return base_vault


@pytest.fixture
def holders() -> Iterator[list[int]]:
    started: list[int] = []
    yield started
    for pgid in started:
        _kill_group(pgid)


def _holder(vault: Path, **env: str) -> int:
    return _detached(str(vault / "run-scout.sh"), env=_env(SCOUT_PROGRESS_SAMPLE_SECS="1", **env))


def _beat(vault: Path) -> rp.Heartbeat | None:
    return rp.read_fresh(rp.heartbeat_path(vault))


def _run_log(vault: Path, holder: int) -> Path:
    _wait_for(lambda: _beat(vault) is not None, "the holder's progress line", 30)
    beat = _beat(vault)
    assert beat is not None and beat.runner_pid == holder
    return Path(beat.log)


def test_a_hung_claude_whose_log_keeps_moving_is_reaped(vault: Path, holders: list[int]) -> None:
    holder = _holder(vault, FAKE_CLAUDE="hung", FAKE_CLAUDE_HOLD_S="300")
    holders.append(holder)
    log = _run_log(vault, holder)
    _wait_for(lambda: (vault / "claude-pids").exists(), "the holder's claude")
    _wait_for(lambda: (_beat(vault) or rp.Heartbeat(1, 0, 0, 1, "")).stalled_s >= 4, "the stall to pass 4 s", 30)
    size = log.stat().st_size
    _wait_for(lambda: log.stat().st_size > size, "the keepalive to write the run log")
    assert time.time() - log.stat().st_mtime < 2, "the run log moves while claude is hung"

    # Run health calls it stuck on the reaper's ceiling: the same line, the same number.
    (finding,) = health.progress_findings(vault, now=datetime.now(UTC), env={"SCOUT_LOCK_MAX_AGE_SECS": "3"})
    assert finding.id == "session.no_progress" and finding.log == str(log)
    assert health.progress_findings(vault, now=datetime.now(UTC), env={"SCOUT_LOCK_MAX_AGE_SECS": "3600"}) == []

    result = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="3", SCOUT_LOCK_WAIT_SECS="10")

    assert result.returncode == 0, result.stderr
    _wait_for(lambda: not _group_alive(holder), "the hung run's group to be gone", 15)
    assert f"Reaped wedged session PID {holder} after " in _failures(vault)
    assert len((vault / "claude-pids").read_text(encoding="utf-8").split()) == 2, "the waiter ran after the reap"
    assert not _lock(vault).exists()
    assert not rp.heartbeat_path(vault).exists()


def test_a_slow_but_alive_claude_is_not_reaped(vault: Path, holders: list[int]) -> None:
    """A claude whose process list changes between every two samples is never stalled, and a waiter takes the
    watcher's word for it although the lock is older than its ceiling and the holder's own CPU barely moves.

    Nothing here depends on how fast the runner is: the watcher samples in lock-step with the fake
    (LOCKSTEP_WATCH), so its stall is exactly 0, not "small", on a loaded runner too."""
    holder = _holder(vault, FAKE_CLAUDE="stepping", FAKE_CLAUDE_HOLD_S="120", FAKE_WATCH_LOCKSTEP="1")
    holders.append(holder)
    _run_log(vault, holder)
    _wait_for(lambda: (vault / "claude-moves").exists(), "the holder's claude to move")
    first = _beat(vault)
    assert first is not None
    # The watcher has judged the claude's moves as progress (the mark moved on), and the lock's wall-clock age is
    # past the waiter's ceiling, so a waiter that judged by age would reap.
    _wait_for(lambda: (_beat(vault) or first).progress_at > first.progress_at, "the watcher to see progress", 30)
    _wait_for(lambda: time.time() - _lock(vault).stat().st_mtime > 4, "the lock to be older than the ceiling")
    beat = _beat(vault)
    assert beat is not None and beat.stalled_s == 0, beat

    result = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="3", SCOUT_LOCK_WAIT_SECS="6")

    assert result.returncode == 0, result.stderr
    assert _group_alive(holder)
    assert "Reaped" not in _failures(vault)
    assert _lock(vault).read_text(encoding="utf-8").strip() == str(holder)
    assert not (vault / ".scout-logs" / ".scout-session.lock.progress").exists(), "the waiter read the watcher's line"
    beat = _beat(vault)
    assert beat is not None and beat.stalled_s == 0, beat
    assert health.progress_findings(vault, now=datetime.now(UTC), env={"SCOUT_LOCK_MAX_AGE_SECS": "3"}) == []


def test_a_crashed_runner_takes_its_progress_line_with_it(vault: Path, holders: list[int]) -> None:
    holder = _holder(vault, FAKE_CLAUDE="hung", FAKE_CLAUDE_HOLD_S="300")
    holders.append(holder)
    _run_log(vault, holder)
    os.kill(holder, signal.SIGKILL)  # the shell only: no EXIT trap runs, its claude lives on

    _wait_for(lambda: not rp.heartbeat_path(vault).exists(), "the watcher to see its runner gone", 15)
    result = _waiter(vault, SCOUT_LOCK_WAIT_SECS="0", FAKE_CLAUDE_HOLD_S="0")
    assert result.returncode == 0, result.stderr
    failures = _failures(vault)
    assert f"Session PID {holder} (" in failures and "died without releasing the session lock" in failures
    assert not _lock(vault).exists()


def test_a_run_that_ends_leaves_no_line_and_no_watcher(vault: Path) -> None:
    result = subprocess.run(
        [str(vault / "run-scout.sh")],
        env=_env(SCOUT_PROGRESS_SAMPLE_SECS="1", FAKE_CLAUDE="working", FAKE_CLAUDE_HOLD_S="2"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not rp.heartbeat_path(vault).exists()
    assert not list((vault / ".scout-logs").glob(".scout-session.lock*"))
    ps = subprocess.run(["ps", "-A", "-ww", "-o", "command="], capture_output=True, text=True, check=True).stdout
    assert f"progress watch --vault {vault} " not in ps


@pytest.mark.parametrize("plant", ["fifo", "forged_future", "forged_link"])
def test_a_planted_progress_line_neither_hangs_a_waiter_nor_vouches_for_the_holder(
    vault: Path, holders: list[int], plant: str
) -> None:
    """A run can write .scout-logs. A FIFO at the line must not hang the next run's waiter, and a forged line
    (a far-future mtime, or a link to a line elsewhere) must not stand in for the holder's progress: the waiter
    falls back to sampling the holder itself, as with no watcher at all."""
    pid = _detached("exec sleep 300")
    holders.append(pid)
    _hold_lock(vault, pid, held_for_s=60)
    line = rp.heartbeat_path(vault)
    forged = f"1 {pid} 1 0 999999999 /v/forged.log\n"
    if plant == "fifo":
        os.mkfifo(line)
    elif plant == "forged_future":
        line.write_text(forged, encoding="utf-8")
        later = time.time() + 86400
        os.utime(line, (later, later))
    else:
        elsewhere = vault / "forged"
        elsewhere.write_text(forged, encoding="utf-8")
        line.symlink_to(elsewhere)

    started = time.monotonic()
    result = _waiter(vault, SCOUT_LOCK_WAIT_SECS="0")

    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 30, "the waiter must not block on the planted file"
    sample = (vault / ".scout-logs" / ".scout-session.lock.progress").read_text(encoding="utf-8").split()
    assert sample[0] == str(pid), "the waiter sampled the holder itself: the planted line was no answer"

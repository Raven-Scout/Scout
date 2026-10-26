"""The lock reaper judges a holder wedged by lack of progress, never by age (#466).

The reaper used to compare the lock's wall-clock age (``now - mtime(lock)``) to
a 7200 s ceiling, so time the host spent asleep counted as wedged time. On a
laptop, a session started just before the lid closed was reaped on the first
slot after wake while it was alive and working (two incidents in #466).

Now every waiter samples the holder's session (the CPU time of its process
group and tree, its process list, its run log's mtime) into
``.scout-session.lock.progress``. The stall only grows while none of those move,
and only by time on a clock that stops while the host sleeps. These tests use
fakes throughout: detached ``sleep`` and ``python3`` holders, a fake ``claude``
and a stubbed retry wrapper. Nothing here starts a real session.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUN_TIMEOUT_S = 60
EIGHT_HOURS_S = 8 * 3600

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    text = (TEMPLATES / "run-scout.sh.tmpl").read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(v),
        "{{CLAUDE_BIN}}": str(v / "bin" / "claude"),
        "{{SCOUTCTL_BIN}}": "/nonexistent/scoutctl",  # the connector-health roll-up is skipped
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text
    _script(v / "run-scout.sh", text)
    lock_lib = (TEMPLATES / "scripts" / "scout-lock.sh.tmpl").read_text(encoding="utf-8")
    _script(v / "scripts" / "scout-lock.sh", lock_lib.replace("{{INSTANCE_NAME}}", "Scout"))
    # The retry wrapper is called as `claude-with-retry.sh LOG_FILE CLAUDE_BIN args…`;
    # the stub runs the (fake) claude as a child, as the real one does.
    _script(v / "scripts" / "claude-with-retry.sh", '#!/bin/bash\nshift\n"$@"\n')
    # A fake `claude`: records its PID, then holds for FAKE_CLAUDE_HOLD_S.
    _script(
        v / "bin" / "claude",
        f'#!/bin/bash\necho "$$" >> "{v}/claude-pids"\nsleep "${{FAKE_CLAUDE_HOLD_S:-0}}"\n',
    )
    return v


def _lock(vault: Path) -> Path:
    return vault / ".scout-logs" / ".scout-session.lock"


def _failures(vault: Path) -> str:
    path = vault / ".scout-logs" / "failures.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _env(**extra: str) -> dict[str, str]:
    return {**os.environ, "SCOUT_FORCE_MODE": "morning-briefing", "SCOUT_LOCK_POLL_SECS": "1", **extra}


def _waiter(vault: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(vault / "run-scout.sh")], env=_env(**env), capture_output=True, text=True, timeout=RUN_TIMEOUT_S
    )


def _detached(cmd: str, env: dict[str, str] | None = None) -> int:
    """Start ``cmd`` as its own process-group leader, reparented to init, the
    way launchd starts a runner. Its exit is reaped at once: an unreaped child
    would stay a zombie that every liveness check calls alive."""
    out = subprocess.run(
        ["bash", "-c", f"set -m; {cmd} </dev/null >/dev/null 2>&1 & echo $!"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return int(out.stdout.strip())


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_for(predicate, what: str, timeout_s: float = 20) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.1)


def _ps(pid: int, field: str) -> str:
    out = subprocess.run(["ps", "-ww", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip()


def _cpu_cs(pid: int) -> int:
    """``pid``'s CPU time in centiseconds, read as scout-lock.sh reads it:
    ``ps -o time=`` is [[dd-]hh:]mm:ss, with hundredths on macOS only."""
    days, _, clock = _ps(pid, "time").rpartition("-")
    secs = 0.0
    for part in clock.split(":"):
        secs = secs * 60 + float(part)
    return int((int(days or 0) * 86400 + secs) * 100 + 0.5)


def _fake_claudes(vault: Path) -> list[int]:
    """PIDs of the live fake `claude --name scout-…` sessions. They run as
    `/bin/bash <vault>/bin/claude …`; the retry wrapper's command line names
    the fake too, so only a process whose script IS the fake counts."""
    ps = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,command="], capture_output=True, text=True, check=True)
    found = []
    for line in ps.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].startswith(f"{vault}/bin/claude ") and " --name scout-" in parts[2]:
            found.append(int(parts[0]))
    return found


def _hold_lock(vault: Path, pid: int, *, held_for_s: int) -> int:
    """Point the lock at ``pid``, written ``held_for_s`` ago. Returns its mtime."""
    lock = _lock(vault)
    lock.write_text(f"{pid}\n", encoding="utf-8")
    written = int(time.time()) - held_for_s
    os.utime(lock, (written, written))
    return written


def _awake_now() -> int:
    """The lock's awake clock, as scout-lock.sh reads it."""
    clock = getattr(time, "CLOCK_UPTIME_RAW", time.CLOCK_MONOTONIC)
    return int(time.clock_gettime(clock))


@pytest.fixture
def holders() -> Iterator[list[int]]:
    started: list[int] = []
    yield started
    for pgid in started:
        _kill_group(pgid)


# --- sleep is not wedged time --------------------------------------------------


@pytest.mark.parametrize(("awake_since_last_sample_s", "reaped"), [(0, False), (60, True)])
def test_wall_clock_age_never_reaps_only_awake_stall_does(
    vault: Path, holders: list[int], awake_since_last_sample_s: int, reaped: bool
) -> None:
    """The issue's first incident: a holder sampled just before the host slept
    8 h, then sampled again on wake. Its lock is 8 h old and it made no progress
    since the last sample, but the host was asleep for all of it, so it is not
    wedged. The control: the same holder with 60 s of AWAKE time since the
    last sample is past the 30 s ceiling and reaped. The old code reaped both,
    on the lock's age alone. The ceiling leaves a loaded runner 30 s between
    the two samples (#498)."""
    pid = _detached("exec sleep 300")
    holders.append(pid)
    _hold_lock(vault, pid, held_for_s=EIGHT_HOURS_S)
    # On Linux the awake clock is CLOCK_MONOTONIC, about the host's uptime, and
    # a fresh CI VM can be younger than the gap. Moved back past zero, the
    # sample would be rejected as corrupt and taken as a new baseline.
    _wait_for(
        lambda: _awake_now() > awake_since_last_sample_s,
        "the awake clock to pass the gap",
        timeout_s=awake_since_last_sample_s + 5,
    )
    # The sample taken before the host slept, written by a waiter itself (a
    # baseline), then moved back by the awake time that passed since.
    baseline = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="30", SCOUT_LOCK_WAIT_SECS="0")
    assert baseline.returncode == 0, baseline.stderr
    progress = vault / ".scout-logs" / ".scout-session.lock.progress"
    sample = progress.read_text(encoding="utf-8").split()
    assert sample[0] == str(pid) and sample[-1] == "0", sample
    sample[6] = str(int(sample[6]) - awake_since_last_sample_s)
    progress.write_text(" ".join(sample) + "\n", encoding="utf-8")

    result = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="30", SCOUT_LOCK_WAIT_SECS="0")

    assert result.returncode == 0, result.stderr
    if reaped:
        _wait_for(lambda: not _group_alive(pid), "the stalled holder to be reaped")
        assert f"Reaped wedged session PID {pid} after " in _failures(vault)
        assert (vault / "claude-pids").exists(), "the run must proceed after the takeover"
    else:
        assert _group_alive(pid), "a holder that was asleep, not stalled, must not be reaped"
        assert "Reaped" not in _failures(vault)
        assert not (vault / "claude-pids").exists()
        assert _lock(vault).read_text(encoding="utf-8").strip() == str(pid)


def test_the_first_sample_of_a_holder_is_a_baseline(vault: Path, holders: list[int]) -> None:
    """With no earlier sample nothing is known about progress, so even a lock
    held for 8 h is not reaped on one look: the waiter records a baseline."""
    pid = _detached("exec sleep 300")
    holders.append(pid)
    _hold_lock(vault, pid, held_for_s=EIGHT_HOURS_S)

    result = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="60", SCOUT_LOCK_WAIT_SECS="0")

    assert result.returncode == 0, result.stderr
    assert _group_alive(pid)
    assert "Reaped" not in _failures(vault)
    sample = (vault / ".scout-logs" / ".scout-session.lock.progress").read_text(encoding="utf-8").split()
    assert sample[0] == str(pid) and sample[-1] == "0", sample
    assert sample[5] == "awake", f"the stall must be timed on the awake clock, not wall time: {sample}"
    assert int(sample[6]) == pytest.approx(_awake_now(), abs=5), sample
    assert _lock(vault).stat().st_mtime == pytest.approx(time.time() - EIGHT_HOURS_S, abs=5), (
        "the lock's mtime is the time it was taken (health.py reads it): a waiter must never touch it"
    )


# --- progress resets the stall ---------------------------------------------------


def test_an_idle_holder_that_resumes_working_is_not_reaped(vault: Path, holders: list[int]) -> None:
    """A holder idle for a long wall-clock time (stopped, its lock 8 h old) that
    starts burning CPU again is progressing: the stall resets, so it is not
    reaped, although without that progress its stall would be past the ceiling.

    Nothing here assumes how fast the runner is (#498). Each waiter takes exactly
    one sample, after the test has waited for the condition that sample must
    see. The ceiling is each waiter's own setting: the idle samples run under a
    ceiling they can never reach, and the sample after the resume runs under one
    the stall is already past. So a slow sample cannot reap the holder while it
    is idle, and only the CPU it burned keeps it alive afterwards. Before, a
    fixed 5 s ceiling gave the resumed holder about 2 s to burn 1 CPU-second,
    and a loaded CI runner sometimes missed it."""
    env = {"SCOUT_LOCK_PROGRESS_CPU_SECS": "1", "SCOUT_LOCK_WAIT_SECS": "0"}
    progress = vault / ".scout-logs" / ".scout-session.lock.progress"
    pid = _detached(f"exec {sys.executable} -c 'while True: pass'")
    holders.append(pid)
    # Stop it only once it runs python: a STOP that lands before the exec is lost.
    _wait_for(lambda: "python" in _ps(pid, "command"), "the holder to exec python")
    os.kill(pid, signal.SIGSTOP)
    _wait_for(lambda: _ps(pid, "stat").startswith("T"), "the holder to stop")
    _hold_lock(vault, pid, held_for_s=EIGHT_HOURS_S)

    # Idle: a baseline, then 2 s of awake time later a sample whose stall has grown.
    baseline = _waiter(vault, **env, SCOUT_LOCK_MAX_AGE_SECS="3600")
    assert baseline.returncode == 0, baseline.stderr
    baseline_sampled = int(progress.read_text(encoding="utf-8").split()[6])
    _wait_for(lambda: _awake_now() >= baseline_sampled + 2, "2 s of awake time")
    first = _waiter(vault, **env, SCOUT_LOCK_MAX_AGE_SECS="3600")
    assert first.returncode == 0, first.stderr
    mark = progress.read_text(encoding="utf-8").split()
    mark_cpu, stalled = int(mark[2]), int(mark[-1])
    assert stalled >= 2, "a stopped holder makes no progress, so its stall must grow"

    # Working again: once it has burned the 1 CPU-second that counts as
    # progress, a waiter whose ceiling the stall is already past samples it.
    os.kill(pid, signal.SIGCONT)
    _wait_for(lambda: _cpu_cs(pid) >= mark_cpu + 100, "the resumed holder to burn 1 CPU-second", timeout_s=120)
    second = _waiter(vault, **env, SCOUT_LOCK_MAX_AGE_SECS=str(stalled - 1))

    assert second.returncode == 0, second.stderr
    assert _group_alive(pid), "a holder whose CPU time advances is alive and working"
    assert "Reaped" not in _failures(vault)
    sample = progress.read_text(encoding="utf-8").split()
    assert sample[-1] == "0", f"CPU progress must reset the stall: {sample}"
    assert not (vault / "claude-pids").exists()
    assert _lock(vault).read_text(encoding="utf-8").strip() == str(pid)


def test_a_holder_whose_run_log_advances_is_not_reaped(vault: Path, holders: list[int]) -> None:
    """A holder that burns no CPU but keeps writing its run log is progressing.
    The holder names its log in ``.scout-session.lock.owner`` when it takes the lock."""
    pid = _detached("exec sleep 300")
    holders.append(pid)
    _hold_lock(vault, pid, held_for_s=EIGHT_HOURS_S)
    run_log = vault / ".scout-logs" / "scout-holder.log"
    run_log.write_text("=== Scout run starting ===\n", encoding="utf-8")
    (vault / ".scout-logs" / ".scout-session.lock.owner").write_text(f"{run_log}\n", encoding="utf-8")

    stop = threading.Event()

    def write_log() -> None:
        stamp = int(time.time())
        while not stop.is_set():
            stamp += 10  # strictly increasing whole seconds, whatever stat's resolution
            os.utime(run_log, (stamp, stamp))
            stop.wait(0.3)

    writer = threading.Thread(target=write_log, daemon=True)
    writer.start()
    try:
        result = _waiter(vault, SCOUT_LOCK_MAX_AGE_SECS="3", SCOUT_LOCK_WAIT_SECS="7")
    finally:
        stop.set()
        writer.join()

    assert result.returncode == 0, result.stderr
    assert _group_alive(pid)
    assert "Reaped" not in _failures(vault)


# --- no progress past the ceiling: the whole session is reaped (#466's test) ---


def test_a_stopped_claude_past_the_ceiling_is_reaped_with_its_whole_group(vault: Path, holders: list[int]) -> None:
    """The scenario from #466: start a run, ``kill -STOP`` its claude, and let
    the next slot find it past the ceiling. The reap takes the runner's whole
    process group, the new run starts, and exactly one ``claude --name scout-``
    is alive afterwards: the new run's."""
    holder = _detached(str(vault / "run-scout.sh"), env=_env(FAKE_CLAUDE_HOLD_S="300"))
    holders.append(holder)
    pids_file = vault / "claude-pids"
    _wait_for(lambda: pids_file.exists() and pids_file.read_text(encoding="utf-8").strip() != "", "the holder's claude")
    old_claude = int(pids_file.read_text(encoding="utf-8").split()[0])
    assert _lock(vault).read_text(encoding="utf-8").strip() == str(holder)
    os.kill(old_claude, signal.SIGSTOP)

    reaper = subprocess.Popen(
        [str(vault / "run-scout.sh")],
        env=_env(FAKE_CLAUDE_HOLD_S="30", SCOUT_LOCK_MAX_AGE_SECS="2", SCOUT_LOCK_WAIT_SECS="30"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    holders.append(reaper.pid)
    try:
        _wait_for(lambda: len(pids_file.read_text(encoding="utf-8").split()) >= 2, "the new run's claude", 40)
        new_claude = int(pids_file.read_text(encoding="utf-8").split()[1])

        _wait_for(lambda: not _group_alive(holder), "the reaped run's group to be gone", 10)
        assert _fake_claudes(vault) == [new_claude], "exactly one claude --name scout- may survive: the new run's"

        failures = _failures(vault)
        assert f"Reaped wedged session PID {holder} after " in failures
        assert "awake with no progress" in failures
        signalled = failures.split("signalled PIDs ", 1)[1].split()[0].split(",")
        assert str(holder) in signalled and str(old_claude) in signalled, failures
        assert _lock(vault).read_text(encoding="utf-8").strip() == str(reaper.pid)
    finally:
        _kill_group(reaper.pid)
        reaper.wait(timeout=10)

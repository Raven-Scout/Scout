"""Integration test for the concurrency-lock guard in templates/run-scout.sh.tmpl.

Renders the runner template with a stubbed claude-with-retry.sh and drives the
lock-guard branches with real holder processes.

Regression context: the guard was liveness-only (`kill -0 $PID`), so a session
that was alive but not progressing — host slept mid-run, wedged network call —
held the lock indefinitely and every subsequent scheduled slot silently exited
0 (41h and 21h outages observed, invisible to every audit surface because the
skip path is exit 0 and last-fire.json still records the fire). The guard now
bounds the lock: a live holder that makes no progress for SCOUT_LOCK_MAX_AGE_SECS
of awake time is reaped (TERM, grace, KILL), the reap is recorded in
failures.log, and the new session takes over the lock. Progress, not lock age,
decides (#466): see test_lock_reaper_progress.py.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "run-scout.sh.tmpl"
TEMPLATES = REPO_ROOT / "templates"

# The reap path is TERM → 5s grace → KILL, so give runs comfortable headroom.
RUN_TIMEOUT_S = 30


def _render(tmpl: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached — the retry wrapper is stubbed
        "{{SCOUTCTL_BIN}}": "/nonexistent/scoutctl",  # the connector-health roll-up is skipped
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    out = scout_dir / "run-scout.sh"
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    _render_lock_lib(scout_dir)
    return out


def _render_lock_lib(scout_dir: Path) -> None:
    """scripts/scout-lock.sh, which every runner sources (#218)."""
    text = (
        (TEMPLATES / "scripts" / "scout-lock.sh.tmpl").read_text(encoding="utf-8").replace("{{INSTANCE_NAME}}", "Scout")
    )
    out = scout_dir / "scripts" / "scout-lock.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def _stub_retry_wrapper(scout_dir: Path) -> Path:
    """Stand-in for scripts/claude-with-retry.sh: records that the session
    body was reached (i.e. the lock was acquired), then succeeds."""
    marker = scout_dir / "session-ran.marker"
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    return marker


def _run(script: Path, *, max_age_s: int, wait_s: int = 0) -> subprocess.CompletedProcess:
    # By default no wait for a busy lock: one try at it (the wait is covered in
    # test_scout_lock.py). A reap needs a wait: wedged is judged across
    # progress samples, and a holder's first sample is only a baseline (#466).
    env = {
        **os.environ,
        "SCOUT_LOCK_MAX_AGE_SECS": str(max_age_s),
        "SCOUT_LOCK_WAIT_SECS": str(wait_s),
        "SCOUT_LOCK_POLL_SECS": "1",
    }
    return subprocess.run(
        [str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _run_log(scout_dir: Path) -> str:
    logs = sorted((scout_dir / ".scout-logs").glob("scout-*.log"))
    assert logs, "runner should always create its per-run log"
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def test_fresh_alive_holder_still_skips(tmp_path: Path) -> None:
    """A live holder that has not stalled keeps the original skip behavior."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    holder = subprocess.Popen(["sleep", "300"])
    try:
        lock = log_dir / ".scout-session.lock"
        lock.write_text(f"{holder.pid}\n", encoding="utf-8")  # mtime = now → fresh

        result = _run(script, max_age_s=3600)

        assert result.returncode == 0, result.stderr
        assert "Another Scout session running" in _run_log(scout_dir)
        assert not marker.exists(), "a fresh live holder must not be taken over"
        assert holder.poll() is None, "a fresh live holder must not be killed"
        assert lock.read_text(encoding="utf-8").strip() == str(holder.pid)
    finally:
        holder.kill()
        holder.wait()


def test_wedged_holder_is_reaped_and_lock_taken_over(tmp_path: Path) -> None:
    """A live holder that makes no progress past the ceiling is killed, logged,
    and replaced. Wedged is judged by progress samples (#466), so the waiter
    polls: its first sample is a baseline, later ones see the stall grow."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    # Detached and leading its own group, as launchd starts a runner; reparented
    # to init, so its death is reaped at once instead of leaving a zombie.
    out = subprocess.run(
        ["bash", "-c", "set -m; sleep 300 </dev/null >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    holder = int(out.stdout.strip())
    try:
        lock = log_dir / ".scout-session.lock"
        lock.write_text(f"{holder}\n", encoding="utf-8")

        result = _run(script, max_age_s=2, wait_s=20)

        assert result.returncode == 0, result.stderr
        log = _run_log(scout_dir)
        assert "Reaping wedged Scout session" in log
        failures = (log_dir / "failures.log").read_text(encoding="utf-8")
        assert f"Reaped wedged session PID {holder} after " in failures, (
            "the reap must be visible to audit surfaces, not just the per-run log"
        )
        assert f"signalled PIDs {holder}" in failures, "the reap must say which PIDs it signalled"
        assert marker.exists(), "the new session must proceed after the takeover"
        deadline = time.monotonic() + 10
        while True:
            try:
                os.kill(holder, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, "the wedged holder must be killed"
            time.sleep(0.1)
        assert not lock.exists(), "the takeover session must clean up its own lock on exit"
        assert not (log_dir / ".scout-session.lock.progress").exists(), "progress samples go with the lock"
    finally:
        try:
            os.killpg(holder, 9)
        except ProcessLookupError:
            pass


def _live_pids(*, pgid: int | None = None, ppid: int | None = None) -> set[int]:
    """Non-zombie processes in a group, or with a parent; one `ps -A` table, as the lock reads it."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[3].startswith("Z"):
            continue
        if (pgid is not None and int(fields[2]) == pgid) or (ppid is not None and int(fields[1]) == ppid):
            pids.add(int(fields[0]))
    return pids


def _wedge(log_dir: Path, holder: subprocess.Popen) -> Path:
    lock = log_dir / ".scout-session.lock"
    lock.write_text(f"{holder.pid}\n", encoding="utf-8")
    held_since = time.time() - 120
    os.utime(lock, (held_since, held_since))  # lock written 120s ago
    return lock


def _wait_for(predicate, timeout_s: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for the holder's session to start"
        time.sleep(0.05)


def test_a_reaped_holder_takes_its_whole_process_group_with_it(tmp_path: Path) -> None:
    """The lock's PID is run-<mode>.sh, which launchd starts as a group leader; the
    claude session runs below it. Killing the bare PID left the session writing
    the vault after its lock was handed on. The child here ignores TERM, as a
    session busy in a tool call effectively does, so only the KILL to the group
    reaches it."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    holder = subprocess.Popen(
        # No loop: a session that keeps starting processes is progressing (#466).
        ["bash", "-c", "bash -c 'trap \"\" TERM; sleep 300' & wait"],
        start_new_session=True,  # its own process group, led by the holder
    )
    try:
        _wait_for(lambda: len(_live_pids(pgid=holder.pid)) >= 2)
        _wedge(log_dir, holder)

        result = _run(script, max_age_s=2, wait_s=20)

        assert result.returncode == 0, result.stderr
        assert marker.exists(), "the new session must proceed once the old one is gone"
        assert _live_pids(pgid=holder.pid) == set(), "a process of the reaped session survived"
        assert "Reap FAILED" not in _run_log(scout_dir)
    finally:
        subprocess.run(["kill", "-KILL", "--", f"-{holder.pid}"], capture_output=True)
        holder.wait()


def test_a_holder_without_its_own_group_is_reaped_with_its_descendants_only(tmp_path: Path) -> None:
    """A hand-started run shares its shell's process group, so the group is never
    signalled: the holder and everything below it are, and the group's other
    members (here, the test itself) are left alone."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    holder = subprocess.Popen(["bash", "-c", "bash -c 'sleep 300 & wait' & wait"])  # in our group
    try:
        _wait_for(lambda: any(_live_pids(ppid=child) for child in _live_pids(ppid=holder.pid)))
        middle = _live_pids(ppid=holder.pid)
        grandchildren = {g for m in middle for g in _live_pids(ppid=m)}
        _wedge(log_dir, holder)

        result = _run(script, max_age_s=2, wait_s=20)

        assert result.returncode == 0, result.stderr
        assert marker.exists(), "the new session must proceed once the old one is gone"
        assert holder.wait(timeout=10) != 0, "the holder must be killed"
        live = _live_pids(pgid=os.getpgid(0))
        assert not (middle | grandchildren) & live, "a descendant of the reaped holder survived"
        assert os.getpid() in live
    finally:
        holder.kill()
        holder.wait()


def test_dead_holder_stale_lock_still_removed(tmp_path: Path) -> None:
    """Guard: the pre-existing crashed-holder branch keeps working."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait()  # reaped → kill -0 fails for this PID
    lock = log_dir / ".scout-session.lock"
    lock.write_text(f"{dead.pid}\n", encoding="utf-8")

    result = _run(script, max_age_s=3600)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), "a crashed holder's lock must not block the run"
    assert not lock.exists()

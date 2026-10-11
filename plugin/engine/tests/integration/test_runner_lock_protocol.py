"""The runners' vault-lock protocol matches scoutctl's (#331).

``run-scout.sh``, ``run-dreaming.sh`` and ``run-research.sh`` take
``.scout-logs/.scout-session.lock`` in shell, and ``scoutctl`` takes the same
file through ``scout/scripts/bootstrap_lock.py``. These tests render the real
runner templates and drive them against crashed holders, live Python
``O_EXCL`` holders, and each other. The lock must be created atomically, a
crashed holder's lock recovered only under the ``<lock>.takeover`` guard, an
empty lock treated as busy, and the EXIT trap must leave other holders' locks
alone. The age-bounded reap itself is covered in ``test_run_scout_lock.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")
ENGINE_ROOT = Path(__file__).resolve().parents[2]

RUN_TIMEOUT_S = 30
RACE_ROUNDS = 20


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
    out = scout_dir / tmpl.name.removesuffix(".tmpl")
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


def _vault(tmp_path: Path, runner: str = "run-scout.sh.tmpl") -> tuple[Path, Path, Path]:
    """Return (rendered runner, log dir, lock path) for a fresh vault."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATES / runner, scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    return script, log_dir, log_dir / ".scout-session.lock"


def _stub_session(scout_dir: Path, body: str) -> None:
    """Replace scripts/claude-with-retry.sh — the session body — with `body`."""
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)


def _stub_overlap_session(scout_dir: Path, hold_s: float = 0.2) -> tuple[Path, Path]:
    """A session that records each run and flags any two that overlap."""
    ran = scout_dir / "ran.log"
    overlap = scout_dir / "overlap.log"
    in_session = scout_dir / "in-session"
    _stub_session(
        scout_dir,
        f'if mkdir "{in_session}" 2>/dev/null; then\n'
        f'    echo "$PPID" >> "{ran}"\n'
        f"    sleep {hold_s}\n"
        f'    rmdir "{in_session}"\n'
        "else\n"
        f'    echo "$PPID" >> "{overlap}"\n'
        "fi",
    )
    return ran, overlap


def _popen(script: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [str(script)],
        env={**os.environ, "SCOUT_LOCK_MAX_AGE_SECS": "3600"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run(script: Path) -> subprocess.CompletedProcess:
    proc = _popen(script)
    out, err = proc.communicate(timeout=RUN_TIMEOUT_S)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _dead_pid() -> int:
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait()  # reaped → kill -0 fails for this PID
    return proc.pid


def _run_log(log_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(log_dir.glob("*.log")))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


# --- static -------------------------------------------------------------------


def _lock_block(runner: str) -> str:
    text = (TEMPLATES / runner).read_text(encoding="utf-8")
    start = text.index("# Concurrency guard")
    end = text.index("trap _scout_release_lock EXIT", start)
    return text[start:end]


def test_all_runners_share_one_lock_block() -> None:
    """The three runners carry the same block; a fix to one must reach all."""
    blocks = {runner: _lock_block(runner) for runner in RUNNERS}
    assert len(set(blocks.values())) == 1, "the runners' lock blocks have drifted apart"


# --- deterministic behaviour, every runner ------------------------------------


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("contents", ["", "   \n", "not-a-pid\n"])
def test_empty_or_unparseable_lock_is_busy(tmp_path: Path, runner: str, contents: str) -> None:
    """An empty lock is a competitor between its O_EXCL create and its PID
    write — never a stale lock to remove (#331 race 4)."""
    script, log_dir, lock = _vault(tmp_path, runner)
    ran, _ = _stub_overlap_session(script.parent)
    lock.write_text(contents, encoding="utf-8")

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert not ran.exists(), "a runner must not start while the lock is mid-write"
    assert lock.exists() and lock.read_text(encoding="utf-8") == contents
    assert "Another Scout session running" in _run_log(log_dir)


@pytest.mark.parametrize("runner", RUNNERS)
def test_exit_trap_leaves_a_taken_over_lock_alone(tmp_path: Path, runner: str) -> None:
    """If the lock was reaped and taken over while this runner ran, its EXIT
    trap must not delete the new holder's lock (#331 race 3)."""
    script, _, lock = _vault(tmp_path, runner)
    holder = subprocess.Popen(["sleep", "300"])
    try:
        # The session body plays the reaper: it replaces the lock with its own.
        _stub_session(script.parent, f'rm -f "{lock}"; echo {holder.pid} > "{lock}"')

        result = _run(script)

        assert result.returncode == 0, result.stderr
        assert lock.exists(), "the runner deleted a lock it no longer held"
        assert lock.read_text(encoding="utf-8").strip() == str(holder.pid)
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.parametrize("runner", RUNNERS)
def test_runner_releases_its_own_lock(tmp_path: Path, runner: str) -> None:
    script, _, lock = _vault(tmp_path, runner)
    ran, _ = _stub_overlap_session(script.parent, hold_s=0)

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert _lines(ran), "an unlocked vault must run"
    assert not lock.exists()
    assert not lock.with_name(lock.name + ".takeover").exists()


@pytest.mark.parametrize("runner", RUNNERS)
def test_live_python_holder_is_skipped_and_not_overwritten(tmp_path: Path, runner: str) -> None:
    """A lock scoutctl took (bootstrap_lock.acquire_lock) blocks the runner,
    and the runner leaves its contents untouched."""
    script, log_dir, lock = _vault(tmp_path, runner)
    ran, _ = _stub_overlap_session(script.parent)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path\n"
            "from scout.scripts.bootstrap_lock import acquire_lock\n"
            "acquire_lock(Path(sys.argv[1])); print('held', flush=True); time.sleep(300)",
            str(lock),
        ],
        cwd=ENGINE_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"

        result = _run(script)

        assert result.returncode == 0, result.stderr
        assert not ran.exists()
        assert lock.read_text(encoding="utf-8") == str(holder.pid)
        assert f"Another Scout session running (PID {holder.pid})" in _run_log(log_dir)
    finally:
        holder.kill()
        holder.wait()


# --- the takeover guard -------------------------------------------------------


def test_stale_lock_is_left_alone_while_another_process_recovers_it(tmp_path: Path) -> None:
    """A fresh <lock>.takeover guard means someone else is mid-recovery: the
    runner skips instead of unlinking the lock out from under them."""
    script, _, lock = _vault(tmp_path)
    ran, _ = _stub_overlap_session(script.parent)
    dead = _dead_pid()
    lock.write_text(str(dead), encoding="utf-8")
    guard = lock.with_name(lock.name + ".takeover")
    guard.touch()

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert not ran.exists()
    assert lock.read_text(encoding="utf-8") == str(dead)
    assert guard.exists(), "a live recoverer's guard is not ours to clear"


def test_abandoned_guard_is_cleared_and_stale_lock_recovered(tmp_path: Path) -> None:
    """A guard older than 30 s was left by a process that died mid-recovery."""
    script, _, lock = _vault(tmp_path)
    ran, _ = _stub_overlap_session(script.parent, hold_s=0)
    lock.write_text(str(_dead_pid()), encoding="utf-8")
    guard = lock.with_name(lock.name + ".takeover")
    guard.touch()
    old = time.time() - 120
    os.utime(guard, (old, old))

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert _lines(ran), "the crashed holder's lock must not block the run"
    assert not lock.exists()
    assert not guard.exists()


def test_wedged_holder_is_not_reaped_while_another_process_holds_the_guard(tmp_path: Path) -> None:
    """The age-bounded reap runs under the same guard as stale recovery."""
    script, log_dir, lock = _vault(tmp_path)
    ran, _ = _stub_overlap_session(script.parent)
    holder = subprocess.Popen(["sleep", "300"])
    try:
        lock.write_text(str(holder.pid), encoding="utf-8")
        old = time.time() - 120
        os.utime(lock, (old, old))
        lock.with_name(lock.name + ".takeover").touch()

        proc = subprocess.run(
            [str(script)],
            env={**os.environ, "SCOUT_LOCK_MAX_AGE_SECS": "30"},
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )

        assert proc.returncode == 0, proc.stderr
        assert holder.poll() is None, "the holder must not be reaped without the guard"
        assert not ran.exists()
        assert lock.read_text(encoding="utf-8") == str(holder.pid)
        assert not (log_dir / "failures.log").exists()
    finally:
        holder.kill()
        holder.wait()


# --- races --------------------------------------------------------------------


def test_two_runners_racing_one_stale_lock_never_both_run(tmp_path: Path) -> None:
    """Two runners recovering the same crashed holder's lock: exactly one runs
    (#331 races 1 and 2)."""
    script, _, lock = _vault(tmp_path)
    ran, overlap = _stub_overlap_session(script.parent)
    for _ in range(RACE_ROUNDS):
        lock.write_text(f"{_dead_pid()}\n", encoding="utf-8")
        before = len(_lines(ran))
        procs = [_popen(script), _popen(script)]
        for proc in procs:
            _, err = proc.communicate(timeout=RUN_TIMEOUT_S)
            assert proc.returncode == 0, err

        assert not _lines(overlap), "two runners held the lock at once"
        assert len(_lines(ran)) == before + 1, "exactly one racer must run"
        assert not lock.exists()


_PY_RACER = """
import os, sys, time
from pathlib import Path
from scout.scripts.bootstrap_lock import LockBusyError, acquire_lock, release_lock

lock, in_session, ran, overlap, clobbered, ready, go = map(Path, sys.argv[1:8])
ready.touch()
while not go.exists():
    time.sleep(0.001)
time.sleep(float(sys.argv[8]))
try:
    acquire_lock(lock)
except LockBusyError:
    sys.exit(0)
try:
    in_session.mkdir()
except FileExistsError:
    overlap.open("a").write("py\\n")
    sys.exit(0)
ran.open("a").write("py\\n")
time.sleep(0.2)
try:
    still_ours = lock.read_text().strip() == str(os.getpid())
except FileNotFoundError:
    still_ours = False  # deleted out from under us
if not still_ours:
    clobbered.open("a").write("py\\n")
in_session.rmdir()
release_lock(lock)
"""


def _wait_for(*paths: Path) -> None:
    deadline = time.monotonic() + RUN_TIMEOUT_S
    while not all(p.exists() for p in paths):
        assert time.monotonic() < deadline, f"racers never became ready: {paths}"
        time.sleep(0.005)


def test_runner_racing_a_python_exclusive_holder_never_overwrites_it(tmp_path: Path) -> None:
    """A runner and scoutctl recovering the same crashed holder's lock: the
    runner must never delete or overwrite the lock scoutctl took with O_EXCL,
    and the two never hold it together.

    Both sides wait on a start barrier — the runner inside scripts/scout-tz.sh,
    which it calls just before its lock block, so start-up cost (the Python
    import, bash's own start) doesn't decide the race — and then the offset
    between them is swept across the runner's recovery window.
    """
    script, _, lock = _vault(tmp_path)
    scout_dir = script.parent
    ran, overlap = _stub_overlap_session(scout_dir)
    clobbered = scout_dir / "clobbered.log"
    runner_ready, py_ready, go = (scout_dir / n for n in ("runner-ready", "py-ready", "go"))
    runner_delay = scout_dir / "runner-delay"
    tz = scout_dir / "scripts" / "scout-tz.sh"
    tz.write_text(
        f'#!/bin/bash\ntouch "{runner_ready}"\n'
        f'while [ ! -e "{go}" ]; do sleep 0.001; done\n'
        f'sleep "$(cat "{runner_delay}")"\n',
        encoding="utf-8",
    )
    tz.chmod(0o755)
    for i in range(RACE_ROUNDS):
        for marker in (runner_ready, py_ready, go):
            marker.unlink(missing_ok=True)
        lock.write_text(f"{_dead_pid()}\n", encoding="utf-8")
        before = len(_lines(ran))
        # Offsets from -10 ms (runner later) to +28 ms (Python later).
        offset = -0.010 + 0.038 * i / (RACE_ROUNDS - 1)
        runner_delay.write_text(f"{max(-offset, 0):.3f}", encoding="utf-8")
        racer = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _PY_RACER,
                *(str(p) for p in (lock, scout_dir / "in-session", ran, overlap, clobbered, py_ready, go)),
                f"{max(offset, 0):.3f}",
            ],
            cwd=ENGINE_ROOT,
        )
        runner = _popen(script)
        _wait_for(runner_ready, py_ready)
        go.touch()
        _, err = runner.communicate(timeout=RUN_TIMEOUT_S)
        assert runner.returncode == 0, err
        assert racer.wait(timeout=RUN_TIMEOUT_S) == 0

        assert not _lines(clobbered), "the runner deleted or overwrote scoutctl's lock"
        assert not _lines(overlap), "the runner and scoutctl held the lock at once"
        assert len(_lines(ran)) > before, "one of them must run"
        assert not lock.exists()

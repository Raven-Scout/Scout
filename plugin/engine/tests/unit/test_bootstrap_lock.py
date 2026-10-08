"""Unit tests for engine/scout/scripts/bootstrap_lock.py."""

from __future__ import annotations

import os
import time

import pytest

from scout.scripts.bootstrap_lock import (
    LockBusyError,
    acquire_lock,
    acquire_lock_with_wait,
    is_lock_held_by_live_pid,
    release_lock,
    remove_stale_lock,
)


def test_acquire_lock_writes_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    assert lock.exists()
    assert lock.read_text().strip() == str(os.getpid())


def test_release_lock_removes_file(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    release_lock(lock)
    assert not lock.exists()


def test_is_lock_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    assert is_lock_held_by_live_pid(lock) is True


def test_is_lock_not_held_when_pid_dead(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    fake_pid = 999999
    lock.write_text(str(fake_pid))
    assert is_lock_held_by_live_pid(lock) is False


def test_is_lock_not_held_when_file_missing(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    assert is_lock_held_by_live_pid(lock) is False


def test_remove_stale_lock_removes_dead_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    lock.write_text("999999")
    remove_stale_lock(lock)
    assert not lock.exists()


def test_remove_stale_lock_preserves_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    remove_stale_lock(lock)
    assert lock.exists()


def test_acquire_raises_when_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    with pytest.raises(LockBusyError):
        acquire_lock(lock)


def test_acquire_lock_with_wait_times_out(tmp_path, monkeypatch):
    """acquire_lock_with_wait raises LockBusyError when deadline expires."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))  # held by live (this) PID

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    # Force monotonic to advance past the deadline immediately.
    times = iter([0.0, 1000.0, 2000.0])
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.monotonic",
        lambda: next(times),
    )

    with pytest.raises(LockBusyError):
        acquire_lock_with_wait(lock, timeout_s=300, poll_s=10)


def test_acquire_lock_with_wait_succeeds_after_retry(tmp_path, monkeypatch):
    """acquire_lock_with_wait succeeds when a previously-held lock becomes free."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("999999")  # held by dead PID — first call to acquire_lock will clean it

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    acquire_lock_with_wait(lock, timeout_s=300, poll_s=10)
    # Lock now held by us
    assert lock.read_text().strip() == str(os.getpid())
    # Should succeed on first try (dead PID cleared by acquire_lock)
    assert sleep_calls == []


def test_release_lock_preserves_other_owner(tmp_path):
    """release_lock must not unlink a lock owned by a different PID."""
    lock = tmp_path / ".scout-session.lock"
    other_pid = os.getpid() + 1  # almost certainly not us
    lock.write_text(str(other_pid))
    release_lock(lock)
    assert lock.exists()
    assert lock.read_text().strip() == str(other_pid)


def test_acquire_lock_clears_stale_dead_pid(tmp_path):
    """acquire_lock unlinks a stale dead-PID lock and writes our own."""
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    lock.write_text("999999")  # dead PID
    acquire_lock(lock)
    assert lock.read_text().strip() == str(os.getpid())


# Regression: acquire_lock must be atomic. The previous implementation
# did an `exists()` check, then `unlink()`, then `write_text()` — three
# non-atomic syscalls. Two racing processes could both pass the check,
# both write their PID, and both believe they held the lock. Issue #36.


def test_acquire_lock_treats_empty_lock_as_busy_not_stale(tmp_path):
    """An empty lock file is the exact state a racing winner leaves between
    its O_EXCL create and its PID write. A second caller must NOT treat that
    empty file as stale and clobber it — that is the residual hole in #36's
    O_EXCL fix that let two callers both 'win'. Empty/unparseable existing
    lock → LockBusyError, and the file is left intact."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("")  # winner created it via O_EXCL, hasn't written PID yet
    with pytest.raises(LockBusyError):
        acquire_lock(lock)
    assert lock.exists()  # must not have been removed


def test_acquire_lock_is_atomic_under_concurrent_callers(tmp_path):
    """When two concurrent callers race to acquire the same lock,
    exactly one must succeed and the other must see LockBusyError —
    NOT both succeed with the second silently clobbering the first."""
    import threading

    lock = tmp_path / ".scout-session.lock"
    results: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()  # synchronize start
        try:
            acquire_lock(lock)
        except LockBusyError:
            results.append("busy")
            return
        results.append("success")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["busy", "success"], f"expected exactly one success and one busy, got: {results}"
    assert lock.exists()
    assert lock.read_text().strip() == str(os.getpid())


# Regression: stale recovery read the dead PID, then unlinked the path. A
# second waiter that read the same dead PID could unlink the live lock the
# first had just taken, then win its own create: two holders (#322).

DEAD_PID = 999999  # macOS default max PID is 99998; 999999 is reliably unused.


def _live_holder_takes_over_after_first_read(monkeypatch, lock):
    """Make `_read_lock_pid` return the dead PID, then simulate another waiter
    completing its takeover (writing a live PID) before this caller acts on it."""
    from scout.scripts import bootstrap_lock

    real_read = bootstrap_lock._read_lock_pid
    state = {"raced": False}

    def racing_read(path):
        pid = real_read(path)
        if not state["raced"] and path == lock and pid == DEAD_PID:
            state["raced"] = True
            lock.write_text(str(os.getppid()))  # the other waiter's live lock
        return pid

    monkeypatch.setattr(bootstrap_lock, "_read_lock_pid", racing_read)


def test_acquire_lock_stale_recovery_never_removes_a_fresh_live_lock(tmp_path, monkeypatch):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(DEAD_PID))
    _live_holder_takes_over_after_first_read(monkeypatch, lock)

    with pytest.raises(LockBusyError):
        acquire_lock(lock)
    assert lock.read_text().strip() == str(os.getppid())
    assert sorted(p.name for p in tmp_path.iterdir()) == [lock.name]  # no takeover debris


def test_remove_stale_lock_never_removes_a_fresh_live_lock(tmp_path, monkeypatch):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(DEAD_PID))
    _live_holder_takes_over_after_first_read(monkeypatch, lock)

    remove_stale_lock(lock)
    assert lock.read_text().strip() == str(os.getppid())
    assert sorted(p.name for p in tmp_path.iterdir()) == [lock.name]


def test_acquire_lock_wins_when_another_waiter_already_cleared_the_stale_lock(tmp_path, monkeypatch):
    from scout.scripts import bootstrap_lock

    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(DEAD_PID))
    real_read = bootstrap_lock._read_lock_pid

    def read_then_vanish(path):
        pid = real_read(path)
        if path == lock and pid == DEAD_PID:
            lock.unlink()  # the other waiter discarded it first
        return pid

    monkeypatch.setattr(bootstrap_lock, "_read_lock_pid", read_then_vanish)
    acquire_lock(lock)
    assert lock.read_text().strip() == str(os.getpid())


def test_stale_recovery_waits_while_another_waiter_holds_the_takeover_guard(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(DEAD_PID))
    guard = tmp_path / ".scout-session.lock.takeover"
    guard.write_text("")  # another waiter is mid-recovery

    with pytest.raises(LockBusyError):
        acquire_lock(lock)
    assert lock.read_text().strip() == str(DEAD_PID)  # not ours to touch yet
    assert guard.exists()


def test_stale_recovery_clears_a_takeover_guard_whose_owner_died(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(DEAD_PID))
    guard = tmp_path / ".scout-session.lock.takeover"
    guard.write_text("")
    old = time.time() - 3600
    os.utime(guard, (old, old))

    with pytest.raises(LockBusyError):
        acquire_lock(lock)  # this poll clears the abandoned guard...
    assert not guard.exists()
    acquire_lock(lock)  # ...and the next one recovers the stale lock
    assert lock.read_text().strip() == str(os.getpid())


def test_remove_stale_lock_leaves_an_unparseable_lock_alone(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("")
    remove_stale_lock(lock)
    assert lock.exists()


def test_release_lock_leaves_an_empty_lock_alone(tmp_path):
    """An empty lock is a competitor between its O_EXCL create and its PID
    write; it isn't ours to remove."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("")
    release_lock(lock)
    assert lock.exists()


def test_release_lock_tolerates_a_lock_that_vanished(tmp_path, monkeypatch):
    from scout.scripts import bootstrap_lock

    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    real_read = bootstrap_lock._read_lock_pid

    def read_then_vanish(path):
        pid = real_read(path)
        path.unlink()
        return pid

    monkeypatch.setattr(bootstrap_lock, "_read_lock_pid", read_then_vanish)
    release_lock(lock)  # must not raise
    assert not lock.exists()

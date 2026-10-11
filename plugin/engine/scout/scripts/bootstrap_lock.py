"""Global pipeline lock for `scoutctl bootstrap install|upgrade`.

Holds ``.scout-logs/.scout-session.lock`` for the entire 8-stage pipeline.
Runner scripts and the dispatcher already check this lock and skip when
held — so holding it for the pipeline closes every interleaving window
between bootstrap stages and dispatcher ticks.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


class LockBusyError(Exception):
    """Raised when the lock is already held by a live PID."""

    def __init__(self, lock_path: Path, pid: int) -> None:
        self.lock_path = lock_path
        self.pid = pid
        super().__init__(f"lock {lock_path} held by live PID {pid}")


def _read_lock_pid(lock_path: Path) -> int | None:
    """Return the PID written in the lock file, or None if absent/unparseable.

    None covers the empty file a racing winner leaves between its O_EXCL
    create and its PID write, as well as a corrupt lock.
    """
    try:
        return int(lock_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    """Return True iff `pid` is a live process we should treat as the holder."""
    if pid <= 0:
        # Corrupt lock — refuse to call os.kill(0, 0) (process group) or
        # os.kill(-1, 0) (every process owned by user).
        return False
    try:
        os.kill(pid, 0)  # signal 0 — existence probe, no actual signal sent
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Different uid — process exists but we can't signal it. Treat as live.
        return True


def is_lock_held_by_live_pid(lock_path: Path) -> bool:
    """Return True iff the lock file exists and its PID is alive.

    TOCTOU note: a process could exit between this check and a subsequent
    operation. That's acceptable for single-user single-machine use; the
    lock is a coordination signal between scout-app sessions, not a
    cross-host mutex.
    """
    if not lock_path.exists():
        return False
    pid = _read_lock_pid(lock_path)
    if pid is None:
        return False
    return _pid_alive(pid)


# A takeover guard older than this was left by a process that died inside the
# few-syscall critical section of _discard_stale_lock; it is safe to clear.
_GUARD_ABANDONED_S = 30


def _discard_stale_lock(lock_path: Path, dead_pid: int) -> bool:
    """Remove the lock iff it still holds ``dead_pid``. Return True if the path is now free.

    Reading the PID and then unlinking the path is not atomic: another waiter
    that read the same dead PID may already have taken the lock over, and an
    unlink would delete its live lock (#322). So recovery is serialised behind
    an O_EXCL ``<lock>.takeover`` guard, and the PID is re-read while holding
    it. Every remover takes the guard first, and nobody can create a lock
    while the dead one occupies the path, so a lock that still reads
    ``dead_pid`` under the guard is still the dead one when it is unlinked.
    The path is never emptied while a live holder is in it.
    """
    guard = lock_path.with_name(f"{lock_path.name}.takeover")
    try:
        os.close(os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError:
        # Another waiter is recovering. Clear the guard only if its owner
        # died mid-recovery; the caller retries on its next poll either way.
        with contextlib.suppress(OSError):
            if time.time() - guard.stat().st_mtime > _GUARD_ABANDONED_S:
                guard.unlink()
        return False
    try:
        if not lock_path.exists():
            return True  # another waiter cleared it and its winner has since released
        if _read_lock_pid(lock_path) != dead_pid:
            return False  # taken over already: a live PID, or a winner mid-write
        lock_path.unlink()
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            guard.unlink()


def acquire_lock(lock_path: Path) -> None:
    """Take the lock by atomically creating the file with our PID.

    Uses ``O_CREAT | O_EXCL`` so two racing callers cannot both believe
    they hold the lock — one wins the create, the other gets
    ``FileExistsError`` and we surface it as :class:`LockBusyError`.

    Stale recovery happens ONLY in response to that create conflict, and
    ONLY for a parseable, dead PID (a crashed holder): such a lock is
    unlinked and the atomic claim retried once. An existing lock whose
    contents we cannot parse — most importantly the empty file a racing
    winner leaves in the window between its O_EXCL create and its PID
    write — is treated as busy and never removed. The previous pre-check
    unlinked any "not held by a live PID" lock, which classified that
    empty file as stale and let both racers win; doing stale recovery
    only on the O_EXCL conflict, and only for a confirmed-dead PID, closes
    that window (#36). The dead-PID lock itself is removed through
    :func:`_discard_stale_lock`, so two waiters recovering the same crashed
    holder can't both win either (#322).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as e:
            pid = _read_lock_pid(lock_path)
            # Clear and retry once only for a parseable, dead PID. Empty /
            # unparseable (racing winner mid-write or corrupt) or a live
            # holder is busy — never unlink it.
            if pid is not None and not _pid_alive(pid) and attempt == 0 and _discard_stale_lock(lock_path, pid):
                continue
            raise LockBusyError(lock_path, pid if pid is not None else -1) from e
        else:
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(str(os.getpid()))
            except Exception:
                # Roll back the partial claim so the next caller can retry cleanly.
                with contextlib.suppress(FileNotFoundError):
                    lock_path.unlink()
                raise
            return


def release_lock(lock_path: Path) -> None:
    """Release the lock if we still hold it.

    A lock whose contents don't parse is left alone: it is most likely a
    competitor between its O_EXCL create and its PID write, not ours (#322).
    While our PID is in the file nobody else removes it — stale recovery
    only discards a dead PID — so the read-then-unlink here is safe.
    """
    if _read_lock_pid(lock_path) == os.getpid():
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def remove_stale_lock(lock_path: Path) -> None:
    """Remove the lock file iff it holds a dead PID. No-op otherwise.

    An empty or unparseable lock is left alone, as in :func:`acquire_lock`.
    """
    pid = _read_lock_pid(lock_path)
    if pid is not None and not _pid_alive(pid):
        _discard_stale_lock(lock_path, pid)


def acquire_lock_with_wait(lock_path: Path, *, timeout_s: int = 300, poll_s: int = 10) -> None:
    """Acquire with up to ``timeout_s`` of polling. Raise LockBusyError on timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            acquire_lock(lock_path)
            return
        except LockBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)

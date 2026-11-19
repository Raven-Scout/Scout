"""Writes that never follow a symlink or a hard link a scheduled run could have planted.

A scheduled run's OS write sandbox (:mod:`scout.sandbox.profile`) denies writes to a list of protected paths and
allows everything else, so a run can put a symlink anywhere it may write: at a state file's temp name, at a lock
file, or at a log. A later write made *outside* the sandbox (the owner's CLI, the schedule tick, the heartbeat)
that opens such a name the ordinary way (``Path.write_text``, ``open(path, "a")``, ``os.open`` without
``O_NOFOLLOW``) writes through the link, into the plugin's hooks, a secrets folder or a git hook. These helpers
are the one way the engine writes those files:

- :func:`atomic_write_bytes` / :func:`atomic_write_text` replace a file whole: a temp file with a random name is
  created beside it with ``O_CREAT | O_EXCL | O_NOFOLLOW`` (so nothing already at that name, a link included, is
  ever opened), written, fsynced and renamed over the target. A rename replaces a symlink at the target itself
  and never writes through it.
- :func:`open_append` opens a log for appending, and :func:`open_lock` a lock file, with ``O_NOFOLLOW`` and
  ``O_NONBLOCK`` (a FIFO put there can't hang the open), then refuse anything but a regular file with one link.

Each refusal is an :class:`UnsafeFileError`, an :class:`OSError`, so a best-effort writer that already swallows
``OSError`` keeps doing so, and nothing is written.

Only the file's own name is checked: its folder is used as found (a vault may legitimately sit behind a symlink).
The temp file and the target are reached through one open handle on that folder, so both land in the same place.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import IO, Any

__all__ = ["UnsafeFileError", "atomic_write_bytes", "atomic_write_text", "open_append", "open_lock"]

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
NEW_FILE_MODE = 0o666  # less the process umask, as ``open()`` makes a file
_TMP_ATTEMPTS = 16


class UnsafeFileError(OSError):
    """A write refused because the name holds a symlink, a hard link to another file, or not a regular file."""

    def __init__(self, path: Path | str, why: str) -> None:
        super().__init__(errno.ELOOP, f"refused to write through {why}", str(path))


def atomic_write_bytes(path: Path | str, data: bytes, *, mode: int | None = None, fsync: bool = True) -> None:
    """Replace ``path`` with ``data``: a new temp file beside it, renamed over it. Raises OSError on failure.

    ``mode`` is the new file's permission bits; ``None`` keeps those of the regular file being replaced, or makes
    a new one as ``open()`` would (0666 less the umask). Nothing at ``path`` is followed: a symlink there is
    replaced, not written through. On any failure the temp file is removed and ``path`` is left as it was.
    """
    path = Path(path)
    dir_fd = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    try:
        if mode is None:
            mode = _kept_mode(path.name, dir_fd)
        tmp, fd = _new_temp(path.name, dir_fd, 0o600 if mode is not None else NEW_FILE_MODE)
        try:
            try:
                if mode is not None:
                    os.fchmod(fd, mode)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                if fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dir_fd)
            raise
        if fsync:
            with contextlib.suppress(OSError):  # not every filesystem syncs a folder; the file itself is synced
                os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_text(
    path: Path | str, text: str, *, mode: int | None = None, fsync: bool = True, encoding: str = "utf-8"
) -> None:
    """:func:`atomic_write_bytes` for text (no newline translation: ``\\n`` stays ``\\n``)."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode, fsync=fsync)


def open_append(
    path: Path | str, *, mode: int = NEW_FILE_MODE, binary: bool = False, encoding: str = "utf-8"
) -> IO[Any]:
    """Open ``path`` to append to, refusing a symlink, a hard link or a non-file.

    A missing file is made with ``mode`` less the umask.

    Returns a file object (text in ``encoding`` unless ``binary``); the caller closes it, as with ``open``.
    """
    fd = _open_checked(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode)
    if binary:
        return os.fdopen(fd, "ab")
    return os.fdopen(fd, "a", encoding=encoding)


def open_lock(path: Path | str, *, mode: int = 0o600) -> IO[bytes]:
    """Open (or make) the lock file ``path`` for ``fcntl.flock``, refusing a symlink, a hard link or a non-file.

    Nothing is ever written to it. Returns an unbuffered binary file object; the caller closes it.
    """
    fd = _open_checked(path, os.O_RDWR | os.O_CREAT, mode)
    return os.fdopen(fd, "r+b", buffering=0)


def _open_checked(path: Path | str, flags: int, mode: int) -> int:
    try:
        fd = os.open(path, flags | _NOFOLLOW | _NONBLOCK | _CLOEXEC, mode)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise UnsafeFileError(path, "a symlink") from e
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeFileError(path, "something that isn't a regular file")
        if st.st_nlink != 1:
            raise UnsafeFileError(path, "a hard link")
        if _NONBLOCK:
            os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _new_temp(name: str, dir_fd: int, mode: int) -> tuple[str, int]:
    """A temp file made by this call alone: a random name, opened ``O_EXCL | O_NOFOLLOW``."""
    for _ in range(_TMP_ATTEMPTS):
        tmp = f".{name}.{secrets.token_hex(6)}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, mode, dir_fd=dir_fd)
        except FileExistsError:
            continue
        return tmp, fd
    raise FileExistsError(errno.EEXIST, "no free temporary name", name)  # pragma: no cover - 16 random collisions


def _kept_mode(name: str, dir_fd: int) -> int | None:
    """The permission bits of the regular file at ``name``; None when there is none (a symlink is not kept)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return stat.S_IMODE(st.st_mode) if stat.S_ISREG(st.st_mode) else None

"""The logic around the two macOS libraries the runner asks (``sandbox_check``, libproc), with the C calls stood in.

Only loading the libraries needs macOS (``SandboxProbe.__init__`` and ``OpenFiles.__init__``, ``# pragma: no cover``
there); what the runner does with their answers runs anywhere. The real calls are checked on the kernel in
``test_lab_runner.py``.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scout.lab import sandbox

ENTRY = ctypes.sizeof(sandbox._ProcFdInfo)
INFO = ctypes.sizeof(sandbox._VnodeFdInfo)


# --- sandbox_check ---------------------------------------------------------------------------------------------------


def _probe(answer: Callable[..., int], pad: int = 0) -> sandbox.SandboxProbe:
    probe = object.__new__(sandbox.SandboxProbe)
    probe._check = answer  # type: ignore[attr-defined]
    probe._pad = [ctypes.c_long(0)] * pad  # type: ignore[attr-defined]
    return probe


def test_sandboxed_and_denies_read_the_kernels_answers() -> None:
    calls: list[tuple[Any, ...]] = []

    def check(pid: ctypes.c_int, op: bytes | None, flags: ctypes.c_int, *rest: Any) -> int:
        calls.append((pid.value, op, flags.value, len(rest)))
        if op is None:
            return 1 if pid.value == 7 else 0
        path = rest[-1].value
        return {b"/denied": 1, b"/allowed": 0}.get(path, -1)

    probe = _probe(check, pad=5)
    assert probe.sandboxed(7) is True and probe.sandboxed(8) is False
    assert probe.denies(7, "file-write-data", "/denied") is True
    assert probe.denies(7, "file-write-data", "/allowed") is False
    assert probe.denies(7, "file-write-data", "/unknown") is None  # the kernel can't say
    # The path rides after the arm64 padding; the filter asks not to log the probe.
    assert calls[-1] == (7, b"file-write-data", sandbox._FILTER_PATH | sandbox._CHECK_NO_REPORT, 6)


@pytest.mark.parametrize(("sandboxed", "denied", "works"), [(0, 0, True), (1, 0, False), (0, 1, False)])
def test_works_wants_this_process_unsandboxed_and_allowed(sandboxed: int, denied: int, works: bool) -> None:
    probe = _probe(lambda _pid, op, *_rest: sandboxed if op is None else denied)
    assert probe.works("/tmp") is works


# --- libproc ---------------------------------------------------------------------------------------------------------


def _files(pidinfo: Callable[..., int], fdinfo: Callable[..., int], max_fds: int = 8) -> sandbox.OpenFiles:
    files = object.__new__(sandbox.OpenFiles)
    files._pidinfo = pidinfo  # type: ignore[attr-defined]
    files._fdinfo = fdinfo  # type: ignore[attr-defined]
    files.max_fds = max_fds
    return files


def _listing(entries: list[tuple[int, int]], err: int = 0) -> Callable[..., int]:
    """``proc_pidinfo(PROC_PIDLISTFDS)``: fills the buffer with ``(fd, type)`` entries."""

    def pidinfo(_pid: int, flavor: int, _arg: int, buf: Any, size: int) -> int:
        assert flavor == sandbox._PROC_PIDLISTFDS
        if err:
            ctypes.set_errno(err)
            return -1
        for i, (fd, kind) in enumerate(entries[: size // ENTRY]):
            buf[i].proc_fd, buf[i].proc_fdtype = fd, kind
        return min(len(entries) * ENTRY, size)

    return pidinfo


def _vnodes(by_fd: dict[int, tuple[int, int, int, int, int] | int]) -> Callable[..., int]:
    """``proc_pidfdinfo(PROC_PIDFDVNODEINFO)``: ``(mode, nlink, dev, ino, size)`` per descriptor, or an errno."""

    def fdinfo(_pid: int, fd: int, flavor: int, ref: Any, size: int) -> int:
        assert flavor == sandbox._PROC_PIDFDVNODEINFO and size == INFO
        got = by_fd[fd]
        if isinstance(got, int):
            ctypes.set_errno(got)
            return 0
        st = ref._obj.vi_stat
        st.vst_mode, st.vst_nlink, st.vst_dev, st.vst_ino, st.vst_size = got
        st.vst_blocks = 0
        return INFO

    return fdinfo


REG, DIR = stat.S_IFREG | 0o600, stat.S_IFDIR | 0o700


def test_unlinked_lists_only_regular_files_with_no_name_left() -> None:
    files = _files(
        _listing([(3, sandbox._PROX_FDTYPE_VNODE), (4, sandbox._PROX_FDTYPE_VNODE), (5, 2), (6, 1), (7, 1)]),
        _vnodes({3: (REG, 0, 1, 10, 5000), 4: (REG, 1, 1, 11, 9000), 6: (DIR, 0, 1, 12, 64), 7: errno.EBADF}),
    )
    assert files.unlinked(42) == [(1, 10, 5000)]  # a named file, a socket, a folder and a closed fd left out


def test_unlinked_tells_a_gone_process_from_one_it_cant_list() -> None:
    assert _files(_listing([], err=errno.ESRCH), _vnodes({})).unlinked(42) is None
    assert _files(_listing([]), _vnodes({})).unlinked(42) == []  # alive, nothing open
    with pytest.raises(OSError, match="couldn't list"):
        _files(_listing([], err=errno.EPERM), _vnodes({})).unlinked(42)
    gone_midway = _files(_listing([(3, 1)]), _vnodes({3: errno.ESRCH}))
    assert gone_midway.unlinked(42) is None
    with pytest.raises(OSError, match="couldn't read descriptor 3"):
        _files(_listing([(3, 1)]), _vnodes({3: errno.EIO})).unlinked(42)


def test_a_listing_that_fills_its_buffer_is_an_error() -> None:
    files = _files(_listing([(n, 1) for n in range(20)]), _vnodes({}), max_fds=4)
    with pytest.raises(OSError, match="more descriptors open than 4"):
        files.unlinked(42)


def test_works_finds_this_processes_own_unlinked_temp_file() -> None:
    """works() opens a temp file, removes nothing itself (TemporaryFile has no name) and asks for this process."""
    opened: dict[int, os.stat_result] = {}
    real = tempfile.TemporaryFile

    def tracked(*a: Any, **k: Any) -> Any:
        fh = real(*a, **k)
        opened[fh.fileno()] = os.fstat(fh.fileno())
        return fh

    def pidinfo(pid: int, _flavor: int, _arg: int, buf: Any, size: int) -> int:
        assert pid == os.getpid()
        fds = sorted(opened)
        for i, fd in enumerate(fds):
            buf[i].proc_fd, buf[i].proc_fdtype = fd, sandbox._PROX_FDTYPE_VNODE
        return len(fds) * ENTRY

    def fdinfo(_pid: int, fd: int, _flavor: int, ref: Any, _size: int) -> int:
        st, out = os.fstat(fd), ref._obj.vi_stat
        out.vst_mode, out.vst_nlink, out.vst_dev, out.vst_ino = st.st_mode, 0, st.st_dev, st.st_ino
        out.vst_size, out.vst_blocks = st.st_size, 0
        return INFO

    files = _files(pidinfo, fdinfo)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(tempfile, "TemporaryFile", tracked)
        assert files.works() is True
        broken = _files(_listing([], err=errno.EPERM), _vnodes({}))
        assert broken.works() is False  # the kernel won't list it
        blind = _files(_listing([]), _vnodes({}))
        assert blind.works() is False  # its own file isn't reported


def test_a_program_denied_twice_is_listed_once(tmp_path: Path) -> None:
    text = sandbox.experiment_profile(
        tmp_path, tmp_path / ".tmp", Path("/tmp/s"), [], [Path("/bin/sh"), Path("/bin/sh")], env={}
    )
    execs = text[text.index("(deny process-exec") :].split(")", 1)[0]
    assert execs.count(f'(literal "{os.path.realpath("/bin/sh")}"') == 1

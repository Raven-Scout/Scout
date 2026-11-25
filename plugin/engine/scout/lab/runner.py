"""Run one experiment's script under the experiment sandbox, with a model-call meter (spec §6.3, §14a).

The caller is the run broker (Task 11), outside the scheduled run's own sandbox: ``sandbox-exec``
can't nest. This module applies the profile from :mod:`scout.lab.sandbox`, serves the meter
(:class:`scout.lab.meter.MeterServer`) on a socket only the script can reach, and makes sure nothing
the script started outlives it.

What it holds the script to, and why each piece is where it is:

- **The ledger is the owner's, not the experiment's.** It lives under
  :func:`scout.sandbox.broker.owner_dir` (``lab-meter/<vault hash>-<slug>-<EID>.jsonl``, folder 0700),
  outside every path the profile lets a script write, so a script can't truncate it to reset its
  spending. The cap therefore holds across runs of one experiment. The run log in the experiment
  folder records what was spent. Beside it, ``<…>.lock`` is held for the whole of a run, so a second
  run of the same experiment is refused at once ("already running") rather than sharing its folder.
- **The folders the profile allows are checked first.** The experiment folder must resolve to the
  same path under the resolved vault (no symlinked component), and the scratch dir is made fresh;
  the rendered profile is then checked to allow exactly those two. A run could otherwise point a
  folder at a protected path and have the broker grant it.
- **The runner never follows a link the script could plant.** It keeps a descriptor on the
  experiment folder and writes the run log and removes the scratch dir through it (``O_NOFOLLOW``,
  ``O_EXCL``, :func:`shutil.rmtree` with ``dir_fd``).
- **Nothing the script started survives it.** At the timeout, after a normal exit and when the
  runner itself is interrupted, the process group is SIGKILLed; then every process of this user is
  swept and killed when its sandbox is this run's: the kernel (``sandbox_check``) says it may write
  this run's probe file and may not write the folder holding the experiment. The probe file sits in
  the runner's 0700 temp dir and the profile grants it ``file-write-data`` only, so a script can't
  delete or swap it to hide from the sweep (it can delete its own ``.tmp``). A child that left the
  group (``setsid``, a double fork) still carries the profile, so the sweep finds it whatever its
  parentage. Matches are frozen (SIGSTOP) until a pass finds no new one, then killed, until a sweep
  finds none; a probe file that isn't a plain file any more is "not clean".
- **The sweep fails closed (ruling R21).** The script starts behind a gate (a ``bash`` that waits on
  its stdin pipe): the runner first checks the kernel's answers about that live, sandboxed process
  (sandboxed, may write the probe file, may not write outside). On a wrong or missing answer it
  kills the group, then closes the pipe, and raises :class:`LabError`; the script never ran. Only
  the runner's "go" opens the gate: an end of file (a runner that died after starting it) exits.
  The gate also caps the size of any file the script writes at the run's bytes cap (at most 1 GiB) and the
  descriptors it may hold at the owner's ``lab.max_open_files`` (default 1024).
- **Totals: processes and bytes (W4).** The owner's ``lab.max_processes`` bounds how many processes the script may
  have at once, itself included, two ways. The kernel's ``RLIMIT_NPROC`` (the gate's ``ulimit -u``, soft and hard,
  so the script can't raise it) counts every process of the user, by real uid, not a process tree, so an absolute
  64 would leave a busy Mac's script unable to fork at all; the gate sets it instead to the user's count just before
  launch, as the kernel counts it (:func:`_owner_process_count`; ``ps`` lists more), plus the allowance, plus
  :data:`NPROC_HEADROOM` for the owner's other processes to grow into meanwhile, never over the per-user ceiling
  (:func:`_nproc_limit`). That kernel cap is the fork-bomb backstop: a bomb can't take the owner's count past it, so it
  can't starve the owner's other processes. The allowance itself is the runner's: it counts the processes holding
  this run's sandbox (the sweep's probe, never anything else) at every poll and kills the run when they are more than
  ``max_processes`` (``over_processes``). The headroom is what keeps a script's forks from depending on how many
  other processes the owner starts during the run (a Mac with many sessions open starts plenty). At the
  same polls (:data:`_POLL_S`) the runner sums the bytes of the regular files in the experiment folder, its scratch
  dir included (``lstat``, no link followed, each inode once), plus every open-but-unlinked regular file a process
  holding this run's sandbox keeps (libproc, :class:`scout.lab.sandbox.OpenFiles`: ``tempfile.TemporaryFile`` and
  SQLite temp stores leave no name to walk), and kills the run past ``lab.max_total_bytes`` (``over_bytes``; a
  folder the runner can't list, or a live holder whose files the kernel won't list, counts as over). Either kill
  goes through the timeout's kill path, and the run log records both flags.
  Two more totals ride the same polls and the same kill path (spec §6.4). The **free-disk floor**
  (``lab.disk_free_floor_bytes``): before launch ``free_bytes(vault) - budget`` must stay at or over the floor
  (:class:`DiskFloorError`, the script never runs), and at every poll the volume's free bytes must too, whatever
  filled the disk (``over_disk_floor``). **Memory** (``lab.max_memory_bytes``, 0 = half of the Mac's): the sum of
  the physical footprints (:func:`footprint_bytes`, GPU allocations included) of the processes holding this
  run's sandbox is held to the limit (``over_memory``). The run log records both flags beside the others.
- **The script can't stop its supervisor.** The profile lets it signal only processes holding its own
  sandbox, so it can't freeze or kill the runner (which would leave its own setsid children
  unswept). A runner killed from outside (by the owner, or the OS) still leaves them: then
  ``ps`` shows them, and a later run of the same experiment doesn't sweep them.
- **Private bytes outside knowledge-base/ are denied too** (ruling R18): the git objects, the inbox,
  the engine cache, every other checkout of the vault's repo (``.claude/worktrees/`` and each git
  worktree), ``Raw/`` and ``action-items/``, and ``.scout-state/`` (the run broker's queue of outgoing commit and
  Telegram text among it), for a private experiment too. Transcripts and ``.scout-logs`` stay readable (spec
  §6.3).
- **A private thread's folder and every other experiment's folder are denied whole** (I4): a thread's timeline
  may carry no frontmatter, and an experiment folder's files (scripts, outputs, run logs) are private by path,
  whatever the experiment's note says. The experiment's own folder is allowed back after every read deny, and
  before the seals (R34).
- **Nothing else under a home folder is readable** (ruling R32): only the vault, the engine and its
  interpreter, the vault's session transcripts and the allowlisted interpreters on the run's PATH
  (:func:`_allow_reads`). The rendered profile itself is unreadable too: it names every private file. A conda
  base prefix right inside a home folder (``~/miniconda3``, which holds ``conda-meta/``) is allowed whole; a
  version-manager shim found first on the script's PATH (a ``shims/`` folder, ``volta-shim``) refuses the run,
  since it reads its manager's settings from the home folder.
- **A committed file runs only as committed** (ruling R34). The script named by ``argv`` comes from the snapshot,
  but its cwd is the experiment's worktree folder, so a sibling reached by a relative path (``sh ./helper.sh``)
  would be the worktree copy, edited after the broker's check and so never under the owner's notice. Every file
  :func:`_committed_files` lists at the validated commit therefore has its worktree copy denied to read and to
  write (as spelled and as resolved when the profile is rendered); ``$SCOUT_LAB_SOURCE_DIR`` holds the committed
  copies, and new files (outputs) stay the script's. What still escapes: a hard link to a committed file is a
  path of its own, so a script that reads one made before the run reads that inode whatever its content (the
  kernel refuses the script's own ``ln`` of a sealed file, checked on macOS 26). Reading it would have to be in
  the committed script, which the owner sees during the notice. A worktree copy swapped for a symlink after the
  profile is rendered also escapes: the kernel matches the link's target, which the profile didn't name.
- **A detached run uses this runner whole** (spec §6.4, ruling P2-h): its supervisor (:mod:`scout.lab.supervise`)
  holds the run lock for its whole life (:func:`hold_run_lock`), learns the sweep's probe file and outside folder
  through ``on_started`` before the gate opens (so a later collect can sweep the script if the supervisor dies), and
  stops the run through ``should_stop`` (the owner's veto or stop, its wall-clock deadline). The run log then says
  ``detached: true`` and why it ``stopped``.
- **Model weights are read-only** (spec §6.4): a detached run's supervisor fetches the weights an experiment names
  into the owner's cache (:mod:`scout.lab.weights`) before the gate opens and passes them here; the profile allows
  back each named revision folder to read and denies it to write, the script gets ``$SCOUT_LAB_WEIGHTS_DIR`` (the
  cache's root) and the run log lists every file's repo, revision and sha256. The owner's Hugging Face token is
  never in the script's environment (:func:`script_env` builds it from nothing).
- **The owner's Python** (``lab.python``, spec §6.4): ``python3`` in a validated argv runs that interpreter
  (:func:`owner_python_argv`); its install prefix, and its venv's folder when it is one, are allowed back to read.
- **The experiment's status is left alone.** The run finishes or abandons it.
"""

from __future__ import annotations

import contextlib
import ctypes
import datetime as _dt
import fcntl
import hashlib
import io
import json
import math
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from scout import config, plural, safe_write
from scout import paths as vault_paths
from scout.errors import LabError
from scout.kb import hook_paths
from scout.kb.read import visibility_map
from scout.lab import paths, sandbox
from scout.lab.meter import ENV_METER, MeterServer
from scout.lab.model import EID_RE, SLUG_RE
from scout.lab.parser import parse_experiment
from scout.lab.weights import FLOOR_REASON as _WEIGHTS_FLOOR_REASON
from scout.lab.weights import Fetched
from scout.sandbox import broker, profile

ENV_EXPERIMENT_DIR = "SCOUT_LAB_EXPERIMENT_DIR"
TMP_NAME = ".tmp"
LEDGER_DIR = "lab-meter"
TAIL_BYTES = 4000
# The labels are read as a full reader (one of FULL_READERS), the way pre_session_data reads them: the runner
# must see every private file to deny it.
READER = "autonomous"
# Where a vault keeps private bytes outside knowledge-base/: every note's history (the git objects), the
# phone-notification inbox (privacy.DELEGATE_NEVER) and the engine's caches (the privacy index holds private
# notes' frontmatter).
INBOX_DIR = ".scout-inbox"
# Copies of private material at the vault root (ruling R18): Raw/ drops, action items, and other checkouts.
PRIVATE_ROOT_DIRS: tuple[str, ...] = ("Raw", "action-items", ".claude/worktrees")
# In a checkout that holds this vault (the vault is one of its worktrees), the parts that hold private bytes.
CHECKOUT_PRIVATE_DIRS: tuple[str, ...] = ("knowledge-base", "Raw", "action-items", INBOX_DIR, ".scout-cache")
_SWEEP_ROUNDS = 20
# Runs inside the sandbox ahead of the script: wait until the runner has checked the sandboxed process
# (R21), give the script /dev/null for stdin, cap the size of any file it writes, and become the script
# (same pid, same process group). Only the runner's "go" opens the gate: an end of file (the runner
# refused, or died before writing it) exits without running the script.
# bash, not sh: /bin/sh may be any shell (dash and POSIX-mode bash count `ulimit -f` in 512-byte blocks), while
# bash run as bash counts it in 1024-byte blocks in every version.
GATE_SHELL = "/bin/bash"
GATE_EXIT = 125
# Room, over the experiment's allowance, for the owner's other processes to grow into during the run before the
# kernel cap refuses the script a fork (:func:`_nproc_limit`).
NPROC_HEADROOM = 256
FILE_SIZE_BLOCKS = 1024 * 1024  # 1 GiB, in bash's 1024-byte `ulimit -f` blocks: the most any one file may hold
# The gate's $1 is the file-size limit (:func:`_file_size_blocks`), $2 the process limit (:func:`_nproc_limit`) and
# $3 the descriptor limit (the owner's ``lab.max_open_files``: it bounds how many open-but-unlinked files the script
# can keep, each capped by `ulimit -f`, besides the runner counting them, :func:`_total_bytes`); the script's argv
# follows them. Bash's `ulimit` without -S or -H sets the soft and the hard limit both, so the script can't raise any
# of them.
_GATE = (
    f'read -r _ || exit {GATE_EXIT}; exec </dev/null; ulimit -f "$1" || exit {GATE_EXIT}; '
    f'ulimit -u "$2" || exit {GATE_EXIT}; ulimit -n "$3" || exit {GATE_EXIT}; shift 3; exec "$@"'
)
# How often the runner checks a running script's totals (processes and bytes).
_POLL_S = 1.0
# What "half of the Mac's memory" is when the Mac won't say how much it has (a conservative fixed limit).
FALLBACK_MEMORY_BYTES = 8 * 1024**3
FLOOR_REASON = _WEIGHTS_FLOOR_REASON  # "free disk would fall below the floor", a weights fetch's too
ENV_WEIGHTS_DIR = "SCOUT_LAB_WEIGHTS_DIR"
PYVENV_CFG = "pyvenv.cfg"
OWNER_PYTHON_NAME = "python3"  # the argv[0] lab.python stands in for
# Apple's git, whichever PATH finds first: /usr/bin/git is a shim that starts one of the other two.
KNOWN_GITS = (
    "/usr/bin/git",
    "/Library/Developer/CommandLineTools/usr/bin/git",
    "/Applications/Xcode.app/Contents/Developer/usr/bin/git",
)
_KILL_WAIT_S = 5.0
_PS = "/bin/ps"
# Interpreters under a home folder (ruling R32): a conda prefix is marked by this folder; a version manager's shim
# lives in this folder, or is volta's one program.
CONDA_META = "conda-meta"
SHIMS_DIR = "shims"
VOLTA_SHIM = "volta-shim"


@dataclass
class RunOutcome:
    exit: int
    stdout_tail: str
    stderr_tail: str
    spent_usd: float  # the experiment's total spend (the ledger's), which the cap is held against
    timed_out: bool
    over_bytes: bool = False  # the folder and its scratch dir held more than max_total_bytes (W4)
    over_processes: bool = False  # the sandbox held more than max_processes processes at a poll (W4)
    over_disk_floor: bool = False  # the volume's free bytes fell under the owner's floor at a poll (spec §6.4)
    over_memory: bool = False  # the processes' physical footprints together passed the memory limit at a poll
    stopped: str | None = None  # why the caller's ``should_stop`` ended the run (a detached run's supervisor, §6.4)


class RunLockBusy(LabError):
    """Another run of the experiment holds its run lock ("already running")."""


class DiskFloorError(LabError):
    """Launching would leave the volume under the owner's free-disk floor: the experiment was not run. Exit 4 (not
    run), not 2 (refused), and the run broker doesn't count it against the run's allowance."""

    exit_code = 4

    def __init__(self) -> None:
        super().__init__(FLOOR_REASON)


def free_bytes(vault: Path) -> int | None:
    """Bytes free to this user on the volume holding ``vault`` (``f_bavail * f_frsize`` of the resolved path);
    None when the volume can't be read."""
    try:
        st = os.statvfs(os.path.realpath(vault))
    except OSError:
        return None
    return st.f_bavail * st.f_frsize


def physical_memory_bytes() -> int:
    """The Mac's physical memory (``hw.memsize``); :data:`FALLBACK_MEMORY_BYTES` when it can't be read."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return FALLBACK_MEMORY_BYTES
    return total if total > 0 else FALLBACK_MEMORY_BYTES


def memory_limit(max_memory_bytes: int) -> int:
    """The memory limit a run is held to: the owner's, or (0) half of :func:`physical_memory_bytes`."""
    return max_memory_bytes if max_memory_bytes > 0 else physical_memory_bytes() // 2


_RUSAGE_INFO_V4 = 4
_RUSAGE_BUF_BYTES = 512  # rusage_info_v4 is 304 bytes; room to spare
_PHYS_FOOTPRINT_OFFSET = 16 + 7 * 8  # ri_uuid[16], then ri_user_time .. ri_resident_size, then ri_phys_footprint
_rusage: Callable[[int, int, Any], int] | None = None


def footprint_bytes(pid: int) -> int | None:
    """The physical footprint of process ``pid`` (``ri_phys_footprint`` from libproc's ``proc_pid_rusage``; the
    figure Activity Monitor calls Memory, GPU allocations included). None when the call fails: a process that
    vanished, or one the kernel won't report."""
    global _rusage
    try:
        if _rusage is None:
            lib = ctypes.CDLL(sandbox._LIBPROC, use_errno=True)
            fn = lib.proc_pid_rusage
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _rusage = fn
        buf = ctypes.create_string_buffer(_RUSAGE_BUF_BYTES)
        if _rusage(pid, _RUSAGE_INFO_V4, buf) != 0:
            return None
        return int.from_bytes(buf.raw[_PHYS_FOOTPRINT_OFFSET : _PHYS_FOOTPRINT_OFFSET + 8], "little")
    except (OSError, AttributeError):
        return None


def _holders_footprint(holders: set[int]) -> int:
    """The footprints of ``holders`` added up; one that vanished counts 0."""
    return sum(footprint_bytes(pid) or 0 for pid in holders)


# --- checks ----------------------------------------------------------------------------


def _running_note(vault: Path, slug: str, eid: str) -> tuple[Path, bool]:
    """The experiment's note and whether it's private. Raises unless it exists and is ``running``."""
    if not re.match(SLUG_RE, slug):
        raise LabError(f"bad thread slug {slug!r}")
    if not re.fullmatch(EID_RE, eid):
        raise LabError(f"{eid!r} is not an experiment ID (E<n>)")
    note = paths.experiment_note(vault, slug, eid)
    if note is None:
        raise LabError(f"{slug}: no experiment note for {eid}")
    try:
        exp = parse_experiment(slug, note.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise LabError(f"cannot read {note.name}: {exc.__class__.__name__}") from None
    if exp.eid != eid:
        raise LabError(f"{note.name} has id {exp.eid!r}, not {eid}")
    if exp.status != "running":
        raise LabError(f"{eid} is {exp.status}, not running; start it before running its script")
    return note, exp.visibility == "private"


def _open_experiment_dir(vault: Path, note: Path) -> tuple[str, int]:
    """Create the experiment folder if needed; its resolved path and a descriptor on it.

    Refuses when the folder (or any folder above it, up to the vault) is a symlink: the path the
    profile allows must be the one inside the vault.
    """
    exp = paths.experiment_dir(note)
    vault_real = os.path.realpath(vault)
    expected = os.path.join(vault_real, *exp.relative_to(vault).parts)
    parent = os.path.dirname(expected)
    if os.path.realpath(exp.parent) != parent:
        raise LabError(f"{exp.parent} resolves outside the vault (a symlinked folder); refusing to run")
    with contextlib.suppress(FileExistsError):
        os.mkdir(exp, 0o755)
    if os.path.islink(exp) or os.path.realpath(exp) != expected:
        raise LabError(f"{exp.name} is a symlink or not inside the vault; refusing to run")
    try:
        fd = os.open(expected, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise LabError(f"cannot open {exp.name}: {exc.__class__.__name__}") from None
    st = os.fstat(fd)
    try:
        again = os.stat(expected, follow_symlinks=False)
    except OSError:
        again = None
    if again is None or (again.st_dev, again.st_ino) != (st.st_dev, st.st_ino) or os.path.realpath(exp) != expected:
        os.close(fd)
        raise LabError(f"{exp.name} changed while it was being opened; refusing to run")
    return expected, fd


def _clear(fd: int, name: str) -> None:
    """Remove ``name`` in the folder ``fd`` without following a link: a planted symlink is unlinked, not followed."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(name, dir_fd=fd)
    else:
        os.unlink(name, dir_fd=fd)


# --- what the profile denies -----------------------------------------------------------


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def private_lab_folders(vault: Path, labels: Mapping[str, str]) -> list[Path]:
    """The lab folders private as a whole (I4): a thread's folder when its front page isn't labeled ``normal``
    (one that is missing counts as private), and every experiment's folder, whatever its note says. A thread's
    other files (a timeline) label by their own frontmatter, so the front page's label is the one that holds for
    them. An experiment folder's files (scripts, outputs, run logs) are private by path (the shipped policy's
    ``visibility_forced_private``, whatever a file's frontmatter says): one experiment's folder is never
    another's input. Each experiment's folder is listed, the one beside a note and any other, even an empty one,
    so a file written into it later is denied too."""
    lab = paths.lab_dir(vault)
    out: list[Path] = []
    try:
        threads = sorted(p for p in lab.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return out
    for thread in threads:
        if labels.get(paths.front_page(vault, thread.name).relative_to(vault).as_posix()) != "normal":
            out.append(thread)
            continue
        folders = {paths.experiment_dir(note) for note in (thread / "experiments").glob(f"{thread.name}-E*.md")}
        with contextlib.suppress(OSError):
            folders.update(p for p in (thread / "experiments").iterdir() if p.is_dir() and not p.is_symlink())
        out.extend(sorted(folders))
    return out


def _deny_reads(vault: Path, exp_real: str) -> tuple[list[Path], list[Path]]:
    """(files and folders to deny, folders to deny whether or not they exist yet): every file under
    knowledge-base/ not labeled ``normal``, the private lab folders (:func:`private_lab_folders`), and the
    private bytes kept elsewhere.

    A path the labels leave out counts as private (the rule every :func:`visibility_map` caller
    follows), so a label failure denies the whole KB rather than none of it. The experiment's own
    folder is its own and is left readable. A folder holding only denied files is denied as one
    ``subpath``: the kernel refuses a profile whose deny list outgrows its 64 KB data limit (about
    two thousand file paths), and a private folder (``personal/``) is usually private as a whole.
    """
    labels = visibility_map(vault, READER, ["knowledge-base/**"], who="lab runner")
    private_folders: list[Path] = []
    holding: list[str] = []
    for folder in private_lab_folders(vault, labels):
        if os.path.realpath(folder) == exp_real:
            continue  # the experiment's own folder: the profile allows it back explicitly
        if not _within(exp_real, os.path.realpath(folder)):
            private_folders.append(folder)
            continue
        # A private thread holding this (normal) experiment: its other files are denied one by one, and its
        # other experiments' folders as wholes.
        holding.append(os.path.realpath(folder))
        for note in sorted((folder / "experiments").glob(f"{folder.name}-E*.md")):
            other = paths.experiment_dir(note)
            if os.path.realpath(other) != exp_real:
                private_folders.append(other)
    private_reals = [os.path.realpath(p) for p in private_folders]

    def private_file(path: Path) -> bool:
        real = os.path.realpath(path)
        return any(_within(real, root) for root in [*private_reals, *holding])

    kb = vault / "knowledge-base"
    # Per folder, bottom-up: (anything readable below it, anything denied below it, its deny entries).
    seen: dict[str, tuple[bool, bool, list[Path]]] = {}
    for root, dirs, files in os.walk(kb, topdown=False):
        base = Path(root)
        readable = _within(exp_real, os.path.realpath(base))  # a folder holding the experiment stays open
        denied = False
        entries: list[Path] = []
        for name in files:
            path = base / name
            if _within(os.path.realpath(path), exp_real) or (
                labels.get(path.relative_to(vault).as_posix()) == "normal" and not private_file(path)
            ):
                readable = True
            else:
                denied = True
                entries.append(path)
        for name in dirs:
            path = base / name
            if path.is_symlink():  # not walked, so not labeled here: its target is denied as a whole
                if _within(os.path.realpath(path), exp_real):
                    readable = True
                else:
                    denied = True
                    entries.append(path)
                continue
            sub_readable, sub_denied, sub_entries = seen.pop(str(path), (False, False, []))
            readable = readable or sub_readable
            denied = denied or sub_denied
            entries += [path] if sub_denied and not sub_readable else sub_entries
        seen[str(base)] = (readable, denied, entries)
    kb_readable, kb_denied, out = seen.get(str(kb), (False, False, []))
    if kb_denied and not kb_readable:
        out = [kb]
    # The private lab folders as wholes, so a file written into one later (another run's output) is denied too.
    whole: list[Path] = []
    for folder, real in zip(private_folders, private_reals, strict=True):
        if any(_within(real, os.path.realpath(done)) for done in out):
            continue  # already under a folder denied as a whole (the KB, say)
        out = [p for p in out if not _within(os.path.realpath(p), real)]
        whole.append(folder)
    for gitdir in [vault / ".git", *hook_paths.git_dirs(vault)]:
        if gitdir.is_dir() and gitdir not in out:
            out.append(gitdir)
    for folder in (vault / INBOX_DIR, vault_paths.cache_dir(vault)):
        if folder.is_dir():
            out.append(folder)
    vault_real = os.path.realpath(vault)

    def tree(path: Path) -> None:
        real = os.path.realpath(path)
        if not os.path.isdir(real) or _within(vault_real, real) or _within(exp_real, real):
            return
        if not any(_within(real, os.path.realpath(done)) for done in out):  # not already under a denied folder
            out.append(path)

    for name in PRIVATE_ROOT_DIRS:
        tree(vault / name)
    for checkout in other_checkouts(vault):
        if _within(vault_real, os.path.realpath(checkout)):
            for name in CHECKOUT_PRIVATE_DIRS:
                tree(checkout / name)
        else:
            tree(checkout)
    return out, whole


def other_checkouts(vault: Path) -> list[Path]:
    """Every other working tree of the vault's repo: the main checkout and each ``<gitdir>/worktrees/*``."""
    vault_real = os.path.realpath(vault)
    found: list[Path] = []
    for gitdir in [vault / ".git", *hook_paths.git_dirs(vault)]:
        if not gitdir.is_dir():
            continue
        candidates: list[Path] = []
        # git_dirs gives a linked worktree's common dir as "<gitdir>/../..": compare the resolved name.
        real_gitdir = Path(os.path.realpath(gitdir))
        if real_gitdir.name == ".git":
            candidates.append(real_gitdir.parent)
        for pointer in sorted(gitdir.glob("worktrees/*/gitdir")):
            try:
                target = Path(pointer.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeDecodeError):
                continue
            target = target if target.is_absolute() else pointer.parent / target
            candidates.append(target.parent)
        for checkout in candidates:
            real = os.path.realpath(checkout)
            if real != vault_real and all(os.path.realpath(f) != real for f in found):
                found.append(checkout)
    return found


def _deny_execs(path_values: Sequence[str | None]) -> list[Path]:
    out: list[Path] = []
    for name in ("git", "claude"):
        for value in path_values:
            found = shutil.which(name, path=value)
            if found is not None and Path(os.path.realpath(found)) not in out:
                out.append(Path(os.path.realpath(found)))
    out += [Path(p) for p in KNOWN_GITS if os.path.exists(p) and Path(p) not in out]
    return out


def _encoded(path: str) -> str:
    """Claude Code's name for a project folder under ``~/.claude/projects/``: every character but a letter or a
    digit becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _below(path: str, root: str) -> int | None:
    """How many folders ``path`` is below ``root`` (0 for ``root`` itself), or None when it isn't inside it."""
    if path == root:
        return 0
    if not path.startswith(root.rstrip("/") + "/"):
        return None
    return len(path[len(root.rstrip("/")) + 1 :].split("/"))


def _interpreter_root(real: str, homes: Sequence[str]) -> str:
    """The folder to allow back for an interpreter at ``real``: its install prefix (``<prefix>/bin/python3``),
    narrowed when the prefix is a home folder, above one, or a folder right inside one (``~/.local``), to the
    interpreter's own folder, or to the file itself. A conda prefix right inside a home folder (it holds
    ``conda-meta/``) is allowed whole."""

    def too_wide(folder: str) -> bool:
        return any(_within(home, folder) or _below(folder, home) == 1 for home in homes)

    prefix = os.path.dirname(os.path.dirname(real))
    # A conda base install (~/miniconda3) sits right inside a home folder; ``conda-meta/`` says the folder is one.
    if (
        any(_below(prefix, home) == 1 for home in homes)
        and not any(_within(home, prefix) for home in homes)
        and os.path.isdir(os.path.join(prefix, CONDA_META))
    ):
        return prefix
    for folder in (prefix, os.path.dirname(real)):
        if not too_wide(folder):
            return folder
    return real


def _is_shim(real: str) -> bool:
    """``real`` (a resolved interpreter) is a version manager's shim: in a ``shims/`` folder (pyenv, asdf, rbenv)
    or volta's ``volta-shim``."""
    path = Path(real)
    return SHIMS_DIR in path.parts[:-1] or path.name == VOLTA_SHIM


def _owner_python_roots(python: str, homes: Sequence[str]) -> list[str]:
    """What to allow back for the owner's ``lab.python``: the interpreter it runs (:func:`_interpreter_root` of its
    resolved path), and, when it is a venv's (``<venv>/bin/python3`` beside ``<venv>/pyvenv.cfg``), the venv's folder,
    where its packages are. Only an interpreter in a folder named ``bin`` makes one, and never a folder that is a home
    folder or above one, as spelled or resolved."""
    out = [_interpreter_root(os.path.realpath(python), homes)]
    bin_dir = os.path.dirname(os.path.abspath(python))
    if os.path.basename(bin_dir) != "bin":
        return out
    spelled = os.path.dirname(bin_dir)
    venv = os.path.realpath(spelled)
    too_wide = any(_within(h, folder) for h in homes for folder in (venv, spelled))
    if os.path.isfile(os.path.join(venv, PYVENV_CFG)) and venv != "/" and not too_wide:
        out.append(venv)
    return out


def check_owner_python(python: str | None) -> None:
    """Raises :class:`LabError` unless ``python`` is None or an absolute path to an executable file that isn't a
    version manager's shim."""
    if python is None:
        return
    if not os.path.isabs(python) or not os.path.isfile(python) or not os.access(python, os.X_OK):
        raise LabError(f"the owner's lab.python {python!r} isn't an executable file; not run")
    if _is_shim(os.path.realpath(python)):
        raise LabError(f"the owner's lab.python {python} is a version-manager shim; name the real interpreter")


def owner_python_argv(argv: Sequence[str], python: str | None) -> list[str]:
    """``argv`` with a leading ``python3`` replaced by the owner's ``lab.python`` when it is set. The broker has
    already checked ``python3`` against its allowlist of interpreters; nothing else is replaced."""
    if python is not None and argv and argv[0] == OWNER_PYTHON_NAME:
        return [python, *argv[1:]]
    return list(argv)


def _allow_reads(vault: Path, source: Mapping[str, str], extra: Sequence[str], python: str | None = None) -> list[Path]:
    """What a script may read under a home folder (ruling R32): the vault, the engine and the interpreter
    running it, the vault's Claude Code session transcripts (spec §6.3), the allowlisted interpreters the run's
    PATH finds, the owner's ``lab.python`` (:func:`_owner_python_roots`), and ``extra`` (the runner's snapshot of
    the experiment's scripts).

    Raises :class:`LabError` when the script's PATH finds a version-manager shim first for an allowlisted
    interpreter (:func:`_is_shim`): a shim reads its manager's settings under the home folder, which the sandbox
    denies, so it would fail inside the script with an error that doesn't say why."""
    import sys

    for name in sorted(broker.EXPERIMENT_INTERPRETERS):
        found = shutil.which(name, path=source.get("PATH"))
        if found is not None and _is_shim(os.path.realpath(found)):
            raise LabError(f"{found} is a version-manager shim; put the real interpreter first on PATH")
    homes = sandbox.homes(source)
    out: list[str] = [os.path.realpath(vault), *extra]
    out.append(str(Path(__file__).resolve().parents[2]))  # the engine (plugin/engine), its .venv included
    for prefix in (sys.prefix, sys.exec_prefix, sys.base_prefix):
        out.append(os.path.realpath(prefix))
    for spelling in {os.path.realpath(vault), os.path.abspath(vault)}:
        for home in homes:
            out.append(os.path.join(home, ".claude", "projects", _encoded(spelling)))
    for name in sorted(broker.EXPERIMENT_INTERPRETERS):
        for value in (source.get("PATH"), os.environ.get("PATH")):
            found = shutil.which(name, path=value)
            if found is not None:
                out.append(_interpreter_root(os.path.realpath(found), homes))
    if python is not None:
        out += _owner_python_roots(python, homes)
    seen: list[Path] = []
    for path in out:
        if Path(path) not in seen:
            seen.append(Path(path))
    return seen


def ledger_path(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Path:
    """The experiment's spending ledger, in the owner's run-broker folder (created 0700)."""
    return _owner_file(vault, slug, eid, env, ".jsonl")


def run_lock_path(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Path:
    """The experiment's run lock, beside its ledger: held (``flock``) for the whole of a run."""
    return _owner_file(vault, slug, eid, env, ".lock")


# Run locks a supervisor holds for its whole life (:func:`hold_run_lock`): lock path -> the thread holding it.
_HELD_FOR_LIFE: dict[str, int] = {}


@contextlib.contextmanager
def hold_run_lock(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Iterator[None]:
    """Hold the experiment's run lock across more than the run itself (a detached run's supervisor, spec §6.4: from
    before its weights fetch until its outcome is recorded). :func:`run_experiment` called from the same thread
    inside it runs under this hold rather than refusing itself; any other run is refused ("already running")."""
    key = str(run_lock_path(vault, slug, eid, env))
    with _run_lock(vault, slug, eid, env):
        _HELD_FOR_LIFE[key] = threading.get_ident()
        try:
            yield
        finally:
            _HELD_FOR_LIFE.pop(key, None)


@contextlib.contextmanager
def _run_lock(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Iterator[None]:
    """Hold the experiment's run lock, or raise :class:`LabError` at once when another run of it holds the lock.
    The lock file is write-only (0200), like the broker's own locks: a run can't open it to hold it. Already held
    by this thread through :func:`hold_run_lock`, it is used as it is."""
    path = run_lock_path(vault, slug, eid, env)
    if _HELD_FOR_LIFE.get(str(path)) == threading.get_ident():
        yield
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o200)
    except OSError as exc:
        raise LabError(f"cannot open the experiment's run lock {path} ({exc.__class__.__name__}); not run") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunLockBusy(f"experiment {slug} {eid} is already running") from None
        yield  # closing the file below lets the lock go
    finally:
        os.close(fd)


def _owner_file(vault: Path, slug: str, eid: str, env: Mapping[str, str], suffix: str) -> Path:
    folder = broker.owner_dir(env) / LEDGER_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
    except OSError as exc:
        # A scheduled run's own sandbox can't write the owner's folder: there, experiments go through the broker.
        raise LabError(
            f"cannot write the owner's model-call ledger folder {folder} ({exc.__class__.__name__}); "
            "inside a scheduled run, experiments run through the run broker, not directly"
        ) from None
    key = hashlib.sha256(os.path.realpath(vault).encode("utf-8")).hexdigest()[:16]
    return folder / f"{key}-{slug}-{eid}{suffix}"


# --- processes -------------------------------------------------------------------------


def _own_live_pids() -> list[int]:
    """This user's processes other than this one, zombies left out (one ``ps`` snapshot)."""
    try:
        listing = subprocess.run(
            [_PS, "-A", "-o", "pid=", "-o", "uid=", "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    uid, me = os.getuid(), os.getpid()
    out: list[int] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            pid = int(parts[0])
            if int(parts[1]) == uid and pid != me and not parts[2].startswith("Z"):
                out.append(pid)
    return out


# Run by :func:`_owner_process_count` as its own process (single-threaded, unlike the runner): the lowest
# RLIMIT_NPROC soft limit at which it can still fork is the kernel's count of the user's processes, itself included,
# plus one (the child). Each child it forks exits at once and is reaped before the next try.
_COUNT_HELPER = """
import os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
def can_fork(limit):
    # The hard limit read afresh each time: the kernel lowers it to kern.maxprocperuid at the first set.
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (limit, resource.getrlimit(resource.RLIMIT_NPROC)[1]))
    except (ValueError, OSError):
        sys.exit(1)
    try:
        pid = os.fork()
    except OSError:
        return False
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return True
lo, hi = 1, min(x for x in (soft, hard, 1 << 20) if x != resource.RLIM_INFINITY)
if not can_fork(hi):
    sys.exit(1)
while lo < hi:
    mid = (lo + hi) // 2
    if can_fork(mid):
        hi = mid
    else:
        lo = mid + 1
# The count, then the per-user ceiling as the kernel now reports it (-1: none).
hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
print(lo - 2, -1 if hard == resource.RLIM_INFINITY else hard)
"""


def _process_counts() -> tuple[int, int | None] | None:
    """This user's process count, as the kernel counts them for ``RLIMIT_NPROC``, and the per-user ceiling the
    kernel enforces (None: no ceiling); None when they can't be found. Asked of the kernel itself
    (:data:`_COUNT_HELPER`), not read from ``ps``: on macOS 26 the kernel's count ran 7 to 12 below the processes
    ``ps`` lists for the user's real uid, so a limit built on ``ps`` would have let a fork bomb have that many more.
    The ceiling is read after the helper has set the limit once: before that, macOS reports no ceiling where
    ``kern.maxprocperuid`` holds one, and a gate asked for more than it can't apply its limit."""
    import sys

    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _COUNT_HELPER],
            capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    parts = proc.stdout.split()
    if proc.returncode != 0 or len(parts) != 2 or not parts[0].isdigit():
        return None
    count, ceiling = parts
    if ceiling == "-1":
        return int(count), None
    if not ceiling.isdigit():
        return None
    return int(count), int(ceiling)


def _owner_process_count() -> int | None:
    """How many processes this user has, as the kernel counts them (:func:`_process_counts`)."""
    counts = _process_counts()
    return None if counts is None else counts[0]


def _nproc_limit(max_processes: int) -> int:
    """The script's ``RLIMIT_NPROC``: the user's process count now, plus ``max_processes`` (the script, created
    next, is the first of them), plus :data:`NPROC_HEADROOM`, never over the per-user ceiling nor this process's
    hard limit, which the gate couldn't raise to. When the ceiling leaves no room for ``max_processes`` the
    experiment isn't run, with an error that says so, rather than started to fail at its first fork."""
    counts = _process_counts()
    if counts is None:
        raise LabError("cannot count this user's processes to set the experiment's process limit; not run")
    count, ceiling = counts
    caps = [c for c in (ceiling, resource.getrlimit(resource.RLIMIT_NPROC)[1]) if c is not None]
    caps = [c for c in caps if c != resource.RLIM_INFINITY]
    cap = min(caps) if caps else None
    if cap is not None and cap < count + max_processes:
        raise LabError(
            f"process cap reached: this user has {plural.count(count, 'process', 'processes')} and the per-user "
            f"limit is {cap}, which leaves no room for the experiment's {max_processes}; not run"
        )
    limit = count + max_processes + NPROC_HEADROOM
    return limit if cap is None else min(limit, cap)


def _file_size_blocks(max_total_bytes: int) -> int:
    """The gate's ``ulimit -f`` (bash's 1024-byte blocks): no one file may hold more than the run's whole bytes cap,
    nor more than 1 GiB."""
    return max(1, min(FILE_SIZE_BLOCKS, -(-max_total_bytes // 1024)))


def _tree_bytes(dir_fd: int, seen: set[tuple[int, int]] | None = None) -> int | None:
    """The bytes the regular files under the folder ``dir_fd`` take up: each file's size or its allocated blocks,
    whichever is more, each inode once, found by ``lstat`` without following a link. None when a folder in it can't
    be listed or opened (a script that made one unreadable has hidden what it holds, so the caller counts that as
    over). One descriptor is open per level of folders, not per folder. ``seen`` collects the ``(dev, ino)`` of
    every file counted."""
    seen = set() if seen is None else seen
    total = 0

    def scan(fd: int) -> list[str]:
        nonlocal total
        folders: list[str] = []
        for name in os.listdir(fd):
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:  # removed while the walk ran
                continue
            if stat.S_ISDIR(st.st_mode):
                folders.append(name)
            elif stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += max(st.st_size, st.st_blocks * 512)
        return folders

    stack: list[tuple[int, list[str]]] = []
    try:
        stack.append((os.dup(dir_fd), []))
        stack[-1] = (stack[-1][0], scan(stack[-1][0]))
        while stack:
            fd, pending = stack[-1]
            if not pending:
                os.close(stack.pop()[0])
                continue
            try:
                child = os.open(pending.pop(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                continue
            stack.append((child, []))
            stack[-1] = (child, scan(child))
    except OSError:
        return None
    finally:
        for fd, _ in stack:
            with contextlib.suppress(OSError):
                os.close(fd)
    return total


def _total_bytes(dir_fd: int, open_files: sandbox.OpenFiles, holders: set[int]) -> int | None:
    """The run's bytes: the folder ``dir_fd`` (:func:`_tree_bytes`) plus every open-but-unlinked regular file one of
    ``holders`` (this run's sandboxed processes) holds, each inode once and none the folder already counted. A file
    opened and then removed (``tempfile.TemporaryFile``, SQLite's temp store) keeps its bytes but no name a walk
    could find. None, which the caller counts as over, when the folder can't be walked or a live holder's open files
    can't be listed; a holder that exited meanwhile is skipped."""
    seen: set[tuple[int, int]] = set()
    total = _tree_bytes(dir_fd, seen)
    if total is None:
        return None
    for pid in sorted(holders):
        try:
            files = open_files.unlinked(pid)
        except OSError:
            return None
        for dev, ino, size in files or []:
            if (dev, ino) not in seen:
                seen.add((dev, ino))
                total += size
    return total


def _decoy(marker: str) -> str:
    return marker + "-denied"


def _holds_profile(probe: sandbox.SandboxProbe, pid: int, marker: str, outside: str) -> bool:
    """The kernel says ``pid``'s sandbox is this run's: it may write ``marker`` but not the decoy beside it
    (a sandbox that allows all of /tmp, as several of the user's own daemons' do, allows both), and may
    not write ``outside``."""
    return (
        probe.sandboxed(pid)
        and probe.denies(pid, "file-write-data", outside) is True
        and probe.denies(pid, "file-write-data", _decoy(marker)) is True
        and probe.denies(pid, "file-write-data", marker) is False
    )


def _holders(probe: sandbox.SandboxProbe, marker: str, outside: str) -> set[int]:
    return {pid for pid in _own_live_pids() if _holds_profile(probe, pid, marker, outside)}


def _marker_ok(marker: str) -> bool:
    """The probe file is still a plain file (not removed, not a link): the sweep's answers mean something."""
    try:
        return all(stat.S_ISREG(os.lstat(p).st_mode) for p in (marker, _decoy(marker)))
    except OSError:
        return False


def _sweep(probe: sandbox.SandboxProbe, marker: str, outside: str) -> bool:
    """Kill every process holding this run's sandbox; True once a sweep finds none (False if it can't tell)."""
    for _ in range(_SWEEP_ROUNDS):
        if not _marker_ok(marker):
            return False
        frozen: set[int] = set()
        for _ in range(50):  # freeze first, so nothing forks while the rest are found
            new = _holders(probe, marker, outside) - frozen
            if not new:
                break
            for pid in new:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGSTOP)
            frozen |= new
        if not frozen:
            return True
        for pid in frozen:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.05)
    return _marker_ok(marker) and not _holders(probe, marker, outside)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_all(proc: subprocess.Popen[bytes], probe: sandbox.SandboxProbe, marker: str, outside: str) -> bool:
    """SIGKILL the script's process group, reap it, then sweep out every process still holding the
    experiment's sandbox, wherever it moved to. True when none is left."""
    pgid = proc.pid
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_KILL_WAIT_S)
    clean = _sweep(probe, marker, outside)
    deadline = time.monotonic() + _KILL_WAIT_S
    while _group_alive(pgid) and time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.05)
    return clean and not _group_alive(pgid)


def _positive_control(proc: subprocess.Popen[bytes], probe: sandbox.SandboxProbe, marker: str, outside: str) -> bool:
    """R21: the kernel's answers about the gated, sandboxed leader are the expected ones.

    True also when the leader is already gone: then ``sandbox-exec`` itself failed (a profile the kernel
    rejected), the gate never opened and nothing of the script ran; the caller reports that exit.
    """
    deadline = time.monotonic() + _KILL_WAIT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        if probe.sandboxed(proc.pid):
            return _holds_profile(probe, proc.pid, marker, outside)
        time.sleep(0.01)
    return False


def _new_file(path: str) -> IO[bytes]:
    """A new file in the runner's own temp dir, opened to write: never through a name already there."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")


def _tail(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(size - TAIL_BYTES, 0))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# --- the run log -----------------------------------------------------------------------


_RUN_LOG_RE = re.compile(r"run-\d{8}-\d{4}(?:-\d{1,2})?\.log\Z")


def runner_owned(rel: str) -> bool:
    """Whether ``rel`` (a path inside the experiment folder) is the runner's own: its scratch dir, or a run log
    :func:`_write_log` names. The run broker leaves these out when it asks whether the experiment is committed."""
    parts = rel.split("/")
    return parts[0] == TMP_NAME or (len(parts) == 1 and bool(_RUN_LOG_RE.match(parts[0])))


def _write_log(fd: int, started: _dt.datetime, body: str) -> str | None:
    """Write ``run-<YYYYMMDD-HHMM>.log`` (``-2``, ``-3``… if taken) in the folder ``fd``, never through a link."""
    stem = f"run-{started.strftime('%Y%m%d-%H%M')}"
    for n in range(1, 100):
        name = f"{stem}.log" if n == 1 else f"{stem}-{n}.log"
        try:
            log_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        except FileExistsError:
            continue
        except OSError:
            return None
        with os.fdopen(log_fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        return name
    return None


def _log_body(
    argv: Sequence[str],
    started: _dt.datetime,
    finished: _dt.datetime,
    outcome: RunOutcome,
    spent_before: float,
    cap_usd: float,
    max_processes: int,
    max_total_bytes: int,
    nproc: int,
    floor: int,
    mem_limit: int,
    detached: bool,
    deadline: _dt.datetime,
    ml: _ML,
) -> str:
    return "\n".join(
        [
            f"started: {started.strftime('%Y-%m-%d %H:%M')}",
            f"finished: {finished.strftime('%Y-%m-%d %H:%M')}",
            f"detached: {'true' if detached else 'false'}",
            f"deadline: {deadline.strftime('%Y-%m-%d %H:%M')}",
            f"argv: {json.dumps(list(argv), ensure_ascii=False)}",
            f"exit: {outcome.exit}",
            f"timed_out: {'true' if outcome.timed_out else 'false'}",
            f"stopped: {outcome.stopped or 'no'}",
            f"over_processes: {'true' if outcome.over_processes else 'false'} (limit {max_processes}; "
            f"the kernel refuses forks past {nproc} of the owner's processes)",
            f"over_bytes: {'true' if outcome.over_bytes else 'false'} (cap {max_total_bytes} bytes)",
            f"over_disk_floor: {'true' if outcome.over_disk_floor else 'false'} (floor {floor} bytes free)",
            f"over_memory: {'true' if outcome.over_memory else 'false'} (limit {mem_limit} bytes)",
            f"spent_usd: {outcome.spent_usd:.4f} (this run {max(outcome.spent_usd - spent_before, 0.0):.4f}, "
            f"cap {cap_usd:.2f})",
            *ml.log_lines(),
            "--- stdout (tail) ---",
            outcome.stdout_tail.rstrip("\n"),
            "--- stderr (tail) ---",
            outcome.stderr_tail.rstrip("\n"),
            "",
        ]
    )


# --- run -------------------------------------------------------------------------------


def run_experiment(
    vault: Path,
    slug: str,
    eid: str,
    argv: Sequence[str],
    *,
    cap_usd: float,
    timeout_s: float = 1800,
    env: Mapping[str, str] | None = None,
    claude_argv: Sequence[str] = ("claude",),
    commit: str | None = None,
    max_processes: int = broker.LabLimits.max_processes,
    max_total_bytes: int = broker.LabLimits.max_total_bytes,
    max_open_files: int = broker.LabLimits.max_open_files,
    disk_free_floor_bytes: int = broker.LabLimits.disk_free_floor_bytes,
    max_memory_bytes: int = 0,
    on_started: Callable[[str, str], None] | None = None,
    should_stop: Callable[[], str | None] | None = None,
    weights: Sequence[Fetched] = (),
    weights_root: Path | None = None,
    python: str | None = None,
) -> RunOutcome:
    """Run ``argv`` for experiment ``eid`` of thread ``slug`` under the experiment sandbox.

    ``env`` supplies the script's ``PATH``, ``HOME`` and ``LANG`` and the HOME the owner's ledger
    folder is found under (default: this process's environment). ``claude_argv`` is what the meter
    runs for each model call; a bare program name is resolved on this process's PATH, outside the
    sandbox. Raises :class:`LabError` unless the experiment is ``running`` and ``sandbox-exec`` can
    apply the profile, and at once when another run of the same experiment holds its run lock
    (:func:`run_lock_path`). Doesn't change the experiment's status and never commits.

    ``commit`` is the commit the run broker validated (:func:`scout.sandbox.broker.validate_experiment`), which
    every caller that runs a reviewed plan passes (ruling R30): the scripts then run from a snapshot of the
    experiment folder as committed there, outside what the script or the run can write, so a script rewritten
    in the worktree after the check isn't what runs; ``argv`` must be the form the broker returns
    (``./<script> …`` or ``<interpreter> <script> …``). The experiment is private only when its note says so
    both there and in the worktree. Without ``commit`` (a test of the sandbox itself) ``argv`` runs as given.

    ``max_processes`` and ``max_total_bytes`` are the owner's totals for the run (see the module docstring); a run
    killed for either still returns its outcome, with ``over_processes`` or ``over_bytes`` set. ``max_open_files``
    is the descriptors the script may hold open (the gate's ``ulimit -n``).

    ``max_total_bytes`` is the experiment's disk budget (the broker passes the plan's, clamped to the owner's
    maximum). ``disk_free_floor_bytes`` (default 15 GB; 0 = none) is the free space the volume must keep: a launch
    that would leave less raises :class:`DiskFloorError`, and a run that sees less at a poll is killed
    (``over_disk_floor``). ``max_memory_bytes`` is the processes' physical footprint together (0 = half of the
    Mac's memory); past it the run is
    killed (``over_memory``).

    Two hooks serve a detached run's supervisor (spec §6.4; :mod:`scout.lab.supervise`), and mark the run log
    ``detached: true``. ``on_started(marker, outside)`` is called once the gate's kernel checks have passed, before
    the gate opens: ``marker`` is the sweep's probe file and ``outside`` the folder the sandbox may not write, the two
    paths :func:`_sweep` needs to find the script's processes if this process dies. When it raises, the gate never
    opens. ``should_stop()`` is asked at every poll; a reason it returns kills the run through the timeout's kill path
    and is the outcome's ``stopped``.

    ``weights`` are the model files the supervisor fetched into the cache at ``weights_root``
    (:func:`scout.lab.weights.fetch`): each named revision folder is allowed back to read only, the script gets
    ``$SCOUT_LAB_WEIGHTS_DIR`` (``weights_root``) and the run log lists every file. ``python`` is the owner's
    ``lab.python``: a leading ``python3`` in ``argv`` runs it (:func:`owner_python_argv`). Raises :class:`LabError`
    when a revision folder isn't a real folder of the cache, or ``python`` isn't an executable file.
    """
    source = os.environ if env is None else env
    if not argv:
        raise LabError("nothing to run: the experiment command is empty")
    if isinstance(cap_usd, bool) or not math.isfinite(cap_usd) or cap_usd <= 0:
        raise LabError(f"cost cap is {cap_usd!r}; it must be a finite number greater than 0")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise LabError(f"timeout is {timeout_s!r}; it must be a finite number of seconds greater than 0")
    for name, value in (
        ("max processes", max_processes),
        ("max total bytes", max_total_bytes),
        ("max open files", max_open_files),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise LabError(f"{name} is {value!r}; it must be a whole number greater than 0")
    for name, value in (("free disk floor", disk_free_floor_bytes), ("max memory", max_memory_bytes)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LabError(f"{name} is {value!r}; it must be a whole number, 0 or more")
    if commit is not None and not _SHA_RE.match(commit):
        raise LabError(f"{commit!r} is not a commit id")
    check_owner_python(python)
    ml = _ML(tuple(weights), weights_dirs(weights, weights_root), python)
    note, private = _running_note(vault, slug, eid)  # checks the slug and EID the lock file is named by
    with _run_lock(vault, slug, eid, source):
        exp_rel = paths.experiment_dir(note).relative_to(vault).as_posix()
        if commit is not None:
            private = private and _committed_private(vault, slug, eid, commit, note)
        exp_real, exp_fd = _open_experiment_dir(vault, note)
        try:
            if not profile.available():
                raise LabError("experiments need macOS sandbox-exec, which isn't available here; not run")
            source_tree = None if commit is None else (commit, exp_rel)
            free = free_bytes(vault)
            if disk_free_floor_bytes > 0 and (free is None or free - max_total_bytes < disk_free_floor_bytes):
                raise DiskFloorError
            totals = (
                max_processes,
                max_total_bytes,
                max_open_files,
                disk_free_floor_bytes,
                memory_limit(max_memory_bytes),
            )
            return _run(
                vault,
                slug,
                eid,
                argv,
                private,
                exp_real,
                exp_fd,
                cap_usd,
                timeout_s,
                source,
                claude_argv,
                source_tree,
                totals,
                (on_started, should_stop),
                ml,
            )
        finally:
            os.close(exp_fd)


@dataclass(frozen=True)
class _ML:
    """What an ML experiment adds to a run (spec §6.4): the cached weights, their revision folders (resolved) and
    the cache's root, and the owner's Python."""

    weights: tuple[Fetched, ...] = ()
    dirs: tuple[tuple[str, ...], str | None] = ((), None)  # (revision folders, root), as weights_dirs returns
    python: str | None = None

    def log_lines(self) -> list[str]:
        lines = [f"python: {self.python}"] if self.python else []
        folders, root = self.dirs
        if root is not None:
            lines.append(f"weights_dir: {root}")
        for f in self.weights:
            folder = os.path.join(root or "", f.repo, f.revision)
            real = os.path.realpath(f.path)
            name = real[len(folder) + 1 :] if real.startswith(folder + os.sep) else Path(f.path).name
            lines.append(f"weights: {f.repo}@{f.revision} {name} sha256:{f.sha256} ({f.size} bytes)")
        return lines


def weights_dirs(weights: Sequence[Fetched], root: Path | None) -> tuple[tuple[str, ...], str | None]:
    """The resolved revision folders ``weights`` are in, and the cache's resolved root (None without weights).
    Raises :class:`LabError` unless each is a real folder ``<root>/<repo>/<revision>`` (no link on the way) holding
    its files."""
    from scout.lab.model import model_repo_ok, model_revision_ok

    if not weights:
        return (), None
    if root is None:
        raise LabError("model weights were given without the cache that holds them; not run")
    root_real = os.path.realpath(root)
    out: list[str] = []
    for f in weights:
        if not model_repo_ok(f.repo) or not model_revision_ok(f.revision):
            raise LabError(f"{f.repo!r}@{f.revision!r} isn't a model pinned to a commit; not run")
        folder = os.path.join(root_real, f.repo, f.revision)
        if os.path.realpath(folder) != folder or not os.path.isdir(folder):
            raise LabError(f"the weights for {f.repo}@{f.revision} aren't in the cache; not run")
        if not os.path.realpath(f.path).startswith(folder + os.sep) or not os.path.isfile(f.path):
            raise LabError(f"a weights file of {f.repo}@{f.revision} isn't in its cache folder; not run")
        if folder not in out:
            out.append(folder)
    return tuple(out), root_real


def script_env(
    source: Mapping[str, str],
    tmp_real: str,
    sock: Path,
    exp_real: str,
    src: str | None,
    weights_root: Path | str | None,
) -> dict[str, str]:
    """The script's whole environment, built from nothing: ``PATH``, ``HOME`` and ``LANG`` of ``source``, its
    scratch dir, the meter's socket and its folders. Nothing else of ``source`` (a token, ``HF_TOKEN``) reaches it."""
    env = {
        "PATH": source.get("PATH") or "/usr/bin:/bin",
        "HOME": source.get("HOME") or str(Path.home()),
        "LANG": source.get("LANG") or "en_US.UTF-8",
        "TMPDIR": tmp_real,
        "PYTHONDONTWRITEBYTECODE": "1",
        ENV_METER: str(sock),
        ENV_EXPERIMENT_DIR: exp_real,
    }
    if src is not None:
        env[ENV_SOURCE_DIR] = src  # the committed scripts; the working folder stays exp_real
    if weights_root is not None:
        env[ENV_WEIGHTS_DIR] = os.path.realpath(weights_root)
    return env


# --- the committed scripts (ruling R30) --------------------------------------------------

_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_GIT_TIMEOUT_S = 60
ENV_SOURCE_DIR = "SCOUT_LAB_SOURCE_DIR"
SOURCE_NAME = "src"


def _git(vault: Path, *args: str) -> bytes | None:
    """``git`` in the vault with this process's PATH, HOME and LANG only (no GIT_* from anywhere); its output,
    or None when it fails."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if os.environ.get(k)}
    env.setdefault("PATH", "/usr/bin:/bin")
    env.update({"GIT_LITERAL_PATHSPECS": "1", "GIT_PAGER": "cat", "GIT_CONFIG_NOSYSTEM": "1"})
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args], cwd=str(vault), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=_GIT_TIMEOUT_S, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _committed_private(vault: Path, slug: str, eid: str, commit: str, note: Path) -> bool:
    """Whether the note as committed at ``commit`` is private, as the privacy layer reads its ``visibility``
    (False when it can't be read)."""
    data = _git(vault, "show", f"{commit}:./{note.relative_to(vault).as_posix()}")
    if data is None:
        return False
    try:
        exp = parse_experiment(slug, data.decode("utf-8"))
    except UnicodeDecodeError:
        return False
    return exp.eid == eid and exp.visibility == "private"


def _snapshot(vault: Path, commit: str, exp_rel: str, dest: str) -> None:
    """The experiment folder's tree at ``commit``, extracted into ``dest`` (a new folder in the runner's 0700
    temp dir, which the profile doesn't let the script write). Links out of it and special files are refused."""
    data = _git(vault, "archive", "--format=tar", f"{commit}:{exp_rel}")
    if data is None:
        raise LabError(f"cannot read the experiment's scripts at commit {commit[:12]}; not run")
    os.mkdir(dest, 0o700)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise LabError(f"cannot unpack the experiment's committed scripts: {exc}") from None


def _committed_files(vault: Path, commit: str, exp_rel: str, exp_real: str) -> list[Path]:
    """The working-tree copy (under ``exp_real``) of every file committed in the experiment folder at ``commit``,
    the commit the snapshot is taken from (R34). Raises :class:`LabError` when git can't list them."""
    data = _git(vault, "ls-tree", "-r", "-z", "--name-only", commit, "--", f"{exp_rel}/")
    if data is None:
        raise LabError(f"cannot list the experiment's committed files at commit {commit[:12]}; not run")
    prefix = exp_rel + "/"
    out: list[Path] = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if not name.startswith(prefix):
            raise LabError(f"git listed {name!r}, outside the experiment folder; not run")
        out.append(Path(exp_real, name[len(prefix) :]))
    return out


def _from_snapshot(argv: Sequence[str], src: str) -> list[str]:
    """``argv`` (``./<script> …`` or ``<interpreter> <script> …``) with the script taken from the snapshot."""
    if argv[0].startswith("./"):
        at = 0
    elif argv[0] in broker.EXPERIMENT_INTERPRETERS and len(argv) >= 2:
        at = 1
    else:
        raise LabError(f"argv {list(argv)!r} doesn't name the experiment's committed script")
    path = os.path.join(src, argv[at].removeprefix("./"))
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(src) + os.sep) or os.path.islink(path) or not os.path.isfile(real):
        raise LabError(f"{argv[at]} isn't a committed file of the experiment's; not run")
    return [*argv[:at], real, *argv[at + 1 :]]


def _run(
    vault: Path,
    slug: str,
    eid: str,
    argv: Sequence[str],
    private: bool,
    exp_real: str,
    exp_fd: int,
    cap_usd: float,
    timeout_s: float,
    source: Mapping[str, str],
    claude_argv: Sequence[str],
    source_tree: tuple[str, str] | None,
    totals: tuple[int, int, int, int, int],
    hooks: tuple[Callable[[str, str], None] | None, Callable[[], str | None] | None] = (None, None),
    ml: _ML | None = None,
) -> RunOutcome:
    ml = ml or _ML()
    max_processes, max_total_bytes, max_open_files, floor, mem_limit = totals
    on_started, should_stop = hooks
    _clear(exp_fd, TMP_NAME)
    os.mkdir(TMP_NAME, 0o700, dir_fd=exp_fd)
    tmp_real = os.path.join(exp_real, TMP_NAME)
    private_dir = tempfile.mkdtemp(prefix="scout-lab-", dir="/tmp")  # 0700: the socket, profile and output
    server: MeterServer | None = None
    try:
        run_argv, src = list(argv), None
        committed: list[Path] = []  # the working-tree copies of the committed files, sealed off (R34)
        if source_tree is not None:
            src = os.path.join(os.path.realpath(private_dir), SOURCE_NAME)
            _snapshot(vault, *source_tree, src)
            run_argv = _from_snapshot(argv, src)
            committed = _committed_files(vault, *source_tree, exp_real)
        run_argv = owner_python_argv(run_argv, ml.python)
        sock = Path(os.path.realpath(private_dir)) / "m.sock"
        # The sweep's fingerprint: only this run's profile may write it, and the script can't remove it.
        marker = os.path.join(os.path.realpath(private_dir), "probe")
        for path in (marker, _decoy(marker)):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        profile_file = os.path.join(private_dir, "experiment.sb")
        deny_reads, deny_dirs = ([], []) if private else _deny_reads(vault, exp_real)
        deny_reads.append(Path(profile_file))  # it names every private file the profile denies
        # The vault's machine state, private experiment or not: the run broker's queue holds the text of commits
        # and Telegram messages waiting to go out, and the rest holds every file's label (privacy-policy.json),
        # the delegates' records and the schedule. None of it is an experiment's input.
        deny_dirs.append(vault_paths.state_dir(vault))
        deny_execs = _deny_execs([source.get("PATH"), os.environ.get("PATH")])
        try:
            text = sandbox.experiment_profile(
                Path(exp_real),
                Path(tmp_real),
                sock,
                deny_reads,
                deny_execs,
                env=source,
                probe_file=Path(marker),
                allow_reads=_allow_reads(vault, source, [] if src is None else [src], ml.python),
                deny_read_dirs=deny_dirs,
                deny_access=committed,
                weights_dirs=[Path(d) for d in ml.dirs[0]],
            )
        except profile.ProfileError as exc:
            raise LabError(f"cannot build the experiment sandbox: {exc}") from None
        # The folders must still be the real ones now that the profile is rendered: a link swapped in
        # before rendering would have widened what it allows.
        allows = "\n".join(
            [
                "(allow file-write*",
                f"  (subpath {profile._quote(exp_real)})",
                f"  (subpath {profile._quote(tmp_real)})",
            ]
        )
        if (
            allows not in text
            or os.path.realpath(exp_real) != exp_real
            or os.path.realpath(tmp_real) != tmp_real
            or not stat.S_ISDIR(os.stat(TMP_NAME, dir_fd=exp_fd, follow_symlinks=False).st_mode)
        ):
            raise LabError("the experiment folder changed while the sandbox was being built; not run")
        safe_write.atomic_write_text(profile_file, text)  # a file, not -p: thousands of denies outgrow argv

        # The folder of experiment notes: the experiment may not write it; the run itself may, so a run's
        # own processes never match the sweep.
        outside = os.path.dirname(exp_real)
        try:
            probe = sandbox.SandboxProbe()
        except (OSError, AttributeError) as exc:
            raise LabError(f"cannot load sandbox_check to find the script's processes afterwards: {exc}") from None
        if not probe.works(tmp_real):
            raise LabError("sandbox_check gave a wrong answer about this process; can't sweep afterwards, not run")
        try:
            open_files = sandbox.OpenFiles(max_fds=max_open_files)
        except (OSError, AttributeError) as exc:
            raise LabError(f"cannot load libproc to count the script's open files: {exc}") from None
        if not open_files.works():
            raise LabError("libproc gave a wrong answer about this process's open files; can't count bytes, not run")

        command = list(claude_argv)
        if command and os.sep not in command[0]:
            command[0] = shutil.which(command[0]) or command[0]
        server = MeterServer(
            sock, ledger_path(vault, slug, eid, source), cap_usd, claude_argv=command, now=lambda: config.now(vault)
        )
        spent_before = server._spent()[0]
        server.start()

        child_env = script_env(source, tmp_real, sock, exp_real, src, ml.dirs[1])
        out_path, err_path = os.path.join(private_dir, "stdout"), os.path.join(private_dir, "stderr")
        started = config.now(vault)
        timed_out = over_bytes = over_processes = over_disk_floor = over_memory = False
        stopped: str | None = None
        clean = False
        nproc = _nproc_limit(max_processes)  # counted last thing before the launch
        with _new_file(out_path) as out_f, _new_file(err_path) as err_f:
            gate_r, gate_w = os.pipe()
            try:
                proc = subprocess.Popen(
                    [
                        str(profile.SANDBOX_EXEC),
                        "-f",
                        profile_file,
                        GATE_SHELL,
                        "-c",
                        _GATE,
                        "gate",
                        str(_file_size_blocks(max_total_bytes)),
                        str(nproc),
                        str(max_open_files),
                        *run_argv,
                    ],
                    cwd=exp_real,
                    env=child_env,
                    stdin=gate_r,
                    stdout=out_f,
                    stderr=err_f,
                    start_new_session=True,
                )
            finally:
                os.close(gate_r)
            try:
                try:
                    if not _positive_control(proc, probe, marker, outside):
                        # Kill before the gate's pipe closes: the gate would see the end of file and exit, but
                        # nothing the script could do may race that.
                        with contextlib.suppress(OSError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        raise LabError(
                            "sandbox_check gave the wrong answer about the sandboxed script before it started; "
                            "its processes couldn't be found afterwards, so it was not run"
                        )
                    if on_started is not None and proc.poll() is None:
                        on_started(marker, outside)  # the sweep's paths, recorded before the script can run
                    if proc.poll() is None:
                        with contextlib.suppress(BrokenPipeError):  # sandbox-exec failed meanwhile: its exit says so
                            os.write(gate_w, b"go\n")  # the gate opens: the script runs
                finally:
                    os.close(gate_w)

                def over(holders: set[int]) -> bool:
                    total = _total_bytes(exp_fd, open_files, holders)
                    return total is None or total > max_total_bytes

                deadline = time.monotonic() + timeout_s
                while True:  # wait out the script, checking its totals at every poll; any break goes to the kill
                    left = deadline - time.monotonic()
                    if left <= 0:
                        timed_out = True
                        break
                    try:
                        proc.wait(timeout=min(_POLL_S, left))
                    except subprocess.TimeoutExpired:
                        holders = _holders(probe, marker, outside)
                        if over(holders):
                            over_bytes = True
                            break
                        if len(holders) > max_processes:
                            over_processes = True
                            break
                        if floor > 0 and ((free := free_bytes(vault)) is None or free < floor):
                            over_disk_floor = True
                            break
                        if _holders_footprint(holders) > mem_limit:
                            over_memory = True
                            break
                        if should_stop is not None and (reason := should_stop()):
                            stopped = reason
                            break
                        continue
                    # The script exited: its last writes, and the unlinked files of anything it left running.
                    over_bytes = over(_holders(probe, marker, outside))
                    break
            finally:
                clean = _kill_all(proc, probe, marker, outside)
        if not over_bytes:  # what a survivor wrote before the kill reached it
            total = _tree_bytes(exp_fd)
            over_bytes = total is None or total > max_total_bytes
        stderr_tail = _tail(err_path)
        outcome = RunOutcome(
            exit=proc.returncode if proc.returncode is not None else -signal.SIGKILL,
            stdout_tail=_tail(out_path),
            stderr_tail=stderr_tail,
            spent_usd=server._spent()[0],
            timed_out=timed_out,
            over_bytes=over_bytes,
            over_processes=over_processes,
            over_disk_floor=over_disk_floor,
            over_memory=over_memory,
            stopped=stopped,
        )
        body = _log_body(
            argv,
            started,
            config.now(vault),
            outcome,
            spent_before,
            cap_usd,
            max_processes,
            max_total_bytes,
            nproc,
            floor,
            mem_limit,
            on_started is not None,
            started + _dt.timedelta(seconds=timeout_s),
            ml,
        )
        if not clean:
            body += "survivors: a process holding this experiment's sandbox was still alive after the sweep\n"
        _write_log(exp_fd, started, body)
        if not clean:
            raise LabError("a process the experiment started survived every kill sweep; check `ps` before re-running")
        if outcome.exit in (65, 71) and stderr_tail.startswith("sandbox-exec:"):
            # The script never ran: a profile the kernel rejected, or a sandbox that can't start inside another.
            raise LabError(f"sandbox-exec could not apply the experiment profile (exit {outcome.exit}): {stderr_tail}")
        return outcome
    finally:
        if server is not None:
            server.stop()
        shutil.rmtree(private_dir, ignore_errors=True)
        with contextlib.suppress(OSError):
            _clear(exp_fd, TMP_NAME)

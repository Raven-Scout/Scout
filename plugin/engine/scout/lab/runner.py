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
  folder records what was spent.
- **The folders the profile allows are checked first.** The experiment folder must resolve to the
  same path under the resolved vault (no symlinked component), and the scratch dir is made fresh;
  the rendered profile is then checked to allow exactly those two. A run could otherwise point a
  folder at a protected path and have the broker grant it.
- **The runner never follows a link the script could plant.** It keeps a descriptor on the
  experiment folder and writes the run log and removes the scratch dir through it (``O_NOFOLLOW``,
  ``O_EXCL``, :func:`shutil.rmtree` with ``dir_fd``).
- **Nothing the script started survives it.** At the timeout, after a normal exit and when the
  runner itself is interrupted, the process group is SIGKILLed; then every process of this user is
  swept and killed when its sandbox is this experiment's (the kernel, through ``sandbox_check``,
  says it may write the scratch dir and may not write the folder holding the experiment). A child
  that left the group (``setsid``, a double fork) still carries the profile, so the sweep finds it
  whatever its parentage. Matches are frozen (SIGSTOP) until a pass finds no new one, then killed,
  until a sweep finds none.
- **Private bytes outside knowledge-base/ are denied too** (ruling R18): the git objects, the inbox,
  the engine cache, every other checkout of the vault's repo (``.claude/worktrees/`` and each git
  worktree), ``Raw/`` and ``action-items/``. Transcripts and ``.scout-logs`` stay readable (spec §6.3).
- **The experiment's status is left alone.** The run finishes or abandons it.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scout import config
from scout import paths as vault_paths
from scout.errors import LabError
from scout.kb import hook_paths
from scout.kb.read import visibility_map
from scout.lab import paths, sandbox
from scout.lab.meter import ENV_METER, MeterServer
from scout.lab.model import EID_RE, SLUG_RE
from scout.lab.parser import parse_experiment
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
# Apple's git, whichever PATH finds first: /usr/bin/git is a shim that starts one of the other two.
KNOWN_GITS = (
    "/usr/bin/git",
    "/Library/Developer/CommandLineTools/usr/bin/git",
    "/Applications/Xcode.app/Contents/Developer/usr/bin/git",
)
_KILL_WAIT_S = 5.0
_PS = "/bin/ps"


@dataclass
class RunOutcome:
    exit: int
    stdout_tail: str
    stderr_tail: str
    spent_usd: float  # the experiment's total spend (the ledger's), which the cap is held against
    timed_out: bool


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
    return note, exp.frontmatter.get("visibility") == "private"


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


def _deny_reads(vault: Path, exp_real: str) -> list[Path]:
    """Every file under knowledge-base/ not labeled ``normal``, plus the private bytes kept elsewhere.

    A path the labels leave out counts as private (the rule every :func:`visibility_map` caller
    follows), so a label failure denies the whole KB rather than none of it. The experiment's own
    folder is its own and is left readable. A folder holding only denied files is denied as one
    ``subpath``: the kernel refuses a profile whose deny list outgrows its 64 KB data limit (about
    two thousand file paths), and a private folder (``personal/``) is usually private as a whole.
    """
    labels = visibility_map(vault, READER, ["knowledge-base/**"], who="lab runner")
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
            if _within(os.path.realpath(path), exp_real) or labels.get(path.relative_to(vault).as_posix()) == "normal":
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
    return out


def other_checkouts(vault: Path) -> list[Path]:
    """Every other working tree of the vault's repo: the main checkout and each ``<gitdir>/worktrees/*``."""
    vault_real = os.path.realpath(vault)
    found: list[Path] = []
    for gitdir in [vault / ".git", *hook_paths.git_dirs(vault)]:
        if not gitdir.is_dir():
            continue
        candidates: list[Path] = []
        if gitdir.name == ".git":
            candidates.append(gitdir.parent)
        for pointer in sorted(gitdir.glob("worktrees/*/gitdir")):
            try:
                target = Path(pointer.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeDecodeError):
                continue
            target = target if target.is_absolute() else pointer.parent / target
            candidates.append(target.parent)
        for checkout in candidates:
            real = os.path.realpath(checkout)
            if real != vault_real and checkout not in found:
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


def ledger_path(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Path:
    """The experiment's spending ledger, in the owner's run-broker folder (created 0700)."""
    folder = broker.owner_dir(env) / LEDGER_DIR
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    key = hashlib.sha256(os.path.realpath(vault).encode("utf-8")).hexdigest()[:16]
    return folder / f"{key}-{slug}-{eid}.jsonl"


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


def _holders(probe: sandbox.SandboxProbe, tmp_real: str, outside: str) -> set[int]:
    """Live processes whose sandbox is this experiment's: it may write ``tmp_real`` and may not write ``outside``."""
    return {
        pid
        for pid in _own_live_pids()
        if probe.sandboxed(pid)
        and probe.denies(pid, "file-write-data", outside) is True
        and probe.denies(pid, "file-write-data", tmp_real) is False
    }


def _sweep(probe: sandbox.SandboxProbe, tmp_real: str, outside: str) -> bool:
    """Kill every process holding this experiment's sandbox; True once a sweep finds none."""
    for _ in range(_SWEEP_ROUNDS):
        frozen: set[int] = set()
        for _ in range(50):  # freeze first, so nothing forks while the rest are found
            new = _holders(probe, tmp_real, outside) - frozen
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
    return not _holders(probe, tmp_real, outside)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_all(proc: subprocess.Popen[bytes], probe: sandbox.SandboxProbe, tmp_real: str, outside: str) -> bool:
    """SIGKILL the script's process group, reap it, then sweep out every process still holding the
    experiment's sandbox, wherever it moved to. True when none is left."""
    pgid = proc.pid
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_KILL_WAIT_S)
    clean = _sweep(probe, tmp_real, outside)
    deadline = time.monotonic() + _KILL_WAIT_S
    while _group_alive(pgid) and time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.05)
    return clean and not _group_alive(pgid)


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
) -> str:
    return "\n".join(
        [
            f"started: {started.strftime('%Y-%m-%d %H:%M')}",
            f"finished: {finished.strftime('%Y-%m-%d %H:%M')}",
            f"argv: {json.dumps(list(argv), ensure_ascii=False)}",
            f"exit: {outcome.exit}",
            f"timed_out: {'true' if outcome.timed_out else 'false'}",
            f"spent_usd: {outcome.spent_usd:.4f} (this run {max(outcome.spent_usd - spent_before, 0.0):.4f}, "
            f"cap {cap_usd:.2f})",
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
) -> RunOutcome:
    """Run ``argv`` for experiment ``eid`` of thread ``slug`` under the experiment sandbox.

    ``env`` supplies the script's ``PATH``, ``HOME`` and ``LANG`` and the HOME the owner's ledger
    folder is found under (default: this process's environment). ``claude_argv`` is what the meter
    runs for each model call; a bare program name is resolved on this process's PATH, outside the
    sandbox. Raises :class:`LabError` unless the experiment is ``running`` and ``sandbox-exec`` can
    apply the profile. Doesn't change the experiment's status and never commits.
    """
    source = os.environ if env is None else env
    if not argv:
        raise LabError("nothing to run: the experiment command is empty")
    if isinstance(cap_usd, bool) or not math.isfinite(cap_usd) or cap_usd <= 0:
        raise LabError(f"cost cap is {cap_usd!r}; it must be a finite number greater than 0")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise LabError(f"timeout is {timeout_s!r}; it must be a finite number of seconds greater than 0")
    note, private = _running_note(vault, slug, eid)
    exp_real, exp_fd = _open_experiment_dir(vault, note)
    try:
        if not profile.available():
            raise LabError("experiments need macOS sandbox-exec, which isn't available here; not run")
        return _run(vault, slug, eid, argv, private, exp_real, exp_fd, cap_usd, timeout_s, source, claude_argv)
    finally:
        os.close(exp_fd)


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
) -> RunOutcome:
    _clear(exp_fd, TMP_NAME)
    os.mkdir(TMP_NAME, 0o700, dir_fd=exp_fd)
    tmp_real = os.path.join(exp_real, TMP_NAME)
    private_dir = tempfile.mkdtemp(prefix="scout-lab-", dir="/tmp")  # 0700: the socket, profile and output
    server: MeterServer | None = None
    try:
        sock = Path(os.path.realpath(private_dir)) / "m.sock"
        deny_reads = [] if private else _deny_reads(vault, exp_real)
        deny_execs = _deny_execs([source.get("PATH"), os.environ.get("PATH")])
        try:
            text = sandbox.experiment_profile(Path(exp_real), Path(tmp_real), sock, deny_reads, deny_execs, env=source)
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
        profile_file = os.path.join(private_dir, "experiment.sb")
        with open(profile_file, "w", encoding="utf-8") as fh:  # a file, not -p: thousands of denies outgrow argv
            fh.write(text)

        # The sweep's two probes: the scratch dir (this experiment's sandbox may write it) and the folder of
        # experiment notes (it may not; the run itself may, so a run's own processes never match).
        outside = os.path.dirname(exp_real)
        try:
            probe = sandbox.SandboxProbe()
        except (OSError, AttributeError) as exc:
            raise LabError(f"cannot load sandbox_check to find the script's processes afterwards: {exc}") from None
        if not probe.works(tmp_real):
            raise LabError("sandbox_check gave a wrong answer about this process; can't sweep afterwards, not run")

        command = list(claude_argv)
        if command and os.sep not in command[0]:
            command[0] = shutil.which(command[0]) or command[0]
        server = MeterServer(
            sock, ledger_path(vault, slug, eid, source), cap_usd, claude_argv=command, now=lambda: config.now(vault)
        )
        spent_before = server._spent()[0]
        server.start()

        child_env = {
            "PATH": source.get("PATH") or "/usr/bin:/bin",
            "HOME": source.get("HOME") or str(Path.home()),
            "LANG": source.get("LANG") or "en_US.UTF-8",
            "TMPDIR": tmp_real,
            "PYTHONDONTWRITEBYTECODE": "1",
            ENV_METER: str(sock),
            ENV_EXPERIMENT_DIR: exp_real,
        }
        out_path, err_path = os.path.join(private_dir, "stdout"), os.path.join(private_dir, "stderr")
        started = config.now(vault)
        timed_out = False
        clean = False
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            proc = subprocess.Popen(
                [str(profile.SANDBOX_EXEC), "-f", profile_file, *argv],
                cwd=exp_real,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                start_new_session=True,
            )
            try:
                try:
                    proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
            finally:
                clean = _kill_all(proc, probe, tmp_real, outside)
        stderr_tail = _tail(err_path)
        outcome = RunOutcome(
            exit=proc.returncode if proc.returncode is not None else -signal.SIGKILL,
            stdout_tail=_tail(out_path),
            stderr_tail=stderr_tail,
            spent_usd=server._spent()[0],
            timed_out=timed_out,
        )
        body = _log_body(argv, started, config.now(vault), outcome, spent_before, cap_usd)
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

"""The experiment runner: meter, timeout, process-group kill, run log, and what it refuses."""

from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.kb.privacy import FULL_READERS
from scout.lab import paths, runner
from scout.sandbox import broker, profile
from tests.unit import lab_runner_helpers as h

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")


def _gone(pid: int, *, group: bool) -> bool:
    try:
        (os.killpg if group else os.kill)(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _ledger(vault: Path, tmp_path: Path) -> Path:
    key = hashlib.sha256(os.path.realpath(vault).encode()).hexdigest()[:16]
    return broker.owner_dir(h.make_env(tmp_path)) / "lab-meter" / f"{key}-{h.SLUG}-{h.EID}.jsonl"


@kernel
def test_over_cap_call_refused_inside_sandbox(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    script = f'printf hi > p.txt; {h.CALL}; echo "rc1=$?"; {h.CALL}; echo "rc2=$?"'
    out = h.run(vault, tmp_path, script, cap=0.5)
    assert "rc1=0" in out.stdout_tail and "rc2=3" in out.stdout_tail, out.stderr_tail
    assert "cost cap" in out.stderr_tail
    assert out.spent_usd == pytest.approx(0.4)


@kernel
def test_timeout_kills_process_group(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    started = time.monotonic()
    out = h.run(vault, tmp_path, "echo $$ > pgid.txt; sleep 300 & sleep 300", timeout_s=2)
    assert time.monotonic() - started < 30
    assert out.timed_out is True and out.exit != 0
    pgid = int((h.exp_dir(vault) / "pgid.txt").read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    # The status is left for the run to finish or abandon.
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None and "status: running" in note.read_text()


@kernel
def test_child_left_behind_after_a_normal_exit_is_killed(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "echo $$ > pgid.txt; sleep 300 & echo $! > child.txt; exit 0")
    assert out.exit == 0 and out.timed_out is False
    assert _gone(int((h.exp_dir(vault) / "pgid.txt").read_text()), group=True)
    assert _gone(int((h.exp_dir(vault) / "child.txt").read_text()), group=False)


@kernel
def test_child_in_its_own_session_is_killed_at_timeout(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    child = "import os, time; os.setsid(); open('esc.txt', 'w').write(str(os.getpid())); time.sleep(300)"
    escape = f'{h.PY} -c "{child}"'
    out = h.run(vault, tmp_path, f"{escape} & while [ ! -s esc.txt ]; do sleep 0.1; done; sleep 300", timeout_s=3)
    assert out.timed_out is True
    pid = int((h.exp_dir(vault) / "esc.txt").read_text())
    deadline = time.monotonic() + 5
    while not _gone(pid, group=False) and time.monotonic() < deadline:
        time.sleep(0.1)
    if not _gone(pid, group=False):  # don't leave it running if the runner failed
        os.kill(pid, signal.SIGKILL)
        pytest.fail("a child that left the process group survived the timeout")


def _alive(pid: int) -> bool:
    """Running or stopped; a zombie waiting for launchd to reap it is dead."""
    state = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _assert_dead_now(pid: int) -> None:
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)  # don't leave it running if the runner failed
        pytest.fail(f"process {pid} that holds the experiment's sandbox outlived run_experiment")


# Writes its pid to esc.txt once it is out of the script's session, then sleeps.
_SETSID = "import os, time; os.setsid(); open('esc.txt', 'w').write(str(os.getpid())); time.sleep(120)"
_DOUBLE_FORK = (
    "import os, time\n"
    "if os.fork():\n    os._exit(0)\n"
    "os.setsid()\n"
    "if os.fork():\n    os._exit(0)\n"
    "open('esc.txt', 'w').write(str(os.getpid()))\n"
    "time.sleep(120)\n"
)
_WAIT_ESC = "while [ ! -s esc.txt ]; do sleep 0.05; done"


@kernel
def test_setsid_child_killed_after_a_normal_exit(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, f'{h.PY} -c "{_SETSID}" & {_WAIT_ESC}; exit 0')
    assert out.exit == 0 and out.timed_out is False, out.stderr_tail
    _assert_dead_now(int((h.exp_dir(vault) / "esc.txt").read_text()))


@kernel
def test_double_forked_setsid_child_killed_at_timeout(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    (tmp_path / "home").mkdir(exist_ok=True)
    script = h.exp_dir(vault)
    script.mkdir()
    (script / "daemon.py").write_text(_DOUBLE_FORK)
    out = h.run(vault, tmp_path, f"{h.PY} daemon.py; {_WAIT_ESC}; sleep 300", timeout_s=3)
    assert out.timed_out is True
    _assert_dead_now(int((h.exp_dir(vault) / "esc.txt").read_text()))


@kernel
def test_sweep_leaves_other_sandboxes_alone(tmp_path: Path) -> None:
    """Processes in other sandboxes are never killed: one that may write the experiment's folder (the run's
    own), and one that may write all of /tmp but nothing in the home folder (as the user's cfprefsd and
    sharingd do: an early version of the fingerprint matched them)."""
    vault = h.make_vault(tmp_path)
    kb = os.path.realpath(vault / "knowledge-base")
    profiles = [
        f'(version 1)(allow default)(allow file-write* (subpath "{kb}"))',
        '(version 1)(allow default)(deny file-write* (subpath "/"))(allow file-write* (subpath "/private/tmp"))',
    ]
    others = [subprocess.Popen([str(profile.SANDBOX_EXEC), "-p", text, "/bin/sleep", "60"]) for text in profiles]
    try:
        time.sleep(0.3)
        out = h.run(vault, tmp_path, "true")
        assert out.exit == 0
        for other in others:
            assert other.poll() is None, "the sweep killed a process whose sandbox isn't the experiment's"
    finally:
        for other in others:
            other.kill()
            other.wait()


# --- the sweep can't be blinded (round 2) ------------------------------------------------------


@kernel
def test_setsid_child_killed_when_the_script_deletes_its_tmp_dir(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, f'{h.PY} -c "{_SETSID}" & {_WAIT_ESC}; rm -rf .tmp; exit 0')
    assert out.exit == 0 and out.timed_out is False, out.stderr_tail
    _assert_dead_now(int((h.exp_dir(vault) / "esc.txt").read_text()))


@kernel
def test_setsid_child_killed_at_timeout_when_the_script_deletes_its_tmp_dir(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, f'{h.PY} -c "{_SETSID}" & {_WAIT_ESC}; rm -rf .tmp; sleep 300', timeout_s=3)
    assert out.timed_out is True
    _assert_dead_now(int((h.exp_dir(vault) / "esc.txt").read_text()))


@kernel
def test_setsid_child_killed_when_the_script_swaps_its_tmp_dir_for_a_link(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    swap = f"rm -rf .tmp; ln -s '{elsewhere}' .tmp"
    out = h.run(vault, tmp_path, f'{h.PY} -c "{_SETSID}" & {_WAIT_ESC}; {swap}; exit 0')
    assert out.exit == 0, out.stderr_tail
    _assert_dead_now(int((h.exp_dir(vault) / "esc.txt").read_text()))
    assert elsewhere.is_dir() and list(elsewhere.iterdir()) == []
    assert not (h.exp_dir(vault) / ".tmp").exists() and not (h.exp_dir(vault) / ".tmp").is_symlink()


@kernel
def test_wrong_probe_answers_refuse_before_the_script_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R21's positive control: a probe that can't answer (an OS or ctypes change) refuses the run."""
    vault = h.make_vault(tmp_path)
    real_denies = runner.sandbox.SandboxProbe.denies

    def blind(self: runner.sandbox.SandboxProbe, pid: int, operation: str, path: str) -> bool | None:
        # Right about this process (so the pre-launch self-check passes), blind about every other one.
        return real_denies(self, pid, operation, path) if pid == os.getpid() else None

    monkeypatch.setattr(runner.sandbox.SandboxProbe, "denies", blind)
    with pytest.raises(LabError, match="sandbox_check"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()
    logs = list(h.exp_dir(vault).glob("run-*.log"))
    assert logs == []


@functools.cache
def _gate_nproc() -> int:
    """A process limit the gate can apply on this machine: the per-user ceiling (a CI runner's
    ``kern.maxprocperuid`` is well under a workstation's), at most 4000, far over what a test starts."""
    counts = runner._process_counts()
    assert counts is not None
    return min(4000, counts[1] or 4000)


def _gate(
    script: str, nproc: object = None, fsize: object = runner.FILE_SIZE_BLOCKS, nofile: object = 1024
) -> subprocess.Popen[bytes]:
    nproc = _gate_nproc() if nproc is None else nproc
    argv = [runner.GATE_SHELL, "-c", runner._GATE, "gate", str(fsize), str(nproc), str(nofile), "/bin/sh", "-c", script]
    return subprocess.Popen(argv, stdin=subprocess.PIPE)


def test_the_gate_runs_nothing_when_its_pipe_closes_without_go(tmp_path: Path) -> None:
    """I3: an end of file at the gate (a refused launch, or a runner that died before writing "go") exits; only
    "go" runs the script."""
    marker = tmp_path / "ran.txt"
    closed = _gate(f"echo ran > '{marker}'")
    assert closed.stdin is not None
    closed.stdin.close()  # the write end closes without "go"
    assert closed.wait(timeout=10) == runner.GATE_EXIT
    assert not marker.exists()
    opened = _gate(f"echo ran > '{marker}'")
    opened.communicate(b"go\n", timeout=10)
    assert opened.returncode == 0 and marker.read_text() == "ran\n"


def test_the_gate_caps_the_size_of_files_the_script_writes(tmp_path: Path) -> None:
    out = tmp_path / "limit.txt"
    py = "import resource; print(*resource.getrlimit(resource.RLIMIT_FSIZE))"
    gate = _gate(f"{h.PY} -I -c '{py}' > '{out}'")
    gate.communicate(b"go\n", timeout=10)
    assert out.read_text().split() == [str(1 << 30)] * 2  # soft and hard: 1 GiB


def test_the_gate_caps_files_at_the_runs_bytes_cap_and_its_descriptors(tmp_path: Path) -> None:
    """W4 fix 1: no one file may hold more than the run's whole bytes cap (``ulimit -f``), and the script may hold
    at most 256 descriptors (``ulimit -n``, soft and hard), so its open-but-unlinked files are bounded too."""
    assert runner._file_size_blocks(1 << 20) == 1024
    assert runner._file_size_blocks(1025) == 2  # rounded up to a whole block
    assert runner._file_size_blocks(1) == 1
    assert runner._file_size_blocks(1 << 40) == runner.FILE_SIZE_BLOCKS  # never over 1 GiB
    out = tmp_path / "limits.txt"
    py = "import resource as r; print(*r.getrlimit(r.RLIMIT_FSIZE), *r.getrlimit(r.RLIMIT_NOFILE))"
    gate = _gate(f"{h.PY} -I -c '{py}' > '{out}'", fsize=1024, nofile=300)
    gate.communicate(b"go\n", timeout=10)
    assert gate.returncode == 0 and out.read_text().split() == [str(1 << 20)] * 2 + ["300"] * 2
    marker = tmp_path / "ran.txt"
    for field in ("fsize", "nofile"):
        for bad in ("x", "-1"):
            refused = _gate(f"echo ran > '{marker}'", **{field: bad})
            refused.communicate(b"go\n", timeout=10)
            assert refused.returncode == runner.GATE_EXIT and not marker.exists(), (field, bad)


def test_the_gate_sets_the_process_limit_it_is_given(tmp_path: Path) -> None:
    """W4: the runner's RLIMIT_NPROC reaches the script as both the soft and the hard limit (so the script can't
    raise it); a value the gate can't apply runs nothing."""
    out = tmp_path / "nproc.txt"
    py = "import resource; print(*resource.getrlimit(resource.RLIMIT_NPROC))"
    gate = _gate(f"{h.PY} -I -c '{py}' > '{out}'")
    gate.communicate(b"go\n", timeout=10)
    assert gate.returncode == 0 and out.read_text().split() == [str(_gate_nproc())] * 2
    marker = tmp_path / "ran.txt"
    for bad in ("x", "-1"):  # (bash 3.2 takes "" as 0, which fails closed; the runner passes only digits)
        refused = _gate(f"echo ran > '{marker}'", nproc=bad)
        refused.communicate(b"go\n", timeout=10)
        assert refused.returncode == runner.GATE_EXIT and not marker.exists(), bad


# --- totals: processes and bytes (W4) ----------------------------------------------------


_BOMB = (
    "import os, sys, time\n"
    "kids = []\n"
    "for _ in range(200):\n"  # bounded: at most 200 sleeping children
    "    try:\n"
    "        pid = os.fork()\n"
    "    except OSError:\n"
    "        break\n"
    "    if pid == 0:\n"
    "        time.sleep(60)\n"
    "        os._exit(0)\n"
    "    kids.append(pid)\n"
    "open('kids.txt', 'w').write(' '.join(map(str, kids)))\n"
    "sys.exit(3 if len(kids) < 200 else 0)\n"
)


def _host_can_fork() -> bool:
    return subprocess.run(["/usr/bin/true"], check=False).returncode == 0


@kernel
def test_a_bounded_fork_bomb_is_held_to_the_process_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The kernel cap is the backstop: a bomb that forks faster than the runner polls is refused a fork near the
    allowance plus the headroom. That cap counts every process of the user, so slots other processes free during the
    run are open to it too (and ones they take come off it): the bound checked is that it is stopped short."""
    monkeypatch.setattr(runner, "NPROC_HEADROOM", 32)
    vault = h.make_vault(tmp_path)
    folder = h.exp_dir(vault)
    folder.mkdir()
    (folder / "bomb.py").write_text(_BOMB)
    out = h.run(vault, tmp_path, f"exec {h.PY} bomb.py", max_processes=16)
    assert out.exit == 3, (out.exit, out.stderr_tail)  # a fork was refused before the 200th
    kids = [int(p) for p in (folder / "kids.txt").read_text().split()]
    assert len(kids) < 200
    for pid in kids:
        _assert_dead_now(pid)
    assert _host_can_fork()


@kernel
def test_a_shell_fork_bomb_ends_non_zero_and_leaves_the_host_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spec's bomb: a loop of 200 background ``sleep``s. The kernel cap (per user, so it moves with the
    machine's other processes) refuses it forks, and the runner kills it once it holds more than its allowance at a
    poll; either ends it short of 200."""
    monkeypatch.setattr(runner, "NPROC_HEADROOM", 32)
    vault = h.make_vault(tmp_path)
    bomb = "i=0; while [ $i -lt 200 ]; do sleep 60 & echo $! >> kids.txt; i=$((i+1)); done; wait"
    started = time.monotonic()
    out = h.run(vault, tmp_path, bomb, timeout_s=5, max_processes=16)
    assert time.monotonic() - started < 30
    assert out.exit != 0  # the shell gave up on a refused fork, or the runner killed it
    kids = [int(p) for p in (h.exp_dir(vault) / "kids.txt").read_text().split()]
    assert len(kids) < 200
    for pid in kids:
        _assert_dead_now(pid)
    assert _host_can_fork()


@kernel
def test_the_process_limit_counts_only_the_experiments_own_processes(tmp_path: Path) -> None:
    """The limit is relative to the owner's processes at launch, not an absolute per-user count: with a limit of
    8, far under the hundreds of processes the owner has, the script still forks, however many processes the
    machine starts meanwhile (up to the headroom)."""
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "sleep 1 & sleep 1 & sleep 1 & wait; echo forked", max_processes=8)
    assert out.exit == 0 and out.stdout_tail.strip() == "forked", out.stderr_tail
    assert out.over_processes is False


@kernel
def test_more_processes_than_the_limit_are_killed_by_the_runner(tmp_path: Path) -> None:
    """The allowance is the runner's: the kernel cap leaves the headroom over it, and the runner kills a script
    that holds more than ``max_processes`` at a poll."""
    vault = h.make_vault(tmp_path)
    script = "i=0; while [ $i -lt 30 ]; do sleep 60 & i=$((i+1)); done; wait"
    started = time.monotonic()
    out = h.run(vault, tmp_path, script, timeout_s=30, max_processes=8)
    assert time.monotonic() - started < 20
    assert out.over_processes is True and out.timed_out is False and out.exit != 0
    log = next(h.exp_dir(vault).glob("run-*.log")).read_text()
    assert "over_processes: true" in log


def test_the_process_limit_is_the_launch_count_plus_the_allowance_plus_the_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inf = runner.resource.RLIM_INFINITY
    monkeypatch.setattr(runner.resource, "getrlimit", lambda _: (inf, inf))
    monkeypatch.setattr(runner, "_process_counts", lambda: (800, None))
    assert runner._nproc_limit(64) == 800 + 64 + runner.NPROC_HEADROOM
    monkeypatch.setattr(runner, "_process_counts", lambda: (800, 1000))
    assert runner._nproc_limit(64) == 1000  # never over the per-user ceiling the gate can't raise past
    monkeypatch.setattr(runner.resource, "getrlimit", lambda _: (8000, 900))
    assert runner._nproc_limit(64) == 900  # nor over this process's own hard limit
    monkeypatch.setattr(runner, "_process_counts", lambda: (850, 1000))
    with pytest.raises(
        LabError, match=r"process cap reached: this user has 850 processes and the per-user limit is 900"
    ):
        runner._nproc_limit(64)  # no room for the allowance: not run, rather than failing at its first fork
    monkeypatch.setattr(runner, "_process_counts", lambda: None)
    with pytest.raises(LabError, match="count"):
        runner._nproc_limit(64)


def test_the_owners_process_count_is_the_kernels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asked of the kernel (the lowest RLIMIT_NPROC at which a helper can still fork), not read from ``ps``."""
    counts = runner._process_counts()
    assert counts is not None and counts[0] >= 1 and (counts[1] is None or counts[1] > counts[0])
    count = runner._owner_process_count()
    assert count is not None and count >= 1
    if sys.platform == "darwin":  # Linux's RLIMIT_NPROC counts threads, which ps doesn't list
        ps = subprocess.run(["/bin/ps", "-A", "-o", "ruid="], capture_output=True, text=True).stdout.split()
        assert count <= ps.count(str(os.getuid())) + 50  # in the neighbourhood of what ps lists (churn aside)
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", ""))
    assert runner._owner_process_count() is None

    def says(out: str) -> None:
        monkeypatch.setattr(runner.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, out, ""))

    for bad in ("x\n", "12\n", "12 x\n", "x 900\n", "1 2 3\n"):
        says(bad)
        assert runner._process_counts() is None and runner._owner_process_count() is None, bad
    says("12 -1\n")
    assert runner._process_counts() == (12, None)
    says("12 900\n")
    assert runner._process_counts() == (12, 900) and runner._owner_process_count() == 12

    def broken(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise OSError("no interpreter")

    monkeypatch.setattr(runner.subprocess, "run", broken)
    assert runner._owner_process_count() is None


@kernel
@pytest.mark.parametrize("where", ["big.bin", ".tmp/big.bin"])
def test_writing_past_the_total_bytes_cap_is_killed(tmp_path: Path, where: str) -> None:
    vault = h.make_vault(tmp_path)
    two = f"open('{where}', 'wb').write(b'x' * 716800); open('{where}.2', 'wb').write(b'x' * 716800)"
    write = f'{h.PY} -I -c "{two}"'  # each file under the per-file limit (the cap), together over it
    started = time.monotonic()
    out = h.run(vault, tmp_path, f"{write}; echo wrote; sleep 30", timeout_s=40, max_total_bytes=1 << 20)
    assert time.monotonic() - started < 20
    assert out.over_bytes is True and out.timed_out is False and out.exit != 0, out.stderr_tail
    assert out.stdout_tail.strip() == "wrote"
    log = next(h.exp_dir(vault).glob("run-*.log")).read_text()
    assert "over_bytes: true" in log and "over_processes: false" in log


@kernel
def test_under_the_total_bytes_cap_runs_to_the_end(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    write = f"{h.PY} -I -c \"open('half.bin', 'wb').write(b'x' * 524288)\""
    out = h.run(vault, tmp_path, f"{write}; sleep 2; echo done", max_total_bytes=1 << 20)
    assert out.exit == 0 and out.over_bytes is False and out.stdout_tail.strip() == "done", out.stderr_tail
    assert "over_bytes: false" in next(h.exp_dir(vault).glob("run-*.log")).read_text()


@kernel
def test_bytes_written_just_before_a_quick_exit_still_count(tmp_path: Path) -> None:
    """A script that writes past the cap and exits before the first poll is still recorded as over."""
    vault = h.make_vault(tmp_path)
    two = "open('a.bin', 'wb').write(b'x' * 716800); open('b.bin', 'wb').write(b'x' * 716800)"
    out = h.run(vault, tmp_path, f'{h.PY} -I -c "{two}"', max_total_bytes=1 << 20)
    assert out.exit == 0 and out.over_bytes is True


@kernel
def test_one_file_cant_outgrow_the_bytes_cap(tmp_path: Path) -> None:
    """The gate's ``ulimit -f`` is the cap itself: Python (which ignores SIGXFSZ) gets EFBIG at 1 MiB."""
    vault = h.make_vault(tmp_path)
    py = (
        "import sys\n"
        "f = open('big.bin', 'wb', buffering=0)\n"
        "try:\n"
        "    f.write(b'x' * 2097152)\n"
        "except OSError as e:\n"
        "    print('refused', e.errno); sys.exit(0)\n"
        "print('wrote', f.tell())\n"
    )
    folder = h.exp_dir(vault)
    folder.mkdir()
    (folder / "big.py").write_text(py)
    out = h.run(vault, tmp_path, f"{h.PY} big.py", max_total_bytes=1 << 20)
    assert (folder / "big.bin").stat().st_size <= 1 << 20, out.stdout_tail


_UNLINKED = (
    "import sys, tempfile, time\n"
    "n, size = int(sys.argv[1]), int(sys.argv[2])\n"
    "held = []\n"
    "for _ in range(n):\n"
    "    f = tempfile.TemporaryFile()\n"  # in $TMPDIR (the scratch dir), unlinked at once
    "    f.write(b'x' * size); f.flush()\n"
    "    held.append(f)\n"
    "print('held', n, flush=True)\n"
    "time.sleep(float(sys.argv[3]))\n"
)


@kernel
def test_open_unlinked_temp_files_count_toward_the_bytes_cap(tmp_path: Path) -> None:
    """W4 fix 2: ``tempfile.TemporaryFile`` leaves no name in the folder; its bytes count all the same."""
    vault = h.make_vault(tmp_path)
    folder = h.exp_dir(vault)
    folder.mkdir()
    (folder / "unlinked.py").write_text(_UNLINKED)
    started = time.monotonic()
    out = h.run(vault, tmp_path, f"{h.PY} unlinked.py 3 716800 30", timeout_s=40, max_total_bytes=1 << 20)
    assert time.monotonic() - started < 20
    assert out.over_bytes is True and out.timed_out is False and out.exit != 0, out.stderr_tail
    assert out.stdout_tail.strip() == "held 3"
    assert [p.name for p in folder.iterdir() if p.name not in ("unlinked.py",) and not p.name.startswith("run-")] == []


@kernel
def test_open_unlinked_temp_files_under_the_cap_are_left_alone(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    folder = h.exp_dir(vault)
    folder.mkdir()
    (folder / "unlinked.py").write_text(_UNLINKED)
    out = h.run(vault, tmp_path, f"{h.PY} unlinked.py 1 2097152 2.5", max_total_bytes=4 << 20)
    assert out.exit == 0 and out.over_bytes is False and out.stdout_tail.strip() == "held 1", out.stderr_tail


@kernel
def test_open_files_reads_this_processes_unlinked_files() -> None:
    import tempfile

    files = runner.sandbox.OpenFiles()
    assert files.works()
    with tempfile.TemporaryFile() as fh, tempfile.NamedTemporaryFile() as named:
        fh.write(b"x" * 70_000)
        fh.flush()
        named.write(b"y" * 50_000)
        named.flush()
        st = os.fstat(fh.fileno())
        found = files.unlinked(os.getpid())
        assert found is not None
        sizes = {(dev, ino): size for dev, ino, size in found}
        assert sizes.get((st.st_dev, st.st_ino), 0) >= 70_000
        named_st = os.fstat(named.fileno())
        assert (named_st.st_dev, named_st.st_ino) not in sizes  # still linked: the folder walk counts it
    gone = subprocess.Popen(["/usr/bin/true"])
    gone.wait()
    assert files.unlinked(gone.pid) is None  # exited and reaped: skipped, not an error


def _state(pid: int) -> str:
    """The first letter of ``ps``'s state for ``pid`` (the test's own child), or "" once it's reaped."""
    return subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1]


@kernel
def test_a_holder_turned_zombie_is_gone_not_over(tmp_path: Path) -> None:
    """Fix round 2: a holder that exits (unreaped) between ``ps`` and the libproc query gets ESRCH from libproc,
    which skips zombies, though signal 0 still reaches it. It is gone, so the run isn't over its bytes cap."""
    files = runner.sandbox.OpenFiles()
    child = subprocess.Popen(["/usr/bin/true"])  # the test's own child
    try:
        deadline = time.monotonic() + 10
        while _state(child.pid) != "Z" and time.monotonic() < deadline:  # exited, not reaped (no waitid on macOS)
            time.sleep(0.02)
        assert _state(child.pid) == "Z"
        os.kill(child.pid, 0)  # the test's own zombie still answers signal 0
        assert files.unlinked(child.pid) is None
        root, _ = _tree(tmp_path)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert runner._total_bytes(fd, files, {child.pid}) == runner._tree_bytes(fd)
        finally:
            os.close(fd)
    finally:
        child.wait()


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["/bin/sleep", "30"])  # the test's own live child: the only process these tests query


@kernel
def test_a_live_process_the_kernel_wont_list_is_an_error_not_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    files = runner.sandbox.OpenFiles()

    def refused(pid: int, flavor: int, arg: int, buf: object, size: int) -> int:
        ctypes.set_errno(errno.EPERM)
        return 0

    child = _sleeper()
    try:
        monkeypatch.setattr(files, "_pidinfo", refused)
        with pytest.raises(OSError):
            files.unlinked(child.pid)
    finally:
        child.kill()
        child.wait()


@kernel
def test_a_live_process_with_no_descriptors_has_no_unlinked_files(monkeypatch: pytest.MonkeyPatch) -> None:
    files = runner.sandbox.OpenFiles()
    monkeypatch.setattr(files, "_pidinfo", lambda *a: 0)  # LISTFDS lists nothing, errno 0
    child = _sleeper()
    try:
        assert files.unlinked(child.pid) == []
    finally:
        child.kill()
        child.wait()


@kernel
def test_a_process_exiting_partway_through_the_listing_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    files = runner.sandbox.OpenFiles()
    child = _sleeper()

    def fdinfo_failing(err: int) -> object:
        def call(pid: int, fd: int, flavor: int, buf: object, size: int) -> int:
            ctypes.set_errno(err)
            return 0

        return call

    try:
        monkeypatch.setattr(files, "_fdinfo", fdinfo_failing(errno.ESRCH))
        assert files.unlinked(child.pid) is None
        monkeypatch.setattr(files, "_fdinfo", fdinfo_failing(errno.EBADF))  # each descriptor closed since: skipped
        assert files.unlinked(child.pid) == []
        monkeypatch.setattr(files, "_fdinfo", fdinfo_failing(errno.EIO))
        with pytest.raises(OSError):
            files.unlinked(child.pid)
    finally:
        child.kill()
        child.wait()


@kernel
def test_the_listing_buffer_is_the_descriptor_limit_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    files = runner.sandbox.OpenFiles(max_fds=40)
    sizes: list[int] = []

    def full(pid: int, flavor: int, arg: int, buf: object, size: int) -> int:
        sizes.append(size)
        return size  # filled to the brim: maybe more than it holds

    child = _sleeper()
    try:
        monkeypatch.setattr(files, "_pidinfo", full)
        for _ in range(2):
            with pytest.raises(OSError):
                files.unlinked(child.pid)
    finally:
        child.kill()
        child.wait()
    assert sizes == [41 * ctypes.sizeof(runner.sandbox._ProcFdInfo)] * 2


class _FakeOpenFiles:
    def __init__(self, by_pid: dict[int, object]) -> None:
        self.by_pid = by_pid

    def unlinked(self, pid: int) -> object:
        got = self.by_pid[pid]
        if isinstance(got, OSError):
            raise got
        return got


def test_total_bytes_adds_each_holders_unlinked_files_once(tmp_path: Path) -> None:
    root, expected = _tree(tmp_path)
    linked = os.stat(root / "one.bin")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        tree = runner._tree_bytes(fd)
        assert tree is not None
        fake = _FakeOpenFiles(
            {
                10: [(1, 100, 5_000), (1, 101, 7_000)],
                11: [(1, 100, 5_000), (linked.st_dev, linked.st_ino, 10_000)],  # shared; and a folder file
                12: None,  # exited meanwhile: skipped
            }
        )
        assert runner._total_bytes(fd, fake, {10, 11, 12}) == tree + 12_000  # type: ignore[arg-type]
        assert runner._total_bytes(fd, fake, set()) == tree  # type: ignore[arg-type]
        failing = _FakeOpenFiles({10: [], 13: PermissionError("not listed")})
        assert runner._total_bytes(fd, failing, {10, 13}) is None  # type: ignore[arg-type]
    finally:
        os.close(fd)
    hidden = root / "a"
    hidden.chmod(0o000)
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert runner._total_bytes(fd, _FakeOpenFiles({}), set()) is None  # type: ignore[arg-type]
        finally:
            os.close(fd)
    finally:
        hidden.chmod(0o755)
    assert expected > 0


def _tree(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "exp"
    (root / "a" / "b").mkdir(parents=True)
    (root / "one.bin").write_bytes(b"x" * 10_000)
    (root / "a" / "b" / "two.bin").write_bytes(b"y" * 20_000)
    os.link(root / "one.bin", root / "a" / "hard.bin")  # the same inode: counted once
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"z" * 500_000)
    (root / "link.bin").symlink_to(outside / "big.bin")  # a link: not followed
    (root / "linkdir").symlink_to(outside)  # a folder link: not walked
    os.mkfifo(root / "fifo")  # not a regular file
    return root, 30_000


def _bytes(root: Path) -> int | None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return runner._tree_bytes(fd)
    finally:
        os.close(fd)


def test_tree_bytes_counts_regular_files_once_and_follows_no_link(tmp_path: Path) -> None:
    root, expected = _tree(tmp_path)
    total = _bytes(root)
    assert total is not None
    # At least the bytes written; at most that rounded up to whole 4 KiB blocks per file (allocated space counts).
    assert expected <= total <= expected + 2 * 4096
    many = tmp_path / "many"
    many.mkdir()
    for i in range(400):  # wider than a process's default descriptor limit: one descriptor per level, not per folder
        (many / f"d{i}").mkdir()
    (many / "d399" / "f").write_bytes(b"x")
    assert (_bytes(many) or 0) >= 1


def test_tree_bytes_fails_closed_on_a_folder_it_cant_list(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    hidden = root / "a"
    hidden.chmod(0o000)  # a script hiding what it wrote from the count
    try:
        assert _bytes(root) is None
    finally:
        hidden.chmod(0o755)


@pytest.mark.parametrize("field", ["max_processes", "max_total_bytes", "max_open_files"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_refuses_bad_totals(tmp_path: Path, field: str, value: object) -> None:
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match=field.replace("_", " ")):
        h.run(vault, tmp_path, "echo ran > ran.txt", **{field: value})  # type: ignore[arg-type]
    assert not (h.exp_dir(vault) / "ran.txt").exists()


@kernel
def test_a_refused_launch_is_killed_before_the_gate_pipe_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3: on a failed positive control the script's group is SIGKILLed first; only then does the gate see an end
    of file."""
    vault = h.make_vault(tmp_path)
    events: list[tuple[str, int]] = []
    pipes: list[tuple[int, int]] = []
    real_pipe, real_close, real_killpg = os.pipe, os.close, os.killpg

    def pipe() -> tuple[int, int]:
        fds = real_pipe()
        pipes.append(fds)
        return fds

    def close(fd: int) -> None:
        events.append(("close", fd))
        real_close(fd)

    def killpg(pgid: int, sig: int) -> None:
        events.append(("killpg", pgid))
        real_killpg(pgid, sig)

    leaders: list[int] = []

    def refuse(proc: subprocess.Popen[bytes], *_: object) -> bool:
        leaders.append(proc.pid)
        return False

    monkeypatch.setattr(runner, "_positive_control", refuse)
    nproc = _gate_nproc()  # counted before os.pipe and os.close are watched: the count opens pipes of its own
    monkeypatch.setattr(runner, "_nproc_limit", lambda _: nproc)  # its `ps` would open the first pipes
    monkeypatch.setattr(runner.os, "pipe", pipe)
    monkeypatch.setattr(runner.os, "close", close)
    monkeypatch.setattr(runner.os, "killpg", killpg)
    with pytest.raises(LabError, match="sandbox_check"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    monkeypatch.undo()
    gate_w = pipes[0][1]
    assert events.index(("killpg", leaders[0])) < events.index(("close", gate_w))
    assert not (h.exp_dir(vault) / "ran.txt").exists()


@pytest.mark.parametrize("status", ["planned", "done", "abandoned", "vetoed"])
def test_refuses_unless_running(tmp_path: Path, status: str) -> None:
    vault = h.make_vault(tmp_path, status=status)
    with pytest.raises(LabError, match="running"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


def test_refuses_missing_experiment_and_bad_ids(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    env = h.make_env(tmp_path)
    for slug, eid in [(h.SLUG, "E9"), ("../x", h.EID), (h.SLUG, "E2/../E1"), ("Summary-Drift", h.EID)]:
        with pytest.raises(LabError):
            runner.run_experiment(vault, slug, eid, ["/usr/bin/true"], cap_usd=1, env=env)


@pytest.mark.parametrize("cap", [0, -1, float("nan"), float("inf")])
def test_refuses_bad_cap(tmp_path: Path, cap: float) -> None:
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="cap"):
        h.run(vault, tmp_path, "true", cap=cap)


def test_refuses_without_sandbox_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    monkeypatch.setattr(profile, "available", lambda: False)
    with pytest.raises(LabError, match="sandbox"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


@kernel
def test_ledger_lives_outside_the_experiment_and_the_script_cannot_write_it(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    ledger = _ledger(vault, tmp_path)
    first = h.run(vault, tmp_path, f"printf hi > p.txt; {h.CALL}", cap=0.5)
    assert first.exit == 0, first.stderr_tail
    assert ledger.is_file() and ledger.parent.stat().st_mode & 0o777 == 0o700
    assert not (h.exp_dir(vault) / "model-calls.jsonl").exists()
    before = ledger.read_text()
    attack = h.run(vault, tmp_path, f": > '{ledger}'; rm -f '{ledger}'; echo '{{}}' >> '{ledger}'")
    assert attack.exit != 0
    assert ledger.read_text() == before
    # The cap is the experiment's, across runs: 0.4 spent, another 0.4 would pass 0.5.
    again = h.run(vault, tmp_path, f'printf hi > p.txt; {h.CALL}; echo "rc=$?"', cap=0.5)
    assert "rc=3" in again.stdout_tail and again.spent_usd == pytest.approx(0.4)


@kernel
def test_run_log_env_and_tmp_cleanup(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    exp = h.exp_dir(vault)
    out = h.run(vault, tmp_path, 'env > env.txt; echo "$TMPDIR" > tmpdir.txt; echo done; echo oops >&2; exit 4')
    assert out.exit == 4 and out.stdout_tail.strip() == "done" and out.stderr_tail.strip() == "oops"
    env = dict(line.split("=", 1) for line in (exp / "env.txt").read_text().splitlines() if "=" in line)
    assert set(env) - {"PWD", "SHLVL", "_", "OLDPWD"} == {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "SCOUT_LAB_METER",
        "SCOUT_LAB_EXPERIMENT_DIR",
    }
    assert env["SCOUT_LAB_EXPERIMENT_DIR"] == os.path.realpath(exp)
    assert env["HOME"] == str(tmp_path / "home")
    assert (exp / "tmpdir.txt").read_text().strip() == os.path.realpath(exp / ".tmp")
    assert not (exp / ".tmp").exists()
    assert not Path(env["SCOUT_LAB_METER"]).parent.exists()
    logs = sorted(exp.glob("run-*.log"))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert "exit: 4" in text and "timed_out: false" in text and "spent_usd: 0.0000" in text
    assert "done" in text and "oops" in text
    assert json.dumps(["/bin/sh", "-c"])[1:-1] in text


@kernel
def test_planted_symlinks_are_not_followed(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    exp = h.exp_dir(vault)
    exp.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (exp / ".tmp").symlink_to(outside)
    now = time.time()
    for t in (now, now + 60):
        (exp / f"run-{time.strftime('%Y%m%d-%H%M', time.localtime(t))}.log").symlink_to(outside / "log-target")
    out = h.run(vault, tmp_path, 'echo t > "$TMPDIR/t.txt"')
    assert out.exit == 0, out.stderr_tail
    assert list(outside.iterdir()) == []
    assert (exp / ".tmp").exists() is False
    assert any(p.is_file() and not p.is_symlink() for p in exp.glob("run-*.log"))


def test_symlinked_experiment_dir_refused(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    h.exp_dir(vault).symlink_to(elsewhere)
    with pytest.raises(LabError, match="symlink|inside the vault"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert list(elsewhere.iterdir()) == []


def _touches(path: Path, exp: Path) -> bool:
    return path == exp or path.is_relative_to(exp) or exp.is_relative_to(path)


def test_deny_list_collapses_wholly_private_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    personal = vault / "knowledge-base" / "personal"
    personal.mkdir()
    for name in ("a.md", "b.md"):
        (personal / name).write_text("---\nvisibility: private\n---\nx\n")
    exp = h.exp_dir(vault)
    exp.mkdir()
    (exp / "script.py").write_text("print(1)\n")
    exp_real = os.path.realpath(exp)
    denied, _ = runner._deny_reads(vault, exp_real)
    assert personal in denied and vault / h.PRIVATE_NOTE in denied
    assert vault / h.NORMAL_NOTE not in denied
    assert not any(p.is_relative_to(personal) and p != personal for p in denied)
    assert not any(_touches(p, exp) for p in denied)

    # No labels (they couldn't be read): every KB file but the experiment's own is denied.
    monkeypatch.setattr(runner, "visibility_map", lambda *a, **k: {})
    denied, _ = runner._deny_reads(vault, exp_real)
    assert vault / "knowledge-base" / "people" in denied
    assert paths.front_page(vault, h.SLUG) in denied
    assert not any(_touches(p, exp) for p in denied)


def test_the_snapshot_argv_names_only_a_committed_file_inside_it(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "run.py").write_text("print(1)\n")
    (src / "sub" / "go.sh").write_text("echo\n")
    (src / "link.py").symlink_to(src / "run.py")
    real = os.path.realpath(src)
    assert runner._from_snapshot(["python3", "run.py", "--n", "2"], str(src)) == [
        "python3",
        f"{real}/run.py",
        "--n",
        "2",
    ]
    assert runner._from_snapshot(["./sub/go.sh", "x"], str(src)) == [f"{real}/sub/go.sh", "x"]
    for argv in (
        ["/bin/sh", "-c", "echo"],
        ["python3", "missing.py"],
        ["python3", "../run.py"],
        ["python3", "link.py"],
    ):
        with pytest.raises(LabError):
            runner._from_snapshot(argv, str(src))
    with pytest.raises(LabError, match="commit"):
        runner.run_experiment(tmp_path, h.SLUG, h.EID, ["./x"], cap_usd=1, commit="HEAD")


def test_the_owners_ledger_folder_unwritable_is_a_lab_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".local").mkdir(parents=True)
    (home / ".local" / "state").write_text("a file where the folder would go")
    with pytest.raises(LabError, match="ledger"):
        runner.ledger_path(tmp_path, h.SLUG, h.EID, {"HOME": str(home)})


def test_private_lab_folders_are_denied_whole_even_before_they_exist(tmp_path: Path) -> None:
    """I4: a private thread's folder and a private experiment's folder are denied as folders, so an output the
    private experiment writes later is denied too; the running experiment's own folder never is."""
    vault = h.make_vault(tmp_path)
    side = paths.thread_dir(vault, "side")
    side.mkdir()
    (side / "side.md").write_text("---\ntype: lab-thread\nvisibility: private\n---\n# Side\n", "utf-8")
    e1 = paths.experiment_note(vault, h.SLUG, "E1")
    assert e1 is not None
    e1.write_text(
        e1.read_text("utf-8").replace("type: lab-experiment\n", "type: lab-experiment\nvisibility: private\n")
    )
    assert not paths.experiment_dir(e1).exists()
    exp_real = os.path.realpath(h.exp_dir(vault))
    files, folders = runner._deny_reads(vault, exp_real)
    assert folders == [paths.experiment_dir(e1)]  # it doesn't exist yet: only a folder deny covers it
    assert side in files and not any(p.is_relative_to(side) and p != side for p in files)  # a folder: a subpath
    assert e1 in files  # the note itself, by its own label
    text = runner.sandbox.experiment_profile(
        h.exp_dir(vault), h.exp_dir(vault) / ".tmp", Path("/tmp/s"), files, [], env={}, deny_read_dirs=folders
    )
    assert f'(subpath "{os.path.realpath(paths.experiment_dir(e1))}")' in text
    # A private thread holding the (normal) experiment: its other files are denied, its own folder isn't.
    front = paths.front_page(vault, h.SLUG)
    front.write_text(front.read_text("utf-8").replace("visibility: normal", "visibility: private"), "utf-8")
    files, folders = runner._deny_reads(vault, exp_real)
    assert side in files and front in files and paths.timeline_path(vault, h.SLUG) in files
    assert folders == [paths.experiment_dir(e1)]  # the private thread's other experiment, as a whole
    assert not any(_touches(p, h.exp_dir(vault)) for p in [*files, *folders])


@kernel
def test_a_profile_the_kernel_rejects_is_an_error_not_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    good = runner.sandbox.experiment_profile
    monkeypatch.setattr(runner.sandbox, "experiment_profile", lambda *a, **k: good(*a, **k) + "(bogus-form)\n")
    with pytest.raises(LabError, match="sandbox-exec could not apply"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


def test_labels_are_read_as_a_full_reader() -> None:
    assert runner.READER in FULL_READERS


# --- the committed files' working-tree copies (R34) ----------------------------------------------


def _commit_experiment(vault: Path, files: dict[str, str]) -> str:
    """Commit ``files`` (name -> text) in the experiment folder with the rest of the vault; the commit id."""
    exp = h.exp_dir(vault)
    exp.mkdir(exist_ok=True)
    for name, text in files.items():
        (exp / name).write_text(text, encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    ident = ["-c", "user.name=Alex", "-c", "user.email=alex@example.org", "-c", "commit.gpgsign=false"]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *ident, *args], cwd=vault, env=env, check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "lab: E2 scripts")
    return git("rev-parse", "HEAD").strip()


def _run_committed(vault: Path, tmp_path: Path, argv: list[str], commit: str) -> runner.RunOutcome:
    return runner.run_experiment(
        vault,
        h.SLUG,
        h.EID,
        argv,
        cap_usd=1,
        timeout_s=60,
        env=h.make_env(tmp_path),
        claude_argv=(str(h.FAKE), str(tmp_path / "calls.log")),
        commit=commit,
    )


@kernel
def test_cwd_relative_sibling_cannot_read_the_worktree_copy(tmp_path: Path) -> None:
    """R34: a committed script calling a sibling by a cwd-relative path doesn't run an uncommitted edit of it."""
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(vault, {"run.sh": "sh ./helper.sh\n", "helper.sh": "echo COMMITTED\n"})
    (h.exp_dir(vault) / "helper.sh").write_text("echo UNCOMMITTED\n", encoding="utf-8")  # after the check
    out = _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert "UNCOMMITTED" not in out.stdout_tail
    assert out.exit != 0, out.stdout_tail


@kernel
def test_source_dir_sibling_runs_the_committed_copy(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(
        vault, {"run.sh": 'sh "$SCOUT_LAB_SOURCE_DIR/helper.sh"\n', "helper.sh": "echo COMMITTED\n"}
    )
    (h.exp_dir(vault) / "helper.sh").write_text("echo UNCOMMITTED\n", encoding="utf-8")
    out = _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert out.exit == 0, out.stderr_tail
    assert "COMMITTED" in out.stdout_tail and "UNCOMMITTED" not in out.stdout_tail


@kernel
def test_a_link_planted_over_a_committed_file_is_sealed_too(tmp_path: Path) -> None:
    """The kernel matches the resolved path: the deny follows a link put in a committed file's place before the run."""
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(vault, {"run.sh": "sh ./helper.sh\n", "helper.sh": "echo COMMITTED\n"})
    exp = h.exp_dir(vault)
    (exp / "evil.sh").write_text("echo UNCOMMITTED\n", encoding="utf-8")
    (exp / "helper.sh").unlink()
    (exp / "helper.sh").symlink_to("evil.sh")
    out = _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert "UNCOMMITTED" not in out.stdout_tail and out.exit != 0, out.stdout_tail


@kernel
def test_new_output_files_stay_readable_and_writable(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(vault, {"run.sh": "echo a,b > out.csv && cat out.csv\n"})
    out = _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert out.exit == 0, out.stderr_tail
    assert out.stdout_tail.strip() == "a,b"
    assert (h.exp_dir(vault) / "out.csv").read_text() == "a,b\n"


@kernel
def test_the_committed_files_working_tree_copies_are_unwritable(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(vault, {"run.sh": "echo changed > helper.sh\n", "helper.sh": "echo COMMITTED\n"})
    out = _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert out.exit != 0 and "not permitted" in out.stderr_tail
    assert (h.exp_dir(vault) / "helper.sh").read_text() == "echo COMMITTED\n"


@kernel
def test_a_committed_name_sbpl_cant_carry_refuses_the_run(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit_experiment(vault, {"run.sh": "echo ran > ran.txt\n", "odd\nname.txt": "x\n"})
    with pytest.raises(LabError, match="cannot build the experiment sandbox"):
        _run_committed(vault, tmp_path, ["sh", "run.sh"], commit)
    assert not (h.exp_dir(vault) / "ran.txt").exists()


@kernel
def test_sweep_is_not_clean_without_its_probe_files(tmp_path: Path) -> None:
    """A probe file that is gone or a link makes the sweep 'not clean', never a silent pass."""
    marker = tmp_path / "probe"
    probe = runner.sandbox.SandboxProbe()
    assert runner._sweep(probe, str(marker), str(tmp_path)) is False  # neither file exists
    marker.write_text("")
    Path(runner._decoy(str(marker))).symlink_to(tmp_path)
    assert runner._sweep(probe, str(marker), str(tmp_path)) is False

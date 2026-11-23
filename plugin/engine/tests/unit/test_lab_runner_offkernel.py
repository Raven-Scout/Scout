"""The runner's own logic, off the kernel: a stand-in ``sandbox-exec``, ``sandbox_check`` and libproc.

The kernel tests (``test_lab_runner.py``, ``test_lab_sandbox.py``) prove what the sandbox refuses, and skip
wherever ``sandbox-exec`` doesn't exist: the ubuntu legs. These run the runner's own bookkeeping there too, through
the same :func:`scout.lab.runner.run_experiment`: the gate, the polls, the kill path, the run log, the meter and
every way a run is refused. The stand-ins hold nothing in a sandbox, so a test here never claims a denial.

- The stand-in ``sandbox-exec`` (``profile.SANDBOX_EXEC`` is pointed at it) checks it was given ``-f <profile>``,
  records its pid (the script's, since everything after it ``exec``s) and ``exec``s the rest of its argv.
- The stand-in probe calls a process "this run's sandbox" when its process group is one of those pids: only
  processes the test itself started, so the runner's freeze-and-kill sweep never reaches anything else.
- The stand-in libproc lists whatever unlinked files the test hands it.
"""

from __future__ import annotations

import os
import resource
import shlex
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import paths, runner, sandbox
from scout.sandbox import profile
from tests.unit import lab_runner_helpers as h

MIB = 1 << 20


@dataclass
class _Kernel:
    """What the stand-ins answer. ``leaders`` is the file the stand-in sandbox-exec appends its pid to."""

    leaders: Path
    probe_works: bool = True
    wrong_answers: bool = False
    open_files_work: bool = True
    unlinked: Callable[[int], list[sandbox.UnlinkedFile] | None] = field(default=lambda _pid: [])

    def pids(self) -> set[int]:
        try:
            return {int(x) for x in self.leaders.read_text().split()}
        except FileNotFoundError:
            return set()


class _Probe:
    def __init__(self, kernel: _Kernel) -> None:
        self.kernel = kernel

    def sandboxed(self, pid: int) -> bool:
        try:
            return os.getpgid(pid) in self.kernel.pids()
        except OSError:
            return False

    def denies(self, pid: int, operation: str, path: str) -> bool | None:
        if not self.sandboxed(pid):
            return None
        if self.kernel.wrong_answers:
            return False  # a sandbox that allows everything, the decoy and the outside included
        return os.path.basename(path) != "probe"  # the run's probe file only

    def works(self, existing_dir: str) -> bool:
        return self.kernel.probe_works


class _OpenFiles:
    def __init__(self, kernel: _Kernel, max_fds: int) -> None:
        self.kernel = kernel
        self.max_fds = max_fds

    def unlinked(self, pid: int) -> list[sandbox.UnlinkedFile] | None:
        return self.kernel.unlinked(pid)

    def works(self) -> bool:
        return self.kernel.open_files_work


def _nproc() -> int:
    soft = resource.getrlimit(resource.RLIMIT_NPROC)[0]
    return soft if soft != resource.RLIM_INFINITY else 1 << 20


def _stand_in(path: Path, leaders: Path, body: str = "") -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "# Stand-in for /usr/bin/sandbox-exec -f <profile> <argv...>: no sandbox, just the argv.\n"
        '[ "$1" = -f ] && [ -f "$2" ] || { echo "sandbox-exec: usage" >&2; exit 64; }\n'
        f"{body}"
        f"echo $$ >> {shlex.quote(str(leaders))}\n"
        "shift 2\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Kernel:
    kernel = _Kernel(tmp_path / "leaders")
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    monkeypatch.setattr(profile, "available", lambda: True)
    monkeypatch.setattr(profile, "SANDBOX_EXEC", _stand_in(bin_dir / "sandbox-exec", kernel.leaders))
    monkeypatch.setattr(runner, "_nproc_limit", lambda _max: _nproc())  # the real count is tested on its own
    monkeypatch.setattr(runner.sandbox, "SandboxProbe", lambda: _Probe(kernel))
    monkeypatch.setattr(runner.sandbox, "OpenFiles", lambda max_fds=1024: _OpenFiles(kernel, max_fds))
    return kernel


def _gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _log(vault: Path) -> str:
    [log] = sorted(h.exp_dir(vault).glob("run-*.log"))
    return log.read_text(encoding="utf-8")


# --- a run, end to end ----------------------------------------------------------------------------------------------


def test_a_run_reports_its_exit_tails_env_and_log(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    exp = h.exp_dir(vault)
    out = h.run(vault, tmp_path, 'env > env.txt; echo "$TMPDIR" > tmpdir.txt; echo done; echo oops >&2; exit 4')
    assert out.exit == 4 and out.stdout_tail.strip() == "done" and out.stderr_tail.strip() == "oops"
    assert not out.timed_out and not out.over_bytes and not out.over_processes
    env = dict(line.split("=", 1) for line in (exp / "env.txt").read_text().splitlines() if "=" in line)
    assert env["SCOUT_LAB_EXPERIMENT_DIR"] == os.path.realpath(exp)
    assert env["HOME"] == str(tmp_path / "home") and "SCOUT_LAB_SOURCE_DIR" not in env
    assert (exp / "tmpdir.txt").read_text().strip() == os.path.realpath(exp / ".tmp")
    assert not (exp / ".tmp").exists()
    assert not Path(env["SCOUT_LAB_METER"]).parent.exists()  # the runner's temp dir is gone
    text = _log(vault)
    for line in ("exit: 4", "timed_out: false", "over_processes: false (limit 64)", "spent_usd: 0.0000"):
        assert line in text, line
    assert "survivors" not in text and "done" in text and "oops" in text
    assert fake.pids()  # the script ran through the stand-in


def test_the_meter_serves_the_script_and_holds_the_cap(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    script = f'printf hi > p.txt; {h.CALL}; echo "rc1=$?"; {h.CALL}; echo "rc2=$?"'
    out = h.run(vault, tmp_path, script, cap=0.5)
    assert "rc1=0" in out.stdout_tail and "rc2=3" in out.stdout_tail, out.stderr_tail
    assert "echo:hi" in out.stdout_tail and "cost cap" in out.stderr_tail
    assert out.spent_usd == pytest.approx(0.4)
    assert "spent_usd: 0.4000 (this run 0.4000, cap 0.50)" in _log(vault)


def test_a_bare_claude_name_is_found_on_the_runners_path(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "claude-bin"
    bin_dir.mkdir()
    claude = bin_dir / "fake-claude"
    claude.write_text(f'#!/bin/sh\nexec {shlex.quote(str(h.FAKE))} {shlex.quote(str(tmp_path / "calls.log"))} "$@"\n')
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    vault = h.make_vault(tmp_path)
    out = runner.run_experiment(
        vault, h.SLUG, h.EID, ["/bin/sh", "-c", f"printf hi > p.txt; {h.CALL}"],
        cap_usd=1, timeout_s=60, env=h.make_env(tmp_path), claude_argv=("fake-claude",),
    )  # fmt: skip
    assert out.exit == 0 and "echo:hi" in out.stdout_tail, out.stderr_tail


def test_the_timeout_kills_the_group(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    started = time.monotonic()
    out = h.run(vault, tmp_path, "sleep 300 & sleep 300", timeout_s=3)
    assert time.monotonic() - started < 60
    assert out.timed_out and out.exit == -signal.SIGKILL
    # The group is the stand-in sandbox-exec's pid (the script is exec'd into it): recorded before the gate opens,
    # so it's there however late the script itself got going.
    [pgid] = fake.pids()
    assert _gone(pgid)
    assert "timed_out: true" in _log(vault)


def test_a_child_left_behind_after_a_normal_exit_is_killed(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "sleep 300 & echo $! > child.txt; exit 0")
    assert out.exit == 0 and not out.timed_out
    child = int((h.exp_dir(vault) / "child.txt").read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the child outlived the run")


# --- totals: bytes and processes ------------------------------------------------------------------------------------


def _write(name: str, kib: int) -> str:
    return f"head -c {kib * 1024} /dev/zero > {name}"


def test_bytes_over_the_cap_at_a_poll_kill_the_run(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    started = time.monotonic()
    # Two files under the per-file limit (`ulimit -f` is the whole cap), over it together.
    out = h.run(vault, tmp_path, f"{_write('a.bin', 700)}; {_write('b.bin', 700)}; sleep 60", max_total_bytes=MIB)
    assert time.monotonic() - started < 30
    assert out.over_bytes and not out.timed_out and out.exit == -signal.SIGKILL
    assert f"over_bytes: true (cap {MIB} bytes)" in _log(vault)


def test_bytes_over_the_cap_at_a_quick_exit_still_count(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, f"{_write('a.bin', 700)}; {_write('b.bin', 700)}; exit 0", max_total_bytes=MIB)
    assert out.over_bytes and out.exit == 0


def test_under_the_cap_runs_to_the_end(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, f"{_write('a.bin', 100)}; sleep 1.5; echo end", max_total_bytes=MIB)
    assert out.exit == 0 and not out.over_bytes and out.stdout_tail.strip() == "end"


def test_a_holders_unlinked_files_count_toward_the_cap(tmp_path: Path, fake: _Kernel) -> None:
    fake.unlinked = lambda _pid: [(1, 42, 2 * MIB)]
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "sleep 60", max_total_bytes=MIB, timeout_s=30)
    assert out.over_bytes and not out.timed_out


def test_a_holder_whose_files_cant_be_listed_counts_as_over(tmp_path: Path, fake: _Kernel) -> None:
    def refused(_pid: int) -> list[sandbox.UnlinkedFile] | None:
        raise PermissionError("not listed")

    fake.unlinked = refused
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "sleep 60", timeout_s=30)
    assert out.over_bytes and not out.timed_out


def test_more_processes_than_the_allowance_kill_the_run(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    out = h.run(vault, tmp_path, "sleep 60 & sleep 60 & sleep 60 & wait", max_processes=2, timeout_s=30)
    assert out.over_processes and not out.over_bytes and not out.timed_out
    assert "over_processes: true (limit 2)" in _log(vault)


# --- what stops a run, or refuses it --------------------------------------------------------------------------------


def test_a_run_whose_probe_file_is_gone_is_not_clean(tmp_path: Path, fake: _Kernel) -> None:
    """Off the kernel nothing stops the script removing the sweep's probe file: the sweep can't tell, so the run
    is an error and its log says a process may have survived."""
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="survived every kill sweep"):
        h.run(vault, tmp_path, 'rm -f "$(dirname "$SCOUT_LAB_METER")/probe"')
    assert "survivors:" in _log(vault)


def test_wrong_answers_about_the_leader_run_nothing(tmp_path: Path, fake: _Kernel) -> None:
    fake.wrong_answers = True
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="before it started"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()
    assert all(_gone(pid) for pid in fake.pids())


def test_a_leader_never_seen_sandboxed_runs_nothing(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_KILL_WAIT_S", 0.5)
    monkeypatch.setattr(runner.sandbox, "SandboxProbe", lambda: _Probe(_Kernel(tmp_path / "nobody")))
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="before it started"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


def test_a_sandbox_exec_that_rejects_the_profile_is_an_error(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejecting = _stand_in(tmp_path / "rejecting", fake.leaders, 'echo "sandbox-exec: bad profile" >&2; exit 65\n')
    monkeypatch.setattr(profile, "SANDBOX_EXEC", rejecting)
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match=r"could not apply the experiment profile \(exit 65\)"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        (lambda k: setattr(k, "probe_works", False), "sandbox_check gave a wrong answer about this process"),
        (lambda k: setattr(k, "open_files_work", False), "libproc gave a wrong answer"),
    ],
)
def test_a_probe_that_fails_its_self_check_refuses(
    tmp_path: Path, fake: _Kernel, setup: Callable[[_Kernel], None], match: str
) -> None:
    setup(fake)
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match=match):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not fake.pids() and not (h.exp_dir(vault) / "ran.txt").exists()


@pytest.mark.parametrize(("name", "match"), [("SandboxProbe", "sandbox_check"), ("OpenFiles", "libproc")])
def test_a_library_that_wont_load_refuses(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch, name: str, match: str
) -> None:
    def missing(*_a: object, **_k: object) -> None:
        raise OSError("dlopen: not found")

    monkeypatch.setattr(runner.sandbox, name, missing)
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match=f"cannot load {match}"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not fake.pids()


def test_a_profile_that_cant_be_built_refuses(tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(*_a: object, **_k: object) -> str:
        raise profile.ProfileError("a path SBPL can't carry")

    monkeypatch.setattr(runner.sandbox, "experiment_profile", bad)
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="cannot build the experiment sandbox"):
        h.run(vault, tmp_path, "echo ran > ran.txt")


def test_a_profile_missing_the_folders_allow_refuses(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rendered profile must allow exactly the folders that were checked: one that doesn't is refused."""
    monkeypatch.setattr(runner.sandbox, "experiment_profile", lambda *_a, **_k: "(version 1)\n(allow default)\n")
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match="changed while the sandbox was being built"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not fake.pids()


def test_a_private_experiment_skips_the_deny_walk(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def walked(*_a: object) -> object:
        raise AssertionError("a private experiment reads nothing private it isn't already")

    monkeypatch.setattr(runner, "_deny_reads", walked)
    vault = h.make_vault(tmp_path, visibility="private")
    out = h.run(vault, tmp_path, "echo ok")
    assert out.exit == 0 and out.stdout_tail.strip() == "ok"


def test_a_version_manager_shim_first_on_path_refuses(tmp_path: Path, fake: _Kernel) -> None:
    shims = tmp_path / "pyenv" / "shims"
    shims.mkdir(parents=True)
    (shims / "python3").write_text("#!/bin/sh\n")
    (shims / "python3").chmod(0o755)
    vault = h.make_vault(tmp_path)
    env = {**h.make_env(tmp_path), "PATH": f"{shims}{os.pathsep}/usr/bin:/bin"}
    with pytest.raises(LabError, match="version-manager shim"):
        runner.run_experiment(vault, h.SLUG, h.EID, ["/bin/sh", "-c", "true"], cap_usd=1, env=env)
    assert not fake.pids()


# --- the committed scripts (R30, R34) -------------------------------------------------------------------------------


def _commit(vault: Path, files: dict[str, str]) -> str:
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


def _run_at(vault: Path, tmp_path: Path, argv: list[str], commit: str) -> runner.RunOutcome:
    return runner.run_experiment(
        vault, h.SLUG, h.EID, argv, cap_usd=1, timeout_s=60, env=h.make_env(tmp_path),
        claude_argv=(str(h.FAKE), str(tmp_path / "calls.log")), commit=commit,
    )  # fmt: skip


def test_a_committed_script_runs_from_its_snapshot(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit(vault, {"run.sh": 'sh "$SCOUT_LAB_SOURCE_DIR/helper.sh"\n', "helper.sh": "echo COMMITTED\n"})
    (h.exp_dir(vault) / "run.sh").write_text("echo EDITED\n", encoding="utf-8")  # after the broker's check
    out = _run_at(vault, tmp_path, ["sh", "run.sh"], commit)
    assert out.exit == 0, out.stderr_tail
    assert out.stdout_tail.strip() == "COMMITTED"


def test_a_note_private_only_in_the_worktree_runs_as_normal(
    tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private takes the note saying so both at the commit and in the worktree: here only the worktree does, so
    the private-data denies are built (the deny walk runs)."""
    vault = h.make_vault(tmp_path)
    commit = _commit(vault, {"run.sh": "echo ok\n"})
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    note.write_text(note.read_text("utf-8").replace("visibility: normal", "visibility: private", 1), "utf-8")
    walks: list[str] = []
    real = runner._deny_reads

    def counted(vault_: Path, exp_real: str) -> tuple[list[Path], list[Path]]:
        walks.append(exp_real)
        return real(vault_, exp_real)

    monkeypatch.setattr(runner, "_deny_reads", counted)
    out = _run_at(vault, tmp_path, ["sh", "run.sh"], commit)
    assert out.exit == 0 and walks


def test_a_commit_that_isnt_there_refuses(tmp_path: Path, fake: _Kernel) -> None:
    vault = h.make_vault(tmp_path)
    _commit(vault, {"run.sh": "echo ok\n"})
    with pytest.raises(LabError, match="cannot read the experiment's scripts"):
        _run_at(vault, tmp_path, ["sh", "run.sh"], "0" * 40)
    assert not fake.pids()


def test_a_commit_listing_git_cant_give_refuses(tmp_path: Path, fake: _Kernel, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    commit = _commit(vault, {"run.sh": "echo ok\n"})
    real = runner._git

    def no_listing(vault_: Path, *args: str) -> bytes | None:
        return None if args[0] == "ls-tree" else real(vault_, *args)

    monkeypatch.setattr(runner, "_git", no_listing)
    with pytest.raises(LabError, match="cannot list the experiment's committed files"):
        _run_at(vault, tmp_path, ["sh", "run.sh"], commit)
    exp_rel = h.exp_dir(vault).relative_to(vault).as_posix()
    monkeypatch.setattr(runner, "_git", lambda *_a: b"elsewhere/x.sh\0")
    with pytest.raises(LabError, match="outside the experiment folder"):
        runner._committed_files(vault, commit, exp_rel, str(h.exp_dir(vault)))
    monkeypatch.setattr(runner, "_git", lambda *_a: f"{exp_rel}/run.sh\0\0".encode())
    assert runner._committed_files(vault, commit, exp_rel, "/x") == [Path("/x/run.sh")]


def test_a_snapshot_that_wont_unpack_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_git", lambda *_a: b"not a tar archive" * 64)
    with pytest.raises(LabError, match="cannot unpack"):
        runner._snapshot(tmp_path, "0" * 40, "x", str(tmp_path / "src"))


def test_the_committed_note_decides_private_only_when_it_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path, visibility="private")
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    text = note.read_text("utf-8")
    for data, private in (
        (None, False),
        (b"\xff\xfe not utf-8", False),
        (text.encode(), True),
        (text.replace("id: E2", "id: E1").encode(), False),
    ):
        monkeypatch.setattr(runner, "_git", lambda *_a, d=data: d)
        assert runner._committed_private(vault, h.SLUG, h.EID, "0" * 40, note) is private, data


def test_git_that_cant_start_or_fails_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner._git(tmp_path, "rev-parse", "HEAD") is None  # not a repository: git exits non-zero

    def broken(*_a: object, **_k: object) -> object:
        raise OSError("no git")

    monkeypatch.setattr(runner.subprocess, "run", broken)
    assert runner._git(tmp_path, "status") is None


# --- the pieces, one at a time --------------------------------------------------------------------------------------


def test_the_sweep_freezes_then_kills_every_holder(tmp_path: Path) -> None:
    kernel = _Kernel(tmp_path / "leaders")
    marker = tmp_path / "probe"
    for p in (marker, Path(runner._decoy(str(marker)))):
        p.write_text("")
    procs = [subprocess.Popen(["sleep", "60"], start_new_session=True) for _ in range(3)]
    try:
        kernel.leaders.write_text("".join(f"{p.pid}\n" for p in procs))
        assert runner._sweep(_Probe(kernel), str(marker), str(tmp_path)) is True
        for p in procs:
            assert p.wait(timeout=10) == -signal.SIGKILL
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_a_sweep_that_keeps_finding_holders_gives_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A holder the kill never reaches (here: the kill is a no-op) makes the sweep 'not clean' after its rounds."""
    kernel = _Kernel(tmp_path / "leaders")
    marker = tmp_path / "probe"
    for p in (marker, Path(runner._decoy(str(marker)))):
        p.write_text("")
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        kernel.leaders.write_text(f"{proc.pid}\n")
        with monkeypatch.context() as m:  # undone before the cleanup below, which needs os.kill
            m.setattr(runner.os, "kill", lambda *_a: None)
            m.setattr(runner, "_SWEEP_ROUNDS", 2)
            m.setattr(runner.time, "sleep", lambda _s: None)
            assert runner._sweep(_Probe(kernel), str(marker), str(tmp_path)) is False
    finally:
        proc.kill()
        proc.wait()


def test_a_group_the_runner_may_not_signal_counts_as_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(_pgid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(runner.os, "killpg", refused)
    assert runner._group_alive(12345) is True


def test_own_live_pids_lists_this_users_processes_but_not_this_one(monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen(["sleep", "30"])
    try:
        pids = runner._own_live_pids()
        assert child.pid in pids and os.getpid() not in pids
    finally:
        child.kill()
        child.wait()
    uid = os.getuid()
    listing = f"  12 {uid} S\n  13 {uid} Z\n  14 {uid + 1} S\n garbage\n  {os.getpid()} {uid} R\n"
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, listing, ""))
    assert runner._own_live_pids() == [12]  # a zombie, another user's process and this one left out

    def broken(*_a: object, **_k: object) -> object:
        raise OSError("no ps")

    monkeypatch.setattr(runner.subprocess, "run", broken)
    assert runner._own_live_pids() == []


def test_the_owners_process_count_runs_its_helper() -> None:
    count = runner._owner_process_count()
    assert count is not None and count >= 1


def test_tail_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert runner._tail(str(tmp_path / "missing")) == ""
    big = tmp_path / "big"
    big.write_bytes(b"a" * 10_000 + b"END")
    assert runner._tail(str(big)).endswith("END") and len(runner._tail(str(big))) == runner.TAIL_BYTES


def test_the_run_log_takes_the_next_free_name_and_gives_up_cleanly(tmp_path: Path) -> None:
    import datetime as dt

    started = dt.datetime(2026, 11, 21, 13, 30)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert runner._write_log(fd, started, "one") == "run-20261121-1330.log"
        assert runner._write_log(fd, started, "two") == "run-20261121-1330-2.log"
        for n in range(3, 100):
            (tmp_path / f"run-20261121-1330-{n}.log").write_text("")
        assert runner._write_log(fd, started, "full") is None  # every name taken
    finally:
        os.close(fd)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    fd = os.open(ro, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if os.geteuid() != 0:
            assert runner._write_log(fd, started, "x") is None  # can't create it at all
    finally:
        os.close(fd)
        ro.chmod(0o700)
    assert runner.runner_owned("run-20261121-1330-2.log") and runner.runner_owned(".tmp/x")
    assert not runner.runner_owned("sub/run-20261121-1330.log") and not runner.runner_owned("out.csv")


def test_clear_removes_a_folder_a_file_or_nothing(tmp_path: Path) -> None:
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "f").write_text("x")
    (tmp_path / "l").symlink_to(tmp_path / "d")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in ("d", "f", "l", "missing"):
            runner._clear(fd, name)
    finally:
        os.close(fd)
    assert sorted(os.listdir(tmp_path)) == []


def test_the_experiment_folder_must_be_inside_the_vault(tmp_path: Path) -> None:
    """A symlinked folder above it (here the experiments folder) points the allowed path outside the vault."""
    vault = h.make_vault(tmp_path)
    exps = paths.thread_dir(vault, h.SLUG) / "experiments"
    moved = tmp_path / "moved-experiments"
    exps.rename(moved)
    exps.symlink_to(moved)
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    with pytest.raises(LabError, match="resolves outside the vault"):
        runner._open_experiment_dir(vault, note)


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 folder")
def test_an_experiment_folder_that_cant_be_opened_refuses(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    folder = h.exp_dir(vault)
    folder.mkdir(mode=0o000)
    try:
        with pytest.raises(LabError, match="cannot open"):
            runner._open_experiment_dir(vault, note)
    finally:
        folder.chmod(0o755)


def test_an_experiment_folder_swapped_while_it_opens_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    folder = h.exp_dir(vault)
    folder.mkdir()
    expected = os.path.realpath(folder)
    real_stat = os.stat

    def swapped(path: object, *a: object, **k: object) -> os.stat_result:
        if path == expected and k.get("follow_symlinks") is False:
            raise FileNotFoundError(path)  # gone between the open and the second look
        return real_stat(path, *a, **k)  # type: ignore[arg-type]

    with monkeypatch.context() as m:
        m.setattr(runner.os, "stat", swapped)
        with pytest.raises(LabError, match="changed while it was being opened"):
            runner._open_experiment_dir(vault, note)


def test_a_note_that_cant_be_read_or_names_another_id_refuses(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    note = paths.experiment_note(vault, h.SLUG, h.EID)
    assert note is not None
    note.write_text(note.read_text("utf-8").replace("id: E2", "id: E1", 1), "utf-8")
    with pytest.raises(LabError, match="has id 'E1', not E2"):
        runner._running_note(vault, h.SLUG, h.EID)
    note.write_bytes(b"\xff\xfe")
    with pytest.raises(LabError, match="cannot read"):
        runner._running_note(vault, h.SLUG, h.EID)


def test_the_run_lock_refuses_a_file_it_cant_open(tmp_path: Path) -> None:
    env = h.make_env(tmp_path)
    vault = h.make_vault(tmp_path)
    lock = runner.run_lock_path(vault, h.SLUG, h.EID, env)
    lock.mkdir()  # a folder where the lock file goes
    with (
        pytest.raises(LabError, match="cannot open the experiment's run lock"),
        runner._run_lock(vault, h.SLUG, h.EID, env),
    ):
        pass


def test_other_checkouts_are_found_from_the_git_dirs(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    ident = ["-c", "user.name=Alex", "-c", "user.email=alex@example.org", "-c", "commit.gpgsign=false"]

    def git(*args: str, cwd: Path = main) -> None:
        subprocess.run(["git", *ident, *args], cwd=cwd, env=env, check=True, capture_output=True)

    git("init", "-q")
    (main / "a.txt").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "init")
    linked = tmp_path / "linked"
    git("worktree", "add", "-q", str(linked))
    found = [os.path.realpath(p) for p in runner.other_checkouts(linked)]
    assert os.path.realpath(main) in found and os.path.realpath(linked) not in found
    found = [os.path.realpath(p) for p in runner.other_checkouts(main)]
    assert found == [os.path.realpath(linked)]
    # A worktree pointer that can't be read is skipped.
    [pointer] = (main / ".git" / "worktrees").glob("*/gitdir")
    pointer.write_bytes(b"\xff\xfe")
    assert runner.other_checkouts(main) == []


def test_interpreter_roots_never_widen_to_a_home_folder(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    conda = tmp_path / "home" / "miniconda3"
    (conda / "conda-meta").mkdir(parents=True)
    assert runner._interpreter_root(f"{conda}/bin/python3", [home]) == str(conda)  # a conda base: whole
    assert runner._interpreter_root("/opt/py/bin/python3", [home]) == "/opt/py"  # an ordinary prefix
    assert runner._interpreter_root(f"{home}/.local/bin/python3", [home]) == f"{home}/.local/bin"
    assert runner._interpreter_root(f"{home}/bin/python3", [home]) == f"{home}/bin/python3"  # just the file
    assert runner._below("/a/b/c", "/a") == 2 and runner._below("/a", "/a") == 0 and runner._below("/b", "/a") is None
    assert runner._is_shim("/x/.pyenv/shims/python3") and runner._is_shim("/x/volta/bin/volta-shim")
    assert not runner._is_shim("/usr/bin/python3")


def test_deny_execs_lists_git_and_claude_once(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("git", "claude"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    found = runner._deny_execs([str(bin_dir), str(bin_dir), None])
    reals = [str(p) for p in found]
    assert os.path.realpath(bin_dir / "git") in reals and os.path.realpath(bin_dir / "claude") in reals
    assert len(reals) == len(set(reals))


def test_the_deny_walk_handles_links_and_other_checkouts(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    exp = h.exp_dir(vault)
    exp.mkdir()
    kb = vault / "knowledge-base"
    outside = tmp_path / "outside"
    outside.mkdir()
    (kb / "linked").symlink_to(outside)  # a folder link: denied as a whole, not walked
    (kb / "into-exp").symlink_to(exp)  # a link into the experiment: left readable
    for name in ("Raw", "action-items"):
        (vault / name).mkdir()
    (vault / ".scout-inbox").mkdir()
    denied, _ = runner._deny_reads(vault, os.path.realpath(exp))
    assert kb / "linked" in denied and kb / "into-exp" not in denied
    assert vault / "Raw" in denied and vault / "action-items" in denied and vault / ".scout-inbox" in denied


def test_a_lab_folder_that_cant_be_listed_has_no_private_folders(tmp_path: Path) -> None:
    assert runner.private_lab_folders(tmp_path / "no-vault", {}) == []


@pytest.mark.parametrize(
    ("argv", "timeout_s", "match"), [([], 60.0, "nothing to run"), (["true"], 0.0, "timeout is 0.0")]
)
def test_an_empty_command_or_no_timeout_refuses(tmp_path: Path, argv: list[str], timeout_s: float, match: str) -> None:
    vault = h.make_vault(tmp_path)
    with pytest.raises(LabError, match=match):
        runner.run_experiment(vault, h.SLUG, h.EID, argv, cap_usd=1, timeout_s=timeout_s, env=h.make_env(tmp_path))


def test_the_deny_walk_covers_every_other_checkout_of_the_vault(tmp_path: Path) -> None:
    """A vault nested in another checkout of its repo gets that checkout's private parts denied; a checkout beside
    it is denied whole."""
    main = h.make_vault(tmp_path)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    ident = ["-c", "user.name=Alex", "-c", "user.email=alex@example.org", "-c", "commit.gpgsign=false"]

    def git(*args: str) -> None:
        subprocess.run(["git", *ident, *args], cwd=main, env=env, check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "vault")
    nested, beside = main / ".claude" / "worktrees" / "w", tmp_path / "beside"
    git("worktree", "add", "-q", str(nested))
    git("worktree", "add", "-q", str(beside))
    for vault in (nested, beside):
        h.exp_dir(vault).mkdir()
    denied, _ = runner._deny_reads(nested, os.path.realpath(h.exp_dir(nested)))
    assert main / "knowledge-base" in denied and beside in denied and main not in denied
    denied, _ = runner._deny_reads(beside, os.path.realpath(h.exp_dir(beside)))
    assert main in denied and nested not in denied  # already under the main checkout, denied whole


def test_allow_reads_without_any_interpreter_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    vault = h.make_vault(tmp_path)
    allowed = runner._allow_reads(vault, {"PATH": str(empty), "HOME": str(tmp_path / "home")}, [])
    assert Path(os.path.realpath(vault)) in allowed


def test_tree_bytes_skips_entries_removed_while_it_walks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "exp"
    (root / "vanishing").mkdir(parents=True)
    (root / "kept.bin").write_bytes(b"x" * 100)
    real_listdir, real_open = os.listdir, os.open

    def listdir(path: object) -> list[str]:
        names = real_listdir(path)  # type: ignore[arg-type]
        return [*names, "ghost"] if isinstance(path, int) else names  # a file gone before its lstat

    def open_(path: object, flags: int, *a: object, dir_fd: int | None = None, **k: object) -> int:
        if path == "vanishing" and dir_fd is not None:
            raise FileNotFoundError(path)  # a folder gone between its lstat and its open
        return real_open(path, flags, *a, dir_fd=dir_fd, **k)  # type: ignore[arg-type]

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with monkeypatch.context() as m:
            m.setattr(runner.os, "listdir", listdir)
            m.setattr(runner.os, "open", open_)
            total = runner._tree_bytes(fd)
    finally:
        os.close(fd)
    assert total is not None and total >= 100


def test_a_live_group_is_alive() -> None:
    assert runner._group_alive(os.getpgid(0)) is True


def test_kill_all_waits_out_a_group_still_dying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "probe"
    for p in (marker, Path(runner._decoy(str(marker)))):
        p.write_text("")
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    answers = iter([True, False])
    probe = _Probe(_Kernel(tmp_path / "leaders"))
    with monkeypatch.context() as m:
        m.setattr(runner, "_group_alive", lambda _pgid: next(answers, False))
        clean = runner._kill_all(proc, probe, str(marker), str(tmp_path))  # type: ignore[arg-type]
    assert clean is True and proc.returncode == -signal.SIGKILL


def test_a_sweep_whose_holders_keep_multiplying_stops_freezing_after_50_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each pass finds a new holder (a fork bomb outrunning the freeze): after 50 the sweep kills what it froze
    and, with holders still there, isn't clean. No real process is touched: the holders are stand-in numbers."""
    marker = tmp_path / "probe"
    for p in (marker, Path(runner._decoy(str(marker)))):
        p.write_text("")
    found = iter(range(1_000_000, 2_000_000))
    signalled: list[tuple[int, int]] = []
    with monkeypatch.context() as m:
        m.setattr(runner, "_holders", lambda *_a: {next(found)})
        m.setattr(runner.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
        m.setattr(runner, "_SWEEP_ROUNDS", 1)
        m.setattr(runner.time, "sleep", lambda _s: None)
        assert runner._sweep(_Probe(_Kernel(tmp_path / "leaders")), str(marker), str(tmp_path)) is False
    stops = [pid for pid, sig in signalled if sig == signal.SIGSTOP]
    kills = [pid for pid, sig in signalled if sig == signal.SIGKILL]
    assert len(stops) == 50 and sorted(kills) == sorted(stops)

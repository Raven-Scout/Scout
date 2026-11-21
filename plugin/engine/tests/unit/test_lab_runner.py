"""The experiment runner: meter, timeout, process-group kill, run log, and what it refuses."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
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


@pytest.mark.parametrize("status", ["planned", "done", "abandoned", "vetoed"])
def test_refuses_unless_running(tmp_path: Path, status: str) -> None:
    vault = h.make_vault(tmp_path, status=status)
    with pytest.raises(LabError, match="running"):
        h.run(vault, tmp_path, "echo ran > ran.txt")
    assert not (h.exp_dir(vault) / "ran.txt").exists()


def test_refuses_missing_experiment_and_bad_ids(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    env = h.make_env(tmp_path)
    for slug, eid in [(h.SLUG, "E9"), ("../x", h.EID), (h.SLUG, "E2/../E1"), ("Monitors", h.EID)]:
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
    denied = runner._deny_reads(vault, exp_real)
    assert personal in denied and vault / h.PRIVATE_NOTE in denied
    assert vault / h.NORMAL_NOTE not in denied
    assert not any(p.is_relative_to(personal) and p != personal for p in denied)
    assert not any(_touches(p, exp) for p in denied)

    # No labels (they couldn't be read): every KB file but the experiment's own is denied.
    monkeypatch.setattr(runner, "visibility_map", lambda *a, **k: {})
    denied = runner._deny_reads(vault, exp_real)
    assert vault / "knowledge-base" / "people" in denied
    assert paths.front_page(vault, h.SLUG) in denied
    assert not any(_touches(p, exp) for p in denied)


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


@kernel
def test_sweep_is_not_clean_without_its_probe_files(tmp_path: Path) -> None:
    """A probe file that is gone or a link makes the sweep 'not clean', never a silent pass."""
    marker = tmp_path / "probe"
    probe = runner.sandbox.SandboxProbe()
    assert runner._sweep(probe, str(marker), str(tmp_path)) is False  # neither file exists
    marker.write_text("")
    Path(runner._decoy(str(marker))).symlink_to(tmp_path)
    assert runner._sweep(probe, str(marker), str(tmp_path)) is False

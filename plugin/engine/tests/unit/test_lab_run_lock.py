"""W3: one run of an experiment at a time (``runner.run_lock_path``), held across processes.

Each run takes an exclusive, non-blocking ``flock`` on a lock file in the owner's folder for the length of the run;
a second run of the same experiment is refused at once, before it touches the experiment folder. The processes are
real (``subprocess``) and synchronised through files, never sleeps: run A signals once it holds the lock and waits
for the test's release file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import scout
from scout.errors import LabError
from scout.lab import paths, runner
from scout.sandbox import broker, profile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG, EID = "summary-drift", "E2"
ENGINE = str(Path(scout.__file__).resolve().parents[1])
WAIT_S = 20.0
ALREADY = f"experiment {SLUG} {EID} is already running"

# A run of E2 in its own process. The runner's body (`_run`) is replaced so no sandbox is needed: it says it has
# started (the lock is held by then) and waits for the release file. Prints "ran" or the LabError.
CHILD = r"""
import os, sys, time
from pathlib import Path
from scout.errors import LabError
from scout.lab import runner
from scout.sandbox import profile

vault, home, started, release = sys.argv[1:5]

def body(*args, **kwargs):
    Path(started).write_text("started")
    deadline = time.monotonic() + 20
    while not os.path.exists(release) and time.monotonic() < deadline:
        time.sleep(0.01)
    return runner.RunOutcome(0, "", "", 0.0, False)

runner._run = body
profile.available = lambda: True
try:
    runner.run_experiment(Path(vault), "summary-drift", "E2", ["./run.sh"], cap_usd=1.0, env={"HOME": home})
    print("ran")
except LabError as exc:
    print(f"LabError: {exc}")
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = (tmp_path / "vault").resolve()
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(v, SLUG))
    note = paths.experiment_note(v, SLUG, EID)
    assert note is not None
    note.write_text(note.read_text(encoding="utf-8").replace("status: planned", "status: running"), "utf-8")
    folder = paths.experiment_dir(note)
    folder.mkdir()
    (folder / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (folder / "run.sh").chmod(0o755)
    return v


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def _wait_for(path: Path, proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + WAIT_S
    while not path.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            out, err = proc.communicate(timeout=5)
            pytest.fail(f"the first run never started: {out!r} {err!r}")
        time.sleep(0.01)


def _child(vault: Path, home: Path, tmp_path: Path, name: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": ENGINE, "HOME": str(home)}
    started, release = tmp_path / f"{name}.started", tmp_path / "release"
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(vault), str(home), str(started), str(release)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def first_run(vault: Path, home: Path, tmp_path: Path) -> Iterator[subprocess.Popen[str]]:
    """Run A, holding E2's run lock until the test writes the release file."""
    proc = _child(vault, home, tmp_path, "a")
    try:
        _wait_for(tmp_path / "a.started", proc)
        yield proc
    finally:
        (tmp_path / "release").write_text("go")
        if proc.poll() is None:
            proc.wait(timeout=WAIT_S)


@pytest.mark.concurrency
def test_two_concurrent_runs_exactly_one_runs(
    vault: Path, home: Path, tmp_path: Path, first_run: subprocess.Popen[str]
) -> None:
    second = _child(vault, home, tmp_path, "b")
    out_b, err_b = second.communicate(timeout=WAIT_S)
    assert not (tmp_path / "b.started").exists(), "the second run got past the lock"
    assert out_b.strip() == f"LabError: {ALREADY}", err_b
    (tmp_path / "release").write_text("go")
    out_a, err_a = first_run.communicate(timeout=WAIT_S)
    assert out_a.strip() == "ran", err_a
    lock = runner.run_lock_path(vault, SLUG, EID, {"HOME": str(home)})
    assert lock.parent == broker.owner_dir({"HOME": str(home)}) / runner.LEDGER_DIR
    assert lock.stat().st_mode & 0o777 == 0o200  # write-only, like the broker's locks


@pytest.mark.concurrency
def test_the_lock_goes_with_the_run(vault: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Released when a run ends, however it ends: the next run takes it."""
    monkeypatch.setattr(profile, "available", lambda: True)

    def boom(*_: object, **__: object) -> runner.RunOutcome:
        raise LabError("the script's sandbox couldn't be built")

    monkeypatch.setattr(runner, "_run", boom)
    env = {"HOME": str(home)}
    with pytest.raises(LabError, match="couldn't be built"):
        runner.run_experiment(vault, SLUG, EID, ["./run.sh"], cap_usd=1.0, env=env)
    monkeypatch.setattr(runner, "_run", lambda *a, **k: runner.RunOutcome(0, "", "", 0.0, False))
    assert runner.run_experiment(vault, SLUG, EID, ["./run.sh"], cap_usd=1.0, env=env).exit == 0


def test_another_experiment_isnt_held_by_the_lock(vault: Path, home: Path) -> None:
    env = {"HOME": str(home)}
    with runner._run_lock(vault, SLUG, EID, env):
        with pytest.raises(LabError, match=ALREADY):  # a second open file, even in this process
            with runner._run_lock(vault, SLUG, EID, env):
                pass  # never reached
        with runner._run_lock(vault, SLUG, "E1", env):
            pass


def test_a_link_planted_at_the_lock_refuses_the_run(vault: Path, home: Path) -> None:
    env = {"HOME": str(home)}
    runner.run_lock_path(vault, SLUG, EID, env).symlink_to("/dev/null")
    with pytest.raises(LabError, match="cannot open the experiment's run lock"):
        with runner._run_lock(vault, SLUG, EID, env):
            pass  # never reached


@pytest.mark.concurrency
def test_the_status_line_shows_already_running_as_refused(
    vault: Path,
    home: Path,
    first_run: subprocess.Popen[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through the owner's own client path (its validation replaced: the vault here has no git), the runner's
    refusal is the run's last line, ``experiment refused: …``."""
    validated = (SLUG, EID, ["./run.sh"], 1.0, "0" * 40)
    monkeypatch.setattr(broker, "validate_experiment", lambda *_a, **_k: validated)
    env = {"SCOUT_DATA_DIR": str(vault), "HOME": str(home), "PATH": os.environ.get("PATH", "")}
    assert broker.client_experiment(SLUG, EID, ["./run.sh"], env=env) == LabError.exit_code
    assert capsys.readouterr().err.splitlines()[-1] == f"experiment refused: {ALREADY}"


# -- against the kernel: two real runs -----------------------------------------------------------------------------

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")

WAITER = (
    "#!/bin/sh\n: > started\ni=0\nwhile [ ! -e go ] && [ $i -lt 400 ]; do sleep 0.05; i=$((i+1)); done\necho waited\n"
)
KERNEL_CHILD = r"""
import sys
from pathlib import Path
from scout.lab import runner
vault, home = sys.argv[1:3]
out = runner.run_experiment(
    Path(vault), "summary-drift", "E2", ["./wait.sh"], cap_usd=1.0,
    env={"HOME": home, "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
)
print(f"exit {out.exit}: {out.stdout_tail.strip()}")
"""


@kernel
@pytest.mark.concurrency
def test_two_real_runs_exactly_one_runs(vault: Path, home: Path) -> None:
    folder = paths.experiment_dir(paths.experiment_note(vault, SLUG, EID) or vault)
    (folder / "wait.sh").write_text(WAITER, encoding="utf-8")
    (folder / "wait.sh").chmod(0o755)
    env = {**os.environ, "PYTHONPATH": ENGINE, "HOME": str(home)}
    first = subprocess.Popen(
        [sys.executable, "-c", KERNEL_CHILD, str(vault), str(home)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(folder / "started", first)
        tmp_before = (folder / runner.TMP_NAME).is_dir()
        with pytest.raises(LabError, match=ALREADY):
            runner.run_experiment(
                vault, SLUG, EID, ["./wait.sh"], cap_usd=1.0, env={"HOME": str(home), "PATH": "/usr/bin:/bin"}
            )
        assert (folder / runner.TMP_NAME).is_dir() == tmp_before  # the refused run left A's scratch dir alone
    finally:
        (folder / "go").write_text("go")
        out, err = first.communicate(timeout=WAIT_S * 2)
    assert out.strip() == "exit 0: waited", err
    assert len(list(folder.glob("run-*.log"))) == 1

"""Detached experiment runs (spec §6.4): the supervisor, its state record, and the runner's two hooks for it.

The broker's side (``detach`` on the ``experiment`` verb, the ``experiment-status`` verb) is in
``test_run_broker_experiment.py``; here are the supervisor itself (:mod:`scout.lab.supervise`), the record
(:mod:`scout.lab.runs`) and, against the kernel, a detached run that outlives its broker and a killed supervisor whose
script the next collect sweeps. Every process signalled here is one this test started.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout.errors import LabError
from scout.lab import paths, runner, runs, sandbox, supervise
from scout.sandbox import broker, profile
from tests.unit.test_run_broker_experiment import (
    EID,
    REQ,
    SLUG,
    T,
    _commit_script,
    _git,
    _note,
    _owner_config,
)
from tests.unit.test_run_broker_experiment import vault as vault  # noqa: F401 - the committed fixture vault

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")
NOW = T + timedelta(hours=7)
LIMITS = broker.LabLimits(max_total_bytes=64 << 20, disk_free_floor_bytes=0)


def _env() -> dict[str, str]:
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if os.environ.get(k)}


def _inputs(vault: Path, argv: list[str] | None = None, deadline_s: float = 3600) -> supervise.Inputs:
    return supervise.Inputs(
        vault=vault,
        slug=SLUG,
        eid=EID,
        argv=argv or ["./run.sh"],
        commit=_git(vault, "rev-parse", "HEAD").strip(),
        cap_usd=1.0,
        deadline_s=deadline_s,
        limits={k: int(getattr(LIMITS, k)) for k in supervise.LIMIT_KEYS},
        env=_env(),
        claude_argv=["claude"],
    )


def _wait(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.1)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def started_pids() -> Iterator[list[int]]:
    """Supervisors a test started: SIGKILLed at the end if still there (only these, never anything else)."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        if not _gone(pid):
            os.kill(pid, signal.SIGKILL)


# -- the record --------------------------------------------------------------------------------------------------


def test_the_record_round_trips_privately(vault: Path) -> None:
    path = runs.record_path(vault, SLUG, EID, _env())
    assert path.parent == broker.owner_dir(_env()) / "lab-runs" and not path.parent.exists()  # naming creates nothing
    rec = runs.RunRecord(T.isoformat(), (T + timedelta(hours=1)).isoformat(), 123, "/m", "/o", {"status": "ran"})
    runs.write(path, rec)
    assert runs.read(path) == rec
    from dataclasses import fields

    # The runner's names: ``marker`` is the sweep's probe file, ``outside`` the folder the sandbox may not write.
    assert [f.name for f in fields(runs.RunRecord)] == ["started", "deadline", "pid", "marker", "outside", "outcome"]
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    path.write_text('{"started": 1}', encoding="utf-8")
    assert runs.read(path) is None


def test_live_counts_only_records_whose_supervisor_holds_the_lock(vault: Path) -> None:
    env = _env()
    for eid in ("E1", "E2"):
        runs.write(runs.record_path(vault, SLUG, eid, env), runs.RunRecord(T.isoformat(), T.isoformat(), os.getpid()))
    assert runs.live(vault, env) == []  # nothing holds either lock: both supervisors are gone
    with runner._run_lock(vault, SLUG, "E1", env):
        assert [r.pid for r in runs.live(vault, env)] == [os.getpid()]
    assert not runs.lock_held(runs.lock_path(vault, SLUG, "E1", env))


@pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS libproc")
def test_process_start_and_open_files(vault: Path, tmp_path: Path) -> None:
    """The two facts an overrun kill needs about a pid: when it started, and whether it has a file open."""
    before = time.time()
    held = tmp_path / "held"
    held.write_text("", encoding="utf-8")
    code = "import sys, time\nf = open(sys.argv[1])\nopen(sys.argv[2], 'w').write('ready')\ntime.sleep(60)\n"
    ready = tmp_path / "ready"
    proc = subprocess.Popen([sys.executable, "-I", "-c", code, str(held), str(ready)])
    try:
        _wait(ready.exists, 30, "the child")
        start = sandbox.process_start(proc.pid)
        assert start is not None and before - 1 <= start <= time.time()
        st = held.stat()
        files = sandbox.OpenFiles(max_fds=65536)
        assert files.holds(proc.pid, st.st_dev, st.st_ino) is True
        other = tmp_path / "other"
        other.write_text("", encoding="utf-8")
        assert files.holds(proc.pid, other.stat().st_dev, other.stat().st_ino) is False
    finally:
        proc.kill()
        proc.wait()
    assert sandbox.process_start(proc.pid) is None  # gone


def test_hhmm() -> None:
    assert [runs.hhmm(s) for s in (0, 59, 3900, 43200, -5)] == ["0h00m", "0h00m", "1h05m", "12h00m", "0h00m"]


# -- the supervisor, in this process ------------------------------------------------------------------------------


def test_failed_gate_is_the_outcome(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reason = "sandbox_check gave the wrong answer about the sandboxed script before it started; not run"

    def gate_fails(*_a: Any, **_k: Any) -> runner.RunOutcome:
        raise LabError(reason)

    monkeypatch.setattr(runner, "run_experiment", gate_fails)
    outcome = supervise.supervise(_inputs(vault))
    assert outcome["status"] == "not run" and outcome["reason"] == reason
    rec = runs.read(runs.record_path(vault, SLUG, EID, _env()))
    assert rec is not None and rec.outcome == outcome and rec.pid == os.getpid() and rec.marker is None
    reply = broker.experiment_status(vault, SLUG, EID, _env(), datetime.now(UTC))
    assert reply.exit == broker.EXIT_NOT_RUN and reply.stderr == f"experiment not run: {reason}\n"


def test_the_supervisor_holds_the_run_lock_and_records_the_marker(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env()
    path = runs.record_path(vault, SLUG, EID, env)
    seen: dict[str, Any] = {}

    def fake_run(vault_: Path, slug: str, eid: str, argv: list[str], **kw: Any) -> runner.RunOutcome:
        rec = runs.read(path)
        assert rec is not None and rec.pid == os.getpid() and rec.outcome is None
        seen["alive"] = runs.alive(vault_, slug, eid, rec, env)
        refused: list[BaseException] = []

        def attached() -> None:  # an attached run of the same experiment, meanwhile
            try:
                with runner._run_lock(vault_, slug, eid, env):
                    pass
            except LabError as exc:
                refused.append(exc)

        t = threading.Thread(target=attached)
        t.start()
        t.join()
        seen["refused"] = [str(e) for e in refused]
        kw["on_started"]("/private/tmp/scout-lab-x/probe", "/outside")
        after = runs.read(path)
        seen["marker"] = None if after is None else (after.marker, after.outside)
        seen["timeout_s"], seen["limits"] = kw["timeout_s"], kw["max_total_bytes"]
        return runner.RunOutcome(0, "out\n", "err\n", 0.25, False)

    monkeypatch.setattr(runner, "run_experiment", fake_run)
    outcome = supervise.supervise(_inputs(vault, deadline_s=7200))
    assert seen["alive"] is True
    assert seen["refused"] == [f"experiment {SLUG} {EID} is already running"]
    assert seen["marker"] == ("/private/tmp/scout-lab-x/probe", "/outside")
    assert 7100 < seen["timeout_s"] <= 7200 and seen["limits"] == LIMITS.max_total_bytes
    assert outcome["status"] == "ran" and outcome["exit"] == 0 and outcome["stdout_tail"] == "out\n"
    rec = runs.read(path)
    assert rec is not None and rec.outcome == outcome and rec.marker == "/private/tmp/scout-lab-x/probe"
    assert not runs.lock_held(runs.lock_path(vault, SLUG, EID, env))  # let go once the outcome is written
    started, deadline = runs.parse_time(rec.started), runs.parse_time(rec.deadline)
    assert started is not None and deadline is not None and (deadline - started).total_seconds() == 7200


def test_a_supervisor_refused_the_lock_writes_no_record(vault: Path) -> None:
    env = _env()
    with runner._run_lock(vault, SLUG, EID, env), pytest.raises(LabError, match="already running"):
        supervise.supervise(_inputs(vault))
    assert runs.read(runs.record_path(vault, SLUG, EID, env)) is None


@pytest.mark.parametrize(
    ("stopped", "status", "timed_out"),
    [
        (supervise.STOPPED_BY_OWNER, "not run", False),
        (supervise.TERMINATED, "not run", False),
        (supervise.DEADLINE_PASSED, "ran", True),
    ],
)
def test_how_a_stop_reads(
    vault: Path, monkeypatch: pytest.MonkeyPatch, stopped: str, status: str, timed_out: bool
) -> None:
    out = runner.RunOutcome(-9, "", "partial\n", 0.0, False, stopped=stopped)
    monkeypatch.setattr(runner, "run_experiment", lambda *_a, **_k: out)
    outcome = supervise.supervise(_inputs(vault))
    assert (outcome["status"], outcome["timed_out"], outcome["stopped"]) == (status, timed_out, stopped)
    reply = broker.experiment_status(vault, SLUG, EID, _env(), datetime.now(UTC))
    if status == "not run":
        assert reply.stderr.rstrip("\n").splitlines()[-1] == f"experiment not run: {stopped}"
        assert reply.stderr.startswith("partial\n")  # the stopped script's output tail comes first
    else:
        assert reply.ran and reply.exit == -9


def _stopper(vault: Path, tmp_path: Path, **kw: Any) -> supervise.Stopper:
    args: dict[str, Any] = {
        "env": _env(),
        "slug": SLUG,
        "eid": EID,
        "deadline": NOW + timedelta(hours=1),
        "stop_file": tmp_path / "stop",
        "terminated": lambda: False,
        "now": lambda: NOW,
    }
    return supervise.Stopper(**{**args, **kw})


def test_veto_stops_a_running_experiment(vault: Path, tmp_path: Path) -> None:
    should_stop = _stopper(vault, tmp_path)
    assert should_stop() is None
    broker.record_veto(_env(), vault, SLUG, EID, NOW)
    assert should_stop() == "stopped by the owner"


def test_each_poll_refreshes_the_marker(vault: Path, tmp_path: Path) -> None:
    """Ruling P2-R9: macOS's /tmp cleanup removes files untouched for 3 days, so each poll touches the sweep's
    probe file and its decoy, and a long run's marker is never cleaned away under it."""
    marker = tmp_path / "probe"
    decoy = tmp_path / "probe-denied"
    for f in (marker, decoy):
        f.write_text("", encoding="utf-8")
        os.utime(f, (1_000_000, 1_000_000))
    should_stop = _stopper(vault, tmp_path)
    should_stop()  # before the gate opens: nothing to touch yet
    assert marker.stat().st_mtime == 1_000_000
    should_stop.marker = str(marker)
    begun = time.time()
    assert should_stop() is None
    for f in (marker, decoy):
        assert f.stat().st_mtime >= begun - 1 and f.stat().st_atime >= begun - 1


def test_on_started_hands_the_marker_to_the_poll(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(*_a: Any, **kw: Any) -> runner.RunOutcome:
        kw["on_started"]("/private/tmp/scout-lab-x/probe", "/outside")
        seen["marker"] = kw["should_stop"].marker
        return runner.RunOutcome(0, "", "", 0.0, False)

    monkeypatch.setattr(runner, "run_experiment", fake_run)
    supervise.supervise(_inputs(vault))
    assert seen["marker"] == "/private/tmp/scout-lab-x/probe"


def test_what_else_stops_it(vault: Path, tmp_path: Path) -> None:
    assert _stopper(vault, tmp_path, terminated=lambda: True)() == supervise.TERMINATED  # a logout's SIGTERM
    (tmp_path / "stop").write_text("{}", encoding="utf-8")  # `lab experiment stop`, then its SIGTERM
    assert _stopper(vault, tmp_path, terminated=lambda: True)() == supervise.STOPPED_BY_OWNER
    (tmp_path / "stop").unlink()
    assert _stopper(vault, tmp_path, now=lambda: NOW + timedelta(hours=2))() == supervise.DEADLINE_PASSED
    store = broker.veto_stores(_env())[0]
    store.parent.mkdir(parents=True, exist_ok=True)
    store.mkdir()  # unreadable as a file: a veto can't be ruled out
    assert _stopper(vault, tmp_path)() == supervise.VETO_UNREADABLE


def test_a_stop_before_the_script_starts_is_not_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_a: Any, **_k: Any) -> runner.RunOutcome:
        raise AssertionError("the script ran")

    monkeypatch.setattr(runner, "run_experiment", never)
    broker.record_veto(_env(), vault, SLUG, EID, NOW)
    outcome = supervise.supervise(_inputs(vault))
    assert outcome == {**outcome, "status": "not run", "reason": supervise.STOPPED_BY_OWNER}


# -- starting one ---------------------------------------------------------------------------------------------------

BLOCKED_FETCH = (
    "import sys, time\n"
    "from scout.lab import supervise\n"
    "supervise.fetch_weights = lambda inputs, should_stop: time.sleep(120)\n"
    "sys.exit(supervise.main(sys.argv[1:]))\n"
)


def _start(vault: Path, command: list[str] | None = None, **kw: Any) -> None:
    supervise.start(
        vault, SLUG, EID, ["./run.sh"], commit=_git(vault, "rev-parse", "HEAD").strip(), cap_usd=1.0,
        deadline_s=kw.pop("deadline_s", 3600), limits=LIMITS, env=_env(), command=command, **kw,
    )  # fmt: skip


def test_detached_start_does_not_wait_for_the_gate(vault: Path, tmp_path: Path, started_pids: list[int]) -> None:
    wrapper = tmp_path / "blocked.py"
    wrapper.write_text(BLOCKED_FETCH, encoding="utf-8")
    begun = time.monotonic()
    _start(vault, [sys.executable, str(wrapper)])
    assert time.monotonic() - begun < 30
    path = runs.record_path(vault, SLUG, EID, _env())
    rec = runs.read(path)
    assert rec is not None
    started_pids.append(rec.pid)
    assert rec.pid != os.getpid() and rec.marker is None and rec.outcome is None
    assert runs.alive(vault, SLUG, EID, rec, _env())
    assert not runs.inputs_path(path).exists()  # the supervisor removed its inputs once read
    assert os.getsid(rec.pid) == rec.pid  # a session of its own
    with pytest.raises(LabError, match="already running"):  # a second detached start of it, meanwhile
        _start(vault, [sys.executable, str(wrapper)])


def test_the_inputs_hold_no_more_of_the_env_than_the_runner_needs(vault: Path, tmp_path: Path) -> None:
    seen = tmp_path / "seen.json"
    peek = (
        "import json, os, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps("
        "{'text': open(sys.argv[1]).read(), 'mode': os.stat(sys.argv[1]).st_mode & 0o777}))\n"
        "sys.exit(3)\n"
    )
    script = tmp_path / "peek.py"
    script.write_text(peek, encoding="utf-8")
    env = {**_env(), "SECRET_TOKEN": "s3cr3t"}
    with pytest.raises(LabError, match="stopped before it started"):
        supervise.start(
            vault, SLUG, EID, ["./run.sh"], commit=None, cap_usd=1.0, deadline_s=60, limits=LIMITS, env=env,
            command=[sys.executable, str(script)],
        )  # fmt: skip
    import json

    peeked = json.loads(seen.read_text(encoding="utf-8"))
    assert peeked["mode"] == 0o600
    inputs = json.loads(peeked["text"])
    assert "s3cr3t" not in peeked["text"] and set(inputs["env"]) <= set(supervise.ENV_KEYS)
    assert inputs["env"]["HOME"] == os.environ["HOME"]
    assert not runs.inputs_path(runs.record_path(vault, SLUG, EID, _env())).exists()


@pytest.mark.parametrize("gpu", [True, False])
def test_the_owners_gpu_switch_reaches_the_runner_detached(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gpu: bool
) -> None:
    """lab.gpu goes into the supervisor's inputs file, survives the round trip, and is what it runs the profile with;
    inputs that don't say (an older supervisor's) are off."""
    import json
    from dataclasses import replace

    seen = tmp_path / "seen.json"
    script = tmp_path / "peek.py"
    script.write_text(f"import shutil, sys\nshutil.copy(sys.argv[1], {str(seen)!r})\nsys.exit(3)\n", encoding="utf-8")
    with pytest.raises(LabError, match="stopped before it started"):
        supervise.start(
            vault, SLUG, EID, ["./run.sh"], commit=None, cap_usd=1.0, deadline_s=60, limits=replace(LIMITS, gpu=gpu),
            env=_env(), command=[sys.executable, str(script)],
        )  # fmt: skip
    data = json.loads(seen.read_text(encoding="utf-8"))
    assert data["gpu"] is gpu
    inputs = supervise.Inputs.from_dict(data)
    assert inputs.gpu is gpu
    assert supervise.Inputs.from_dict({k: v for k, v in data.items() if k != "gpu"}).gpu is False
    assert supervise.Inputs.from_dict({**data, "gpu": "yes"}).gpu is False  # only JSON true turns it on
    got: dict[str, Any] = {}

    def fake_run(*_a: Any, **kw: Any) -> runner.RunOutcome:
        got.update(kw)
        return runner.RunOutcome(0, "", "", 0.0, False)

    monkeypatch.setattr(runner, "run_experiment", fake_run)
    assert supervise.supervise(inputs)["status"] == "ran"
    assert got["gpu"] is gpu


def test_start_reports_a_supervisor_that_exits_first(vault: Path) -> None:
    command = [sys.executable, "-c", "import sys; print('supervise: no luck here', file=sys.stderr); sys.exit(1)"]
    with pytest.raises(LabError, match="stopped before it started: no luck here"):
        _start(vault, command)


def test_start_gives_up_after_its_wait(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervise, "START_WAIT_S", 1.0)
    begun = time.monotonic()
    with pytest.raises(LabError, match="wrote no record within"):
        _start(vault, [sys.executable, "-c", "import time; time.sleep(30)"])
    assert time.monotonic() - begun < 10


def test_start_refuses_over_an_uncollected_run(vault: Path) -> None:
    path = runs.record_path(vault, SLUG, EID, _env())
    runs.write(path, runs.RunRecord(T.isoformat(), T.isoformat(), 1))  # no outcome, nothing holds the lock
    with pytest.raises(LabError, match="stopped without a result.*collect"):
        _start(vault, [sys.executable, "-c", "raise SystemExit(1)"])


# -- against the kernel -------------------------------------------------------------------------------------------


def _detached_vault(vault: Path, name: str, body: str) -> None:
    _commit_script(vault, name, body)
    _owner_config({"lab": {"detached_max_hours": 1, "max_detached": 1, "disk_free_floor_bytes": 0}})


@kernel
def test_kernel_hooks_report_the_marker_and_stop_the_run(vault: Path) -> None:
    _commit_script(vault, "slow.sh", "#!/bin/sh\nsleep 30\n")
    commit = _git(vault, "rev-parse", "HEAD").strip()
    started: list[tuple[str, str]] = []
    out = runner.run_experiment(
        vault, SLUG, EID, ["./slow.sh"], cap_usd=1.0, timeout_s=60, env=_env(), commit=commit,
        disk_free_floor_bytes=0, on_started=lambda marker, outside: started.append((marker, outside)),
        should_stop=lambda: "stopped by the owner" if started else None,
    )  # fmt: skip
    assert out.stopped == "stopped by the owner" and not out.timed_out and out.exit != 0
    [(marker, outside)] = started
    assert marker.endswith("/probe") and outside == str(paths.experiment_dir(_note(vault)).parent.resolve())
    log = next(paths.experiment_dir(_note(vault)).glob("run-*.log")).read_text(encoding="utf-8")
    assert "detached: true" in log and "stopped: stopped by the owner" in log and "deadline: " in log


@kernel
def test_kernel_detached_run_outlives_its_broker(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], started_pids: list[int]
) -> None:
    _detached_vault(vault, "detached.sh", "#!/bin/sh\nsleep 3; echo done > out.txt\n")
    import shutil
    import tempfile

    short = Path(tempfile.mkdtemp(prefix="sl-", dir="/tmp"))  # a unix socket's path must be short
    broker_dir = short / "scout-broker.t1"
    broker_dir.mkdir(mode=0o700)
    serve = "import sys; from pathlib import Path; from scout.sandbox import broker\n"
    serve += "sys.exit(broker.serve(Path(sys.argv[1]), Path(sys.argv[2])))\n"
    server = subprocess.Popen(
        [sys.executable, "-c", serve, str(vault), str(broker_dir)],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=(tmp_path / "broker.log").open("wb"),
    )  # fmt: skip
    try:
        sock = broker_dir / broker.SOCKET_NAME
        _wait(sock.exists, 30, "the broker's socket")
        env = {**os.environ, broker.ENV_SOCKET: str(sock), "SCOUT_DATA_DIR": str(vault)}
        assert broker.client_experiment(SLUG, EID, ["./detached.sh"], env=env, detach=True) == 0
        assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == "experiment started: detached"
        path = runs.record_path(vault, SLUG, EID, _env())
        rec = runs.read(path)
        assert rec is not None
        started_pids.append(rec.pid)
    finally:
        alive_then = server.poll() is None
        server.kill()  # the broker, this test's child, stops (the supervisor has a session of its own)
        server.wait(timeout=10)
        shutil.rmtree(short, ignore_errors=True)
    assert alive_then, (tmp_path / "broker.log").read_text(encoding="utf-8", errors="replace")
    time.sleep(5)
    _wait(lambda: (r := runs.read(path)) is not None and r.outcome is not None, 60, "the outcome")
    owner = {**_env(), "SCOUT_DATA_DIR": str(vault)}
    assert broker.client_collect(SLUG, EID, env=owner) == 0
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == "experiment ran: exit 0"
    assert (paths.experiment_dir(_note(vault)) / "out.txt").read_text(encoding="utf-8") == "done\n"
    log = next(paths.experiment_dir(_note(vault)).glob("run-*.log")).read_text(encoding="utf-8")
    assert "detached: true" in log


@kernel
def test_kernel_killed_supervisor_leaves_nothing(
    vault: Path, capsys: pytest.CaptureFixture[str], started_pids: list[int]
) -> None:
    _detached_vault(vault, "long.sh", "#!/bin/sh\nsleep 60\n")
    b = broker.Broker.for_vault(vault, dict(os.environ), clock=lambda: NOW)
    reply = b.handle({**REQ, "argv": ["./long.sh"], "detach": True})
    assert reply.exit == 0, reply.stderr
    path = runs.record_path(vault, SLUG, EID, _env())
    first = runs.read(path)
    assert first is not None
    started_pids.append(first.pid)
    _wait(lambda: (r := runs.read(path)) is not None and r.marker is not None, 60, "the gate's checks")
    rec = runs.read(path)
    assert rec is not None and rec.marker is not None and rec.outside is not None
    probe = sandbox.SandboxProbe()
    _wait(lambda: bool(runner._holders(probe, rec.marker or "", rec.outside or "")), 30, "the script")
    script = runner._holders(probe, rec.marker, rec.outside)
    os.kill(rec.pid, signal.SIGKILL)  # the supervisor this test started
    _wait(lambda: _gone(rec.pid), 30, "the supervisor to go")
    assert all(not _gone(pid) for pid in script)  # its script outlived it
    owner = {**_env(), "SCOUT_DATA_DIR": str(vault)}
    assert broker.client_collect(SLUG, EID, env=owner) == broker.EXIT_NOT_RUN
    last = capsys.readouterr().err.rstrip("\n").splitlines()[-1]
    assert last == "experiment not run: the supervisor stopped without a result"
    _wait(lambda: all(_gone(pid) for pid in script), 10, "the script's processes to go")
    after = runs.read(path)
    assert after is not None and after.outcome is not None and after.outcome["swept"] == "clean"
    assert not Path(rec.marker).parent.exists()  # the supervisor's private temp folder is gone too

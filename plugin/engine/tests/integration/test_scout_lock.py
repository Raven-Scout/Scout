"""scripts/scout-lock.sh — the shared session lock: wait, skip rows, redundancy (#218).

Generation 1: a runner that finds the lock busy waits for it (polling, up to
``SCOUT_LOCK_WAIT_SECS``) instead of exiting at once, because the dispatcher has
already stamped the slot as fired. A wait that runs out records ``skipped_lock``.

Generation 2: once it has the lock, a runner looks in the run ledger for a
witness, an ``ok`` run of its own kind that finished in the last
``SCOUT_REDUNDANT_WINDOW_MIN`` minutes. A dreaming or research run that waited,
whose witness finished during that wait, would redo the holder's work, so it
records ``skipped_redundant``. Every other case logs a note and runs: the
negative controls below (a failed holder, another kind, outside the window, a
``scout`` run, the escape hatch, an unwaited serial run) are the runs that must
not be cancelled.

The real runner, ``scout-lock.sh`` and ``run-outcome.sh`` templates are rendered
into a temporary vault; the session body (``claude-with-retry.sh``) is a stub.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = {"run-scout.sh.tmpl": "scout", "run-dreaming.sh.tmpl": "dreaming", "run-research.sh.tmpl": "research"}
RUN_TIMEOUT_S = 60
SLOT = "dreaming-evening"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _render(tmpl: Path, out: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached — the retry wrapper is stubbed
        "{{SCOUTCTL_BIN}}": str(scout_dir / "bin" / "scoutctl"),
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text
    return _script(out, text)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    _script(v / "scripts" / "claude-with-retry.sh", f'#!/bin/bash\ntouch "{v}/session-ran"\n')
    return v


def _lock(vault: Path) -> Path:
    return vault / ".scout-logs" / ".scout-session.lock"


def _ledger_path(vault: Path) -> Path:
    return vault / ".scout-logs" / "run-outcomes.jsonl"


def _ledger(vault: Path) -> list[dict]:
    path = _ledger_path(vault)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _witness_row(*, runner: str | None, finished_at: int, outcome: str = "ok", code: int = 0) -> str:
    row = {
        "slot": "dreaming-manual",
        "mode": "dreaming-manual",
        "started_at": finished_at - 600,
        "finished_at": finished_at,
        "exit_code": code,
        "failure_class": "ok" if code == 0 else "unknown",
        "outcome": outcome,
        "log": "dreaming-holder.log",
    }
    if runner is not None:
        row["runner"] = runner
    return json.dumps(row)


def _write_row(vault: Path, row: str) -> None:
    with _ledger_path(vault).open("a", encoding="utf-8") as fh:
        fh.write(row + "\n")


def _holder(vault: Path, *, hold_s: float, row: str | None = None, release: bool = True) -> int:
    """Start a live session holding the lock for ``hold_s``, and return its PID.

    On its way out it appends ``row`` (``"finished_at": 0`` becomes its finish
    time) and, if ``release``, removes the lock; without ``release`` it dies
    holding it, like a crashed session. It runs detached, so its exit is reaped
    at once: an unreaped child would stay a zombie that ``kill -0`` calls alive.
    """
    lines = [f"sleep {hold_s}"]
    if row is not None:
        template = row.replace('"finished_at": 0', '"finished_at": FINISHED')
        lines.append(f"printf '%s\\n' '{template}' | sed \"s/FINISHED/$(date +%s)/\" >> '{_ledger_path(vault)}'")
    if release:
        lines.append(f"rm -f '{_lock(vault)}'")
    body = "\n".join(lines)
    out = subprocess.run(
        ["bash", "-c", f"bash -c {shlex.quote(body)} </dev/null >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    pid = int(out.stdout.strip())
    _lock(vault).write_text(f"{pid}\n", encoding="utf-8")
    return pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int) -> None:
    deadline = time.monotonic() + 10
    while _alive(pid):
        assert time.monotonic() < deadline, f"holder {pid} never exited"
        time.sleep(0.05)


def _run(vault: Path, runner: str, **env: str) -> subprocess.CompletedProcess[str]:
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    return subprocess.run(
        [str(script)],
        env={
            **os.environ,
            "SCOUT_FORCE_MODE": SLOT,
            "SCOUT_LOCK_POLL_SECS": "1",
            "SCOUT_LOCK_WAIT_SECS": "30",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _run_log(vault: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((vault / ".scout-logs").glob("*.log")))


def _own_rows(vault: Path) -> list[dict]:
    """The rows this runner wrote (the holder's rows name the holder's log)."""
    return [r for r in _ledger(vault) if r.get("log") != "dreaming-holder.log"]


# --- generation 1: wait, don't skip -------------------------------------------


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_busy_lock_is_waited_for_and_then_run(vault: Path, runner: str) -> None:
    holder = _holder(vault, hold_s=4)

    result = _run(vault, runner)

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists(), "a waiter must run once the holder is gone"
    log = _run_log(vault)
    assert "waiting up to 30s for the lock" in log
    assert "Lock acquired after" in log
    (row,) = _own_rows(vault)
    assert (row["outcome"], row["runner"]) == ("ok", RUNNERS[runner])
    assert not _lock(vault).exists(), "the waiter releases the lock it finally took"


def test_a_wait_that_runs_out_records_skipped_lock(vault: Path) -> None:
    holder = _holder(vault, hold_s=60)
    try:
        started = time.monotonic()

        result = _run(vault, "run-dreaming.sh.tmpl", SCOUT_LOCK_WAIT_SECS="3")

        assert result.returncode == 0, result.stderr
        # The wait counts whole seconds (`date +%s`), so it can end up to 1 s short.
        assert time.monotonic() - started >= 2, "the runner gave up before its wait ran out"
        assert not (vault / "session-ran").exists()
        (row,) = _ledger(vault)
        assert (row["outcome"], row["slot"]) == ("skipped_lock", SLOT)
        assert f"Another Scout session running (PID {holder}) — skipping after a" in _run_log(vault)
        assert _lock(vault).read_text(encoding="utf-8").strip() == str(holder)
    finally:
        os.kill(holder, signal.SIGKILL)


def test_a_holder_that_crashes_during_the_wait_is_recovered(vault: Path) -> None:
    """Every poll is a full try, so a lock left by a holder that died mid-wait
    is recovered as stale rather than waited on until the timeout."""
    holder = _holder(vault, hold_s=1, release=False)

    result = _run(vault, "run-dreaming.sh.tmpl", SCOUT_LOCK_WAIT_SECS="20")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists()
    assert not _lock(vault).exists()


@pytest.mark.parametrize("bogus", ["", "soon", "-5"])
def test_an_unreadable_wait_falls_back_to_the_default(vault: Path, bogus: str) -> None:
    holder = _holder(vault, hold_s=4)

    result = _run(vault, "run-dreaming.sh.tmpl", SCOUT_LOCK_WAIT_SECS=bogus)

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert "waiting up to 1800s for the lock" in _run_log(vault)


# --- generation 2: the redundant waiter ----------------------------------------


@pytest.mark.parametrize("runner", ["run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
def test_a_waiter_behind_a_same_kind_run_that_finished_ok_is_skipped(vault: Path, runner: str) -> None:
    """The #218 shape: the holder did this run's work while it waited."""
    kind = RUNNERS[runner]
    holder = _holder(vault, hold_s=4, row=_witness_row(runner=kind, finished_at=0))

    result = _run(vault, runner)

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert not (vault / "session-ran").exists(), "the waiter re-ran the holder's session"
    (row,) = _own_rows(vault)
    assert (row["outcome"], row["slot"], row["exit_code"]) == ("skipped_redundant", SLOT, 0)
    assert f"Redundant run: a {kind} run (slot dreaming-manual, log dreaming-holder.log)" in _run_log(vault)
    assert not _lock(vault).exists(), "the skip releases the lock it took"


@pytest.mark.parametrize(
    ("case", "row"),
    [
        ("failed holder", _witness_row(runner="dreaming", finished_at=0, outcome="failed", code=1)),
        ("other kind", _witness_row(runner="research", finished_at=0)),
        ("row without a runner", _witness_row(runner=None, finished_at=0)),
    ],
)
def test_a_waiter_behind_a_run_that_did_not_do_its_work_runs(vault: Path, case: str, row: str) -> None:
    """A waiter behind a failed holder is the recovery the wait exists for."""
    holder = _holder(vault, hold_s=4, row=row)

    result = _run(vault, "run-dreaming.sh.tmpl")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists(), case
    assert _own_rows(vault)[-1]["outcome"] == "ok"
    assert "Redundant run" not in _run_log(vault)


def test_a_witness_outside_the_window_is_ignored(vault: Path) -> None:
    _write_row(vault, _witness_row(runner="dreaming", finished_at=int(time.time()) - 2 * 3600))
    holder = _holder(vault, hold_s=4)

    result = _run(vault, "run-dreaming.sh.tmpl")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists()
    assert "finished ok" not in _run_log(vault)


def test_a_witness_that_finished_before_the_wait_does_not_skip(vault: Path) -> None:
    """Waited, but behind some other holder: the witness is a serial run, so note and run."""
    _write_row(vault, _witness_row(runner="dreaming", finished_at=int(time.time()) - 300))
    holder = _holder(vault, hold_s=4)

    result = _run(vault, "run-dreaming.sh.tmpl")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists()
    assert "Note: a dreaming run (slot dreaming-manual, log dreaming-holder.log) finished ok" in _run_log(vault)


def test_a_scout_waiter_behind_a_scout_run_only_warns(vault: Path) -> None:
    """run-scout.sh carries briefing and consolidation, which are different work."""
    holder = _holder(vault, hold_s=4, row=_witness_row(runner="scout", finished_at=0))

    result = _run(vault, "run-scout.sh.tmpl")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists()
    assert "Note: a scout run" in _run_log(vault)


def test_an_unwaited_serial_run_is_noted_and_runs(vault: Path) -> None:
    """A back-to-back run never touches the lock and is sometimes the only reader
    of the last run's blind spot (#218, second comment): never skipped."""
    _write_row(vault, _witness_row(runner="dreaming", finished_at=int(time.time()) - 60))

    result = _run(vault, "run-dreaming.sh.tmpl")

    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").exists()
    log = _run_log(vault)
    assert "Note: a dreaming run" in log and "running anyway" in log
    assert "waiting up to" not in log


@pytest.mark.parametrize(("policy", "noted"), [("warn", True), ("off", False)])
def test_the_escape_hatch_keeps_a_redundant_waiter_running(vault: Path, policy: str, noted: bool) -> None:
    holder = _holder(vault, hold_s=4, row=_witness_row(runner="dreaming", finished_at=0))

    result = _run(vault, "run-dreaming.sh.tmpl", SCOUT_REDUNDANT_WAIT=policy)

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert (vault / "session-ran").exists()
    assert ("Note: a dreaming run" in _run_log(vault)) is noted


def test_enforce_skips_a_redundant_scout_waiter_too(vault: Path) -> None:
    holder = _holder(vault, hold_s=4, row=_witness_row(runner="scout", finished_at=0))

    result = _run(vault, "run-scout.sh.tmpl", SCOUT_REDUNDANT_WAIT="enforce")

    assert result.returncode == 0, result.stderr
    _gone(holder)
    assert not (vault / "session-ran").exists()
    assert _own_rows(vault)[-1]["outcome"] == "skipped_redundant"


def test_a_narrower_window_excludes_an_older_witness(vault: Path) -> None:
    _write_row(vault, _witness_row(runner="dreaming", finished_at=int(time.time()) - 300))

    result = _run(vault, "run-dreaming.sh.tmpl", SCOUT_REDUNDANT_WINDOW_MIN="1")

    assert result.returncode == 0, result.stderr
    assert "finished ok" not in _run_log(vault)


def test_a_malformed_ledger_never_blocks_a_run(vault: Path) -> None:
    _ledger_path(vault).write_text('not json\n[1, 2]\n{"finished_at": "soon", "runner": "dreaming"}\n')

    result = _run(vault, "run-dreaming.sh.tmpl")

    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").exists()


# --- the shared file -----------------------------------------------------------


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_runner_without_scout_lock_fails_loudly_and_runs_nothing(vault: Path, runner: str) -> None:
    (vault / "scripts" / "scout-lock.sh").unlink()

    result = _run(vault, runner)

    assert result.returncode == 1
    assert not (vault / "session-ran").exists()
    assert "scout-lock.sh missing" in (vault / ".scout-logs" / "failures.log").read_text(encoding="utf-8")
    (row,) = _ledger(vault)
    assert row["outcome"] == "failed"


def _lock_block(runner: str) -> str:
    text = (TEMPLATES / runner).read_text(encoding="utf-8")
    start = text.index("# Concurrency guard")
    end = text.index("scout_record_skip skipped_redundant", start)
    return text[start:end]


def test_all_runners_source_one_lock_and_carry_no_copy_of_it() -> None:
    blocks = {runner: _lock_block(runner) for runner in RUNNERS}
    assert len(set(blocks.values())) == 1, "the runners' lock blocks have drifted apart"
    for runner in RUNNERS:
        text = (TEMPLATES / runner).read_text(encoding="utf-8")
        assert f"RUNNER_KIND={RUNNERS[runner]}\n" in text
        assert "set -C" not in text, f"{runner} carries its own copy of the lock protocol"

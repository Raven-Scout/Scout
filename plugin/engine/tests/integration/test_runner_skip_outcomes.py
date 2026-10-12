"""A deliberate runner skip records a run outcome (#401).

A runner that finds the session lock held by a live session, or whose budget
check says to skip, logs one line and exits 0. Before #401 it exited before
``scripts/run-outcome.sh record``, so the slot had fired with no ledger row and
``scoutctl health`` reported ``slot.fired_silent:<slot>`` for a run that was
skipped on purpose. These tests render the real runner and ``run-outcome.sh``
templates into a temporary vault and check that each skip leaves a
``skipped_lock`` / ``skipped_budget`` row that health reads as covering the fire.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")
RUN_TIMEOUT_S = 30
SLOT = "morning-briefing"

SCHEDULE = f"""\
schema_version: 1
slots:
  {SLOT}:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0
"""


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
    # health notify is a no-op stand-in; the fake notifiers keep the
    # engine-unreachable floor from ever reaching a real desktop.
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")
    for name in ("osascript", "notify-send"):
        _script(tmp_path / "fakebin" / name, "#!/bin/bash\ncat > /dev/null\n")
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    # The session body must never run on a skip path.
    _script(v / "scripts" / "claude-with-retry.sh", f'#!/bin/bash\ntouch "{v}/session-ran"\n')
    return v


def _run(vault: Path, runner: str, **env: str) -> subprocess.CompletedProcess[str]:
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    fake_bin = vault.parent / "fakebin"
    return subprocess.run(
        [str(script)],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", **env},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _ledger(vault: Path) -> list[dict]:
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def live_holder(vault: Path):
    """A live process holding the session lock, as another session would."""
    holder = subprocess.Popen(["sleep", "60"])
    (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{holder.pid}\n", encoding="utf-8")
    yield holder
    holder.kill()
    holder.wait()


def _over_budget(vault: Path) -> None:
    _script(vault / "scripts" / "budget-check.sh", "#!/bin/bash\necho 'over budget'\nexit 1\n")


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_lock_skip_records_a_skipped_lock_row(vault: Path, runner: str, live_holder: subprocess.Popen) -> None:
    before = int(time.time())

    # No wait: a wait that runs out is the same skip (test_scout_lock.py).
    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_LOCK_WAIT_SECS="0")

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "skipped_lock"
    assert row["slot"] == SLOT
    assert row["exit_code"] == 0
    assert before <= row["started_at"] <= row["finished_at"]
    # The holder's lock is untouched.
    assert (vault / ".scout-logs" / ".scout-session.lock").read_text(encoding="utf-8").strip() == str(live_holder.pid)


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_budget_skip_records_a_skipped_budget_row(vault: Path, runner: str) -> None:
    _over_budget(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "skipped_budget"
    assert row["slot"] == SLOT
    assert row["exit_code"] == 0
    # The budget skip releases the lock it took.
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_manual_skip_is_recorded_under_the_runner_default_mode(vault: Path, runner: str) -> None:
    _over_budget(vault)
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_FORCE_MODE"}

    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    subprocess.run(
        [str(script)],
        env={**env, "PATH": f"{vault.parent / 'fakebin'}{os.pathsep}{env['PATH']}"},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=True,
    )

    (row,) = _ledger(vault)
    expected = {
        "run-scout.sh.tmpl": "manual",
        "run-dreaming.sh.tmpl": "dreaming-manual",
        "run-research.sh.tmpl": "research-manual",
    }[runner]
    assert (row["slot"], row["mode"], row["outcome"]) == (expected, expected, "skipped_budget")


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_run_that_was_not_skipped_records_no_skip(vault: Path, runner: str) -> None:
    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "ok"
    assert row["runner"] == {"run-scout.sh.tmpl": "scout", "run-dreaming.sh.tmpl": "dreaming"}.get(runner, "research")


# ----- the health reading of a skip --------------------------------------------


def _health_vault(vault: Path, monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    state = vault / ".scout-state"
    state.mkdir(exist_ok=True)
    (state / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    old = (now - timedelta(days=60)).timestamp()
    os.utime(state / "schedule.yaml", (old, old))
    monkeypatch.setattr(health, "vault_birth", lambda _v: now - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True, raising=False)
    tick = {
        "id": "x",
        "ts": health.iso_z(now - timedelta(minutes=5)),
        "kind": "schedule.tick.completed",
        "source": "t",
        "payload": {},
    }
    (vault / ".scout-logs" / f"schedule-events-{now.date().isoformat()}.jsonl").write_text(json.dumps(tick) + "\n")
    # An earlier, ordinary run of the slot: the ledger predates the fire.
    earlier = int((now - timedelta(days=2)).timestamp())
    row = {
        "slot": SLOT,
        "mode": SLOT,
        "started_at": earlier,
        "finished_at": earlier + 60,
        "exit_code": 0,
        "failure_class": "ok",
        "log": None,
    }
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    path.write_text(json.dumps(row) + "\n" + (path.read_text(encoding="utf-8") if path.exists() else ""))


def _fire(vault: Path, at: datetime) -> None:
    (vault / ".scout-state" / "last-fire.json").write_text(
        json.dumps({"schema_version": 1, "last_fire": {SLOT: health.iso_z(at)}}), encoding="utf-8"
    )


def test_health_does_not_report_a_budget_skipped_fire_as_silent(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fired = datetime.now(UTC).replace(microsecond=0)
    _over_budget(vault)
    assert _run(vault, "run-scout.sh.tmpl", SCOUT_FORCE_MODE=SLOT).returncode == 0
    now = fired + timedelta(hours=5)
    _health_vault(vault, monkeypatch, now)
    _fire(vault, fired)

    ids = {f.id for f in health.evaluate(vault, now=now).findings}

    assert f"slot.fired_silent:{SLOT}" not in ids
    (status,) = health.evaluate(vault, now=now).slots
    assert status.last_outcome is not None and status.last_outcome.outcome == "skipped_budget"


def test_health_still_reports_a_fire_that_recorded_nothing(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative control: no skip row, no run row — still fired_silent."""
    fired = datetime.now(UTC).replace(microsecond=0)
    now = fired + timedelta(hours=5)
    _health_vault(vault, monkeypatch, now)
    _fire(vault, fired)

    ids = {f.id for f in health.evaluate(vault, now=now).findings}

    assert f"slot.fired_silent:{SLOT}" in ids

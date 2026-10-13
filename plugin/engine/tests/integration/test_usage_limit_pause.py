"""A weekly usage limit pauses Scout until the date it names (#424).

Claude Code ends a run that hits the weekly limit with "You've hit your weekly
limit · resets Oct 15 at 2pm (Europe/Prague)". Before #424 the date wasn't read,
so no ``resets_at`` was recorded: the budget gate fell back to its short
rate-limit backoff, every slot until the reset started a session that failed in
seconds, and run health went red for days.

These tests render the real runner, ``scout-lock.sh``, ``rate-limit-detect.sh``, ``run-outcome.sh``
and ``budget-check.sh`` into a temporary vault, run them with this checkout's
engine for ``scoutctl budget``, and stub only the Claude session (which prints the
weekly-limit message) and every other ``scoutctl`` call (so no notification is
ever sent).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
RUN_TIMEOUT_S = 60
SLOT = "morning-briefing"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Runs this checkout's engine for `scoutctl budget …`; records every other call.
SCOUTCTL_STUB = """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:2] == ["budget"]:
    os.execv({python!r}, [{python!r}, "-m", "scout.cli", *sys.argv[1:]])
with open({calls!r}, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
"""

# The session: count it, print the weekly-limit message into the run log, fail.
SESSION_STUB = """#!/bin/bash
echo x >> "{sessions}"
echo "{message}" >> "$1"
exit 1
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _render(tmpl: Path, out: Path, vault: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(vault),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached: the retry wrapper is stubbed
        "{{SCOUTCTL_BIN}}": str(vault / "bin" / "scoutctl"),
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text
    return _script(out, text)


def _weekly_message(lifts: datetime) -> str:
    hour = lifts.hour % 12 or 12
    ampm = "am" if lifts.hour < 12 else "pm"
    return f"You've hit your weekly limit · resets {MONTHS[lifts.month - 1]} {lifts.day} at {hour}{ampm} (UTC)"


@pytest.fixture
def lifts() -> datetime:
    """Three days ahead, on the hour: the date matters, not just the time."""
    return (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def vault(tmp_path: Path, lifts: datetime) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    _script(
        v / "bin" / "scoutctl",
        SCOUTCTL_STUB.format(python=sys.executable, calls=str(tmp_path / "scoutctl-calls.jsonl")),
    )
    for name in ("osascript", "notify-send"):  # the engine-unreachable floor never reaches a desktop
        _script(tmp_path / "fakebin" / name, "#!/bin/bash\ncat > /dev/null\n")
    for name in ("run-outcome.sh", "rate-limit-detect.sh", "budget-check.sh", "scout-lock.sh"):
        _render(TEMPLATES / "scripts" / f"{name}.tmpl", v / "scripts" / name, v)
    _script(
        v / "scripts" / "claude-with-retry.sh",
        SESSION_STUB.format(sessions=tmp_path / "sessions", message=_weekly_message(lifts)),
    )
    return v


def _run_slot(vault: Path) -> None:
    runner = _render(TEMPLATES / "run-scout.sh.tmpl", vault / "run-scout.sh", vault)
    fake_bin = vault.parent / "fakebin"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    result = subprocess.run(
        [str(runner)],
        env={**env, "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}", "SCOUT_FORCE_MODE": SLOT},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sessions(vault: Path) -> int:
    path = vault.parent / "sessions"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def test_a_weekly_limit_holds_every_slot_until_its_date(vault: Path, lifts: datetime) -> None:
    _run_slot(vault)  # the session runs and fails on the weekly limit

    (tracked,) = _jsonl(vault / ".scout-logs" / "usage-tracker.jsonl")
    assert tracked["type"] == "rate_limit"
    assert tracked["resets_at"] == health.iso_z(lifts)
    (failed,) = _jsonl(vault / ".scout-logs" / "run-outcomes.jsonl")
    assert (failed["outcome"], failed["failure_class"]) == ("failed", "rate_limited")
    assert failed["resets_at"] == int(lifts.timestamp())

    for _ in range(3):  # later slots, all before the reset
        _run_slot(vault)

    assert _sessions(vault) == 1  # no session started after the first
    rows = _jsonl(vault / ".scout-logs" / "run-outcomes.jsonl")
    assert [r["outcome"] for r in rows] == ["failed", "skipped_budget", "skipped_budget", "skipped_budget"]
    run_logs = sorted((vault / ".scout-logs").glob("scout-*.log"))
    assert any(f"backing off until {health.iso_z(lifts)}" in p.read_text(encoding="utf-8") for p in run_logs)


def test_health_reads_the_held_slots_as_one_pause(vault: Path, lifts: datetime) -> None:
    for _ in range(4):
        _run_slot(vault)

    report = health.evaluate(vault, now=datetime.now(UTC))

    assert report.severity == "yellow"
    assert [(f.id, f.severity) for f in report.findings] == [(health.PAUSED_FINDING_ID, "yellow")]
    assert f"runs paused until {health.iso_z(lifts)}" in report.findings[0].summary


def test_the_slot_after_the_reset_runs_again(vault: Path, lifts: datetime) -> None:
    """The pause ends on its own: once the reset has passed, the gate lets a session start."""
    _run_slot(vault)
    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    (row,) = _jsonl(tracker)
    past = datetime.now(UTC) - timedelta(days=4)
    row.update(ts=health.iso_z(past - timedelta(hours=1)), resets_at=health.iso_z(past))
    tracker.write_text(json.dumps(row) + "\n", encoding="utf-8")

    _run_slot(vault)

    assert _sessions(vault) == 2

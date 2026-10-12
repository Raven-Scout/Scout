"""templates/scripts/rate-limit-detect.sh — decides whether later runs back off.

After a failed session the runner calls ``scripts/rate-limit-detect.sh <log>
--write-tracker``: exit 0 means the log shows a rate limit, and the
``rate_limit`` row it appends to ``usage-tracker.jsonl`` makes the budget gate
hold later slots (until ``resets_at`` when the log names the reset, #407); exit
1 means it doesn't. (The in-run retry wrapper keeps its own transient list.) Its
pattern list must agree with ``RATE_LIMIT_RE`` in ``run-outcome.sh``, which only
labels the failure; the parity test below runs both real scripts on the same logs.

The templates are rendered into a temporary vault and run by the real shell.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

SCRIPTS = Path(__file__).resolve().parents[3] / "templates" / "scripts"


def _install(vault: Path, name: str, **values: str) -> Path:
    values = {"SCOUT_DIR": str(vault), "SCOUTCTL_BIN": str(vault / "bin" / "scoutctl"), **values}
    text = render_template((SCRIPTS / f"{name}.tmpl").read_text(encoding="utf-8"), values)
    assert "{{" not in text
    path = vault / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _log(vault: Path, text: str) -> Path:
    log = vault / ".scout-logs" / "scout-2026-01-05_08-00.log"
    log.write_text(text, encoding="utf-8")
    return log


RATE_LIMITED = [
    pytest.param("You've hit your session limit · resets 3pm\n", id="session-limit"),
    pytest.param("Weekly limit reached · resets Mon 9am\n", id="limit-resets-rewording"),
    pytest.param("You have hit your usage limit\n", id="usage-limit"),
    pytest.param("API Error: 429 Too Many Requests\n", id="http-429"),
]


@pytest.mark.parametrize("log_text", RATE_LIMITED)
def test_a_rate_limited_log_is_detected(vault: Path, log_text: str) -> None:
    detect = _install(vault, "rate-limit-detect.sh")

    result = subprocess.run([str(detect), str(_log(vault, log_text))], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr


def test_an_ordinary_failure_is_not_a_rate_limit(vault: Path) -> None:
    detect = _install(vault, "rate-limit-detect.sh")

    result = subprocess.run(
        [str(detect), str(_log(vault, "Traceback: KeyError 'title'\n"))], capture_output=True, text=True, check=False
    )

    assert result.returncode == 1


@pytest.mark.parametrize("log_text", RATE_LIMITED)
def test_run_outcome_labels_what_the_detector_retries(vault: Path, tmp_path: Path, log_text: str) -> None:
    """The two pattern lists stay in sync: every log the retry detector treats as a
    rate limit, run-outcome labels `rate_limited` (and not `unknown`)."""
    stub = tmp_path / "scoutctl"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    outcome = _install(vault, "run-outcome.sh", SCOUTCTL_BIN=str(stub), INSTANCE_NAME="Scout")
    detect = _install(vault, "rate-limit-detect.sh")
    log = _log(vault, log_text)

    assert subprocess.run([str(detect), str(log)], check=False).returncode == 0
    subprocess.run([str(outcome), "record", "dreaming-nightly", "1", "0", str(log)], check=True, capture_output=True)

    rows = [json.loads(line) for line in (vault / ".scout-logs" / "run-outcomes.jsonl").read_text().splitlines()]
    assert rows[-1]["failure_class"] == "rate_limited"


# ---------- the tracker row names the reset (#407) ----------


def _real_scoutctl(vault: Path) -> Path:
    """The installed path the template names, running this checkout's engine."""
    path = vault / "bin" / "scoutctl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scout.cli "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def _tracker(vault: Path) -> list[dict]:
    path = vault / ".scout-logs" / "usage-tracker.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _in_three_hours() -> datetime:
    return (datetime.now(UTC) + timedelta(hours=3)).replace(second=0, microsecond=0)


def test_the_tracker_row_records_when_the_limit_lifts(vault: Path) -> None:
    _real_scoutctl(vault)
    detect = _install(vault, "rate-limit-detect.sh")
    lifts = _in_three_hours()
    log = _log(vault, f"You've hit your session limit · resets {lifts:%H:%M} (UTC)\n")

    subprocess.run([str(detect), str(log), "--write-tracker"], check=True, capture_output=True)

    (row,) = _tracker(vault)
    assert row["type"] == "rate_limit"
    assert row["resets_at"] == lifts.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize(
    "log_text",
    [
        pytest.param("You've hit your session limit · resets soon\n", id="no-time"),
        pytest.param("API Error: 429 Too Many Requests\n", id="no-reset-named"),
    ],
)
def test_a_row_without_a_readable_reset_has_no_resets_at(vault: Path, log_text: str) -> None:
    _real_scoutctl(vault)
    detect = _install(vault, "rate-limit-detect.sh")

    subprocess.run([str(detect), str(_log(vault, log_text)), "--write-tracker"], check=True, capture_output=True)

    (row,) = _tracker(vault)
    assert row["type"] == "rate_limit" and "resets_at" not in row


def test_the_row_is_written_without_scoutctl(vault: Path) -> None:
    """No engine (a broken install): the row is still written, as before #407."""
    detect = _install(vault, "rate-limit-detect.sh")
    log = _log(vault, "You've hit your session limit · resets 3pm\n")

    result = subprocess.run(
        [str(detect), str(log), "--write-tracker"],
        env={**os.environ, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    (row,) = _tracker(vault)
    assert "resets_at" not in row

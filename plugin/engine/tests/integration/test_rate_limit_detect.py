"""templates/scripts/rate-limit-detect.sh — decides whether a failed run is retried.

The retry wrapper calls ``scripts/rate-limit-detect.sh <log>``: exit 0 means the
log shows a rate limit (transient, retry later), exit 1 means it doesn't. Its
pattern list must agree with ``RATE_LIMIT_RE`` in ``run-outcome.sh``, which only
labels the failure; the parity test below runs both real scripts on the same logs.

The templates are rendered into a temporary vault and run by the real shell.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

SCRIPTS = Path(__file__).resolve().parents[3] / "templates" / "scripts"


def _install(vault: Path, name: str, **values: str) -> Path:
    text = render_template((SCRIPTS / f"{name}.tmpl").read_text(encoding="utf-8"), {"SCOUT_DIR": str(vault), **values})
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

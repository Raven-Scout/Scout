"""templates/scripts/run-outcome.sh — the out-of-band record of how each run ended.

Every runner calls ``scripts/run-outcome.sh record <mode> <exit> <start> <log>``
after the session. The script appends a row to ``.scout-logs/run-outcomes.jsonl``,
names the failure class from the run's log, and sends a Telegram notice through
``scoutctl notify telegram`` at one and at two consecutive failures, then stays
quiet. It lives in the runner, not the session, because the failure it watches
for is a run that dies before the session can report anything.

The template is rendered into a temporary vault and run by the real shell, with
a stand-in ``scoutctl`` that records each call.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "run-outcome.sh.tmpl"

SCOUTCTL_STUB = """#!/usr/bin/env python3
import json, sys
with open({calls!r}, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit({exit_code})
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _install(vault: Path, *, scoutctl: Path) -> Path:
    text = render_template(
        TEMPLATE.read_text(encoding="utf-8"),
        {"SCOUT_DIR": str(vault), "SCOUTCTL_BIN": str(scoutctl), "INSTANCE_NAME": "Scout"},
    )
    assert "{{" not in text
    return _script(vault / "scripts" / "run-outcome.sh", text)


@pytest.fixture
def calls(vault: Path) -> Path:
    return vault / "scoutctl.calls"


@pytest.fixture
def outcome(vault: Path, calls: Path) -> Path:
    stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(calls), exit_code=0))
    return _install(vault, scoutctl=stub)


def _log(vault: Path, text: str, name: str = "scout-2026-01-05_08-00.log") -> Path:
    path = vault / ".scout-logs" / name
    path.write_text(text, encoding="utf-8")
    return path


def _record(script: Path, mode: str, exit_code: int, log: Path, **env: str) -> subprocess.CompletedProcess[str]:
    started = str(int(time.time()) - 90)
    return subprocess.run(
        ["bash", str(script), "record", mode, str(exit_code), started, str(log)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def _ledger(vault: Path) -> list[dict]:
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _notices(calls: Path) -> list[list[str]]:
    if not calls.exists():
        return []
    return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]


def _tiers(calls: Path) -> list[str]:
    return [argv[argv.index("--tier") + 1] for argv in _notices(calls)]


# ---------- the ledger ----------


def test_a_successful_run_is_recorded_and_sends_nothing(vault: Path, outcome: Path, calls: Path) -> None:
    log = _log(vault, "=== Scout run finished (exit code: 0) ===\n")

    result = _record(outcome, "morning-briefing", 0, log)

    assert result.returncode == 0, result.stderr
    (row,) = _ledger(vault)
    assert row["mode"] == "morning-briefing"
    assert row["slot"] == "morning-briefing"
    assert row["exit_code"] == 0
    assert row["failure_class"] == "ok"
    assert row["log"] == log.name
    assert row["finished_at"] >= row["started_at"]
    assert 85 <= row["duration_s"] <= 120
    assert _notices(calls) == []


def test_the_slot_is_the_dispatcher_slot_when_one_is_set(vault: Path, outcome: Path) -> None:
    log = _log(vault, "")

    _record(outcome, "manual", 0, log, SCOUT_FORCE_MODE="evening-consolidation")

    (row,) = _ledger(vault)
    assert (row["slot"], row["mode"]) == ("evening-consolidation", "manual")


def test_scout_data_dir_overrides_the_installed_vault_path(vault: Path, outcome: Path, tmp_path: Path) -> None:
    other = tmp_path / "Other"
    log = _log(vault, "")

    _record(outcome, "research", 0, log, SCOUT_DATA_DIR=str(other))

    assert (other / ".scout-logs" / "run-outcomes.jsonl").exists()
    assert not (vault / ".scout-logs" / "run-outcomes.jsonl").exists()


@pytest.mark.parametrize(
    ("log_text", "klass"),
    [
        pytest.param("Error: Exceeded USD budget (20)\n", "budget", id="budget-cap"),
        pytest.param("Failed to authenticate. API Error: 401\n", "oauth_expired", id="auth-401"),
        pytest.param("OAuth session expired, please log in again\n", "oauth_expired", id="oauth"),
        pytest.param(
            "Failed to authenticate: OAuth session expired and could not be refreshed\n",
            "oauth_expired",
            id="oauth-cli-string",
        ),
        # Text that only quotes an auth error, or another service's 403, is not
        # a rejected Claude login: the phrases are the CLI's, at line start.
        pytest.param(
            "Summary: last week's runs failed to authenticate (OAuth session expired)\n",
            "unknown",
            id="quoted-auth-text-is-not-auth",
        ),
        pytest.param("tracker fetch: HTTP/1.1 403 Forbidden\n", "unknown", id="other-service-403-is-not-auth"),
        pytest.param("Unable to connect to API (ECONNRESET)\n", "network", id="econnreset"),
        pytest.param("getaddrinfo ENOTFOUND api.example.com\n", "network", id="dns"),
        pytest.param("fatal: Unable to create '/v/.git/index.lock': File exists.\n", "git_lock", id="git-lock"),
        pytest.param("API Error: 429 Too Many Requests\n", "rate_limited", id="http-429"),
        pytest.param("You have hit your usage limit\n", "rate_limited", id="usage-limit"),
        # The budget check's own preamble names the budget on every run; that
        # is not a budget failure.
        pytest.param(
            "[budget-check] Window budget: $18.75, Skip at: $16.88\nsomething else broke\n",
            "unknown",
            id="budget-preamble-is-not-a-budget-failure",
        ),
        # A 429 inside a number is not a rate limit.
        pytest.param("run finished (exit code: 1, duration: 4291s)\n", "unknown", id="429-inside-a-number"),
    ],
)
def test_the_failure_class_is_named_from_the_log(vault: Path, outcome: Path, log_text: str, klass: str) -> None:
    log = _log(vault, log_text)

    _record(outcome, "dreaming-nightly", 1, log)

    assert _ledger(vault)[-1]["failure_class"] == klass


def test_a_missing_log_is_an_unknown_failure(vault: Path, outcome: Path) -> None:
    _record(outcome, "dreaming-nightly", 1, vault / ".scout-logs" / "gone.log")

    assert _ledger(vault)[-1]["failure_class"] == "unknown"


# ---------- escalation ----------


def test_the_first_failure_is_silent_the_second_loud_then_quiet(vault: Path, outcome: Path, calls: Path) -> None:
    log = _log(vault, "Failed to authenticate\n")

    for _ in range(4):
        _record(outcome, "morning-briefing", 1, log)

    assert _tiers(calls) == ["info", "action_required"]


def test_a_success_resets_the_failure_streak(vault: Path, outcome: Path, calls: Path) -> None:
    bad = _log(vault, "Failed to authenticate\n", "scout-bad.log")
    good = _log(vault, "", "scout-good.log")

    _record(outcome, "morning-briefing", 1, bad)
    _record(outcome, "morning-briefing", 0, good)
    _record(outcome, "morning-briefing", 1, bad)

    assert _tiers(calls) == ["info", "info"]


def test_the_notice_names_the_run_the_class_and_the_log(vault: Path, outcome: Path, calls: Path) -> None:
    log = _log(vault, "Failed to authenticate\n")

    _record(outcome, "morning-briefing", 1, log)

    (argv,) = _notices(calls)
    assert argv[:2] == ["notify", "telegram"]
    body = argv[argv.index("--body") + 1]
    assert "morning-briefing" in body
    assert "exit 1" in body
    assert "oauth_expired" in body
    assert "Re-authenticate" in body
    assert "run: claude auth login" in body, "the Telegram notice names the same fix as the desktop alert"
    assert str(log) in body


@pytest.mark.parametrize("scoutctl", ["missing", "failing"])
def test_a_broken_scoutctl_never_fails_the_record(vault: Path, calls: Path, scoutctl: str) -> None:
    if scoutctl == "missing":
        stub = vault / "bin" / "no-such-scoutctl"
    else:
        stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(calls), exit_code=1))
    script = _install(vault, scoutctl=stub)
    log = _log(vault, "Failed to authenticate\n")

    result = _record(script, "morning-briefing", 1, log, PATH="/usr/bin:/bin")

    assert result.returncode == 0, result.stderr
    assert _ledger(vault)[-1]["failure_class"] == "oauth_expired"


# ---------- clear-stale-locks ----------


def test_clear_stale_locks_removes_only_old_git_locks(vault: Path, outcome: Path) -> None:
    git = vault / ".git"
    git.mkdir()
    stale, fresh = git / "index.lock", git / "HEAD.lock"
    stale.write_text("", encoding="utf-8")
    fresh.write_text("", encoding="utf-8")
    three_hours_ago = time.time() - 3 * 3600
    os.utime(stale, (three_hours_ago, three_hours_ago))

    result = subprocess.run(["bash", str(outcome), "clear-stale-locks"], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert fresh.exists()


def test_an_unknown_command_prints_usage(outcome: Path) -> None:
    result = subprocess.run(["bash", str(outcome), "bogus"], capture_output=True, text=True, timeout=30)

    assert result.returncode == 64
    assert "usage:" in result.stderr

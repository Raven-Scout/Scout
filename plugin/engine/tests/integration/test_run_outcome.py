"""templates/scripts/run-outcome.sh — the out-of-band record of how each run ended.

Every runner calls ``scripts/run-outcome.sh record <mode> <exit> <start> <log>``
after the session. The script appends a row to ``.scout-logs/run-outcomes.jsonl``,
names the failure class from the run's log, then calls ``scoutctl health notify
--quiet`` after every run. If scoutctl cannot run, it raises one local desktop
alert a day (the floor). It lives in the runner, not the session, because the
failure it watches for is a run that dies before the session can report anything.

The template is rendered into a temporary vault and run by the real shell, with
a stand-in ``scoutctl`` that records each call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
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


def _notifier_bin(tmp_path: Path) -> tuple[Path, Path]:
    """Fake `osascript` + `notify-send` that append their argv to a record file.

    Every test that can reach the floor puts these first on PATH, so none ever
    pops a real desktop notification.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    record = tmp_path / "notifications.log"
    for name in ("osascript", "notify-send"):
        p = fake_bin / name
        p.write_text(f'#!/bin/bash\necho "{name} $*" >> "{record}"\ncat > /dev/null\n', encoding="utf-8")
        p.chmod(0o755)
    return fake_bin, record


def _notifications(record: Path) -> list[str]:
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


@pytest.fixture
def notifier(tmp_path: Path) -> tuple[Path, Path]:
    return _notifier_bin(tmp_path)


def _env(fake_bin: Path, **env: str) -> dict[str, str]:
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", **env}


# ---------- the ledger ----------


def test_a_successful_run_is_recorded_and_sends_nothing(
    vault: Path, outcome: Path, calls: Path, notifier: tuple[Path, Path]
) -> None:
    log = _log(vault, "=== Scout run finished (exit code: 0) ===\n")

    result = _record(outcome, "morning-briefing", 0, log, **_env(notifier[0]))

    assert result.returncode == 0, result.stderr
    (row,) = _ledger(vault)
    assert row["mode"] == "morning-briefing"
    assert row["slot"] == "morning-briefing"
    assert row["exit_code"] == 0
    assert row["failure_class"] == "ok"
    assert row["log"] == log.name
    assert row["finished_at"] >= row["started_at"]
    assert 85 <= row["duration_s"] <= 120
    assert not any(argv[:1] == ["notify"] for argv in _notices(calls))
    assert _notifications(notifier[1]) == []


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
        # Claude Code's own quota message names a reset time; it is a rate limit.
        pytest.param("You've hit your session limit · resets 3pm\n", "rate_limited", id="session-limit"),
        pytest.param("Weekly limit reached · resets Mon 9am\n", "rate_limited", id="limit-resets-rewording"),
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


# ---------- a usage limit's reset (#407) ----------

# Runs this checkout's engine for `budget resets-at`; records every other call.
SCOUTCTL_ENGINE_STUB = """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:3] == ["budget", "resets-at"]:
    os.execv({python!r}, [{python!r}, "-m", "scout.cli", *sys.argv[1:]])
with open({calls!r}, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
"""


@pytest.fixture
def engine_outcome(vault: Path, calls: Path) -> Path:
    stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_ENGINE_STUB.format(python=sys.executable, calls=str(calls)))
    return _install(vault, scoutctl=stub)


def test_a_usage_limit_row_records_when_it_lifts(vault: Path, engine_outcome: Path, calls: Path) -> None:
    lifts = (datetime.now(UTC) + timedelta(hours=3)).replace(second=0, microsecond=0)
    log = _log(vault, f"You've hit your session limit · resets {lifts:%H:%M} (UTC)\n")

    result = _record(engine_outcome, "dreaming-nightly", 1, log)

    assert result.returncode == 0, result.stderr
    row = _ledger(vault)[-1]
    assert row["failure_class"] == "rate_limited"
    assert row["resets_at"] == int(lifts.timestamp())
    assert _notices(calls) == [["health", "notify", "--quiet"]]


@pytest.mark.parametrize(
    ("exit_code", "log_text"),
    [
        pytest.param(1, "You've hit your session limit · resets soon\n", id="unreadable-time"),
        pytest.param(1, "API Error: 429 Too Many Requests\n", id="no-reset-named"),
        pytest.param(1, "Unable to connect to API (ECONNRESET) · usage limit resets 3pm\n", id="not-rate-limited"),
        pytest.param(0, "You've hit your session limit · resets 3pm\n", id="a-success"),
    ],
)
def test_a_row_without_a_readable_reset_has_no_resets_at(
    vault: Path, engine_outcome: Path, exit_code: int, log_text: str
) -> None:
    _record(engine_outcome, "dreaming-nightly", exit_code, _log(vault, log_text))

    assert "resets_at" not in _ledger(vault)[-1]


def test_a_failing_engine_leaves_resets_at_out(vault: Path, calls: Path) -> None:
    stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(calls), exit_code=1))
    script = _install(vault, scoutctl=stub)

    _record(script, "dreaming-nightly", 1, _log(vault, "You've hit your session limit · resets 3pm\n"))

    row = _ledger(vault)[-1]
    assert row["failure_class"] == "rate_limited" and "resets_at" not in row


# ---------- the notifier ----------


@pytest.mark.parametrize(("exit_code", "text"), [(0, ""), (1, "Failed to authenticate\n")], ids=["success", "failure"])
def test_every_record_calls_health_notify(
    vault: Path, outcome: Path, calls: Path, notifier: tuple[Path, Path], exit_code: int, text: str
) -> None:
    log = _log(vault, text)

    _record(outcome, "morning-briefing", exit_code, log, **_env(notifier[0]))
    _record(outcome, "morning-briefing", exit_code, log, **_env(notifier[0]))

    assert _notices(calls) == [["health", "notify", "--quiet"]] * 2


def test_no_telegram_call_from_bash(vault: Path, outcome: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    log = _log(vault, "Failed to authenticate\n")

    for _ in range(3):
        _record(outcome, "morning-briefing", 1, log, **_env(notifier[0]))

    assert [argv for argv in _notices(calls) if argv[:1] == ["notify"]] == []


def test_the_notifier_output_lands_in_the_run_log(vault: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    stub = _script(vault / "bin" / "scoutctl", "#!/bin/bash\necho 'pushed: auth.expired'\n")
    script = _install(vault, scoutctl=stub)
    log = _log(vault, "")

    _record(script, "morning-briefing", 0, log, **_env(notifier[0]))
    _record(script, "morning-briefing", 0, vault / ".scout-logs" / "gone.log", **_env(notifier[0]))

    assert "pushed: auth.expired" in log.read_text(encoding="utf-8")
    assert "pushed: auth.expired" in (vault / ".scout-logs" / "health-notify.log").read_text(encoding="utf-8")


# ---------- the engine-unreachable floor ----------


def _floor(vault: Path, calls: Path, exit_code: int = 1) -> Path:
    stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(calls), exit_code=exit_code))
    return _install(vault, scoutctl=stub)


def test_an_unreachable_scoutctl_raises_one_floor_alert(vault: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    fake_bin, record = notifier
    script = _floor(vault, calls)
    log = _log(vault, "")

    for _ in range(2):
        result = _record(script, "morning-briefing", 0, log, **_env(fake_bin))
        assert result.returncode == 0, result.stderr

    (note,) = _notifications(record)
    assert "engine is broken" in note
    assert "Reinstall" in note
    marker = vault / ".scout-logs" / ".engine-alert-sent"
    assert marker.read_text(encoding="utf-8").strip().isdigit()


def test_the_floor_alerts_again_after_the_interval(vault: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    fake_bin, record = notifier
    script = _floor(vault, calls)
    log = _log(vault, "")

    for _ in range(2):
        _record(script, "morning-briefing", 0, log, **_env(fake_bin, SCOUT_ENGINE_ALERT_INTERVAL_S="0"))

    assert len(_notifications(record)) == 2


def test_a_non_numeric_interval_falls_back_to_a_day(vault: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    fake_bin, record = notifier
    script = _floor(vault, calls)
    log = _log(vault, "")

    for _ in range(2):
        _record(script, "morning-briefing", 0, log, **_env(fake_bin, SCOUT_ENGINE_ALERT_INTERVAL_S="soon"))

    assert len(_notifications(record)) == 1


def test_a_working_engine_clears_the_floor_marker(vault: Path, calls: Path, notifier: tuple[Path, Path]) -> None:
    fake_bin, record = notifier
    broken = _floor(vault, calls, exit_code=1)
    log = _log(vault, "")
    _record(broken, "morning-briefing", 0, log, **_env(fake_bin))
    marker = vault / ".scout-logs" / ".engine-alert-sent"
    assert marker.exists()

    working = _floor(vault, calls, exit_code=0)
    _record(working, "morning-briefing", 0, log, **_env(fake_bin))
    assert not marker.exists()

    broken = _floor(vault, calls, exit_code=1)  # the same script path, pointed at a broken engine again
    _record(broken, "morning-briefing", 0, log, **_env(fake_bin))
    assert len(_notifications(record)) == 2, "the next breakage alerts at once"


def test_a_missing_scoutctl_raises_the_floor(vault: Path, notifier: tuple[Path, Path]) -> None:
    fake_bin, record = notifier
    script = _install(vault, scoutctl=vault / "bin" / "no-such-scoutctl")
    log = _log(vault, "")

    result = _record(script, "morning-briefing", 0, log, PATH=f"{fake_bin}{os.pathsep}/usr/bin:/bin")

    assert result.returncode == 0, result.stderr
    assert len(_notifications(record)) == 1


def test_a_failed_notification_leaves_no_marker(vault: Path, calls: Path, tmp_path: Path) -> None:
    fake_bin = tmp_path / "failbin"
    fake_bin.mkdir()
    for name in ("osascript", "notify-send"):
        _script(fake_bin / name, "#!/bin/bash\ncat > /dev/null\nexit 1\n")
    script = _floor(vault, calls)

    result = _record(script, "morning-briefing", 0, _log(vault, ""), **_env(fake_bin))

    assert result.returncode == 0, result.stderr
    assert not (vault / ".scout-logs" / ".engine-alert-sent").exists()


@pytest.mark.parametrize("scoutctl", ["missing", "failing"])
def test_a_broken_scoutctl_never_fails_the_record(
    vault: Path, calls: Path, scoutctl: str, notifier: tuple[Path, Path]
) -> None:
    if scoutctl == "missing":
        stub = vault / "bin" / "no-such-scoutctl"
    else:
        stub = _script(vault / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(calls), exit_code=1))
    script = _install(vault, scoutctl=stub)
    log = _log(vault, "Failed to authenticate\n")

    result = _record(script, "morning-briefing", 1, log, PATH=f"{notifier[0]}{os.pathsep}/usr/bin:/bin")

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

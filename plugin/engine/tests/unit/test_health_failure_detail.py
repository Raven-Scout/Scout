"""Run-health findings carry the failing run's own error line, its log and its reset (#445)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
TEMPLATE = Path(health.__file__).resolve().parents[2] / "templates" / "scripts" / "run-outcome.sh.tmpl"

# The tail of a real runner log after Claude Code stopped on its quota: the CLI's own line, then
# the retry wrapper's banner (which repeats it) and the runner's footer.
LIMIT_TAIL = """\
[budget-check] window: 3h, daily: $200.00, window budget: $25.00, skip at: $22.50
You've hit your weekly limit · resets Oct 13 at 2pm (UTC)
=== Claude usage limit — no retry (exit 1) ===
    You've hit your weekly limit · resets Oct 13 at 2pm (UTC)
    The next scheduled run after the reset picks up.
=== Scout run finished at Sun Oct 11 11:00:00 UTC 2026 (exit code: 1, duration: 30s) ===
Rate limit detected — logged to usage tracker
"""


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(at: datetime, *, klass: str, log: str | None = "scout-run.log", **extra) -> dict:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {
        "slot": "morning-briefing",
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "exit_code": 0 if klass == "ok" else 1,
        "failure_class": klass,
        "log": log,
        **extra,
    }


def _ledger(vault: Path, rows: list[dict]) -> None:
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _log(vault: Path, text: str, name: str = "scout-run.log") -> Path:
    path = vault / ".scout-logs" / name
    path.write_text(text, encoding="utf-8")
    return path


def _only(report: health.RunHealth) -> health.Finding:
    assert len(report.findings) == 1, report.findings
    return report.findings[0]


@pytest.mark.parametrize(
    ("klass", "tail", "line"),
    [
        pytest.param(
            "rate_limited", LIMIT_TAIL, "You've hit your weekly limit · resets Oct 13 at 2pm (UTC)", id="usage-limit"
        ),
        pytest.param(
            "oauth_expired",
            "starting\n=== Authentication failure (HTTP 401/403) ===\nOAuth token has expired\n",
            "OAuth token has expired",
            id="auth",
        ),
        pytest.param(
            "network",
            "starting\nAPI Error: Connection error (ECONNRESET)\n=== Scout run finished ===\n",
            "API Error: Connection error (ECONNRESET)",
            id="network",
        ),
        pytest.param(
            "git_lock",
            "fatal: Unable to create '/v/.git/index.lock': File exists.\n=== Scout run finished ===\n",
            "fatal: Unable to create '/v/.git/index.lock': File exists.",
            id="git-lock",
        ),
    ],
)
def test_failure_line_is_the_error_itself_not_the_runner_banner(tmp_path, klass, tail, line):
    log = _log(_vault(tmp_path), tail)

    assert health.failure_line(log, klass) == line


def test_failure_line_is_none_when_nothing_names_the_failure(tmp_path):
    v = _vault(tmp_path)

    assert health.failure_line(_log(v, "starting\n=== Scout run finished ===\n"), "unknown") is None
    assert health.failure_line(v / ".scout-logs" / "missing.log", "network") is None


def test_failure_line_is_capped(tmp_path):
    log = _log(_vault(tmp_path), "API Error: ECONNRESET " + "x" * 2000 + "\n")

    line = health.failure_line(log, "network")

    assert line is not None and len(line) <= health.FAILURE_LINE_MAX and line.endswith("…")


def test_a_failing_finding_carries_the_error_line_and_the_log(tmp_path):
    v = _vault(tmp_path)
    log = _log(v, "API Error: Connection error (ECONNRESET)\n")
    _ledger(v, [_row(NOW - timedelta(hours=1), klass="network")])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "runs.failing"
    assert finding.detail == "API Error: Connection error (ECONNRESET)"
    assert finding.log == str(log)
    assert finding.until is None


def test_a_ledger_auth_finding_carries_the_error_line(tmp_path):
    v = _vault(tmp_path)
    _log(v, "OAuth token has expired\n")
    _ledger(v, [_row(NOW - timedelta(hours=1), klass="oauth_expired")])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "auth.expired" and finding.detail == "OAuth token has expired"


def test_a_log_only_auth_finding_carries_the_error_line(tmp_path):
    """No ledger yet (a runner older than the ledger): the newest log is read directly."""
    v = _vault(tmp_path)
    log = _log(v, "Invalid authentication credentials\n", name="scout-2026-10-11_09-00.log")

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "auth.expired" and finding.source == "log"
    assert finding.detail == "Invalid authentication credentials" and finding.log == str(log)


def test_a_paused_finding_carries_the_limit_line_and_when_it_lifts(tmp_path):
    v = _vault(tmp_path)
    log = _log(v, LIMIT_TAIL)
    resets = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    _ledger(v, [_row(NOW - timedelta(hours=1), klass="rate_limited", resets_at=int(resets.timestamp()))])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == health.PAUSED_FINDING_ID
    assert finding.detail == "You've hit your weekly limit · resets Oct 13 at 2pm (UTC)"
    assert finding.log == str(log)
    assert finding.until == resets


def test_a_limit_row_recorded_without_its_reset_reads_it_from_the_log(tmp_path):
    """Rows written by runners older than #407 carry no resets_at: the log still names it."""
    v = _vault(tmp_path)
    _log(v, LIMIT_TAIL)
    _ledger(v, [_row(NOW - timedelta(hours=1), klass="rate_limited")])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == health.PAUSED_FINDING_ID
    assert finding.until == datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    assert "paused until 2026-10-13T14:00:00Z" in finding.summary


def test_a_log_reset_is_read_against_the_run_not_the_clock(tmp_path):
    """A bare "resets 2pm" means the 2pm after the run ended, even when health runs days later."""
    v = _vault(tmp_path)
    _log(v, "You've hit your session limit · resets 2pm (UTC)\n")
    ended = NOW - timedelta(days=3)  # 2026-10-08 12:00Z: its reset, 14:00 that day, has long passed
    _ledger(v, [_row(ended, klass="rate_limited")])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "runs.failing" and finding.until is None


def test_to_dict_emits_the_new_keys(tmp_path):
    f = health.Finding(
        id="runs.paused",
        severity="yellow",
        since=None,
        summary="s",
        fix="f",
        source="ledger",
        detail="d",
        log="/v/.scout-logs/x.log",
        until=datetime(2026, 10, 13, 14, 0, tzinfo=UTC),
    )

    d = f.to_dict()

    assert d["detail"] == "d" and d["log"] == "/v/.scout-logs/x.log" and d["until"] == "2026-10-13T14:00:00Z"
    bare = health.Finding(id="x", severity="note", since=None, summary="s", fix="", source="git").to_dict()
    assert bare["detail"] is None and bare["log"] is None and bare["until"] is None


@pytest.mark.parametrize(
    ("klass", "var", "line"),
    [
        ("network", "NETWORK_RE", "API Error: Connection error (ECONNREFUSED)"),
        ("network", "NETWORK_RE", "Stream idle timeout - partial response received"),
        ("git_lock", "GIT_LOCK_RE", "fatal: Unable to create '/v/.git/HEAD.lock': File exists."),
        ("rate_limited", "RATE_LIMIT_RE", "You've hit your session limit · resets 2:10pm (Europe/Prague)"),
        ("rate_limited", "RATE_LIMIT_RE", "API Error: 429 Too Many Requests"),
        ("rate_limited", "RATE_LIMIT_RE", 'API Error: 529 {"type":"overloaded_error"}'),
    ],
)
def test_a_detail_line_also_matches_the_runner_class_pattern(tmp_path, klass, var, line):
    """failure_line only picks lines the runner's own class pattern matches too, so the detail never
    names a different failure than the class beside it."""
    shell = re.search(rf"^{var}='([^']*)'", TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE)
    assert shell is not None and re.search(shell.group(1), line, re.IGNORECASE)

    assert health.failure_line(_log(_vault(tmp_path), f"{line}\n"), klass) == line

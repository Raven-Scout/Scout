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

# The sign-in race, as a synthetic line: the CLI's real one names the account's email.
AUTH_RACE_LINE = (
    "Claude Code couldn't verify your sign-in with Anthropic yet (its sign-in token was still being "
    "refreshed, often by another Claude Code process), so it couldn't load your organization's settings "
    "for alex@example.com."
)

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
        pytest.param(
            "auth_race",
            f"starting\n{AUTH_RACE_LINE}\n=== Retry exhausted: 2 attempts, last exit 1 ===\n",
            AUTH_RACE_LINE.replace("alex@example.com", "<email>"),
            id="auth-race",
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
        ("network", "NETWORK_RE", "API Error: Unable to connect to API (FailedToOpenSocket)"),
        ("git_lock", "GIT_LOCK_RE", "fatal: Unable to create '/v/.git/HEAD.lock': File exists."),
        ("rate_limited", "RATE_LIMIT_RE", "You've hit your session limit · resets 2:10pm (Europe/Prague)"),
        ("rate_limited", "RATE_LIMIT_RE", "API Error: 429 Too Many Requests"),
        ("rate_limited", "RATE_LIMIT_RE", 'API Error: 529 {"type":"overloaded_error"}'),
        # Real CLI shapes the anchored patterns must keep catching (C8 delta review).
        ("rate_limited", "RATE_LIMIT_RE", "Opus weekly limit reached ∙ resets Mon 9am"),
        ("rate_limited", "RATE_LIMIT_RE", "5-hour limit reached ∙ resets 1am"),
        ("rate_limited", "RATE_LIMIT_RE", "Error: 429 Too Many Requests"),
        ("network", "NETWORK_RE", "API Error: Stream idle timeout - partial response received"),
        ("network", "NETWORK_RE", 'code: "ConnectionRefused",'),
        ("network", "NETWORK_RE", "error: FailedToOpenSocket: Was there a typo in the url or port?"),
        ("budget", "BUDGET_RE", "Error: Exceeded USD budget (16.6)"),
        ("budget", "BUDGET_RE", "budget spent, no retry: this run's attempts spent $25.00 of its $25.00 cap"),
        (
            "auth_race",
            "AUTH_RACE_RE",
            "Claude Code couldn't verify your sign-in with Anthropic yet (its sign-in token was still being refreshed)",
        ),
        ("auth_race", "AUTH_RACE_RE", "Claude Code couldn\u2019t verify your sign-in with Anthropic yet."),
        ("auth_race", "AUTH_RACE_RE", "Claude Code couldn't confirm it: its sign-in token was still being refreshed"),
    ],
)
def test_a_detail_line_also_matches_the_runner_class_pattern(tmp_path, klass, var, line):
    """failure_line only picks lines the runner's own class pattern matches too, so the detail never
    names a different failure than the class beside it."""
    shell = re.search(rf"^{var}='([^']*)'", TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE)
    assert shell is not None and re.search(shell.group(1), line, re.IGNORECASE)

    assert health.failure_line(_log(_vault(tmp_path), f"{line}\n"), klass) == line


@pytest.mark.parametrize(
    ("klass", "prose"),
    [
        ("rate_limited", "Drafting reply about CANARY_PRIVATE_NOTE: …hit your monthly limit, resets in May"),
        ("rate_limited", "Summary: CANARY_PRIVATE_NOTE says the API was overloaded last week"),
        ("rate_limited", "CANARY_PRIVATE_NOTE: too many requests from the vendor"),
        ("network", "Noted in CANARY_PRIVATE_NOTE: the ECONNRESET bug is fixed"),
        ("git_lock", "CANARY_PRIVATE_NOTE mentions index.lock in the runbook"),
        ("budget", "CANARY_PRIVATE_NOTE: we exceeded USD budget in Q3"),
        ("oauth_expired", "  CANARY_PRIVATE_NOTE: OAuth token has expired for the vendor"),
        ("auth_race", "CANARY_PRIVATE_NOTE: Claude Code couldn't verify your sign-in with Anthropic yet"),
        ("auth_race", "CANARY_PRIVATE_NOTE: its sign-in token was still being refreshed"),
    ],
)
def test_failure_line_never_quotes_the_sessions_own_prose(tmp_path, klass, prose):
    """The log holds the session's output too, which can quote vault notes: a line only counts when
    the error shape opens it (C8 review canary)."""
    log = _log(_vault(tmp_path), f"starting\n{prose}\n=== Scout run finished ===\n")

    assert health.failure_line(log, klass) is None


def test_failure_line_prefers_the_cli_line_over_later_prose(tmp_path):
    tail = "API Error: 429 Too Many Requests\nI was rate limited; CANARY_PRIVATE_NOTE hit your limit, resets soon\n"

    assert health.failure_line(_log(_vault(tmp_path), tail), "rate_limited") == "API Error: 429 Too Many Requests"


def test_failure_line_writes_home_as_tilde(tmp_path, monkeypatch):
    monkeypatch.setattr(health.Path, "home", classmethod(lambda cls: tmp_path))
    log = _log(_vault(tmp_path), f"fatal: Unable to create '{tmp_path}/Scout/.git/index.lock': File exists.\n")

    assert health.failure_line(log, "git_lock") == "fatal: Unable to create '~/Scout/.git/index.lock': File exists."


def test_an_auth_race_finding_names_the_race_and_never_the_email(tmp_path):
    """The race's line names the account; the finding quotes it with the address written as <email>."""
    v = _vault(tmp_path)
    _log(v, f"{AUTH_RACE_LINE}\n=== Retry exhausted: 2 attempts, last exit 1 ===\n")
    _ledger(v, [_row(NOW - timedelta(hours=1), klass="auth_race")])

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "runs.failing" and "(auth_race)" in finding.summary
    assert finding.detail is not None and finding.detail.startswith("Claude Code couldn't verify your sign-in")
    assert "@" not in finding.detail and "<email>" in finding.detail


def test_recurring_auth_races_say_the_login_is_fine(tmp_path):
    v = _vault(tmp_path)
    _log(v, f"{AUTH_RACE_LINE}\n")
    rows = []
    for hours, klass in ((30, "auth_race"), (20, "ok"), (10, "auth_race"), (5, "auth_race"), (1, "ok")):
        rows.append(_row(NOW - timedelta(hours=hours), klass=klass))
    _ledger(v, rows)

    finding = health.recurring_failures(health.read_outcomes(v), vault=v, now=NOW)

    assert finding is not None and "auth_race ×3" in finding.summary
    assert "the login itself is fine" in finding.fix and "@" not in (finding.detail or "")


@pytest.mark.parametrize(
    ("classes", "severity", "race_fix"),
    [
        pytest.param(["auth_race", "auth_race"], "yellow", True, id="two-races-stay-yellow"),
        pytest.param(["auth_race"] * health.RECURRING_FAILURE_THRESHOLD, "red", True, id="enough-races-turn-red"),
        pytest.param(["network", "auth_race"], "red", False, id="a-mixed-streak-is-the-usual-one"),
    ],
)
def test_a_streak_of_sign_in_races_says_what_it_is(tmp_path, classes, severity, race_fix):
    """The login is fine and the wrapper already retried: no doctor, and red only once it has cost several runs."""
    v = _vault(tmp_path)
    _log(v, f"{AUTH_RACE_LINE}\n")
    rows = [_row(NOW - timedelta(hours=10), klass="ok")]
    rows += [_row(NOW - timedelta(hours=len(classes) - i), klass=k) for i, k in enumerate(classes)]
    _ledger(v, rows)

    finding = _only(health.evaluate(v, now=NOW))

    assert finding.id == "runs.failing" and finding.severity == severity
    assert ("sign-in was busy" in finding.fix) is race_fix
    assert ("bootstrap doctor" in finding.fix) is not race_fix

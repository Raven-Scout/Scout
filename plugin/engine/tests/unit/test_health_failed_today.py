"""runs.failed_today (UI audit R1): a run that failed today stays visible until its own schedule finishes.

The trailing streak (runs.failing) clears on any later success, so a failed morning briefing followed
by a clean consolidation used to read "healthy" all day while the Today tile counted the failure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import health

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path, timezone: str = "UTC") -> Path:
    """A vault whose day is ``timezone``'s, so "today" doesn't move with the host's zone."""
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    (v / "scout-config.yaml").write_text(f"user:\n  timezone: {timezone}\n", encoding="utf-8")
    return v


def _row(at: datetime, slot: str, *, code: int = 0, klass: str = "ok", **extra) -> dict:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {
        "slot": slot,
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "exit_code": code,
        "failure_class": klass,
        "log": f"scout-{started}.log",
        **extra,
    }


def _ledger(vault: Path, rows: list[dict]) -> None:
    text = "\n".join(json.dumps(r) for r in rows) + "\n"
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(text, encoding="utf-8")


def _ids(report: health.RunHealth) -> dict[str, str]:
    return {f.id: f.severity for f in report.findings}


def _today(report: health.RunHealth) -> health.Finding:
    return next(f for f in report.findings if f.id == health.FAILED_TODAY_FINDING_ID)


def test_a_failure_today_stays_yellow_after_another_slot_succeeds(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="unknown"),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    report = health.evaluate(v, now=NOW)
    assert report.severity == "yellow"
    assert _ids(report) == {"runs.failed_today": "yellow"}
    finding = report.findings[0]
    assert finding.summary == (
        "morning-briefing (at 08:00, unknown) failed today and hasn't finished a run since. "
        "Other scheduled runs have finished since."
    )
    assert finding.since == NOW - timedelta(hours=4)
    assert ".scout-logs/scout-" in finding.fix and "that schedule" in finding.fix


def test_a_later_success_of_the_same_slot_clears_it(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=3), "midday-consolidation"),
            _row(NOW - timedelta(hours=1), "morning-briefing"),
        ],
    )
    assert health.evaluate(v, now=NOW).severity == "green"


def test_a_skip_of_the_same_slot_clears_nothing(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=3), "midday-consolidation"),
            _row(NOW - timedelta(hours=1), "morning-briefing", outcome="skipped_budget"),
        ],
    )
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failed_today": "yellow"}


def test_yesterdays_failure_is_not_todays(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=13), "morning-briefing", code=1),  # 23:00 the day before
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    assert health.evaluate(v, now=NOW).severity == "green"


def test_the_vault_timezone_decides_where_today_starts(tmp_path):
    # 23:00 UTC the day before is 02:00 today in Kyiv (UTC+3 in October).
    v = _vault(tmp_path, "Europe/Kyiv")
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=13), "overnight-research", code=1),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    assert "overnight-research (at 02:00" in _today(health.evaluate(v, now=NOW)).summary


def test_several_slots_failed_today_are_one_finding(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=9), "overnight-research", code=1, klass="network"),
            _row(NOW - timedelta(hours=8), "overnight-research", code=1, klass="network"),
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="unknown"),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    (finding,) = health.evaluate(v, now=NOW).findings
    assert finding.summary.startswith(
        "2 schedules failed today and haven't finished a run since: "
        "overnight-research (at 04:00, network), morning-briefing (at 08:00, unknown)."
    )
    assert finding.since == NOW - timedelta(hours=9) and "each schedule" in finding.fix


def test_a_slot_in_the_trailing_streak_is_left_to_runs_failing(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5), "overnight-research", code=1),
            _row(NOW - timedelta(hours=4), "midday-consolidation"),
            _row(NOW - timedelta(hours=1), "morning-briefing", code=1),
        ],
    )
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"runs.failing": "yellow", "runs.failed_today": "yellow"}
    summary = _today(report).summary
    assert "overnight-research" in summary and "morning-briefing" not in summary


def test_only_the_streak_is_reported_when_every_failure_is_in_it(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5), "midday-consolidation"),
            _row(NOW - timedelta(hours=1), "morning-briefing", code=1),
        ],
    )
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "yellow"}


def test_a_failure_today_quotes_its_own_error_line(tmp_path):
    v = _vault(tmp_path)
    failed = _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="network")
    (v / ".scout-logs" / failed["log"]).write_text("API Error: Connection error.\n", encoding="utf-8")
    _ledger(v, [failed, _row(NOW - timedelta(hours=1), "midday-consolidation")])
    finding = _today(health.evaluate(v, now=NOW))
    assert finding.detail == "API Error: Connection error."
    assert finding.log is not None and finding.log.endswith(failed["log"])


def test_the_newest_failure_of_a_slot_speaks_for_it(tmp_path):
    v = _vault(tmp_path)
    older = _row(NOW - timedelta(hours=6), "morning-briefing", code=1, klass="network")
    newer = _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="unknown")
    (v / ".scout-logs" / older["log"]).write_text("API Error: Connection error.\n", encoding="utf-8")
    (v / ".scout-logs" / newer["log"]).write_text("no recognisable line\n", encoding="utf-8")
    _ledger(v, [older, newer, _row(NOW - timedelta(hours=1), "midday-consolidation")])
    finding = _today(health.evaluate(v, now=NOW))
    assert finding.summary.startswith("morning-briefing (at 08:00, unknown) failed today")
    assert finding.since == NOW - timedelta(hours=6)  # since its first failure today
    assert finding.log is not None and finding.log.endswith(newer["log"])
    assert newer["log"] in finding.fix and finding.detail is None


def _schedule(vault: Path, *slots: str) -> None:
    lines = ["schema_version: 1", "slots:"]
    for slot in slots:
        lines += [
            f"  {slot}:",
            "    type: briefing",
            "    runner: run-scout.sh",
            '    fires_at_local: "08:00"',
            "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]",
            "    missed_window_hours: 4",
            "    on_miss: fire",
            "    cooldown_minutes: 0",
        ]
    (vault / ".scout-state").mkdir(exist_ok=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_manual_run_that_failed_clears_on_any_later_finished_run(tmp_path):
    # No readable schedule: only the hand-run trigger says no schedule runs it.
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1, trigger="manual"),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    assert health.FAILED_TODAY_FINDING_ID not in _ids(health.evaluate(v, now=NOW))


def test_a_run_now_failure_of_a_schedule_waits_for_that_schedule(tmp_path):
    # fire-now records `trigger: manual` with the schedule's own slot: it is still that schedule's
    # run, so another slot's success doesn't clear it, and its own next success does.
    v = _vault(tmp_path)
    _schedule(v, "morning-briefing", "midday-consolidation")
    forced = _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="unknown", trigger="manual")
    _ledger(v, [forced, _row(NOW - timedelta(hours=1), "midday-consolidation")])
    finding = _today(health.evaluate(v, now=NOW))
    assert finding.summary.startswith("morning-briefing (at 08:00, unknown) failed today")

    _ledger(
        v,
        [
            forced,
            _row(NOW - timedelta(hours=2), "midday-consolidation"),
            _row(NOW - timedelta(hours=1), "morning-briefing", trigger="schedule"),
        ],
    )
    assert health.FAILED_TODAY_FINDING_ID not in _ids(health.evaluate(v, now=NOW))


def test_a_slot_the_schedule_doesnt_run_clears_on_any_later_finished_run(tmp_path):
    # A trigger's skill or `research-manual`: no schedule will run it next.
    v = _vault(tmp_path)
    _schedule(v, "morning-briefing", "midday-consolidation")
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5), "research-manual", code=1),
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    finding = _today(health.evaluate(v, now=NOW))
    assert "research-manual" not in finding.summary
    assert finding.summary.startswith("morning-briefing (at 08:00) failed today and hasn't finished a run since.")


def test_a_manual_failure_nothing_has_followed_is_the_streaks(tmp_path):
    v = _vault(tmp_path)
    _schedule(v, "morning-briefing")
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=3), "morning-briefing"),
            _row(NOW - timedelta(hours=1), "dreaming-manual", code=1),
            _row(NOW - timedelta(minutes=30), "morning-briefing", outcome="skipped_budget"),
        ],
    )
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "yellow"}


def test_a_slot_runs_recurring_names_is_one_finding_not_two(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=40), "morning-briefing", code=1, klass="network"),
            _row(NOW - timedelta(hours=38), "midday-consolidation"),
            _row(NOW - timedelta(hours=20), "morning-briefing", code=1, klass="network"),
            _row(NOW - timedelta(hours=18), "midday-consolidation"),
            _row(NOW - timedelta(hours=4), "morning-briefing", code=1, klass="network"),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"runs.recurring": "yellow"}
    (finding,) = report.findings
    assert finding.summary.endswith(
        "Still failing today, with no finished run since: morning-briefing (at 08:00, network)."
    )


def test_while_recurring_fires_it_names_every_slot_still_failing_today(tmp_path):
    # runs.recurring counts every failure up to the last success, so it holds all of today's too.
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=40), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=38), "midday-consolidation"),
            _row(NOW - timedelta(hours=20), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=18), "midday-consolidation"),
            _row(NOW - timedelta(hours=5), "morning-briefing", code=1),
            _row(NOW - timedelta(hours=4), "overnight-research", code=1),
            _row(NOW - timedelta(hours=1), "midday-consolidation"),
        ],
    )
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"runs.recurring": "yellow"}
    assert report.findings[0].summary.endswith(
        "Still failing today, with no finished run since: morning-briefing (at 07:00), overnight-research (at 08:00)."
    )

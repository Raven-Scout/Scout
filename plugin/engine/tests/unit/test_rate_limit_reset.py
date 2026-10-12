"""scout.scripts.rate_limit_reset — when does a Claude usage limit lift?

Claude Code ends a run that hits its quota with "You've hit your session limit ·
resets <time>" (also "weekly limit", "resets Mon 9am", and an optional
"(Area/City)" zone). The budget gate and run health read the reset time from the
log, so they wait for exactly that long instead of a fixed back-off (#407).
Anything that doesn't parse returns None, and callers keep the old behaviour.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.rate_limit_reset import find_reset, reset_from_log

PRAGUE = ZoneInfo("Europe/Prague")
NEW_YORK = ZoneInfo("America/New_York")

# Monday 2026-01-05, 12:00 UTC (13:00 in Prague, 07:00 in New York).
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "You've hit your session limit · resets 2:10pm (Europe/Prague)\n", _utc(2026, 1, 5, 13, 10), id="named-zone"
        ),
        pytest.param(
            "You've hit your session limit · resets 6pm (America/New_York)\n", _utc(2026, 1, 5, 23, 0), id="hour-only"
        ),
        pytest.param(
            "You've hit your weekly limit · resets 2pm (Europe/Prague)\n", _utc(2026, 1, 5, 13, 0), id="weekly"
        ),
        pytest.param(
            "You've hit your session limit · resets 12am (Europe/Prague)\n", _utc(2026, 1, 5, 23, 0), id="midnight"
        ),
        pytest.param(
            "You've hit your session limit · resets 12pm (America/New_York)\n", _utc(2026, 1, 5, 17, 0), id="noon"
        ),
        pytest.param(
            "You've hit your session limit · resets 16:30 (Europe/Prague)\n", _utc(2026, 1, 5, 15, 30), id="24h-clock"
        ),
        pytest.param(
            "Session limit reached ∙ RESETS 3PM (Europe/Prague)\n", _utc(2026, 1, 5, 14, 0), id="case-and-bullet"
        ),
    ],
)
def test_a_reset_time_in_a_named_zone_is_read(text: str, expected: datetime) -> None:
    assert find_reset(text, now=NOW) == expected


def test_a_time_earlier_today_means_tomorrow() -> None:
    # 09:00 in Prague passed four hours ago; the limit lifts tomorrow at 09:00.
    assert find_reset("You've hit your session limit · resets 9am (Europe/Prague)", now=NOW) == _utc(2026, 1, 6, 8, 0)


def test_a_time_that_passed_minutes_ago_is_today() -> None:
    """The log is read after the run ends. A reset that passed while the run
    wound down has happened; reading it as tomorrow would hold the next run back
    for a day."""
    now = _utc(2026, 1, 5, 13, 25)  # 14:25 in Prague
    assert find_reset("You've hit your session limit · resets 2:10pm (Europe/Prague)", now=now) == _utc(
        2026, 1, 5, 13, 10
    )


def test_a_weekday_names_the_next_such_day() -> None:
    # NOW is Monday 07:00 in New York, so "Thu 9am" is this Thursday.
    text = "You've hit your weekly limit · resets Thu 9am (America/New_York)"
    assert find_reset(text, now=NOW) == _utc(2026, 1, 8, 14, 0)


def test_todays_weekday_already_passed_means_next_week() -> None:
    text = "You've hit your weekly limit · resets Mon 5am (America/New_York)"
    assert find_reset(text, now=NOW) == _utc(2026, 1, 12, 10, 0)


def test_a_full_weekday_name_is_read() -> None:
    text = "You've hit your weekly limit · resets Wednesday 9:30am (America/New_York)"
    assert find_reset(text, now=NOW) == _utc(2026, 1, 7, 14, 30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "You've hit your weekly limit · resets Oct 15 at 2pm (Europe/Prague)",
            _utc(2026, 10, 15, 12, 0),
            id="month-day",
        ),
        pytest.param(
            "You've hit your weekly limit · resets 15 Oct 2pm (Europe/Prague)",
            _utc(2026, 10, 15, 12, 0),
            id="day-month",
        ),
        pytest.param(
            "You've hit your weekly limit · resets October 15th, 2pm (Europe/Prague)",
            _utc(2026, 10, 15, 12, 0),
            id="full-month-ordinal",
        ),
    ],
)
def test_a_calendar_date_in_the_weekly_limit_is_read(text: str, expected: datetime) -> None:
    assert find_reset(text, now=NOW) == expected


def test_a_calendar_date_that_passed_this_year_means_next_year() -> None:
    # NOW is 5 January 2026, so 2 January has passed; the limit lifts on 2 January 2027.
    text = "You've hit your weekly limit · resets Jan 2 at 9am (America/New_York)"
    assert find_reset(text, now=NOW) == _utc(2027, 1, 2, 14, 0)


def test_an_impossible_calendar_date_is_none() -> None:
    text = "You've hit your weekly limit · resets Feb 30 at 9am (Europe/Prague)"
    assert find_reset(text, now=NOW) is None


def test_the_reset_lands_on_the_far_side_of_a_dst_change() -> None:
    """Prague leaves summer time on Sunday 2026-10-25: Monday 09:00 is UTC+1."""
    friday = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    text = "You've hit your weekly limit · resets Mon 9am (Europe/Prague)"
    assert find_reset(text, now=friday) == _utc(2026, 10, 26, 8, 0)


def test_without_a_zone_the_reset_is_in_the_given_default_zone() -> None:
    text = "You've hit your session limit · resets 3pm"
    assert find_reset(text, now=NOW, default_zone=PRAGUE) == _utc(2026, 1, 5, 14, 0)


def test_without_a_zone_or_default_the_reset_is_in_local_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code prints the reset in the machine's local time, the same
    machine the runner reads the log on."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert find_reset("You've hit your session limit · resets 3pm", now=NOW) == _utc(2026, 1, 5, 20, 0)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_the_last_reset_in_the_log_wins() -> None:
    text = (
        "You've hit your session limit · resets 2pm (Europe/Prague)\n"
        "=== Claude retry attempt 2/2 ===\n"
        "You've hit your session limit · resets 5pm (Europe/Prague)\n"
    )
    assert find_reset(text, now=NOW) == _utc(2026, 1, 5, 16, 0)


def test_an_unreadable_newest_reset_does_not_fall_back_to_an_older_one() -> None:
    text = (
        "You've hit your session limit · resets 2pm (Europe/Prague)\n"
        "You've hit your session limit · resets 3pm (Mars/Olympus_Mons)\n"
    )
    assert find_reset(text, now=NOW) is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("API Error: 429 Too Many Requests\n", id="no-reset-named"),
        pytest.param("You've hit your session limit · resets soon\n", id="no-time"),
        pytest.param("You've hit your session limit · resets in 3 hours\n", id="relative"),
        pytest.param("You've hit your session limit · resets 3\n", id="bare-number"),
        pytest.param("You've hit your session limit · resets 13pm\n", id="hour-out-of-range-12h"),
        pytest.param("You've hit your session limit · resets 0am\n", id="zero-hour-12h"),
        pytest.param("You've hit your session limit · resets 24:00\n", id="hour-out-of-range-24h"),
        pytest.param("You've hit your session limit · resets 9:75am\n", id="minute-out-of-range"),
        pytest.param("You've hit your session limit · resets 3pm (Mars/Olympus_Mons)\n", id="unknown-zone"),
        pytest.param("The counter resets 3pm daily\n", id="resets-without-a-limit"),
    ],
)
def test_an_unreadable_reset_is_none(text: str) -> None:
    assert find_reset(text, now=NOW, default_zone=PRAGUE) is None


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        find_reset("resets 3pm", now=datetime(2026, 1, 5, 12, 0))  # noqa: DTZ001 — the point of the test


# ---------- reading a log ----------


def test_reset_from_log_reads_the_tail(tmp_path: Path) -> None:
    log = tmp_path / "scout-2026-01-05_13-00.log"
    log.write_text("x\n" * 5000 + "You've hit your session limit · resets 2:10pm (Europe/Prague)\n", encoding="utf-8")
    assert reset_from_log(log, now=NOW) == _utc(2026, 1, 5, 13, 10)


def test_reset_from_log_ignores_a_reset_long_before_the_tail(tmp_path: Path) -> None:
    """Only the end of the log speaks for how the run ended; an old line far up
    (a quoted message, an earlier attempt) must not set the back-off."""
    log = tmp_path / "scout-2026-01-05_13-00.log"
    log.write_text("You've hit your session limit · resets 2:10pm (Europe/Prague)\n" + "x" * 30000, encoding="utf-8")
    assert reset_from_log(log, now=NOW) is None


def test_reset_from_a_missing_log_is_none(tmp_path: Path) -> None:
    assert reset_from_log(tmp_path / "nope.log", now=NOW) is None


# ---------- scoutctl budget resets-at ----------


def test_cli_prints_the_reset_as_epoch_seconds(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("You've hit your session limit · resets 2:10pm (Europe/Prague)\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["budget", "resets-at", str(log), "--now", str(int(NOW.timestamp()))])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(int(_utc(2026, 1, 5, 13, 10).timestamp()))


def test_cli_prints_iso_on_request(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("You've hit your session limit · resets 2:10pm (Europe/Prague)\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["budget", "resets-at", str(log), "--iso", "--now", str(int(NOW.timestamp()))])

    assert result.output.strip() == "2026-01-05T13:10:00Z"


def test_cli_defaults_now_to_the_clock(tmp_path: Path) -> None:
    soon = datetime.now(UTC) + timedelta(hours=2)
    log = tmp_path / "run.log"
    log.write_text(f"You've hit your session limit · resets {soon:%H:%M} (UTC)\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["budget", "resets-at", str(log)])

    assert result.exit_code == 0, result.output
    assert int(result.output) == int(soon.replace(second=0, microsecond=0).timestamp())


def test_cli_exits_1_silently_when_there_is_no_reset(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("Traceback: KeyError 'title'\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["budget", "resets-at", str(log)])

    assert result.exit_code == 1
    assert result.output == ""

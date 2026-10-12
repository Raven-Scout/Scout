"""When does a Claude usage limit lift? Read it from the run's own log (#407).

Claude Code ends a run that hits its quota with a line that names the reset:

    You've hit your session limit · resets 2:10pm (Europe/Prague)
    You've hit your weekly limit · resets Mon 9am

A session limit lifts hours later, so a fixed back-off is wrong both ways: two
hours is too short for a five-hour window and too long for a limit that lifts in
ten minutes. The runner records the parsed time (``scoutctl budget resets-at``)
in the usage tracker and the run ledger, the budget gate waits exactly until
then, and run health says when Scout resumes.

The zone in parentheses is optional; without it the time is the machine's local
time, which is what Claude Code prints and where the runner reads the log.
Anything this can't read returns None, and every caller keeps its old behaviour.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Same anchor as the `limit.{0,20}resets` pattern in rate-limit-detect.sh and
# run-outcome.sh, so a "resets" that isn't a quota message never counts.
_RESET_RE = re.compile(
    r"limit\b.{0,20}?\bresets\s+"
    r"(?:(?P<dow>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\b\.?)?"
    r"(?:\s*\((?P<zone>[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*)\))?",
    re.IGNORECASE,
)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# The log is read after the run winds down, so a reset that passed a little
# while ago has happened: it is today, not tomorrow (or next week).
_ALREADY_PASSED_GRACE = timedelta(hours=1)

# Matches run-outcome.sh's classify window: the tail decides how the run ended.
LOG_TAIL_BYTES = 20000


def _wall_clock(match: re.Match[str]) -> tuple[int, int] | None:
    """(hour, minute) on a 24-hour clock, or None when the time is not a time."""
    hour = int(match["hour"])
    minute = int(match["minute"]) if match["minute"] else 0
    ampm = (match["ampm"] or "").lower().replace(".", "")
    if not 0 <= minute <= 59:
        return None
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif match["minute"] is None or hour > 23:
        # "resets 3" is not a time; "resets 15:00" is.
        return None
    return hour, minute


def _attach(naive: datetime, zone: tzinfo | None) -> datetime:
    # A naive datetime's .astimezone() reads it as local time, DST included.
    return naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()


def _resolve(match: re.Match[str], *, now: datetime, default_zone: tzinfo | None) -> datetime | None:
    clock = _wall_clock(match)
    if clock is None:
        return None
    zone = default_zone  # None: the machine's local time
    if match["zone"]:
        try:
            zone = ZoneInfo(match["zone"])
        except (ZoneInfoNotFoundError, ValueError):
            return None
    local_now = now.astimezone(zone) if zone is not None else now.astimezone()
    # Step in wall-clock days, then attach the zone, so a DST change between now
    # and the reset lands on the right UTC instant.
    day = local_now.replace(tzinfo=None, hour=clock[0], minute=clock[1], second=0, microsecond=0)
    step = timedelta(days=1)
    if match["dow"]:
        step = timedelta(days=7)
        day += timedelta(days=(_WEEKDAYS.index(match["dow"][:3].lower()) - day.weekday()) % 7)
    reset = _attach(day, zone)
    if reset < now - _ALREADY_PASSED_GRACE:
        reset = _attach(day + step, zone)
    return reset.astimezone(UTC)


def find_reset(text: str, *, now: datetime, default_zone: tzinfo | None = None) -> datetime | None:
    """The UTC instant the newest usage-limit message in ``text`` says the limit lifts.

    ``now`` (timezone-aware) anchors a bare time to its next occurrence; a time
    that passed within the last hour is taken as already past. ``default_zone``
    applies when the message names none (None: the machine's local time).
    Only the newest message counts: an earlier one may describe a limit that has
    since lifted. Returns None when it names no time this can read.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    matches = list(_RESET_RE.finditer(text))
    return _resolve(matches[-1], now=now, default_zone=default_zone) if matches else None


def reset_from_log(log: Path, *, now: datetime) -> datetime | None:
    """:func:`find_reset` over the tail of a run log; None if it can't be read."""
    try:
        with log.open("rb") as fh:
            size = log.stat().st_size
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    return find_reset(text, now=now)


__all__ = ["LOG_TAIL_BYTES", "find_reset", "reset_from_log"]

"""The self-update e2e harness's zone picks (scripts/e2e/self-update/e2e.py).

The harness fakes no clock: it picks a real zone whose local time opens or closes the updater's
gates. Zone offsets are whole hours or :30/:45, so a single window narrower than an hour has no
matching zone for part of every hour (Raven-Scout/Scout#530: 20:12 UTC, window 21:20-21:55).
These tests sweep every minute of a week and check each pick against the engine's own gates
and the default schedule a fresh install gets.
"""

from __future__ import annotations

import functools
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.schedule import load_schedule, next_fire_per_slot
from scout.scripts import heartbeat
from scout.scripts.self_update_run import SLOT_LOOKAHEAD

REPO = Path(__file__).resolve().parents[4]
SCHEDULE = load_schedule(REPO / "plugin/engine/scout/defaults/schedule.yaml")
OFF_PEAK = heartbeat.HeartbeatConfig()
# A Monday. One week reaches every weekday in every zone. The season does not matter: the
# harness tries the fixed-offset Etc/GMT zones first, and they always hold a match.
WEEK_START = datetime(2026, 10, 26, tzinfo=UTC)


def _harness():
    spec = importlib.util.spec_from_file_location("e2e_self_update", REPO / "scripts/e2e/self-update/e2e.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _slot_due_at(weekday: int, hour: int, minute: int) -> bool:
    # The lookahead is local wall-clock math, so the answer depends on these three alone;
    # the sweep asks ~30,000 times for a few hundred distinct local times.
    local = datetime(2026, 10, 26, hour, minute, tzinfo=UTC) + timedelta(days=weekday)  # the 26th is a Monday
    return bool(next_fire_per_slot(SCHEDULE, now=local, window_hours=SLOT_LOOKAHEAD.total_seconds() / 3600))


def _slot_due(local: datetime) -> bool:
    return _slot_due_at(local.weekday(), local.hour, local.minute)


def _minutes(start: datetime, days: int):
    for i in range(days * 24 * 60):
        yield start + timedelta(minutes=i)


def test_the_minute_from_the_issue_finds_a_slot_due_zone():
    now = datetime(2026, 10, 27, 20, 12, tzinfo=UTC)
    h = _harness()
    local = now.astimezone(h._zone(h.slot_due_zone(now)))
    assert _slot_due(local)


def test_every_minute_has_a_slot_due_zone_the_updater_defers_in():
    h = _harness()
    for now in _minutes(WEEK_START, 7):
        try:
            zone = h.slot_due_zone(now)
        except SystemExit as e:
            pytest.fail(f"{now:%a %H:%M} UTC: {e}")
        local = now.astimezone(h._zone(zone))
        where = f"{now:%a %H:%M} UTC in {zone} ({local:%a %H:%M})"
        assert _slot_due(local), f"no slot due within 45 min at {where}"
        # Still true two minutes on, when the updater has started and read the clock itself.
        assert _slot_due(local + timedelta(minutes=2)), f"no slot due 2 min after {where}"
        assert not heartbeat.in_off_peak(local.hour, OFF_PEAK), f"off-peak at {where}"


def test_every_minute_has_a_quiet_zone_with_the_gates_open():
    h = _harness()
    for now in _minutes(WEEK_START, 7):
        try:
            zone = h.quiet_zone(now)
        except SystemExit as e:
            pytest.fail(f"{now:%a %H:%M} UTC: {e}")
        local = now.astimezone(h._zone(zone))
        where = f"{now:%a %H:%M} UTC in {zone} ({local:%a %H:%M})"
        assert heartbeat.in_off_peak(local.hour, OFF_PEAK), f"not off-peak at {where}"
        assert not _slot_due(local), f"a slot is due within 45 min at {where}"

"""The verdict engine (spec §7, §7a): before/after windows per change and the rules that judge them.

Signals are built in memory; only the settings tests touch files. Days are local calendar dates in Prague."""

from __future__ import annotations

import json
import stat
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scout.changes.models import Change, Signal, Why
from scout.changes.settings import AUTO_MODES, Settings, read_settings, write_settings
from scout.changes.store import owner_dir
from scout.changes.verdicts import (
    BASELINE_DAYS,
    TARGETED_REASONS,
    Window,
    evaluate,
    judge,
    window,
)

TZ = ZoneInfo("Europe/Prague")
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
# A change on this local day: baseline 10-06..10-19, review window 10-20..11-02, review on 11-03 (before NOW).
DAY = date(2026, 10, 20)
CID = "chg-00000000000a"


def _at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def _change(
    cid: str = CID,
    day: date = DAY,
    *,
    kind: str = "brain-edit",
    lane: str = "dreaming",
    targets: tuple[str, ...] = (),
    backfilled: bool = False,
    reverts: str | None = None,
    hour: int = 12,
) -> Change:
    return Change(
        id=cid,
        ts=_at(day, hour),
        kind=kind,
        summary="a change",
        why=Why("unknown", "", inferred=True),
        commit=cid[4:] + "0" * 28,
        files=("SKILL.md",),
        targets=targets,
        author_lane=lane,
        backfilled=backfilled,
        reverts=reverts,
    )


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _baseline_days() -> list[date]:
    return _days(DAY - timedelta(days=BASELINE_DAYS), BASELINE_DAYS)


def _review_days(n: int = 14) -> list[date]:
    return _days(DAY, n)


def _away(day: date) -> Signal:
    return Signal(ts=_at(day, 7), kind="owner-away", surface="calendar", ref=day.isoformat())


def _recurrence(day: date, pattern: str = "pattern-288") -> Signal:
    return Signal(ts=_at(day, 9), kind="recurrence", surface="mistake-audit", ref=f"{pattern}@{day.isoformat()}")


def _reply(day: date, hour: int = 11, minute: int = 0, **kw) -> Signal:
    return Signal(ts=_at(day, hour, minute), kind="dm-reply", surface="slack-dm", **kw)


def _test(changes: list[Change], signals: list[Signal], *, now: datetime = NOW, settings: Settings | None = None):
    return evaluate(changes, sorted(signals, key=lambda s: s.ts), now=now, tz=TZ, settings=settings or Settings())


def _w(engagement: float = 0.0, noise: float = 0.0, recurrences: dict | None = None, away: int = 0) -> Window:
    return Window(days=14, engagement=engagement, noise=noise, recurrences=recurrences or {}, away_days=away)


def test_cant_tell_when_away_more_than_half_a_window():
    away = [_away(d) for d in _review_days()[:8]]
    test = _test([_change()], away)[CID]
    assert test["state"] == "done"
    assert test["verdict"] == "can't tell" and test["reasons"] == ["away"]
    assert test["after"]["away_days"] == 8 and test["baseline"]["away_days"] == 0
    # Exactly half is not more than half; a duplicated away row for one day counts once.
    seven = [_away(d) for d in _review_days()[:7]] + [_away(DAY)]
    assert _test([_change()], seven)[CID]["verdict"] == "no clear effect"
    # Away in the baseline counts too.
    assert _test([_change()], [_away(d) for d in _baseline_days()[:8]])[CID]["verdict"] == "can't tell"


def test_low_engagement_alone_is_never_cant_tell():
    # Busy baseline (two replies a day), then silence: nothing marks the owner away, so it is never "can't tell".
    signals = [_reply(d, h) for d in _baseline_days() for h in (9, 15)]
    test = _test([_change()], signals)[CID]
    assert test["baseline"]["engagement"] == 2.0 and test["after"]["engagement"] == 0.0
    assert test["verdict"] != "can't tell" and "away" not in test["reasons"]


def test_owner_who_stopped_replying_is_hurt():
    signals: list[Signal] = []
    for d in _baseline_days():
        for i, h in enumerate((8, 9, 10)):
            sent = f"{d.toordinal()}.{i}"
            signals.append(Signal(ts=_at(d, h), kind="dm-sent", surface="slack-dm", ref=sent))
            signals.append(_reply(d, h, minute=30, ref=f"{sent}/{d.toordinal()}.{i}9"))
        signals.append(Signal(ts=_at(d, 20), kind="session-reliance", surface="kb", session=f"{d.toordinal():016x}"))
    for d in _review_days():
        for i, h in enumerate((8, 9, 10)):
            signals.append(Signal(ts=_at(d, h), kind="dm-sent", surface="slack-dm", ref=f"{d.toordinal()}.{i}"))
    test = _test([_change()], signals)[CID]
    assert test["baseline"]["engagement"] == 4.0 and test["baseline"]["noise"] == 0.0
    assert test["after"]["engagement"] == 0.0 and test["after"]["noise"] == 3.0
    assert test["verdict"] == "hurt" and test["reasons"] == ["noise-up"] and test["suspect"] is True
    # Noise went up with no targeted reason: one-at-a-time would undo it, targeted would not.
    assert test["would_have_done"] == {"targeted": False, "one_at_a_time": True}


def test_zero_noise_baseline_then_noise_is_up():
    assert judge(_w(1.0, 0.0), _w(1.0, 0.5), targets=(), named_negative=False) == ("hurt", ("noise-up",))
    # Noise up with engagement up as well is not hurt.
    assert judge(_w(1.0, 0.0), _w(1.1, 0.5), targets=(), named_negative=False) == ("no clear effect", ())
    # Up, but under 25 %.
    assert judge(_w(1.0, 1.0), _w(1.0, 1.2), targets=(), named_negative=False)[0] == "no clear effect"
    assert judge(_w(1.0, 1.0), _w(1.0, 1.25), targets=(), named_negative=False)[0] == "hurt"


def test_flat_everything_is_no_clear_effect():
    test = _test([_change()], [])[CID]
    assert test["verdict"] == "no clear effect" and test["reasons"] == [] and test["suspect"] is False
    assert test["baseline"] == {"days": 14, "engagement": 0.0, "noise": 0.0, "recurrences": {}, "away_days": 0}
    assert test["would_have_done"] == {"targeted": False, "one_at_a_time": False}
    assert test["rolled_back_by"] is None and test["overlap"] == [] and test["review_on"] == "2026-11-03"
    assert test["calibrating"] is True and test["marked_wrong"] is False and test["engagement_targeted"] is False


def test_flags_marked_wrong_engagement_targeted_and_calibrating():
    wrong = Signal(ts=_at(date(2026, 11, 10)), kind="verdict-wrong", surface="app", ref=CID)
    other = Signal(ts=_at(date(2026, 11, 10)), kind="verdict-wrong", surface="app", ref="chg-0000000000ff")
    settings = Settings(calibrated_on=date(2026, 11, 1))
    test = _test([_change(targets=("engagement",))], [wrong, other], settings=settings)[CID]
    assert test["marked_wrong"] is True and test["engagement_targeted"] is True and test["calibrating"] is False
    assert _test([_change()], [other])[CID]["marked_wrong"] is False


def test_targeted_recurrence_up_is_hurt_and_targeted():
    signals = [
        _recurrence(DAY - timedelta(days=10)),
        _recurrence(DAY + timedelta(days=2)),
        _recurrence(DAY + timedelta(days=5)),
        _recurrence(DAY + timedelta(days=6), "pattern-9"),  # not this change's target
    ]
    test = _test([_change(targets=("pattern-288", "briefing"))], signals)[CID]
    assert test["baseline"]["recurrences"] == {"pattern-288": 1} and test["after"]["recurrences"] == {"pattern-288": 2}
    assert test["verdict"] == "hurt" and test["reasons"] == ["recurrence-up"] and test["suspect"] is True
    assert test["would_have_done"]["targeted"] is True and "recurrence-up" in TARGETED_REASONS


def test_recurrences_falling_to_zero_is_helped():
    signals = [_recurrence(DAY - timedelta(days=10)), _recurrence(DAY - timedelta(days=3))]
    test = _test([_change(targets=("pattern-288",))], signals)[CID]
    assert test["verdict"] == "helped" and test["reasons"] == ["recurrence-zero"] and test["suspect"] is False
    # Only pattern-* targets count for recurrences.
    before, after = _w(recurrences={"briefing": 2}), _w(recurrences={"briefing": 0})
    assert judge(before, after, targets=("briefing",), named_negative=False) == ("no clear effect", ())
    assert judge(after, before, targets=("briefing",), named_negative=False) == ("no clear effect", ())


def test_engagement_up_15_with_noise_flat_is_helped():
    assert judge(_w(2.0, 0.5), _w(2.3, 0.5), targets=(), named_negative=False) == ("helped", ("engagement-up",))
    assert judge(_w(2.0, 0.5), _w(2.29, 0.5), targets=(), named_negative=False) == ("no clear effect", ())
    # Engagement up but noise up too: not helped.
    assert judge(_w(2.0, 0.5), _w(2.3, 0.6), targets=(), named_negative=False) == ("no clear effect", ())
    # From zero, any engagement is up.
    assert judge(_w(0.0, 0.0), _w(0.1, 0.0), targets=(), named_negative=False) == ("helped", ("engagement-up",))


def test_named_negative_reply_is_hurt():
    negative = _reply(DAY + timedelta(days=3), sentiment="negative", change=CID, ref="1.1/2.2")
    test = _test([_change()], [negative])[CID]
    assert test["verdict"] == "hurt" and test["reasons"] == ["named-negative"]
    assert test["would_have_done"]["targeted"] is True
    # Before the change, naming another change, or not negative: no effect on this verdict.
    early = _reply(DAY - timedelta(days=3), sentiment="negative", change=CID, ref="1.1/2.2")
    assert _test([_change()], [early])[CID]["verdict"] == "no clear effect"
    for kw in ({"sentiment": "negative", "change": "chg-0000000000ff"}, {"sentiment": "neutral", "change": CID}):
        assert "named-negative" not in _test([_change()], [_reply(DAY + timedelta(days=3), **kw)])[CID]["reasons"]


def test_rollback_overrides_and_clears_suspect():
    signals = [_recurrence(DAY + timedelta(days=2))]
    rollback = _change("chg-00000000000b", DAY + timedelta(days=16), kind="rollback", reverts=CID)
    tests = _test([_change(targets=("pattern-288",)), rollback], signals)
    test = tests[CID]
    assert test["verdict"] == "rolled back" and test["suspect"] is False
    assert test["rolled_back_by"] == "chg-00000000000b"
    assert test["would_have_done"] == {"targeted": False, "one_at_a_time": False}
    assert tests["chg-00000000000b"]["state"] == "not-tested"


def test_untested_lanes_kinds_and_backfill():
    untested = [
        _change("chg-000000000001", lane="interactive"),
        _change("chg-000000000002", lane="update"),
        _change("chg-000000000003", kind="rollback"),
        _change("chg-000000000004", kind="upgrade"),
        _change("chg-000000000005", backfilled=True),
    ]
    tests = _test([*untested, _change()], [_recurrence(DAY + timedelta(days=1))])
    for c in untested:
        t = tests[c.id]
        assert t["state"] == "not-tested" and t["verdict"] is None
        assert t["baseline"] is None and t["after"] is None and t["overlap"] == [] and t["suspect"] is False
    # Untested changes on the same day are left out of a tested change's overlap.
    assert tests[CID]["state"] == "done" and tests[CID]["overlap"] == []
    # With nothing tested there are no windows to count.
    assert {t["state"] for t in _test(untested, []).values()} == {"not-tested"}


def test_testing_until_review_day():
    ts = NOW - timedelta(days=13)
    change = _change(day=ts.astimezone(TZ).date(), hour=13)
    test = _test([change], [])[CID]
    assert test["state"] == "testing" and test["after"] is None and test["verdict"] is None
    assert test["review_on"] == (change.ts.astimezone(TZ).date() + timedelta(days=14)).isoformat()
    assert test["baseline"] is not None and test["suspect"] is False
    done = _test([change], [], now=change.ts + timedelta(days=14))[CID]
    assert done["state"] == "done" and done["after"] is not None and done["verdict"] == "no clear effect"
    # review_days comes from the settings.
    longer = _test([change], [], now=change.ts + timedelta(days=14), settings=Settings(review_days=21))[CID]
    assert longer["state"] == "testing"


def test_overlap_and_one_at_a_time_picks_the_newest():
    first = _change(targets=("pattern-288",))
    second = _change("chg-00000000000b", DAY + timedelta(days=3), targets=("pattern-288",))
    edge = _change("chg-00000000000c", DAY + timedelta(days=14))  # exactly review_days later: inside (inclusive)
    far = _change("chg-00000000000d", DAY + timedelta(days=15))  # one day more: outside
    signals = [_recurrence(DAY + timedelta(days=5)), _recurrence(DAY + timedelta(days=8))]
    tests = _test([first, second, edge, far], signals)
    assert tests[CID]["overlap"] == ["chg-00000000000b", "chg-00000000000c"]
    assert tests["chg-00000000000b"]["overlap"] == [CID, "chg-00000000000c", "chg-00000000000d"]
    assert tests["chg-00000000000d"]["overlap"] == ["chg-00000000000b", "chg-00000000000c"]
    # The edge change is newer than the first but still testing, so not suspect: the second is the newest suspect.
    assert tests[CID]["verdict"] == tests["chg-00000000000b"]["verdict"] == "hurt"
    assert tests[CID]["would_have_done"] == {"targeted": True, "one_at_a_time": False}
    assert tests["chg-00000000000b"]["would_have_done"] == {"targeted": True, "one_at_a_time": True}


def test_window_counts_what_the_report_counts():
    start = date(2026, 11, 2)
    signals = [
        # Pending at NOW (sent under 48 h ago, so not noise yet), and a red carried on the window's last day.
        Signal(ts=NOW - timedelta(hours=30), kind="dm-sent", surface="slack-dm", ref="9.9"),
        Signal(ts=_at(date(2026, 11, 15)), kind="red-carried", surface="action-items"),
        # Out of the window: the day before it starts.
        Signal(ts=_at(date(2026, 11, 1)), kind="red-carried", surface="action-items"),
        _away(start),
        _away(start),
        Signal(ts=_at(start), kind="owner-away", surface="calendar", ref="not-a-date"),
        Signal(ts=_at(start), kind="owner-away", surface="calendar", ref="2026-12-25"),  # outside the window
        _recurrence(start + timedelta(days=1)),
        _recurrence(start + timedelta(days=1)),  # a duplicated row counts once
    ]
    signals.append(Signal(ts=_at(start), kind="owner-away", surface="calendar"))  # no ref: no day to count
    w = window(signals, start=start, days=14, now=NOW, tz=TZ, targets=("pattern-288", "briefing"))
    assert w.days == 14 and w.noise == 1 / 14 and w.engagement == 0.0
    assert w.away_days == 1 and dict(w.recurrences) == {"pattern-288": 1}
    # Days after now have no rows yet: they count as zero, over the whole window.
    later = window(signals, start=date(2026, 11, 15), days=7, now=NOW, tz=TZ, targets=())
    assert later.noise == 1 / 7 and later.recurrences == {}


def test_settings_bad_file_reads_defaults(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert read_settings(env) == Settings()  # no file
    path = owner_dir(env) / "settings.json"
    path.parent.mkdir(parents=True)
    for text in ("garbage{", "[1, 2]", json.dumps({"schema": 2, "auto_rollback": "targeted"}), "[" * 100_000):
        path.write_text(text)
        assert read_settings(env) == Settings()
    path.write_text(json.dumps({"schema": 1, "auto_rollback": "maybe", "review_days": "x", "calibrated_on": "soon"}))
    assert read_settings(env) == Settings()
    path.write_text(json.dumps({"schema": 1, "auto_rollback": "targeted", "review_days": True}))
    assert read_settings(env) == Settings(auto_rollback="targeted")
    path.unlink()
    path.mkdir()  # unreadable: a directory where the file should be
    assert read_settings(env) == Settings()


def test_settings_round_trip_clamp_and_mode(tmp_path):
    env = {"HOME": str(tmp_path)}
    path = owner_dir(env) / "settings.json"
    write_settings(Settings(auto_rollback="one-at-a-time", review_days=21, calibrated_on=date(2026, 12, 1)), env)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_settings(env) == Settings("one-at-a-time", 21, date(2026, 12, 1))
    assert json.loads(path.read_text())["schema"] == 1
    for raw, clamped in ((3, 7), (100, 60), (7, 7), (60, 60)):
        path.write_text(json.dumps({"schema": 1, "review_days": raw}))
        assert read_settings(env).review_days == clamped
    assert AUTO_MODES == ("off", "targeted", "one-at-a-time")
    assert not list(path.parent.glob("*.tmp"))


def test_write_settings_refuses_an_unknown_mode_and_reports_a_failed_save(tmp_path):
    import pytest

    env = {"HOME": str(tmp_path)}
    with pytest.raises(ValueError):
        write_settings(Settings(auto_rollback="always"), env)
    assert not owner_dir(env).exists()
    owner_dir(env).parent.mkdir(parents=True)
    owner_dir(env).write_text("a file where the owner folder should be")
    with pytest.raises(OSError):
        write_settings(Settings(), env)

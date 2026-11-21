from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from scout.telegram_inbound import ledger as L

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


def msg(i: int, **kw) -> L.InboundMessage:
    return L.InboundMessage(
        message_id=i,
        date=1000 + i,
        text=f"m{i}",
        reply_to_text=None,
        forwarded=False,
        kind=kw.pop("kind", "text"),
        **kw,
    )


@pytest.fixture
def led(fake_data_dir: Path) -> L.Ledger:
    return L.Ledger(fake_data_dir)


def test_ledger_dir(fake_data_dir: Path) -> None:
    assert L.ledger_dir(fake_data_dir) == fake_data_dir / ".scout-state" / "telegram-inbound"


def test_add_is_idempotent_by_message_id(led: L.Ledger) -> None:
    with led.locked():
        assert led.add(msg(1)) is True
        assert led.add(msg(1)) is False
    assert led.get(1).text == "m1"
    assert led.get(99) is None


def test_mutation_requires_lock(led: L.Ledger) -> None:
    with pytest.raises(RuntimeError):
        led.add(msg(1))


def test_mark_dispatched_increments_attempt_and_sets_time(led: L.Ledger) -> None:
    with led.locked():
        led.add(msg(1))
        led.mark_dispatched([1], now=NOW)
        led.mark_dispatched([1], now=NOW)
    m = led.get(1)
    assert (m.state, m.attempt, m.dispatched_at) == (L.DISPATCHED, 2, NOW.isoformat())


def test_in_flight_excludes_dispatches_older_than_60_minutes(led: L.Ledger) -> None:
    with led.locked():
        led.add(msg(1))
        led.add(msg(2))
        led.mark_dispatched([1], now=NOW - dt.timedelta(minutes=61))
        led.mark_dispatched([2], now=NOW - dt.timedelta(minutes=59))
    assert [m.message_id for m in led.in_flight(NOW)] == [2]


def _timed_out(led: L.Ledger, attempts: int = 1) -> None:
    with led.locked():
        led.add(msg(1))
        for _ in range(attempts):
            led.mark_dispatched([1], now=NOW - dt.timedelta(minutes=90))


def test_reconcile_reverts_timed_out_dispatch_when_no_run_alive(led: L.Ledger) -> None:
    _timed_out(led)
    with led.locked():
        assert led.reconcile(now=NOW, run_alive=False) == []
    assert led.get(1).state == L.PENDING
    assert led.get(1).attempt == 1


def test_reconcile_keeps_dispatch_while_run_alive(led: L.Ledger) -> None:
    _timed_out(led)
    with led.locked():
        assert led.reconcile(now=NOW, run_alive=True) == []
    assert led.get(1).state == L.DISPATCHED


def test_reconcile_leaves_fresh_dispatch(led: L.Ledger) -> None:
    with led.locked():
        led.add(msg(1))
        led.mark_dispatched([1], now=NOW)
        led.reconcile(now=NOW, run_alive=False)
    assert led.get(1).state == L.DISPATCHED


def test_third_timeout_moves_message_to_failed_and_returns_it(led: L.Ledger) -> None:
    _timed_out(led, attempts=L.MAX_ATTEMPTS)
    with led.locked():
        failed = led.reconcile(now=NOW, run_alive=False)
    assert [m.message_id for m in failed] == [1]
    assert led.get(1).state == L.FAILED


def test_record_reply_marks_reply_to_and_covers_answered(led: L.Ledger) -> None:
    with led.locked():
        for i in (1, 2, 3):
            led.add(msg(i))
        led.mark_dispatched([1, 2, 3], now=NOW)
        led.record_reply(reply_to=3, covers=[1, 2], at=NOW)
    assert all(led.get(i).state == L.ANSWERED and led.get(i).answered_at for i in (1, 2, 3))
    row = json.loads((L.ledger_dir(led.vault) / "replies.jsonl").read_text().splitlines()[0])
    assert row == {"reply_to": 3, "covers": [1, 2], "delivered_at": NOW.isoformat()}


def test_record_reply_answers_a_message_that_was_reverted_to_pending(led: L.Ledger) -> None:
    _timed_out(led)
    with led.locked():
        led.reconcile(now=NOW, run_alive=False)
        assert led.get(1).state == L.PENDING
        led.record_reply(reply_to=1, covers=[], at=NOW)
    assert led.get(1).state == L.ANSWERED


def test_unanswered_ids_is_pending_dispatched_and_failed(led: L.Ledger) -> None:
    """A failed message (out of respond attempts) stays answerable: a scheduled run picks it up."""
    with led.locked():
        for i in range(1, 7):
            led.add(msg(i))
        led.mark_dispatched([2], now=NOW)
        led.mark_skipped([3], L.SKIPPED_CHEAP)
        led.mark_skipped([4], L.SKIPPED_UNSUPPORTED)
        led.record_reply(reply_to=5, covers=[], at=NOW)
        led.mark_skipped([6], L.FAILED)
    assert led.unanswered_ids() == {1, 2, 6}
    assert [m.message_id for m in led.unanswered()] == [1, 2, 6]
    assert [m.message_id for m in led.pending()] == [1]  # what fires a respond run: never a failed one
    assert led.counts()[L.SKIPPED_CHEAP] == 1


def test_unanswered_can_leave_out_fresh_dispatches(led: L.Ledger) -> None:
    """A dispatch inside the timeout belongs to a respond run that may be answering it right now."""
    with led.locked():
        for i in (1, 2, 3, 4):
            led.add(msg(i))
        led.mark_dispatched([2], now=NOW - dt.timedelta(minutes=59))
        led.mark_dispatched([3], now=NOW - dt.timedelta(minutes=60))
        led.mark_dispatched([4], now=NOW)
        led._update([4], lambda m: setattr(m, "dispatched_at", "not a time"))  # unreadable: stale
    assert [m.message_id for m in led.unanswered(now=NOW, exclude_in_flight=True)] == [1, 3, 4]
    assert [m.message_id for m in led.unanswered(now=NOW)] == [1, 2, 3, 4]


def test_mark_acked(led: L.Ledger) -> None:
    with led.locked():
        led.add(msg(1))
        led.mark_acked([1])
    assert led.get(1).acked is True


def test_poll_state_round_trips(led: L.Ledger) -> None:
    assert led.get_poll_state() == {}
    with led.locked():
        led.set_poll_state(offset=42, last_poll_at="2026-11-21T12:00:00+00:00", last_error=None)
    assert led.get_poll_state() == {"offset": 42, "last_poll_at": "2026-11-21T12:00:00+00:00", "last_error": None}


def _write_raw(led: L.Ledger, text: str) -> Path:
    d = L.ledger_dir(led.vault)
    d.mkdir(parents=True)
    (d / "messages.json").write_text(text)
    return d


def test_unlocked_read_of_corrupt_file_leaves_it_in_place(led: L.Ledger) -> None:
    d = _write_raw(led, "{not json")
    assert led.get(1) is None
    assert led.pending() == [] and led.counts()[L.PENDING] == 0
    assert (d / "messages.json").read_text() == "{not json"
    assert not list(d.glob("messages.json.corrupt-*"))


def test_unknown_field_in_row_is_ignored(led: L.Ledger) -> None:
    row = {
        "message_id": 1,
        "date": 1,
        "text": "x",
        "reply_to_text": None,
        "forwarded": False,
        "kind": "text",
        "future_field": 7,
    }
    d = _write_raw(led, json.dumps({"1": row}))
    assert led.get(1).text == "x"
    assert not list(d.glob("messages.json.corrupt-*"))


def test_wrong_shape_json_is_quarantined_under_lock(led: L.Ledger) -> None:
    d = _write_raw(led, "[1, 2]")
    with led.locked():
        assert led.add(msg(1)) is True
    assert list(d.glob("messages.json.corrupt-*"))


def test_non_filenotfound_oserror_propagates(led: L.Ledger) -> None:
    d = _write_raw(led, "{}")
    (d / "messages.json").chmod(0)
    try:
        if os.access(d / "messages.json", os.R_OK):
            pytest.skip("running as a user that ignores file modes")
        with pytest.raises(PermissionError):
            led.get(1)
    finally:
        (d / "messages.json").chmod(0o600)


def test_ledger_survives_corrupt_messages_json(led: L.Ledger) -> None:
    d = L.ledger_dir(led.vault)
    d.mkdir(parents=True)
    (d / "messages.json").write_text("{not json")
    with led.locked():
        assert led.add(msg(1)) is True
    assert led.get(1) is not None
    assert list(d.glob("messages.json.corrupt-*"))

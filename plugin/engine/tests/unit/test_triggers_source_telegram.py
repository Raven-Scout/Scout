"""TelegramSource: poll getUpdates into the ledger, ack, and emit at most one event per tick.

Fixtures are synthetic: a fake Bot API (``http_post``), a fake session lock and a
fixed clock. No network, no real secrets.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from scout.errors import ConfigError
from scout.telegram_inbound import ledger as ledger_mod
from scout.telegram_inbound.ledger import InboundMessage, Ledger
from scout.triggers.dispatcher import FireOutcome
from scout.triggers.sources.telegram import TelegramSource

OWNER = 424242
TOKEN = "123456:test-token"
T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.UTC)
FAILED_NOTICE = "I couldn't handle this after 3 tries — I'll pick it up in the next scheduled run."
UNSUPPORTED_NOTICE = "I can only read text messages for now."


class FakeBot:
    """Scriptable Bot API: a queue of updates (confirmed by offset) plus call recording."""

    def __init__(self, *, reaction_ok: bool = True, fail_get_updates: bool = False) -> None:
        self.updates: list[dict[str, Any]] = []
        self.reaction_ok = reaction_ok
        self.fail_get_updates = fail_get_updates
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> dict:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        if method == "getUpdates":
            if self.fail_get_updates:
                raise RuntimeError("connection reset")
            return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= json["offset"]]}
        if method == "setMessageReaction":
            return {"ok": self.reaction_ok, "result": True, "description": "reactions disabled"}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 9000 + len(self.calls)}}
        raise AssertionError(f"unexpected method {method}")

    def push(self, update_id: int, message_id: int, text: str | None = "please summarise the plan", **extra) -> None:
        message: dict[str, Any] = {
            "message_id": message_id,
            "date": 1_790_000_000 + message_id,
            "chat": {"id": OWNER, "type": "private"},
            "from": {"id": OWNER, "is_bot": False},
            **extra,
        }
        if text is not None:
            message["text"] = text
        self.updates.append({"update_id": update_id, "message": message})

    def methods(self, name: str) -> list[dict[str, Any]]:
        return [payload for method, payload in self.calls if method == name]


class Clock:
    def __init__(self, now: dt.datetime = T0) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now = self.now + dt.timedelta(**kw)


class Lock:
    def __init__(self, held: bool = False) -> None:
        self.held = held

    def __call__(self) -> bool:
        return self.held


def _source(vault: Path, bot: FakeBot, *, lock: Lock | None = None, clock: Clock | None = None, chat_id: int = OWNER):
    return TelegramSource(
        vault=vault,
        credentials=lambda: (TOKEN, chat_id),
        http_post=bot,
        lock_held=lock or Lock(),
        clock=clock or Clock(),
    )


def _ok(event_id: str) -> FireOutcome:
    return FireOutcome(trigger_id="telegram-respond", event_id=event_id, action_kind="run_skill", status="ok")


def _state(vault: Path, message_id: int) -> str:
    msg = Ledger(vault).get(message_id)
    assert msg is not None
    return msg.state


# ----- ack paths ---------------------------------------------------------------------


def test_new_message_is_acked_with_reaction_and_returns_one_event(fake_data_dir: Path):
    bot = FakeBot()
    bot.push(100, 5)
    events = _source(fake_data_dir, bot).scan_since("ignored")

    assert len(events) == 1
    ev = events[0]
    assert ev.source == "telegram"
    assert ev.source_event_id == "5:1"
    assert ev.normalized_match_fields == {"type": "message"}
    assert [m["message_id"] for m in ev.raw_payload["messages"]] == [5]

    reactions = bot.methods("setMessageReaction")
    assert len(reactions) == 1
    assert reactions[0]["message_id"] == 5
    assert reactions[0]["reaction"] == [{"type": "emoji", "emoji": "\U0001f440"}]
    assert bot.methods("sendMessage") == []

    msg = Ledger(fake_data_dir).get(5)
    assert msg is not None and msg.acked is True and msg.state == ledger_mod.PENDING


def test_reaction_failure_falls_back_to_on_it_reply(fake_data_dir: Path):
    bot = FakeBot(reaction_ok=False)
    bot.push(100, 5)
    _source(fake_data_dir, bot).scan_since("ignored")

    replies = bot.methods("sendMessage")
    assert len(replies) == 1
    assert replies[0]["text"] == "On it"
    assert replies[0]["reply_parameters"]["message_id"] == 5
    msg = Ledger(fake_data_dir).get(5)
    assert msg is not None and msg.acked is True


def test_cheap_message_is_skipped_without_ack_or_event(fake_data_dir: Path):
    bot = FakeBot()
    bot.push(100, 5, text="thanks!")
    events = _source(fake_data_dir, bot).scan_since("ignored")

    assert events == []
    assert _state(fake_data_dir, 5) == ledger_mod.SKIPPED_CHEAP
    assert bot.methods("setMessageReaction") == []
    assert bot.methods("sendMessage") == []


def test_ok_replying_to_a_scout_question_starts_a_run(fake_data_dir: Path):
    bot = FakeBot()
    bot.push(100, 5, text="ok", reply_to_message={"message_id": 4, "text": "Shall I open the draft PR?"})
    bot.push(101, 6, text="/start")
    (event,) = _source(fake_data_dir, bot).scan_since("ignored")

    assert [m["message_id"] for m in event.raw_payload["messages"]] == [5]
    assert _state(fake_data_dir, 5) == ledger_mod.PENDING
    assert _state(fake_data_dir, 6) == ledger_mod.SKIPPED_CHEAP
    assert [r["message_id"] for r in bot.methods("setMessageReaction")] == [5]


def test_unsupported_message_gets_one_plain_reply_and_no_run(fake_data_dir: Path):
    """A photo with no caption: one plain reply, no reaction, no event, and not a cheap skip
    (is_cheap("") is True, so the unsupported check must come first)."""
    bot = FakeBot()
    bot.push(100, 5, text=None, photo=[{"file_id": "x"}])
    src = _source(fake_data_dir, bot)
    events = src.scan_since("ignored")

    assert events == []
    assert _state(fake_data_dir, 5) == ledger_mod.SKIPPED_UNSUPPORTED
    replies = bot.methods("sendMessage")
    assert [r["text"] for r in replies] == [UNSUPPORTED_NOTICE]
    assert bot.methods("setMessageReaction") == []

    # A re-read of the same update (offset already advanced, so none) never re-replies.
    assert src.scan_since("ignored") == []
    assert len(bot.methods("sendMessage")) == 1


def test_message_from_someone_else_is_dropped(fake_data_dir: Path):
    bot = FakeBot()
    bot.updates.append(
        {
            "update_id": 100,
            "message": {"message_id": 5, "chat": {"id": 7, "type": "private"}, "from": {"id": 7}, "text": "hi"},
        }
    )
    assert _source(fake_data_dir, bot).scan_since("ignored") == []
    assert Ledger(fake_data_dir).get(5) is None
    assert Ledger(fake_data_dir).get_poll_state()["offset"] == 101


# ----- offset and errors --------------------------------------------------------------


def test_offset_advances_only_after_ledger_write(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    bot = FakeBot()
    bot.push(100, 5)
    bot.push(101, 6)

    def boom(self, msg):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(Ledger, "add", boom)
    with pytest.raises(OSError):
        _source(fake_data_dir, bot).scan_since("ignored")
    assert Ledger(fake_data_dir).get_poll_state() == {}

    monkeypatch.undo()
    _source(fake_data_dir, bot).scan_since("ignored")
    assert Ledger(fake_data_dir).get(5) is not None and Ledger(fake_data_dir).get(6) is not None
    assert Ledger(fake_data_dir).get_poll_state()["offset"] == 102

    # The next poll confirms everything up to the stored offset.
    _source(fake_data_dir, bot).scan_since("ignored")
    assert [p["offset"] for p in bot.methods("getUpdates")] == [0, 0, 102]


def test_malformed_update_is_skipped_and_offset_advances(fake_data_dir: Path):
    """One poison update (parse_message raises) must never block every future tick."""
    bot = FakeBot()
    # Owner's private chat but no message_id: parse_message raises KeyError.
    bot.updates.append({"update_id": 100, "message": {"chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}}})
    bot.push(101, 6)

    events = _source(fake_data_dir, bot).scan_since("ignored")

    assert [m["message_id"] for m in events[0].raw_payload["messages"]] == [6]
    assert Ledger(fake_data_dir).get_poll_state()["offset"] == 102


def test_api_error_leaves_ledger_and_offset_untouched(fake_data_dir: Path):
    bot = FakeBot()
    bot.push(100, 5)
    src = _source(fake_data_dir, bot)
    src.scan_since("ignored")
    before = Ledger(fake_data_dir)._messages.read_text(encoding="utf-8")

    bot.push(101, 6)
    bot.fail_get_updates = True
    assert src.scan_since("ignored") == []

    assert Ledger(fake_data_dir)._messages.read_text(encoding="utf-8") == before
    state = Ledger(fake_data_dir).get_poll_state()
    assert state["offset"] == 101
    assert state["last_error"] and "connection reset" in state["last_error"]
    assert TOKEN not in state["last_error"]


def test_a_successful_poll_after_an_api_error_clears_it(fake_data_dir: Path):
    """A transient error must not wedge the source: the next tick polls again and recovers."""
    bot = FakeBot(fail_get_updates=True)
    src = _source(fake_data_dir, bot)
    src.scan_since("ignored")
    assert Ledger(fake_data_dir).get_poll_state()["last_error"]

    healthy, _reason = src.health_check()
    assert healthy is True

    bot.fail_get_updates = False
    bot.push(100, 5)
    assert len(src.scan_since("ignored")) == 1
    assert Ledger(fake_data_dir).get_poll_state()["last_error"] is None


# ----- when to fire -------------------------------------------------------------------


def test_no_event_while_session_lock_held(fake_data_dir: Path):
    bot = FakeBot()
    bot.push(100, 5)
    lock = Lock(held=True)
    src = _source(fake_data_dir, bot, lock=lock)

    assert src.scan_since("ignored") == []
    assert _state(fake_data_dir, 5) == ledger_mod.PENDING
    assert len(bot.methods("setMessageReaction")) == 1  # the ack still goes out

    lock.held = False
    assert [e.source_event_id for e in src.scan_since("ignored")] == ["5:1"]


def test_backlog_of_many_messages_yields_one_event(fake_data_dir: Path):
    bot = FakeBot()
    for i in range(30):
        bot.push(100 + i, 10 + i, text=f"question number {i}")
    events = _source(fake_data_dir, bot).scan_since("ignored")

    assert len(events) == 1
    assert len(events[0].raw_payload["messages"]) == 30
    assert events[0].source_event_id == "39:1"


def test_new_message_during_inflight_run_waits_then_fires(fake_data_dir: Path):
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)
    bot.push(100, 5)
    [first] = src.scan_since("ignored")
    src.on_fired(first, _ok(first.source_event_id))
    assert _state(fake_data_dir, 5) == ledger_mod.DISPATCHED

    clock.advance(minutes=5)
    bot.push(101, 6)
    assert src.scan_since("ignored") == []  # a run is in flight
    assert _state(fake_data_dir, 6) == ledger_mod.PENDING
    assert len(bot.methods("setMessageReaction")) == 2  # but the new message is acked now

    # The run answers message 5; the next tick fires for message 6 alone.
    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        ledger.record_reply(reply_to=5, covers=[5], at=clock())
    clock.advance(minutes=5)
    [second] = src.scan_since("ignored")
    assert second.source_event_id == "6:1"
    assert [m["message_id"] for m in second.raw_payload["messages"]] == [6]


def test_event_id_changes_on_retry_after_timeout(fake_data_dir: Path):
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)
    bot.push(100, 5)
    [first] = src.scan_since("ignored")
    src.on_fired(first, _ok(first.source_event_id))

    clock.advance(minutes=61)
    [retry] = src.scan_since("ignored")
    assert first.source_event_id == "5:1"
    assert retry.source_event_id == "5:2"


def _spawn_failed(event_id: str) -> FireOutcome:
    return FireOutcome(trigger_id="telegram-respond", event_id=event_id, action_kind="run_skill", status="error")


def test_a_failed_spawn_leaves_the_message_pending_with_a_new_attempt(fake_data_dir: Path):
    bot = FakeBot()
    src = _source(fake_data_dir, bot)
    bot.push(100, 5)
    [ev] = src.scan_since("ignored")
    src.on_fired(ev, _spawn_failed(ev.source_event_id))
    msg = Ledger(fake_data_dir).get(5)
    assert msg is not None and msg.state == ledger_mod.PENDING and msg.attempt == 1
    assert msg.dispatched_at is None


def test_a_failed_spawn_refires_on_a_later_tick(fake_data_dir: Path):
    """The engine records the fire whatever the outcome, so the retry needs a new event id.
    Realistic case: triggers.yaml exists before run-respond.sh is installed."""
    from scout.triggers.dedup import DedupStore

    dedup = DedupStore(fake_data_dir / ".scout-cache" / "trigger-fires.json")
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)
    bot.push(100, 5)

    [first] = src.scan_since("ignored")
    dedup.record_fire("telegram-respond", first.source_event_id, clock())
    src.on_fired(first, _spawn_failed(first.source_event_id))

    clock.advance(minutes=10)
    [retry] = src.scan_since("ignored")
    assert (first.source_event_id, retry.source_event_id) == ("5:1", "5:2")
    assert dedup.is_new("telegram-respond", retry.source_event_id)

    src.on_fired(retry, _ok(retry.source_event_id))
    msg = Ledger(fake_data_dir).get(5)
    assert msg is not None and msg.state == ledger_mod.DISPATCHED and msg.attempt == 2


def test_a_message_that_never_spawns_fails_with_one_notice(fake_data_dir: Path):
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)
    bot.push(100, 5)
    for _ in range(ledger_mod.MAX_ATTEMPTS):
        [ev] = src.scan_since("ignored")
        src.on_fired(ev, _spawn_failed(ev.source_event_id))
        clock.advance(minutes=10)

    assert _state(fake_data_dir, 5) == ledger_mod.FAILED
    replies = bot.methods("sendMessage")
    assert [(r["text"], r["reply_parameters"]["message_id"]) for r in replies] == [(FAILED_NOTICE, 5)]
    assert src.scan_since("ignored") == []  # a failed message fires nothing more
    assert len(bot.methods("sendMessage")) == 1


@pytest.mark.parametrize("kind", ["notify", "interactive"])
def test_on_fired_ignores_non_run_skill_actions(fake_data_dir: Path, kind: str):
    """Only a run_skill fire starts a run that will answer; a notify must not mark dispatched."""
    bot = FakeBot()
    src = _source(fake_data_dir, bot)
    bot.push(100, 5)
    [ev] = src.scan_since("ignored")
    src.on_fired(ev, FireOutcome(trigger_id="t", event_id=ev.source_event_id, action_kind=kind, status="ok"))
    msg = Ledger(fake_data_dir).get(5)
    assert msg is not None and msg.state == ledger_mod.PENDING and msg.attempt == 0


def test_on_fired_does_not_overwrite_answered(fake_data_dir: Path):
    """A fast run can reply before on_fired runs; the answered message stays answered."""
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)
    bot.push(100, 5)
    bot.push(101, 6)
    [ev] = src.scan_since("ignored")

    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        ledger.record_reply(reply_to=5, covers=[5], at=clock())
    src.on_fired(ev, _ok(ev.source_event_id))

    answered = Ledger(fake_data_dir).get(5)
    assert answered is not None and answered.state == ledger_mod.ANSWERED and answered.attempt == 0
    dispatched = Ledger(fake_data_dir).get(6)
    assert dispatched is not None and dispatched.state == ledger_mod.DISPATCHED and dispatched.attempt == 1


def test_event_id_is_fresh_after_partial_answer(fake_data_dir: Path):
    """t0: 5 fires and its run dies. t1: 6 arrives, [5, 6] fire, only 5 is answered.
    t2: 6 times out back to pending; its retry must be a new id that dedup accepts."""
    from scout.triggers.dedup import DedupStore

    dedup = DedupStore(fake_data_dir / ".scout-cache" / "trigger-fires.json")
    bot = FakeBot()
    clock = Clock()
    src = _source(fake_data_dir, bot, clock=clock)

    def fire() -> str:
        [ev] = src.scan_since("ignored")
        assert dedup.is_new("telegram-respond", ev.source_event_id), ev.source_event_id
        dedup.record_fire("telegram-respond", ev.source_event_id, clock())
        src.on_fired(ev, _ok(ev.source_event_id))
        return ev.source_event_id

    bot.push(100, 5)
    t0 = fire()

    clock.advance(minutes=61)
    bot.push(101, 6)
    t1 = fire()
    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        ledger.record_reply(reply_to=5, covers=[5], at=clock())

    clock.advance(minutes=61)
    t2 = fire()

    assert len({t0, t1, t2}) == 3
    assert (t0, t1, t2) == ("5:1", "6:1", "6:2")
    assert _state(fake_data_dir, 6) == ledger_mod.DISPATCHED


def test_failed_message_sends_one_notice(fake_data_dir: Path):
    clock = Clock()
    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        ledger.add(
            InboundMessage(message_id=5, date=0, text="hard one", reply_to_text=None, forwarded=False, kind="text")
        )
        for _ in range(ledger_mod.MAX_ATTEMPTS):
            ledger.mark_dispatched([5], now=clock() - dt.timedelta(minutes=61))
    bot = FakeBot()
    src = _source(fake_data_dir, bot, clock=clock)

    assert src.scan_since("ignored") == []
    assert _state(fake_data_dir, 5) == ledger_mod.FAILED
    replies = bot.methods("sendMessage")
    assert [(r["text"], r["reply_parameters"]["message_id"]) for r in replies] == [(FAILED_NOTICE, 5)]

    src.scan_since("ignored")
    assert len(bot.methods("sendMessage")) == 1  # once, not every tick


def test_a_batch_that_fails_together_gets_one_notice(fake_data_dir: Path):
    """One notice per tick, threaded under the newest message that just failed, naming how many."""
    clock = Clock()
    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        for i in (5, 6, 7):
            ledger.add(
                InboundMessage(message_id=i, date=0, text=f"ask {i}", reply_to_text=None, forwarded=False, kind="text")
            )
        for _ in range(ledger_mod.MAX_ATTEMPTS):
            ledger.mark_dispatched([5, 6, 7], now=clock() - dt.timedelta(minutes=61))
    bot = FakeBot()
    assert _source(fake_data_dir, bot, clock=clock).scan_since("ignored") == []
    assert all(_state(fake_data_dir, i) == ledger_mod.FAILED for i in (5, 6, 7))
    replies = bot.methods("sendMessage")
    assert [(r["text"], r["reply_parameters"]["message_id"]) for r in replies] == [
        ("I couldn't handle 3 of your messages after 3 tries — I'll pick them up in the next scheduled run.", 7)
    ]


def test_failed_message_never_fires_a_respond_run(fake_data_dir: Path):
    """Out of attempts is left for a scheduled run: it fires nothing, even alongside a new message."""
    clock = Clock()
    ledger = Ledger(fake_data_dir)
    with ledger.locked():
        ledger.add(
            InboundMessage(message_id=5, date=0, text="hard one", reply_to_text=None, forwarded=False, kind="text")
        )
        ledger.mark_skipped([5], ledger_mod.FAILED)
    bot = FakeBot()
    src = _source(fake_data_dir, bot, clock=clock)
    assert src.scan_since("ignored") == []

    bot.push(100, 6)
    (event,) = src.scan_since("ignored")
    assert [m["message_id"] for m in event.raw_payload["messages"]] == [6]
    src.on_fired(event, _ok(event.source_event_id))
    assert _state(fake_data_dir, 5) == ledger_mod.FAILED and _state(fake_data_dir, 6) == ledger_mod.DISPATCHED


# ----- health / registry ----------------------------------------------------------------


def test_health_check_rejects_group_chat_id(fake_data_dir: Path):
    healthy, reason = _source(fake_data_dir, FakeBot(), chat_id=-1001234).health_check()
    assert healthy is False
    assert reason == "group chats unsupported"


def test_health_check_reports_missing_credentials(fake_data_dir: Path):
    def missing() -> tuple[str, int]:
        raise ConfigError("Missing secret: telegram-bot-token")

    src = TelegramSource(vault=fake_data_dir, credentials=missing, http_post=FakeBot(), lock_held=Lock())
    healthy, reason = src.health_check()
    assert healthy is False
    assert "Missing secret" in reason


def test_health_check_ok_and_no_webhook(fake_data_dir: Path):
    src = _source(fake_data_dir, FakeBot())
    assert src.health_check() == (True, "ok")
    assert src.supports_webhook() is False


def test_registry_knows_telegram(fake_data_dir: Path):
    from scout.triggers.sources import SOURCE_NAMES, get_source, supported_match_types

    assert "telegram" in SOURCE_NAMES
    assert supported_match_types("telegram") == ["message"]
    src = get_source("telegram", vault=fake_data_dir)
    assert isinstance(src, TelegramSource)
    assert src.name == "telegram"

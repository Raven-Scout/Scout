"""`scoutctl telegram find-chat-id` and `inbound-status`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.telegram_inbound import bot
from scout.telegram_inbound.ledger import Ledger


def _update(update_id: int, chat_id: int, first_name: str, chat_type: str = "private") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1,
            "text": "hi",
            "chat": {"id": chat_id, "type": chat_type, "first_name": first_name},
            "from": {"id": chat_id, "first_name": first_name},
        },
    }


def test_find_chat_id_never_passes_a_confirming_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("scout.scripts.notify_telegram._read_secret", lambda name: "TOKEN-123")

    def fake_get_updates(**kwargs: Any) -> list[dict]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(bot, "get_updates", fake_get_updates)
    result = CliRunner().invoke(app, ["telegram", "find-chat-id"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["offset"] == 0
    assert "TOKEN-123" not in result.output


def test_find_chat_id_prints_private_ids_and_labels_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        _update(1, 111, "Ada"),
        _update(2, 111, "Ada"),
        _update(3, -999, "Group", chat_type="group"),
        _update(4, 222, "Bo"),
    ]
    monkeypatch.setattr("scout.scripts.notify_telegram._read_secret", lambda name: "TOKEN-123")
    monkeypatch.setattr(bot, "get_updates", lambda **kw: updates)
    result = CliRunner().invoke(app, ["telegram", "find-chat-id"])
    assert result.exit_code == 0, result.output
    assert "111" in result.output and "Ada" in result.output
    assert "222" in result.output and "Bo" in result.output
    group_line = next(ln for ln in result.output.splitlines() if ln.startswith("-999"))
    assert "group (outbound only)" in group_line
    private_line = next(ln for ln in result.output.splitlines() if ln.startswith("111"))
    assert "outbound only" not in private_line
    assert result.output.count("111") == 1  # distinct ids only


def test_find_chat_id_hints_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scout.scripts.notify_telegram._read_secret", lambda name: "TOKEN-123")
    monkeypatch.setattr(bot, "get_updates", lambda **kw: [])
    result = CliRunner().invoke(app, ["telegram", "find-chat-id"])
    assert result.exit_code == 0
    assert "send" in result.output.lower()
    assert "consumed" in result.output.lower()


def test_inbound_status_json_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    ledger = Ledger(tmp_path)
    with ledger.locked():
        ledger.set_poll_state(offset=7, last_poll_at="2026-11-21T10:00:00+00:00", last_error="boom")
    result = CliRunner().invoke(app, ["telegram", "inbound-status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"counts", "last_poll_at", "last_error"}
    assert data["last_poll_at"] == "2026-11-21T10:00:00+00:00"
    assert data["last_error"] == "boom"
    assert data["counts"]["pending"] == 0


def test_inbound_status_plain_with_no_ledger_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["telegram", "inbound-status"])
    assert result.exit_code == 0, result.output
    assert "never" in result.output.lower()


def test_find_chat_id_lists_groups_as_outbound_only(monkeypatch: pytest.MonkeyPatch) -> None:
    group = _update(3, -999, "Ada", chat_type="supergroup")
    group["message"]["chat"]["title"] = "Team Room"
    channel = _update(4, -555, "Ada", chat_type="channel")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("scout.scripts.notify_telegram._read_secret", lambda name: "TOKEN-123")

    def fake_get_updates(**kwargs: Any) -> list[dict]:
        calls.append(kwargs)
        return [_update(1, 111, "Ada"), group, channel]

    monkeypatch.setattr(bot, "get_updates", fake_get_updates)
    result = CliRunner().invoke(app, ["telegram", "find-chat-id"])
    assert result.exit_code == 0, result.output
    lines = {ln.split("\t")[0]: ln for ln in result.output.splitlines()}
    assert "group (outbound only)" in lines["-999"] and "Team Room" in lines["-999"]
    assert "outbound only" not in lines["111"]
    assert "-555" not in result.output
    assert calls[0]["offset"] == 0
    assert "TOKEN-123" not in result.output


# ----- scoutctl telegram pending: message text only from the protected ledger -----


def _seed(vault: Path) -> None:
    import datetime as dt

    from scout.telegram_inbound.ledger import FAILED, SKIPPED_CHEAP, InboundMessage

    ledger = Ledger(vault)
    now = dt.datetime.now(dt.UTC)
    with ledger.locked():
        for i in (5, 6, 7, 8, 9, 10):
            ledger.add(
                InboundMessage(
                    message_id=i,
                    date=1_776_250_000 + i,
                    text=f"real text {i}",
                    reply_to_text="your question" if i == 6 else None,
                    forwarded=i == 7,
                    kind="text",
                )
            )
        ledger.mark_dispatched([6], now=now)  # in flight: a respond run has it
        ledger.mark_skipped([8], SKIPPED_CHEAP)
        ledger.record_reply(reply_to=9, covers=[9], at=now)
        ledger.mark_skipped([10], FAILED)


def _pending(*args: str) -> Any:
    return CliRunner().invoke(app, ["telegram", "pending", *args])


def test_pending_json_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    result = _pending("--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"messages"}
    rows = data["messages"]
    # Unanswered only (pending, dispatched, failed), oldest first; cheap and answered ones never show.
    assert [r["message_id"] for r in rows] == [5, 6, 7, 10]
    fields = {"message_id", "date", "text", "reply_to_text", "forwarded", "kind", "state", "attempt"}
    assert all(set(r) == fields for r in rows)
    assert rows[1] == {
        "message_id": 6,
        "date": 1_776_250_006,
        "text": "real text 6",
        "reply_to_text": "your question",
        "forwarded": False,
        "kind": "text",
        "state": "dispatched",
        "attempt": 1,
    }
    assert rows[2]["forwarded"] is True and rows[3]["state"] == "failed"


def test_pending_ids_returns_only_those_still_unanswered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A respond run takes ids from its trigger payload and text from here: an id the ledger doesn't
    hold unanswered (unknown, answered, skipped) is dropped, whatever the payload said."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    result = _pending("--json", "--ids", "6,8,9,99, 10")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)["messages"]
    assert [r["message_id"] for r in rows] == [6, 10]
    assert rows[0]["text"] == "real text 6"  # the ledger's words, never a payload's


def test_pending_can_leave_out_in_flight_dispatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheduled drain leaves a fresh dispatch to its respond run (ruling 17)."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    result = _pending("--json", "--exclude-in-flight")
    assert result.exit_code == 0, result.output
    assert [r["message_id"] for r in json.loads(result.output)["messages"]] == [5, 7, 10]


@pytest.mark.parametrize("ids", ["x", "5,-1", "0", "5;6"])
def test_pending_refuses_ids_that_are_not_message_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ids: str
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    result = _pending("--json", "--ids", ids)
    assert result.exit_code == 1
    assert "message ids" in result.output


def test_pending_never_writes_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.telegram_inbound.ledger import ledger_dir

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    assert json.loads(_pending("--json").output) == {"messages": []}
    assert not ledger_dir(tmp_path).exists()  # no ledger: nothing created, not even the lock
    messages = ledger_dir(tmp_path) / "messages.json"
    messages.parent.mkdir(parents=True)
    messages.write_text("{not json", encoding="utf-8")
    result = _pending("--json")
    assert result.exit_code == 0 and json.loads(result.output) == {"messages": []}
    assert sorted(p.name for p in ledger_dir(tmp_path).iterdir()) == ["messages.json"]  # never quarantined
    assert messages.read_text(encoding="utf-8") == "{not json"


def test_pending_plain_text_lists_one_line_per_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    result = _pending()
    assert result.exit_code == 0, result.output
    assert [ln.split("\t")[0] for ln in result.output.splitlines()] == ["5", "6", "7", "10"]

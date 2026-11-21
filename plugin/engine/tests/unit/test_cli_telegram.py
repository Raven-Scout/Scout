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

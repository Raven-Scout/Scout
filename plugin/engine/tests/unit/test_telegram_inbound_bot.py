"""Bot API helpers for inbound Telegram messages."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from scout.errors import ConfigError
from scout.telegram_inbound import bot

OWNER = 4242
TOKEN = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyz0123456789"


class FakePost:
    def __init__(self, response: dict | Exception | None = None) -> None:
        self.response = {"ok": True, "result": True} if response is None else response
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> dict:
        self.calls.append((url, json, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _update(**message: Any) -> dict:
    base: dict[str, Any] = {
        "message_id": 7,
        "date": 1700000000,
        "chat": {"id": OWNER, "type": "private"},
        "from": {"id": OWNER},
    }
    base.update(message)
    return {"update_id": 1, "message": base}


def test_parse_accepts_owner_private_text() -> None:
    msg = bot.parse_message(_update(text="hello"), owner_chat_id=OWNER)
    assert msg is not None
    assert (msg.message_id, msg.date, msg.text, msg.kind) == (7, 1700000000, "hello", "text")
    assert msg.reply_to_text is None
    assert msg.forwarded is False


def test_parse_rejects_other_chat_and_other_sender() -> None:
    other_chat = _update(text="x", chat={"id": 99, "type": "private"})
    other_sender = _update(text="x", **{"from": {"id": 99}})
    assert bot.parse_message(other_chat, owner_chat_id=OWNER) is None
    assert bot.parse_message(other_sender, owner_chat_id=OWNER) is None
    assert bot.parse_message({"update_id": 2}, owner_chat_id=OWNER) is None


def test_parse_rejects_group_chat() -> None:
    update = _update(text="x", chat={"id": OWNER, "type": "group"})
    assert bot.parse_message(update, owner_chat_id=OWNER) is None


def test_parse_uses_caption_when_no_text() -> None:
    msg = bot.parse_message(_update(caption="a photo note", photo=[{}]), owner_chat_id=OWNER)
    assert msg is not None
    assert (msg.text, msg.kind) == ("a photo note", "text")


def test_parse_marks_sticker_unsupported() -> None:
    msg = bot.parse_message(_update(sticker={"file_id": "x"}), owner_chat_id=OWNER)
    assert msg is not None
    assert (msg.text, msg.kind) == ("", "unsupported")


def test_parse_keeps_reply_to_text_and_forwarded_flag() -> None:
    update = _update(text="re", reply_to_message={"message_id": 3, "text": "earlier"}, forward_origin={"type": "user"})
    msg = bot.parse_message(update, owner_chat_id=OWNER)
    assert msg is not None
    assert msg.reply_to_text == "earlier"
    assert msg.forwarded is True


def test_get_updates_sends_offset_and_returns_result() -> None:
    post = FakePost({"ok": True, "result": [{"update_id": 5}]})
    assert bot.get_updates(token=TOKEN, offset=5, http_post=post) == [{"update_id": 5}]
    url, payload, _ = post.calls[0]
    assert url == f"{bot.TELEGRAM_API}/bot{TOKEN}/getUpdates"
    assert payload == {"offset": 5, "timeout": 0, "allowed_updates": ["message"]}


def test_get_updates_raises_scrubbed_error_on_not_ok() -> None:
    post = FakePost({"ok": False, "description": f"Unauthorized {TOKEN}"})
    with pytest.raises(bot.TelegramApiError) as ei:
        bot.get_updates(token=TOKEN, offset=0, http_post=post)
    assert TOKEN not in str(ei.value)


def test_get_updates_scrubs_transport_exception() -> None:
    post = FakePost(requests.ConnectionError(f"failed for url {bot.TELEGRAM_API}/bot{TOKEN}/getUpdates"))
    with pytest.raises(bot.TelegramApiError) as ei:
        bot.get_updates(token=TOKEN, offset=0, http_post=post)
    assert TOKEN not in str(ei.value)
    assert TOKEN not in repr(ei.value)
    assert ei.value.__cause__ is None


def test_set_reaction_returns_false_on_http_error() -> None:
    post = FakePost(requests.ConnectionError("boom"))
    assert bot.set_reaction(token=TOKEN, chat_id=OWNER, message_id=7, http_post=post) is False
    assert bot.set_reaction(token=TOKEN, chat_id=OWNER, message_id=7, http_post=FakePost({"ok": False})) is False


def test_set_reaction_payload() -> None:
    post = FakePost()
    assert bot.set_reaction(token=TOKEN, chat_id=OWNER, message_id=7, http_post=post) is True
    url, payload, _ = post.calls[0]
    assert url.endswith("/setMessageReaction")
    assert payload == {
        "chat_id": OWNER,
        "message_id": 7,
        "reaction": [{"type": "emoji", "emoji": "\U0001f440"}],
    }


def test_send_plain_reply_sets_reply_parameters_and_silent() -> None:
    post = FakePost()
    assert bot.send_plain_reply(token=TOKEN, chat_id=OWNER, reply_to=7, text="On it", http_post=post) is True
    url, payload, _ = post.calls[0]
    assert url.endswith("/sendMessage")
    assert payload == {
        "chat_id": OWNER,
        "text": "On it",
        "disable_notification": True,
        "reply_parameters": {"message_id": 7, "allow_sending_without_reply": True},
    }
    assert "parse_mode" not in payload


def test_send_plain_reply_returns_false_on_error() -> None:
    post = FakePost(requests.Timeout("slow"))
    assert bot.send_plain_reply(token=TOKEN, chat_id=OWNER, reply_to=7, text="x", http_post=post) is False


def test_read_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".scout-secrets"
    d.mkdir()
    for name, value in (("telegram-bot-token", TOKEN), ("telegram-chat-id", str(OWNER))):
        (d / name).write_text(value + "\n")
        (d / name).chmod(0o600)
    assert bot.read_credentials() == (TOKEN, OWNER)
    (d / "telegram-chat-id").unlink()
    with pytest.raises(ConfigError):
        bot.read_credentials()


def test_default_http_post_rejects_non_json_and_non_2xx(monkeypatch) -> None:
    class Resp:
        def __init__(self, status: int, body: Any) -> None:
            self.status_code, self._body = status, body

        def json(self) -> Any:
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    for resp in (Resp(500, {"ok": False}), Resp(200, ValueError("not json"))):
        monkeypatch.setattr(requests, "post", lambda *a, _r=resp, **k: _r)
        with pytest.raises(bot.TelegramApiError):
            bot.default_http_post(f"{bot.TELEGRAM_API}/bot{TOKEN}/x", json={}, timeout=1.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(200, {"ok": True}))
    assert bot.default_http_post("u", json={}, timeout=1.0) == {"ok": True}

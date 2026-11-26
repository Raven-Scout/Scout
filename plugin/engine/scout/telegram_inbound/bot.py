"""Telegram Bot API helpers for inbound messages: poll, parse, react, reply.

Every call goes through an injected ``http_post`` so tests need no network. A
bot token sits in the request URL, so any failure is re-raised as a
:class:`TelegramApiError` whose text is redacted and which carries no
``__cause__`` / ``__context__`` (those would hold the URL).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests

from scout.errors import ConfigError
from scout.redact import redact_secrets
from scout.scripts.notify_telegram import TELEGRAM_API, _read_secret
from scout.telegram_inbound.ledger import InboundMessage

HttpPost = Callable[..., dict]  # (url, *, json, timeout) -> dict

DEFAULT_TIMEOUT = 10.0
DEFAULT_REACTION = "\U0001f440"  # eyes


class TelegramApiError(Exception):
    """A Bot API call failed. The message never contains the bot token."""


def read_credentials() -> tuple[str, int]:
    """``(token, chat_id)`` from the mode-600 secret files; ``ConfigError`` when missing."""
    token = _read_secret("telegram-bot-token")
    raw = _read_secret("telegram-chat-id")
    try:
        chat_id = int(raw)
    except ValueError:
        raise ConfigError("telegram-chat-id must be an integer chat id") from None
    return token, chat_id


def default_http_post(url: str, *, json: dict[str, Any], timeout: float) -> dict:
    """POST ``json`` and return the decoded body; non-JSON or non-2xx is a ``TelegramApiError``."""
    try:
        resp = requests.post(url, json=json, timeout=timeout)
    except requests.RequestException as e:
        # Type name only: the exception text and request carry the URL.
        raise TelegramApiError(f"request failed: {type(e).__name__}") from None
    if not 200 <= resp.status_code < 300:
        raise TelegramApiError(f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        raise TelegramApiError("response was not JSON") from None
    if not isinstance(body, dict):
        raise TelegramApiError("response was not a JSON object")
    return body


def _call(method: str, payload: dict[str, Any], *, token: str, http_post: HttpPost, timeout: float) -> Any:
    """POST one Bot API method; return ``result`` or raise a scrubbed ``TelegramApiError``."""
    failure: str | None = None
    result: Any = None
    try:
        body = http_post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload, timeout=timeout)
        if isinstance(body, dict) and body.get("ok") is True:
            result = body.get("result")
        else:
            desc = body.get("description", "") if isinstance(body, dict) else ""
            failure = f"{method} not ok: {desc}"
    except Exception as e:  # noqa: BLE001 - any transport failure is re-raised scrubbed below
        failure = f"{method} failed: {type(e).__name__}: {e}"
    if failure is not None:
        # Raised outside the except block so the URL-bearing original is not chained.
        raise TelegramApiError(redact_secrets(failure, token))
    return result


def get_updates(*, token: str, offset: int, http_post: HttpPost, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Updates with ``update_id >= offset`` (a non-blocking poll, messages only)."""
    result = _call(
        "getUpdates",
        {"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
        token=token,
        http_post=http_post,
        timeout=timeout,
    )
    return list(result or [])


def set_reaction(
    *,
    token: str,
    chat_id: int,
    message_id: int,
    emoji: str = DEFAULT_REACTION,
    http_post: HttpPost,
) -> bool:
    """React to a message; False on any error (an ack must never break the caller)."""
    payload = {"chat_id": chat_id, "message_id": message_id, "reaction": [{"type": "emoji", "emoji": emoji}]}
    try:
        _call("setMessageReaction", payload, token=token, http_post=http_post, timeout=DEFAULT_TIMEOUT)
    except TelegramApiError:
        return False
    return True


def send_plain_reply(*, token: str, chat_id: int, reply_to: int, text: str, http_post: HttpPost) -> bool:
    """Send a silent plain-text reply to ``reply_to``; False on any error."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": True,
        "reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True},
    }
    try:
        _call("sendMessage", payload, token=token, http_post=http_post, timeout=DEFAULT_TIMEOUT)
    except TelegramApiError:
        return False
    return True


def parse_message(update: dict, *, owner_chat_id: int) -> InboundMessage | None:
    """The owner's private-chat message in ``update``, else None (anyone else is ignored)."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if chat.get("type") != "private" or chat.get("id") != owner_chat_id or sender.get("id") != owner_chat_id:
        return None
    text = message.get("text") or message.get("caption") or ""
    reply_to = message.get("reply_to_message") or {}
    return InboundMessage(
        message_id=int(message["message_id"]),
        date=int(message.get("date", 0)),
        text=text,
        reply_to_text=reply_to.get("text"),
        forwarded="forward_origin" in message,
        kind="text" if text else "unsupported",
    )

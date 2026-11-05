"""Slack bot outbound — posts as the Scout Slack app behind ``scoutctl notify slack``.

Until now every Slack message a run sent went through the claude.ai Slack
connector, which posts **as the user**: the wrap DM lands in the user's own
self-DM, reads as if they wrote it, and never notifies their phone. This sends
the same message as the workspace's **Scout** app bot instead (created from
``engine/scout/docs/connectors/slack-app-manifest.json``; setup in
``slack-bot-setup.md``).

Reads ``~/.scout-secrets/slack-bot-token`` (an ``xoxb-`` bot token, mode 600).
The recipient is always the owner, ``user.slack_user_id`` in the vault's
``scout-config.yaml``: the command has no way to name another person or a
channel. Two calls per send:

  - ``conversations.open(users=<owner>)`` → the bot's DM channel with the owner;
  - ``chat.postMessage(channel, text, thread_ts?)``, one per chunk.

``--thread-ts`` replies inside a thread of that DM (the user's reply to an
earlier wrap). A ts from another conversation is refused by Slack
(``thread_not_found``-style errors surface as :class:`SlackAPIError`).

Returns an :class:`~scout.events.Event` with ``kind="notification.sent"`` and
``source="cli:notify_slack"``. Payload shape::

    {"channel": "slack", "dm_channel": "D…", "ts": "<first chunk ts>",
     "thread_ts": "…"?, "body_chars": <int>, "dry_run": <bool>?}

``ts`` is what a later run passes as ``--thread-ts`` to answer in the same thread.

**Privacy.** The DM is in a workspace the user doesn't own, so it is treated as
the shared surface ``slack`` like the connector's DMs: the body goes through
``check_outbound`` first and a hit drops the message (exit 4). With no vault to
check against, nothing is sent.

**Token handling.** The token travels in the ``Authorization`` header, never in
a URL, so ``requests`` errors cannot carry it; anything raised is still passed
through ``redact_secrets`` at the CLI boundary.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from scout import paths
from scout.errors import ConfigError, ScoutError
from scout.events import Event
from scout.ids import new_ulid
from scout.scripts.notify_telegram import (
    DuplicateSendError,
    OutboundBlockedError,
    _check_shared,
    _iso,
    _recent_rows,
    _record_sent,
    _split_message,
    secrets_dir,
)

SLACK_API = "https://slack.com/api"
# chat.postMessage truncates past 40,000 chars; Slack's guidance is to keep a
# message under 4,000 for readability, and a wrap DM is far shorter.
MAX_MESSAGE_LEN = 4000
DEFAULT_TIMEOUT = 10.0
TOKEN_FILE = "slack-bot-token"
SENT_LOG = "slack-bot-sent.jsonl"
SURFACE = "slack"

__all__ = ["DuplicateSendError", "OutboundBlockedError", "SlackAPIError", "send"]


class SlackAPIError(ScoutError):
    """Slack answered ``ok: false`` (bad token, missing scope, unknown thread …)."""

    exit_code = 2

    def __init__(self, method: str, error: str) -> None:
        super().__init__(f"{method} failed: {error}")
        self.method = method
        self.error = error


def _read_token() -> str:
    """The bot token, with the same 600-permission rule as every Scout secret."""
    path = secrets_dir() / TOKEN_FILE
    if not path.exists():
        raise ConfigError(
            f"Missing secret: {path}. Create the Scout Slack app and save its bot token there (mode 600) — "
            "see engine/scout/docs/connectors/slack-bot-setup.md."
        )
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(f"{path} has insecure permissions {oct(mode)}; expected 600. Run: chmod 600 {path}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"Could not read {path}: {e}") from e
    if not value.startswith("xoxb-"):
        raise ConfigError(f"{path} does not hold a bot token (expected one starting with xoxb-).")
    return value


def _owner_id() -> str:
    """The owner's Slack user id from the vault config (``user.slack_user_id``)."""
    from scout.config import load_config

    try:
        user = load_config(paths.data_dir()).get("user") or {}
    except Exception as e:  # noqa: BLE001 - no vault: say what to set
        raise ConfigError(f"could not load scout-config.yaml to find user.slack_user_id: {e}") from e
    owner = str(user.get("slack_user_id") or "").strip()
    if not owner:
        raise ConfigError("user.slack_user_id is not set in scout-config.yaml; the bot only ever DMs the owner.")
    return owner


def _call(method: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST one Web API method; raise on HTTP failure or ``ok: false``."""
    resp = requests.post(
        f"{SLACK_API}/{method}",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackAPIError(method, str(data.get("error", "unknown_error")))
    return data


def send(
    body: str,
    *,
    thread_ts: str | None = None,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    allow_duplicate: bool = False,
    now: datetime | None = None,
    receipt: Callable[[str], None] | None = None,
) -> Event:
    """DM the owner as the Scout app bot and return an :class:`Event`.

    Raises:
        ValueError: empty body.
        ConfigError: missing/insecure token, or no ``user.slack_user_id`` (exit 10).
        OutboundBlockedError: the body names a private note, or there is no
            vault to check it against (exit 4). Nothing is sent.
        DuplicateSendError: a byte-identical body went to the same thread
            inside the duplicate window (exit 3).
        SlackAPIError: Slack answered ``ok: false`` (exit 2).
        requests.RequestException: HTTP-layer failure.
    """
    if not body.strip():
        raise ValueError("body cannot be empty")

    token = _read_token()
    owner = _owner_id()
    _check_shared(body, SURFACE, channel="slack", command="notify slack")  # a dry run too: it shows what would be sent

    now = now or datetime.now(UTC)
    ts = _iso(now)
    key = hashlib.sha256(f"{owner}\x00{thread_ts or ''}\x00{body}".encode()).hexdigest()
    log = None if dry_run else _sent_log_path()
    rows = _recent_rows(log, now) if log else []
    if not allow_duplicate:
        for row in rows:
            if row["key"] == key:
                raise DuplicateSendError(row["id"], row["ts"])

    chunks = [c for c in _split_message(body, MAX_MESSAGE_LEN) if c.strip()]

    if dry_run:
        ev = Event(
            id=new_ulid(),
            ts=ts,
            kind="notification.sent",
            source="cli:notify_slack",
            payload={
                "channel": "slack",
                "body_chars": len(body),
                "dry_run": True,
                **({"thread_ts": thread_ts} if thread_ts else {}),
            },
        )
        if receipt:
            receipt(ev.id)
        print(f"[dry-run] POST {SLACK_API}/conversations.open users={owner}", file=sys.stderr)
        for chunk in chunks:
            payload = {"channel": "<dm>", "text": chunk, **({"thread_ts": thread_ts} if thread_ts else {})}
            print(
                f"[dry-run] POST {SLACK_API}/chat.postMessage body: {json.dumps(payload, ensure_ascii=False)}",
                file=sys.stderr,
            )
        return ev

    dm_channel = _call("conversations.open", token, {"users": owner}, timeout)["channel"]["id"]
    first_ts: str | None = None
    for chunk in chunks:
        payload: dict[str, Any] = {"channel": dm_channel, "text": chunk, "unfurl_links": False}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        posted = _call("chat.postMessage", token, payload, timeout)
        first_ts = first_ts or str(posted.get("ts", ""))

    ev = Event(
        id=new_ulid(),
        ts=ts,
        kind="notification.sent",
        source="cli:notify_slack",
        payload={
            "channel": "slack",
            "dm_channel": dm_channel,
            "ts": first_ts,
            "body_chars": len(body),
            **({"thread_ts": thread_ts} if thread_ts else {}),
        },
    )
    if log:
        _record_sent(log, rows, {"key": key, "id": ev.id, "ts": ts})
    if receipt:
        receipt(ev.id)
    return ev


def _sent_log_path() -> Path | None:
    """The sent-log path (notify_telegram's rule, its own file), or None with no vault state dir."""
    try:
        state = paths.state_dir(paths.data_dir())
        state.mkdir(exist_ok=True)
    except Exception:  # noqa: BLE001 - a send never fails for want of a log
        return None
    return state / SENT_LOG

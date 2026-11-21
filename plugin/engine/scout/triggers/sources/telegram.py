"""Telegram trigger source: the owner's private-chat messages to the bot.

Each tick polls ``getUpdates`` from the offset kept in the message ledger
(``scout.telegram_inbound``), records every accepted message, and acknowledges
it at once (a reaction, or a short reply). The ``ts`` argument of
:meth:`TelegramSource.scan_since` is ignored: the stored offset replaces the
fixed-lookback contract, and the ledger is written before the offset advances.

At most one event comes back per tick. It carries every pending message, so a
``run_skill`` trigger starts one respond run for the whole backlog, and it is
returned only when that run can start (nothing in flight, session lock free).
The event id is ``"<newest pending id>:<attempt>"``, so a retry after a
timeout is a new id that dedup accepts. :meth:`TelegramSource.on_fired` marks
the snapshot dispatched once the run has been spawned.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from scout import paths
from scout.errors import ConfigError
from scout.telegram_inbound import bot
from scout.telegram_inbound.classify import is_cheap
from scout.telegram_inbound.ledger import PENDING, SKIPPED_CHEAP, SKIPPED_UNSUPPORTED, InboundMessage, Ledger
from scout.triggers.sources.base import ConnectorEvent

if TYPE_CHECKING:  # dispatcher -> actions -> config -> sources would be an import cycle
    from scout.triggers.dispatcher import FireOutcome

SUPPORTED_MATCH_TYPES: list[str] = ["message"]

UNSUPPORTED_REPLY = "I can only read text messages for now."
ACK_FALLBACK_REPLY = "On it"
# Sent by the tick itself, no LLM. A failed message stays answerable: the next scheduled run's drain answers it.
FAILED_NOTICE = "I couldn't handle this after 3 tries — I'll pick it up in the next scheduled run."


def _iso_z(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_lock_held(vault: Path) -> bool:
    from scout.kb.live_run import LOCK_NAME
    from scout.scripts.bootstrap_lock import is_lock_held_by_live_pid

    return is_lock_held_by_live_pid(paths.logs_dir(vault) / LOCK_NAME)


class TelegramSource:
    """Poll the bot's inbound messages into the ledger and fire one respond run at a time."""

    name = "telegram"
    SUPPORTED_MATCH_TYPES = SUPPORTED_MATCH_TYPES

    def __init__(
        self,
        *,
        vault: Path,
        credentials: Callable[[], tuple[str, int]] | None = None,
        http_post: bot.HttpPost | None = None,
        lock_held: Callable[[], bool] | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._vault = Path(vault)
        self._credentials = credentials or bot.read_credentials
        self._http_post = http_post or bot.default_http_post
        self._lock_held = lock_held or (lambda: _session_lock_held(self._vault))
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._ledger = Ledger(self._vault)

    @classmethod
    def for_vault(cls, vault: Path) -> TelegramSource:
        return cls(vault=vault)

    # -- polling -----------------------------------------------------------------
    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        del ts  # the stored offset replaces the lookback window
        token, chat_id = self._credentials()
        ledger = self._ledger
        with ledger.locked():
            now = self._clock()
            offset = int(ledger.get_poll_state().get("offset") or 0)
            try:
                updates = bot.get_updates(token=token, offset=offset, http_post=self._http_post)
            except bot.TelegramApiError as e:
                ledger.set_poll_state(offset=offset, last_poll_at=now.isoformat(), last_error=str(e))
                return []

            seen: list[InboundMessage] = []
            update_ids: list[int] = []
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                if isinstance(update_id, int):
                    update_ids.append(update_id)
                try:
                    msg = bot.parse_message(update, owner_chat_id=chat_id)
                except Exception:  # noqa: BLE001 - one malformed update must never block every later tick
                    continue
                if msg is not None:
                    ledger.add(msg)
                    seen.append(msg)

            for msg in seen:
                self._acknowledge(msg.message_id, token=token, chat_id=chat_id)

            if update_ids:
                offset = max(offset, max(update_ids) + 1)
            ledger.set_poll_state(offset=offset, last_poll_at=now.isoformat(), last_error=None)

            for failed in ledger.reconcile(now=now, run_alive=self._lock_held()):
                bot.send_plain_reply(
                    token=token,
                    chat_id=chat_id,
                    reply_to=failed.message_id,
                    text=FAILED_NOTICE,
                    http_post=self._http_post,
                )

            pending = ledger.pending()
            if not pending or ledger.in_flight(now) or self._lock_held():
                return []
            # The newest message's own next attempt: each dispatch of it bumps its attempt, so
            # the id never repeats, even after a partial answer left older messages behind.
            newest = max(pending, key=lambda m: m.message_id)
            return [
                ConnectorEvent(
                    source=self.name,
                    source_event_id=f"{newest.message_id}:{newest.attempt + 1}",
                    ts=_iso_z(now),
                    raw_payload={"messages": [dataclasses.asdict(m) for m in pending]},
                    normalized_match_fields={"type": "message"},
                )
            ]

    def _acknowledge(self, message_id: int, *, token: str, chat_id: int) -> None:
        """Classify and ack a message once. Keyed on the stored entry (still pending, not yet
        acked), so an update re-read after a crash is acked then, and never twice."""
        ledger = self._ledger
        entry = ledger.get(message_id)
        if entry is None or entry.state != PENDING or entry.acked:
            return
        # Unsupported first: a caption-less photo has empty text, and is_cheap("") is True.
        if entry.kind == "unsupported":
            bot.send_plain_reply(
                token=token, chat_id=chat_id, reply_to=message_id, text=UNSUPPORTED_REPLY, http_post=self._http_post
            )
            ledger.mark_skipped([message_id], SKIPPED_UNSUPPORTED)
            return
        if is_cheap(entry.text):
            ledger.mark_skipped([message_id], SKIPPED_CHEAP)
            return
        reacted = bot.set_reaction(token=token, chat_id=chat_id, message_id=message_id, http_post=self._http_post)
        if not reacted:
            bot.send_plain_reply(
                token=token, chat_id=chat_id, reply_to=message_id, text=ACK_FALLBACK_REPLY, http_post=self._http_post
            )
        ledger.mark_acked([message_id])

    # -- engine hook -------------------------------------------------------------
    def on_fired(self, event: ConnectorEvent, outcome: FireOutcome) -> None:
        """Mark the event's message snapshot dispatched once a run_skill run was spawned.

        Only ids still pending are marked, re-read under the lock: a fast run may already
        have answered some, and an answered message must never go back to dispatched."""
        if outcome.status != "ok" or outcome.action_kind != "run_skill":
            return
        ids = {int(m["message_id"]) for m in event.raw_payload.get("messages", [])}
        if not ids:
            return
        with self._ledger.locked():
            still_pending = [m.message_id for m in self._ledger.pending() if m.message_id in ids]
            if still_pending:
                self._ledger.mark_dispatched(still_pending, now=self._clock())

    # -- health ------------------------------------------------------------------
    def health_check(self) -> tuple[bool, str]:
        try:
            _token, chat_id = self._credentials()
        except ConfigError as e:
            return False, str(e)
        if chat_id < 0:
            return False, "group chats unsupported"
        return True, "ok"

    def supports_webhook(self) -> bool:
        return False

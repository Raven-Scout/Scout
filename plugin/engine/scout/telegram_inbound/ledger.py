"""On-disk ledger of inbound Telegram messages under ``<vault>/.scout-state/telegram-inbound/``.

States: pending -> dispatched -> answered, with skipped_cheap / skipped_unsupported as exits. failed (out of
respond attempts) is still answerable: no respond run fires for it, but a scheduled run's reply answers it.
Every mutating method must run inside :meth:`Ledger.locked`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import paths, safe_write

try:
    import fcntl
except ImportError:  # non-POSIX: advisory locks unavailable
    fcntl = None  # type: ignore[assignment]

LEDGER_DIRNAME = "telegram-inbound"
DISPATCH_TIMEOUT = dt.timedelta(minutes=60)
MAX_ATTEMPTS = 3

PENDING = "pending"
DISPATCHED = "dispatched"
ANSWERED = "answered"
SKIPPED_CHEAP = "skipped_cheap"
SKIPPED_UNSUPPORTED = "skipped_unsupported"
FAILED = "failed"
STATES = (PENDING, DISPATCHED, ANSWERED, SKIPPED_CHEAP, SKIPPED_UNSUPPORTED, FAILED)
# What a reply may still answer. Only PENDING fires a respond run; a FAILED message waits for a scheduled run.
UNANSWERED = (PENDING, DISPATCHED, FAILED)


def ledger_dir(vault: Path) -> Path:
    return paths.state_dir(vault) / LEDGER_DIRNAME


@dataclass
class InboundMessage:
    message_id: int
    date: int
    text: str
    reply_to_text: str | None
    forwarded: bool
    kind: str  # "text" or "unsupported"
    state: str = PENDING
    attempt: int = 0
    dispatched_at: str | None = None
    acked: bool = False
    answered_at: str | None = None


class Ledger:
    def __init__(self, vault: Path) -> None:
        self.vault = Path(vault)
        self._dir = ledger_dir(self.vault)
        self._messages = self._dir / "messages.json"
        self._replies = self._dir / "replies.jsonl"
        self._poll = self._dir / "offset.json"
        self._held = False

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive flock on ``.lock``; not re-entrant."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with safe_write.open_lock(self._dir / ".lock") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            self._held = True
            try:
                yield
            finally:
                self._held = False

    def _require_lock(self) -> None:
        if not self._held:
            raise RuntimeError("Ledger mutation requires `with ledger.locked():`")

    # -- storage ---------------------------------------------------------
    def _load(self) -> dict[str, InboundMessage]:
        """Read messages.json. Structural corruption (bad JSON, wrong shape) reads as empty; the file is
        quarantined only while the lock is held, so an unlocked reader never destroys a file a writer is
        replacing. Other OSErrors propagate."""
        try:
            text = self._messages.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        known = {f.name for f in dataclasses.fields(InboundMessage)}
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("messages.json is not an object")
            return {k: InboundMessage(**{f: x for f, x in v.items() if f in known}) for k, v in raw.items()}
        except (ValueError, TypeError, AttributeError):
            if self._held:
                stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%f")
                os.replace(self._messages, self._messages.with_name(f"{self._messages.name}.corrupt-{stamp}"))
            return {}

    def _save(self, msgs: dict[str, InboundMessage]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {k: dataclasses.asdict(m) for k, m in msgs.items()}
        safe_write.atomic_write_text(self._messages, json.dumps(data, indent=1, sort_keys=True) + "\n")

    # -- reads -----------------------------------------------------------
    def get(self, message_id: int) -> InboundMessage | None:
        return self._load().get(str(message_id))

    def _in_state(self, *states: str) -> list[InboundMessage]:
        return sorted((m for m in self._load().values() if m.state in states), key=lambda m: m.message_id)

    def pending(self) -> list[InboundMessage]:
        return self._in_state(PENDING)

    def in_flight(self, now: dt.datetime) -> list[InboundMessage]:
        return [
            m
            for m in self._in_state(DISPATCHED)
            if m.dispatched_at and now - dt.datetime.fromisoformat(m.dispatched_at) < DISPATCH_TIMEOUT
        ]

    def unanswered(self, *, now: dt.datetime | None = None, exclude_in_flight: bool = False) -> list[InboundMessage]:
        """Every message a reply may still answer, ascending by id: pending, dispatched, and failed (out of
        respond attempts, left for a scheduled run). ``exclude_in_flight`` leaves out a dispatch younger than
        ``DISPATCH_TIMEOUT``, whose respond run may be answering it now; an unreadable dispatch time is stale."""
        at = now or dt.datetime.now(dt.UTC)

        def in_flight(m: InboundMessage) -> bool:
            try:
                return at - dt.datetime.fromisoformat(m.dispatched_at or "") < DISPATCH_TIMEOUT
            except (ValueError, TypeError):  # unreadable or naive time
                return False

        rows = self._in_state(*UNANSWERED)
        return [m for m in rows if not (exclude_in_flight and m.state == DISPATCHED and in_flight(m))]

    def unanswered_ids(self) -> set[int]:
        return {m.message_id for m in self._in_state(*UNANSWERED)}

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(STATES, 0)
        for m in self._load().values():
            out[m.state] = out.get(m.state, 0) + 1
        return out

    # -- mutations (lock required) ---------------------------------------
    def add(self, msg: InboundMessage) -> bool:
        self._require_lock()
        msgs = self._load()
        if str(msg.message_id) in msgs:
            return False
        msgs[str(msg.message_id)] = msg
        self._save(msgs)
        return True

    def _update(self, ids: list[int], fn: Any) -> None:
        self._require_lock()
        msgs = self._load()
        for i in ids:
            if (m := msgs.get(str(i))) is not None:
                fn(m)
        self._save(msgs)

    def mark_acked(self, ids: list[int]) -> None:
        self._update(ids, lambda m: setattr(m, "acked", True))

    def mark_skipped(self, ids: list[int], state: str) -> None:
        self._update(ids, lambda m: setattr(m, "state", state))

    def mark_dispatched(self, ids: list[int], *, now: dt.datetime) -> None:
        def f(m: InboundMessage) -> None:
            m.state = DISPATCHED
            m.attempt += 1
            m.dispatched_at = now.isoformat()

        self._update(ids, f)

    def record_reply(self, *, reply_to: int, covers: list[int], at: dt.datetime) -> None:
        self._require_lock()
        row = {"reply_to": reply_to, "covers": list(covers), "delivered_at": at.isoformat()}
        self._dir.mkdir(parents=True, exist_ok=True)
        with safe_write.open_append(self._replies) as f:
            f.write(json.dumps(row) + "\n")

        def f2(m: InboundMessage) -> None:
            m.state = ANSWERED
            m.answered_at = at.isoformat()

        self._update([*covers, reply_to], f2)

    def reconcile(self, *, now: dt.datetime, run_alive: bool) -> list[InboundMessage]:
        """Revert timed-out dispatches to pending (none while a run is alive); fail those out of attempts."""
        self._require_lock()
        if run_alive:
            return []
        msgs = self._load()
        failed: list[InboundMessage] = []
        for m in msgs.values():
            if m.state != DISPATCHED or not m.dispatched_at:
                continue
            if now - dt.datetime.fromisoformat(m.dispatched_at) < DISPATCH_TIMEOUT:
                continue
            if m.attempt >= MAX_ATTEMPTS:
                m.state = FAILED
                failed.append(m)
            else:
                m.state = PENDING
        self._save(msgs)
        return failed

    # -- poll state ------------------------------------------------------
    def get_poll_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._poll.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def set_poll_state(self, *, offset: int, last_poll_at: str, last_error: str | None) -> None:
        self._require_lock()
        self._dir.mkdir(parents=True, exist_ok=True)
        state = {"offset": offset, "last_poll_at": last_poll_at, "last_error": last_error}
        safe_write.atomic_write_text(self._poll, json.dumps(state, indent=1) + "\n")

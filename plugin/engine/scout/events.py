"""Event dataclass returned by mutators.

In v0.4, mutators return an `Event` but nothing persists it. v0.5 will
add an `emit()` function that appends to the SQLite event store; the
shape defined here is its wire format. See v0.4 spec §13.2 and the v0.5+
event-architecture vision spec.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import safe_write
from scout.ids import new_ulid


@dataclass(frozen=True)
class Event:
    """A single mutation event.

    Fields:
        id: ULID for the event itself (distinct from any entity ULID
            referenced in the payload).
        ts: ISO 8601 UTC timestamp with millisecond precision and 'Z'
            suffix, e.g. "2026-04-26T12:34:56.789Z".
        kind: Flat namespace, e.g. "action_item.completed".
        source: Origin tag, e.g. "cli:mark_done", "hook:connector-log".
        payload: Arbitrary JSON-compatible dict. Per-kind schemas are
            documented in the v0.5+ event-architecture spec.
    """

    id: str
    ts: str
    kind: str
    source: str
    payload: dict[str, Any]


def now_iso() -> str:
    """ISO 8601 UTC string with millisecond precision and 'Z' suffix."""
    n = dt.datetime.now(tz=dt.UTC)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


EVENT_LOG_PREFIX = "schedule-events-"


def append_event(log_dir: Path, *, kind: str, source: str, payload: dict[str, Any]) -> Event:
    """Append one event row to ``<log_dir>/schedule-events-<UTC date>.jsonl`` and return it.

    The same file and row shape the schedule tick writes (``schedule_tick._emit_event``),
    for events raised outside the tick (``privacy.check_failed``,
    ``privacy.outbound_blocked``). The file's date is the event's own UTC date.
    A link or non-file a run put at the log is removed (never followed) and the log made again, with an
    ``events.unsafe_file_replaced`` row first: a planted link must not silence every later event.
    """
    ev = Event(id=new_ulid(), ts=now_iso(), kind=kind, source=source, payload=payload)
    log_dir.mkdir(parents=True, exist_ok=True)

    def replaced(path: Path, why: str) -> None:
        note = Event(id=new_ulid(), ts=now_iso(), kind=UNSAFE_REPLACED, source="scout.events", payload={})
        _append_regular(path, _row(note, {"file": path.name, "why": why}))

    _append_regular(log_dir / f"{EVENT_LOG_PREFIX}{ev.ts[:10]}.jsonl", _row(ev, ev.payload), on_unsafe=replaced)
    return ev


UNSAFE_REPLACED = "events.unsafe_file_replaced"


def unsafe_file_recorder(log_dir: Path, *, kind: str, source: str) -> Callable[[Path, str], None]:
    """An ``on_unsafe`` for :mod:`scout.safe_write`: an event ``kind`` naming the file (its name only) and why."""

    def record(path: Path, why: str) -> None:
        append_event(log_dir, kind=kind, source=source, payload={"file": path.name, "why": why})

    return record


def _row(ev: Event, payload: dict[str, Any]) -> bytes:
    row = {"id": ev.id, "ts": ev.ts, "kind": ev.kind, "source": ev.source, "payload": payload}
    return (json.dumps(row) + "\n").encode("utf-8")


def _append_regular(path: Path, data: bytes, *, on_unsafe: Callable[[Path, str], None] | None = None) -> None:
    """Append to ``path`` only if it is a regular file with one link (or new): never block, never follow a link.

    A FIFO, device or socket put where a log belongs would make a plain ``open("a")`` hang, and a hook that hangs
    times out, which Claude Code treats as an allow (review of #637). :func:`scout.safe_write.open_append` opens it
    O_NONBLOCK | O_NOFOLLOW and refuses anything but a plain file, or, given ``on_unsafe``, makes it again.
    """
    with safe_write.open_append(path, mode=0o644, binary=True, on_unsafe=on_unsafe) as fh:
        fh.write(data)

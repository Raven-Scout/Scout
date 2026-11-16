"""Event dataclass returned by mutators.

In v0.4, mutators return an `Event` but nothing persists it. v0.5 will
add an `emit()` function that appends to the SQLite event store; the
shape defined here is its wire format. See v0.4 spec §13.2 and the v0.5+
event-architecture vision spec.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    """
    ev = Event(id=new_ulid(), ts=now_iso(), kind=kind, source=source, payload=payload)
    log_dir.mkdir(parents=True, exist_ok=True)
    row = {"id": ev.id, "ts": ev.ts, "kind": ev.kind, "source": ev.source, "payload": ev.payload}
    with (log_dir / f"{EVENT_LOG_PREFIX}{ev.ts[:10]}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return ev

"""Record 🔴 action items left untouched for ``RED_AFTER_DAYS`` days as ``red-carried`` rows (noise).

"Untouched" is ``scout.action_items.stale``'s rule: the item's block text has not changed, ignoring date-only
edits. Items are keyed by the tag ``stale`` extracts (the same ``task_blocks`` rule), which can differ from
``ActionItem.short_prefix``: the parser also reads a tag after a status emoji, ``stale`` does not. An item
``stale`` does not track has no clock, so it is never recorded.

A row's ref is a hash of the tag plus the day the item last changed: no item text, and an item touched and then
stale again gets a new ref."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from scout.action_items import stale
from scout.action_items.parser import parse_lines
from scout.changes.models import Signal
from scout.changes.store import append_signal_once

RED_AFTER_DAYS = 3
_OPEN_STATUSES = frozenset({"open", "in_progress"})


def _stale_tag(raw_line: str) -> str | None:
    """The tag ``stale`` would track this task line under, or None when it tracks none."""
    blocks = stale.task_blocks([raw_line])
    return blocks[0].tag if blocks else None


def record_red_carried(
    vault: Path,
    lines: list[str],
    *,
    today: date,
    now: datetime,
    env: Mapping[str, str],
) -> int:
    """Write one ``red-carried`` row per open 🔴 item untouched for 3+ days; return the rows written.

    Never raises: a ledger that can't be written costs that row (and any after it), never the caller."""
    written = 0
    try:
        changed = stale.last_changed_by_tag(lines, data_dir=vault, today=today)
        seen: set[str] = set()
        for item in parse_lines(lines):
            if item.priority != "🔴" or item.status not in _OPEN_STATUSES:
                continue
            tag = _stale_tag(item.raw_line)
            if tag is None or tag in seen or tag not in changed:
                continue
            seen.add(tag)
            since = changed[tag]
            if (today - since).days < RED_AFTER_DAYS:
                continue
            ref = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:16] + "@" + since.isoformat()
            signal = Signal(ts=now, kind="red-carried", surface="action-items", ref=ref)
            if append_signal_once(signal, vault=vault, env=env):
                written += 1
    except Exception:  # noqa: BLE001 - a changes-map error never fails the caller
        return written
    return written

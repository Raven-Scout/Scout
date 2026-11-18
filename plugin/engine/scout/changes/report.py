"""The two JSON documents the macOS app reads: ``scoutctl changes --json`` and ``scoutctl engagement --json``.

Both carry ``schema_version`` (a client ignores unknown keys) and never an absolute path: change rows hold
vault-relative files only, and engagement rows hold no path at all. Readers never raise; rows they cannot parse
are counted in ``skipped_rows``."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.changes.models import CHANGE_KINDS, SCHEMA_VERSION
from scout.changes.store import read_changes, read_signals

FEEDBACK_KINDS = frozenset({"dm-reply", "dm-reaction", "inline-comment", "app-edit"})
RELIANCE_KIND = "session-reliance"
# Plan 2 adds unanswered dm-sent here.
NOISE_KINDS = frozenset({"red-carried"})
DEFAULT_LIMIT = 500


def changes_report(vault: Path, *, now: datetime, since: datetime, limit: int = DEFAULT_LIMIT) -> dict:
    """Changes since ``since``, newest first, at most ``limit``; ``counts`` covers every in-range change."""
    changes, skipped = read_changes(vault)
    inside = sorted((c for c in changes if since <= c.ts <= now), key=lambda c: c.ts, reverse=True)
    limit = max(1, limit)
    counts = {kind: 0 for kind in sorted(CHANGE_KINDS)}
    for c in inside:
        counts[c.kind] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "truncated": len(inside) > limit,
        "skipped_rows": skipped,
        "changes": [c.to_row() for c in inside[:limit]],
        "counts": counts,
    }


def engagement_report(vault: Path, env: Mapping[str, str], *, now: datetime, since: datetime, tz: ZoneInfo) -> dict:
    """Per-calendar-day engagement in ``tz``, every day in the range present (zeros included).

    ``by_surface`` counts the signals that fed feedback, reliance or noise that day. A session's reliance on one
    surface counts once per local day, so a duplicated row never inflates the rate."""
    signals, skipped = read_signals(vault, env)
    first, last = since.astimezone(tz).date(), now.astimezone(tz).date()
    days: dict[str, dict] = {}
    day = first
    while day <= last:
        days[day.isoformat()] = {"date": day.isoformat(), "feedback": 0, "reliance": 0, "noise": 0, "by_surface": {}}
        day += timedelta(days=1)
    seen: set[tuple[str, str, str]] = set()
    for s in signals:
        if not since <= s.ts <= now:
            continue
        date = s.ts.astimezone(tz).date().isoformat()
        bucket = days.get(date)
        if bucket is None:
            continue
        if s.kind in FEEDBACK_KINDS:
            field = "feedback"
        elif s.kind == RELIANCE_KIND:
            field = "reliance"
            if s.session is not None:
                key = (s.session, s.surface, date)
                if key in seen:
                    continue
                seen.add(key)
        elif s.kind in NOISE_KINDS:
            field = "noise"
        else:
            continue
        bucket[field] += 1
        bucket["by_surface"][s.surface] = bucket["by_surface"].get(s.surface, 0) + 1
    rows = list(days.values())
    for r in rows:
        r["by_surface"] = dict(sorted(r["by_surface"].items()))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "skipped_rows": skipped,
        "days": rows,
        "totals": {f: sum(r[f] for r in rows) for f in ("feedback", "reliance", "noise")},
    }

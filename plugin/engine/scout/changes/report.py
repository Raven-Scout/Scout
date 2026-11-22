"""The two JSON documents the macOS app reads: ``scoutctl changes --json`` and ``scoutctl engagement --json``.

Both carry ``schema_version`` (a client ignores unknown keys) and never an absolute path: change rows hold
vault-relative files only, and engagement rows hold no path at all. Readers never raise; rows they cannot parse
are counted in ``skipped_rows``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.changes.models import CHANGE_KINDS, SCHEMA_VERSION, Change, Signal
from scout.changes.scan import derive
from scout.changes.store import read_changes, read_signals

FEEDBACK_KINDS = frozenset({"dm-reply", "dm-reaction", "inline-comment", "app-edit", "verdict-wrong"})
RELIANCE_KIND = "session-reliance"
ANSWER_KINDS = frozenset({"dm-reply", "dm-reaction"})
# A DM is noise once it has gone this long with no reply or reaction (the owner may still answer later; the answer
# still counts as feedback on its own day, but the DM stays noise).
DM_ANSWER_WINDOW = timedelta(hours=48)
# An unanswered dm-sent is noise too, but that rule needs the other rows, so daily_counts applies it separately.
NOISE_KINDS = frozenset({"red-carried"})
FIELDS = ("feedback", "reliance", "noise")
DEFAULT_LIMIT = 500


def changes_report(
    vault: Path,
    *,
    now: datetime,
    since: datetime,
    env: Mapping[str, str],
    tz: ZoneInfo,
    limit: int = DEFAULT_LIMIT,
    owner_vault: Path | None = None,
) -> dict:
    """Changes since ``since``, newest first, at most ``limit``; ``counts`` covers every in-range change.

    One entry per change ``id`` (the first row in the file wins): two scans that overlap can each append a row
    for the same commit, and the duplicate must not show twice or count twice.

    Each change carries its ``test`` (scout.changes.verdicts), computed now from both engagement stores, the same
    rows ``engagement_report`` reads; days are local dates in ``tz``. ``calibrating`` and ``auto_rollback`` come from
    the owner store's settings, which a run can't write. ``skipped_rows`` counts change rows only. ``needs_ok`` lists
    every readable ``governance-pending/`` file with the owner's decision on its current diff (``awaiting-ok``,
    ``approved`` or ``rejected``), that diff's ``diff_hash`` and whether applying it is a ``governance`` change;
    ``needs_ok_skipped`` counts files that didn't fit. A row is a rollback, with ``reverts``, only when git shows its
    commit put the target's own lines back (scout.changes.scan, re-derived on every read).

    Git and ``.scout-logs/`` are read from ``vault``; owner rows are keyed to ``owner_vault`` (default ``vault``),
    which the CLI sets to the installed vault, as ``engagement --json`` does, so a worktree cwd undercounts nothing."""
    from scout.changes.governance import decision, read_pending
    from scout.changes.settings import read_settings
    from scout.changes.verdicts import evaluate  # verdicts counts through daily_counts, below: import it late

    changes, skipped = read_changes(vault)
    unique: dict[str, Change] = {}
    for c in changes:
        unique.setdefault(c.id, c)
    # changes.jsonl is run-writable, so a row is only a pointer to a commit: what the report shows is what git says
    # that commit is now. A row whose id isn't its commit's, or whose commit isn't a self-change, is dropped.
    derived = derive(vault, [c.commit for c in unique.values()])
    verified = derived is not None
    if derived is not None:
        kept: dict[str, Change] = {}
        for cid, c in unique.items():
            row = derived.get(c.commit)
            if cid != f"chg-{c.commit[:12]}" or row is None:
                skipped += 1
                continue
            kept[cid] = replace(row, backfilled=c.backfilled)
        unique = kept
    inside = sorted((c for c in unique.values() if since <= c.ts <= now), key=lambda c: c.ts, reverse=True)
    limit = max(1, limit)
    counts = {kind: 0 for kind in sorted(CHANGE_KINDS)}
    for c in inside:
        counts[c.kind] += 1
    settings = read_settings(env)
    signals, _ = read_signals(vault, env, owner_vault=owner_vault)
    # A shown change's overlap partners are at most review_days local dates from it (under review_days + 1 days of
    # clock, plus a DST hour), and what rolls it back comes after it, so older rows can't change what is shown.
    # Leaving them out bounds the work a stale or forged row can cause.
    floor = since - timedelta(days=settings.review_days + 2)
    tests = evaluate([c for c in unique.values() if floor <= c.ts <= now], signals, now=now, tz=tz, settings=settings)
    pending, pending_skipped = read_pending(vault)
    needs_ok = [
        {
            "id": p.id,
            "summary": p.summary,
            "why": p.why,
            "targets": list(p.targets),
            "path": p.path,
            "state": decision(signals, p.id, p.hash),
            "diff_hash": p.hash,
            "governance": p.governance,
        }
        for p in pending
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "truncated": len(inside) > limit,
        "skipped_rows": skipped,
        "verified": verified,
        "calibrating": settings.calibrated_on is None,
        "auto_rollback": settings.auto_rollback,
        "changes": [{**c.to_row(), "test": tests[c.id]} for c in inside[:limit]],
        "counts": counts,
        "needs_ok": needs_ok,
        "needs_ok_skipped": pending_skipped,
    }


def _sent_dms(signals: Sequence[Signal]) -> list[Signal]:
    """Each ``slack-dm`` ``dm-sent`` row with a ref, once per ref (a duplicated row never counts twice)."""
    seen: set[str] = set()
    out = []
    for s in signals:
        if s.kind == "dm-sent" and s.surface == "slack-dm" and s.ref and s.ref not in seen:
            seen.add(s.ref)
            out.append(s)
    return out


def _is_answered(sent: Signal, signals: Sequence[Signal]) -> bool:
    prefix = f"{sent.ref}/"
    return any(
        r.kind in ANSWER_KINDS
        and r.ref is not None
        and r.ref.startswith(prefix)
        and sent.ts <= r.ts <= sent.ts + DM_ANSWER_WINDOW
        for r in signals
    )


def _unanswered_dms(signals: Sequence[Signal], since: datetime, now: datetime) -> list[Signal]:
    """DMs sent inside [since, now] with no reply or reaction within 48 h of the send."""
    return [s for s in _sent_dms(signals) if since <= s.ts <= now and not _is_answered(s, signals)]


def pending_dm_count(signals: Sequence[Signal], *, since: datetime, now: datetime) -> int:
    """Unanswered DMs still younger than 48 h: not noise yet, because the owner may yet answer."""
    return sum(1 for s in _unanswered_dms(signals, since, now) if now - s.ts < DM_ANSWER_WINDOW)


def daily_counts(signals: Sequence[Signal], *, since: datetime, now: datetime, tz: ZoneInfo) -> dict[str, dict]:
    """Per-local-day buckets (keyed by ISO date, every day in the range present, zeros included).

    Each bucket is ``{"date", "feedback", "reliance", "noise", "by_surface"}``. An unanswered DM is noise on the local
    day it was sent, once 48 h have passed. The verdict engine counts through this function, so it sees what the
    report shows."""
    first, last = since.astimezone(tz).date(), now.astimezone(tz).date()
    days: dict[str, dict] = {}
    day = first
    while day <= last:
        days[day.isoformat()] = {
            "date": day.isoformat(),
            **dict.fromkeys(FIELDS, 0),
            "by_surface": {f: {} for f in FIELDS},
        }
        day += timedelta(days=1)

    def add(bucket: dict, field: str, surface: str) -> None:
        bucket[field] += 1
        tally = bucket["by_surface"][field]
        tally[surface] = tally.get(surface, 0) + 1

    seen: set[tuple[str, str, str]] = set()
    for s in signals:
        if not since <= s.ts <= now:
            continue
        date = s.ts.astimezone(tz).date().isoformat()
        bucket = days[date]  # in [since, now], so its local date is one of the days built above
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
        add(bucket, field, s.surface)
    for sent in _unanswered_dms(signals, since, now):
        if now - sent.ts >= DM_ANSWER_WINDOW:
            add(days[sent.ts.astimezone(tz).date().isoformat()], "noise", sent.surface)
    for r in days.values():
        r["by_surface"] = {f: dict(sorted(r["by_surface"][f].items())) for f in FIELDS}
    return days


def engagement_report(vault: Path, env: Mapping[str, str], *, now: datetime, since: datetime, tz: ZoneInfo) -> dict:
    """Per-calendar-day engagement in ``tz``, every day in the range present (zeros included).

    ``by_surface`` splits each of feedback, reliance and noise by surface (``{"feedback": {surface: n}, ...}``), so
    an ignored 🔴 on ``action-items`` never reads as engagement there; each field's surfaces sum to its count. A
    session's reliance on one surface counts once per local day, so a duplicated row never inflates the rate.
    ``pending.dm_sent`` counts DMs under 48 h old with no answer yet; they are not noise until the window closes."""
    signals, skipped = read_signals(vault, env)
    rows = list(daily_counts(signals, since=since, now=now, tz=tz).values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "skipped_rows": skipped,
        "days": rows,
        "totals": {f: sum(r[f] for r in rows) for f in FIELDS},
        "pending": {"dm_sent": pending_dm_count(signals, since=since, now=now)},
    }

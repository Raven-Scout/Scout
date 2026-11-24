"""The verdict engine (spec §7, §7a): a before/after test for every change a scheduled run made.

Verdicts are computed on read, never stored: every input is an append-only ledger row, so a run can't write its own
verdict. Windows are local calendar dates in the host timezone. The baseline is the 14 dates before the change's
local date; the review window is ``review_days`` dates starting on it. Engagement and noise come from
:func:`scout.changes.report.daily_counts`, so a verdict counts exactly what the engagement report shows.

A DM sent near the end of a window that is still under 48 h old at ``now`` is pending, not noise (the report's
rule), so noise can be undercounted for a window that just closed, never overcounted."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from scout.changes.models import Change, Signal
from scout.changes.report import daily_counts
from scout.changes.settings import Settings

BASELINE_DAYS = 14
NOISE_UP = 1.25
ENGAGEMENT_UP = 1.15
TESTED_LANES = frozenset({"briefing", "consolidation", "dreaming", "research", "work", "run"})
UNTESTED_KINDS = frozenset({"rollback", "upgrade"})
# The reasons a `targeted` auto-rollback would act on: the change's own target went bad, not the whole window.
TARGETED_REASONS = frozenset({"recurrence-up", "named-negative"})
# One-at-a-time waits this long after a rollback in a shared window before it may undo the next suspect (spec §7.5,
# "wait a week, and re-check"): the rollback's own effect needs time to show.
ONE_AT_A_TIME_WAIT = timedelta(days=7)
_MARK_KINDS = frozenset({"recurrence", "owner-away"})
# Rates are integer counts over whole days; this absorbs float error at an exact threshold (2.0 -> 2.3 is +15 %).
_EPS = 1e-9


@dataclass(frozen=True)
class Window:
    days: int
    engagement: float  # (feedback + reliance) per calendar day, days with no rows counted as zero
    noise: float  # noise per calendar day
    recurrences: Mapping[str, int]  # per pattern-* target: recurrence rows in the window
    away_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "engagement": round(self.engagement, 4),
            "noise": round(self.noise, 4),
            "recurrences": dict(sorted(self.recurrences.items())),
            "away_days": self.away_days,
        }


def _patterns(targets: Sequence[str]) -> tuple[str, ...]:
    """The targets recurrences count for: mistake patterns only (``pattern-N``), once each."""
    return tuple(dict.fromkeys(t for t in targets if t.startswith("pattern-")))


def _counts(signals: Sequence[Signal], *, first: date, now: datetime, tz: ZoneInfo) -> dict[str, dict]:
    """:func:`daily_counts` from local midnight of ``first`` to ``now``. A day's bucket doesn't depend on where the
    range starts, so one call serves every window inside it."""
    return daily_counts(signals, since=datetime.combine(first, time(0), tzinfo=tz), now=now, tz=tz)


def _window(
    days_map: Mapping[str, dict],
    marks: Sequence[Signal],
    *,
    start: date,
    days: int,
    tz: ZoneInfo,
    targets: Sequence[str],
) -> Window:
    end = start + timedelta(days=days - 1)
    engagement = noise = 0
    for i in range(days):
        bucket = days_map.get((start + timedelta(days=i)).isoformat())
        if bucket is not None:
            engagement += bucket["feedback"] + bucket["reliance"]
            noise += bucket["noise"]
    refs: dict[str, set[str]] = {p: set() for p in _patterns(targets)}
    away: set[date] = set()
    for s in marks:
        if not s.ref:
            continue
        if s.kind == "recurrence":
            pattern = s.ref.split("@", 1)[0]
            if pattern in refs and start <= s.ts.astimezone(tz).date() <= end:
                refs[pattern].add(s.ref)  # a duplicated row counts once
        else:  # owner-away: marks holds only the two kinds
            try:
                day = date.fromisoformat(s.ref)
            except ValueError:
                continue
            if start <= day <= end:
                away.add(day)
    span = max(days, 1)
    return Window(
        days=days,
        engagement=engagement / span,
        noise=noise / span,
        recurrences={p: len(r) for p, r in refs.items()},
        away_days=len(away),
    )


def window(
    signals: Sequence[Signal], *, start: date, days: int, now: datetime, tz: ZoneInfo, targets: Sequence[str]
) -> Window:
    """The ``days`` local dates from ``start``: per-day engagement and noise, recurrences of each ``pattern-*``
    target (a ``recurrence`` row's ref before its first ``@``), and the distinct ``owner-away`` dates inside it."""
    marks = [s for s in signals if s.kind in _MARK_KINDS]
    return _window(_counts(signals, first=start, now=now, tz=tz), marks, start=start, days=days, tz=tz, targets=targets)


def _up(after: float, before: float, ratio: float) -> bool:
    """``after`` is at least ``ratio`` × ``before`` and above it (so 0 -> anything above 0 is up)."""
    return after >= ratio * before - _EPS and after > before + _EPS


def judge(
    before: Window, after: Window, *, targets: Sequence[str], named_negative: bool
) -> tuple[str, tuple[str, ...]]:
    """The verdict and the reasons for it (spec §7), checked in order: can't tell, hurt, helped, no clear effect.

    ``can't tell`` comes only from away days, never from low engagement: an owner who stopped answering shows up as
    noise, so as ``hurt``. Recurrences compare per-day rates, since the two windows can differ in length."""
    if before.away_days * 2 > before.days or after.away_days * 2 > after.days:
        return "can't tell", ("away",)
    patterns = _patterns(targets)

    def count(w: Window, p: str) -> int:
        return w.recurrences.get(p, 0)

    hurt: list[str] = []
    if _up(after.noise, before.noise, NOISE_UP) and after.engagement <= before.engagement + _EPS:
        hurt.append("noise-up")
    # Integer cross-multiplication compares the per-day rates exactly.
    if any(count(after, p) * before.days > count(before, p) * after.days for p in patterns):
        hurt.append("recurrence-up")
    if named_negative:
        hurt.append("named-negative")
    if hurt:
        return "hurt", tuple(hurt)
    helped: list[str] = []
    if sum(count(before, p) for p in patterns) > 0 and sum(count(after, p) for p in patterns) == 0:
        helped.append("recurrence-zero")
    if _up(after.engagement, before.engagement, ENGAGEMENT_UP) and after.noise <= before.noise + _EPS:
        helped.append("engagement-up")
    if helped:
        return "helped", tuple(helped)
    return "no clear effect", ()


def is_tested(change: Change) -> bool:
    """Run-made, not a rollback or an upgrade, not backfilled. Interactive changes are the owner's own."""
    return change.author_lane in TESTED_LANES and change.kind not in UNTESTED_KINDS and not change.backfilled


def _untested(base: dict[str, Any]) -> dict[str, Any]:
    return {
        **base,
        "state": "not-tested",
        "review_on": None,
        "baseline": None,
        "after": None,
        "verdict": None,
        "reasons": [],
        "suspect": False,
        "overlap": [],
        "would_have_done": {"targeted": False, "one_at_a_time": False},
    }


def evaluate(
    changes: Sequence[Change], signals: Sequence[Signal], *, now: datetime, tz: ZoneInfo, settings: Settings
) -> dict[str, dict]:
    """Each change id's ``test`` object (see the module docstring and the changes contract).

    ``state`` is ``testing`` until the change's local date + ``review_days`` (``review_on``), then ``done``. A
    rollback change whose ``reverts`` names a change makes that change's verdict ``rolled back`` and clears
    ``suspect``. ``overlap`` lists the other tested changes whose local dates are at most ``review_days`` apart
    from this one, either side (inclusive): the effect is shared. ``would_have_done`` is what each auto-rollback mode
    would undo, shown during calibration so the owner can judge the rules before trusting them."""
    review = settings.review_days
    today = now.astimezone(tz).date()
    by_id = {c.id: c for c in changes}
    local = {c.id: c.ts.astimezone(tz).date() for c in by_id.values()}
    tested = [c for c in by_id.values() if is_tested(c)]
    rolled_back_by: dict[str, str] = {}
    for c in sorted(by_id.values(), key=lambda c: (c.ts, c.id)):
        if c.kind == "rollback" and c.reverts:
            rolled_back_by.setdefault(c.reverts, c.id)
    wrong = {s.ref for s in signals if s.kind == "verdict-wrong" and s.ref}
    marks = [s for s in signals if s.kind in _MARK_KINDS]
    negatives = [s for s in signals if s.kind == "dm-reply" and s.sentiment == "negative" and s.change]
    days_map: dict[str, dict] = {}
    if tested:
        first = min(local[c.id] for c in tested) - timedelta(days=BASELINE_DAYS)
        days_map = _counts(signals, first=first, now=now, tz=tz)

    out: dict[str, dict] = {}
    for c in by_id.values():
        base = {
            "rolled_back_by": rolled_back_by.get(c.id),
            "calibrating": settings.calibrated_on is None,
            "marked_wrong": c.id in wrong,
            "engagement_targeted": "engagement" in c.targets,
        }
        if not is_tested(c):
            out[c.id] = _untested(base)
            continue
        day = local[c.id]
        review_on = day + timedelta(days=review)
        before = _window(
            days_map, marks, start=day - timedelta(days=BASELINE_DAYS), days=BASELINE_DAYS, tz=tz, targets=c.targets
        )
        after: Window | None = None
        verdict: str | None = None
        reasons: tuple[str, ...] = ()
        done = today >= review_on
        if done:
            after = _window(days_map, marks, start=day, days=review, tz=tz, targets=c.targets)
            named = any(s.change == c.id and day <= s.ts.astimezone(tz).date() < review_on for s in negatives)
            verdict, reasons = judge(before, after, targets=c.targets, named_negative=named)
        if base["rolled_back_by"]:
            verdict = "rolled back"
        out[c.id] = {
            **base,
            "state": "done" if done else "testing",
            "review_on": review_on.isoformat(),
            "baseline": before.to_dict(),
            "after": after.to_dict() if after else None,
            "verdict": verdict,
            "reasons": list(reasons),
            "suspect": verdict == "hurt",
            "overlap": sorted(o.id for o in tested if o.id != c.id and abs((local[o.id] - day).days) <= review),
        }

    def rolled_back_lately(cid: str) -> bool:
        by = out[cid]["rolled_back_by"]
        rollback = by_id.get(by) if by else None
        return rollback is not None and now - rollback.ts < ONE_AT_A_TIME_WAIT

    for c in tested:
        test = out[c.id]
        # A verdict the owner marked wrong is never acted on, by either mode.
        hurt = test["verdict"] == "hurt" and not test["marked_wrong"]
        # One-at-a-time undoes only the newest suspect in a shared window, waits a week after any rollback in that
        # window, and re-checks.
        newer_suspect = any(out[o]["suspect"] and (by_id[o].ts, o) > (c.ts, c.id) for o in test["overlap"])
        waiting = any(rolled_back_lately(o) for o in test["overlap"])
        test["would_have_done"] = {
            "targeted": hurt and not TARGETED_REASONS.isdisjoint(test["reasons"]),
            "one_at_a_time": hurt and not newer_suspect and not waiting,
        }
    return out

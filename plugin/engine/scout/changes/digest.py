"""``scoutctl changes digest``: the "What Scout is working on" document (spec §10).

Assembled deterministically from the two reports and the work queue; no model call, so it can't drift from the data.
``build_digest`` returns the v1 document the app's Changes tab and the weekly DM share; ``render_text`` is the plain
text layout, at most 12 lines, with empty sections left out. Readers never raise."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scout.changes.models import SCHEMA_VERSION
from scout.changes.report import changes_report, engagement_report
from scout.changes.settings import read_settings
from scout.yaml_safe import load_yaml

MAX_ITEMS = 3
MAX_LINES = 12
WEEK = 7
_PRIORITY = {"high": 0, "medium": 1, "low": 2}
_OPEN_STATES = frozenset({"open", "in-progress"})
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_BARS = "▁▂▃▄▅▆▇█"


def _local_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def _wishlist(vault: Path) -> list[dict[str, str]]:
    """Open or in-progress items of ``docs/wishlist/*.md``, high priority first, then oldest; a file that doesn't parse
    is skipped."""
    items: list[tuple[int, str, str, str]] = []
    folder = vault / "docs" / "wishlist"
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parts = text.split("---\n", 2) if text.startswith("---\n") else []
        if len(parts) < 3:
            continue
        try:
            meta = load_yaml(parts[1])
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict) or str(meta.get("status", "")).lower() not in _OPEN_STATES:
            continue
        title = meta.get("title")
        rank = _PRIORITY.get(str(meta.get("priority", "")).lower(), len(_PRIORITY))
        items.append(
            (rank, str(meta.get("date", "")), path.stem, title if isinstance(title, str) and title else path.stem)
        )
    items.sort(key=lambda i: (i[0], i[1], i[2]))
    return [{"slug": slug, "title": title} for _, _, slug, title in items[:MAX_ITEMS]]


def build_digest(
    vault: Path,
    env: Mapping[str, str],
    *,
    now: datetime,
    tz: ZoneInfo,
    owner_vault: Path | None = None,
) -> dict:
    """The digest of the 7 local days ending today (``tz``) against the 7 before; see the module docstring.

    Each report is read once. Engagement covers 14 local days; changes reach back far enough to hold a test that was
    reviewed in the last 7 days (the review window plus 7 days), so "what it learned" can see it."""
    today = now.astimezone(tz).date()
    week_start = today - timedelta(days=WEEK - 1)
    review_days = read_settings(env).review_days
    engagement = engagement_report(
        vault, env, now=now, since=_local_start(today - timedelta(days=2 * WEEK - 1), tz), tz=tz
    )
    changes = changes_report(
        vault,
        now=now,
        since=_local_start(today - timedelta(days=review_days + WEEK), tz),
        env=env,
        tz=tz,
        owner_vault=owner_vault,
    )

    by_date = {d["date"]: d for d in engagement["days"]}

    def total(first: date, field: str) -> int:
        return sum(by_date[(first + timedelta(days=i)).isoformat()][field] for i in range(WEEK))

    def worked(first: date) -> int:
        return total(first, "feedback") + total(first, "reliance")

    this, last = worked(week_start), worked(week_start - timedelta(days=WEEK))
    days = [
        {
            "date": (week_start + timedelta(days=i)).isoformat(),
            "engagement": by_date[(week_start + timedelta(days=i)).isoformat()]["feedback"]
            + by_date[(week_start + timedelta(days=i)).isoformat()]["reliance"],
            "noise": by_date[(week_start + timedelta(days=i)).isoformat()]["noise"],
        }
        for i in range(WEEK)
    ]

    rows = changes["changes"]
    testing_rows = sorted(
        (c for c in rows if c["test"]["state"] == "testing"), key=lambda c: (c["test"]["review_on"], c["id"])
    )
    testing_ids = {c["id"] for c in testing_rows}
    shipped_rows = [
        c
        for c in rows  # newest first already
        if c["id"] not in testing_ids and datetime.fromisoformat(c["ts"]).astimezone(tz).date() >= week_start
    ]

    first, last_day = week_start.isoformat(), today.isoformat()  # ISO dates compare as text
    learned_rows = [
        c
        for c in rows
        if c["test"]["suspect"] or (c["test"]["state"] == "done" and first <= c["test"]["review_on"] <= last_day)
    ]
    learned_rows.sort(key=lambda c: (c["test"]["review_on"] or "", c["ts"]), reverse=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "week_of": (today - timedelta(days=today.weekday())).isoformat(),
        "verified": changes["verified"],
        "engagement": {
            "this_week": this,
            "last_week": last,
            "change_pct": round((this - last) * 100 / last) if last else None,
            "feedback": total(week_start, "feedback"),
            "reliance": total(week_start, "reliance"),
        },
        "noise": {
            "this_week": total(week_start, "noise"),
            "last_week": total(week_start - timedelta(days=WEEK), "noise"),
        },
        "days": days,
        "testing": [
            {
                "id": c["id"],
                "summary": c["summary"],
                "days_left": max(0, (date.fromisoformat(c["test"]["review_on"]) - today).days),
            }
            for c in testing_rows[:MAX_ITEMS]
        ],
        "shipped": [{"id": c["id"], "summary": c["summary"]} for c in shipped_rows[:MAX_ITEMS]],
        "learned": [
            {
                "id": c["id"],
                "summary": c["summary"],
                "verdict": c["test"]["verdict"],
                "suspect": bool(c["test"]["suspect"]),
            }
            for c in learned_rows[:MAX_ITEMS]
        ],
        "up_next": _wishlist(vault),
        "needs_ok": sum(1 for n in changes["needs_ok"] if n["state"] == "awaiting-ok"),
    }


def _arrow(delta: int) -> str:
    return "↑" if delta > 0 else "↓" if delta < 0 else "→"


def _sparkline(values: list[int]) -> str:
    top = max(values)
    return "".join(_BARS[min(len(_BARS) - 1, round(v * (len(_BARS) - 1) / top))] for v in values)


def _learned_line(row: dict) -> str:
    if row["suspect"]:
        return f"  • ⚠ Suspect: {row['summary']} (Undo in the Changes tab)"
    verdict = str(row["verdict"] or "").strip()
    if verdict == "helped":
        return f"  • ✅ Helped: {row['summary']}"
    return f"  • {verdict.capitalize()}: {row['summary']}"


def render_text(doc: dict) -> str:
    """The digest as plain text: at most 12 lines, empty sections omitted, no bare change ids (summaries only).

    When every section is full the lists are trimmed from the longest one's tail, so each section keeps a line."""
    day = date.fromisoformat(doc["week_of"])
    eng, noise = doc["engagement"], doc["noise"]
    if eng["change_pct"] is None:
        vs = "new this week" if eng["this_week"] else "nothing last week"
    else:
        vs = f"{_arrow(eng['change_pct'])} {abs(eng['change_pct'])}% vs last"
    delta = noise["this_week"] - noise["last_week"]
    noise_vs = "same" if delta == 0 else f"{_arrow(delta)} {abs(delta)}"
    head = [
        f"What Scout is working on — week of {_MONTHS[day.month - 1]} {day.day}",
        f"Your engagement: {eng['this_week']} this week ({vs}), noise {noise['this_week']} ({noise_vs})",
    ]
    if any(d["engagement"] for d in doc["days"]):
        spark = _sparkline([d["engagement"] for d in doc["days"]])
        head.append(f"  {spark}  feedback {eng['feedback']} · sessions that used Scout {eng['reliance']}")

    sections: list[tuple[str, list[str]]] = []

    def add(title: str, lines: list[str]) -> None:
        if lines:
            sections.append((title, lines))

    add(
        f"Testing now ({len(doc['testing'])})",
        [f"  • {t['summary']} — {t['days_left']} day{'' if t['days_left'] == 1 else 's'} left" for t in doc["testing"]],
    )
    add(f"Just shipped ({len(doc['shipped'])})", [f"  • {s['summary']}" for s in doc["shipped"]])
    # A suspect change leads, so trimming a full document never drops the one the owner may want to undo.
    add("What it learned", [_learned_line(row) for row in sorted(doc["learned"], key=lambda r: not r["suspect"])])
    add("Up next", [f"  • {w['title']}" for w in doc["up_next"]])
    tail = [f"Needs your OK ({doc['needs_ok']}) — open the Changes tab"] if doc["needs_ok"] else []

    budget = MAX_LINES - len(head) - len(tail)
    while sum(1 + len(lines) for _, lines in sections) > budget:
        longest = max(len(lines) for _, lines in sections)
        next(lines for _, lines in reversed(sections) if len(lines) == longest).pop()
    out = list(head)
    for title, lines in sections:
        out.append(title)
        out.extend(lines)
    return "\n".join([*out, *tail])

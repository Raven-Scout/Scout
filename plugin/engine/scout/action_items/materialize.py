"""Materialize today's daily action-items file from the most recent prior day.

Daily-file completeness invariant: once ``action-items-<today>.md`` exists it
must contain the full carried-forward item list — never a stub that points at
a previous day's file. Companion surfaces (scout-app, the TUI) render the
daily file as the whole truth, so a pointer makes every open item invisible
until the next briefing rewrites the file. The motivating incident
(mistake-audit Pattern #110): a lightweight auxiliary session was the day's
first writer and created a partial file whose "all other items" section said
"carry forward in full from yesterday — see that file", while the morning
briefing — delayed because the host slept through its slot — took another
half hour to rewrite it; the user opened the app inside that window and saw a
5-item stub instead of ~100 open items.

This module is the deterministic backstop: the newest prior daily file
(looking back up to ``LOOKBACK_DAYS``) carried forward under a fresh date
header and a provisional banner. The body is compacted to an items-only view
(see ``compact``) when that provably preserves every item's status, priority
and details; otherwise it falls back to a verbatim copy of the prior file so
nothing is silently dropped. No LLM involved, idempotent (no-op when today's
file exists), and quiet when there is nothing to do — runner preambles invoke
it best-effort before every session.

Creating the day's file is also when items untouched for 60+ days leave it
(``scout.action_items.stale``): once a day, before any session reads it.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from scout import paths
from scout.action_items.parser import parse_lines
from scout.config import pinned_eval_day, resolve_timezone
from scout.errors import ConfigError
from scout.kb.lint_rules import is_diary_heading

LOOKBACK_DAYS = 7

_BANNER = (
    "**Mechanical carry-forward** — materialized at {now} from "
    "[[action-items-{prev}]] (items carried forward, not re-verified). ⏳ The "
    "next briefing/consolidation rewrites this file in full; until then every "
    "open item below carries as-is so nothing is invisible."
)


def _human_date(d: _dt.date) -> str:
    # Avoid strftime's platform-dependent no-pad flag (%-d vs %#d).
    return f"{d.strftime('%A')}, {d.strftime('%b')} {d.day}, {d.year}"


def _carry_body(prev_file: Path) -> str:
    """Previous file's content minus its H1 and, if present, the bold
    "**<session>** — Last updated …" header line that conventionally follows."""
    lines = prev_file.read_text(encoding="utf-8").splitlines(keepends=True)
    skip = 1
    if len(lines) > 1 and lines[1].lstrip().startswith("**"):
        skip = 2
    return "".join(lines[skip:])


_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
_WEEKDAYS = "Mon|Tue|Tues|Wed|Weds|Thu|Thur|Thurs|Fri|Sat|Sun"
# A day label in an H2: "(Saturday Oct 17)", "— Sat Oct 17, 2026", "— Oct 17".
_DAY_LABEL = re.compile(
    rf"(?:\b(?:{_WEEKDAYS})[a-z]*\.?,?\s+)?"
    rf"\b(?:{_MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:,?\s+20\d{{2}})?\b"
)
# A date introduced by one of these is a window boundary or a provenance stamp,
# not the section's own day: "(since May 3 …)", "From chrome sweep (Wed Sep 9 …".
_WINDOW_PREP = re.compile(
    r"\b(?:since|from|between|through|until|as of|after|before|window|vs\.?)\b",
    re.IGNORECASE,
)
_CARRIED_MARK = " — ⚠️ carried from {prev}, not re-verified"
_MONTH_NUMBER = {n: i for i, n in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1)}


def _mark_carried_day_headings(body: str, prev_date: _dt.date) -> str:
    """Annotate every ``## `` heading that names the *previous* file's day.

    The carry-forward re-dates the H1 and writes a fresh banner; until now that
    was the whole of it, so a heading like ``## 📅 Today's Meetings (Saturday
    Oct 17)`` travelled verbatim into Sunday's file and asserted the wrong day
    one screen below a correct title. Measured over one vault: 39 of 193 daily
    files carried at least one such heading.

    We deliberately **mark** rather than re-date. The content under the heading
    is still yesterday's — re-dating the label would make a visible staleness
    invisible, which is strictly worse than the defect. The mark says the same
    thing the banner already says about the body, at the place a reader is
    actually looking.
    """
    out = []
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if not stripped.startswith("## ") or _CARRIED_MARK.split("{")[0] in stripped:
            out.append(line)
            continue
        hit = None
        for m in _DAY_LABEL.finditer(stripped):
            if _WINDOW_PREP.search(stripped[: m.start()]):
                continue
            hit = m
            break
        if hit and _names_date(hit.group(0), prev_date):
            nl = "\n" if line.endswith("\n") else ""
            out.append(stripped + _CARRIED_MARK.format(prev=prev_date.isoformat()) + nl)
        else:
            out.append(line)
    return "".join(out)


def _names_date(label: str, when: _dt.date) -> bool:
    """Does this free-text day label resolve to ``when``?

    The year is optional in house style, so an absent year is taken from
    ``when`` — inside a dated daily file that is never ambiguous.
    """
    m = re.search(rf"\b({_MONTHS})[a-z]*\.?\s+(\d{{1,2}})(?:,?\s+(20\d{{2}}))?", label)
    if not m:
        return False
    mon = _MONTH_NUMBER.get(m.group(1)[:3].title())
    if not mon:
        return False
    try:
        return _dt.date(int(m.group(3)) if m.group(3) else when.year, mon, int(m.group(2))) == when
    except ValueError:
        return False


_H2 = re.compile(r"^##\s")
_H3 = re.compile(r"^###\s")
_H4PLUS = re.compile(r"^#{4,}\s")
_TOP_BULLET = re.compile(r"^-\s")


def compact(body: str) -> str:
    """Items-only view of a daily file body (spec §3.6): headings that directly
    hold items, the items, and their indented children (comments included).
    Run narration, tables, digests and prose are dropped — git keeps them."""
    out: list[str] = []
    pending: list[str] = []
    in_item = False
    in_fence = False
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            in_item = False
            continue
        if in_fence:
            continue
        if _H4PLUS.match(line):
            in_item = False
            continue
        if _H3.match(line):
            in_item = False
            if not is_diary_heading(line):
                pending = [p for p in pending if _H2.match(p)] + [line]
            continue
        if _H2.match(line):
            in_item = False
            # A run-diary H2 is narration: drop the heading (its items follow
            # the previous section; the signature check guards their status).
            pending = [] if is_diary_heading(line) else [line]
            continue
        if _TOP_BULLET.match(line):
            for h in pending:
                if out:
                    out.append("")
                out.append(h)
            pending = []
            out.append(line)
            in_item = True
            continue
        if in_item and line.strip() and line[:1].isspace():
            out.append(line)
            continue
        if line.strip():
            in_item = False
    return "\n".join(out) + ("\n" if out else "")


def _signature(body: str) -> list[tuple[str, str, str, str, tuple[str, ...]]]:
    # The parsed section is part of an item's identity: a compaction that
    # re-homes an item under a different heading falls back to verbatim.
    return sorted(
        (i.raw_line.strip(), i.status, i.priority, i.section, tuple(i.details)) for i in parse_lines(body.splitlines())
    )


def _archive_stale(data_dir: Path | None, today: _dt.date, body: str) -> tuple[str, str]:
    """Move items untouched for STALE_AFTER_DAYS out of the carried body.

    Returns the body to write and a banner suffix naming what moved. Once a
    day is the right cadence: this runs only when the day's file is created.
    Best-effort — any failure carries the body verbatim, because the
    completeness backstop must never be blocked by housekeeping.
    """
    from scout.action_items import stale

    try:
        vault = paths.data_dir() if data_dir is None else data_dir
        ledger = stale.ActivityLedger.load(vault)
        ledger.catch_up(vault, before=today)
        result = stale.split_stale(body.splitlines(), ledger, today=today)
        archive = stale.write_archive(vault, today, result.archived) if result.archived else None
        ledger.save()
    except Exception:  # noqa: BLE001 — housekeeping must never block the copy
        return body, ""
    if archive is None:
        return body, ""
    count = len(result.archived)
    noun = "item" if count == 1 else "items"
    note = f" 🗄️ {count} {noun} untouched for {stale.STALE_AFTER_DAYS}+ days auto-archived → [[archive/{archive.stem}]]."
    trailing = "\n" if body.endswith("\n") else ""
    return "\n".join(result.lines) + trailing, note


def _drop_dismissed(data_dir: Path | None, body: str) -> str:
    """The body without Inbox and Loose ends entries the user dismissed.
    Best-effort, like the archive: a failure carries the body verbatim."""
    from scout.action_items.dismissed import dismissed_keys
    from scout.action_items.side_entries import drop_dismissed

    try:
        dismissed = set(dismissed_keys(data_dir))
    except Exception:  # noqa: BLE001 — housekeeping must never block the copy
        return body
    lines = body.splitlines()
    kept = drop_dismissed(lines, dismissed)
    if len(kept) == len(lines):
        return body
    trailing = "\n" if body.endswith("\n") else ""
    return "\n".join(kept) + trailing


def materialize(
    data_dir: Path | None = None,
    date: _dt.date | None = None,
) -> Path | None:
    """Ensure the daily file for ``date`` (default: today in the configured
    timezone) exists and is complete.

    Returns the created path, or None when there was nothing to do (the file
    already exists, or no prior daily file exists within LOOKBACK_DAYS).
    """
    # resolve_timezone never raises — the backstop must never block a run
    # over a config problem (fallback lives inside the resolver; #207).
    tz = resolve_timezone(data_dir)
    now = _dt.datetime.now(tz)
    try:
        pinned = pinned_eval_day()  # EVAL-ONLY pinned day (config.EVAL_TODAY_ENV); None on real vaults
    except ConfigError:
        pinned = None  # never block a run over config
    target_date = date or pinned or now.date()
    target = paths.action_items_daily_path(data_dir, date=target_date)

    if target.exists():
        return None
    if not target.parent.is_dir():
        return None

    for days_back in range(1, LOOKBACK_DAYS + 1):
        prev_date = target_date - _dt.timedelta(days=days_back)
        prev = paths.action_items_daily_path(data_dir, date=prev_date)
        if not prev.exists():
            continue
        banner = _BANNER.format(
            now=now.strftime("%H:%M %Z"),
            prev=prev_date.isoformat(),
        )
        body = _carry_body(prev)
        # Before anything reshapes the body: a `## ` heading that names the day
        # we are carrying *from* is about to travel into a file dated something
        # else. Mark it; never re-date it (see _mark_carried_day_headings).
        body = _mark_carried_day_headings(body, prev_date)
        # Archive before compacting: the ledger fingerprinted the prior file as
        # written, and compaction drops whatever headings the archive emptied.
        body, archived_note = _archive_stale(data_dir, target_date, body)
        body = _drop_dismissed(data_dir, body)
        compacted = compact(body)
        if _signature(compacted) == _signature(body):
            body = "\n" + compacted
        content = f"# Action Items — {_human_date(target_date)}\n{banner}{archived_note}\n{body}"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        return target

    return None

"""UserPromptSubmit hook — pre-session KB staleness scorer.

Started as a port of ~/Scout/hooks/kb-pre-filter.sh:
  - Walks $SCOUT_DATA_DIR/knowledge-base/, classifying each *.md file
    as STALE / NO_DATE / FRESH against a per-file freshness budget. The date
    comes from the frontmatter `last_updated:` key, else the prose
    "Last updated" line, read in any zone a run writes (#201).
  - Writes $SCOUT_DATA_DIR/.scout-cache/kb-filter.md so the SCOUT skill
    can read this cache instead of re-scanning the filesystem.
  - Exits 0 even on partial failure (single bad file doesn't block the session).

Discovery exclusions are layered to match the bash:
  - find-level: */ontology/*, *archive*, */personal/*
  - per-file basename skip: review-queue.md, archived.md, *-archive*,
    *-draft*, *-prompt*
  - per-file rel-path skip: */people/*.md (entity files)
  - per-item record folders: knowledge-base/{scout-mistake-audit,
    research-queue,session-log,kg-audits,review-queue}/ (#201)

Hooks must NEVER raise — main() catches all exceptions and returns 0.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scout import paths
from scout.config import resolve_timezone
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _boundary_zone() -> ZoneInfo:
    """Configured zone for freshness math (was hardcoded ET; #207).

    The bash original used the system TZ implicitly when parsing wall-clock
    dates with `date -j -f ... +%s`, then subtracted UTC-epoch seconds. We
    replicate the UTC-epoch arithmetic to stay correct across DST — but the
    zone is now the configured one, resolved per run.
    """
    return resolve_timezone()


# Per-filename freshness budget (in hours). Bash lines 33-37.
FRESHNESS_OVERRIDES: dict[str, int] = {
    "linear-issues.md": 6,
    "knowledge-base.md": 6,
    "people.md": 168,
    "channels.md": 336,
    "ai-costs.md": 168,
    "ai-landscape.md": 168,
}

# Priority emoji → freshness budget (in hours). Bash lines 43-46.
PRIORITY_FRESHNESS: dict[str, int] = {
    "🔴": 72,
    "🟡": 168,
    "🟢": 336,
}

# Default freshness budget for project files with no priority frontmatter.
DEFAULT_FRESHNESS_HOURS = 168

# Per-file basename skip rules. Bash line 90.
SKIP_BASENAMES: tuple[str, ...] = ("review-queue.md", "archived.md")
SKIP_BASENAME_GLOBS: tuple[str, ...] = ("*-archive*", "*-draft*", "*-prompt*")

# Find-level path exclusions. Bash lines 128-130.
SKIP_PATH_FRAGMENTS: tuple[str, ...] = ("/ontology/", "archive", "/personal/")

# knowledge-base/ folders of per-item records: mistake-audit entries, research
# and review queue items, session logs, KG audit reports. Each record has its
# own lifecycle (a `status:`, a creation `date:`), not a freshness budget, and
# together they buried the real KB documents in the NO DATE list (#201).
SKIP_KB_SUBDIRS: frozenset[str] = frozenset(
    {"scout-mistake-audit", "research-queue", "session-log", "kg-audits", "review-queue"}
)

# How many lines to scan from the file head for the prose date line and the
# priority marker. Bash uses head -25.
HEAD_SCAN_LINES = 25

# How far classify() reads, so a `last_updated:` key at the bottom of a long
# frontmatter block is still found (#230). The prose and priority scans stay
# within HEAD_SCAN_LINES.
FRONTMATTER_SCAN_LINES = 200

# Zone abbreviations a run may append to a prose date, mapped to the zone they
# name. Anything else after the time is ignored and the configured zone
# applies. Left out on purpose: IST (India / Ireland) is ambiguous; CST is read
# as US Central, Scout's default region.
TZ_ABBREVIATIONS: dict[str, str] = {
    **dict.fromkeys(("ET", "EST", "EDT"), "America/New_York"),
    **dict.fromkeys(("CT", "CST", "CDT"), "America/Chicago"),
    **dict.fromkeys(("MT", "MST", "MDT"), "America/Denver"),
    **dict.fromkeys(("PT", "PST", "PDT"), "America/Los_Angeles"),
    **dict.fromkeys(("UTC", "GMT"), "UTC"),
    **dict.fromkeys(("CET", "CEST"), "Europe/Berlin"),
    **dict.fromkeys(("WET", "WEST"), "Europe/Lisbon"),
    **dict.fromkeys(("EET", "EEST"), "Europe/Athens"),
    "BST": "Europe/London",
    "JST": "Asia/Tokyo",
    **dict.fromkeys(("AEST", "AEDT"), "Australia/Sydney"),
}

# A full ISO 8601 timestamp anywhere in the text (`2026-08-12T10:20:00+02:00`).
_ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")

# A date, then optionally a time and a zone abbreviation, found anywhere in the
# text, so a leading weekday or a trailing note can't defeat it. The time takes
# `~` (approximate) and an `x` last digit (`9:3x`), which runs write. The
# meridiem is matched before the zone, so `PM` is never read as one.
_DATE_RE = re.compile(
    r"(?:(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4}))"
    r"(?:(?:\s+|,\s*)(?:at\s+)?~?(?P<hour>\d{1,2}):(?P<minute>\d[\dxX])(?::\d{2})?"
    r"(?:\s*(?P<meridiem>[AaPp])\.?[Mm]\.?\b)?)?"
    r"(?:\s+(?P<zone>[A-Z]{2,4})\b)?"
)

_FRONTMATTER_KEY_RE = re.compile(r"^(last_updated|last_verified):\s*(.*)$")


# -- helpers -----------------------------------------------------------------


def _read_head(path: Path, n: int = HEAD_SCAN_LINES) -> list[str]:
    """Read up to n lines from path. Returns [] on any read error."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head: list[str] = []
            for i, line in enumerate(f):
                if i >= n:
                    break
                head.append(line.rstrip("\n"))
            return head
    except OSError:
        return []


# -- public API --------------------------------------------------------------


def freshness_hours_for(path: Path, *, lines: list[str] | None = None) -> int:
    """Compute the freshness budget (hours) for a KB file.

    Bash lines 28-50. Special-cased basenames take precedence; everything else
    falls back to YAML frontmatter `priority:` matching by emoji substring.

    Optional `lines` parameter: when provided (pre-read by the caller), skips
    the internal _read_head call. Pass `lines` from classify() to avoid reading
    the file twice per classify (#78).
    """
    name = path.name
    if name in FRESHNESS_OVERRIDES:
        return FRESHNESS_OVERRIDES[name]

    # Look for priority in the first 25 lines.
    head = lines if lines is not None else _read_head(path)
    for line in head:
        # Bash: grep -i 'priority:' | head -1 | sed 's/.*priority: *//' | tr -d '"'
        m = re.search(r"priority:\s*(.*)", line, re.IGNORECASE)
        if m:
            value = m.group(1).replace('"', "")
            for emoji, hours in PRIORITY_FRESHNESS.items():
                if emoji in value:
                    return hours
            return DEFAULT_FRESHNESS_HOURS
    return DEFAULT_FRESHNESS_HOURS


def extract_date_string(path: Path, *, lines: list[str] | None = None) -> str:
    """Extract the cleaned date string from a "Last Updated" / "Last Verified" line.

    Bash lines 99-106 — heavy sed cleanup. Replicates:
      1. head -25 | grep -i 'last updated\\|last verified' | head -1
      2. strip ** markers
      3. strip everything up through the first ':' followed by space
      4. strip '. Source...' / '. Verified...' (case-insensitive)
      5. strip ' (...' parentheticals
      6. trim whitespace

    Optional `lines` parameter: when provided (pre-read by the caller), skips
    the internal _read_head call. Pass `lines` from classify() to avoid reading
    the file twice per classify (#78).
    """
    return _prose_date_line(lines if lines is not None else _read_head(path))


def _prose_date_line(head: list[str]) -> str:
    """The cleaned date text from the first "Last updated" / "Last verified" line in ``head``."""
    line = ""
    for raw in head:
        # Single space (not \s+) for strict bash parity — bash uses literal " ".
        if re.search(r"last updated|last verified", raw, re.IGNORECASE):
            line = raw
            break
    if not line:
        return ""

    # 1. Strip bold markers
    line = line.replace("**", "")
    # 2. Strip everything through the first ':' followed by space (label prefix).
    #    Bash: sed 's/^[^:]*: *//'
    m = re.match(r"^[^:]*:\s*(.*)$", line)
    if m:
        line = m.group(1)
    # 3. Strip ". Source..." / ". Verified..." (case-insensitive)
    line = re.sub(r"\.\s*Source.*$", "", line, flags=re.IGNORECASE)
    line = re.sub(r"\.\s*Verified.*$", "", line, flags=re.IGNORECASE)
    # 4. Strip " (...)" parenthetical (and anything after)
    line = re.sub(r"\s*\(.*$", "", line)
    return line.strip()


def _month_day_year(mon: str, day: str, year: str) -> datetime | None:
    # %B takes "September", %b takes "Sep"; mon[:3] also covers "Sept".
    for text, fmt in ((mon, "%B"), (mon, "%b"), (mon[:3], "%b")):
        try:
            return datetime.strptime(f"{text} {day} {year}", f"{fmt} %d %Y")
        except ValueError:
            continue
    return None


def parse_date(s: str, tz: ZoneInfo | None = None) -> datetime | None:
    """Find the first date in ``s`` and return it zone-aware, or None.

    Reads the renderings runs actually write (#201): an ISO 8601 timestamp
    with its offset; or a date (``2026-08-12`` / ``August 12, 2026``) with an
    optional time (``~9:30 PM``, ``09:3x``) and zone abbreviation (``CEST``,
    ``ET``). A known abbreviation sets the zone; otherwise the wall-clock time
    is read in ``tz`` (default: the configured zone). An impossible time is
    dropped and the date kept. Callers in a loop should resolve the zone once
    and pass it in.
    """
    if not s:
        return None
    text = s.replace("**", "")
    zone = tz or _boundary_zone()

    m = _ISO_TIMESTAMP_RE.search(text)
    if m:
        try:
            parsed = datetime.fromisoformat(m.group(0))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)

    for m in _DATE_RE.finditer(text):
        if m.group("iso"):
            try:
                day = datetime.fromisoformat(m.group("iso"))
            except ValueError:
                continue
        else:
            found = _month_day_year(m.group("mon"), m.group("day"), m.group("year"))
            if found is None:
                continue
            day = found
        named = TZ_ABBREVIATIONS.get(m.group("zone") or "")
        when_zone = ZoneInfo(named) if named else zone
        if m.group("hour") is None:
            return day.replace(tzinfo=when_zone)
        hour = int(m.group("hour"))
        minute = int(m.group("minute").lower().replace("x", "0"))
        meridiem = (m.group("meridiem") or "").lower()
        if meridiem and 1 <= hour <= 12:
            hour = hour % 12 + (12 if meridiem == "p" else 0)
        if hour > 23 or minute > 59:
            return day.replace(tzinfo=when_zone)
        return day.replace(hour=hour, minute=minute, tzinfo=when_zone)
    return None


def _frontmatter_value(lines: list[str]) -> str:
    """The ``last_updated:`` value from the YAML frontmatter, else ``last_verified:``, else ""."""
    if not lines or lines[0].strip() != "---":
        return ""
    found: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = _FRONTMATTER_KEY_RE.match(line)
        if m and m.group(1) not in found:
            # Drop a YAML comment (` # …`) and surrounding quotes.
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip("\"'")
            found[m.group(1)] = value
    return found.get("last_updated") or found.get("last_verified") or ""


def resolve_date(lines: list[str], tz: ZoneInfo) -> tuple[datetime | None, str, str]:
    """Return ``(when, source, raw)`` for a KB file's head lines.

    ``source`` is ``"property"`` for the frontmatter ``last_updated:`` (or
    ``last_verified:``) key that kb-management.md asks every file to carry,
    ``"prose"`` for the older ``**Last updated:** …`` line, or ``"none"``. The
    property wins; an unreadable property falls back to the prose line.
    """
    value = _frontmatter_value(lines)
    if value:
        when = parse_date(value, tz=tz)
        if when is not None:
            return when, "property", value
    prose = _prose_date_line(lines[:HEAD_SCAN_LINES])
    if prose:
        when = parse_date(prose, tz=tz)
        if when is not None:
            return when, "prose", prose
    return None, "none", prose or value


def discover_kb_files(scout_dir: Path) -> list[Path]:
    """Walk knowledge-base/ and return the sorted list of *.md files to evaluate.

    Replicates bash find filters + per-file skip rules. Output is sorted
    alphabetically by full path (matches `find ... | sort` in bash line 131).
    """
    kb_root = scout_dir / "knowledge-base"
    if not kb_root.is_dir():
        return []

    candidates: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(kb_root, followlinks=False):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            p = Path(dirpath) / fname
            if not p.is_file():
                continue
            rel_posix = p.relative_to(scout_dir).as_posix()

            # Find-level exclusions: */ontology/*, *archive*, */personal/*
            if "/ontology/" in rel_posix:
                continue
            if "archive" in rel_posix:
                continue
            if "/personal/" in rel_posix:
                continue

            # Per-file basename exact-match skip
            name = p.name
            if name in SKIP_BASENAMES:
                continue
            # Per-file basename glob skip
            if any(fnmatch.fnmatchcase(name, g) for g in SKIP_BASENAME_GLOBS):
                continue
            # Per-file rel-path skip: */people/*.md (entity files; top-level
            # people.md is allowed because there's no subdir segment)
            if "/people/" in rel_posix:
                continue
            # Per-item record folders directly under knowledge-base/.
            parts = rel_posix.split("/")
            if len(parts) > 2 and parts[1] in SKIP_KB_SUBDIRS:
                continue

            candidates.append(p)

    candidates.sort()
    return candidates


def classify(path: Path, now: datetime, scout_dir: Path, tz: ZoneInfo | None = None) -> tuple[str, dict[str, Any]]:
    """Classify a single file as STALE / FRESH / NO_DATE.

    Returns (label, details). For STALE/FRESH, details has age_hours,
    budget_hours, datestr, rel. For NO_DATE, details has rel only.

    Reads the file head once and passes the result to both extract_date_string
    and freshness_hours_for to avoid opening the file twice per classify (#78).
    """
    rel = path.relative_to(scout_dir).as_posix()
    # Read once, far enough for long frontmatter (#230); share with both
    # helpers to avoid double I/O (#78).
    head_lines = _read_head(path, n=FRONTMATTER_SCAN_LINES)
    zone = tz or _boundary_zone()
    parsed, source, datestr = resolve_date(head_lines, zone)
    if parsed is None:
        return ("NO_DATE", {"rel": rel})

    # Bash interpreted the wall-clock date in the system zone via `date -j -f`
    # then subtracted UTC-epoch seconds. We do the same in the configured zone:
    # parse_date returns a zone-aware datetime; attach the zone to `now` if
    # naive, then subtract via .timestamp() to get UTC-elapsed seconds (NOT
    # wall-clock seconds — same-zone aware subtraction in Python returns
    # wall-clock delta, which drifts 1h across DST boundaries).
    now_aware = now if now.tzinfo is not None else now.replace(tzinfo=zone)
    age_seconds = now_aware.timestamp() - parsed.timestamp()
    age_hours = int(age_seconds // 3600)
    budget = freshness_hours_for(path, lines=head_lines[:HEAD_SCAN_LINES])

    label = "STALE" if age_hours > budget else "FRESH"
    return (
        label,
        {
            "rel": rel,
            "age_hours": age_hours,
            "budget_hours": budget,
            "datestr": datestr,
            "source": source,
        },
    )


def _count_sources(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"property": 0, "prose": 0}
    for entry in entries:
        source = entry.get("source", "prose")
        counts[source] = counts.get(source, 0) + 1
    return counts


def render_output(
    stale: list[dict[str, Any]],
    no_date: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    *,
    session_type: str,
    now_et: str,
) -> str:
    """Render the kb-filter.md content. Mirrors bash lines 134-164.

    Adds two lines the bash never had (#201): a warning when no file has a
    readable date, so a staleness check that read nothing can't look like a
    healthy KB, and where the dates came from, so the prose fallback is
    visible as files move to the ``last_updated:`` property.
    """
    lines: list[str] = [f"# KB Pre-Filter — {now_et} ({session_type})", ""]
    dated = stale + fresh
    if no_date and not dated:
        lines.append(
            f"> ⚠ None of the {len(no_date)} KB files has a readable date — the staleness check did not run. "
            "Give each file a `last_updated:` frontmatter key in ISO 8601 with an offset "
            "(e.g. `2026-08-12T10:20:00+02:00`)."
        )
        lines.append("")

    if stale:
        lines.append("## STALE — Need reading/audit")
        for entry in stale:
            lines.append(
                f"- **{entry['rel']}** — {entry['age_hours']}h old "
                f"(standard: {entry['budget_hours']}h) — last: {entry['datestr']}"
            )
        lines.append("")

    if no_date:
        lines.append("## NO DATE — Need checking")
        for entry in no_date:
            lines.append(f"- {entry['rel']}")
        lines.append("")

    # FRESH section is always written, even when empty (bash line 156 has no guard).
    lines.append("## FRESH — Skip unless feedback signals")
    for entry in fresh:
        lines.append(f"- {entry['rel']} ({entry['age_hours']}h old)")

    lines.append("")
    lines.append("---")
    lines.append(f"Stale: {len(stale)} | No date: {len(no_date)} | Fresh: {len(fresh)}")
    if dated:
        by = _count_sources(dated)
        lines.append(f"Dates read from: {by['property']} last_updated property, {by['prose']} prose line")
    # Trailing newline to match bash `echo` semantics.
    return "\n".join(lines) + "\n"


def run(
    session_type: str = "dreaming",
    *,
    now: datetime | None = None,
) -> Event | None:
    """Score the KB and write .scout-cache/kb-filter.md.

    Returns:
        Event in all paths where the KB dir exists (including empty KB).
        None when knowledge-base/ does not exist (truly unrecoverable input).
    """
    scout_dir = paths.data_dir()
    kb_root = scout_dir / "knowledge-base"
    if not kb_root.is_dir():
        return None

    # Resolve the configured zone ONCE per run and thread it through — the
    # classify loop would otherwise re-read the config per KB file.
    zone = _boundary_zone()
    if now is None:
        now = datetime.now(zone)
    now_et = now.strftime("%Y-%m-%d %H:%M %Z")

    files = discover_kb_files(scout_dir)
    stale: list[dict[str, Any]] = []
    no_date: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []

    for f in files:
        try:
            label, details = classify(f, now, scout_dir, tz=zone)
        except Exception:
            # One bad file must not block the rest. Treat as NO_DATE.
            label = "NO_DATE"
            details = {"rel": f.relative_to(scout_dir).as_posix()}
        if label == "STALE":
            stale.append(details)
        elif label == "FRESH":
            fresh.append(details)
        else:
            no_date.append(details)

    content = render_output(stale, no_date, fresh, session_type=session_type, now_et=now_et)
    cache_dir = scout_dir / ".scout-cache"
    out_path = cache_dir / "kb-filter.md"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError:
        # Best-effort — never raise from a hook.
        pass

    payload = {
        "stale": len(stale),
        "no_date": len(no_date),
        "fresh": len(fresh),
        "dated_by": _count_sources(stale + fresh),
        "session_type": session_type,
        "output_path": str(out_path),
    }
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="kb_pre_filter.scored",
        source="hook:kb-pre-filter",
        payload=payload,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: scoutctl hook kb-pre-filter [session-type].

    Always returns 0 — hooks must NEVER block a session.
    """
    args = argv if argv is not None else sys.argv[1:]
    session_type = args[0] if args else "dreaming"
    try:
        event = run(session_type=session_type)
        if event is not None:
            payload = event.payload
            print(
                f"KB pre-filter written to {payload['output_path']} "
                f"({payload['stale']} stale, {payload['fresh']} fresh, "
                f"{payload['no_date']} undated)"
            )
    except Exception:
        # Hooks must never break a session.
        pass
    return 0

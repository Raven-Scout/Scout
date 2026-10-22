#!/usr/bin/env python3
"""recurring-task-status.py — compute per-entity status for recurring_task entities.

Reads `knowledge-base/recurring-tasks/*.md`, parses YAML frontmatter, and emits a
status report (markdown table by default; pass `--json` for machine output).

This is Stage 4 of the "Recurring-task primitive" Wishlist item. The
completion-evidence lookup (Linear API / Slack search) is left to the caller —
this script handles the *local* computation only: given an entity's cadence,
surface_window, and last_completed_date, decide whether the task is
`due`, `surfacing`, `overdue`, or `done` *for today*.

Stage 4b input hook: a live caller (briefing/consolidation, which has Linear MCP
and Slack search available) looks up the real completion date and passes it via
`--last-completed <slug>=<YYYY-MM-DD>` (repeatable). The override takes precedence
over the entity's stored `last_completed_date` and is NOT written back to the file
— live evidence stays caller-side; only the date math runs here.
    e.g. recurring-task-status.py --date 2026-05-29 \
            --last-completed weekly-status-update=2026-05-29

Status semantics:
    done       — last_completed_date is within the current cadence window
    surfacing  — today is inside surface_window leading up to the next due date
    due        — cadence window contains today and no completion recorded
    overdue    — cadence window has passed and no completion recorded
    upcoming   — outside surface_window; informational only

Cadence DSL (subset implemented in v1):
    daily
    weekly:<weekday>          e.g. weekly:friday
    monthly:<day>             e.g. monthly:1
    monthly:nth:<n>:<weekday> e.g. monthly:nth:2:tuesday
    quarterly:cycle-start
    yearly:<MM-DD>            e.g. yearly:06-15  (name days, birthdays, renewals)

Privacy (#453): each row carries its entity file's `visibility`, from the Scout
engine (in process, else `scoutctl kb files --json`); a private task's name is
printed as `[private] <name>`. Without either, rows are `unknown` and marked
[private]. A reader that sees nothing (SCOUT_READER=agent:...) gets no rows.

Owner: each row names who does the task, from the entity's `observed_author`,
else its `last_completed_by`, and says whether that is the vault owner (matched
against scout-config.yaml's user and connector identities). A `due`/`overdue`
row someone else owns stays `due`, but is listed under the table as theirs: a
🟢 Watching item, never the vault owner's 🔴.

Surface-window DSL (subset):
    T-0 morning  -> show on the due day itself
    T-1 day      -> show 1 day before
    T-2d through T-0   -> 2-day surfacing window
"""

from __future__ import annotations


def _run_under_a_python_with_yaml() -> None:
    """Re-exec this script under a Python that has PyYAML when this one lacks it.

    Brain files run it with the `python3` first on PATH, which a Homebrew upgrade
    leaves without PyYAML (#403). Candidates, best first: the engine's Python,
    named by the engine pointer (~/.local/state/scout/engine.json, the file the
    scoutctl launcher reads), then the vault's .venv. argv and the exit code pass
    through unchanged; the guard (shared with vault-local re-exec helpers) stops
    a second hop. With no usable interpreter, fail with one line, never a
    traceback. It runs before the imports below, so it imports its own; the
    `import yaml` guard further down stays as a backstop.
    """
    import importlib.util
    import json
    import os
    import sys
    from pathlib import Path

    if importlib.util.find_spec("yaml") is not None:
        return
    guard = "SCOUT_VENV_BOOTSTRAPPED"
    candidates = []
    if not os.environ.get(guard):
        try:
            pointer = json.loads((Path.home() / ".local/state/scout/engine.json").read_text(encoding="utf-8"))
            if isinstance(pointer, dict) and isinstance(pointer.get("python"), str):
                candidates.append(pointer["python"])
        except (OSError, ValueError):
            pass
        candidates.append(str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3"))
    script = os.path.abspath(sys.argv[0])
    for python in candidates:
        if os.access(python, os.X_OK):
            try:
                os.execve(python, [python, script, *sys.argv[1:]], dict(os.environ, **{guard: "1"}))
            except OSError:
                continue
    looked = f"tried {', '.join(candidates)}" if candidates else "already re-run once"
    print(
        f"error: recurring-task-status.py needs PyYAML, which {sys.executable} lacks, and no Scout "
        f"engine Python was usable ({looked}); run `scoutctl bootstrap upgrade` to re-record the "
        "engine in ~/.local/state/scout/engine.json, or run this script with the engine's Python.",
        file=sys.stderr,
    )
    sys.exit(2)


_run_under_a_python_with_yaml()

import argparse
import calendar
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("error: pyyaml not installed. run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class TaskStatus:
    name: str
    cadence: str
    surface_window: str
    status: str
    next_due: Optional[str]
    last_completed: Optional[str]
    # Count of cadence due-dates that fell strictly between last_completed and today.
    # Derived, not a status: it makes an elapsed lapse visible WITHOUT reclassifying
    # anything as `overdue` (which is unreachable for weekly:* cadences — see
    # count_missed_windows). Without it, a task that had already missed two windows
    # still read as `upcoming` and "nothing owed".
    windows_missed: int = 0
    domain: str = ""
    priority: str = ""
    reason: str = ""
    feeds: list = field(default_factory=list)
    # Who does this task, per the entity's own `observed_author` / `last_completed_by`
    # (see resolve_owner). Empty when the entity records neither: the vault owner's.
    owner: str = ""
    owner_source: str = ""
    owner_is_user: bool = True
    visibility: str = "normal"


# ----- privacy labels (#453) ---------------------------------------------------
# Every row carries its file's visibility from the Scout engine's classifier:
# in process when this Python can import the engine, else from
# `scoutctl kb files --json` (SCOUTCTL_BIN, then PATH). With neither, every row
# is `unknown` and printed as [private]: fail closed, never silently unlabelled.


def reader_sees_nothing() -> bool:
    """True when SCOUT_READER names a reader that may see no file (an agent, or
    a malformed value). Mirrors scout.kb.reader.resolve_reader (spec §6.2):
    only `owner` and `autonomous` see the vault."""
    reader = os.environ.get("SCOUT_READER")
    return reader is not None and reader not in ("owner", "autonomous")


def visibility_map(vault: Path, globs: list) -> "dict | None":
    """{vault-relative path: "normal" | "private"} for the files matching `globs`,
    or None when no label source works (the caller marks every row unknown)."""
    try:
        from scout.kb.read import visible_files
        from scout.kb.reader import resolve_reader
    except ImportError:
        pass
    else:
        try:
            reader = resolve_reader(None, os.environ)
            return {rel: label.visibility for rel, label in visible_files(vault, reader, globs)}
        except Exception:
            return None
    import shutil

    scoutctl = os.environ.get("SCOUTCTL_BIN") or shutil.which("scoutctl")
    if not scoutctl:
        return None
    argv = [scoutctl, "kb", "files", "--json", "--repo", str(vault)]
    for glob in globs:
        argv += ["--glob", glob]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
        rows = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            label = row.get("label") if isinstance(row.get("label"), dict) else {}
            out[row["path"]] = "normal" if label.get("visibility") == "normal" else "private"
    return out


def row_visibility(labels: "dict | None", rel: str) -> str:
    """A row's visibility: its label, `private` when the label source skipped it, `unknown` with no source."""
    if labels is None:
        return "unknown"
    return labels.get(rel, "private")


def private_mark(visibility: str) -> str:
    return "" if visibility == "normal" else "[private] "


def warn_unlabelled(script: str) -> None:
    print(
        f"{script}: can't read privacy labels (the Scout engine isn't importable here and scoutctl wasn't "
        "found or failed); every row is marked [private]. Put scoutctl on PATH or set SCOUTCTL_BIN.",
        file=sys.stderr,
    )


def vault_of(directory: Path) -> Path:
    """The vault holding `directory`: the parent of its `knowledge-base`
    ancestor, else $SCOUT_DATA_DIR, else this script's vault."""
    resolved = directory.resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name == "knowledge-base":
            return parent.parent
    env = os.environ.get("SCOUT_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    return yaml.safe_load(block) or {}


def next_due_date(cadence: str, today: date) -> Optional[date]:
    """Return the next occurrence ON OR AFTER today for the given cadence."""
    if cadence == "daily":
        return today

    if cadence.startswith("weekly:"):
        weekday_name = cadence.split(":", 1)[1].strip().lower()
        target = WEEKDAYS.get(weekday_name)
        if target is None:
            return None
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)

    if cadence.startswith("monthly:nth:"):
        # monthly:nth:<n>:<weekday>
        parts = cadence.split(":")
        if len(parts) != 4:
            return None
        try:
            n = int(parts[2])
        except ValueError:
            return None
        target = WEEKDAYS.get(parts[3].lower())
        if target is None or not 1 <= n <= 5:
            return None
        return _nth_weekday_on_or_after(today, n, target)

    if cadence.startswith("monthly:"):
        try:
            day = int(cadence.split(":", 1)[1])
        except ValueError:
            return None
        candidate = today.replace(day=min(day, 28))
        if candidate < today:
            month = today.month + 1
            year = today.year + (month > 12)
            month = ((month - 1) % 12) + 1
            candidate = date(year, month, min(day, 28))
        return candidate

    if cadence.startswith("yearly:"):
        # yearly:<MM-DD> — annual anniversaries that recur on a fixed calendar date
        # (name days, birthdays, renewals). The v1 DSL had no way to say that, so
        # an annual entity parsed as status `unknown` and never surfaced.
        month_day = _parse_month_day(cadence.split(":", 1)[1])
        if month_day is None:
            return None
        month, day = month_day
        candidate = _clamped_date(today.year, month, day)
        if candidate is not None and candidate < today:
            candidate = _clamped_date(today.year + 1, month, day)
        return candidate

    if cadence == "quarterly:cycle-start":
        # No project-cycle data here; signal "unknown".
        return None

    return None


def _parse_month_day(spec: str) -> Optional[tuple[int, int]]:
    """Parse an 'MM-DD' cadence suffix into (month, day); None if malformed."""
    parts = spec.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return (month, day)


def _clamped_date(year: int, month: int, day: int) -> Optional[date]:
    """date(year, month, day) with the day clamped to that month's last valid day.

    Keeps `yearly:02-29` meaningful in non-leap years (it lands on Feb 28) rather
    than raising and silently degrading the entity to `unknown`.
    """
    try:
        return date(year, month, day)
    except ValueError:
        pass
    try:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))
    except ValueError:
        return None


def _nth_weekday_on_or_after(today: date, n: int, weekday: int) -> Optional[date]:
    for month_offset in range(0, 3):
        month = today.month + month_offset
        year = today.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        first = date(year, month, 1)
        delta = (weekday - first.weekday()) % 7
        candidate = first + timedelta(days=delta + 7 * (n - 1))
        if candidate.month == month and candidate >= today:
            return candidate
    return None


def parse_surface_window(window: str) -> tuple[int, int]:
    """Return (days_before_start, days_before_end). T-N means N days before due."""
    w = window.strip().lower()
    if w == "t-0 morning":
        return (0, 0)
    if w in ("t-1 day", "t-1d"):
        return (1, 1)
    m = re.match(r"t-(\d+)\s*d?\s*through\s*t-(\d+)\s*d?", w)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"t-(\d+)\s*d", w)
    if m:
        return (int(m.group(1)), 0)
    return (0, 0)


def cadence_window_for_today(cadence: str, today: date) -> tuple[Optional[date], Optional[date]]:
    """Return (window_start, window_end) — the cadence period today falls into.

    For weekly:friday, the window is Sat..Fri (the week containing today, ending on Friday).
    """
    if cadence.startswith("weekly:"):
        weekday_name = cadence.split(":", 1)[1].strip().lower()
        target = WEEKDAYS.get(weekday_name)
        if target is None:
            return (None, None)
        days_ahead = (target - today.weekday()) % 7
        window_end = today + timedelta(days=days_ahead)
        window_start = window_end - timedelta(days=6)
        return (window_start, window_end)
    if cadence == "daily":
        return (today, today)
    if cadence.startswith("yearly:"):
        # Mirrors the weekly shape one order of magnitude up: the window is the
        # year ending on the next occurrence, so `today == cad_end` is the due day
        # and a completion recorded any time in the prior year reads as `done`.
        window_end = next_due_date(cadence, today)
        month_day = _parse_month_day(cadence.split(":", 1)[1])
        if window_end is None or month_day is None:
            return (None, None)
        # The prior occurrence comes from the configured MM-DD, not from
        # window_end: a clamped yearly:02-29 ends on Feb 28, and stepping back
        # from that would count a leap-day completion as this year's.
        prior = _clamped_date(window_end.year - 1, *month_day)
        if prior is None:
            return (None, None)
        return (prior + timedelta(days=1), window_end)
    return (None, None)


def count_missed_windows(cadence: str, last_completed: Optional[date], today: date) -> int:
    """How many cadence due-dates fell after `last_completed` and before today's window end?

    Purely derived from the two dates — it does not consult or change `status`. A
    non-zero value means the commitment lapsed that many times, which for
    `weekly:*` cadences is otherwise invisible: `cadence_window_for_today()`
    always returns a window whose end is >= today, so the `overdue` branch in
    `compute_status()` is dead code. Reporting the count restores
    visibility without inventing statuses or pre-judging the cadence question.

    Returns 0 when there is no completion on record (nothing to measure a lapse
    against) or when the cadence DSL has no computable due-date.
    """
    if last_completed is None:
        return 0
    _, cad_end = cadence_window_for_today(cadence, today)
    if cad_end is None:
        return 0
    missed = 0
    # The completion satisfied the window ending on the first due-date on or
    # after it, early or not (compute_status reads that window as `done`). Count
    # the due-dates after that one, up to but excluding the current window's end
    # (today's window is still live, not yet missed).
    satisfied = next_due_date(cadence, last_completed)
    if satisfied is None:
        return 0
    cursor = next_due_date(cadence, satisfied + timedelta(days=1))
    while cursor is not None and cursor < cad_end:
        missed += 1
        nxt = next_due_date(cadence, cursor + timedelta(days=1))
        if nxt is None or nxt <= cursor:
            break
        cursor = nxt
    return missed


# ----- owner ---------------------------------------------------------------------
# A recurring task is not always the vault owner's to do: a weekly project update
# may be written by a teammate. Entities record that as `observed_author` /
# `last_completed_by`; without reading them, every `due` row became the vault
# owner's 🔴 however clearly the entity said someone else does it.


@dataclass(frozen=True)
class VaultUser:
    """The vault owner: their `user.name` (the brain files' USER_NAME),
    and every form of their identity, casefolded."""

    name: str = ""
    forms: frozenset = frozenset()

    @property
    def label(self) -> str:
        return self.name or "the vault owner"


UNKNOWN_USER = VaultUser()


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def vault_user(vault: Path) -> VaultUser:
    """The vault owner from `<vault>/scout-config.yaml`: `user.name`, its first word
    alone, `user.email`, and the `github_username` / `user_slack_id` connector inputs.

    The first word matches only a bare first name, never another person's full
    name that happens to share it. An unreadable config gives no forms, and then
    every row stays the vault owner's (main warns).
    """
    try:
        cfg = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return UNKNOWN_USER
    if not isinstance(cfg, dict):
        return UNKNOWN_USER
    user = _mapping(cfg.get("user"))
    inputs = _mapping(_mapping(cfg.get("connectors")).get("inputs"))
    name = str(user.get("name") or "").strip()
    candidates = [
        name,
        name.split()[0] if name else "",
        user.get("email"),
        inputs.get("github_username"),
        inputs.get("user_slack_id"),
    ]
    forms = frozenset(str(c).strip().casefold() for c in candidates if c and str(c).strip())
    return VaultUser(name, forms)


def resolve_owner(entity: dict, user: VaultUser) -> tuple[str, str, bool]:
    """(owner, the field it came from, whether it is the vault owner) for an entity.

    `observed_author` wins over `last_completed_by`: the former is a run's standing
    judgement from the whole authorship history, the latter only the most recent
    completion, which one guest update can flip. A value can carry an inline
    `# …` note after the name (YAML drops it only when unquoted), so only the
    text before the first `#` counts. With neither field, or without the vault
    owner's identity to compare against, the task stays the vault owner's.
    """
    for key in ("observed_author", "last_completed_by"):
        raw = entity.get(key)
        if raw is None:
            continue
        name = str(raw).split("#", 1)[0].strip().strip("\"'").strip()
        if not name:
            continue
        return name, key, not user.forms or name.casefold() in user.forms
    return "", "", True


def compute_status(
    entity: dict,
    today: date,
    last_completed_override: Optional[str] = None,
    user: VaultUser = UNKNOWN_USER,
) -> TaskStatus:
    name = entity.get("name", "?")
    cadence = entity.get("cadence", "")
    surface_window = entity.get("surface_window", "T-0 morning")
    # A live caller (briefing/consolidation with Linear MCP / Slack search) can pass
    # the freshly-looked-up completion date via --last-completed <slug>=<date>; it
    # takes precedence over the entity's stored last_completed_date and is NOT
    # written back to the file (Stage 4b — live evidence stays caller-side).
    overridden = False
    if last_completed_override is not None:
        last_completed_raw = last_completed_override
        overridden = True
    else:
        last_completed_raw = entity.get("last_completed_date")
    last_completed = None
    if last_completed_raw:
        try:
            last_completed = date.fromisoformat(str(last_completed_raw))
        except ValueError:
            last_completed = None
            if overridden:
                print(
                    f"warning: --last-completed value '{last_completed_raw}' for "
                    f"'{name}' is not a valid YYYY-MM-DD date; ignoring",
                    file=sys.stderr,
                )

    nd = next_due_date(cadence, today)
    win_start_days, win_end_days = parse_surface_window(surface_window)

    status = "upcoming"
    reason = ""

    if nd is None:
        status = "unknown"
        reason = f"cadence DSL '{cadence}' not yet supported by v1"
    else:
        cad_start, cad_end = cadence_window_for_today(cadence, today)
        within_cadence = cad_start and cad_end and cad_start <= today <= cad_end

        if last_completed and cad_start and last_completed >= cad_start:
            status = "done"
            reason = f"last_completed {last_completed} >= cadence window start {cad_start}"
        elif within_cadence and today == cad_end:
            status = "due"
            reason = f"today is the due day ({cadence})"
        elif within_cadence:
            surface_start = nd - timedelta(days=win_start_days)
            surface_end = nd - timedelta(days=win_end_days)
            if surface_start <= today <= surface_end:
                status = "surfacing"
                reason = f"in surface_window {surface_window} for next due {nd}"
            else:
                status = "upcoming"
                reason = f"in cadence window but before surface_window"
        elif cad_end and today > cad_end:
            status = "overdue"
            reason = f"cadence window ended {cad_end} with no completion logged"

    if overridden and last_completed is not None:
        reason = (reason + " " if reason else "") + "[last_completed via --last-completed override]"

    feeds = []
    for rel in entity.get("relationships", []) or []:
        if isinstance(rel, dict) and rel.get("type") == "feeds":
            feeds.append(rel.get("target", ""))

    missed = count_missed_windows(cadence, last_completed, today)
    if missed:
        reason = (reason + " " if reason else "") + f"[{missed} cadence window(s) missed since {last_completed}]"

    owner, owner_source, owner_is_user = resolve_owner(entity, user)
    # `status` stays as computed: a lapsed cadence is still lapsed whoever owns it,
    # and hiding the row would hide that. What the owner changes is the tier the
    # caller gives it: someone else's next step is 🟢 Watching, not the user's 🔴.
    if not owner_is_user:
        reason = (reason + " " if reason else "") + (
            f"[not {user.label}'s — {owner_source}: {owner}; a 🟢 Watching item waiting on {owner}, "
            f"never a 🔴 for {user.label}]"
        )

    return TaskStatus(
        name=name,
        cadence=cadence,
        surface_window=surface_window,
        status=status,
        next_due=nd.isoformat() if nd else None,
        last_completed=last_completed.isoformat() if last_completed else None,
        windows_missed=missed,
        domain=entity.get("domain", ""),
        priority=entity.get("priority", ""),
        reason=reason,
        feeds=feeds,
        owner=owner,
        owner_source=owner_source,
        owner_is_user=owner_is_user,
    )


def main() -> int:
    doc = __doc__ or "recurring-task-status"
    parser = argparse.ArgumentParser(description=doc.split("\n")[0])
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parent.parent / "knowledge-base" / "recurring-tasks"),
        help="Directory containing recurring_task entity files",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument("--date", help="Reference date YYYY-MM-DD (defaults to today)")
    parser.add_argument(
        "--status",
        action="append",
        help="Filter to one or more statuses (repeatable)",
    )
    parser.add_argument(
        "--last-completed",
        action="append",
        metavar="SLUG=YYYY-MM-DD",
        help=(
            "Override an entity's last_completed_date with a live-looked-up value, "
            "keyed by file stem (slug). Repeatable. The runner fetches this from "
            "Linear (`get_project` -> lastUpdateAt) or Slack search, then passes it "
            "in so the local date math reflects live evidence without writing to the "
            "entity file. Example: --last-completed weekly-status-update=2026-05-29"
        ),
    )
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()

    overrides: dict[str, str] = {}
    for item in args.last_completed or []:
        if "=" not in item:
            print(
                f"error: --last-completed expects SLUG=YYYY-MM-DD, got '{item}'",
                file=sys.stderr,
            )
            return 2
        slug, value = item.split("=", 1)
        overrides[slug.strip()] = value.strip()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    seen_slugs: set[str] = set()
    rows: list[TaskStatus] = []
    vault = vault_of(root)
    paths = [] if reader_sees_nothing() else sorted(root.glob("*.md"))
    labels = None
    if paths:
        try:
            scope = root.resolve().relative_to(vault.resolve()).as_posix()
        except ValueError:
            scope = None
        labels = visibility_map(vault, [f"{scope}/**"]) if scope else None
        if labels is None:
            warn_unlabelled("recurring-task-status")
    user = vault_user(vault)
    for path in paths:
        entity = parse_frontmatter(path)
        if entity.get("type") != "recurring_task":
            continue
        if entity.get("status") and str(entity["status"]).lower() not in ("open", "active"):
            continue
        seen_slugs.add(path.stem)
        row = compute_status(entity, today, overrides.get(path.stem), user)
        try:
            rel = path.resolve().relative_to(vault.resolve()).as_posix()
        except ValueError:
            rel = ""
        row.visibility = row_visibility(labels, rel)
        rows.append(row)

    if not user.forms and any(r.owner for r in rows):
        print(
            f"recurring-task-status: can't read the vault owner's name or email from {vault / 'scout-config.yaml'}; "
            "every row is treated as theirs, whoever its owner field names.",
            file=sys.stderr,
        )

    for slug in overrides:
        if slug not in seen_slugs:
            print(
                f"warning: --last-completed slug '{slug}' matched no open recurring_task "
                f"entity file in {root}",
                file=sys.stderr,
            )

    if args.status:
        wanted = {s.lower() for s in args.status}
        rows = [r for r in rows if r.status in wanted]

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return 0

    if not rows:
        print(f"# recurring-task-status — {today.isoformat()}\n\nNo entities matched.\n")
        return 0

    print(f"# recurring-task-status — {today.isoformat()}\n")
    print("| Name | Status | Owner | Missed | Cadence | Next due | Surface window | Last completed | Reason |")
    print("|------|--------|-------|--------|---------|----------|----------------|----------------|--------|")
    for r in rows:
        missed = f"**{r.windows_missed}**" if r.windows_missed else "0"
        owner = r.owner or "—"
        if not r.owner_is_user:
            owner = f"**{r.owner}** (not {user.label})"
        print(
            f"| {private_mark(r.visibility)}{r.name} | **{r.status}** | {owner} | {missed} | {r.cadence} | "
            f"{r.next_due or '—'} | {r.surface_window} | {r.last_completed or '—'} | {r.reason} |"
        )

    others = [r for r in rows if not r.owner_is_user and r.status in ("due", "overdue")]
    if others:
        print(
            f"\n⚠️  {len(others)} `due`/`overdue` row(s) above are not {user.label}'s to do. Each is a 🟢 Watching "
            f"item waiting on its owner — never a 🔴 or 🟡 action item for {user.label}:"
        )
        for r in others:
            print(f"  - `{private_mark(r.visibility)}{r.name}` → {r.owner} ({r.owner_source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

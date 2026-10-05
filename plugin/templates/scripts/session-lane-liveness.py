#!/usr/bin/env python3
"""session-lane-liveness.py — does each scheduled session type still produce work?

Connector health watches tool calls inside a run, and the run-outcome ledger
watches one run's exit. Neither notices a whole session type (a *lane*: every
slot of one type) quietly producing nothing for weeks, which looks exactly like
a string of individually unremarkable quiet runs. This check does.

A lane is judged by committed output, not by .scout-state/last-fire.json:
last-fire advances when a run is dispatched, even one that dies at the door on
an expired login, so it cannot tell "ran and did work" from "never got started".
A commit is a lane's output when
  - its subject starts with the lane's name ("dreaming [22:00]: ..."), the
    convention the dreaming and research phases commit with, or
  - it landed during a run of that lane recorded in .scout-logs/run-outcomes.jsonl
    (written by scripts/run-outcome.sh), which covers lanes whose commits carry
    no such prefix.

The lanes are the slot types declared in .scout-state/schedule.yaml (``manual``
excluded). Each has a (warn, dark) budget in days without output, widened when
the lane's schedule leaves longer gaps than that: a weekly lane is not dark
after three days. A lane with no output at all is only judged once its runs
have been recorded for its whole dark budget; before that it reports NO DATA YET
rather than raising an alarm on the first day after install.

Usage:
  session-lane-liveness.py            # table + verdict
  session-lane-liveness.py --json     # machine-readable
  session-lane-liveness.py --quiet    # print only when a lane is dark
  session-lane-liveness.py --notify   # desktop notification (macOS) when a lane is dark

Exit codes: 0 = no lane dark · 2 = at least one lane DARK · 3 = a lane FIRING-BUT-SILENT

The vault is $SCOUT_DATA_DIR, or else the directory this script is installed in
(<vault>/scripts/). Read-only. Standard library only, so it runs under any
python3, including when the engine itself is what broke.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# (warn, dark) days without output. Deliberately generous: these mean "the lane
# is broken", not "a quiet night".
LANE_BUDGETS_DAYS = {
    "briefing": (2, 4),
    "consolidation": (2, 4),
    "dreaming": (3, 7),
    "research": (3, 7),
}
DEFAULT_BUDGET = (3, 7)
NOT_LANES = {"manual"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RECENT_FIRE_DAYS = 1.5  # fired this recently but nothing committed: firing but silent
COMMIT_SCAN = 4000

AUTH_RE = re.compile(
    r"OAuth session expired|OAuth token (has )?expired|Failed to authenticate"
    r"|Invalid authentication credentials|API Error: 40[13]",
    re.IGNORECASE,
)

OK, DEGRADED, NO_DATA, SILENT, DARK = "🟢 OK", "🟡 DEGRADED", "⚪ NO DATA YET", "🔴 FIRING-BUT-SILENT", "🔴 DARK"


def vault_dir() -> Path:
    env = os.environ.get("SCOUT_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


# ---------- inputs ----------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def read_schedule(path: Path) -> dict[str, dict]:
    """{slot: {"type", "runner", "weekdays"}} from schedule.yaml, without a yaml dependency.

    Reads the ``slots:`` mapping at whatever indent it uses, and both list styles
    for ``weekdays`` (``[Mon, Tue]`` and one ``- Mon`` per line).
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    slots: dict[str, dict] = {}
    in_slots = False
    slot_indent: int | None = None
    slot: str | None = None
    in_weekdays = False
    for raw in lines:
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 0:
            in_slots, slot, slot_indent, in_weekdays = text == "slots:", None, None, False
            continue
        if not in_slots:
            continue
        if slot_indent is None:
            slot_indent = indent
        if indent <= slot_indent:
            slot, in_weekdays = None, False
            if indent == slot_indent and text.endswith(":"):
                slot = _unquote(text[:-1])
                slots[slot] = {"weekdays": []}
            continue
        if slot is None:
            continue
        if text.startswith("- "):
            if in_weekdays:
                slots[slot]["weekdays"].append(_unquote(text[2:]))
            continue
        key, sep, value = text.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        in_weekdays = key == "weekdays" and not value
        if key == "weekdays" and value.startswith("["):
            slots[slot]["weekdays"] = [_unquote(v) for v in value.strip("[]").split(",") if v.strip()]
        elif key in ("type", "runner"):
            slots[slot][key] = _unquote(value)
    return {key: s for key, s in slots.items() if s.get("type")}


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_fires(vault: Path) -> dict[str, datetime]:
    try:
        raw = json.loads((vault / ".scout-state" / "last-fire.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, datetime] = {}
    index = raw.get("last_fire") if isinstance(raw, dict) else None
    for slot, ts in (index or {}).items():
        dt = _parse_iso(ts) if isinstance(ts, str) else None
        if dt:
            out[slot] = dt
    return out


def run_windows(vault: Path) -> list[tuple[str, datetime, datetime, str | None]]:
    """(slot, started, finished, log file name) for every run in the run-outcome ledger."""
    out: list[tuple[str, datetime, datetime, str | None]] = []
    try:
        lines = (vault / ".scout-logs" / "run-outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            started = datetime.fromtimestamp(int(row["started_at"]), timezone.utc)
            finished = datetime.fromtimestamp(int(row["finished_at"]), timezone.utc)
            slot = str(row.get("slot") or row["mode"])
        except (ValueError, KeyError, TypeError):
            continue
        log = row.get("log")
        out.append((slot, started, finished, log if isinstance(log, str) and log else None))
    return out


def recent_commits(vault: Path) -> list[tuple[datetime, str]]:
    try:
        raw = subprocess.run(
            ["git", "-C", str(vault), "log", "-n", str(COMMIT_SCAN), "--format=%ct%x1f%s"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[tuple[datetime, str]] = []
    for line in raw.splitlines():
        epoch, sep, subject = line.partition("\x1f")
        if sep and epoch.isdigit():
            out.append((datetime.fromtimestamp(int(epoch), timezone.utc), subject))
    return out


def newest_log_outcome(vault: Path, runner: str | None, ledger_logs: list[str], *, shared: bool) -> str | None:
    """Why the lane's newest run produced nothing, read from its runner log.

    The logs the run ledger recorded for this lane are always the lane's own. A
    runner's other logs (named <stem>-YYYY-MM-DD_HH-MM.log, including runs that
    stopped before the ledger step, such as a budget skip) count only when no
    other lane uses the same runner: run-scout.sh serves both briefing and
    consolidation, and its log names do not say which one ran.
    """
    log_dir = vault / ".scout-logs"
    names = {name for name in ledger_logs if (log_dir / name).is_file()}
    m = re.fullmatch(r"run-(.+)\.sh", runner or "")
    if m and not shared:
        pattern = re.compile(re.escape(m.group(1)) + r"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.log")
        names |= {p.name for p in log_dir.glob("*.log") if pattern.fullmatch(p.name)}
    logs = [log_dir / name for name in sorted(names)]
    if not logs:
        return None
    text = logs[-1].read_text(encoding="utf-8", errors="replace")
    name = logs[-1].name
    if "Budget check: skipping" in text:
        return f"{name}: budget-skip"
    if AUTH_RE.search(text):
        return f"{name}: AUTH FAILURE"
    if re.search(r"Another .* session running", text):
        return f"{name}: lock-skip"
    code = re.search(r"exit code: (\d+)", text)
    if code:
        return f"{name}: exit {code.group(1)}"
    return f"{name}: no completion line"


# ---------- judgement ----------


def longest_gap_days(weekdays: list[str]) -> int:
    """Longest run of days between the lane's scheduled weekdays (1 for daily)."""
    days = sorted({WEEKDAYS.index(d[:3].lower()) for d in weekdays if d[:3].lower() in WEEKDAYS})
    if not days:
        return 1
    if len(days) == 1:
        return 7
    return max([b - a for a, b in zip(days, days[1:])] + [days[0] + 7 - days[-1]])


def lane_budgets(lane: str, weekdays: list[str]) -> tuple[int, int]:
    """(warn, dark) days, widened so a lane's own schedule gaps never read as an outage."""
    warn, dark = LANE_BUDGETS_DAYS.get(lane, DEFAULT_BUDGET)
    gap = longest_gap_days(weekdays)
    return max(warn, gap), max(dark, gap + 1)


def _days(now: datetime, then: datetime | None) -> float | None:
    return (now - then).total_seconds() / 86400 if then else None


def assess(vault: Path, now: datetime | None = None) -> tuple[list[dict], int]:
    now = now or datetime.now(timezone.utc)
    schedule = read_schedule(vault / ".scout-state" / "schedule.yaml")
    lanes: dict[str, list[str]] = {}
    for slot, spec in schedule.items():
        if spec["type"] not in NOT_LANES:
            lanes.setdefault(spec["type"], []).append(slot)

    def lane_of_slot(slot: str) -> str | None:
        if slot in schedule:
            return schedule[slot]["type"] if schedule[slot]["type"] in lanes else None
        prefix = slot.split("-", 1)[0]  # an unscheduled run such as "dreaming-manual"
        return prefix if prefix in lanes else None

    windows: dict[str, list[tuple[datetime, datetime]]] = {lane: [] for lane in lanes}
    ledger_logs: dict[str, list[str]] = {lane: [] for lane in lanes}
    for slot, started, finished, log in run_windows(vault):
        lane = lane_of_slot(slot)
        if lane:
            windows[lane].append((started, finished))
            if log:
                ledger_logs[lane].append(log)

    output: dict[str, tuple[datetime, str]] = {}
    for when, subject in recent_commits(vault):
        words = subject.split()
        named = words[0].rstrip(":").lower() if words else ""
        # A subject that names a lane is authoritative; only an unnamed commit is
        # credited to whichever lane's run it landed in.
        if named in lanes:
            owners = {named}
        else:
            owners = {lane for lane, spans in windows.items() if any(s <= when <= f for s, f in spans)}
        for lane in owners:
            if lane not in output or when > output[lane][0]:
                output[lane] = (when, subject)

    fires = last_fires(vault)
    report: list[dict] = []
    worst = 0
    for lane in sorted(lanes):
        slots = lanes[lane]
        weekdays = [d for slot in slots for d in schedule[slot]["weekdays"]]
        warn, dark = lane_budgets(lane, weekdays)
        commit_dt, subject = output.get(lane, (None, ""))
        fire_dt = max((fires[s] for s in slots if s in fires), default=None)
        commit_age, fire_age = _days(now, commit_dt), _days(now, fire_dt)
        observed = _days(now, min((s for s, _ in windows[lane]), default=None))

        code = 0
        if commit_age is not None and commit_age < warn:
            status = OK
        elif commit_age is not None and commit_age < dark:
            status = DEGRADED
        elif commit_age is None and (observed is None or observed < dark):
            status = NO_DATA
        elif fire_age is not None and fire_age < RECENT_FIRE_DAYS:
            status, code = SILENT, 3
        else:
            status, code = DARK, 2
        worst = max(worst, code)

        entry = {
            "lane": lane,
            "status": status,
            "slots": slots,
            "last_commit": commit_dt.isoformat() if commit_dt else None,
            "last_commit_age_days": round(commit_age, 2) if commit_age is not None else None,
            "last_commit_subject": subject[:110],
            "last_fire": fire_dt.isoformat() if fire_dt else None,
            "last_fire_age_days": round(fire_age, 2) if fire_age is not None else None,
            "runs_recorded": len(windows[lane]),
            "budget_warn_days": warn,
            "budget_dark_days": dark,
        }
        if code:
            runner = schedule[slots[0]].get("runner")
            shared = any(schedule[s].get("runner") == runner for other in lanes if other != lane for s in lanes[other])
            entry["newest_log"] = newest_log_outcome(vault, runner, ledger_logs[lane], shared=shared)
        report.append(entry)
    return report, worst


# ---------- output ----------


def render(report: list[dict], now: datetime) -> str:
    def age(days: float | None) -> str:
        return f"{days}d" if days is not None else "never"

    lines = [
        f"# Session-lane liveness — {now.astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        f"{'LANE':16}{'STATUS':24}{'LAST OUTPUT':14}{'LAST FIRE':12}BUDGET",
    ]
    for e in report:
        lines.append(
            f"{e['lane']:16}{e['status']:24}{age(e['last_commit_age_days']):14}{age(e['last_fire_age_days']):12}"
            f"warn {e['budget_warn_days']}d / dark {e['budget_dark_days']}d"
        )
    bad = [e for e in report if e["status"] in (SILENT, DARK)]
    if bad:
        lines += ["", "## Lanes needing attention", ""]
        for e in bad:
            lines.append(
                f"- **{e['lane']}** — {e['status']}. Last output {age(e['last_commit_age_days'])} ago; "
                f"last fire {age(e['last_fire_age_days'])} ago."
            )
            if e.get("newest_log"):
                lines.append(f"  Newest runner log → `{e['newest_log']}`")
            if e["status"] == SILENT:
                lines.append(
                    "  The scheduler is firing this lane and it commits nothing. Check the runner "
                    "log above: an expired login, the budget check, or a held lock are the usual causes."
                )
    return "\n".join(lines) + "\n"


def notify(lanes: list[str]) -> None:
    """Best-effort desktop notification where osascript exists (macOS).

    The lane names come from the vault's schedule, so they go to AppleScript as
    argv, never spliced into its source.
    """
    osascript = shutil.which("osascript")
    if not osascript or not lanes:
        return
    script = (
        'on run argv\n    display notification (item 2 of argv) with title (item 1 of argv) sound name "Basso"\nend run'
    )
    message = f"Scout lane(s) not producing output: {', '.join(lanes)}"
    try:
        subprocess.run(
            [osascript, "-", "Scout lane liveness", message], input=script, text=True, capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="is each scheduled session lane still producing commits?")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--notify", action="store_true", help="desktop notification when a lane is dark")
    ap.add_argument("--quiet", action="store_true", help="print only when a lane is dark")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    report, worst = assess(vault_dir(), now)
    if args.json:
        print(json.dumps({"generated_at": now.isoformat(), "lanes": report}, indent=2, ensure_ascii=False))
    elif worst or not args.quiet:
        print(render(report, now), end="")
    if args.notify and worst:
        notify([e["lane"] for e in report if e["status"] in (SILENT, DARK)])
    return worst


if __name__ == "__main__":
    sys.exit(main())

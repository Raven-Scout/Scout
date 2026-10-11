"""Run health: one read-only evaluator for "is Scout actually running?" (design D1).

Every surface (``scoutctl health``, the doctor block, the app, push
notifications) asks this module instead of re-deriving the verdict. It reads the
on-disk run ledger (``.scout-logs/run-outcomes.jsonl``, written by
``run-outcome.sh``) and, when there is no ledger at all, the newest run log. It
never writes anything.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout.errors import ConfigError
from scout.schedule import SlotRuntime, SlotType, load_schedule

SEVERITY_ORDER = {"note": 0, "yellow": 1, "red": 2}
SKIP_OUTCOMES = frozenset({"skipped_lock", "skipped_budget", "skipped_redundant"})

LEDGER_NAME = "run-outcomes.jsonl"
LOCK_SKIP_THRESHOLD = 3
LOCK_SKIP_WINDOW = timedelta(hours=24)

# Runner log filename prefixes written under <vault>/.scout-logs/ by the three
# cat-1b runners (run-scout.sh / run-dreaming.sh / run-research.sh).
_RUN_LOG_GLOBS = ("scout-*.log", "dreaming-*.log", "research-*.log")

# The Claude CLI's own auth-error phrasing, anchored at the start of a line, so a
# session that merely *writes about* a 401 or quotes one of these errors can't
# trip a detector. One definition shared verbatim, as a POSIX ERE, by
# templates/scripts/claude-with-retry.sh.tmpl (AUTH_PATTERNS) and
# templates/scripts/run-outcome.sh.tmpl (AUTH_RE); a test keeps the three equal.
# Written so Python's re (with MULTILINE) reads it the same way grep -E does.
CLI_AUTH_FAILURE_ERE = (
    "^(Failed to authenticate([.:]|$)"
    "|Invalid authentication credentials"
    "|API Error: 40[13]"
    "|OAuth (session|token)( has)? expired)"
)

# Rejected-Claude-credential signatures: the retry wrapper's own banner, or the
# CLI's raw error (pre-0.8.0 runners never wrote the banner).
_AUTH_FAILURE_RE = re.compile(r"=== Authentication failure \(HTTP 401/403\)|" + CLI_AUTH_FAILURE_ERE, re.MULTILINE)

_AUTH_FIX = "Open Terminal and run: claude auth login (or claude setup-token for unattended hosts)"


def iso_z(dt: datetime) -> str:
    """Format ``dt`` as ``2026-10-11T08:00:00Z`` (UTC, seconds precision)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_or_none(dt: datetime | None) -> str | None:
    return iso_z(dt) if dt is not None else None


@dataclass(frozen=True)
class OutcomeRow:
    slot: str
    mode: str
    started_at: datetime
    finished_at: datetime
    exit_code: int
    failure_class: str
    outcome: str  # normalised: ok | failed | a SKIP_OUTCOMES member
    log: str | None
    commits_in_window: int | None
    newest_commit: str | None


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str  # "yellow" | "red" | "note"
    since: datetime | None
    summary: str
    fix: str
    source: str  # "ledger" | "log" | "schedule" | "events" | "git"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "since": _iso_or_none(self.since),
            "summary": self.summary,
            "fix": self.fix,
            "source": self.source,
        }


@dataclass(frozen=True)
class SlotStatus:
    key: str
    type: str
    last_fire: datetime | None
    last_outcome: OutcomeRow | None

    def to_dict(self) -> dict:
        last = self.last_outcome
        return {
            "key": self.key,
            "type": self.type,
            "last_fire": _iso_or_none(self.last_fire),
            "last_outcome": None
            if last is None
            else {
                "outcome": last.outcome,
                "finished_at": iso_z(last.finished_at),
                "failure_class": last.failure_class,
            },
        }


@dataclass(frozen=True)
class RunHealth:
    severity: str  # "green" | "yellow" | "red"
    findings: tuple[Finding, ...]
    evaluated_at: datetime
    last_ok_at: datetime | None
    slots: tuple[SlotStatus, ...] = ()

    @property
    def exit_code(self) -> int:
        return {"green": 0, "yellow": 1, "red": 2}[self.severity]

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "evaluated_at": iso_z(self.evaluated_at),
            "last_ok_at": _iso_or_none(self.last_ok_at),
            "findings": [f.to_dict() for f in self.findings],
            "slots": [s.to_dict() for s in self.slots],
        }


def _opt_int(value: object) -> int | None:
    try:
        return None if value is None else int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _parse_row(obj: object) -> OutcomeRow | None:
    if not isinstance(obj, dict):
        return None
    slot = obj.get("slot") or obj.get("mode")
    if not slot:
        return None
    try:
        started = datetime.fromtimestamp(int(obj["started_at"]), UTC)
        finished = datetime.fromtimestamp(int(obj["finished_at"]), UTC)
        exit_code = int(obj["exit_code"])
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    outcome = obj.get("outcome")
    if not (outcome in ("ok", "failed") or outcome in SKIP_OUTCOMES):
        outcome = "ok" if exit_code == 0 else "failed"
    log = obj.get("log")
    commit = obj.get("newest_commit")
    return OutcomeRow(
        slot=str(slot),
        mode=str(obj.get("mode") or slot),
        started_at=started,
        finished_at=finished,
        exit_code=exit_code,
        failure_class=str(obj.get("failure_class") or ""),
        outcome=outcome,
        log=str(log) if log else None,
        commits_in_window=_opt_int(obj.get("commits_in_window")),
        newest_commit=str(commit) if commit else None,
    )


def read_outcomes(vault: Path) -> list[OutcomeRow]:
    """Read the run ledger in file order, skipping malformed rows."""
    path = vault / ".scout-logs" / LEDGER_NAME
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[OutcomeRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        row = _parse_row(obj)
        if row is not None:
            rows.append(row)
    return rows


def _tail_text(path: Path, *, max_bytes: int = 65536) -> str:
    """Return the trailing ``max_bytes`` of ``path`` decoded as UTF-8 ("" on error)."""
    try:
        with path.open("rb") as f:
            size = path.stat().st_size
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _log_auth_finding(vault: Path) -> Finding | None:
    """Fallback for vaults without a ledger: did the newest run log end on a 401/403?"""
    logs_dir = vault / ".scout-logs"
    if not logs_dir.is_dir():
        return None
    candidates: list[Path] = []
    for pat in _RUN_LOG_GLOBS:
        candidates.extend(logs_dir.glob(pat))
    if not candidates:
        return None
    try:
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not _AUTH_FAILURE_RE.search(_tail_text(latest)):
        return None
    return Finding(
        id="auth.expired",
        severity="red",
        since=None,
        summary=(
            "Scout can't run: your Claude login expired. "
            f"The newest run log ({latest.name}) ended on an authentication failure."
        ),
        fix=_AUTH_FIX,
        source="log",
    )


def _last_ok_at(rows: list[OutcomeRow]) -> datetime | None:
    oks = [r.finished_at for r in rows if r.outcome == "ok"]
    return max(oks) if oks else None


def ledger_findings(rows: list[OutcomeRow], *, vault: Path, now: datetime) -> list[Finding]:
    """Findings derived from the ledger (or the newest log when there is no ledger)."""
    if not rows:
        auth = _log_auth_finding(vault)
        return [auth] if auth else []

    findings: list[Finding] = []
    last_ok = _last_ok_at(rows)
    non_skip = [r for r in rows if r.outcome not in SKIP_OUTCOMES]

    streak = 0
    for r in reversed(non_skip):
        if r.outcome != "failed":
            break
        streak += 1

    if streak:
        newest = non_skip[-1]
        if newest.failure_class == "oauth_expired":
            when = iso_z(last_ok) if last_ok else "never"
            findings.append(
                Finding(
                    id="auth.expired",
                    severity="red",
                    since=last_ok,
                    summary=(
                        "Scout can't run: your Claude login expired. "
                        f"{streak} failed run(s) since the last success ({when})."
                    ),
                    fix=_AUTH_FIX,
                    source="ledger",
                )
            )
        else:
            log_hint = f": .scout-logs/{newest.log}" if newest.log else ""
            findings.append(
                Finding(
                    id="runs.failing",
                    severity="red" if streak >= 2 else "yellow",
                    since=last_ok,
                    summary=f"The last {streak} scheduled run(s) failed ({newest.failure_class}).",
                    fix=f"Run scoutctl bootstrap doctor and read the newest log{log_hint}",
                    source="ledger",
                )
            )

    cutoff = now - LOCK_SKIP_WINDOW
    lock_skips = [r for r in rows if r.outcome == "skipped_lock" and r.finished_at >= cutoff]
    if len(lock_skips) >= LOCK_SKIP_THRESHOLD:
        findings.append(
            Finding(
                id="lock.skips",
                severity="yellow",
                since=None,
                summary=f"{len(lock_skips)} runs skipped in the last 24 h because another session held the vault lock.",
                fix="See #218; a long-running session is blocking scheduled ones.",
                source="ledger",
            )
        )
    return findings


def build_report(
    findings: list[Finding],
    *,
    now: datetime,
    last_ok_at: datetime | None,
    slots: tuple[SlotStatus, ...] = (),
) -> RunHealth:
    """Sort findings (red, yellow, note; then id) and derive the overall severity."""
    ordered = tuple(sorted(findings, key=lambda f: (-SEVERITY_ORDER[f.severity], f.id)))
    worst = max((SEVERITY_ORDER[f.severity] for f in ordered), default=0)
    severity = "red" if worst == 2 else "yellow" if worst == 1 else "green"
    return RunHealth(severity=severity, findings=ordered, evaluated_at=now, last_ok_at=last_ok_at, slots=slots)


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TICK_STALL = timedelta(hours=2)
FIRE_MATCH_SLACK = timedelta(seconds=120)
_STALE_FIX = "Check that the Mac was awake and the schedule-tick job is loaded: scoutctl bootstrap doctor"
_SILENT_FIX = "The run may have been skipped (lock or budget) or killed; see .scout-logs/failures.log"
_STALLED_FIX = (
    "Reinstall the scheduler job: scoutctl schedule install-plist --force (macOS) "
    "or scoutctl schedule install-cron (Linux)"
)


def longest_gap_hours(weekdays: tuple[str, ...]) -> int:
    """24 x the longest run of days between scheduled weekdays (daily 24, one day 168)."""
    days = sorted({_WEEKDAYS.index(d[:3].lower()) for d in weekdays if d[:3].lower() in _WEEKDAYS})
    if not days:
        return 24
    if len(days) == 1:
        return 168
    return 24 * max([b - a for a, b in zip(days, days[1:], strict=False)] + [days[0] + 7 - days[-1]])


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def read_last_fires(vault: Path) -> dict[str, datetime]:
    """``.scout-state/last-fire.json`` -> {slot key: last fire time}; {} on any error."""
    try:
        data = json.loads((vault / ".scout-state" / "last-fire.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = data.get("last_fire") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    fires: dict[str, datetime] = {}
    for key, value in raw.items():
        ts = _parse_ts(value)
        if ts is not None:
            fires[str(key)] = ts
    return fires


def last_tick_completed(vault: Path) -> datetime | None:
    """Newest ``schedule.tick.completed`` event time in the two newest event logs."""
    files = sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl"), key=lambda p: p.name)[-2:]
    newest: datetime | None = None
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable log
            continue
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("kind") != "schedule.tick.completed":
                continue
            ts = _parse_ts(obj.get("ts"))
            if ts is not None and (newest is None or ts > newest):
                newest = ts
    return newest


def vault_birth(vault: Path) -> datetime | None:
    """Root-commit time when the vault itself is a git repo, else scout-config.yaml mtime, else None."""
    try:
        top = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == vault.resolve():
            log = subprocess.run(
                ["git", "-C", str(vault), "log", "--max-parents=0", "--format=%ct", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            stamps = [int(x) for x in log.stdout.split() if x.isdigit()] if log.returncode == 0 else []
            if stamps:
                return datetime.fromtimestamp(min(stamps), UTC)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return datetime.fromtimestamp((vault / "scout-config.yaml").stat().st_mtime, UTC)
    except OSError:
        return None


def _age(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    return f"{hours // 24} d" if hours >= 72 else f"{hours} h"


def schedule_findings(
    vault: Path, rows: list[OutcomeRow], *, now: datetime
) -> tuple[list[Finding], tuple[SlotStatus, ...]]:
    """Slot, fire and scheduler-liveness findings, plus per-slot status."""
    findings: list[Finding] = []
    statuses: list[SlotStatus] = []
    birth = vault_birth(vault)

    tick = last_tick_completed(vault)
    if tick is not None and now - tick > TICK_STALL:
        findings.append(
            Finding(
                id="scheduler.stalled",
                severity="red",
                since=tick,
                summary=f"The scheduler has not completed a tick since {iso_z(tick)}.",
                fix=_STALLED_FIX,
                source="events",
            )
        )
    elif tick is None and birth is not None and now - birth > TICK_STALL:
        findings.append(
            Finding(
                id="scheduler.stalled",
                severity="red",
                since=None,
                summary="The scheduler has never recorded a tick.",
                fix=_STALLED_FIX,
                source="events",
            )
        )

    schedule_path = vault / ".scout-state" / "schedule.yaml"
    try:
        schedule = load_schedule(schedule_path)
        schedule_mtime = datetime.fromtimestamp(schedule_path.stat().st_mtime, UTC)
    except (ConfigError, OSError):
        return findings, ()

    fires = read_last_fires(vault)
    first_row = min((r.started_at for r in rows), default=None)
    ref = max(birth, schedule_mtime) if birth is not None else None
    slot_findings: list[Finding] = []
    stale: list[Finding] = []
    judged = 0
    for slot in schedule.values():
        if slot.type is SlotType.MANUAL or slot.runtime is not SlotRuntime.LOCAL:
            continue
        judged += 1
        key = slot.key
        gap = longest_gap_hours(slot.weekdays)
        mw = slot.missed_window_hours
        fire = fires.get(key)
        mine = [r for r in rows if r.slot == key]
        statuses.append(
            SlotStatus(key=key, type=slot.type.value, last_fire=fire, last_outcome=mine[-1] if mine else None)
        )
        if fire is None:
            if ref is not None and now - ref > timedelta(hours=gap + mw):
                slot_findings.append(
                    Finding(
                        id=f"slot.never_fired:{key}",
                        severity="yellow",
                        since=None,
                        summary=f"{key} has never fired.",
                        fix=_STALE_FIX,
                        source="schedule",
                    )
                )
            continue
        if now - fire > timedelta(hours=2 * gap + mw):
            stale.append(
                Finding(
                    id=f"slot.stale:{key}",
                    severity="yellow",
                    since=fire,
                    summary=f"{key} last fired {_age(now - fire)} ago.",
                    fix=_STALE_FIX,
                    source="schedule",
                )
            )
        if (
            first_row is not None
            and fire > first_row
            and now - fire >= timedelta(hours=mw)
            and not any(r.started_at >= fire - FIRE_MATCH_SLACK for r in mine)
        ):
            slot_findings.append(
                Finding(
                    id=f"slot.fired_silent:{key}",
                    severity="yellow",
                    since=fire,
                    summary=f"{key} fired at {iso_z(fire)} but no run outcome was recorded.",
                    fix=_SILENT_FIX,
                    source="ledger",
                )
            )
    if stale and len(stale) == judged:
        stale = [replace(f, severity="red") for f in stale]
    return findings + slot_findings + stale, tuple(statuses)


def evaluate(vault: Path, *, now: datetime | None = None) -> RunHealth:
    now = now or datetime.now(UTC)
    rows = read_outcomes(vault)
    found = ledger_findings(rows, vault=vault, now=now)
    sched, slots = schedule_findings(vault, rows, now=now)
    return build_report(found + sched, now=now, last_ok_at=_last_ok_at(rows), slots=slots)

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
import platform
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import takewhile
from pathlib import Path

from scout.errors import ConfigError
from scout.schedule import Schedule, SlotRuntime, SlotType, load_schedule
from scout.scripts.bootstrap_lock import is_lock_held_by_live_pid
from scout.scripts.rate_limit_reset import LOG_TAIL_BYTES, reset_from_log
from scout.update_outcomes import UpdateOutcome
from scout.update_outcomes import read as read_updates

SEVERITY_ORDER = {"note": 0, "yellow": 1, "red": 2}
SKIP_OUTCOMES = frozenset({"skipped_lock", "skipped_budget", "skipped_redundant"})

LEDGER_NAME = "run-outcomes.jsonl"
LOCK_SKIP_THRESHOLD = 3
LOCK_SKIP_WINDOW = timedelta(hours=24)
# A Claude usage limit that named its reset: runs wait for it (#424).
PAUSED_FINDING_ID = "runs.paused"

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
    "|OAuth (session|token)( has)? expired"
    "|Not logged in)"
)

# Rejected-Claude-credential signatures: the retry wrapper's own banner, or the
# CLI's raw error (pre-0.8.0 runners never wrote the banner).
_AUTH_FAILURE_RE = re.compile(r"=== Authentication failure \(HTTP 401/403\)|" + CLI_AUTH_FAILURE_ERE, re.MULTILINE)

_AUTH_FIX = "Open Terminal and run: claude auth login (or scoutctl auth setup-token for unattended runs)"

# The line that names a failed run's error, per ledger failure class, best first (#445). Each is
# a narrower cut of run-outcome.sh's class pattern (a test holds every pick to it), aimed at the
# CLI's own message rather than the runner's banners, which repeat or merely name the class.
FAILURE_LINE_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    "oauth_expired": (re.compile(CLI_AUTH_FAILURE_ERE),),
    "network": (
        re.compile(
            r"ENOTFOUND|ECONNRESET|ECONNREFUSED|ConnectionRefused|ETIMEDOUT|socket hang up"
            r"|socket connection was closed unexpectedly|Stream idle timeout|Connection closed mid-response",
            re.IGNORECASE,
        ),
    ),
    "git_lock": (re.compile(r"index\.lock|HEAD\.lock", re.IGNORECASE),),
    "rate_limited": (
        re.compile(r"hit your .{0,20}limit|limit.{0,20}resets", re.IGNORECASE),
        re.compile(r"API Error: 429|too many requests|overloaded", re.IGNORECASE),
    ),
}
FAILURE_LINE_MAX = 300


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
    # When a `rate_limited` run's usage limit lifts, as the log named it (#407).
    resets_at: datetime | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str  # "yellow" | "red" | "note"
    since: datetime | None
    summary: str
    fix: str
    source: str  # "ledger" | "log" | "schedule" | "events" | "git" | "token"
    # A failed run's own error line, verbatim (#445): what Claude Code or git said, not the class.
    detail: str | None = None
    # Absolute path of that run's log, when it is still on disk.
    log: str | None = None
    # When a usage-limit pause lifts (runs.paused only).
    until: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "since": _iso_or_none(self.since),
            "summary": self.summary,
            "fix": self.fix,
            "source": self.source,
            "detail": self.detail,
            "log": self.log,
            "until": _iso_or_none(self.until),
        }


@dataclass(frozen=True)
class SlotStatus:
    key: str
    type: str
    # The newer of the slot's dispatch and its last session end, as
    # .scout-state/last-fire.json records it (schedule_tick folds the Stop
    # hook's session-end rows into it), so usually just before a run's finish.
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


def _opt_epoch(value: object) -> datetime | None:
    seconds = _opt_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
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
        resets_at=_opt_epoch(obj.get("resets_at")),
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


def failure_line(log: Path, failure_class: str) -> str | None:
    """The newest line in ``log``'s tail that names its ``failure_class`` error, or None.

    Patterns are tried best first; runner banners (``=== … ===``) never count, so
    the line is the CLI's own words. Read over the same tail run-outcome.sh classifies.
    """
    patterns = FAILURE_LINE_RES.get(failure_class, ())
    if not patterns:
        return None
    lines = [ln for ln in _tail_text(log, max_bytes=LOG_TAIL_BYTES).splitlines() if not ln.lstrip().startswith("===")]
    for pattern in patterns:
        hit = next((ln.strip() for ln in reversed(lines) if pattern.search(ln)), None)
        if hit:
            return hit if len(hit) <= FAILURE_LINE_MAX else hit[: FAILURE_LINE_MAX - 1] + "…"
    return None


def _run_log(vault: Path, row: OutcomeRow) -> Path | None:
    """``row``'s log under .scout-logs/, when it is still on disk."""
    if not row.log:
        return None
    path = vault / ".scout-logs" / row.log
    return path if path.is_file() else None


def with_log_reset(rows: list[OutcomeRow], vault: Path) -> list[OutcomeRow]:
    """Fill the newest failure's missing usage-limit reset from its log (#445).

    Runners before #407 recorded no ``resets_at``; their logs still name it. The
    reset is read against the run's own end, so a bare "resets 2pm" is that run's 2pm.
    """
    failed = _trailing_failures(rows)
    newest = failed[-1] if failed else None
    if newest is None or newest.failure_class != "rate_limited" or newest.resets_at is not None:
        return rows
    log = _run_log(vault, newest)
    reset = reset_from_log(log, now=newest.finished_at) if log else None
    if reset is None:
        return rows
    return [replace(r, resets_at=reset) if r is newest else r for r in rows]


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
        detail=failure_line(latest, "oauth_expired"),
        log=str(latest),
    )


def _last_ok_at(rows: list[OutcomeRow]) -> datetime | None:
    oks = [r.finished_at for r in rows if r.outcome == "ok"]
    return max(oks) if oks else None


def _trailing_failures(rows: list[OutcomeRow]) -> list[OutcomeRow]:
    """The failed runs since the newest non-skip run that wasn't a failure, oldest first."""
    failed: list[OutcomeRow] = []
    for r in reversed(rows):
        if r.outcome in SKIP_OUTCOMES:
            continue
        if r.outcome != "failed":
            break
        failed.append(r)
    return failed[::-1]


@dataclass(frozen=True)
class UsagePause:
    """A Claude usage limit in force: the newest failure named a reset still ahead (#424)."""

    began: datetime  # the first run of the trailing streak that failed on the limit
    lifts: datetime
    failed_runs: int  # trailing runs that failed on the limit


def usage_pause(rows: list[OutcomeRow], *, now: datetime) -> UsagePause | None:
    """The usage-limit pause in force at ``now``, or None.

    Every earlier run that failed on the limit (a reset not yet parsed, #424) is
    part of the same pause; a different failure before them is not.
    """
    failed = _trailing_failures(rows)
    newest = failed[-1] if failed else None
    if newest is None or newest.failure_class != "rate_limited" or not newest.resets_at or newest.resets_at <= now:
        return None
    on_limit = list(takewhile(lambda r: r.failure_class == "rate_limited", reversed(failed)))
    return UsagePause(began=on_limit[-1].finished_at, lifts=newest.resets_at, failed_runs=len(on_limit))


def _window_text(row: OutcomeRow) -> str:
    """What landed in *row*'s window, as measured (#237), or nothing when unmeasured.

    A window is observable and authorship is not (a hand commit, or a concurrent
    one, can land in it), so this never says "by this run". A failed run that
    committed before it died is not a run that "produced nothing".
    """
    if row.commits_in_window is None:
        return ""
    if row.commits_in_window == 0:
        return " No commits in that run's window."
    newest = f" (newest {row.newest_commit})" if row.newest_commit else ""
    return f" {row.commits_in_window} commit(s) in that run's window{newest}."


def ledger_findings(rows: list[OutcomeRow], *, vault: Path, now: datetime) -> list[Finding]:
    """Findings derived from the ledger (or the newest log when there is no ledger)."""
    if not rows:
        auth = _log_auth_finding(vault)
        return [auth] if auth else []

    findings: list[Finding] = []
    last_ok = _last_ok_at(rows)
    failed = _trailing_failures(rows)
    streak = len(failed)
    pause = usage_pause(rows, now=now)

    if streak:
        newest = failed[-1]
        log = _run_log(vault, newest)
        detail = failure_line(log, newest.failure_class) if log else None
        log_path = str(log) if log else None
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
                    detail=detail,
                    log=log_path,
                )
            )
        elif pause is not None:
            # It lifts on its own, and the budget gate holds every slot until then
            # (#424): one pause that says when, however many slots failed on the
            # limit first, rather than a red streak that sends anyone to the doctor.
            findings.append(
                Finding(
                    id=PAUSED_FINDING_ID,
                    severity="yellow",
                    since=last_ok,
                    summary=(
                        f"Claude usage limit — runs paused until {iso_z(pause.lifts)}. "
                        f"The last {pause.failed_runs} scheduled run(s) failed on the limit."
                    ),
                    fix="Nothing to fix: the first scheduled run after the reset picks up.",
                    source="ledger",
                    detail=detail,
                    log=log_path,
                    until=pause.lifts,
                )
            )
        else:
            log_hint = f": .scout-logs/{newest.log}" if newest.log else ""
            findings.append(
                Finding(
                    id="runs.failing",
                    severity="red" if streak >= 2 else "yellow",
                    since=last_ok,
                    summary=(
                        f"The last {streak} scheduled run(s) failed ({newest.failure_class})." + _window_text(newest)
                    ),
                    fix=f"Run scoutctl bootstrap doctor and read the newest log{log_hint}",
                    source="ledger",
                    detail=detail,
                    log=log_path,
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
                fix="A long-running session is holding the vault lock; check .scout-logs/ for a run that took hours.",
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
_MIN_SANE_EPOCH = 1_000_000_000
_STALE_FIX = "Check that the Mac was awake and the schedule-tick job is loaded: scoutctl bootstrap doctor"
# Runners record their lock and budget skips (#401), so a fire with no row is a
# run killed before it could record, or a vault whose runners predate that.
_SILENT_FIX = (
    "The run was killed or ended before recording an outcome; see .scout-logs/failures.log. "
    "If this vault's runners predate skip recording, run /scout-update"
)
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


# Repository-selecting variables an outer git process (a hook, a worktree
# shell) may export; inherited, they would point git at some other repo.
_GIT_ENV_DROP = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX"}
)


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}


def vault_birth(vault: Path) -> datetime | None:
    """Root-commit time when the vault itself is a git repo, else scout-config.yaml mtime, else None."""
    env = _git_env()
    try:
        top = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
        if top.returncode == 0 and os.path.samefile(top.stdout.strip(), vault):
            log = subprocess.run(
                ["git", "-C", str(vault), "log", "--max-parents=0", "--format=%ct", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=env,
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


_CRON_BLOCK_BEGIN = "# >>> scout-managed >>>"
_CRON_BLOCK_END = "# <<< scout-managed <<<"


def _cron_has_tick() -> bool:
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    in_block = False
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == _CRON_BLOCK_BEGIN:
            in_block = True
        elif stripped == _CRON_BLOCK_END:
            in_block = False
        elif in_block and "scoutctl schedule tick" in line:
            return True
    return False


def tick_job_installed(home: Path | None = None) -> bool:
    """Is the schedule-tick job installed on THIS host (launchd agent / managed cron line)?

    A vault synced from another Mac, or set up with ``--no-jobs``, never ticks
    here, and that is not a stalled scheduler.
    """
    system = platform.system()
    if system == "Darwin":
        base = home if home is not None else Path.home()
        return (base / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist").exists()
    if system == "Linux":
        return _cron_has_tick()
    return False


def _age(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    return f"{hours // 24} d" if hours >= 72 else f"{hours} h"


def _load_schedule(vault: Path) -> tuple[Schedule, datetime] | None:
    """The vault's schedule and its file's mtime; None when absent or malformed."""
    schedule_path = vault / ".scout-state" / "schedule.yaml"
    try:
        schedule = load_schedule(schedule_path)
        return schedule, datetime.fromtimestamp(schedule_path.stat().st_mtime, UTC)
    except (ConfigError, OSError):
        return None


def schedule_findings(
    vault: Path, rows: list[OutcomeRow], *, now: datetime
) -> tuple[list[Finding], tuple[SlotStatus, ...]]:
    """Slot, fire and scheduler-liveness findings, plus per-slot status."""
    return _schedule_findings(vault, _load_schedule(vault), rows, read_last_fires(vault), now=now)


def _schedule_findings(
    vault: Path,
    loaded: tuple[Schedule, datetime] | None,
    rows: list[OutcomeRow],
    fires: dict[str, datetime],
    *,
    now: datetime,
) -> tuple[list[Finding], tuple[SlotStatus, ...]]:
    statuses: list[SlotStatus] = []
    if loaded is None:
        return [], ()
    schedule, schedule_mtime = loaded

    birth = vault_birth(vault)
    # run-outcome.sh records started_at 0 for a run whose start it couldn't read.
    first_row = min((r.started_at for r in rows if r.started_at.timestamp() >= _MIN_SANE_EPOCH), default=None)
    ref = max(birth, schedule_mtime) if birth is not None else None
    # A run longer than its missed window has no ledger row until it ends.
    session_live = is_lock_held_by_live_pid(vault / ".scout-logs" / ".scout-session.lock")
    slot_findings: list[Finding] = []
    stale: list[Finding] = []
    never_fired = 0
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
                never_fired += 1
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
        # last-fire.json holds the dispatch or, usually, the session end (a
        # second or two before finished_at); a row that ended at or after the
        # fire (less the slack) covers either.
        if (
            not session_live
            and first_row is not None
            and fire > first_row
            and now - fire >= timedelta(hours=mw)
            and not any(r.finished_at >= fire - FIRE_MATCH_SLACK for r in mine)
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
    # Every judged slot not running (stale or never fired), at least one stale: red.
    if stale and len(stale) + never_fired == judged:
        stale = [replace(f, severity="red") for f in stale]
    stalled = _scheduler_stalled(vault, now=now, ref=ref) if judged else None
    return ([stalled] if stalled else []) + slot_findings + stale, tuple(statuses)


def _scheduler_stalled(vault: Path, *, now: datetime, ref: datetime | None) -> Finding | None:
    """Liveness of the tick itself (#199); only asked when some local slot is judged."""
    tick = last_tick_completed(vault)
    if tick is not None:
        if now - tick <= TICK_STALL:
            return None
        return Finding(
            id="scheduler.stalled",
            severity="red",
            since=tick,
            summary=f"The scheduler has not completed a tick since {iso_z(tick)}.",
            fix=_STALLED_FIX,
            source="events",
        )
    if ref is None or now - ref <= TICK_STALL or not tick_job_installed():
        return None
    return Finding(
        id="scheduler.stalled",
        severity="red",
        since=None,
        summary="The scheduler has never recorded a tick.",
        fix=_STALLED_FIX,
        source="events",
    )


# (warn, dark) days without committed output. Deliberately generous: these mean
# "the lane is broken", not "a quiet night".
LANE_BUDGETS_DAYS = {
    "briefing": (2, 4),
    "consolidation": (2, 4),
    "dreaming": (3, 7),
    "research": (3, 7),
}
DEFAULT_BUDGET = (3, 7)
RECENT_FIRE_DAYS = 1.5  # fired this recently but nothing committed: firing but silent
COMMIT_SCAN = 4000
_LANE_AUTH_RE = re.compile(
    r"OAuth session expired|OAuth token (has )?expired|Failed to authenticate"
    r"|Invalid authentication credentials|API Error: 40[13]",
    re.IGNORECASE,
)
_LANE_DARK_FIX = "Read the newest run log under .scout-logs/ and run: scoutctl bootstrap doctor"
_LANE_SILENT_FIX = (
    "The scheduler is firing this lane and it commits nothing. Check the newest run log: "
    "an expired login, the budget check, or a held lock are the usual causes."
)


def lane_budgets(lane: str, weekdays: tuple[str, ...]) -> tuple[int, int]:
    """(warn, dark) days, widened so a lane's own schedule gaps never read as an outage."""
    warn, dark = LANE_BUDGETS_DAYS.get(lane, DEFAULT_BUDGET)
    gap = longest_gap_hours(weekdays) // 24
    return max(warn, gap), max(dark, gap + 1)


def recent_commits(vault: Path, limit: int = COMMIT_SCAN) -> list[tuple[datetime, str]]:
    """(commit time, subject) for the vault's newest ``limit`` commits; [] without git or a repo.

    Only a repo rooted at the vault counts: a vault nested inside some other
    repository must not be judged by that repository's history.
    """
    env = _git_env()
    try:
        top = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
        if top.returncode != 0 or not os.path.samefile(top.stdout.strip(), vault):
            return []
        raw = subprocess.run(
            ["git", "-C", str(vault), "log", "-n", str(limit), "--format=%ct%x1f%s"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[tuple[datetime, str]] = []
    for line in raw.splitlines():
        epoch, sep, subject = line.partition("\x1f")
        if sep and epoch.isdigit():
            out.append((datetime.fromtimestamp(int(epoch), UTC), subject))
    return out


def newest_log_outcome(vault: Path, runner: str | None, ledger_logs: list[str], *, shared: bool) -> str | None:
    """Why a lane's newest run produced nothing, read from its runner log.

    The logs the ledger recorded for the lane are always its own. A runner's
    other logs (``<stem>-YYYY-MM-DD_HH-MM.log``, including runs that stopped
    before the ledger step) count only when no other lane uses the same runner:
    run-scout.sh serves both briefing and consolidation and its log names do not
    say which one ran.
    """
    log_dir = vault / ".scout-logs"
    names = {name for name in ledger_logs if (log_dir / name).is_file()}
    m = re.fullmatch(r"run-(.+)\.sh", runner or "")
    if m and not shared:
        pattern = re.compile(re.escape(m.group(1)) + r"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.log")
        names |= {p.name for p in log_dir.glob("*.log") if pattern.fullmatch(p.name)}
    if not names:
        return None
    name = sorted(names)[-1]
    try:
        text = (log_dir / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "Budget check: skipping" in text:
        return f"{name}: budget-skip"
    if _LANE_AUTH_RE.search(text):
        return f"{name}: AUTH FAILURE"
    if re.search(r"Another .* session running", text):
        return f"{name}: lock-skip"
    code = re.search(r"exit code: (\d+)", text)
    if code:
        return f"{name}: exit {code.group(1)}"
    return f"{name}: no completion line"


def _days(now: datetime, then: datetime | None) -> float | None:
    return (now - then).total_seconds() / 86400 if then else None


def _days_text(days: float) -> str:
    n = int(days)
    return f"{n} day{'' if n == 1 else 's'}"


def lane_findings(
    vault: Path,
    schedule: Schedule | None,
    rows: list[OutcomeRow],
    fires: dict[str, datetime],
    *,
    now: datetime,
    commits: list[tuple[datetime, str]] | None = None,
    paused_since: datetime | None = None,
) -> list[Finding]:
    """Does each session type (lane) still produce commits? (port of session-lane-liveness.py)

    A lane is every slot of one type, ``manual`` excluded, judged by committed
    output rather than last-fire (which advances even when a run dies at the
    door). With no commit history to read (``commits`` empty, or git absent when
    ``commits`` is None) nothing can be judged, so no lane is reported.

    Verdicts, as the script's: OK and DEGRADED (inside the dark budget) and NO DATA
    YET raise nothing; DARK is ``lane.dark:<type>``, red; FIRING-BUT-SILENT (past
    the dark budget, yet fired within ``RECENT_FIRE_DAYS``) is ``lane.silent:<type>``,
    also red: a broken lane that still fires, which the script exited 3 on and
    pushed. Both ids share one notifier key (``lane:<type>``), so a lane flapping
    between them is one outage.

    ``paused_since`` is when a Claude usage limit that is still in force began
    (:func:`usage_pause`). A lane that was inside its dark budget then has gone
    quiet only because runs are paused, which ``runs.paused`` already says, so it
    is not reported (#424). A lane that was already dark is.
    """
    if schedule is None:
        return []
    if commits is None:
        commits = recent_commits(vault)
    if not commits:
        return []

    lanes: dict[str, list[str]] = {}
    for slot in schedule.values():
        if slot.type is not SlotType.MANUAL:
            lanes.setdefault(slot.type.value, []).append(slot.key)

    def lane_of_slot(slot: str) -> str | None:
        if slot in schedule:
            lane = schedule[slot].type.value
            return lane if lane in lanes else None
        prefix = slot.split("-", 1)[0]  # an unscheduled run such as "dreaming-manual"
        return prefix if prefix in lanes else None

    windows: dict[str, list[tuple[datetime, datetime]]] = {lane: [] for lane in lanes}
    ledger_logs: dict[str, list[str]] = {lane: [] for lane in lanes}
    for row in rows:
        lane = lane_of_slot(row.slot)
        if lane:
            # run-outcome.sh records started_at 0 for a run whose start it
            # couldn't read; that row's window would span 1970..now.
            if row.started_at.timestamp() >= _MIN_SANE_EPOCH:
                windows[lane].append((row.started_at, row.finished_at))
            if row.log:
                ledger_logs[lane].append(row.log)

    output: dict[str, datetime] = {}
    for when, subject in commits:
        words = subject.split()
        named = words[0].rstrip(":").lower() if words else ""
        # A subject that names a lane is authoritative; only an unnamed commit is
        # credited to whichever lane's run it landed in.
        if named in lanes:
            owners = {named}
        else:
            owners = {lane for lane, spans in windows.items() if any(s <= when <= f for s, f in spans)}
        for lane in owners:
            if lane not in output or when > output[lane]:
                output[lane] = when

    findings: list[Finding] = []
    for lane in sorted(lanes):
        slots = lanes[lane]
        weekdays = tuple(d for key in slots for d in schedule[key].weekdays)
        warn, dark = lane_budgets(lane, weekdays)
        commit_dt = output.get(lane)
        fire_dt = max((fires[k] for k in slots if k in fires), default=None)
        commit_age, fire_age = _days(now, commit_dt), _days(now, fire_dt)
        observed = _days(now, min((s for s, _ in windows[lane]), default=None))

        if commit_age is not None and commit_age < dark:
            continue  # OK, or DEGRADED (inside the dark budget): not worth a finding
        if commit_age is None and (observed is None or observed < dark):
            continue  # NO DATA YET: not observed for the lane's whole dark budget
        if paused_since is not None and commit_dt is not None and (paused_since - commit_dt).days < dark:
            continue  # inside its budget when the usage limit paused runs
        silent = fire_age is not None and fire_age < RECENT_FIRE_DAYS
        summary = (
            f"{lane} has produced no commits in {_days_text(commit_age)}"
            if commit_age is not None
            else f"{lane} has no commits on record"
        )
        runner = schedule[slots[0]].runner
        shared = any(schedule[k].runner == runner for other in lanes if other != lane for k in lanes[other])
        newest = newest_log_outcome(vault, runner, ledger_logs[lane], shared=shared)
        if newest:
            summary += f"; newest run log: {newest}"
        findings.append(
            Finding(
                id=f"lane.{'silent' if silent else 'dark'}:{lane}",
                severity="red",
                since=commit_dt,
                summary=summary,
                fix=_LANE_SILENT_FIX if silent else _LANE_DARK_FIX,
                source="git",
            )
        )
    return findings


UPDATE_DEFER_HOLD = timedelta(hours=72)
UPDATE_BEHIND_NOTE = timedelta(days=14)
_UPDATE_SOURCE = "update ledger"
_PRE_SWITCH_STEPS = frozenset({"check", "fetch", "venv"})


def _newer(target: str | None, installed: str) -> bool:
    from scout.scripts.self_update import _semver_tuple

    if not target:
        return False
    try:
        return _semver_tuple(target) > _semver_tuple(installed)
    except ValueError:
        return False


def _trailing(rows: list[UpdateOutcome], result: str) -> list[UpdateOutcome]:
    run: list[UpdateOutcome] = []
    for row in reversed(rows):
        if row.result != result:
            break
        run.append(row)
    return run[::-1]


def _update_words(row: UpdateOutcome) -> str:
    """The newest attempt in a few words, for ``update.last`` (and the app's "Updates" row)."""
    target = row.target or "a new version"
    return {
        "up_to_date": f"up to date ({row.target or row.from_version})",
        "applied": f"updated to {target}",
        "applied_with_sidecars": f"updated to {target}, with files to merge",
        "waiting_release": f"{target} is not published yet, or is still settling",
        "deferred": f"put off: {row.detail}",
        "held": f"on hold: {row.detail}",
        "failed": f"the update to {target} failed at the {row.step} step",
    }[row.result]


def update_findings(vault: Path, rows: list[UpdateOutcome], *, now: datetime) -> list[Finding]:
    """``update.*`` findings from the headless-update ledger (spec §4.2, #360)."""
    if not rows:
        return []
    newest = rows[-1]
    log_hint = f" (log: .scout-logs/{newest.log})" if newest.log else ""
    findings: list[Finding] = []
    if newest.result == "failed":
        engine = (
            "the current engine still runs" if newest.step in _PRE_SWITCH_STEPS else "the vault may be partly upgraded"
        )
        findings.append(
            Finding(
                id="update.failed",
                severity="red",
                since=_trailing(rows, "failed")[0].at,
                summary=f"Scout's update to {newest.target or 'a new version'} failed at the {newest.step} step; "
                f"{engine}. {newest.detail}".strip(),
                fix=f"Run: scoutctl bootstrap upgrade{log_hint}",
                source=_UPDATE_SOURCE,
            )
        )
    elif newest.result == "held":
        findings.append(
            Finding(
                id="update.held",
                severity="yellow",
                since=newest.at,
                summary=f"Scout's update to {newest.target or 'a new version'} is on hold: {newest.detail}".strip(),
                fix=f"Run: scoutctl bootstrap doctor{log_hint}",
                source=_UPDATE_SOURCE,
            )
        )
    elif newest.result == "deferred":
        first = _trailing(rows, "deferred")[0]
        if now - first.at > UPDATE_DEFER_HOLD:
            findings.append(
                Finding(
                    id="update.held",
                    severity="yellow",
                    since=first.at,
                    summary=f"Scout's update to {newest.target or 'a new version'} has been put off for "
                    f"{_age(now - first.at)}: {newest.detail}".strip(),
                    fix=f"Run: scoutctl self-update run{log_hint}",
                    source=_UPDATE_SOURCE,
                )
            )
    if newest.sidecars:
        from scout.scripts.brain_merge import pending_brain_sidecars

        pending = [s for s in newest.sidecars if s in set(pending_brain_sidecars(vault))]
        if pending:
            findings.append(
                Finding(
                    id="update.sidecars",
                    severity="yellow",
                    since=newest.at,
                    summary=f"The update to {newest.target} left files to merge by hand: {', '.join(pending)}. "
                    "Scout keeps running on the current versions.",
                    fix="Run: scoutctl bootstrap drift, then scoutctl bootstrap resolve",
                    source=_UPDATE_SOURCE,
                )
            )
    if newest.result != "not_eligible":
        findings.append(
            Finding(
                id="update.last",
                severity="note",
                since=newest.at,
                summary=f"Updates automatically. Last checked {_age(now - newest.at)} ago: {_update_words(newest)}.",
                fix="Run: scoutctl self-update run --dry-run",
                source=_UPDATE_SOURCE,
            )
        )
    if _newer(newest.target, newest.from_version):
        if newest.result == "not_eligible" and newest.managed_by in ("dev", "unknown"):
            findings.append(
                Finding(
                    id="update.behind",
                    severity="note",
                    since=newest.at,
                    summary=f"This engine ({newest.managed_by}, {newest.from_version}) is behind the latest "
                    f"release {newest.target}; Scout does not update it automatically.",
                    fix="Update the checkout yourself, then run: scoutctl bootstrap upgrade",
                    source=_UPDATE_SOURCE,
                )
            )
        elif (
            newest.result != "not_eligible"
            and newest.release_published_at is not None
            and now - newest.release_published_at > UPDATE_BEHIND_NOTE
        ):
            findings.append(
                Finding(
                    id="update.behind",
                    severity="note",
                    since=newest.release_published_at,
                    summary=f"Scout {newest.target} was released {_age(now - newest.release_published_at)} ago; "
                    f"this engine is still on {newest.from_version}.",
                    fix="Run: scoutctl self-update run",
                    source=_UPDATE_SOURCE,
                )
            )
    return findings


_TOKEN_FIX = "Open Terminal and run: scoutctl auth setup-token"


def token_findings(found: list[Finding], *, vault: Path, home: Path | None, now: datetime) -> list[Finding]:
    """Adjust for a stored long-lived token (#267, run-health P6).

    The token overrides the login, so a rejection names the token's fix, not
    `claude auth login`. A token inside its last 30 days, or past them, is yellow:
    it lasts a year and nothing else says when that is. So is a last run that
    couldn't read the stored token (a locked Keychain) and fell back to the login.
    """
    from scout import claude_auth

    last = claude_auth.last_sign_in(vault)
    if last is not None and last.token_unreadable:
        found = [
            *found,
            Finding(
                id="auth.token_unreadable",
                severity="yellow",
                since=last.at,
                summary=(
                    "The last scheduled run couldn't read the stored long-lived Claude token (is the "
                    "Keychain locked?) and used your interactive login, which expires."
                ),
                fix="Check it with: scoutctl auth status (keep the login Keychain unlocked while runs fire)",
                source="token",
            ),
        ]
    st = claude_auth.status(claude_auth.default_store(home=home), now=now)
    if st.mode != "token":
        return found
    out = []
    for f in found:
        if f.id == "auth.expired":
            summary = f.summary.replace("your Claude login expired", "your stored Claude token was rejected")
            f = replace(f, summary=summary, fix=f"{_TOKEN_FIX} (or scoutctl auth clear to use your Claude login)")
        out.append(f)
    expiry = st.expiry_text()
    if st.expiring and expiry:
        out.append(
            Finding(
                id="auth.token_expiring",
                severity="yellow",
                since=None,
                summary=f"The long-lived Claude token scheduled runs use {expiry}.",
                fix=_TOKEN_FIX,
                source="token",
            )
        )
    return out


def evaluate(vault: Path, *, now: datetime | None = None, home: Path | None = None) -> RunHealth:
    """``home`` locates the stored Claude token (default: the real home)."""
    now = now or datetime.now(UTC)
    rows = with_log_reset(read_outcomes(vault), vault)
    found = token_findings(ledger_findings(rows, vault=vault, now=now), vault=vault, home=home, now=now)
    loaded = _load_schedule(vault)
    fires = read_last_fires(vault)
    sched, slots = _schedule_findings(vault, loaded, rows, fires, now=now)
    pause = usage_pause(rows, now=now)
    lanes = lane_findings(
        vault, loaded[0] if loaded else None, rows, fires, now=now, paused_since=pause.began if pause else None
    )
    updates = update_findings(vault, read_updates(vault), now=now)
    return build_report(found + sched + lanes + updates, now=now, last_ok_at=_last_ok_at(rows), slots=slots)

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
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta, tzinfo
from itertools import takewhile
from pathlib import Path

from scout.config import config_load_problem, resolve_timezone
from scout.engine_checkout import WORKTREES_HINT, checkout_state
from scout.errors import ConfigError
from scout.plural import count, have, last_runs_were_skipped, noun, the_last, were
from scout.schedule import Schedule, SlotRuntime, SlotType, load_schedule
from scout.scripts.bootstrap_lock import is_lock_held_by_live_pid
from scout.scripts.rate_limit_reset import LOG_TAIL_BYTES, reset_from_log
from scout.update_outcomes import UpdateOutcome
from scout.update_outcomes import read as read_updates

SEVERITY_ORDER = {"note": 0, "yellow": 1, "red": 2}
SKIP_OUTCOMES = frozenset(
    {
        "skipped_lock",
        "skipped_budget",
        "skipped_redundant",
        "skipped_lease",
        "skipped_gates",
        "skipped_sandbox",
        "skipped_runtime",
    }
)
# A run skipped because the gate probe found claude not loading Scout's hooks (spec 2026-11-13 §3.5).
GATES_SKIP = "skipped_gates"
# A run skipped because the write sandbox expected on this machine couldn't be set up (spec 2026-11-15 §8).
SANDBOX_SKIP = "skipped_sandbox"
# A run skipped because `scoutctl agent-runtime check` refused its agent runtime (spec 2026-11-13 §8.3).
RUNTIME_SKIP = "skipped_runtime"
# A vault linked to Agnes skips a run it can't take the Repository Writer Lease
# for (R2): failure_class lease_held (Agnes holds it) or lease_unavailable.
LEASE_SKIP = "skipped_lease"
LEASE_UNAVAILABLE_RED = 3

LEDGER_NAME = "run-outcomes.jsonl"
LOCK_SKIP_THRESHOLD = 3
LOCK_SKIP_WINDOW = timedelta(hours=24)
# How long a run whose protected files the integrity check had to put back keeps health red (spec 2026-11-15 §13):
# the restore fixed the vault, but the owner should look at what that run did.
INTEGRITY_WINDOW = timedelta(days=7)
# Runs that fail, succeed, then fail again never form a trailing streak, so
# runs.failing clears on every success between them. This many failed runs in
# the window, with a success after the first of them, is a pattern of its own.
RECURRING_FINDING_ID = "runs.recurring"
# Two days, not one: failures about twice a day can leave one inside any 24 h.
RECURRING_FAILURE_THRESHOLD = 3
RECURRING_FAILURE_WINDOW = timedelta(hours=48)
# A Claude usage limit that named its reset: runs wait for it (#424).
PAUSED_FINDING_ID = "runs.paused"
# A run that failed today whose schedule hasn't finished since (UI audit R1). The trailing streak
# (runs.failing) clears on any later success, so a failed morning briefing followed by a clean
# consolidation read "healthy" all day; this keeps it yellow until that schedule itself finishes.
FAILED_TODAY_FINDING_ID = "runs.failed_today"
# The newest run exited 0 in under a minute with nothing committed in its window. Exit 0 only says the session
# ended: one that stopped before doing its work (the v0.18.19 KB gate's took one denied Bash call to mean "Bash is
# off" and quit after 28 s) is green under every other finding. Every scheduled run commits its session-log row.
HOLLOW_FINDING_ID = "runs.hollow_ok"
HOLLOW_MAX_S = 60

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

# Cloudflare's bot-challenge page in front of the API. The CLI reports it as
# "Failed to authenticate. API Error: 403 Attention Required! | Cloudflare", which the
# auth pattern above matches, but no credential was rejected: telling the user to log in
# again would be wrong. Same string as run-outcome.sh.tmpl's EDGE_CHALLENGE_RE.
_EDGE_CHALLENGE_RE = re.compile(r"API Error: 403 Attention Required! \| Cloudflare")

_AUTH_FIX = "Open Terminal and run: claude auth login (or scoutctl auth setup-token for unattended runs)"

# The line that names a failed run's error, per ledger failure class, best first (#445). Each is
# a narrower cut of run-outcome.sh's class pattern (a test holds every pick to it), aimed at the
# CLI's own message rather than the runner's banners, which repeat or merely name the class.
#
# Each pattern must match at the START of a (stripped) line, in the shape Claude Code, git or the
# retry wrapper prints. The log also holds the session's own output, which can quote vault notes,
# so a line that merely mentions a limit or a lock is never quoted (C8 review: a model line
# "Drafting reply about <note>: …hit your monthly limit, resets in May" used to come out verbatim).
_ERR = r"(?:Error: )?"
_NET_CODES = r"(?:ENOTFOUND|ECONNRESET|ECONNREFUSED|ETIMEDOUT|ConnectionRefused|FailedToOpenSocket)"
_NET_STREAM = (
    r"(?:Stream idle timeout|Connection closed mid-response|(?:The )?socket connection was closed unexpectedly)"
)
FAILURE_LINE_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    "oauth_expired": (re.compile(CLI_AUTH_FAILURE_ERE),),
    # The sign-in race (run-outcome.sh's AUTH_RACE_RE), when Claude Code's sentence opens the line. That line
    # names the account's email, which failure_line() writes as <email>.
    "auth_race": (
        re.compile(
            _ERR + r"(?:(?:Claude Code )?couldn.{1,3}t verify your sign-in with Anthropic yet"
            r"|Claude Code .{0,80}?sign-in token was still being refreshed)"
        ),
    ),
    "network": (
        re.compile(
            r"(?:API Error: .{0,80}?(?:" + _NET_CODES + r"|socket hang up|Connection error|" + _NET_STREAM + r")"
            r"|" + _NET_STREAM + r"|socket hang up"
            # Bun's error object, one field per line: `code: "ConnectionRefused",`
            r'|code: "' + _NET_CODES + r'"'
            r"|" + _ERR + r"(?:getaddrinfo |connect |read )?" + _NET_CODES + r"\b)",
            re.IGNORECASE,
        ),
    ),
    "git_lock": (re.compile(r"(?:fatal|error): .{0,240}?\b(?:index|HEAD)\.lock\b"),),
    "rate_limited": (
        re.compile(
            _ERR + r"(?:You(?:'ve| have) (?:hit|reached) your\b.{0,40}?\blimit"
            r"|Claude (?:AI )?usage limit reached"
            # "5-hour limit reached ∙ resets 1am", "Opus weekly limit reached ∙ resets Mon"
            r"|(?:(?:\d+-hour|weekly|daily|session|Opus|Sonnet|Haiku)\s+){0,2}limit reached\s*[·∙•|-]\s*resets)",
            re.IGNORECASE,
        ),
        re.compile(r"(?:API )?Error: (?:429|529)\b|API Error: .{0,40}?(?:overloaded|too many requests)", re.IGNORECASE),
    ),
    # claude's cap message, or the engine's line when the retry wrapper withheld a retry (#561).
    "budget": (re.compile(_ERR + r"Exceeded USD budget|budget spent.{0,8}no retry", re.IGNORECASE),),
}
FAILURE_LINE_MAX = 300
# An address in a quoted error line (the sign-in race names the account) reaches the app and diagnostics
# bundles as <email>: it never helps read the error.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


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
    # Whether the session ran in the write sandbox (Phase 1c): enforced | unavailable | off | conflict | failed;
    # None for rows from runners that predate it.
    sandbox: str | None = None
    # When sandbox is "off": "env" (SCOUT_RUN_SANDBOX) or "switch" (scoutctl sandbox off).
    sandbox_source: str | None = None
    # The post-run integrity check: clean | restored | unrestored | error | unchecked; None for older runners.
    integrity: str | None = None
    # Wall-clock length, and length in awake time (time the host slept doesn't count); None for older runners.
    duration_s: int | None = None
    awake_s: int | None = None
    # The agent runtime that ran the session (spec 2026-11-13 §8.2), e.g. "claude-code".
    agent_runtime: str | None = None
    # The model the runner passed to the agent (scoutctl model resolve).
    model: str | None = None
    # Which runner wrote the row (scout | dreaming | research) and what started it
    # (schedule | heartbeat | manual | event: a connector-event trigger).
    runner: str | None = None
    trigger: str | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str  # "yellow" | "red" | "note"
    since: datetime | None
    summary: str
    fix: str
    source: str  # "ledger" | "log" | "schedule" | "events" | "git" | "token" | "config" | "state"
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
        sandbox=str(obj["sandbox"]) if isinstance(obj.get("sandbox"), str) else None,
        sandbox_source=str(obj["sandbox_source"]) if isinstance(obj.get("sandbox_source"), str) else None,
        integrity=str(obj["integrity"]) if isinstance(obj.get("integrity"), str) else None,
        duration_s=_opt_nonneg(obj.get("duration_s")),
        awake_s=_opt_nonneg(obj.get("awake_s")),
        agent_runtime=_opt_token(obj.get("agent_runtime"), _RUNTIME_RE),
        model=_opt_token(obj.get("model"), _MODEL_RE),
        runner=obj["runner"] if obj.get("runner") in _RUNNERS else None,
        trigger=obj["trigger"] if obj.get("trigger") in _TRIGGERS else None,
    )


# The same shapes run-outcome.sh writes, so a hand-edited row can't carry free text into a client.
_RUNTIME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,80}")
_RUNNERS = frozenset({"scout", "dreaming", "research"})
_TRIGGERS = frozenset({"schedule", "heartbeat", "manual", "event"})


def _opt_nonneg(value: object) -> int | None:
    n = _opt_int(value) if not isinstance(value, bool) else None
    return n if n is not None and n >= 0 else None


def _opt_token(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


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

    Patterns are tried best first and must open the line; runner banners (``=== … ===``)
    never count, so the line is the CLI's own words, never the session's prose. Read over
    the same tail run-outcome.sh classifies.
    """
    patterns = FAILURE_LINE_RES.get(failure_class, ())
    if not patterns:
        return None
    lines = [ln.strip() for ln in _tail_text(log, max_bytes=LOG_TAIL_BYTES).splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("===")]
    for pattern in patterns:
        # match(), not search(): the error shape must open the line (see FAILURE_LINE_RES).
        hit = next((ln for ln in reversed(lines) if pattern.match(ln)), None)
        if hit:
            hit = _EMAIL_RE.sub("<email>", _home_relative(hit))
            return hit if len(hit) <= FAILURE_LINE_MAX else hit[: FAILURE_LINE_MAX - 1] + "…"
    return None


def _home_relative(text: str) -> str:
    """``text`` with the user's home directory written as ``~`` (a git lock error names the vault's path)."""
    try:
        home = str(Path.home())
    except RuntimeError:
        return text
    return text.replace(home + "/", "~/") if home and home != "/" else text


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
    # The challenge line is the CLI's auth wording too; drop it, so a real 401 on a retry still counts.
    tail = "\n".join(ln for ln in _tail_text(latest).splitlines() if not _EDGE_CHALLENGE_RE.search(ln))
    if not _AUTH_FAILURE_RE.search(tail):
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
    return f" {count(row.commits_in_window, 'commit')} in that run's window{newest}."


# The sign-in race (failure_class auth_race): the login is fine, and the retry wrapper already retried it.
_AUTH_RACE_FIX = (
    "Claude Code's sign-in was busy: another Claude Code process was refreshing it when these runs started, "
    "and the login itself is fine. It usually clears by itself, and the retry wrapper retries it after a "
    "short wait (SCOUT_RETRY_MAX attempts in all). If it keeps happening, move those slots away from times "
    "you usually have Claude Code open, or store a long-lived token for scheduled runs: scoutctl auth setup-token"
)

_RECURRING_FIX = {
    "budget": (
        "These runs reached the per-session cap (claude --max-budget-usd, set by connectors.inputs.max_budget "
        "in scout-config.yaml). Compare what the runs cost with that cap, then raise it or move those slots "
        "to a cheaper model (the slot's model: in .scout-state/schedule.yaml)."
    ),
    "auth_race": _AUTH_RACE_FIX,
}


def _recurring_rows(rows: list[OutcomeRow], *, now: datetime) -> list[OutcomeRow]:
    """The failed runs runs.recurring counts, oldest first; [] when they make no pattern."""
    cutoff = now - RECURRING_FAILURE_WINDOW
    recent = [r for r in rows if r.outcome not in SKIP_OUTCOMES and r.finished_at >= cutoff]
    oks = [r.finished_at for r in recent if r.outcome == "ok"]
    if not oks:
        return []
    settled = [r for r in recent if r.finished_at <= max(oks)]
    failed = [r for r in settled if r.outcome == "failed"]
    if len(failed) < RECURRING_FAILURE_THRESHOLD:
        return []
    first, last = failed[0].finished_at, failed[-1].finished_at
    if not any(r.outcome == "ok" and first < r.finished_at < last for r in settled):
        return []  # one streak that has ended: nothing recurs
    return failed


def recurring_failures(
    rows: list[OutcomeRow], *, vault: Path, now: datetime, still_failing: str | None = None
) -> Finding | None:
    """Failed runs in the last 48 h with a success between them, which the trailing streak can't show.

    A vault whose runs fail about twice a day reads green after every success
    under runs.failing alone. This names how many failed, of how many ran, and
    the failure classes, so the pattern stays visible between failures.

    Only runs up to the last success count: a trailing streak is reported by
    runs.failing, auth.expired or runs.paused, and counting it here would add a
    doctor fix next to a pause, or advice for an older class next to a login
    that has expired. A single streak that has already ended isn't a pattern
    either, so a success must fall between the first and last failure counted.

    ``still_failing`` names this pattern's slots that failed again today and haven't
    finished since; they are said here, not in a second finding (runs.failed_today).
    """
    failed = _recurring_rows(rows, now=now)
    if not failed:
        return None
    cutoff = now - RECURRING_FAILURE_WINDOW
    last_ok = max(r.finished_at for r in rows if r.outcome == "ok" and r.finished_at >= cutoff)
    settled = [r for r in rows if r.outcome not in SKIP_OUTCOMES and cutoff <= r.finished_at <= last_ok]
    first = failed[0].finished_at
    classes = Counter(r.failure_class for r in failed).most_common()
    class_text = ", ".join(f"{klass} ×{n}" for klass, n in classes)
    slots = ", ".join(sorted({r.slot for r in failed}))
    newest = failed[-1]
    log = _run_log(vault, newest)
    log_hint = f": .scout-logs/{newest.log}" if newest.log else ""
    summary = (
        f"{len(failed)} of the last {len(settled)} scheduled runs failed in the past 48 h, "
        f"with successes in between ({class_text}). Failed slots: {slots}."
    )
    if still_failing:
        summary += f" Still failing today, with no finished run since: {still_failing}."
    return Finding(
        id=RECURRING_FINDING_ID,
        severity="yellow",
        since=first,
        summary=summary,
        fix=_RECURRING_FIX.get(
            classes[0][0], f"Run scoutctl bootstrap doctor and read the newest failed log{log_hint}"
        ),
        source="ledger",
        detail=failure_line(log, newest.failure_class) if log else None,
        log=str(log) if log else None,
    )


@dataclass(frozen=True)
class OpenFailure:
    """One slot's failures today since it last finished a run (runs.failed_today)."""

    first: OutcomeRow
    newest: OutcomeRow

    @property
    def slot(self) -> str:
        return self.newest.slot


def open_failures_today(
    rows: list[OutcomeRow], *, vault: Path, now: datetime, tz: tzinfo | None = None
) -> tuple[list[OpenFailure], tzinfo]:
    """Each scheduled slot that failed today and hasn't finished a run since, oldest first, and
    the vault's zone.

    "Today" is the vault's day (``user.timezone``, else the host's zone), the same day the usage
    summary counts. A scheduled slot recovers when it next finishes a run: a success of another
    slot doesn't count, which is the case the trailing streak misses. A run no schedule runs
    (trigger ``manual``, or a slot the schedule doesn't name, such as a trigger's skill) has no
    next scheduled run, so any later finished run clears it; one that nothing has followed is in
    the trailing streak, which runs.failing, auth.expired or runs.paused already report, so this
    never names one. Skips clear nothing.
    """
    zone = tz or resolve_timezone(vault)
    day_start = datetime.combine(now.astimezone(zone).date(), time(0), tzinfo=zone)
    loaded = _load_schedule(vault)

    def is_scheduled(r: OutcomeRow) -> bool:
        return r.trigger != "manual" and (loaded is None or r.slot in loaded[0])

    in_streak = {r.slot for r in _trailing_failures(rows)}
    open_: dict[str, list[OutcomeRow]] = {}
    for r in sorted(rows, key=lambda r: r.finished_at):
        if r.outcome == "ok":
            open_.pop(r.slot, None)
            for slot in [s for s, failed in open_.items() if not is_scheduled(failed[-1])]:
                del open_[slot]
        elif r.outcome == "failed" and r.finished_at >= day_start and r.slot not in in_streak:
            open_.setdefault(r.slot, []).append(r)
    found = [OpenFailure(first=f[0], newest=f[-1]) for f in open_.values()]
    return sorted(found, key=lambda o: o.newest.finished_at), zone


def describe_open_failures(found: list[OpenFailure], zone: tzinfo) -> str:
    """``morning-briefing (at 08:00, unknown), …``: each slot with its newest failure's time and class."""

    def one(o: OpenFailure) -> str:
        r = o.newest
        klass = f", {r.failure_class}" if r.failure_class and r.failure_class != "ok" else ""
        return f"{r.slot} (at {r.finished_at.astimezone(zone):%H:%M}{klass})"

    return ", ".join(one(o) for o in found)


def failed_today_finding(
    found: list[OpenFailure], *, rows: list[OutcomeRow], vault: Path, zone: tzinfo
) -> Finding | None:
    """runs.failed_today for ``found`` (UI audit R1), or None when nothing is open.

    The newest failure speaks for each slot (its time and class); ``since`` is the first one.
    """
    if not found:
        return None
    named = describe_open_failures(found, zone)
    if len(found) == 1:
        summary = f"{named} failed today and hasn't finished a run since."
    else:
        summary = f"{len(found)} schedules failed today and haven't finished a run since: {named}."
    first = min(o.first.finished_at for o in found)
    if any(r.outcome == "ok" and r.finished_at > first for r in rows):
        summary += " Other scheduled runs have finished since."
    newest = found[-1].newest
    log = _run_log(vault, newest)
    log_hint = f" (.scout-logs/{newest.log})" if newest.log else ""
    return Finding(
        id=FAILED_TODAY_FINDING_ID,
        severity="yellow",
        since=first,
        summary=summary,
        fix=(
            f"Read the failed run's log{log_hint}. This clears when "
            f"{'that schedule' if len(found) == 1 else 'each schedule'} next finishes a run, or run it again now."
        ),
        source="ledger",
        detail=failure_line(log, newest.failure_class) if log else None,
        log=str(log) if log else None,
    )


def failed_today(rows: list[OutcomeRow], *, vault: Path, now: datetime, tz: tzinfo | None = None) -> Finding | None:
    """runs.failed_today on its own, without folding into runs.recurring (see ledger_findings)."""
    found, zone = open_failures_today(rows, vault=vault, now=now, tz=tz)
    return failed_today_finding(found, rows=rows, vault=vault, zone=zone)


def hollow_ok(rows: list[OutcomeRow], *, vault: Path) -> Finding | None:
    """runs.hollow_ok: the newest run (skips aside) exited ok in under :data:`HOLLOW_MAX_S` with zero commits.

    Only a measured window counts: a row whose commits or length weren't measured (an older runner, no git)
    says nothing. It clears when the next run finishes with a commit, fails, or takes longer.
    """
    newest = next((r for r in reversed(rows) if r.outcome not in SKIP_OUTCOMES), None)
    if newest is None or newest.outcome != "ok" or newest.commits_in_window != 0:
        return None
    if newest.duration_s is None or newest.duration_s >= HOLLOW_MAX_S:
        return None
    log = _run_log(vault, newest)
    log_hint = f" (.scout-logs/{newest.log})" if newest.log else ""
    return Finding(
        id=HOLLOW_FINDING_ID,
        severity="yellow",
        since=newest.finished_at,
        summary=(
            f"The last run ({newest.slot}) ended ok after {newest.duration_s} s with no commits in its window: "
            "it probably stopped before doing its work."
        ),
        fix=(
            f"Read the end of its log{log_hint}: a session that stops early says why, e.g. a denied tool call it "
            "took to mean the run can't go on. This clears when the next run commits."
        ),
        source="ledger",
        log=str(log) if log else None,
    )


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
                        f"{count(streak, 'failed run')} since the last success ({when})."
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
                        f"{the_last(pause.failed_runs, 'scheduled run')} failed on the limit."
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
            # A streak of sign-in races only: nothing to repair, so it stays yellow until it has cost as many
            # runs as a recurring failure does, and says what it is rather than sending anyone to the doctor.
            races = all(r.failure_class == "auth_race" for r in failed)
            red_at = RECURRING_FAILURE_THRESHOLD if races else 2
            findings.append(
                Finding(
                    id="runs.failing",
                    severity="red" if streak >= red_at else "yellow",
                    since=last_ok,
                    summary=(
                        f"{the_last(streak, 'scheduled run')} failed ({newest.failure_class})." + _window_text(newest)
                    ),
                    fix=_AUTH_RACE_FIX if races else f"Run scoutctl bootstrap doctor and read the newest log{log_hint}",
                    source="ledger",
                    detail=detail,
                    log=log_path,
                )
            )

    # One finding per slot: a slot runs.recurring names that failed again today is said there.
    today, zone = open_failures_today(rows, vault=vault, now=now)
    recurring_slots = {r.slot for r in _recurring_rows(rows, now=now)}
    folded = [o for o in today if o.slot in recurring_slots]
    recurring = recurring_failures(
        rows, vault=vault, now=now, still_failing=describe_open_failures(folded, zone) if folded else None
    )
    if recurring is not None:
        findings.append(recurring)
        today = [o for o in today if o.slot not in recurring_slots]
    failed_today_ = failed_today_finding(today, rows=rows, vault=vault, zone=zone)
    if failed_today_ is not None:
        findings.append(failed_today_)
    hollow = hollow_ok(rows, vault=vault)
    if hollow is not None:
        findings.append(hollow)

    cutoff = now - LOCK_SKIP_WINDOW
    findings.extend(lease_findings(rows, vault=vault, cutoff=cutoff))
    findings.extend(gates_findings(rows, vault=vault))
    findings.extend(runtime_findings(rows, vault=vault))
    findings.extend(sandbox_findings(rows, vault=vault))
    findings.extend(integrity_findings(rows, vault=vault, now=now))
    findings.extend(broker_findings(rows, vault=vault))
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


def _trailing_lease_unavailable(rows: list[OutcomeRow]) -> list[OutcomeRow]:
    """The newest run-or-lease-skip rows that skipped as lease_unavailable, oldest first.

    Lock, budget and redundancy skips are passed over: they say nothing about
    whether Agnes answers. Any run, or a lease_held skip, ends the streak.
    """
    streak: list[OutcomeRow] = []
    for r in reversed(rows):
        if r.outcome in SKIP_OUTCOMES and r.outcome != LEASE_SKIP:
            continue
        if r.outcome != LEASE_SKIP or r.failure_class != "lease_unavailable":
            break
        streak.append(r)
    return streak[::-1]


def _trailing_gates_skips(rows: list[OutcomeRow]) -> list[OutcomeRow]:
    """The newest gate-probe skips, oldest first, passing over other skips; any run ends the streak."""
    streak: list[OutcomeRow] = []
    for r in reversed(rows):
        if r.outcome in SKIP_OUTCOMES and r.outcome != GATES_SKIP:
            continue
        if r.outcome != GATES_SKIP:
            break
        streak.append(r)
    return streak[::-1]


# Why the gate probe refused to run (failure_class of a gates_unsafe_* skip): (the cause, what to do).
_UNSAFE_ADVICE = {
    "gates_unsafe_endpoint": (
        "a Claude setting names an endpoint the check can't keep on this machine",
        "Run `scoutctl gates probe` to see which setting, and remove or rename it in your Claude settings' env, "
        "or run Scout where the check's network sandbox is available (macOS).",
    ),
    "gates_unsafe_launcher": (
        "the `claude` the runners call is a launcher script (a mise, asdf or similar shim) the check can't see "
        "through, so it can't turn off every provider",
        "Point Scout at the real claude binary: set connectors.inputs.claude_bin in scout-config.yaml to it "
        "(for mise: `mise which claude`), then run /scout-update; or run Scout where the check's network "
        "sandbox is available (macOS).",
    ),
    "": (
        "the check couldn't be kept on this machine",
        "Run `scoutctl gates probe` to see why.",
    ),
}


def gates_findings(rows: list[OutcomeRow], *, vault: Path) -> list[Finding]:
    """Runs skipped because claude didn't load Scout's privacy hooks (spec 2026-11-13 §3.5)."""
    streak = _trailing_gates_skips(rows)
    if not streak:
        return []
    log = _run_log(vault, streak[-1])
    if streak[-1].failure_class.startswith("gates_unsafe"):
        cause, fix = _UNSAFE_ADVICE.get(streak[-1].failure_class, _UNSAFE_ADVICE[""])
        return [
            Finding(
                id="gates.probe_unsafe",
                severity="red",
                since=streak[0].finished_at,
                summary=(
                    f"{last_runs_were_skipped(len(streak))}: Scout couldn't check its privacy hooks "
                    f"safely, because {cause}."
                ),
                fix=f"{fix} Runs resume on their own once `scoutctl gates probe` passes.",
                source="ledger",
                log=str(log) if log else None,
            )
        ]
    return [
        Finding(
            id="gates.not_loaded",
            severity="red",
            since=streak[0].finished_at,
            summary=(
                f"{last_runs_were_skipped(len(streak))}: the claude the runners use didn't load "
                "Scout's privacy hooks (for example in bare mode), so no run may read the vault."
            ),
            fix=(
                "Run `scoutctl gates probe` to see why. Update Scout (a newer plugin may know this Claude Code "
                "version), or pin Claude Code to the last version whose probe passed. Runs resume on their own "
                "once the probe passes."
            ),
            source="ledger",
            log=str(log) if log else None,
        )
    ]


def runtime_findings(rows: list[OutcomeRow], *, vault: Path) -> list[Finding]:
    """Runs skipped because their agent runtime was refused (spec 2026-11-13 §8.3 rule 5: agent_runtime.refused)."""
    streak: list[OutcomeRow] = []
    for r in reversed(rows):
        if r.outcome in SKIP_OUTCOMES and r.outcome != RUNTIME_SKIP:
            continue
        if r.outcome != RUNTIME_SKIP:
            break
        streak.append(r)
    if not streak:
        return []
    streak.reverse()
    log = _run_log(vault, streak[-1])
    return [
        Finding(
            id="agent_runtime.refused",
            severity="red",
            since=streak[0].finished_at,
            summary=(
                f"{last_runs_were_skipped(len(streak))}: Scout refused the agent runtime "
                f"{'it was' if len(streak) == 1 else 'they were'} set to use here (its privacy gates aren't "
                "proven, its provider isn't consented, or the check couldn't run). Scout never falls back to "
                "another runtime on its own."
            ),
            fix=(
                "Run `scoutctl agent-runtime check --slot <slot>` to see why. Fix that, or set the slot back to "
                "claude-code (`agent_runtime:` in .scout-state/schedule.yaml, or SCOUT_AGENT_RUNTIME)."
            ),
            source="ledger",
            log=str(log) if log else None,
        )
    ]


def _trailing_sandbox_skips(rows: list[OutcomeRow]) -> list[OutcomeRow]:
    """The newest write-sandbox skips, oldest first, passing over other skips; any run ends the streak."""
    streak: list[OutcomeRow] = []
    for r in reversed(rows):
        if r.outcome in SKIP_OUTCOMES and r.outcome != SANDBOX_SKIP:
            continue
        if r.outcome != SANDBOX_SKIP:
            break
        streak.append(r)
    return streak[::-1]


# Why the newest run went without the write sandbox: (severity, what happened, what to do).
_UNSANDBOXED = {
    "unavailable": (
        "yellow",
        "there is no OS write sandbox for Scout on this machine yet (macOS only for now), so only the hook gates "
        "kept it from changing what decides whether they run",
        "Nothing to do on Linux until Scout ships its sandbox there. On macOS, check that /usr/bin/sandbox-exec "
        "exists: `scoutctl sandbox status`.",
    ),
    "conflict": (
        "yellow",
        "Claude Code's own Bash sandbox is on in your Claude settings (sandbox.enabled), and Scout's write sandbox "
        "can't run around it, so the run had Claude's sandbox only",
        "Turn off sandbox.enabled in the Claude settings the runs use if you want Scout's write sandbox instead; "
        "`scoutctl sandbox status` says which applies.",
    ),
}
# "off" isn't here: the owner turned it off on purpose, and the doctor says so (bootstrap_doctor._check_run_sandbox).


def sandbox_findings(rows: list[OutcomeRow], *, vault: Path) -> list[Finding]:
    """Runs skipped because the write sandbox couldn't be set up, or run without it (spec 2026-11-15 §8)."""
    streak = _trailing_sandbox_skips(rows)
    if streak:
        log = _run_log(vault, streak[-1])
        return [
            Finding(
                id="sandbox.failed",
                severity="red",
                since=streak[0].finished_at,
                summary=(
                    f"{last_runs_were_skipped(len(streak))}: the write sandbox that keeps a run from "
                    "switching off Scout's gates couldn't be set up on this machine."
                ),
                fix=(
                    "Run `scoutctl sandbox status` to see why; the run log has the same line. Runs resume on their "
                    "own once it says enforced. To run without it meanwhile: `scoutctl sandbox off`."
                ),
                source="ledger",
                log=str(log) if log else None,
            )
        ]
    last = next((r for r in reversed(rows) if r.outcome not in SKIP_OUTCOMES), None)
    if last is not None and last.sandbox == "off" and last.sandbox_source == "env":
        return [
            Finding(
                id="runs.sandbox_env_off",
                severity="note",
                since=last.finished_at,
                summary=(
                    "The last scheduled run ran without Scout's write sandbox: SCOUT_RUN_SANDBOX=off was set in the "
                    "environment the scheduler started it with. That switch is meant for tests and the eval harness."
                ),
                fix=(
                    "Remove SCOUT_RUN_SANDBOX from the environment your scheduled runs start with (the LaunchAgent "
                    "or crontab, or the shell that starts the tick). `scoutctl sandbox off` is the owner's switch."
                ),
                source="ledger",
            )
        ]
    if last is None or last.sandbox not in _UNSANDBOXED:
        return []
    severity, what, fix = _UNSANDBOXED[last.sandbox]
    return [
        Finding(
            id="runs.unsandboxed",
            severity=severity,
            since=last.finished_at,
            summary=f"The last scheduled run ran without Scout's write sandbox: {what}.",
            fix=fix,
            source="ledger",
            log=None,
        )
    ]


def integrity_findings(rows: list[OutcomeRow], *, vault: Path, now: datetime) -> list[Finding]:
    """A run whose protected tracked files the post-run integrity check put back, or couldn't (spec 2026-11-15 §13)."""
    recent = [r for r in rows if r.integrity in ("restored", "unrestored") and r.finished_at >= now - INTEGRITY_WINDOW]
    if recent:
        newest = recent[-1]
        log = _run_log(vault, newest)
        still = newest.integrity == "unrestored"
        return [
            Finding(
                id="run.integrity_restored",
                severity="red",
                since=recent[0].finished_at,
                summary=(
                    f"{count(len(recent), 'scheduled run')} in the last {INTEGRITY_WINDOW.days} days changed a "
                    "protected file (a runner script, agent settings or Scout's schedule) in git. Scout put it "
                    "back in a new commit" + ("; a tag or the stash still holds the change." if still else ".")
                ),
                fix=(
                    "Read that run's log (the `integrity check:` lines name the files and the restoring commit) and "
                    "its session, to see what made it change them."
                    + (" Then delete the tag or drop the stash entry it names." if still else "")
                ),
                source="ledger",
                log=str(log) if log else None,
            )
        ]
    last = next((r for r in reversed(rows) if r.outcome not in SKIP_OUTCOMES), None)
    if last is not None and last.integrity == "error":
        log = _run_log(vault, last)
        return [
            Finding(
                id="run.integrity_unchecked",
                severity="yellow",
                since=last.finished_at,
                summary="The post-run integrity check couldn't run after the last scheduled run.",
                fix="The run log's `integrity check:` line says why; `git status` in the vault shows its state.",
                source="ledger",
                log=str(log) if log else None,
            )
        ]
    return []


def broker_findings(rows: list[OutcomeRow], *, vault: Path) -> list[Finding]:
    """The newest sandboxed run had no broker, or a queued request couldn't be applied (spec 2026-11-16 §6)."""
    from scout.sandbox import broker

    findings: list[Finding] = []
    last = next((r for r in reversed(rows) if r.outcome not in SKIP_OUTCOMES), None)
    status = broker.read_status(vault)
    at = status.get("at") if status is not None else None
    fresh = isinstance(at, int) and last is not None and at >= last.started_at.timestamp() - 60  # this run's own
    if (
        last is not None
        and last.sandbox == "enforced"
        and fresh
        and status is not None
        and status["status"] == "unavailable"
    ):
        findings.append(
            Finding(
                id="runs.broker_unavailable",
                severity="yellow",
                since=last.finished_at,
                summary=(
                    "The last scheduled run ran without its commit and Telegram broker, so its sandbox still let it "
                    "write the vault's git dir and read the Telegram token: "
                    f"{status.get('reason') or 'no reason given'}."
                ),
                fix=(
                    "If the reason names an old git-safe-commit.sh, run /scout-update. Otherwise read the run log's "
                    "`broker:` lines; the next run tries again on its own."
                ),
                source="state",
            )
        )
    problem = broker.queue_problem(vault)
    if problem is not None:
        findings.append(
            Finding(
                id="broker.queue_unsafe",
                severity="red",
                since=None,
                summary=(
                    "The run brokers' queue (.scout-state/broker-queue/) holds something other than plain folders "
                    f"({problem}), so queued commits and Telegram messages aren't applied. Scout doesn't make that."
                ),
                fix=(
                    "Look at .scout-state/broker-queue/ and replace what isn't a plain folder; then read the newest "
                    "run's session to see what made it."
                ),
                source="state",
            )
        )
    failed = broker.failed_entries(vault)
    if failed:
        findings.append(
            Finding(
                id="broker.queue_failed",
                severity="yellow",
                since=None,
                summary=(
                    f"{count(len(failed), 'commit or Telegram request')} a run queued couldn't be applied after the "
                    "session (refused, older than 24 h, or out of retries)."
                ),
                fix=(
                    "Look in .scout-state/broker-queue/failed/: commit the paths yourself or delete the entry. The "
                    "event log's broker.queue_failed rows say why each one failed."
                ),
                source="state",
            )
        )
    return findings


def lease_findings(rows: list[OutcomeRow], *, vault: Path, cutoff: datetime) -> list[Finding]:
    """Runs a linked vault skipped over the Repository Writer Lease (Agnes R2)."""
    findings: list[Finding] = []
    streak = _trailing_lease_unavailable(rows)
    if streak:
        log = _run_log(vault, streak[-1])
        findings.append(
            Finding(
                id="lease.unavailable",
                severity="red" if len(streak) >= LEASE_UNAVAILABLE_RED else "yellow",
                since=streak[0].finished_at,
                summary=(
                    f"{last_runs_were_skipped(len(streak))}: Agnes couldn't grant the repository "
                    "writer lease, so Scout didn't write the vault."
                ),
                fix=(
                    "Check the network and the Agnes login: scoutctl repo lease status. Runs resume on their own "
                    "once Agnes answers; scoutctl repo unlink returns to lease-free runs."
                ),
                source="ledger",
                log=str(log) if log else None,
            )
        )
    held = [r for r in rows if r.outcome == LEASE_SKIP and r.failure_class == "lease_held" and r.finished_at >= cutoff]
    if held:
        slots = ", ".join(sorted({r.slot for r in held}))
        findings.append(
            Finding(
                id="lease.held",
                severity="note",
                since=None,
                summary=(
                    f"{count(len(held), 'run')} skipped in the last 24 h because Agnes held the repository writer "
                    f"lease (an Agnes-hosted run): {slots}."
                ),
                fix="Nothing to fix if Agnes runs that work. To stop the overlap, give each slot one place to run.",
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


def delegate_state_unchecked(env: Mapping[str, str] | None = None) -> bool:
    """Whether the delegate state can't be checked here: a scheduled run (#517's rule) that can't read the state's
    signing key (the run's sandbox denies ~/.scout-secrets). Such a check would report every signed state as
    forged on every run; the owner's terminal, the doctor and the app check it instead."""
    from scout.delegate_mirror import STATE_KEY, secrets_dir
    from scout.kb.reader import is_scheduled

    if not is_scheduled(os.environ if env is None else env):
        return False
    try:
        (secrets_dir() / STATE_KEY).read_bytes()
    except FileNotFoundError:
        return False  # gone, not hidden: that is reported, wherever the check runs
    except OSError:
        return True  # denied (the run's sandbox): this place can't check it
    return False


def delegate_inbox_findings(
    vault: Path, *, now: datetime | None = None, env: Mapping[str, str] | None = None
) -> list[Finding]:
    """Yellow for a delegate inbox whose last pull failed, that no pull has reached for a day, whose copies
    folder holds files no pull took (#550 V4b1), or whose proposals have waited a week for review (V4b2).
    Nothing for a vault with no inbox, nor in a run that can't read the state's key
    (:func:`delegate_state_unchecked`)."""
    if not (vault / ".scout-state" / "delegate-inbox" / "state.json").exists() or delegate_state_unchecked(env):
        return []
    from scout.delegate_inbox_sync import summary
    from scout.repo_sync import RepoSyncError

    try:
        inboxes = summary(vault, now=now)
    except RepoSyncError as e:
        return [
            Finding(
                id="delegate.inbox_state",
                severity="yellow",
                since=None,
                summary=f"The delegate inbox state can't be trusted ({e.code}), so no agent's proposals are taken "
                "or applied",
                fix="Check it from a terminal with: scoutctl bootstrap doctor. Don't move or delete "
                ".scout-state/delegate-inbox/: its signed records are what inbox revert needs, and if the state key "
                "(~/.scout-secrets/delegate-mirrors/state.key) went missing, restore it from a backup",
                source="state",
            )
        ]
    now = now or datetime.now(UTC)
    found = []
    for key, box in inboxes.items():
        if box["last_error"]:
            found.append(
                Finding(
                    id="delegate.inbox_failed",
                    severity="yellow",
                    since=None,
                    summary=f"Delegate inbox {key}: the last pull failed ({box['last_error']}); its agent's "
                    "proposals wait",
                    fix=f"See why with: scoutctl inbox pull {key}",
                    source="state",
                )
            )
            continue
        pulled = _parse_ts(box["last_pull"]) or _parse_ts(box["linked_at"])
        if pulled is not None and now - pulled > timedelta(days=1):
            found.append(
                Finding(
                    id="delegate.inbox_stalled",
                    severity="yellow",
                    since=pulled,
                    summary=f"Delegate inbox {key} hasn't been pulled for over a day; is the schedule tick running?",
                    fix="Pull now with: scoutctl inbox sync (and check scoutctl bootstrap doctor)",
                    source="state",
                )
            )
        reviewed = _parse_ts(box.get("oldest_review"))
        if box.get("review") and reviewed is not None and now - reviewed > timedelta(days=7):
            found.append(
                Finding(
                    id="delegate.inbox_waiting",
                    severity="yellow",
                    since=reviewed,
                    summary=f"Delegate inbox {key}: {count(box['review'], 'proposal')} {have(box['review'])} "
                    "waited over a week for your review (observations and comments)",
                    fix="Look at them with: scoutctl inbox review",
                    source="state",
                )
            )
        if box["untaken"]:
            found.append(
                Finding(
                    id="delegate.inbox_untaken",
                    severity="yellow",
                    since=None,
                    summary=f"Delegate inbox {key}: {count(box['untaken'], 'file')} in .scout-inbox/agents/{key}/ "
                    f"{were(box['untaken'])} never taken by a pull (something else put them there); they are ignored",
                    fix=f"Look at them, then delete them: .scout-inbox/agents/{key}/",
                    source="state",
                )
            )
    return found


def delegate_findings(
    vault: Path, *, now: datetime | None = None, env: Mapping[str, str] | None = None
) -> list[Finding]:
    """Yellow for a delegate mirror whose last sync failed, or that is behind and no sync has tried for an hour
    (#550 V2c): its agent reads an older copy until one works.

    Nothing for a vault that links no delegate. A failed sync is never a run failure;
    this is where it shows. "No sync tried" covers what records nothing itself: a tick
    that stopped, a sync that couldn't start, or one that died before it began.
    """
    if not (vault / ".scout-state" / "delegate-mirrors.json").exists() or delegate_state_unchecked(env):
        return []  # nothing to check, or a run that can't read the signing key: checked outside runs
    from scout.delegate_mirror import load_links
    from scout.repo_sync import RepoSyncError

    try:
        links = load_links(vault)
    except RepoSyncError as e:
        if e.code == "state_unverified":
            return [
                Finding(
                    id="delegate.state_unverified",
                    severity="yellow",
                    since=None,
                    summary="The delegate mirror state's signature doesn't match (it wasn't written by Scout on "
                    "this computer, or the signing key is gone), so no agent's mirror syncs",
                    fix="Check each link and sign it again with: scoutctl delegate trust-state",
                    source="state",
                )
            ]
        return [
            Finding(
                id="delegate.state_unreadable",
                severity="yellow",
                since=None,
                summary="The delegate mirror state (.scout-state/delegate-mirrors.json) can't be read, or wasn't "
                "written by Scout on this computer, so no agent's mirror syncs",
                fix="Fix or delete that file, then link each delegate again with: scoutctl delegate link",
                source="config",
            )
        ]
    from scout.delegate_mirror import needs_sync

    now = now or datetime.now(UTC)
    behind = set(needs_sync(vault, {k: v for k, v in links.items() if v.last_error is None}, now=now))
    found = []
    for key, link in sorted(links.items()):
        if link.last_error is not None:
            code = link.last_error.get("code", "unknown")
            found.append(
                Finding(
                    id="delegate.sync_failed",
                    severity="yellow",
                    since=_parse_ts(link.last_error.get("first_at") or link.last_error.get("at")),
                    summary=f"Delegate mirror {key} failed to sync ({code}); its agent still reads the copy from "
                    f"{link.last_sync or 'when it was linked'}",
                    fix=f"Retry now with: scoutctl delegate sync {key} (a manual sync skips the backoff)",
                    source="state",
                )
            )
            continue
        tried = _parse_ts(link.last_attempt) or _parse_ts(link.last_sync) or _parse_ts(link.linked_at)
        if key in behind and (tried is None or now - tried > timedelta(hours=1)):
            found.append(
                Finding(
                    id="delegate.sync_stalled",
                    severity="yellow",
                    since=tried,
                    summary=f"Delegate mirror {key} is behind the vault and no sync has tried for over an hour; "
                    "is the schedule tick running?",
                    fix="Sync now with: scoutctl delegate sync --all (and check scoutctl bootstrap doctor)",
                    source="state",
                )
            )
    return found


def config_findings(vault: Path) -> list[Finding]:
    """Red while scout-config.yaml doesn't load: runs fall back to the defaults, and their commits may be refused."""
    problem = config_load_problem(vault)
    if problem is None:
        return []
    return [
        Finding(
            id="config.unreadable",
            severity="red",
            since=None,
            summary=f"scout-config.yaml doesn't load ({problem}), so runs use the default settings "
            "and their commits may be refused",
            fix="Fix that line in scout-config.yaml, then check it with: scoutctl bootstrap doctor",
            source="config",
        )
    ]


def schedule_findings(
    vault: Path, rows: list[OutcomeRow], *, now: datetime
) -> tuple[list[Finding], tuple[SlotStatus, ...]]:
    """Slot, fire and scheduler-liveness findings, plus per-slot status."""
    return _schedule_findings(vault, _load_schedule(vault), rows, read_last_fires(vault), now=now)


def _live_session_slot(vault: Path, fires: dict[str, datetime]) -> str | None:
    """The slot of the session holding the vault lock, or None if none is live.

    Every scheduled run reads run health while it holds the lock (#467), so the
    lock alone can't defer every slot. The tick stamps a fire at dispatch,
    before its runner takes the lock, and a slot dispatched while the lock is
    held records a skip row. So the holder is the slot with the newest fire at
    or before the lock's mtime.
    """
    lock = vault / ".scout-logs" / ".scout-session.lock"
    if not is_lock_held_by_live_pid(lock):
        return None
    try:
        taken = datetime.fromtimestamp(lock.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    before = {k: t for k, t in fires.items() if t <= taken + FIRE_MATCH_SLACK}
    return max(before, key=lambda k: before[k]) if before else None


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
    live_slot = _live_session_slot(vault, fires)
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
            key != live_slot
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


# A runner log's name: ``<stem>-YYYY-MM-DD_HH-MM.log`` for the run that claimed
# the minute (and every log from before unique names), or
# ``<stem>-YYYY-MM-DD_HH-MM-SS-PID.log`` for a later run in the same minute.
_RUN_LOG_TAIL = r"-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})(?:-(\d{2})-(\d+))?\.log"
_ANY_RUN_LOG_RE = re.compile(r"[a-z]+" + _RUN_LOG_TAIL)


def run_log_pattern(stem: str) -> re.Pattern[str]:
    """Matches a runner's log names, in both forms, for runner stem ``stem``."""
    return re.compile(re.escape(stem) + _RUN_LOG_TAIL)


def run_log_sort_key(name: str) -> tuple[str, int, int, str]:
    """Orders runner log names by start: minute, then second, then PID.

    Plain text order puts ``…_09-03-25-4242.log`` before ``…_09-03.log`` and a
    short PID after a long one. The minute-named log of a minute sorts first:
    the run that claimed it was that minute's first. Names in neither form sort
    before all others, by text.
    """
    m = _ANY_RUN_LOG_RE.fullmatch(name)
    if m is None:
        return ("", -1, -1, name)
    return (m.group(1), int(m.group(2) or 0), int(m.group(3) or 0), name)


def newest_log_outcome(vault: Path, runner: str | None, ledger_logs: list[str], *, shared: bool) -> str | None:
    """Why a lane's newest run produced nothing, read from its runner log.

    The logs the ledger recorded for the lane are always its own. A runner's
    other logs (``<stem>-YYYY-MM-DD_HH-MM.log`` or, for a later run in the same
    minute, ``<stem>-YYYY-MM-DD_HH-MM-SS-PID.log``; including runs that stopped
    before the ledger step) count only when no other lane uses the same runner:
    run-scout.sh serves both briefing and consolidation and its log names do not
    say which one ran.
    """
    log_dir = vault / ".scout-logs"
    names = {name for name in ledger_logs if (log_dir / name).is_file()}
    m = re.fullmatch(r"run-(.+)\.sh", runner or "")
    if m and not shared:
        pattern = run_log_pattern(m.group(1))
        names |= {p.name for p in log_dir.glob("*.log") if pattern.fullmatch(p.name)}
    if not names:
        return None
    name = max(names, key=run_log_sort_key)
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


def engine_findings(*, home: Path | None) -> list[Finding]:
    """A dev-managed engine checkout that scheduled runs would execute unreviewed.

    A dev engine is an editable install, so its working tree is what the next scheduled run
    executes, and a checkout off main or a release tag blocks the upgrade. Scheduled runs once
    branched and edited that checkout directly; engine edits belong in a clone under
    ~/.scout-worktrees/ (phases/core/git-setup.md). Other managers own their engine trees.
    """
    from scout.scripts.engine_pointer import read_pointer

    pointer = read_pointer(home=home or Path.home())
    if pointer is None or pointer.managed_by != "dev":
        return []
    st = checkout_state(Path(pointer.engine_root))
    if st is None:
        return []
    found = []
    if st.changes:
        named = st.changes[0] if len(st.changes) == 1 else f"e.g. {st.changes[0]}"
        found.append(
            Finding(
                id="engine.dirty",
                severity="yellow",
                since=None,
                summary=(
                    f"The engine checkout scheduled runs use ({st.root}) has {len(st.changes)} uncommitted "
                    f"{noun(len(st.changes), 'change')} ({named}): they run in every scheduled run, unreviewed, "
                    "and block upgrades."
                ),
                fix=(
                    f"Save the work as a branch or PR from a clone under {WORKTREES_HINT}, then in Terminal: "
                    f"git -C {st.root} status (and restore each file)"
                ),
                source="git",
            )
        )
    if st.off_release:
        where = f"branch {st.branch}" if st.branch else "a detached HEAD"
        found.append(
            Finding(
                id="engine.off_release",
                severity="yellow",
                since=None,
                summary=(
                    f"The engine checkout scheduled runs use ({st.root}) is on {where}, not main or a release "
                    f"tag, so upgrades can't move it."
                ),
                fix=(
                    f"Push any work on it from a clone under {WORKTREES_HINT}, then in Terminal: "
                    f"git -C {st.root} switch main"
                ),
                source="git",
            )
        )
    return found


def progress_findings(vault: Path, *, now: datetime, env: Mapping[str, str] | None = None) -> list[Finding]:
    """``session.no_progress``: the live lock holder's watcher saw no progress past the reaper's ceiling.

    The same line and the same ceiling (``SCOUT_LOCK_MAX_AGE_SECS``, awake time)
    the lock reaper reads (``scout.run_progress``), so health calls a session
    stuck exactly when the next run would reap it, and a run whose log only the
    lease keepalive writes is not mistaken for one that works. Nothing without
    a fresh line: no watcher (an older runner), a dead one, or no live holder.

    The ceiling is ``SCOUT_LOCK_MAX_AGE_SECS`` from *this* process's environment
    (default 7200 s). The reaper reads it from the runner's, i.e. the launchd
    job's: set it in both places, or health and the reaper disagree on when.
    """
    from scout import run_progress

    lock = vault / ".scout-logs" / run_progress.LOCK_NAME
    beat = run_progress.read_fresh(run_progress.heartbeat_path(vault), now=now.timestamp())
    if beat is None or not is_lock_held_by_live_pid(lock) or run_progress.lock_pid(lock) != beat.runner_pid:
        return []
    ceiling = run_progress.env_secs(
        run_progress.MAX_AGE_SECS_ENV,
        run_progress.DEFAULT_MAX_AGE_SECS,
        os.environ if env is None else env,
        allow_zero=True,
    )
    if beat.stalled_s <= ceiling:
        return []
    return [
        Finding(
            id="session.no_progress",
            severity="yellow",
            since=datetime.fromtimestamp(beat.progress_at, UTC),
            summary=(
                f"The run holding the vault lock (PID {beat.runner_pid}) has made no progress for "
                f"{beat.stalled_s // 60} min of awake time: its agent burns no CPU and starts no tools."
            ),
            fix=(
                "The next scheduled run reaps it. To end it now, stop that PID's process group; its log is " + beat.log
            ),
            source="state",
            log=beat.log,
        )
    ]


def evaluate(vault: Path, *, now: datetime | None = None, home: Path | None = None) -> RunHealth:
    """``home`` locates the stored Claude token and the engine pointer (default: the real home)."""
    now = now or datetime.now(UTC)
    rows = with_log_reset(read_outcomes(vault), vault)
    found = token_findings(ledger_findings(rows, vault=vault, now=now), vault=vault, home=home, now=now)
    found += progress_findings(vault, now=now)
    loaded = _load_schedule(vault)
    fires = read_last_fires(vault)
    sched, slots = _schedule_findings(vault, loaded, rows, fires, now=now)
    pause = usage_pause(rows, now=now)
    lanes = lane_findings(
        vault, loaded[0] if loaded else None, rows, fires, now=now, paused_since=pause.began if pause else None
    )
    updates = update_findings(vault, read_updates(vault), now=now)
    engine = engine_findings(home=home)
    config = config_findings(vault)
    delegates = delegate_findings(vault) + delegate_inbox_findings(vault)
    return build_report(
        found + sched + lanes + updates + engine + config + delegates,
        now=now,
        last_ok_at=_last_ok_at(rows),
        slots=slots,
    )

"""Heartbeat decision — fires every 30 min via launchd to maybe launch a session.

Port of ``~/Scout/scripts/heartbeat.sh`` (#74 + #79). The bash version ran
three separate ``python3 -c`` invocations against ``usage-tracker.jsonl``
just to extract minutes-since-last-session, hours-since-dreaming, and
hours-since-research — paying ~150–300 ms of cold start each, every
30 minutes, forever. Folded here into one walk of the tracker that emits
all three derived values plus the pgrep / git-status side checks.

Gating order (preserved from bash):
  1. Another ``claude .*scout-`` process already running → skip
  2. Off-peak detection from ``scout-config.yaml`` (used by gate 5)
  3. Budget check (delegates to ``scoutctl budget check`` so its tracker
     parse is shared with #87's optimization)
  4. Minimum gap since last session (default 120 min)
  5. Off-peak conservatism (skip if off-peak AND <240 min since last)
  6. Work signals — fire only when:
       * >=4 h since last dreaming run (``signal_hours``) OR
       * uncommitted changes in the vault git repo, outside Scout's own
         machine-state dirs (``dirty_tree``; ``heartbeat.dirty_tree_triggers``)
  7. Pick runner:
       * research, if >=24 h since last research AND research-queue has
         an open item AND ``run-research.sh`` exists
       * otherwise dreaming
  8. Launch the chosen runner detached, log the PID

Every threshold above is set in ``scout-config.yaml`` (#231): the gaps, signal
hours and dirty-tree toggle under ``heartbeat:``, the window under ``off_peak:``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout import paths
from scout.scripts._config_scan import scan_overrides

# Defaults mirror heartbeat.sh constants so behavior is preserved when no
# scout-config.yaml is present.
DEFAULT_OFF_PEAK_START = 23
DEFAULT_OFF_PEAK_END = 6
DEFAULT_MIN_GAP_MINUTES = 120
DEFAULT_OFF_PEAK_MIN_GAP_MINUTES = 240
DEFAULT_DREAMING_SIGNAL_HOURS = 4
DEFAULT_RESEARCH_MIN_GAP_HOURS = 24

EXIT_LAUNCHED = 0
EXIT_SKIPPED = 0  # bash returns 0 on intentional skip too
EXIT_ERROR = 1

_TRACKER_FILENAME = "usage-tracker.jsonl"
_RESEARCH_QUEUE_REL = "knowledge-base/research-queue.md"
_RESEARCH_QUEUE_DIR_REL = "knowledge-base/research-queue"

# Every knob, with its inclusive range. Hours of the day go to 24 so an
# "always off-peak" window (start 0, end 24) stays expressible.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "off_peak_start": (0, 24),
    "off_peak_end": (0, 24),
    "min_gap_minutes": (0, 10080),
    "off_peak_min_gap_minutes": (0, 10080),
    "dreaming_signal_hours": (1, 720),
    "research_min_gap_hours": (0, 720),
}
_BOOL_FIELDS = frozenset({"dirty_tree_triggers"})

# The keys of the nested ``heartbeat:`` block, in documentation order.
HEARTBEAT_KEYS = (
    "min_gap_minutes",
    "off_peak_min_gap_minutes",
    "dreaming_signal_hours",
    "research_min_gap_hours",
    "dirty_tree_triggers",
)

# Flat spellings — back-compat with hand-made override files.
_CONFIG_KEYS = {
    "off_peak_start": "off_peak_start",
    "off_peak_end": "off_peak_end",
    **{key: key for key in HEARTBEAT_KEYS},
}

# The nested shapes:
#   off_peak:            heartbeat:
#     start: 23            dreaming_signal_hours: 4
#     end: 6               ...
_NESTED_CONFIG_KEYS = {
    ("off_peak", "start"): "off_peak_start",
    ("off_peak", "end"): "off_peak_end",
    **{("heartbeat", key): key for key in HEARTBEAT_KEYS},
}

_TRUE = frozenset({"true", "yes", "on"})
_FALSE = frozenset({"false", "no", "off"})

# A top-level ``heartbeat:`` line that carries a value (a flow mapping).
_INLINE_HEARTBEAT_RE = re.compile(r"^heartbeat\s*:\s*[^#\s]")
_KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:")


# ----- config -------------------------------------------------------------


@dataclass(frozen=True)
class HeartbeatConfig:
    off_peak_start: int = DEFAULT_OFF_PEAK_START
    off_peak_end: int = DEFAULT_OFF_PEAK_END
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES
    off_peak_min_gap_minutes: int = DEFAULT_OFF_PEAK_MIN_GAP_MINUTES
    dreaming_signal_hours: int = DEFAULT_DREAMING_SIGNAL_HOURS
    research_min_gap_hours: int = DEFAULT_RESEARCH_MIN_GAP_HOURS
    dirty_tree_triggers: bool = True


def load_config(config_path: Path) -> HeartbeatConfig:
    """The heartbeat's knobs from scout-config.yaml; see :func:`load_config_with_issues`."""
    return load_config_with_issues(config_path)[0]


def _heartbeat_block_keys(text: str) -> list[str]:
    """The keys written directly under the top-level ``heartbeat:`` block."""
    keys: list[str] = []
    in_block = False
    child_indent: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_LINE_RE.match(line)
        if not line[0].isspace():
            in_block = m is not None and m.group(2) == "heartbeat"
            child_indent = None
            continue
        if not in_block or m is None:
            continue
        if child_indent is None:
            child_indent = m.group(1)
        if m.group(1) == child_indent:
            keys.append(m.group(2))
    return keys


def _parse(field_name: str, raw: str) -> tuple[int | bool | None, str]:
    """``(value, "")`` for a good raw value, ``(None, why)`` for a bad one."""
    if field_name in _BOOL_FIELDS:
        word = raw.lower()
        if word in _TRUE or word in _FALSE:
            return word in _TRUE, ""
        return None, "is not true or false"
    try:
        value = int(raw)
    except ValueError:
        return None, "is not a whole number"
    low, high = _INT_RANGES[field_name]
    if not low <= value <= high:
        return None, f"is out of range ({low}–{high})"
    return value, ""


def load_config_with_issues(config_path: Path) -> tuple[HeartbeatConfig, list[str]]:
    """Parse the heartbeat's knobs from scout-config.yaml, with what was wrong.

    Missing file → defaults, no issues. A bad value keeps that knob's default
    and yields one issue; so does an unknown key under ``heartbeat:`` (#231:
    keys there used to be dropped without a word). The heartbeat logs the
    issues and the doctor shows them as warnings, so a tick never fails over a
    hand edit. The scan is the shared two-level reader (no pyyaml on this hot
    path): both the nested ``heartbeat:``/``off_peak:`` shapes and the flat
    top-level spellings, the nested one winning when both are set.
    """
    if not config_path.exists():
        return HeartbeatConfig(), []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return HeartbeatConfig(), []

    labels = {field: key for key, field in _CONFIG_KEYS.items()}
    labels.update({field: f"{section}.{key}" for (section, key), field in _NESTED_CONFIG_KEYS.items()})
    flat = scan_overrides(text, flat_keys=_CONFIG_KEYS)
    nested = scan_overrides(text, nested_keys=_NESTED_CONFIG_KEYS)

    overrides: dict[str, int | bool] = {}
    issues: list[str] = []
    for field_name, raw in {**flat, **nested}.items():
        label = labels[field_name] if field_name in nested else field_name
        value, why = _parse(field_name, raw)
        if value is None:
            default = getattr(HeartbeatConfig(), field_name)
            issues.append(f"scout-config.yaml: {label}: {raw} {why}, using the default {str(default).lower()}")
        else:
            overrides[field_name] = value

    known = ", ".join(HEARTBEAT_KEYS)
    for key in _heartbeat_block_keys(text):
        if key not in HEARTBEAT_KEYS:
            issues.append(f"scout-config.yaml: heartbeat.{key}: unknown key, ignored (known: {known})")
    if any(_INLINE_HEARTBEAT_RE.match(line) for line in text.splitlines()):
        issues.append(
            "scout-config.yaml: heartbeat: write one key per line under `heartbeat:` "
            "(an inline {…} mapping is not read), using the defaults"
        )
    return HeartbeatConfig(**overrides), issues  # type: ignore[arg-type]


# ----- state collection ---------------------------------------------------


@dataclass(frozen=True)
class TrackerStats:
    """Derived recency stats from one walk of usage-tracker.jsonl."""

    minutes_since_last_session: int
    hours_since_dreaming: int
    hours_since_research: int

    @classmethod
    def empty(cls) -> TrackerStats:
        return cls(
            minutes_since_last_session=9999,
            hours_since_dreaming=99,
            hours_since_research=999,
        )


def read_tracker_stats(tracker_path: Path, *, now: datetime | None = None) -> TrackerStats:
    """Compute all three "time since" stats from a single pass of the tracker.

    Replaces the three separate ``python3 -c`` calls in heartbeat.sh — each of
    which opened the same JSONL and iterated every line. Malformed rows are
    silently skipped, same as bash.
    """
    if not tracker_path.exists():
        return TrackerStats.empty()
    n = now or datetime.now(UTC)
    last_any: datetime | None = None
    last_dreaming: datetime | None = None
    last_research: datetime | None = None
    try:
        with tracker_path.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts_str = row.get("ts")
                if not isinstance(ts_str, str):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if last_any is None or ts > last_any:
                    last_any = ts
                row_type = row.get("type")
                if row_type == "dreaming" and (last_dreaming is None or ts > last_dreaming):
                    last_dreaming = ts
                elif row_type == "research" and (last_research is None or ts > last_research):
                    last_research = ts
    except OSError:
        return TrackerStats.empty()
    return TrackerStats(
        minutes_since_last_session=(int((n - last_any).total_seconds() / 60) if last_any else 9999),
        hours_since_dreaming=(int((n - last_dreaming).total_seconds() / 3600) if last_dreaming else 99),
        hours_since_research=(int((n - last_research).total_seconds() / 3600) if last_research else 999),
    )


def scout_session_running(pgrep_pattern: str = "claude.*scout-") -> bool:
    """True iff a ``claude .*scout-`` process is currently alive.

    Wrapped so tests can monkey-patch the subprocess away.
    """
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pgrep_pattern],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() != ""


# Scout's own machine state: a runner or schedule tick rewriting these is not
# the user's pending work (#231 — an untracked .scout-state/last-fire.json once
# kept the tree dirty around the clock, so the 4-hour gate never gated).
_MACHINE_STATE_DIRS = (".scout-state", ".scout-logs", ".scout-cache")


def vault_has_uncommitted_changes(vault: Path) -> bool:
    """True iff ``git status --porcelain`` in *vault* shows modifications
    outside Scout's machine-state dirs."""
    if not (vault / ".git").is_dir():
        return False
    excludes = [f":(exclude){d}" for d in _MACHINE_STATE_DIRS]
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain", "--", ".", *excludes],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() != ""


def research_queue_has_unchecked(queue_path: Path) -> bool:
    """True iff *queue_path* exists and contains at least one ``- [ ]`` line."""
    if not queue_path.exists():
        return False
    try:
        with queue_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("- [ ]"):
                    return True
    except OSError:
        return False
    return False


def _item_status(path: Path) -> str | None:
    """Read the frontmatter ``status:`` of a per-file queue item (lowercased), or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    # scan the leading frontmatter block (between the first two '---' fences)
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"\s*status\s*:\s*(\S+)", line)
        if m:
            return m.group(1).strip().lower()
    else:
        return None  # no closing fence → malformed frontmatter, treat as no status
    return None  # closing fence found, no status key


def research_queue_has_open(vault: Path) -> bool:
    """True iff the research queue has at least one open item.

    Current per-file format: any ``knowledge-base/research-queue/*.md`` with
    frontmatter ``status: open`` or ``in-progress``. Falls back to the legacy
    single-file ``research-queue.md`` (``- [ ]`` lines) for pre-migration vaults.
    """
    queue_dir = vault / _RESEARCH_QUEUE_DIR_REL
    if queue_dir.is_dir():
        try:
            items = sorted(queue_dir.glob("*.md"))
        except OSError:
            return False
        return any(_item_status(i) in ("open", "in-progress") for i in items)
    # Legacy single-file fallback (pre-migration vaults only):
    return research_queue_has_unchecked(vault / _RESEARCH_QUEUE_REL)


def run_budget_check(scoutctl_bin: str | None = None) -> int:
    """Invoke ``scoutctl budget check`` and return its exit code.

    Delegates so the budget check's tracker parse (#87) is shared. If scoutctl
    can't be found at all, returns 0 (don't gate on a missing optimization).
    """
    if scoutctl_bin is None:
        scoutctl_bin = os.environ.get("SCOUTCTL_BIN", "scoutctl")
    try:
        return subprocess.run(
            [scoutctl_bin, "budget", "check"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 0


# ----- decision -----------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    action: str  # "launch" | "skip"
    runner: Path | None = None
    session_type: str | None = None
    reason: str = ""


def in_off_peak(hour: int, config: HeartbeatConfig) -> bool:
    """Spans midnight: True if hour is at or after off_peak_start OR before off_peak_end."""
    return hour >= config.off_peak_start or hour < config.off_peak_end


def decide(
    *,
    stats: TrackerStats,
    config: HeartbeatConfig,
    now_hour: int,
    budget_ok: bool,
    session_already_running: bool,
    uncommitted_vault_changes: bool,
    research_queue_open: bool,
    research_runner: Path,
    dreaming_runner: Path,
) -> Decision:
    """Pure decision function — given collected state, pick launch / skip.

    Gates run in the same order as heartbeat.sh; the first one that fails
    short-circuits with a reason string the caller can write to the
    heartbeat log.
    """
    if session_already_running:
        return Decision("skip", reason="session_already_running")

    if not budget_ok:
        return Decision("skip", reason="budget_exhausted")

    if stats.minutes_since_last_session < config.min_gap_minutes:
        return Decision(
            "skip",
            reason=(f"last_session_{stats.minutes_since_last_session}m_ago_need_{config.min_gap_minutes}m"),
        )

    off_peak = in_off_peak(now_hour, config)
    if off_peak and stats.minutes_since_last_session < config.off_peak_min_gap_minutes:
        return Decision(
            "skip",
            reason=(
                f"off_peak_conservatism_{stats.minutes_since_last_session}m_ago_need_{config.off_peak_min_gap_minutes}m"
            ),
        )

    # Name the operand(s) that fired, so the log tells "the timer said so"
    # from "the tree was dirty" (#231).
    operands = []
    if stats.hours_since_dreaming >= config.dreaming_signal_hours:
        operands.append("signal_hours")
    if config.dirty_tree_triggers and uncommitted_vault_changes:
        operands.append("dirty_tree")
    if not operands:
        return Decision("skip", reason="no_pending_work_signals")
    trigger = f", trigger={'+'.join(operands)}"

    pick_research = (
        stats.hours_since_research >= config.research_min_gap_hours
        and research_queue_open
        and research_runner.is_file()
        and os.access(research_runner, os.X_OK)
    )
    if pick_research:
        return Decision(
            "launch",
            runner=research_runner,
            session_type="research",
            reason=(
                f"research_{stats.hours_since_research}h_since_last_dreaming_{stats.hours_since_dreaming}h_since_last"
                f"{trigger}"
            ),
        )

    if dreaming_runner.is_file() and os.access(dreaming_runner, os.X_OK):
        return Decision(
            "launch",
            runner=dreaming_runner,
            session_type="dreaming",
            reason=f"dreaming_{stats.hours_since_dreaming}h_since_last{trigger}",
        )

    return Decision("skip", reason="no_runner_executable")


# ----- launch -------------------------------------------------------------


def launch_runner(runner: Path, *, vault: Path, log_path: Path) -> int:
    """Spawn *runner* fully detached, appending stdout/stderr to *log_path*.

    Mirrors ``nohup "$RUNNER" >> "$HEARTBEAT_LOG" 2>&1 &`` plus the
    schedule_tick.py runner pattern (start_new_session + DEVNULL stdin).
    ``SCOUT_TRIGGER=heartbeat`` lets the run ledger count heartbeat launches
    apart from scheduled ones (run-health spec §5.3). Returns the child's PID.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [str(runner)],
            cwd=str(vault),
            env={**os.environ, "SCOUT_TRIGGER": "heartbeat"},
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )
    return proc.pid


# ----- driver -------------------------------------------------------------


def _log_line(log_path: Path, reason: str, tz_name: str | None = None) -> None:
    """Append a timestamped reason line to the heartbeat log.

    ``tz_name=None`` resolves the configured zone (#207). Imported lazily so
    pyyaml stays off this module's import path (see module docstring on cold
    starts) and is only paid when a line is actually logged.
    """
    try:
        from scout.config import resolve_timezone, timezone_or_default

        zone = timezone_or_default(tz_name) if tz_name else resolve_timezone()
        ts = datetime.now(tz=zone)
        stamp = ts.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        stamp = datetime.now().isoformat(timespec="minutes")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {reason}\n")
    except OSError:
        pass


def run(
    *,
    dry_run: bool = False,
    data_dir: Path | None = None,
    scoutctl_bin: str | None = None,
    now: datetime | None = None,
) -> int:
    """Execute one heartbeat tick. Returns process exit code.

    Always returns 0 on intentional skip — the launchd plist treats non-zero
    as a configuration error, and skipping is normal/expected behavior.
    """
    target = data_dir or paths.data_dir()
    tracker_path = paths.logs_dir(target) / _TRACKER_FILENAME
    # Through paths.config_path — heartbeat's own hardcoded dotted filename
    # was the file-that-nothing-writes half of #207 (comment thread).
    config_path = paths.config_path(target)
    log_path = paths.logs_dir(target) / "heartbeat.log"

    # Resolve the vault's configured zone once for every log stamp this tick
    # (lazy import — keeps pyyaml off the module import path; #207).
    from scout.config import resolve_timezone as _resolve_tz

    zone = _resolve_tz(target)
    log_tz = zone.key

    config, issues = load_config_with_issues(config_path)
    for issue in issues:
        _log_line(log_path, f"config: {issue}", tz_name=log_tz)
    n = now or datetime.now(UTC)
    stats = read_tracker_stats(tracker_path, now=n)

    research_runner = target / "run-research.sh"
    dreaming_runner = target / "run-dreaming.sh"

    decision = decide(
        stats=stats,
        config=config,
        # The off-peak window is wall-clock hours in the vault's zone, the
        # same zone the log stamps use (the host's unless one is configured).
        now_hour=n.astimezone(zone).hour,
        budget_ok=run_budget_check(scoutctl_bin) == 0,
        session_already_running=scout_session_running(),
        # Skip the git probe entirely when its answer cannot trigger a launch.
        uncommitted_vault_changes=config.dirty_tree_triggers and vault_has_uncommitted_changes(target),
        research_queue_open=research_queue_has_open(target),
        research_runner=research_runner,
        dreaming_runner=dreaming_runner,
    )

    if decision.action == "skip":
        _log_line(log_path, f"skipped: {decision.reason}", tz_name=log_tz)
        return EXIT_SKIPPED

    runner = decision.runner
    assert runner is not None  # "launch" always has a runner
    if dry_run:
        _log_line(log_path, f"dry_run: would launch {decision.session_type} ({decision.reason})", tz_name=log_tz)
        print(f"would_launch {runner}")
        return EXIT_LAUNCHED

    try:
        pid = launch_runner(runner, vault=target, log_path=log_path)
    except OSError as exc:
        _log_line(log_path, f"launch_failed: {exc}", tz_name=log_tz)
        return EXIT_ERROR
    _log_line(log_path, f"launched {decision.session_type} PID={pid} ({decision.reason})", tz_name=log_tz)
    return EXIT_LAUNCHED


def main(*, dry_run: bool = False) -> int:
    try:
        return run(dry_run=dry_run)
    except Exception:
        return EXIT_ERROR


__all__ = [
    "DEFAULT_DREAMING_SIGNAL_HOURS",
    "DEFAULT_MIN_GAP_MINUTES",
    "DEFAULT_OFF_PEAK_END",
    "DEFAULT_OFF_PEAK_MIN_GAP_MINUTES",
    "DEFAULT_OFF_PEAK_START",
    "DEFAULT_RESEARCH_MIN_GAP_HOURS",
    "Decision",
    "HEARTBEAT_KEYS",
    "HeartbeatConfig",
    "TrackerStats",
    "decide",
    "in_off_peak",
    "launch_runner",
    "load_config",
    "load_config_with_issues",
    "main",
    "read_tracker_stats",
    "research_queue_has_open",
    "research_queue_has_unchecked",
    "run",
    "run_budget_check",
    "scout_session_running",
    "vault_has_uncommitted_changes",
]


# Iterable re-export kept for forward-compat — module-level imports already
# pulled it in via collections.abc.
_ = Iterable

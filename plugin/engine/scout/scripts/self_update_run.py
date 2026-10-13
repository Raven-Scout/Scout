"""``scoutctl self-update run`` — headless engine updates (run-health spec §7, #360).

For engines Scout.app doesn't manage (``install.sh``, ``claude-code``). The
heartbeat starts this detached at most once a day. Each attempt that gets past
the scheduled gate appends one row to ``.scout-logs/update-outcomes.jsonl``,
and ``health.update_findings`` turns that ledger into the ``update.*`` findings.

The order of operations is the safety argument:

1. **Eligibility.** ``managed_by: scout-app`` is refused before anything else
   is read, fetched or run. The app's own updater is the only path for it.
   ``dev`` and ``unknown`` are never updated (D7); they get a read-only check
   so ``update.behind`` can say they're behind.
2. **check / pre-flight / fetch / venv** run without the vault lock and touch
   neither the vault nor the live engine. A failure here leaves the old engine
   live and pointed to (D10, rollback).
3. **vault** is the new engine's ``bootstrap auto``. It takes the vault lock
   itself (D9) and re-points the jobs, shim and pointer. From here on a failure
   rolls forward: it's recorded as ``failed`` and pushed, never undone.

Every side effect goes through :class:`Deps`, so tests run the whole flow with
fakes and nothing here touches the real install.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout.scripts.engine_pointer import EnginePointer
from scout.scripts.self_update import ReleaseStatus, UpdateStatus, _semver_tuple, release_status
from scout.update_outcomes import UpdateOutcome
from scout.update_outcomes import append as append_outcome
from scout.update_outcomes import read as read_outcomes

PLUGIN_ID = "scout@scout-plugin"
MARKETPLACE = "scout-plugin"
UPDATABLE = ("install.sh", "claude-code")
DEFAULT_SOAK_HOURS = 24.0
CHECK_FAILURES_BEFORE_FAILED = 3
STALE_CHECK = timedelta(hours=36)
APPLY_SPACING = timedelta(hours=24)
RETRY_AFTER_FAILURE = timedelta(hours=24)
MAX_FAILURES_PER_TARGET = 3
SLOT_LOOKAHEAD = timedelta(minutes=45)
NETWORK_PREFIX = "network: "
_LOCK_BUSY_MARK = "held by live PID"
_STEP_TIMEOUT_S = 900
_DETAIL_MAX = 300


@dataclass(frozen=True)
class Proc:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], "dict[str, str] | None"], Proc]


@dataclass
class Deps:
    """Every side effect of an update attempt. ``default_deps`` wires the real ones."""

    vault: Path
    now: Callable[[], datetime]
    read_pointer: Callable[[], EnginePointer | None]
    interpreter: str
    load_config: Callable[[], dict[str, Any]]
    check: Callable[[], UpdateStatus]
    fetch_release: Callable[[str], dict | None]
    run: Runner
    lock_busy: Callable[[], bool]
    slot_due_within: Callable[[timedelta], bool]
    in_off_peak: Callable[[], bool]
    doctor_severity: Callable[[], str]
    blocking_sidecars: Callable[[], list[str]]
    claude_bin: Callable[[], str]
    notify: Callable[[UpdateOutcome, list[UpdateOutcome]], None] = lambda row, prior: None
    log_name: str | None = None
    say: Callable[[str], None] = field(default=lambda msg: None)


@dataclass(frozen=True)
class Attempt:
    """What one invocation did. ``row`` is None when the scheduled gate said "not now"."""

    row: UpdateOutcome | None
    reason: str = ""
    # True when the heartbeat's once-a-day marker may be written: the day's
    # attempt is settled. False for "not now" and for ``deferred``.
    settled: bool = False


class _Stop(Exception):
    def __init__(self, row: UpdateOutcome) -> None:
        self.row = row


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


def _newer(target: str, installed: str) -> bool:
    try:
        return _semver_tuple(target) > _semver_tuple(installed)
    except ValueError:
        return False


def auto_update_settings(config: dict[str, Any]) -> tuple[bool, str, float]:
    """(enabled, channel, soak_hours) from the merged config; tolerant of bad values."""
    block = config.get("auto_update")
    block = block if isinstance(block, dict) else {}
    enabled = block.get("enabled") is True
    channel = block.get("channel")
    channel = channel if isinstance(channel, str) else "stable"
    soak = block.get("soak_hours", DEFAULT_SOAK_HOURS)
    if isinstance(soak, bool) or not isinstance(soak, (int, float)) or soak < 0:
        soak = DEFAULT_SOAK_HOURS
    return enabled, channel, float(soak)


def eligibility(deps: Deps) -> tuple[str | None, EnginePointer | None, dict[str, Any] | None]:
    """``(reason, pointer, config)``; ``reason`` is None when this engine may update itself.

    Checked in spec §7.2 order. The ``scout-app`` refusal comes first and reads
    nothing but the pointer: no config, no network, no subprocess.
    """
    pointer = deps.read_pointer()
    if pointer is not None and pointer.managed_by == "scout-app":
        return "managed_by=scout-app", pointer, None
    config = deps.load_config()
    enabled, channel, _ = auto_update_settings(config)
    if not enabled:
        return "disabled", pointer, config
    if pointer is None or pointer.python != deps.interpreter:
        return "pointer_mismatch", pointer, config
    if pointer.managed_by not in UPDATABLE:
        return f"managed_by={pointer.managed_by}", pointer, config
    if channel != "stable":
        return f"channel={channel}", pointer, config
    return None, pointer, config


def resolve_install_path(list_json: str) -> Path | None:
    """The Scout plugin's ``installPath`` from ``claude plugin list --json``.

    Claude Code has printed both a top-level list and a ``{"plugins": {id: [...]}}``
    map; accept either (install.sh does the same).
    """
    try:
        data = json.loads(list_json)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict) and isinstance(data.get("plugins"), dict):
        entries = [e for group in data["plugins"].values() if isinstance(group, list) for e in group]
    else:
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID and isinstance(entry.get("installPath"), str):
            return Path(entry["installPath"])
    return None


def _plugin_version(root: Path) -> str | None:
    try:
        data = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def _last_completed_check(rows: list[UpdateOutcome]) -> datetime | None:
    for row in reversed(rows):
        if row.result != "deferred":
            return row.at
    return None


def scheduled_gate(deps: Deps, rows: list[UpdateOutcome]) -> str | None:
    """Why a scheduled attempt should not run now, or None (spec §7.1, §7.5)."""
    now = deps.now()
    for row in reversed(rows):
        if row.result in ("applied", "applied_with_sidecars"):
            if now - row.at < APPLY_SPACING:
                return "applied a version in the last 24 h"
            break
    if deps.in_off_peak():
        return None
    last = _last_completed_check(rows)
    if last is None or now - last > STALE_CHECK:
        return None
    return "outside the off-peak window"


def _target_backoff(rows: list[UpdateOutcome], target: str, *, now: datetime) -> str | None:
    # A network outage says nothing about the release itself, so it never counts against it.
    failures = [
        r for r in rows if r.result == "failed" and r.target == target and not r.detail.startswith(NETWORK_PREFIX)
    ]
    if len(failures) >= MAX_FAILURES_PER_TARGET:
        return f"{target} failed {len(failures)} times; waiting for a newer release or --force"
    if failures and now - failures[-1].at < RETRY_AFTER_FAILURE:
        return f"{target} failed less than 24 h ago"
    return None


def _trailing_network_failures(rows: list[UpdateOutcome]) -> int:
    count = 0
    for row in reversed(rows):
        if row.step == "check" and row.result in ("deferred", "failed") and row.detail.startswith(NETWORK_PREFIX):
            if row.result == "failed":
                break
            count += 1
            continue
        break
    return count


class _Attempt:
    """One attempt's state: builds rows that share the from/target/managed_by fields."""

    def __init__(self, deps: Deps, managed_by: str, from_version: str) -> None:
        self.deps = deps
        self.managed_by = managed_by
        self.from_version = from_version
        self.target: str | None = None
        self.published_at: datetime | None = None

    def row(self, result: str, step: str, detail: str = "", **kw: Any) -> UpdateOutcome:
        return UpdateOutcome(
            at=self.deps.now(),
            from_version=self.from_version,
            target=self.target,
            managed_by=self.managed_by,
            result=result,
            step=step,
            detail=_clip(detail),
            log=self.deps.log_name,
            release_published_at=self.published_at,
            **kw,
        )

    def stop(self, result: str, step: str, detail: str = "", **kw: Any) -> _Stop:
        return _Stop(self.row(result, step, detail, **kw))

    def run(self, argv: list[str], step: str, env: dict[str, str] | None = None) -> Proc:
        self.deps.say(f"[{step}] $ {' '.join(argv)}")
        try:
            proc = self.deps.run(argv, env)
        except (OSError, subprocess.SubprocessError) as e:
            raise self.stop("failed", step, f"could not run {Path(argv[0]).name}: {type(e).__name__}: {e}") from e
        if proc.stdout.strip():
            self.deps.say(proc.stdout.rstrip())
        if proc.stderr.strip():
            self.deps.say(proc.stderr.rstrip())
        return proc


def _check(att: _Attempt, rows: list[UpdateOutcome]) -> UpdateStatus:
    try:
        status = att.deps.check()
    except RuntimeError as e:
        if _trailing_network_failures(rows) + 1 >= CHECK_FAILURES_BEFORE_FAILED:
            raise att.stop("failed", "check", f"{NETWORK_PREFIX}{e}") from e
        raise att.stop("deferred", "check", f"{NETWORK_PREFIX}{e}") from e
    att.from_version = status.installed
    att.target = status.available
    return status


def _release(att: _Attempt, rows: list[UpdateOutcome], soak_hours: float) -> ReleaseStatus:
    assert att.target is not None
    try:
        release = release_status(att.target, now=att.deps.now(), soak_hours=soak_hours, fetch=att.deps.fetch_release)
    except RuntimeError as e:
        if _trailing_network_failures(rows) + 1 >= CHECK_FAILURES_BEFORE_FAILED:
            raise att.stop("failed", "check", f"{NETWORK_PREFIX}{e}") from e
        raise att.stop("deferred", "check", f"{NETWORK_PREFIX}{e}") from e
    att.published_at = release.published_at
    return release


def _fetch(att: _Attempt, pointer: EnginePointer) -> Path:
    claude = att.deps.claude_bin()
    for argv in (
        [claude, "plugin", "marketplace", "update", MARKETPLACE],
        [claude, "plugin", "update", PLUGIN_ID, "--scope", "user"],
    ):
        proc = att.run(argv, "fetch")
        if proc.returncode != 0:
            raise att.stop("failed", "fetch", f"`{' '.join(argv[1:])}` exited {proc.returncode}: {proc.stderr}")
    proc = att.run([claude, "plugin", "list", "--json"], "fetch")
    root = resolve_install_path(proc.stdout) if proc.returncode == 0 else None
    if root is None:
        raise att.stop("failed", "fetch", f"`claude plugin list --json` does not name {PLUGIN_ID}")
    version = _plugin_version(root)
    if version != att.target:
        raise att.stop("failed", "fetch", f"Claude Code installed {version or 'an unknown version'}, not {att.target}")
    if root.resolve() == Path(pointer.engine_root).resolve():
        # install-venv.sh deletes the venv it builds into: never point it at the live engine.
        raise att.stop("failed", "fetch", f"the new plugin root is the live engine root ({root})")
    return root


def _venv(att: _Attempt, root: Path) -> Path:
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_VENV_DIR"}
    proc = att.run(["bash", str(root / "scripts" / "install-venv.sh")], "venv", env)
    if proc.returncode != 0:
        raise att.stop("failed", "venv", f"install-venv.sh exited {proc.returncode}: {proc.stderr}")
    scoutctl = root / ".venv" / "bin" / "scoutctl"
    proc = att.run([str(scoutctl), "version"], "venv")
    got = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if proc.returncode != 0 or got != att.target:
        raise att.stop("failed", "venv", f"the new engine reports {got or 'no version'}, not {att.target}")
    return scoutctl


def _vault_env(vault: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_VENV_DIR"}
    env["SCOUT_DATA_DIR"] = str(vault)
    return env


def _parse_json(text: str) -> dict[str, Any] | None:
    for candidate in (text, text.strip().splitlines()[-1] if text.strip() else ""):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _vault_step(att: _Attempt, scoutctl: Path) -> list[str]:
    argv = [
        str(scoutctl),
        "bootstrap",
        "auto",
        "--no-interactive",
        "--yes",
        "--json",
        "--managed-by",
        att.managed_by,
        "--claude-bin",
        att.deps.claude_bin(),
    ]
    proc = att.run(argv, "vault", _vault_env(att.deps.vault))
    result = _parse_json(proc.stdout)
    if result is None:
        raise att.stop("failed", "vault", f"bootstrap auto exited {proc.returncode} without a result: {proc.stderr}")
    if result.get("action") == "refused":
        error = str(result.get("error") or result.get("reason") or "")
        if result.get("mutated") is False and _LOCK_BUSY_MARK in error:
            raise att.stop("deferred", "vault", f"vault lock busy: {error}")
        raise att.stop("failed", "vault", f"bootstrap auto refused: {error}")
    conflicts = result.get("conflicts")
    return [str(c) for c in conflicts] if isinstance(conflicts, list) else []


def _verify(att: _Attempt, scoutctl: Path, conflicts: list[str]) -> UpdateOutcome:
    proc = att.run([str(scoutctl), "bootstrap", "doctor", "--json"], "verify", _vault_env(att.deps.vault))
    report = _parse_json(proc.stdout)
    severity = str(report.get("severity")) if report else "unknown"
    if severity not in ("green", "yellow"):
        errors = "; ".join(str(e) for e in (report or {}).get("errors") or [])
        raise att.stop("failed", "verify", f"install doctor is {severity} after the upgrade. {errors}", doctor=severity)
    if conflicts:
        return att.row(
            "applied_with_sidecars",
            "verify",
            f"left {len(conflicts)} file(s) to merge",
            doctor=severity,
            sidecars=tuple(conflicts),
        )
    return att.row("applied", "verify", doctor=severity)


def run_update(deps: Deps, *, scheduled: bool, dry_run: bool = False, force: bool = False) -> Attempt:
    """One update attempt. Appends its row (unless ``dry_run``) and calls ``deps.notify``."""
    rows = read_outcomes(deps.vault)
    reason, pointer, config = eligibility(deps)
    from_version = pointer.version if pointer is not None else "unknown"
    managed_by = pointer.managed_by if pointer is not None else "unknown"
    att = _Attempt(deps, managed_by, from_version)

    if reason is not None:
        row = att.row("not_eligible", "check", reason)
        if reason in ("managed_by=dev", "managed_by=unknown"):
            # Read-only: the target feeds update.behind. Nothing else runs.
            try:
                status = deps.check()
                att.from_version = status.installed
                att.target = status.available
                row = att.row("not_eligible", "check", reason)
            except RuntimeError:
                pass
        return _finish(deps, row, rows, dry_run=dry_run)

    assert pointer is not None and config is not None
    _, _, soak_hours = auto_update_settings(config)
    if scheduled and not force:
        why = scheduled_gate(deps, rows)
        if why is not None:
            deps.say(f"not now: {why}")
            return Attempt(row=None, reason=why, settled=why.startswith("applied"))

    try:
        if deps.lock_busy():
            raise att.stop("deferred", "check", "a Scout session holds the vault lock")
        if deps.slot_due_within(SLOT_LOOKAHEAD):
            raise att.stop("deferred", "check", "a scheduled run is due within 45 min")
        status = _check(att, rows)
        assert att.target is not None
        if not status.update_available or not _newer(att.target, att.from_version):
            raise att.stop("up_to_date", "check")
        release = _release(att, rows, soak_hours)
        if not release.published:
            raise att.stop("waiting_release", "check", release.reason)
        if not force:
            backoff = _target_backoff(rows, att.target, now=deps.now())
            if backoff is not None:
                deps.say(f"not now: {backoff}")
                return Attempt(row=None, reason=backoff, settled=True)
        sidecars = deps.blocking_sidecars()
        if sidecars:
            raise att.stop("held", "check", f"unresolved merge file(s): {', '.join(sidecars)}")
        severity = deps.doctor_severity()
        if severity == "red":
            raise att.stop("held", "check", "the install doctor is red", doctor=severity)
        if dry_run:
            deps.say(f"would update {from_version} -> {att.target} ({managed_by}): fetch, venv, vault, verify")
            return Attempt(row=None, reason="dry run")
        root = _fetch(att, pointer)
        scoutctl = _venv(att, root)
        conflicts = _vault_step(att, scoutctl)
        row = _verify(att, scoutctl, conflicts)
    except _Stop as stop:
        row = stop.row
    return _finish(deps, row, rows, dry_run=dry_run)


def _finish(deps: Deps, row: UpdateOutcome, prior: list[UpdateOutcome], *, dry_run: bool) -> Attempt:
    deps.say(f"{row.result} ({row.step}) {row.from_version} -> {row.target or '?'} {row.detail}".rstrip())
    if dry_run:
        return Attempt(row=row, reason="dry run")
    append_outcome(deps.vault, row)
    deps.notify(row, prior)
    return Attempt(row=row, settled=row.result != "deferred")


# --- process handling ---------------------------------------------------------------------

LOCK_NAME = ".scout-cache/self-update.lock"


@contextmanager
def run_lock(vault: Path) -> Iterator[bool]:
    """Hold an exclusive, non-blocking lock for one attempt; yields False when another holds it.

    The heartbeat may start a new attempt every 30 min while an earlier one is still
    building its venv; two must never fetch or build into the same plugin root at once.
    The OS drops the lock when the holder exits, however it exits.
    """
    path = vault / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def spawn_detached(argv: list[str], *, log_path: Path, popen: Callable[..., Any] = subprocess.Popen) -> int:
    """Start ``argv`` in its own session, appending its output to ``log_path``; return its PID.

    The heartbeat's launchd job has no ``AbandonProcessGroup``, so launchd kills
    whatever is left in the job's process group when the heartbeat exits. A
    backgrounded ``nohup … &`` stays in that group; a new session does not. This is
    the same pattern ``heartbeat.launch_runner`` and ``schedule_tick`` use.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_fh:
        proc = popen(
            argv,
            env=os.environ.copy(),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )
    return int(proc.pid)


# --- real wiring --------------------------------------------------------------------------


def _subprocess_runner(argv: list[str], env: dict[str, str] | None) -> Proc:
    proc = subprocess.run(  # noqa: S603 - argv is built here, never from a shell string
        argv, capture_output=True, text=True, env=env, timeout=_STEP_TIMEOUT_S, stdin=subprocess.DEVNULL
    )
    return Proc(proc.returncode, proc.stdout, proc.stderr)


def _slot_due_within(vault: Path, window: timedelta) -> bool:
    # Slots fire in `schedule tick`'s zone ($TZ, then the host's), so look ahead in that
    # zone, not the vault's: with a `user.timezone` override that differs from the host,
    # the vault's zone would mispredict every slot. _in_off_peak reads the vault's zone
    # because the heartbeat's off-peak window is defined there. Both are deliberate (#449).
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from scout.schedule import load_schedule, next_fires
    from scout.scripts.schedule_tick import _local_tz_name

    path = vault / ".scout-state" / "schedule.yaml"
    if not path.exists():
        return False
    try:
        tz = ZoneInfo(_local_tz_name())
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    try:
        sched = load_schedule(path)
    except Exception:  # noqa: BLE001 - an unreadable schedule never blocks an update
        return False
    return bool(next_fires(sched, now=datetime.now(tz), window_hours=window.total_seconds() / 3600))


def _in_off_peak(vault: Path) -> bool:
    from scout import config as scout_config
    from scout.scripts import heartbeat

    cfg = heartbeat.load_config(vault / "scout-config.yaml")
    return heartbeat.in_off_peak(scout_config.now(vault).hour, cfg)


def _claude_bin(config: Callable[[], dict[str, Any]]) -> str:
    from scout.scripts.bootstrap import resolve_claude_bin

    connectors = config().get("connectors")
    inputs = connectors.get("inputs") if isinstance(connectors, dict) else None
    explicit = inputs.get("claude_bin") if isinstance(inputs, dict) else None
    return resolve_claude_bin(explicit if isinstance(explicit, str) else "")


def default_notify(vault: Path) -> Callable[[UpdateOutcome, list[UpdateOutcome]], None]:
    from scout import health_notify

    def notify(row: UpdateOutcome, prior: list[UpdateOutcome]) -> None:
        for push in info_pushes(row, prior):
            health_notify.fan_out(push, vault=vault, now=row.at)
        health_notify.notify(vault)

    return notify


def info_pushes(row: UpdateOutcome, prior: list[UpdateOutcome]) -> list[Any]:
    """The one-off informational pushes an attempt earns (spec §7.3, §9 P7)."""
    from scout.health_notify import Push

    pushes = []
    if row.result in ("applied", "applied_with_sidecars") and not any(
        r.result in ("applied", "applied_with_sidecars") for r in prior
    ):
        pushes.append(
            Push(
                finding_id="update.applied",
                kind="info",
                severity="green",
                title=f"Scout updated itself to {row.target}",
                body=f"Scout updated itself from {row.from_version} to {row.target}. It does this because "
                "auto_update.enabled is on in scout-config.yaml. To turn it off, run: "
                "scoutctl config set-auto-update --disabled",
            )
        )
    if row.result == "applied_with_sidecars":
        pushes.append(
            Push(
                finding_id="update.sidecars",
                kind="info",
                severity="yellow",
                title=f"Scout {row.target}: files to merge",
                body=f"Scout updated to {row.target} and kept your edits to {', '.join(row.sidecars)}. "
                "Scout keeps running on them. To merge the new versions, run: scoutctl bootstrap drift, "
                "then scoutctl bootstrap resolve",
            )
        )
    return pushes


def default_deps(vault: Path, *, home: Path | None = None, log_name: str | None = None, quiet: bool = False) -> Deps:
    from scout.config import load_config
    from scout.scripts import self_update
    from scout.scripts.bootstrap import blocking_sidecars
    from scout.scripts.bootstrap_doctor import run_doctor
    from scout.scripts.bootstrap_lock import is_lock_held_by_live_pid
    from scout.scripts.engine_pointer import read_pointer

    home = home or Path.home()

    def config() -> dict[str, Any]:
        return load_config(vault)

    return Deps(
        vault=vault,
        now=lambda: datetime.now().astimezone(),
        read_pointer=lambda: read_pointer(home=home),
        interpreter=str(Path(sys.executable).absolute()),
        load_config=config,
        check=self_update.check,
        fetch_release=self_update.fetch_release,
        run=_subprocess_runner,
        lock_busy=lambda: is_lock_held_by_live_pid(vault / ".scout-logs" / ".scout-session.lock"),
        slot_due_within=lambda window: _slot_due_within(vault, window),
        in_off_peak=lambda: _in_off_peak(vault),
        doctor_severity=lambda: run_doctor(vault=vault, home=home).severity.value,
        blocking_sidecars=lambda: blocking_sidecars(vault),
        claude_bin=lambda: _claude_bin(config),
        notify=default_notify(vault),
        log_name=log_name,
        say=(lambda msg: None) if quiet else (lambda msg: print(msg, flush=True)),
    )

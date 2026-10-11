"""Run-health push channels and fan-out (desktop, Telegram, ``notify.command``).

Every push goes to every configured channel, in order, and one failing channel
never stops the others. Results never carry ``str(exception)``: HTTP errors
from the Telegram client can embed the bot token.
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from scout import health, notify_desktop
from scout.errors import ConfigError
from scout.health import RunHealth
from scout.scripts import notify_telegram

AUTH_FINDING_ID = "auth.expired"
AUTH_ALERT_WINDOW_S = 24 * 3600
COMMAND_TIMEOUT_S = 30
STATE_FILE = ".scout-state/health-notify.json"
LOCK_FILE = ".scout-state/health-notify.lock"
REMIND_AFTER = timedelta(hours=24)
LOCK_TIMEOUT_S = 10.0
LOCK_RETRY_S = 0.2

ChannelResult = dict


@dataclass(frozen=True)
class Push:
    finding_id: str  # or "recovered"
    kind: str  # "new" | "reminder" | "recovered" | "info"
    severity: str  # "red" | "green" | "yellow"
    title: str
    body: str


def _result(channel: str, ok: bool, detail: str) -> ChannelResult:
    return {"channel": channel, "ok": ok, "detail": detail}


# ----- desktop ----------------------------------------------------------


def _wrapper_alert_age_s(vault: Path, now: datetime) -> float | None:
    """Seconds since the launch wrapper last sent its own auth alert, if ever."""
    marker = vault / ".scout-logs" / ".auth-alert-sent"
    try:
        text = marker.read_text(encoding="utf-8").strip()
        stamp = float(text) if text.isdigit() else marker.stat().st_mtime
    except OSError:
        return None
    return now.timestamp() - stamp


def desktop_channel(push: Push, *, vault: Path, now: datetime, send: Callable | None = None) -> ChannelResult | None:
    """Desktop notification; skipped when the wrapper already alerted about auth today."""
    if push.finding_id == AUTH_FINDING_ID:
        age = _wrapper_alert_age_s(vault, now)
        if age is not None and age < AUTH_ALERT_WINDOW_S:
            return None
    send = send or notify_desktop.send
    if send(push.title, push.body):
        return _result("desktop", True, "sent")
    return _result("desktop", False, "unavailable or failed")


# ----- telegram ---------------------------------------------------------


def telegram_channel(push: Push, *, send: Callable | None = None) -> ChannelResult | None:
    """Telegram push; None when Telegram is not configured."""
    send = send or notify_telegram.send
    tier = "action_required" if push.severity == "red" and push.kind in ("new", "reminder") else "info"
    try:
        send(tier, f"{push.title}\n{push.body}")
    except ConfigError:
        return None
    except notify_telegram.DuplicateSendError:
        # The identical push already reached the chat: a recorded no-op.
        return _result("telegram", True, "duplicate")
    except Exception as e:  # noqa: BLE001 - class name only; str(e) may embed the bot token
        return _result("telegram", False, type(e).__name__)
    return _result("telegram", True, "sent")


# ----- notify.command ---------------------------------------------------


def _configured_command(vault: Path) -> str | None:
    try:
        data = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    notify = data.get("notify") if isinstance(data, dict) else None
    command = notify.get("command") if isinstance(notify, dict) else None
    if isinstance(command, str) and command.strip():
        return command
    return None


def command_channel(push: Push, *, vault: Path, run: Callable | None = None) -> ChannelResult | None:
    """Run the user's ``notify.command`` with the push in SCOUT_NOTIFY_* env vars."""
    command = _configured_command(vault)
    if command is None:
        return None
    run = run or subprocess.run
    env = {
        **os.environ,
        "SCOUT_NOTIFY_TITLE": push.title,
        "SCOUT_NOTIFY_BODY": push.body,
        "SCOUT_NOTIFY_SEVERITY": push.severity,
        "SCOUT_NOTIFY_FINDING": push.finding_id,
    }
    try:
        proc = run(
            ["/bin/sh", "-c", command],
            env=env,
            timeout=COMMAND_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        return _result("command", False, "timeout")
    except OSError:
        return _result("command", False, "oserror")
    return _result("command", proc.returncode == 0, f"exit {proc.returncode}")


# ----- fan-out ----------------------------------------------------------


def fan_out(
    push: Push,
    *,
    vault: Path,
    now: datetime,
    channels: list[Callable[[Push], ChannelResult | None]] | None = None,
) -> list[ChannelResult]:
    """Send ``push`` to every channel in order; skipped channels are dropped."""
    if channels is None:
        channels = [
            functools.partial(desktop_channel, vault=vault, now=now),
            functools.partial(telegram_channel),
            functools.partial(command_channel, vault=vault),
        ]
        names = ["desktop", "telegram", "command"]
    else:
        names = [getattr(c, "__name__", "unknown").removesuffix("_channel") for c in channels]
    results: list[ChannelResult] = []
    for name, channel in zip(names, channels, strict=True):
        try:
            res = channel(push)
        except Exception:  # noqa: BLE001 - one channel must never stop the others
            res = _result(name, False, "error")
        if res is not None:
            results.append(res)
    return results


# ----- state machine ----------------------------------------------------


def load_state(vault: Path) -> dict:
    """The saved notifier state; ``{}`` when missing, corrupt, or not a mapping."""
    try:
        data = json.loads((vault / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(vault: Path, state: dict) -> None:
    """Atomically write the state; a write failure must never break a run."""
    path = vault / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_words(delta: timedelta) -> str:
    days = int(delta.total_seconds() // 86400)
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    hours = max(1, int(delta.total_seconds() // 3600))
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _push_for(finding: health.Finding, kind: str, body: str) -> Push:
    return Push(
        finding_id=finding.id,
        kind=kind,
        severity="red",
        title=f"Scout: {finding.summary[:60]}",
        body=body,
    )


def plan(report: RunHealth, state: dict, *, now: datetime) -> tuple[list[Push], dict]:
    """Decide which pushes the report earns, and the state that remembers them."""
    prior = state.get("findings")
    prior = prior if isinstance(prior, dict) else {}
    stamp = health.iso_z(now)
    pushes: list[Push] = []
    findings: dict[str, dict] = {}
    for f in report.findings:
        old = prior.get(f.id)
        old = old if isinstance(old, dict) else {}
        first_seen = _parse_stamp(old.get("first_seen")) or now
        entry: dict = {"first_seen": health.iso_z(first_seen), "last_pushed": None, "pushes": 0}
        if f.severity == "red":
            last_pushed = _parse_stamp(old.get("last_pushed"))
            count = old.get("pushes") if isinstance(old.get("pushes"), int) else 0
            entry["last_pushed"] = old.get("last_pushed") if last_pushed else None
            entry["pushes"] = count
            if last_pushed is None:
                pushes.append(_push_for(f, "new", f"{f.summary}\nFix: {f.fix}"))
            elif now - last_pushed >= REMIND_AFTER:
                elapsed = _elapsed_words(now - first_seen)
                pushes.append(_push_for(f, "reminder", f"Still failing after {elapsed}: {f.summary}\nFix: {f.fix}"))
            if pushes and pushes[-1].finding_id == f.id:
                entry["last_pushed"] = stamp
                entry["pushes"] = count + 1
        findings[f.id] = entry
    new_state: dict = {"findings": findings, "last_overall": report.severity}
    if "last_recovered" in state:
        new_state["last_recovered"] = state["last_recovered"]
    if state.get("last_overall") == "red" and report.severity != "red":
        warnings = sum(1 for f in report.findings if f.severity == "yellow")
        body = "Scout runs are healthy again."
        if report.severity == "yellow":
            body = f"Scout runs are healthy again; remaining warnings: {warnings}"
        pushes.append(
            Push(
                finding_id="recovered",
                kind="recovered",
                severity=report.severity,
                title="Scout: runs recovered",
                body=body,
            )
        )
    return pushes, new_state


def _acquire_lock(vault: Path, *, timeout: float, sleep: Callable[[float], None]):
    """Take the exclusive advisory lock, retrying up to ``timeout`` s; None if it stays held."""
    path = vault / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            sleep(LOCK_RETRY_S)


def notify(
    vault: Path,
    *,
    now: datetime | None = None,
    channels: list[Callable[[Push], ChannelResult | None]] | None = None,
    report: RunHealth | None = None,
    lock_timeout: float = LOCK_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[Push, list[ChannelResult]]]:
    """Evaluate run health, push what is newly red / overdue / recovered, remember it.

    Serialised by an advisory lock so a run-end call and a heartbeat sweep cannot
    both announce the same failure. Never raises: pushes are best-effort.
    """
    try:
        lock = _acquire_lock(vault, timeout=lock_timeout, sleep=sleep)
        if lock is None:
            return []
        with lock:
            now = now or datetime.now(UTC)
            report = report or health.evaluate(vault, now=now)
            pushes, state = plan(report, load_state(vault), now=now)
            sent: list[tuple[Push, list[ChannelResult]]] = []
            for push in pushes:
                results = fan_out(push, vault=vault, now=now, channels=channels)
                sent.append((push, results))
                if push.kind == "recovered":
                    state["last_recovered"] = results
                else:
                    state["findings"][push.finding_id]["last_results"] = results
            save_state(vault, state)
            return sent
    except Exception:  # noqa: BLE001 - a notifier fault must never fail the run that called it
        return []

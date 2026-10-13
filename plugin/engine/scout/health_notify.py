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
import sys
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
# At wake, launchd fires the missed heartbeat and schedule tick together, and the
# tick's completion lands only at its end: a sweep that runs first sees a stalled
# scheduler that isn't. These findings push only once present for GRACE.
GRACE = timedelta(minutes=20)
GRACED_PREFIXES = ("scheduler.stalled", "slot.stale:")
TITLE_SUMMARY_MAX = 60
LOCK_TIMEOUT_S = 10.0
LOCK_RETRY_S = 0.2

ChannelResult = dict


@dataclass(frozen=True)
class Push:
    finding_id: str  # the first listed notifier key, or "recovered"
    kind: str  # "new" | "reminder" | "recovered" | "info"
    severity: str  # "red" | "green" | "yellow"
    title: str
    body: str
    finding_ids: tuple[str, ...] = ()  # every notifier key this push lists; () for "recovered"


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
    """Desktop notification; skipped when it is only about auth and the wrapper alerted today."""
    if (push.finding_ids or (push.finding_id,)) == (AUTH_FINDING_ID,):
        age = _wrapper_alert_age_s(vault, now)
        if age is not None and 0 <= age < AUTH_ALERT_WINDOW_S:
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
        send(tier, push.body)  # the body already says it all; the title is for desktop
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


def _notify_key(finding_id: str) -> str:
    """The state key for a finding: one per lane, so silent<->dark is one outage."""
    for prefix in ("lane.silent:", "lane.dark:"):
        if finding_id.startswith(prefix):
            return "lane:" + finding_id.removeprefix(prefix)
    return finding_id


def _graced(finding_id: str) -> bool:
    return finding_id.startswith(GRACED_PREFIXES)


def _short(summary: str) -> str:
    """``summary`` in at most TITLE_SUMMARY_MAX chars, cut at a word boundary with an ellipsis."""
    if len(summary) <= TITLE_SUMMARY_MAX:
        return summary
    cut = summary[: TITLE_SUMMARY_MAX - 1]
    head, sep, _ = cut.rpartition(" ")
    return (head if sep and head else cut).rstrip(" ,;:") + "…"


def _red_push(listed: list[tuple[str, health.Finding, str | None]], kind: str) -> Push:
    """One push for every listed red: (key, finding, elapsed words when it is a reminder)."""
    if len(listed) == 1:
        _, f, elapsed = listed[0]
        lead = f"Still failing after {elapsed}: " if elapsed else ""
        title = f"Scout: {_short(f.summary)}"
        body = f"{lead}{f.summary}\nFix: {f.fix}"
    else:
        title = f"Scout: {len(listed)} problems need attention"
        body = "\n".join(
            f"• {f'Still failing after {elapsed}: ' if elapsed else ''}{f.summary} — Fix: {f.fix}"
            for _, f, elapsed in listed
        )
    keys = tuple(key for key, _, _ in listed)
    return Push(finding_id=keys[0], kind=kind, severity="red", title=title, body=body, finding_ids=keys)


def plan(report: RunHealth, state: dict, *, now: datetime) -> tuple[list[Push], dict]:
    """Decide which pushes the report earns, and the state that remembers them.

    At most one red push per call (every red that is new, being retried, or due a
    reminder, listed together) and at most one "recovered". State is per notifier
    key. A graced red (see GRACE) that has not yet been present for GRACE is only
    recorded, and does not count as red for the "recovered" bookkeeping.
    """
    prior = state.get("findings")
    prior = prior if isinstance(prior, dict) else {}
    stamp = health.iso_z(now)
    findings: dict[str, dict] = {}
    listed: list[tuple[str, health.Finding, str | None]] = []
    any_new = False
    announced = "green"
    for f in report.findings:
        key = _notify_key(f.id)
        if key in findings:
            continue
        old = prior.get(key)
        old = old if isinstance(old, dict) else {}
        first_seen = _parse_stamp(old.get("first_seen")) or now
        entry: dict = {"first_seen": health.iso_z(first_seen), "last_pushed": None, "pushes": 0}
        findings[key] = entry
        if f.severity == "yellow" and announced == "green":
            announced = "yellow"
        if f.severity != "red":
            continue
        last_pushed = _parse_stamp(old.get("last_pushed"))
        count = old.get("pushes") if isinstance(old.get("pushes"), int) else 0
        entry["last_pushed"] = old.get("last_pushed") if last_pushed else None
        entry["pushes"] = count
        if _graced(f.id) and now - first_seen < GRACE:
            continue
        announced = "red"
        if last_pushed is None:
            any_new = True
            listed.append((key, f, None))
        elif now - last_pushed >= REMIND_AFTER:
            listed.append((key, f, _elapsed_words(now - (f.since or first_seen))))
    pushes: list[Push] = []
    if listed:
        listed.sort(key=lambda item: item[0] != AUTH_FINDING_ID)  # stable: report order otherwise
        pushes.append(_red_push(listed, "new" if any_new else "reminder"))
        for key, _, _ in listed:
            findings[key]["last_pushed"] = stamp
            findings[key]["pushes"] += 1
    new_state: dict = {"findings": findings, "last_overall": announced}
    if "last_recovered" in state:
        new_state["last_recovered"] = state["last_recovered"]
    if state.get("last_overall") == "red" and announced != "red":
        pause = next((f for f in report.findings if f.id == health.PAUSED_FINDING_ID), None)
        warnings = sum(1 for f in report.findings if f.severity == "yellow")
        title, body = "Scout: runs recovered", "Scout runs are healthy again."
        if pause is not None:
            # Out of red because the usage limit's reset is now known (#424): not
            # healthy, just waiting. The pause is yellow, so this is its one push.
            title, body = "Scout: runs paused (Claude usage limit)", f"{pause.summary}\nFix: {pause.fix}"
        elif warnings:
            body = f"Scout runs are healthy again; remaining warnings: {warnings}"
        pushes.append(Push(finding_id="recovered", kind="recovered", severity=announced, title=title, body=body))
    return pushes, new_state


def _acquire_lock(vault: Path, *, timeout: float, sleep: Callable[[float], None]):
    """Take the exclusive advisory lock, retrying up to ``timeout`` s; None if it stays held."""
    path = vault / LOCK_FILE
    path.parent.mkdir(exist_ok=True)  # the state dir only, never the vault itself
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
        if not vault.is_dir():
            return []  # nothing to evaluate, and nowhere to keep state: create nothing
        lock = _acquire_lock(vault, timeout=lock_timeout, sleep=sleep)
        if lock is None:
            return []
        with lock:
            now = now or datetime.now(UTC)
            report = report or health.evaluate(vault, now=now)
            prior = load_state(vault)
            pushes, state = plan(report, prior, now=now)
            sent: list[tuple[Push, list[ChannelResult]]] = []
            for push in pushes:
                results = fan_out(push, vault=vault, now=now, channels=channels)
                sent.append((push, results))
                if push.kind == "recovered":
                    state["last_recovered"] = results
                    continue
                delivered = any(r.get("ok") for r in results)
                prior_findings = prior.get("findings")
                prior_findings = prior_findings if isinstance(prior_findings, dict) else {}
                for key in push.finding_ids:
                    entry = state["findings"][key]
                    entry["last_results"] = results
                    if not delivered:
                        # Nothing was delivered: restore the prior stamp so the next call retries.
                        old = prior_findings.get(key)
                        old = old if isinstance(old, dict) else {}
                        entry["last_pushed"] = old.get("last_pushed")
                        entry["pushes"] = old["pushes"] if isinstance(old.get("pushes"), int) else 0
            save_state(vault, state)
            return sent
    except Exception as e:  # noqa: BLE001 - a notifier fault must never fail the run that called it
        # Only the evaluate / plan / state path raises here: channels are caught in
        # fan_out and record class names only, so no channel text (a token) lands here.
        print(f"health notify: internal error {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return []

"""Run-health push channels and fan-out (desktop, Telegram, ``notify.command``).

Every push goes to every configured channel, in order, and one failing channel
never stops the others. Results never carry ``str(exception)``: HTTP errors
from the Telegram client can embed the bot token.
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from scout import notify_desktop
from scout.errors import ConfigError
from scout.scripts import notify_telegram

AUTH_FINDING_ID = "auth.expired"
AUTH_ALERT_WINDOW_S = 24 * 3600
COMMAND_TIMEOUT_S = 30

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

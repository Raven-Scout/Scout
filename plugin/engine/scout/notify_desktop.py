"""Desktop notification: osascript on macOS, notify-send on Linux.

Mirrors ``notify_auth_failure`` in the launchd/cron wrapper
(``templates/scripts/claude-with-retry.sh.tmpl``). Title and body are passed
as argv, never interpolated into the AppleScript source.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

TIMEOUT_S = 10

_APPLESCRIPT = (
    'on run argv\n    display notification (item 2 of argv) with title (item 1 of argv) sound name "Basso"\nend run\n'
)


def send(
    title: str,
    body: str,
    *,
    run: Callable | None = None,
    which: Callable | None = None,
    env: Mapping[str, str] | None = None,
    bus_path: Path | None = None,
) -> bool:
    """Show a desktop notification. True on exit 0; False if unavailable or failed."""
    run = run or subprocess.run
    which = which or shutil.which
    try:
        if which("osascript"):
            result = run(
                ["osascript", "-", title, body],
                input=_APPLESCRIPT,
                text=True,
                capture_output=True,
                timeout=TIMEOUT_S,
            )
            return result.returncode == 0
        if which("notify-send"):
            child_env = dict(os.environ if env is None else env)
            bus = bus_path if bus_path is not None else Path(f"/run/user/{os.getuid()}/bus")
            # cron runs without the desktop session's bus address.
            if not child_env.get("DBUS_SESSION_BUS_ADDRESS") and bus.exists():
                child_env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
            result = run(
                ["notify-send", "--urgency=critical", title, body],
                env=child_env,
                capture_output=True,
                timeout=TIMEOUT_S,
            )
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    return False

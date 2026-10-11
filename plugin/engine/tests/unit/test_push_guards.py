"""conftest's _block_real_pushes: an unpatched desktop or Telegram send fails the test.

Each test checks that the guard is in place BEFORE touching the entry point, so
without the guard it fails on the identity check and never reaches a real
notifier.
"""

from __future__ import annotations

import subprocess

import pytest
import requests

from scout import health_notify, notify_desktop
from scout.scripts import notify_telegram


def test_desktop_subprocess_run_is_blocked():
    assert notify_desktop.subprocess is not subprocess
    with pytest.raises(pytest.fail.Exception, match="real osascript"):
        notify_desktop.send("T", "B", which=lambda name: f"/bin/{name}" if name == "osascript" else None)


def test_desktop_guard_keeps_the_rest_of_subprocess():
    assert notify_desktop.subprocess.TimeoutExpired is subprocess.TimeoutExpired
    assert notify_desktop.subprocess.SubprocessError is subprocess.SubprocessError
    assert subprocess.run is not notify_desktop.subprocess.run  # the real module is untouched


def test_telegram_requests_post_is_blocked():
    assert notify_telegram.requests is not requests
    with pytest.raises(pytest.fail.Exception, match="real Telegram send"):
        notify_telegram.requests.post("https://example.invalid", json={}, timeout=1)
    assert notify_telegram.requests.HTTPError is requests.HTTPError


def test_the_notifiers_catch_all_cannot_swallow_the_guard(tmp_path):
    assert notify_desktop.subprocess is not subprocess
    push = health_notify.Push("a", "new", "red", "T", "B")
    with pytest.raises(pytest.fail.Exception):
        health_notify.fan_out(
            push,
            vault=tmp_path,
            now=None,  # type: ignore[arg-type]
            channels=[lambda p: notify_desktop.subprocess.run(["osascript"])],
        )

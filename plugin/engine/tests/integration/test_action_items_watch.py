"""Integration test for scoutctl action-items watch.

Spawns the CLI as a subprocess, mutates the watched file, asserts a
diff line is printed within a small timeout. Marked `slow` so default
unit-test runs skip it; CI runs it explicitly.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from pathlib import Path
from typing import IO

import pytest

pytestmark = pytest.mark.slow


def _scoutctl_path() -> str:
    """Resolve the scoutctl entry point inside the engine venv."""
    venv_bin = Path(__file__).parent.parent.parent / ".venv" / "bin"
    candidate = venv_bin / "scoutctl"
    if candidate.exists():
        return str(candidate)
    return "scoutctl"  # fall back to PATH lookup


def _read_until(stream: IO[str] | None, substring: str, timeout: float) -> str:
    """Read `stream` until `substring` appears or `timeout` elapses.

    Returns the accumulated output. Uses `select` so a wedged subprocess
    doesn't hang the test.
    """
    deadline = time.monotonic() + timeout
    buf: list[str] = []
    assert stream is not None
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], 0.2)
        if stream in ready:
            line = stream.readline()
            if not line:
                break
            buf.append(line)
            if substring in line:
                return "".join(buf)
    return "".join(buf)


def test_watch_emits_completed_line_on_checkbox_flip(tmp_path: Path) -> None:
    daily = tmp_path / "action-items-2026-04-26.md"
    daily.write_text(
        "## In Progress\n\n- [ ] [#A3F7] Submit Lever feedback\n",
    )

    env = {**os.environ, "NO_COLOR": "1"}  # ensure plain output even on TTY-emulating CI
    proc = subprocess.Popen(
        [_scoutctl_path(), "action-items", "watch", str(daily), "--no-color"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        # Wait for the watcher to say it is armed. It reads the file and starts
        # its observer before printing this line, so a write after it is a real
        # change it will see. A fixed sleep lost that race on slow runners (#307):
        # a cold `scoutctl` start takes 0.5-1s under load.
        ready = _read_until(proc.stderr, "Watching", timeout=30.0)
        assert "Watching" in ready, f"watcher never armed; stderr: {ready!r}"

        # Mutate the file: flip the checkbox with write_text, which truncates
        # first. A watcher that diffed the read between the truncate and the
        # write printed "removed" then "added", never "completed" (macOS CI
        # under xdist load); the watcher now waits for the file to settle.
        daily.write_text(
            "## In Progress\n\n- [x] [#A3F7] Submit Lever feedback\n",
        )

        out = _read_until(proc.stdout, "completed", timeout=10.0)
        assert "completed" in out
        assert "removed" not in out
        assert "added" not in out
        assert "[#A3F7]" in out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

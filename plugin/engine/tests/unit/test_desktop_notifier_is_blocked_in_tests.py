"""No test may raise a real desktop notification: the banner lands on the developer's screen.

A test that ran run-outcome.sh with a failing scoutctl and no fake notifier once popped a real
"Scout engine is broken" banner on the maintainer's Mac on every run. tests/conftest.py puts a
recording `osascript` and `notify-send` first on PATH for every test; the `desktop_notifications`
fixture is the file they append to.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

RUN_OUTCOME = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "run-outcome.sh.tmpl"


@pytest.mark.parametrize("name", ["osascript", "notify-send"])
def test_the_notifier_on_path_is_the_recording_stand_in(name: str, desktop_notifications: Path) -> None:
    done = subprocess.run([name, "-", "Title", "Body"], input="display notification", capture_output=True, text=True)

    assert done.returncode == 0
    assert desktop_notifications.read_text(encoding="utf-8").splitlines() == [f"{name} - Title Body"]


def test_a_child_process_gets_the_stand_in_too(desktop_notifications: Path) -> None:
    code = "import subprocess; subprocess.run(['osascript', '-e', 'beep'], check=True)"
    subprocess.run([sys.executable, "-c", code], check=True, env=dict(os.environ))

    assert desktop_notifications.read_text(encoding="utf-8").splitlines() == ["osascript -e beep"]


def test_the_engine_unreachable_floor_reaches_only_the_stand_in(tmp_path: Path, desktop_notifications: Path) -> None:
    """The regression: run-outcome.sh with a failing scoutctl and no fake of its own."""
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    scoutctl = vault / "bin" / "scoutctl"
    scoutctl.parent.mkdir()
    scoutctl.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    scoutctl.chmod(0o755)
    script = vault / "scripts" / "run-outcome.sh"
    script.parent.mkdir()
    script.write_text(
        render_template(
            RUN_OUTCOME.read_text(encoding="utf-8"),
            {"SCOUT_DIR": str(vault), "SCOUTCTL_BIN": str(scoutctl), "INSTANCE_NAME": "Scout"},
        ),
        encoding="utf-8",
    )
    log = vault / ".scout-logs" / "scout-2026-01-05_08-00.log"
    log.write_text("", encoding="utf-8")

    done = subprocess.run(
        ["bash", str(script), "record", "morning-briefing", "0", "0", str(log)], capture_output=True, text=True
    )

    assert done.returncode == 0, done.stderr
    (line,) = desktop_notifications.read_text(encoding="utf-8").splitlines()
    assert line.startswith("osascript - Scout engine is broken ")

"""heartbeat.sh starts `scoutctl self-update run --scheduled` once a day (run-health §7.1, #360).

The template is rendered and run with a stub scoutctl that only logs its argv,
so nothing real is updated, scheduled or notified.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "heartbeat.sh.tmpl"

STUB = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
"""


@pytest.fixture
def heartbeat(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".scout-cache").mkdir(parents=True)
    (vault / ".scout-logs").mkdir()
    stub = tmp_path / "scoutctl"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "heartbeat.sh"
    script.write_text(
        TEMPLATE.read_text(encoding="utf-8")
        .replace("{{SCOUTCTL_BIN}}", str(stub))
        .replace("{{SCOUT_DIR}}", str(vault))
        .replace("{{INSTANCE_NAME}}", "Scout")
    )
    log = tmp_path / "argv.log"

    def run(*args: str) -> list[str]:
        env = {**os.environ, "STUB_LOG": str(log), "SCOUT_DATA_DIR": str(vault)}
        subprocess.run(["/bin/bash", str(script), *args], env=env, check=True, timeout=30)
        # The self-update run is detached: give it a moment to land in the log.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            lines = log.read_text().splitlines() if log.exists() else []
            if any("self-update" in line for line in lines) or not _expect_su[0]:
                break
            time.sleep(0.05)
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return lines

    _expect_su = [True]
    return vault, run, _expect_su


def _marker(vault: Path) -> Path:
    return vault / ".scout-cache" / f"self-update-{datetime.now():%Y-%m-%d}.done"


def test_starts_self_update_when_no_marker(heartbeat):
    vault, run, _ = heartbeat
    lines = run()
    assert "self-update run --scheduled" in lines
    assert "health notify --quiet" in lines
    assert lines[-1] in ("heartbeat run", "self-update run --scheduled")
    assert "heartbeat run" in lines


def test_marker_present_skips_self_update(heartbeat):
    vault, run, expect = heartbeat
    _marker(vault).touch()
    expect[0] = False
    lines = run()
    assert "self-update run --scheduled" not in lines
    assert "heartbeat run" in lines


def test_dry_run_skips_self_update(heartbeat):
    vault, run, expect = heartbeat
    expect[0] = False
    lines = run("--dry-run")
    assert lines == ["heartbeat run --dry-run"]


def test_old_markers_are_pruned(heartbeat):
    vault, run, _ = heartbeat
    old = vault / ".scout-cache" / "self-update-2020-01-01.done"
    old.touch()
    os.utime(old, (0, 0))
    run()
    assert not old.exists()

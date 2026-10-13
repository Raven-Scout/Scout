"""heartbeat.sh starts `scoutctl self-update run --scheduled --detach` once a day (run-health §7.1, #360).

The template is rendered and run with a stub scoutctl that only logs its argv,
so nothing real is updated, scheduled or notified.
"""

from __future__ import annotations

import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "heartbeat.sh.tmpl"

STUB = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" = "$1" ] && exit 3
exit 0
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

    def run(*args: str, **env_extra: str) -> list[str]:
        env = {**os.environ, "STUB_LOG": str(log), "SCOUT_DATA_DIR": str(vault), **env_extra}
        subprocess.run(["/bin/bash", str(script), *args], env=env, check=True, timeout=30)
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return lines

    return vault, run


def _marker(vault: Path) -> Path:
    return vault / ".scout-cache" / f"self-update-{datetime.now():%Y-%m-%d}.done"


def test_starts_a_detached_self_update_when_no_marker(heartbeat):
    vault, run = heartbeat
    lines = run()
    # In order: the sweep, the detached update (which returns at once), then the heartbeat itself.
    assert lines == ["health notify --quiet", "self-update run --scheduled --detach", "heartbeat run"]


def test_marker_present_skips_self_update(heartbeat):
    vault, run = heartbeat
    _marker(vault).touch()
    assert run() == ["health notify --quiet", "heartbeat run"]


def test_a_failing_self_update_never_stops_the_heartbeat(heartbeat):
    vault, run = heartbeat
    assert run(STUB_FAIL="self-update")[-1] == "heartbeat run"


def test_dry_run_skips_self_update(heartbeat):
    vault, run = heartbeat
    assert run("--dry-run") == ["heartbeat run --dry-run"]


def test_old_markers_are_pruned(heartbeat):
    vault, run = heartbeat
    old = vault / ".scout-cache" / "self-update-2020-01-01.done"
    old.touch()
    os.utime(old, (0, 0))
    run()
    assert not old.exists()

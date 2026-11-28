"""git-safe-commit.sh hands a scheduled run's commit to the broker through the scoutctl beside it, on either layout.

On layout v2 the helper lives in scout/scripts/ and the vault has no scripts/ folder: the broker branch used to
exec $VAULT/scripts/scoutctl, so every scheduled run's commit failed with "No such file or directory" (the
first run after the move, 2026-11-27).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


@pytest.mark.parametrize("scripts_dir", ["scripts", "scout/scripts"])
def test_the_broker_branch_runs_the_scoutctl_beside_the_helper(tmp_path: Path, scripts_dir: str) -> None:
    vault = tmp_path / "v"
    d = vault / scripts_dir
    d.mkdir(parents=True)
    helper = d / "git-safe-commit.sh"
    template = (TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl").read_text()
    helper.write_text(render_template(template, {"SCOUT_DIR": str(vault)}))
    helper.chmod(0o755)
    record = tmp_path / "argv"
    fake = d / "scoutctl"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{record}"\n')
    fake.chmod(0o755)
    env = {**os.environ, "SCOUT_RUN_BROKER": "1", "SCOUT_DATA_DIR": str(vault)}
    proc = subprocess.run([str(helper), "notes: a", "notes/a.md"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert record.read_text().splitlines() == ["broker", "commit", "--", "notes: a", "notes/a.md"]

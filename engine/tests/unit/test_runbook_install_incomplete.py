"""The /scout-setup and /scout-update pre-flight blocks recognise an interrupted install.

install() leaves `.scout-state/install-incomplete` in the vault until its
version stamp is written. Without a check for it, /scout-setup saw
`.scout-state/` and sent the user to /scout-update, whose `bootstrap upgrade`
refuses a marked vault — a dead end. These tests run each runbook's Step 0
bash block against a temporary HOME.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _step0_block(runbook: str) -> str:
    text = (REPO / "commands" / runbook).read_text(encoding="utf-8")
    step0 = text[text.index("## Step 0: Pre-flight") :]
    match = re.search(r"```bash\n(.*?)```", step0, re.S)
    assert match is not None, f"no bash block under Step 0 in {runbook}"
    return match.group(1)


def _run(runbook: str, home: Path) -> str:
    # The /scout-update resolver short-circuits on ~/scout-plugin/.git, so the
    # block never shells out to a real `claude`.
    (home / "scout-plugin" / ".git").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-c", _step0_block(runbook)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def _interrupted_vault(home: Path) -> Path:
    vault = home / "Scout"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "install-incomplete").touch()
    return vault


def test_setup_routes_an_interrupted_install_to_resume(tmp_path):
    _interrupted_vault(tmp_path)
    assert _run("scout-setup.md", tmp_path) == "INSTALL_INCOMPLETE"


def test_setup_still_refuses_a_finished_vault(tmp_path):
    (tmp_path / "Scout" / ".scout-state").mkdir(parents=True)
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    assert _run("scout-setup.md", tmp_path) == "VAULT_EXISTS"


def test_update_sends_an_interrupted_install_back_to_setup(tmp_path):
    vault = _interrupted_vault(tmp_path)
    # Even with the config already stamped (crash just before the marker was
    # removed), upgrade would refuse — so the runbook must not reach it.
    (vault / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    assert _run("scout-update.md", tmp_path) == "INSTALL_INCOMPLETE"


def test_update_does_not_flag_a_finished_vault(tmp_path):
    (tmp_path / "Scout" / ".scout-state").mkdir(parents=True)
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    assert _run("scout-update.md", tmp_path) != "INSTALL_INCOMPLETE"

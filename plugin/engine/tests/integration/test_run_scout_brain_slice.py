"""Integration test: run-scout.sh points the session at its mode's slice of SKILL.md (#84).

Renders the runner with the in-tree engine as ``scoutctl`` and a stubbed
``scripts/claude-with-retry.sh`` that saves the prompt it was handed. The
assertions are on that prompt and on the slice the real ``scoutctl brain slice``
wrote, for a briefing slot, a consolidation slot, a manual run, and a run whose
``scoutctl`` is missing (the run must still get the whole SKILL.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scout.scripts.brain_slice import join_marked

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
ENGINE_ROOT = REPO_ROOT / "engine"
TEMPLATES = REPO_ROOT / "templates"
RUN_TIMEOUT_S = 60

SKILL = (
    "\n\n".join(
        [
            "# SKILL",
            *join_marked(
                [
                    ("## Run Modes\nshared rule", None),
                    ("## Slack Query — Briefing Data Gathering\nbriefing-only rule", ("briefing",)),
                    ("## Slack Outbound Scan\nconsolidation-only rule", ("consolidation",)),
                    ("## Slack Notification\nshared wrap rule", None),
                ]
            ),
        ]
    )
    + "\n"
)


def _render(scout_dir: Path, scoutctl: str) -> Path:
    text = (TEMPLATES / "run-scout.sh.tmpl").read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached: the retry wrapper is stubbed
        "{{SCOUTCTL_BIN}}": scoutctl,
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text
    out = scout_dir / "run-scout.sh"
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    lock_lib = scout_dir / "scripts" / "scout-lock.sh"
    lock_lib.parent.mkdir(parents=True, exist_ok=True)
    lock_lib.write_text(
        (TEMPLATES / "scripts" / "scout-lock.sh.tmpl")
        .read_text(encoding="utf-8")
        .replace("{{INSTANCE_NAME}}", "Scout"),
        encoding="utf-8",
    )
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.write_text(
        "#!/bin/bash\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "-p" ]; then printf "%s" "$2" > "$SCOUT_DATA_DIR/prompt.seen"; fi\n'
        "  shift\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return out


def _scoutctl(tmp_path: Path) -> str:
    shim = tmp_path / "bin" / "scoutctl"
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/bash\nexport PYTHONPATH="{ENGINE_ROOT}"\nexec "{sys.executable}" -P -m scout "$@"\n')
    shim.chmod(0o755)
    return str(shim)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    (scout_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return scout_dir


def _run(script: Path, force_mode: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    if force_mode is not None:
        env["SCOUT_FORCE_MODE"] = force_mode
    done = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    assert done.returncode == 0, done.stdout + done.stderr
    return (script.parent / "prompt.seen").read_text(encoding="utf-8")


def _log(vault: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((vault / ".scout-logs").glob("scout-*.log")))


@pytest.mark.parametrize(
    ("slot", "kept", "dropped"),
    [
        ("morning-briefing", "briefing-only rule", "consolidation-only rule"),
        ("weekend-briefing", "briefing-only rule", "consolidation-only rule"),
        ("midday-consolidation", "consolidation-only rule", "briefing-only rule"),
    ],
)
def test_a_scheduled_run_reads_its_modes_slice(tmp_path: Path, vault: Path, slot: str, kept: str, dropped: str) -> None:
    prompt = _run(_render(vault, _scoutctl(tmp_path)), slot)
    sliced = vault / ".scout-cache" / "brain" / "SKILL.md"
    assert f"Step 1: Read {sliced} in full." in prompt
    assert "read-only view" in prompt and "change SKILL.md itself" in prompt
    text = sliced.read_text(encoding="utf-8")
    assert kept in text and dropped not in text
    assert "shared rule" in text and "shared wrap rule" in text and "scout:only" not in text
    assert (vault / "SKILL.md").read_text(encoding="utf-8") == SKILL
    assert "brain slice: SKILL" in _log(vault)


def test_a_manual_run_reads_the_whole_skill_file(tmp_path: Path, vault: Path) -> None:
    stale = vault / ".scout-cache" / "brain" / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("an earlier run's briefing slice", encoding="utf-8")
    prompt = _run(_render(vault, _scoutctl(tmp_path)), None)
    assert f"Step 1: Read {vault}/SKILL.md in full.\n" in prompt
    assert "read-only view" not in prompt
    assert not stale.exists()


def test_a_run_without_scoutctl_still_reads_the_whole_skill_file(vault: Path) -> None:
    prompt = _run(_render(vault, "/nonexistent/scoutctl"), "morning-briefing")
    assert f"Step 1: Read {vault}/SKILL.md in full.\n" in prompt
    assert "read-only view" not in prompt

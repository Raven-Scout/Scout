"""Shared setup for the experiment sandbox and runner tests (Task 10).

A vault holding the anonymized ``summary-drift`` fixture thread with E2 set to ``running``, one private
KB note (Sam's) and one normal one (Priya's), and an env whose HOME is inside the test's temp dir,
so the meter ledger (``broker.owner_dir(env)/lab-meter/``) never touches the real
``~/.local/state/scout``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

from scout.lab import paths, runner

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
FAKE = FIXTURES / "fake-claude.sh"
SLUG, EID = "summary-drift", "E2"
PRIVATE_NOTE = "knowledge-base/people/sam.md"
NORMAL_NOTE = "knowledge-base/people/priya.md"
# How a script inside the sandbox reaches the meter (ruling R5): this interpreter, not a scoutctl on PATH.
PY = shlex.quote(sys.executable)
CALL = f"{PY} -m scout lab experiment call --prompt-file p.txt"


def make_vault(tmp_path: Path, *, status: str = "running", visibility: str = "normal") -> Path:
    vault = tmp_path / "vault"
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(vault, SLUG))
    note = paths.experiment_note(vault, SLUG, EID)
    assert note is not None
    text = note.read_text(encoding="utf-8")
    text = text.replace("status: planned", f"status: {status}", 1).replace(
        "visibility: normal", f"visibility: {visibility}", 1
    )
    note.write_text(text, encoding="utf-8")
    (vault / "knowledge-base" / "people").mkdir(parents=True)
    (vault / PRIVATE_NOTE).write_text("---\ntype: person\nvisibility: private\n---\n# Sam\nCANARY-PRIV-LAB\n", "utf-8")
    (vault / NORMAL_NOTE).write_text("---\ntype: person\n---\n# Priya\nnormal-note-text\n", "utf-8")
    return vault


def make_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "en_US.UTF-8"}


def exp_dir(vault: Path) -> Path:
    note = paths.experiment_note(vault, SLUG, EID)
    assert note is not None
    return paths.experiment_dir(note)


def run(
    vault: Path, tmp_path: Path, script: str, *, cap: float = 1.0, timeout_s: float = 60, **limits: int
) -> runner.RunOutcome:
    """``script`` under ``/bin/sh -c``; ``limits`` (``max_processes``, ``max_total_bytes``) go to the runner."""
    return runner.run_experiment(
        vault,
        SLUG,
        EID,
        ["/bin/sh", "-c", script],
        cap_usd=cap,
        timeout_s=timeout_s,
        env=make_env(tmp_path),
        claude_argv=(str(FAKE), str(tmp_path / "calls.log")),
        **limits,  # type: ignore[arg-type]
    )

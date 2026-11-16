"""write-session-cost.sh records the run's model in usage-tracker.jsonl when it is a plain name."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "write-session-cost.sh.tmpl"


def _install(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "Scout"
    (vault / "scripts").mkdir(parents=True)
    script = vault / "scripts" / "write-session-cost.sh"
    text = TEMPLATE.read_text(encoding="utf-8").replace("{{SCOUT_DIR}}", str(vault))
    text = text.replace("{{INSTANCE_NAME}}", "Scout")
    script.write_text(text, encoding="utf-8")
    script.chmod(0o755)
    return script, vault / ".scout-logs" / "usage-tracker.jsonl"


def _write(script: Path, model: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODEL", "CLAUDE_CODE_SESSION_ID")}
    if model is not None:
        env["SCOUT_MODEL"] = model
    return subprocess.run(
        [str(script), "midday-consolidation", "25", "3.10", "0", "session"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _rows(tracker: Path) -> list[dict]:
    return [json.loads(line) for line in tracker.read_text(encoding="utf-8").splitlines()]


def test_the_model_is_recorded(tmp_path: Path) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, "sonnet").returncode == 0
    (row,) = _rows(tracker)
    assert row["model"] == "sonnet"
    assert row["budget_spent"] == 3.10


def test_no_model_leaves_the_row_as_before(tmp_path: Path) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, None).returncode == 0
    (row,) = _rows(tracker)
    assert set(row) == {"ts", "ts_local", "type", "budget_cap", "budget_spent", "exit_code", "source"}


@pytest.mark.parametrize("unsafe", ['sonnet","x":"y', "two words", "Opus"])
def test_an_unsafe_model_is_left_out_and_the_line_stays_json(tmp_path: Path, unsafe: str) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, unsafe).returncode == 0
    (row,) = _rows(tracker)  # json.loads would raise on a broken line
    assert "model" not in row and "x" not in row

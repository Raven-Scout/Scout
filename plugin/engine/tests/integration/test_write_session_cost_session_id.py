"""write-session-cost.sh records the Claude Code session id, so a run that writes its row twice is visible as one id on two rows."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "write-session-cost.sh.tmpl"
SID = "0f0e0d0c-1234-4abc-8def-0123456789ab"


def _install(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "Scout"
    (vault / "scripts").mkdir(parents=True)
    script = vault / "scripts" / "write-session-cost.sh"
    text = TEMPLATE.read_text(encoding="utf-8").replace("{{SCOUT_DIR}}", str(vault))
    text = text.replace("{{INSTANCE_NAME}}", "Scout")
    script.write_text(text, encoding="utf-8")
    script.chmod(0o755)
    return script, vault / ".scout-logs" / "usage-tracker.jsonl"


def _write(script: Path, session_id: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODEL", "CLAUDE_CODE_SESSION_ID")}
    if session_id is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session_id
    return subprocess.run(
        [str(script), "dreaming", "50", "3.10", "0", "session"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _rows(tracker: Path) -> list[dict]:
    return [json.loads(line) for line in tracker.read_text(encoding="utf-8").splitlines()]


def test_the_session_id_is_recorded(tmp_path: Path) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, SID).returncode == 0
    (row,) = _rows(tracker)
    assert row["session_id"] == SID


def test_two_writes_from_one_session_share_the_id(tmp_path: Path) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, SID).returncode == 0
    assert _write(script, SID).returncode == 0
    rows = _rows(tracker)
    assert [r["session_id"] for r in rows] == [SID, SID]


def test_no_session_leaves_the_key_out(tmp_path: Path) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, None).returncode == 0
    (row,) = _rows(tracker)
    assert "session_id" not in row


@pytest.mark.parametrize("unsafe", ['x","budget_spent":0,"y":"', "not-a-uuid", SID.upper()])
def test_an_unsafe_id_is_left_out_and_the_line_stays_json(tmp_path: Path, unsafe: str) -> None:
    script, tracker = _install(tmp_path)
    assert _write(script, unsafe).returncode == 0
    (row,) = _rows(tracker)  # json.loads would raise on a broken line
    assert "session_id" not in row
    assert row["budget_spent"] == 3.10

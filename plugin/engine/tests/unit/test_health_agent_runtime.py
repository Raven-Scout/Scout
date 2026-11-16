"""Run health reports runs skipped because their agent runtime was refused (spec 2026-11-13 §8.3 rule 5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import health

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _row(at: datetime, *, outcome: str | None = None, code: int = 0) -> dict[str, object]:
    started = int((at - timedelta(minutes=5)).timestamp())
    row: dict[str, object] = {
        "slot": "dreaming-nightly",
        "mode": "dreaming",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "duration_s": 300,
        "exit_code": code,
        "failure_class": "ok",
        "log": f"dreaming-{started}.log",
    }
    if outcome:
        row["outcome"] = outcome
    return row


def _findings(tmp_path: Path, rows: list[dict[str, object]]) -> dict[str, str]:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return {f.id: f.severity for f in health.evaluate(v, now=NOW).findings}


def test_a_refused_runtime_is_red(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(hours=3)), _row(NOW - timedelta(hours=1), outcome="skipped_runtime")]
    assert _findings(tmp_path, rows).get("agent_runtime.refused") == "red"


def test_a_run_after_the_refusal_clears_it(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(hours=3), outcome="skipped_runtime"), _row(NOW - timedelta(hours=1))]
    assert "agent_runtime.refused" not in _findings(tmp_path, rows)


def test_other_skips_dont_end_the_streak(tmp_path: Path) -> None:
    rows = [
        _row(NOW - timedelta(hours=3), outcome="skipped_runtime"),
        _row(NOW - timedelta(hours=1), outcome="skipped_lock"),
    ]
    assert _findings(tmp_path, rows).get("agent_runtime.refused") == "red"
    assert "skipped_runtime" in health.SKIP_OUTCOMES

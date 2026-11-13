"""Run health reads a gate-probe skip (spec 2026-11-13 §3.5, plan Task 1a.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import health

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(at: datetime, *, code: int = 0, klass: str = "ok", **extra: object) -> dict[str, object]:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {
        "slot": "morning-briefing",
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "duration_s": 300,
        "exit_code": code,
        "failure_class": klass,
        "log": f"scout-{started}.log",
        **extra,
    }


def _ledger(vault: Path, rows: list[dict[str, object]]) -> None:
    text = "\n".join(json.dumps(r) for r in rows) + "\n"
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(text, encoding="utf-8")


def _ids(report: health.RunHealth) -> dict[str, str]:
    return {f.id: f.severity for f in report.findings}


def test_a_gates_skip_reports_gates_not_loaded_red(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=5)), _row(NOW - timedelta(hours=1), outcome="skipped_gates")])
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"gates.not_loaded": "red"}
    (finding,) = report.findings
    assert "scoutctl gates probe" in finding.fix


def test_a_gates_skip_is_a_skip_not_a_failure(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), outcome="skipped_gates")])
    assert "runs.failing" not in _ids(health.evaluate(v, now=NOW))
    assert "skipped_gates" in health.SKIP_OUTCOMES


def test_a_run_after_a_gates_skip_clears_the_finding(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=3), outcome="skipped_gates"), _row(NOW - timedelta(hours=1))])
    assert "gates.not_loaded" not in _ids(health.evaluate(v, now=NOW))


def test_other_skips_after_a_gates_skip_keep_the_finding(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=3), outcome="skipped_gates"),
            _row(NOW - timedelta(hours=1), outcome="skipped_lock"),
        ],
    )
    assert "gates.not_loaded" in _ids(health.evaluate(v, now=NOW))

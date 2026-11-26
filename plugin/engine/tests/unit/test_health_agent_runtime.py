"""Run health reports runs skipped because their agent runtime was refused (spec 2026-11-13 §8.3 rule 5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import health

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _row(
    at: datetime,
    *,
    outcome: str | None = None,
    code: int = 0,
    slot: str = "dreaming-nightly",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    started = int((at - timedelta(minutes=5)).timestamp())
    row: dict[str, object] = {
        **(extra or {}),
        "slot": slot,
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


# -- agent_runtime.fallback (#652 S7) -----------------------------------------------------------------------------

_FELL: dict[str, object] = {"agent_runtime": "claude-code", "agent_runtime_fallback": "resolve_failed"}


def test_a_run_that_fell_back_to_claude_code_is_yellow(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(hours=1), extra=_FELL)]
    assert _findings(tmp_path, rows).get("agent_runtime.fallback") == "yellow"


def test_the_finding_names_each_slot_whose_last_run_fell_back(tmp_path: Path) -> None:
    rows = [
        _row(NOW - timedelta(hours=3), slot="night", extra=_FELL),
        _row(NOW - timedelta(hours=2), slot="noon", extra=_FELL),
        _row(NOW - timedelta(hours=1), slot="dawn", extra={"agent_runtime": "claude-code"}),
    ]
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    found = {f.id: f for f in health.evaluate(v, now=NOW).findings}["agent_runtime.fallback"]
    assert "night, noon" in found.summary and "dawn" not in found.summary
    assert found.since == datetime.fromtimestamp(rows[0]["finished_at"], UTC)  # type: ignore[arg-type]


def test_a_later_run_on_the_set_runtime_clears_it(tmp_path: Path) -> None:
    rows = [
        _row(NOW - timedelta(hours=3), extra=_FELL),
        _row(NOW - timedelta(hours=1), extra={"agent_runtime": "claude-code"}),
    ]
    assert "agent_runtime.fallback" not in _findings(tmp_path, rows)


def test_a_skip_before_the_runtime_was_resolved_doesnt_clear_it(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(hours=3), extra=_FELL), _row(NOW - timedelta(hours=1), outcome="skipped_lock")]
    assert _findings(tmp_path, rows).get("agent_runtime.fallback") == "yellow"


def test_an_unknown_fallback_value_is_ignored(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(hours=1), extra={"agent_runtime": "claude-code", "agent_runtime_fallback": "because"})]
    assert "agent_runtime.fallback" not in _findings(tmp_path, rows)


def test_the_protection_card_leaves_the_fallback_to_the_health_panel(tmp_path: Path) -> None:
    from scout import run_protection

    v = tmp_path / "Scout"
    rows = [health._parse_row(_row(NOW - timedelta(hours=1), extra=_FELL))]
    assert [r for r in rows if r] and not run_protection.ledger_problems(v, [r for r in rows if r], now=NOW)

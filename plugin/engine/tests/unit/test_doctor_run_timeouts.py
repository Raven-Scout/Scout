"""The doctor compares each slot's awake run length with its run timeout (spec 2026-11-13 §8.6, task 1.8)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.bootstrap_doctor import _check_run_timeouts
from tests._agent_runtimes import DummyRuntime, registered

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

SLOT = """\
  {key}:
    type: {kind}
    runner: run-{runner}.sh
    fires_at_local: "02:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 60
{extra}"""


@pytest.fixture(autouse=True)
def _dummy_runtime() -> Iterator[None]:
    with registered(DummyRuntime("dummy")):
        yield


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    slots = [("night", "dreaming", ""), ("web", "research", ""), ("other", "dreaming", "    agent_runtime: dummy\n")]
    body = "".join(SLOT.format(key=k, kind=kind, runner=kind, extra=e) for k, kind, e in slots)
    (v / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")
    return v


def _rows(vault: Path, slot: str, minutes: list[int], *, days_ago: float = 1, outcome: str = "ok") -> None:
    finished = int((NOW - timedelta(days=days_ago)).timestamp())
    with (vault / ".scout-logs" / "run-outcomes.jsonl").open("a", encoding="utf-8") as f:
        for m in minutes:
            f.write(json.dumps({"slot": slot, "outcome": outcome, "finished_at": finished, "awake_s": m * 60}) + "\n")


def test_silent_without_awake_lengths(vault: Path) -> None:
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(
        json.dumps({"slot": "night", "outcome": "ok", "finished_at": int(NOW.timestamp()), "duration_s": 99999}) + "\n",
        encoding="utf-8",
    )
    assert _check_run_timeouts(vault=vault, now=NOW) == ([], [])


def test_a_slot_running_past_its_timeout_is_a_warning(vault: Path) -> None:
    _rows(vault, "other", [20, 30, 40, 50, 60])  # p90 60 min against the 45-min default
    warnings, _ = _check_run_timeouts(vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "'other'" in warnings[0] and "60 min awake" in warnings[0] and "45-min run timeout" in warnings[0]
    assert "set-limit dummy" in warnings[0]


def test_a_slot_that_fits_says_nothing(vault: Path) -> None:
    _rows(vault, "other", [10, 20, 30, 40, 44])
    assert _check_run_timeouts(vault=vault, now=NOW)[0] == []


def test_claude_slots_get_a_readiness_note_while_the_timeout_is_off(vault: Path) -> None:
    _rows(vault, "night", [20, 30, 40, 50, 60])
    _rows(vault, "web", [30, 40, 50, 60, 70])
    warnings, notes = _check_run_timeouts(vault=vault, now=NOW)
    assert warnings == []
    assert len(notes) == 1
    assert "night 60 min p90 vs 45 (would be stopped)" in notes[0]
    assert "web 70 min p90 vs 90 (fits)" in notes[0]


def test_too_few_runs_old_runs_and_skips_are_left_out(vault: Path) -> None:
    _rows(vault, "night", [300, 300, 300, 300])  # four runs only
    _rows(vault, "web", [300] * 5, days_ago=40)  # outside the window
    _rows(vault, "web", [300] * 5, outcome="skipped_lock")
    assert _check_run_timeouts(vault=vault, now=NOW) == ([], [])


def test_a_run_stopped_at_its_timeout_is_a_warning(vault: Path) -> None:
    """A cut-off run ran at least its timeout, which the 90th percentile under-counts (review of #653)."""
    finished = int((NOW - timedelta(days=1)).timestamp())
    with (vault / ".scout-logs" / "run-outcomes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"slot": "other", "outcome": "failed", "finished_at": finished, "failure_class": "timeout"}))
        f.write("\n")
    warnings, _ = _check_run_timeouts(vault=vault, now=NOW)
    assert len(warnings) == 1 and "'other': 1 run(s)" in warnings[0] and "stopped at their run timeout" in warnings[0]


def test_ignored_config_values_are_a_warning(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    claude-code:\n      run_timeout_minutes: 5\n", encoding="utf-8"
    )
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text("", encoding="utf-8")
    warnings, _ = _check_run_timeouts(vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "budget.per_runtime.claude-code: ignored run_timeout_minutes: 5" in warnings[0]
    assert "only the owner turns it on" in warnings[0]


def test_runs_without_an_awake_length_say_why(vault: Path) -> None:
    finished = int((NOW - timedelta(days=1)).timestamp())
    with (vault / ".scout-logs" / "run-outcomes.jsonl").open("a", encoding="utf-8") as f:
        for _ in range(3):
            f.write(
                json.dumps({"slot": "night", "outcome": "ok", "finished_at": finished, "agent_runtime": "claude-code"})
            )
            f.write("\n")
    _, notes = _check_run_timeouts(vault=vault, now=NOW)
    assert len(notes) == 1 and "3 recent run(s) recorded no awake length" in notes[0] and "python3" in notes[0]

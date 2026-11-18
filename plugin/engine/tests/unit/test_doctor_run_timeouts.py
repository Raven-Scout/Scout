"""The doctor compares each slot's awake run length with its run timeout (spec 2026-11-13 §8.6, task 1.8)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.bootstrap_doctor import _check_run_limits, _check_run_timeouts
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


def test_ignored_config_values_are_a_warning_on_a_fresh_vault(vault: Path) -> None:
    """No run-outcomes.jsonl yet (a fresh vault), and the warning still shows (review of #654)."""
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    claude-code:\n      run_timeout_minutes: 5\n", encoding="utf-8"
    )
    assert not (vault / ".scout-logs" / "run-outcomes.jsonl").exists()
    warnings = _check_run_limits(vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "budget.per_runtime.claude-code: ignored run_timeout_minutes: 5" in warnings[0]
    assert "only the owner turns it on" in warnings[0] and "slots: night, web" in warnings[0]
    assert _check_run_timeouts(vault=vault, now=NOW) == ([], [])  # the timeouts check doesn't repeat it


def test_ignored_config_values_are_judged_per_slot(vault: Path) -> None:
    """60 minutes is looser than a dreaming slot's 45 but narrows a research slot's 90: named for the first only."""
    slots = [("other", "dreaming", "    agent_runtime: dummy\n"), ("dig", "research", "    agent_runtime: dummy\n")]
    body = "".join(SLOT.format(key=k, kind=kind, runner=kind, extra=e) for k, kind, e in slots)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    dummy:\n      run_timeout_minutes: 60\n", encoding="utf-8"
    )
    warnings = _check_run_limits(vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "run_timeout_minutes: 60 (looser than 45) (slots: other)" in warnings[0]
    assert "dig" not in warnings[0]


@pytest.mark.parametrize("bad", ['"²"', '"①"', "0.5", '"0.0"', '"5m"'])
def test_a_value_that_isnt_a_whole_number_never_breaks_the_doctor(vault: Path, bad: str) -> None:
    """A run can write any value into scout-config.yaml; the doctor names it instead of crashing (review of #654)."""
    (vault / "scout-config.yaml").write_text(
        f"budget:\n  per_runtime:\n    dummy:\n      run_timeout_minutes: {bad}\n      max_tokens_per_day: {bad}\n",
        encoding="utf-8",
    )
    _rows(vault, "other", [20, 30, 40, 50, 60])
    warnings = _check_run_limits(vault=vault, now=NOW)
    assert len(warnings) == 1 and "not a whole number of minutes" in warnings[0]
    assert "not a whole number of tokens" in warnings[0]
    assert any("45-min run timeout" in w for w in _check_run_timeouts(vault=vault, now=NOW)[0])


def test_future_dated_usage_rows_are_a_warning(vault: Path) -> None:
    rows = [
        {"source": "runner", "ts": "2099-01-01T00:00:00Z", "agent_runtime": "dummy", "tokens": 10**15},
        {"source": "runner", "ts": (NOW + timedelta(minutes=2)).isoformat(), "tokens": 1},  # clock skew: fine
        {"source": "runner", "ts": NOW.isoformat(), "tokens": 1},
    ]
    (vault / ".scout-logs" / "usage-tracker.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    warnings = _check_run_limits(vault=vault, now=NOW)
    assert len(warnings) == 1 and "1 row(s) dated in the future" in warnings[0]


def test_run_limits_are_silent_by_default(vault: Path) -> None:
    assert _check_run_limits(vault=vault, now=NOW) == []


def test_run_limits_skip_unknown_runtimes_and_never_break_the_doctor(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.agent_runtimes import limits
    from scout.scripts import budget_check

    body = SLOT.format(key="odd", kind="dreaming", runner="dreaming", extra="    agent_runtime: no-such-runtime\n")
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")
    # The schedule itself refuses an unknown runtime; the doctor says so instead of crashing.
    (only,) = _check_run_limits(vault=vault, now=NOW)
    assert only.startswith("could not check scout-config.yaml run limits:") and "no-such-runtime" in only

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(limits, "limits_for", boom)
    monkeypatch.setattr(budget_check, "iter_tracker_rows", boom)
    (vault / ".scout-logs" / "usage-tracker.jsonl").write_text("{}\n", encoding="utf-8")
    body = SLOT.format(key="other", kind="dreaming", runner="dreaming", extra="    agent_runtime: dummy\n")
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")
    assert _check_run_limits(vault=vault, now=NOW) == ["could not check scout-config.yaml run limits: boom"]


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

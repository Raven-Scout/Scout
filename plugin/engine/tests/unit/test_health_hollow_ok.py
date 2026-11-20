"""runs.hollow_ok: a run that exits 0 in under a minute with nothing committed did no work.

The v0.18.19 KB gate's morning-consolidation saw one Bash call denied, decided "Bash is now denied in this
session", and ended after 28 s with exit 0 and no commit. Every other finding read that run as healthy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("user:\n  timezone: UTC\n", encoding="utf-8")
    return v


def _row(at: datetime, slot: str = "morning-consolidation", *, seconds: int | None = 28, commits: int | None = 0,
         code: int = 0, **extra) -> dict:  # fmt: skip
    started = int(at.timestamp()) - (seconds or 0)
    row = {
        "slot": slot,
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "exit_code": code,
        "failure_class": "ok" if code == 0 else "unknown",
        "log": f"scout-{started}.log",
        "commits_in_window": commits,
        **extra,
    }
    if seconds is not None:
        row["duration_s"] = seconds
    return row


def _ledger(vault: Path, rows: list[dict]) -> None:
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _hollow(vault: Path) -> health.Finding | None:
    report = health.evaluate(vault, now=NOW)
    return next((f for f in report.findings if f.id == health.HOLLOW_FINDING_ID), None)


def test_a_short_ok_run_with_no_commits_is_yellow(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=3), seconds=900, commits=2), _row(NOW - timedelta(minutes=5))])
    (v / ".scout-logs" / _row(NOW - timedelta(minutes=5))["log"]).write_text("done\n", encoding="utf-8")
    report = health.evaluate(v, now=NOW)
    assert report.severity == "yellow"
    found = _hollow(v)
    assert found is not None
    assert found.summary == (
        "The last run (morning-consolidation) ended ok after 28 s with no commits in its window: "
        "it probably stopped before doing its work."
    )
    assert found.since == NOW - timedelta(minutes=5)
    assert found.log and found.log.endswith(_row(NOW - timedelta(minutes=5))["log"])
    assert "denied tool call" in found.fix and ".scout-logs/scout-" in found.fix


@pytest.mark.parametrize(
    "newest",
    [
        {"seconds": 60},  # a minute or more is a run that worked, whatever it committed
        {"commits": 1},  # it committed
        {"commits": None},  # the window wasn't measured: say nothing
        {"seconds": None},  # an older runner's row has no length
        {"code": 1},  # a failure: runs.failing reports it
    ],
    ids=["one-minute", "committed", "unmeasured-commits", "no-duration", "failed"],
)
def test_runs_that_are_not_hollow(tmp_path, newest):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(minutes=5), **newest)])
    assert _hollow(v) is None


def test_a_later_run_that_commits_clears_it(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=2)), _row(NOW - timedelta(minutes=5), seconds=600, commits=1)])
    assert _hollow(v) is None


def test_skips_after_it_do_not_hide_it(tmp_path):
    v = _vault(tmp_path)
    skip = _row(NOW - timedelta(minutes=1), seconds=1, outcome="skipped_lock")
    _ledger(v, [_row(NOW - timedelta(minutes=5)), skip])
    assert _hollow(v) is not None

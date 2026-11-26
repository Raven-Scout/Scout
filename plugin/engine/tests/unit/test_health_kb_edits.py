"""Run health counts the KB edits runs still make with interpreter scripts (#824's `kb_edit` record), per run,
so the owner can see them reach 0 before the read gate's DENY_INTERPRETER_KB_EDITS switch goes on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health
from scout.hooks import kb_read_gate

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _row(at: datetime, *, outcome: str | None = None) -> dict[str, object]:
    started = int((at - timedelta(minutes=30)).timestamp())
    row: dict[str, object] = {
        "slot": "dreaming-nightly",
        "mode": "dreaming",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "duration_s": 1800,
        "exit_code": 0,
        "failure_class": "ok",
        "log": f"dreaming-{started}.log",
    }
    if outcome:
        row["outcome"] = outcome
    return row


def _event(at: datetime, *, kind: str = health.KB_EDIT_EVENT, payload: dict[str, object] | None = None) -> str:
    ts = at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({"id": "x", "ts": ts, "kind": kind, "source": "hook:kb-read-gate", "payload": payload or {}})


def _vault(tmp_path: Path, rows: list[dict[str, object]], events: list[tuple[datetime, str]]) -> Path:
    v = tmp_path / "Scout"
    logs = v / ".scout-logs"
    logs.mkdir(parents=True)
    (logs / "run-outcomes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for at, line in events:
        with (logs / f"schedule-events-{at.date().isoformat()}.jsonl").open("a") as fh:
            fh.write(line + "\n")
    return v


def _finding(v: Path) -> health.Finding | None:
    return next((f for f in health.evaluate(v, now=NOW).findings if f.id == "kb.interpreter_edits"), None)


RUNS = [NOW - timedelta(hours=h) for h in (50, 26, 2)]  # finished times, oldest first


def test_no_records_no_line(tmp_path: Path) -> None:
    assert _finding(_vault(tmp_path, [_row(at) for at in RUNS], [])) is None


def test_counts_per_run_newest_last(tmp_path: Path) -> None:
    events = [(RUNS[0] - timedelta(minutes=5), _event(RUNS[0] - timedelta(minutes=5)))]
    events += [(RUNS[2] - timedelta(minutes=m), _event(RUNS[2] - timedelta(minutes=m))) for m in (1, 2, 3)]
    found = _finding(_vault(tmp_path, [_row(at) for at in RUNS], events))
    assert found is not None and found.severity == "note" and found.source == "events"
    assert "4 edits in the last 3 runs (2 with any; per run, newest last: 1, 0, 3)" in found.summary
    assert "DENY_INTERPRETER_KB_EDITS" in found.fix


def test_the_record_as_an_unresolved_read_category_counts_too(tmp_path: Path) -> None:
    at = RUNS[2] - timedelta(minutes=1)
    other = _event(at, kind="kb_read_gate.unresolved_read", payload={"categories": ["program_path"]})
    mine = _event(at, kind="kb_read_gate.unresolved_read", payload={"categories": ["kb_edit", "program_path"]})
    found = _finding(_vault(tmp_path, [_row(at) for at in RUNS], [(at, other), (at, mine)]))
    assert found is not None and "1 edit in the last 3 runs" in found.summary


def test_skipped_runs_dont_count_and_records_outside_a_run_dont_either(tmp_path: Path) -> None:
    rows = [_row(RUNS[0]), _row(RUNS[1], outcome="skipped_lock"), _row(RUNS[2])]
    between = RUNS[1] - timedelta(minutes=10)
    found = _finding(_vault(tmp_path, rows, [(between, _event(between))]))
    assert found is not None and "No interpreter KB edits in the last 2 runs" in found.summary


def test_zero_in_the_recent_runs_says_so_until_the_quiet_window_ends(tmp_path: Path) -> None:
    old = NOW - timedelta(days=5)
    found = _finding(_vault(tmp_path, [_row(at) for at in RUNS], [(old, _event(old))]))
    assert found is not None
    assert "No interpreter KB edits in the last 3 runs; the last was 5 d ago." in found.summary
    stale = NOW - health.KB_EDIT_QUIET - timedelta(hours=1)
    older = NOW - timedelta(days=20)  # in an event log older than the window: not even opened
    events = [(stale, _event(stale)), (older, _event(older))]
    assert _finding(_vault(tmp_path / "b", [_row(at) for at in RUNS], events)) is None


def test_one_run_and_no_runs(tmp_path: Path) -> None:
    at = RUNS[2] - timedelta(minutes=1)
    one = _finding(_vault(tmp_path / "one", [_row(RUNS[2])], [(at, _event(at))]))
    assert one is not None and "1 edit in the last run" in one.summary
    none = _finding(_vault(tmp_path / "none", [], [(at, _event(at))]))
    assert none is not None and "1 time in 14 days" in none.summary


def test_bad_lines_are_skipped(tmp_path: Path) -> None:
    at = RUNS[2] - timedelta(minutes=1)
    junk = [(at, "not json kb_edit"), (at, json.dumps(["kb_edit"])), (at, json.dumps({"kind": "kb_edit", "ts": 1}))]
    assert _finding(_vault(tmp_path, [_row(r) for r in RUNS], junk)) is None


def test_no_line_once_the_switch_denies_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_read_gate, "DENY_INTERPRETER_KB_EDITS", True, raising=False)
    at = RUNS[2] - timedelta(minutes=1)
    assert _finding(_vault(tmp_path, [_row(r) for r in RUNS], [(at, _event(at))])) is None

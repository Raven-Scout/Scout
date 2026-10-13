"""The connector-call ledger's bounds: daily cap, retention, locking (#66)."""

from __future__ import annotations

import json
import multiprocessing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout import connector_calls
from scout.scripts import connector_health_report as chr_mod

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _line(i: int, pad: int = 0) -> str:
    return json.dumps({"ts": "2026-09-30T12:00:00Z", "session_id": f"s{i}", "err": "x" * pad}) + "\n"


def test_retention_covers_the_reports_window() -> None:
    """The report reads 14 days; keeping fewer would silently shrink it."""
    assert connector_calls.RETAIN_DAYS > chr_mod.DEFAULT_WINDOW_DAYS + 1
    assert chr_mod.DEFAULT_RETAIN_DAYS == connector_calls.RETAIN_DAYS


def test_day_path(tmp_path: Path) -> None:
    assert connector_calls.day_path(tmp_path, "2026-09-30").name == "connector-calls-2026-09-30.jsonl"


def test_append_writes_every_row_under_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    assert connector_calls.append_rows(path, [_line(1), _line(2)]) == 2
    assert connector_calls.append_rows(path, []) == 0
    assert path.read_text().splitlines() == [_line(1).strip(), _line(2).strip()]


def test_rows_past_the_daily_cap_are_dropped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    one = len(_line(0).encode())
    assert connector_calls.append_rows(path, [_line(i) for i in range(3)], max_bytes=3 * one) == 3
    assert "cap" not in capsys.readouterr().err
    # Full: nothing more fits, not even a short row.
    assert connector_calls.append_rows(path, [_line(3), _line(4)], max_bytes=3 * one) == 0
    err = capsys.readouterr().err
    assert "daily cap" in err and "dropped 2 row(s)" in err
    assert len(path.read_text().splitlines()) == 3
    assert path.stat().st_size == 3 * one


def test_a_batch_is_cut_at_the_cap_not_rejected_whole(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    one = len(_line(0).encode())
    assert connector_calls.append_rows(path, [_line(i) for i in range(5)], max_bytes=2 * one + 1) == 2


def test_the_cap_counts_bytes_not_characters(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    wide = "é" * 10 + "\n"  # 21 bytes, 11 characters
    assert connector_calls.append_rows(path, [wide, wide], max_bytes=30) == 1


def test_the_first_row_of_a_day_prunes_old_days(tmp_path: Path) -> None:
    old = tmp_path / "connector-calls-2020-01-01.jsonl"
    old.write_text(_line(0))
    keep = tmp_path / "connector-calls-backup.jsonl"
    keep.write_text(_line(0))
    today = tmp_path / "connector-calls-2099-01-01.jsonl"
    connector_calls.append_rows(today, [_line(1)])
    assert not old.exists()
    assert keep.exists()
    # Later appends to an existing day don't scan the directory again.
    old.write_text(_line(0))
    connector_calls.append_rows(today, [_line(2)])
    assert old.exists()


def test_prune_keeps_the_retention_window(tmp_path: Path) -> None:
    for day in ("2026-08-30", "2026-08-31", "2026-09-01", "2026-09-30"):
        (tmp_path / f"connector-calls-{day}.jsonl").write_text(_line(0))
    removed = connector_calls.prune(tmp_path, now=NOW)  # cutoff 2026-08-31 12:00Z
    assert sorted(p.name for p in removed) == ["connector-calls-2026-08-30.jsonl", "connector-calls-2026-08-31.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "connector-calls-2026-09-01.jsonl",
        "connector-calls-2026-09-30.jsonl",
    ]


def test_prune_tolerates_a_file_that_vanishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "connector-calls-2020-01-01.jsonl").write_text(_line(0))

    def gone(_path: object) -> None:
        raise FileNotFoundError("another writer pruned it first")

    monkeypatch.setattr(connector_calls.os, "remove", gone)
    assert connector_calls.prune(tmp_path, now=NOW) == []


def _writer(path: str, worker: int, rows: int) -> None:
    for i in range(rows):
        # Rows bigger than PIPE_BUF, so an unlocked O_APPEND write could interleave.
        connector_calls.append_rows(Path(path), [_line(worker * 1000 + i, pad=6000)], max_bytes=40 * 6100)


@pytest.mark.concurrency
def test_concurrent_writers_never_interleave_or_overshoot_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(path), w, 15)) for w in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    lines = path.read_text().splitlines()
    assert all(json.loads(line)["err"] == "x" * 6000 for line in lines)  # every row whole
    assert len(lines) == 40  # 60 attempted; the cap holds exactly 40 of this size
    assert path.stat().st_size <= 40 * 6100

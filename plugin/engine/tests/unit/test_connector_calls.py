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


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_cap_writes_one_marker_then_closes_the_day(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    one = len(_line(0).encode())
    assert connector_calls.append_rows(path, [_line(i) for i in range(3)], max_bytes=3 * one) == 3
    assert "cap" not in capsys.readouterr().err
    # Full: the first row that doesn't fit becomes the marker; nothing else lands.
    assert connector_calls.append_rows(path, [_line(3), _line(4)], max_bytes=3 * one) == 0
    err = capsys.readouterr().err
    assert "daily cap" in err and "dropped 2 row(s)" in err
    rows = _rows(path)
    assert [r["session_id"] for r in rows] == ["s0", "s1", "s2", "s3"]
    marker = rows[-1]
    assert marker["connector"] == connector_calls.CAP_CONNECTOR
    assert marker["error"] is True and "daily cap" in marker["err"]
    size = path.stat().st_size
    # Closed: later appends drop everything and write no second marker.
    assert connector_calls.append_rows(path, [_line(5)], max_bytes=3 * one) == 0
    assert path.stat().st_size == size


def test_a_closed_day_stays_closed_even_with_room_left(tmp_path: Path) -> None:
    """A marker written short of the cap still closes the file: small rows don't sneak in after it."""
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    big = _line(0, pad=900)
    assert connector_calls.append_rows(path, [big], max_bytes=800) == 0  # marker only, < 800 bytes
    assert path.stat().st_size < 800
    assert connector_calls.append_rows(path, ["{}\n"], max_bytes=800) == 0
    assert [r["connector"] for r in _rows(path)] == [connector_calls.CAP_CONNECTOR]


def test_the_marker_takes_the_dropped_rows_run(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    row = json.dumps({"ts": "2026-09-30T12:00:00Z", "session_id": "run-7", "mode": "research", "x": "y" * 300})
    connector_calls.append_rows(path, [row + "\n"], max_bytes=10)
    marker = _rows(path)[0]
    assert (marker["session_id"], marker["mode"]) == ("run-7", "research")


def test_an_unparseable_dropped_row_still_gets_a_visible_marker(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    connector_calls.append_rows(path, ["not json " * 10 + "\n"], max_bytes=10)
    marker = _rows(path)[0]
    # Not "interactive"/"unknown": the report keeps it.
    assert (marker["session_id"], marker["mode"]) == ("unknown", "connector-log")


def test_far_from_the_cap_the_tail_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    path.write_text(_line(0))
    real = Path.open

    def no_reads(self: Path, mode: str = "r", *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        assert "b" not in mode, "tail read far from the cap"
        return real(self, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", no_reads)
    assert connector_calls.append_rows(path, [_line(1)]) == 1


def test_an_unreadable_tail_counts_as_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    path.write_text(_line(0))
    real = Path.open

    def fail_reads(self: Path, mode: str = "r", *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if "b" in mode:
            raise OSError("busy")
        return real(self, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", fail_reads)
    assert connector_calls.append_rows(path, [_line(1)], max_bytes=10_000) == 1


def test_a_batch_is_cut_at_the_cap_not_rejected_whole(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    one = len(_line(0).encode())
    assert connector_calls.append_rows(path, [_line(i) for i in range(5)], max_bytes=2 * one + 1) == 2
    assert [r["session_id"] for r in _rows(path)] == ["s0", "s1", "s2"]  # s2 is the marker


def test_the_cap_counts_bytes_not_characters(tmp_path: Path) -> None:
    path = tmp_path / "connector-calls-2026-09-30.jsonl"
    wide = json.dumps({"k": "é" * 10}, ensure_ascii=False) + "\n"  # 24 bytes, 14 characters
    assert connector_calls.append_rows(path, [wide, wide], max_bytes=40) == 1


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
    assert all(json.loads(line)["err"] == "x" * 6000 for line in lines[:-1])  # every row whole
    # 60 attempted; the cap holds exactly 40 of this size, then one marker closes the day.
    assert len(lines) == 41
    assert sum(json.loads(line).get("connector") == connector_calls.CAP_CONNECTOR for line in lines) == 1
    assert json.loads(lines[-1])["connector"] == connector_calls.CAP_CONNECTOR

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scout import update_outcomes as uo

AT = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)


def _row(**kw) -> uo.UpdateOutcome:
    base = dict(at=AT, from_version="0.16.0", target="0.17.0", managed_by="install.sh", result="applied", step="verify")
    base.update(kw)
    return uo.UpdateOutcome(**base)


def test_round_trip_uses_the_spec_keys(tmp_path: Path):
    row = _row(
        doctor="green",
        sidecars=("SKILL.md.proposed-merge",),
        detail="ok",
        log="self-update-2026-10-12.log",
        release_published_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    uo.append(tmp_path, row)
    raw = json.loads((tmp_path / uo.LEDGER).read_text().splitlines()[0])
    assert raw["at"] == int(AT.timestamp())
    assert raw["from"] == "0.16.0" and raw["target"] == "0.17.0"
    assert raw["sidecars"] == ["SKILL.md.proposed-merge"]
    assert raw["release_published_at"] == int(datetime(2026, 10, 10, tzinfo=UTC).timestamp())
    assert uo.read(tmp_path) == [row]


def test_minimal_row_round_trips(tmp_path: Path):
    row = _row(target=None, result="not_eligible", step="check", detail="disabled")
    uo.append(tmp_path, row)
    assert uo.read(tmp_path) == [row]


def test_append_creates_the_logs_dir_and_appends(tmp_path: Path):
    uo.append(tmp_path, _row(result="deferred"))
    uo.append(tmp_path, _row(result="applied"))
    assert [r.result for r in uo.read(tmp_path)] == ["deferred", "applied"]


def test_missing_ledger_reads_empty(tmp_path: Path):
    assert uo.read(tmp_path) == []


def test_bad_lines_are_skipped(tmp_path: Path):
    good = _row()
    uo.append(tmp_path, good)
    path = tmp_path / uo.LEDGER
    with path.open("a", encoding="utf-8") as f:
        f.write('{"at": 1, "result": "appl\n')  # truncated append
        f.write("[]\n")
        f.write('{"at": "x", "from": "0.1.0", "result": "applied", "step": "verify", "managed_by": "dev"}\n')
        f.write('{"at": 5, "from": "0.1.0", "result": "bogus", "step": "verify", "managed_by": "dev"}\n')
        f.write('{"at": 5, "from": "0.1.0", "result": "applied", "step": "bogus", "managed_by": "dev"}\n')
        f.write('{"at": 5, "result": "applied", "step": "verify", "managed_by": "dev"}\n')
        f.write("\n")
    assert uo.read(tmp_path) == [good]


def test_odd_optional_fields_are_tolerated(tmp_path: Path):
    path = tmp_path / uo.LEDGER
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "at": 100,
                "from": "0.1.0",
                "target": 7,
                "managed_by": "dev",
                "result": "not_eligible",
                "step": "check",
                "sidecars": "nope",
                "doctor": None,
                "detail": None,
                "release_published_at": "bad",
            }
        )
        + "\n"
    )
    (row,) = uo.read(tmp_path)
    assert row.target is None and row.sidecars == () and row.detail == "" and row.release_published_at is None


def test_unreadable_ledger_reads_empty(tmp_path: Path):
    (tmp_path / uo.LEDGER).mkdir(parents=True)  # a directory where the file should be
    assert uo.read(tmp_path) == []

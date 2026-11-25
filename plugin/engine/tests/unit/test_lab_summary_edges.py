"""The lab documents when a thread's files are damaged or missing: they still render, with what can be read."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest

from scout.lab import paths, summary

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG = "summary-drift"
NOW = dt.datetime(2026, 11, 21, 14, 0, tzinfo=dt.UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(tmp_path, SLUG))
    return tmp_path


def _note(vault: Path, eid: str) -> Path:
    note = paths.experiment_note(vault, SLUG, eid)
    assert note is not None
    return note


def test_json_turns_anything_else_into_text() -> None:
    assert summary._json({"d": dt.date(2026, 11, 21), "s": {1}, "n": None, "p": Path("x")}) == {
        "d": "2026-11-21",
        "s": [1],
        "n": None,
        "p": "x",
    }


def test_a_raised_date_that_isnt_a_date_isnt_counted(vault: Path) -> None:
    front = paths.front_page(vault, SLUG)
    text = front.read_text("utf-8").replace("raised 2026-11-21", "raised 2026-13-45", 1)
    front.write_text(text, "utf-8")
    [row] = summary.lab_list(vault, None, full=True, now=NOW)["threads"]
    assert row["counts"]["raised_7d"] == 1  # Q3 still counts; Q5's bad date only falls out of the count


def test_an_unreadable_experiment_note_has_no_frontmatter_and_isnt_due(vault: Path) -> None:
    _note(vault, "E2").write_bytes(b"\xff\xfe not utf-8")
    doc = summary.lab_thread(vault, SLUG)
    [e2] = [e for e in doc["experiments"] if e["eid"] == "E2"]
    assert e2["frontmatter"] is None
    [row] = summary.lab_list(vault, None, full=True, now=NOW)["threads"]
    assert "E2" not in row["experiments"]["due"]


def test_a_missing_timeline_renders_no_entries(vault: Path) -> None:
    paths.timeline_path(vault, SLUG).unlink()
    assert summary.lab_thread(vault, SLUG)["timeline"] == []


def test_an_experiment_without_a_note_has_no_frontmatter(vault: Path) -> None:
    _note(vault, "E1").unlink()
    doc = summary.lab_thread(vault, SLUG)
    [e1] = [e for e in doc["experiments"] if e["eid"] == "E1"]
    assert e1["frontmatter"] is None and e1["title"]

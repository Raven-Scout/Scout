"""templates/scripts/recurring-task-status.py — the yearly cadence and missed-window count.

The script ships to vaults verbatim (it has no ``.tmpl`` suffix), so it is driven
here the way a vault runs it: as a CLI over a directory of entity files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "recurring-task-status.py"


def _entity(directory: Path, slug: str, *, cadence: str, surface_window: str = "T-0 morning", **extra: str) -> None:
    fields = {"type": "recurring_task", "name": slug, "cadence": cadence, "surface_window": surface_window, **extra}
    frontmatter = yaml.safe_dump(fields, sort_keys=False)
    (directory / f"{slug}.md").write_text(f"---\n{frontmatter}---\n\n# {slug}\n", encoding="utf-8")


def _run(directory: Path, on: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(directory), "--date", on, *extra],
        capture_output=True,
        text=True,
        check=True,
    )


def _status(directory: Path, on: str) -> dict:
    rows = json.loads(_run(directory, on, "--json").stdout)
    assert len(rows) == 1
    return rows[0]


@pytest.fixture
def tasks(tmp_path: Path) -> Path:
    d = tmp_path / "recurring-tasks"
    d.mkdir()
    return d


@pytest.mark.parametrize(
    ("on", "status", "next_due"),
    [
        ("2027-06-12", "upcoming", "2027-06-15"),
        ("2027-06-13", "surfacing", "2027-06-15"),  # T-2d window opens
        ("2027-06-14", "surfacing", "2027-06-15"),
        ("2027-06-15", "due", "2027-06-15"),
        ("2027-06-16", "upcoming", "2028-06-15"),  # rolled to next year
    ],
)
def test_yearly_cadence_surfaces_and_falls_due_once_a_year(tasks: Path, on: str, status: str, next_due: str) -> None:
    _entity(tasks, "anniversary", cadence="yearly:06-15", surface_window="T-2d through T-0")
    row = _status(tasks, on)
    assert (row["status"], row["next_due"]) == (status, next_due)


def test_yearly_cadence_is_done_once_completed_this_year(tasks: Path) -> None:
    _entity(tasks, "renewal", cadence="yearly:06-15", last_completed_date="2027-06-15")
    assert _status(tasks, "2027-06-15")["status"] == "done"


@pytest.mark.parametrize(("on", "next_due"), [("2027-02-20", "2027-02-28"), ("2028-02-20", "2028-02-29")])
def test_yearly_feb_29_lands_on_feb_28_in_non_leap_years(tasks: Path, on: str, next_due: str) -> None:
    _entity(tasks, "leap-birthday", cadence="yearly:02-29")
    assert _status(tasks, on)["next_due"] == next_due


@pytest.mark.parametrize(
    ("on", "status"),
    [
        ("2029-01-15", "upcoming"),  # the 2028 completion covered 2028, not 2029
        ("2029-02-28", "due"),  # 02-29 lands on Feb 28 in 2029
    ],
)
def test_yearly_feb_29_completed_on_a_leap_day_is_due_again_the_next_year(tasks: Path, on: str, status: str) -> None:
    _entity(tasks, "leap-birthday", cadence="yearly:02-29", last_completed_date="2028-02-29")
    assert _status(tasks, on)["status"] == status


@pytest.mark.parametrize("cadence", ["yearly:13-01", "yearly:garbage", "yearly:"])
def test_malformed_yearly_cadence_reports_unknown_rather_than_raising(tasks: Path, cadence: str) -> None:
    _entity(tasks, "broken", cadence=cadence)
    assert _status(tasks, "2027-06-15")["status"] == "unknown"


def test_weekly_task_reports_windows_missed_since_last_completion(tasks: Path) -> None:
    """Two Fridays passed without a completion, yet the status still reads `upcoming`.

    `overdue` is unreachable for weekly cadences, so the count is the only thing
    that shows the lapse.
    """
    _entity(tasks, "weekly-update", cadence="weekly:friday", last_completed_date="2026-05-01")
    row = _status(tasks, "2026-05-20")  # a Wednesday; Fridays 05-08 and 05-15 went by
    assert row["status"] == "upcoming"
    assert row["windows_missed"] == 2
    assert "[2 cadence window(s) missed since 2026-05-01]" in row["reason"]


def test_windows_missed_is_zero_without_a_completion_on_record(tasks: Path) -> None:
    _entity(tasks, "weekly-update", cadence="weekly:friday")
    assert _status(tasks, "2026-05-20")["windows_missed"] == 0


def test_markdown_table_has_a_missed_column(tasks: Path) -> None:
    _entity(tasks, "weekly-update", cadence="weekly:friday", last_completed_date="2026-05-01")
    table = _run(tasks, "2026-05-20").stdout
    assert "| Name | Status | Missed | Cadence |" in table
    assert "| weekly-update | **upcoming** | **2** | weekly:friday |" in table

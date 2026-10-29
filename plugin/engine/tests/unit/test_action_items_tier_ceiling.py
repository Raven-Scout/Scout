"""*Deadline-Distance Ceiling* in code: `action-items check-tier-ceiling`.

A task's due date caps its tier: 30+ days out 🟢, 7–29 🟡, under 7 🔴 allowed,
under 3 and the user's move 🔴. Past-due tasks are check-demotion's; tasks
with no due date are out of scope.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.prefetched import PrefetchCheckError
from scout.action_items.tier_ceiling import (
    GREEN,
    RED,
    YELLOW,
    ceiling,
    check_tier_ceiling,
    judge,
    users_move,
)

runner = CliRunner()
TODAY = dt.date(2026, 4, 15)


def d(offset: int) -> dt.date:
    return TODAY + dt.timedelta(days=offset)


def _task(tag: str, body: str) -> str:
    return f"- [ ] [#{tag}] **Write the quota migration** — {body}\n"


def _due(tag: str, offset: int, rest: str = "") -> str:
    return _task(tag, f"due {d(offset):%b} {d(offset).day}{rest}")


def _lines(red: str = "", yellow: str = "", green: str = "", parked: str = "", notes: str = "") -> list[str]:
    return (
        f"# Action Items — {TODAY}\n\n## 💡 Today's Focus\n\n{red}\n## 🔴 Urgent\n\n{red}\n## 🟡 To Do\n\n{yellow}\n"
        f"## 🟢 Watching\n\n{green}\n## 🅿️ Parked\n\n{parked}\n## ✅ Recently Completed\n\n"
        f"## 🪵 Run notes\n\n{notes}"
    ).splitlines()


def _problems(**sections: str) -> dict[str, str]:
    return {f.tag: f.problem for f in judge(_lines(**sections), today=TODAY).findings}


# ----- the ceiling -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "tier"),
    [(0, RED), (2, RED), (3, RED), (6, RED), (7, YELLOW), (12, YELLOW), (29, YELLOW), (30, GREEN), (60, GREEN)],
)
def test_ceiling_boundaries(days: int, tier: str) -> None:
    assert ceiling(days) == tier


@pytest.mark.parametrize(
    ("days", "section", "flagged"),
    [
        (30, GREEN, False),
        (30, YELLOW, True),
        (29, YELLOW, False),
        (7, YELLOW, False),
        (7, RED, True),
        (6, RED, False),
        (3, RED, False),
        (3, YELLOW, False),  # 3 days out is not "under 3": 🟡 is fine
        (2, YELLOW, True),  # under 3 and the user's move: 🔴
        (2, RED, False),
        (0, YELLOW, True),
    ],
)
def test_boundaries_in_the_file(days: int, section: str, flagged: bool) -> None:
    key = {RED: "red", YELLOW: "yellow", GREEN: "green"}[section]
    assert ("CEIL" in _problems(**{key: _due("CEIL", days)})) is flagged


def test_a_twelve_day_task_in_red_is_named_with_its_ceiling() -> None:
    problems = _problems(red=_due("QMIG", 12, "; action required, final notice."))
    assert problems == {"QMIG": f"in 🔴, ceiling 🟡 (due {d(12):%b} {d(12).day}, 12 days out)"}


def test_iso_and_dated_bodies_both_read() -> None:
    assert _problems(red=_task("ISO", f"due {d(40)}; deprecation notice.")) == {
        "ISO": f"in 🔴, ceiling 🟢 (due {d(40):%b} {d(40).day}, 40 days out)"
    }


# ----- whose move ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "body", "mine"),
    [
        (RED, "due Apr 16; send the numbers.", True),
        (YELLOW, "due Apr 16; send the numbers.", True),
        (YELLOW, "due Apr 16; waiting on Sam since Apr 10.", False),
        (YELLOW, "due Apr 16; nudge Priya for the sizing.", False),
        (GREEN, "due Apr 16; Sam is on it.", False),
    ],
)
def test_users_move(section: str, body: str, mine: bool) -> None:
    assert users_move(section, body) is mine


def test_under_three_days_a_nudge_may_stay_yellow() -> None:
    assert _problems(yellow=_due("NUDG", 1, "; waiting on Sam since Apr 10.")) == {}


def test_under_three_days_waiting_in_green_is_not_promoted() -> None:
    assert _problems(green=_due("WAIT", 1, "; waiting on Sam since Apr 10.")) == {}


def test_under_three_days_your_move_in_yellow_is_named() -> None:
    assert _problems(yellow=_due("MINE", 2)) == {
        "MINE": f"in 🟡, due {d(2):%b} {d(2).day} (2 days out) and your move: move it to 🔴"
    }


# ----- out of scope ----------------------------------------------------------------------


def test_no_due_date_is_out_of_scope() -> None:
    result = judge(_lines(red=_task("NODUE", "action required: rotate the key.")), today=TODAY)
    assert result.passed and result.checked == ()


def test_past_due_is_left_to_check_demotion() -> None:
    result = judge(_lines(red=_due("LATE", -2), yellow=_due("OVRD", -10, "; still want this?")), today=TODAY)
    assert result.passed and result.checked == ()


def test_due_today_is_checked() -> None:
    result = judge(_lines(red=_due("TDAY", 0)), today=TODAY)
    assert [(p.tag, p.days_out, p.ceiling) for p in result.checked] == [("TDAY", 0, RED)]


def test_parked_and_snoozed_tasks_are_not_checked() -> None:
    snoozed = _due("SNZ", 12).rstrip("\n") + f"\n  - snoozed-until: {d(3)} (from: urgent)\n"
    result = judge(_lines(red=snoozed, parked=_due("PRK", 1)), today=TODAY)
    assert result.checked == ()


# ----- override line -----------------------------------------------------------------------


def test_an_override_line_accepts_the_placement() -> None:
    notes = "- 07:12 morning-consolidation\n  - Ceiling override [#QMIG] — the launch moved it up; Alex said so\n"
    result = judge(_lines(red=_due("QMIG", 12), notes=notes), today=TODAY)
    assert result.passed and result.overridden == ("QMIG",)


def test_an_override_needs_a_reason_and_the_run_notes() -> None:
    bare = "- Ceiling override [#QMIG] —\n"
    assert not judge(_lines(red=_due("QMIG", 12), notes=bare), today=TODAY).passed
    elsewhere = _due("QMIG", 12) + "  - Ceiling override [#QMIG] — because\n"
    assert not judge(_lines(red=elsewhere), today=TODAY).passed


# ----- the vault --------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _file(vault: Path, day: dt.date, **sections: str) -> None:
    lines = _lines(**sections)
    lines[0] = f"# Action Items — {day}"
    (vault / "action-items" / f"action-items-{day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_body_that_lost_its_due_date_uses_the_earlier_file(vault: Path) -> None:
    _file(vault, d(-1), yellow=_due("QMIG", 12))
    _file(vault, TODAY, red=_task("QMIG", "write it today."))
    result = check_tier_ceiling(today=TODAY.isoformat())
    assert [f.tag for f in result.findings] == ["QMIG"]
    assert result.checked[0].from_earlier_file


def test_a_new_due_date_today_wins_over_the_earlier_file(vault: Path) -> None:
    _file(vault, d(-1), yellow=_due("QMIG", 12))
    _file(vault, TODAY, red=_due("QMIG", 4))
    assert check_tier_ceiling(today=TODAY.isoformat()).passed


def test_it_cannot_check_without_todays_file(vault: Path) -> None:
    with pytest.raises(PrefetchCheckError, match="no action-items file"):
        check_tier_ceiling(today=TODAY.isoformat())


# ----- CLI --------------------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["check-tier-ceiling", "--date", TODAY.isoformat(), *args])


def test_cli_exit_0(vault: Path) -> None:
    _file(vault, TODAY, red=_due("SOON", 2), yellow=_due("QMIG", 12), green=_due("FAR", 40))
    result = _cli()
    assert result.exit_code == 0
    assert "all 3 dated tasks within their ceiling" in result.stdout


def test_cli_exit_0_with_nothing_dated(vault: Path) -> None:
    _file(vault, TODAY, red=_task("NODUE", "send it."))
    result = _cli()
    assert result.exit_code == 0 and "no open task has a future due date" in result.stdout


def test_cli_exit_1_names_each_task(vault: Path) -> None:
    _file(vault, TODAY, red=_due("QMIG", 12), yellow=_due("FAR", 40))
    result = _cli()
    assert result.exit_code == 1
    assert "2 tasks off the deadline-distance ceiling" in result.stdout
    assert "[#QMIG] in 🔴, ceiling 🟡" in result.stdout and "[#FAR] in 🟡, ceiling 🟢" in result.stdout
    assert "- Ceiling override [#TAG] — <reason>" in result.stdout


def test_cli_json(vault: Path) -> None:
    _file(vault, TODAY, red=_due("QMIG", 12))
    result = _cli("--json")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["passed"] is False and out["checked"][0]["ceiling"] == "🟡" and out["checked"][0]["days_out"] == 12
    assert [f["tag"] for f in out["findings"]] == ["QMIG"]


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli()
    assert result.exit_code == 2 and "check-tier-ceiling: cannot check" in result.stdout


def test_cli_bad_date_is_exit_2(vault: Path) -> None:
    result = runner.invoke(app, ["check-tier-ceiling", "--date", "2026-02-30"])
    assert result.exit_code == 2

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
    AssignedIssue,
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
    assert result.checked[0].due_from == "earlier file"


def test_a_new_due_date_today_wins_over_the_earlier_file(vault: Path) -> None:
    _file(vault, d(-1), yellow=_due("QMIG", 12))
    _file(vault, TODAY, red=_due("QMIG", 4))
    assert check_tier_ceiling(today=TODAY.isoformat()).passed


def test_it_cannot_check_without_todays_file(vault: Path) -> None:
    with pytest.raises(PrefetchCheckError, match="no action-items file"):
        check_tier_ceiling(today=TODAY.isoformat())


# ----- pre-fetched dated Linear issues ---------------------------------------------------------


def _assigned(*issues: tuple[str, int]) -> list[AssignedIssue]:
    return [AssignedIssue(ident, f"Title of {ident}", d(offset)) for ident, offset in issues]


def _refs(tag: str, ident: str, body: str = "write it.") -> str:
    return _task(tag, body) + f"  - Refs: {ident}\n"


def _judge(assigned: list[AssignedIssue], **sections: str) -> dict[str, str]:
    result = judge(_lines(**sections), today=TODAY, assigned=assigned)
    return {f.label: f.problem for f in result.findings}


def test_a_dated_issue_with_no_task_line_is_named_with_where_it_goes() -> None:
    problems = _judge(_assigned(("PROJ-1210", 40), ("PROJ-1211", 12)), notes="- 07:12 run\n  - PROJ-1210 is new\n")
    assert set(problems) == {"[PROJ-1210]", "[PROJ-1211]"}
    assert problems["[PROJ-1210]"] == (
        f'Linear issue "Title of PROJ-1210" is assigned to you, due {d(40):%b} {d(40).day} (40 days out), and no '
        f"task line names it: add it under 🟢 Watching or lower, with `due {d(40)}` and `Refs: PROJ-1210`"
    )
    assert "under 🟡 To Do or lower" in problems["[PROJ-1211]"]


@pytest.mark.parametrize("key", ["red", "yellow", "green", "parked"])
def test_a_task_line_in_any_tier_tracks_the_issue(key: str) -> None:
    assert "[PROJ-1210]" not in _judge(_assigned(("PROJ-1210", 40)), **{key: _refs("DASH", "PROJ-1210")})


def test_a_completed_task_line_tracks_the_issue() -> None:
    lines = _lines()
    i = lines.index("## ✅ Recently Completed")
    lines[i + 1 : i + 1] = ["", "- [x] [#DASH] **Plan the dashboard** — done. Refs: PROJ-1210"]
    assert judge(lines, today=TODAY, assigned=_assigned(("PROJ-1210", 40))).passed


def test_an_identifier_prefix_is_not_a_match() -> None:
    assert "[PROJ-121]" in _judge(_assigned(("PROJ-121", 12)), yellow=_refs("QMIG", "PROJ-1211"))


def test_past_due_assigned_issues_are_left_to_check_demotion() -> None:
    result = judge(_lines(), today=TODAY, assigned=_assigned(("PROJ-1209", -1)))
    assert result.passed


def test_a_task_naming_the_issue_takes_its_ceiling_from_the_linear_due_date() -> None:
    result = judge(_lines(red=_refs("QMIG", "PROJ-1211")), today=TODAY, assigned=_assigned(("PROJ-1211", 12)))
    assert [(p.tag, p.due_from, p.ceiling) for p in result.checked] == [("QMIG", "PROJ-1211", YELLOW)]
    assert [f.problem for f in result.findings] == [f"in 🔴, ceiling 🟡 (due {d(12):%b} {d(12).day}, 12 days out)"]


def test_the_body_due_date_wins_over_the_linear_one() -> None:
    lines = _lines(red=_refs("QMIG", "PROJ-1211", f"due {d(4)}; write it."))
    assert judge(lines, today=TODAY, assigned=_assigned(("PROJ-1211", 12))).passed


def test_an_issue_override_accepts_it_untracked() -> None:
    notes = "- 07:12 run\n  - Ceiling override [PROJ-1210] — Sam owns it in practice; reassigning\n"
    result = judge(_lines(notes=notes), today=TODAY, assigned=_assigned(("PROJ-1210", 40)))
    assert result.passed and result.overridden == ("PROJ-1210",)


def test_nothing_pre_fetched_checks_no_issues() -> None:
    result = judge(_lines(), today=TODAY, assigned=None)
    assert result.passed and result.assigned is None


def _context(vault: Path, generated: str, assigned: object) -> None:
    cache = vault / ".scout-cache"
    cache.mkdir(exist_ok=True)
    data = {"generated_at": f"{generated}T07:00:00+00:00", "linear_assigned_due": assigned}
    (cache / "session-context.json").write_text(json.dumps(data), encoding="utf-8")


def _row(ident: str, offset: int) -> dict[str, str]:
    return {
        "identifier": ident,
        "title": "Write the quota migration",
        "dueDate": d(offset).isoformat(),
        "state": "Todo",
    }


def test_the_vault_check_reads_todays_pre_fetch(vault: Path) -> None:
    _file(vault, TODAY, green=_due("FAR", 40))
    _context(vault, TODAY.isoformat(), [_row("PROJ-1211", 12)])
    result = check_tier_ceiling(today=TODAY.isoformat())
    assert [f.label for f in result.findings] == ["[PROJ-1211]"]


@pytest.mark.parametrize("generated", [d(-1).isoformat(), None])
def test_an_old_or_missing_pre_fetch_checks_only_the_ceiling(vault: Path, generated: str | None) -> None:
    _file(vault, TODAY, green=_due("FAR", 40))
    if generated:
        _context(vault, generated, [_row("PROJ-1211", 12)])
    result = check_tier_ceiling(today=TODAY.isoformat())
    assert result.passed and result.assigned is None


def test_a_pre_fetch_without_the_list_checks_no_issues(vault: Path) -> None:
    """No Linear key: linear_assigned_due is null, as #532's own-activity rows are."""
    _file(vault, TODAY)
    _context(vault, TODAY.isoformat(), None)
    assert check_tier_ceiling(today=TODAY.isoformat()).assigned is None


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


def test_cli_names_an_untracked_issue_by_its_identifier(vault: Path) -> None:
    _file(vault, TODAY, green=_due("FAR", 40))
    _context(vault, TODAY.isoformat(), [_row("PROJ-1211", 12)])
    result = _cli()
    assert result.exit_code == 1
    assert "  [PROJ-1211] Linear issue" in result.stdout and "[PROJ-123]" in result.stdout
    out = json.loads(_cli("--json").stdout)
    assert out["findings"] == [{"tag": "PROJ-1211", "problem": out["findings"][0]["problem"], "untracked": True}]
    assert out["assigned"] == [{"identifier": "PROJ-1211", "due": d(12).isoformat()}]


def test_cli_passes_with_every_dated_issue_tracked(vault: Path) -> None:
    _file(vault, TODAY, yellow=_refs("QMIG", "PROJ-1211", f"due {d(12)}; write it."))
    _context(vault, TODAY.isoformat(), [_row("PROJ-1211", 12)])
    result = _cli()
    assert result.exit_code == 0
    assert "all 1 dated task within their ceiling; every dated Linear issue assigned to you has a task line" in (
        result.stdout
    )


def test_cli_bad_date_is_exit_2(vault: Path) -> None:
    result = runner.invoke(app, ["check-tier-ceiling", "--date", "2026-02-30"])
    assert result.exit_code == 2

"""*Past-Due Tasks Step Down* in code (#534): the quiet-day count and `action-items check-demotion`.

The runner pre-fetches each past-due 🔴/🟡 task with its branch (hold /
step_down / park) into `.scout-cache/session-context.json`; the check holds
today's file to those branches, counting new evidence this run recorded.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.past_due import (
    HOLD,
    PARK,
    STEP_DOWN,
    History,
    PastDue,
    assess,
    check_demotion,
    note_entries,
    parse_due,
    past_due_tasks,
)
from scout.action_items.prefetched import PrefetchCheckError

runner = CliRunner()
TODAY = dt.date(2026, 4, 15)


def d(offset: int) -> dt.date:
    return TODAY + dt.timedelta(days=offset)


def _never(line: str) -> dt.date:
    raise AssertionError(f"no comment expected: {line}")


def _assess(
    line: str, note: str = "", *, section: str = "🔴", extra: tuple[str, ...] = (), **kw: Any
) -> PastDue | None:
    return assess([line, *extra], section, note, today=TODAY, first_seen=kw.pop("first_seen", _never), **kw)


def _note(*entries: str) -> str:
    return "---\ntag: X\ncreated: 2026-01-01\nupdated: 2026-04-01\n---\n\nSome context.\n\n" + "".join(
        f"- {e}\n" for e in entries
    )


def _task(tag: str, body: str) -> str:
    return f"- [ ] [#{tag}] **Send the survey answers** — {body}"


# ----- due dates ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "due"),
    [
        ("due 2026-04-01; no reply since.", dt.date(2026, 4, 1)),
        ("the form was due Apr 1; nothing back.", dt.date(2026, 4, 1)),
        ("due by April 1st", dt.date(2026, 4, 1)),
        ("due on Wed, Apr 1", dt.date(2026, 4, 1)),
        ("due 1 Apr 2025", dt.date(2025, 4, 1)),
        ("due Sep 22, 2025; still want this?", dt.date(2025, 9, 22)),
        ("due: Dec 30", dt.date(2025, 12, 30)),  # nearest to Apr 15 2026: last December
        ("due Jun 2", dt.date(2026, 6, 2)),
        ("Due Mar 3 · then the launch", dt.date(2026, 3, 3)),
    ],
)
def test_parse_due(body: str, due: dt.date) -> None:
    assert parse_due(body, TODAY) == due


@pytest.mark.parametrize("body", ["send it today", "due today", "overdue by a week", "due 3 days ago", "due Feb 30"])
def test_parse_due_without_a_date(body: str) -> None:
    assert parse_due(body, TODAY) is None


# ----- context notes -------------------------------------------------------------


def test_note_entries_read_dated_lines_and_dated_headings() -> None:
    text = (
        "---\ntag: X\ncreated: 2026-04-01\n---\n\nRestored verbatim from the 2026-01-01 snapshot.\n\n"
        "- 2026-04-02: Sam replied with the numbers\n"
        "- **Apr 3** — no reply yet\n"
        "## 2026-04-05\n\n- stepped down, past due with no news\n"
        "- Dec 30: kicked off\n"
    )
    entries = [(e.on, e.is_evidence, e.is_marker) for e in note_entries(text, TODAY)]
    assert entries == [
        (dt.date(2026, 4, 2), True, False),
        (dt.date(2026, 4, 3), False, False),  # says nothing happened
        (dt.date(2026, 4, 5), True, False),  # the heading itself
        (dt.date(2026, 4, 5), False, True),
        (dt.date(2025, 12, 30), True, False),  # a note line is never in the future
    ]


# ----- the count -----------------------------------------------------------------


def test_not_past_due_is_not_listed() -> None:
    assert _assess(_task("TASK", f"due {TODAY}")) is None
    assert _assess(_task("TASK", f"due {d(3)}")) is None
    assert _assess(_task("TASK", "send the answers")) is None


def test_under_seven_quiet_days_holds() -> None:
    found = _assess(_task("TASK", f"due {d(-6)}"))
    assert found is not None and found.branch == HOLD and found.quiet_days == 6
    assert found.to_json()["steps_down_on"] == d(1).isoformat()


def test_seven_quiet_days_steps_down() -> None:
    found = _assess(_task("TASK", f"due {d(-7)}"))
    assert found is not None and (found.branch, found.quiet_days, found.stepped_down) == (STEP_DOWN, 7, None)


def test_new_evidence_restarts_the_count() -> None:
    found = _assess(_task("TASK", f"due {d(-20)}"), _note(f"{d(-3)}: Sam sent a draft"))
    assert found is not None and (found.branch, found.quiet_days, found.last_evidence) == (HOLD, 3, d(-3))


def test_evidence_before_the_due_date_does_not_count() -> None:
    found = _assess(_task("TASK", f"due {d(-10)}"), _note(f"{d(-30)}: kicked off"))
    assert found is not None and found.branch == STEP_DOWN and found.quiet_days == 10


def test_a_no_news_line_does_not_restart_the_count() -> None:
    found = _assess(_task("TASK", f"due {d(-10)}"), _note(f"{d(-2)}: still no reply from Sam"))
    assert found is not None and found.branch == STEP_DOWN and found.last_evidence is None


def test_a_stepped_down_task_parks_fourteen_days_after_the_line() -> None:
    note = _note(f"{d(-14)}: stepped down, past due with no news")
    found = _assess(_task("TASK", f"due {d(-25)}; still want this?"), note, section="🟡")
    assert found is not None and (found.branch, found.stepped_down) == (PARK, d(-14))
    marker = _note(f"{d(-13)}: stepped down, past due with no news")
    early = _assess(_task("TASK", f"due {d(-25)}; still want this?"), marker)
    assert early is not None and early.branch == STEP_DOWN
    assert early.to_json()["parks_on"] == d(1).isoformat()


def test_a_step_down_line_from_before_new_evidence_is_a_past_cycle() -> None:
    note = _note(f"{d(-30)}: stepped down, past due with no news", f"{d(-10)}: Alex: yes, still want it")
    found = _assess(_task("TASK", f"due {d(-40)}; still want this?"), note)
    assert found is not None and (found.branch, found.stepped_down, found.quiet_days) == (STEP_DOWN, None, 10)


def test_a_still_want_this_yes_with_a_new_due_date_releases_it() -> None:
    assert _assess(_task("TASK", f"due {d(5)}"), _note(f"{d(-1)}: Alex: yes, by {d(5)}")) is None


def test_a_still_want_this_yes_restarts_the_count() -> None:
    note = _note(f"{d(-20)}: stepped down, past due with no news", f"{d(-1)}: Alex said yes, still wanted")
    found = _assess(_task("TASK", f"due {d(-30)}; still want this?"), note, section="🟡")
    assert found is not None and (found.branch, found.stepped_down) == (HOLD, None)


def test_a_comment_is_evidence_dated_when_it_first_appeared() -> None:
    comment = "  - alex: I'll do it this week"
    found = _assess(_task("TASK", f"due {d(-20)}"), extra=(comment,), first_seen=lambda line: d(-2))
    assert found is not None and (found.branch, found.last_evidence) == (HOLD, d(-2))


def test_machine_sub_bullets_are_not_comments() -> None:
    extra = ("  - Refs: PROJ-1234", "  - Topic: #STRM", "  - estimate: 45m (raw: 30m, kind: comms)")
    found = _assess(_task("TASK", f"due {d(-9)}"), extra=extra)
    assert found is not None and found.branch == STEP_DOWN


def test_a_task_snoozed_past_today_is_skipped() -> None:
    assert _assess(_task("TASK", f"due {d(-30)}"), extra=(f"  - snoozed-until: {d(2)}",)) is None
    lapsed = _assess(_task("TASK", f"due {d(-30)}"), extra=(f"  - snoozed-until: {d(-1)}",))
    assert lapsed is not None and lapsed.branch == STEP_DOWN


@pytest.mark.parametrize(("past", "branch"), [(0, None), (2, HOLD), (3, STEP_DOWN)])
def test_a_recurring_task_steps_down_on_its_third_day(past: int, branch: str | None) -> None:
    found = _assess(_task("TASK", f"due {d(-past)}"), extra=("  - Refs: [[recurring-tasks/weekly-status]]",))
    assert (found.branch if found else None) == branch
    if found:
        assert found.recurring and found.threshold == 3


def test_a_missing_body_date_falls_back() -> None:
    found = _assess(_task("TASK", "still want this?"), due_fallback=d(-8))
    assert found is not None and found.due == d(-8) and found.branch == STEP_DOWN


def test_round_trips_through_json() -> None:
    found = _assess(_task("TASK", f"due {d(-8)}"), _note(f"{d(-1)}: stepped down, past due with no news"))
    assert found is not None and PastDue.from_json(json.loads(json.dumps(found.to_json()))) == found


# ----- reading the vault -------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items" / "context").mkdir(parents=True)
    (data_dir / ".scout-cache").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _file(vault: Path, day: dt.date, red: str = "", yellow: str = "", parked: str = "", green: str = "") -> Path:
    path = vault / "action-items" / f"action-items-{day}.md"
    path.write_text(
        f"# Action Items — {day}\n\n## 🎯 Today's Focus\n\n{red}\n## 🔴 Urgent\n\n{red}\n## 🟡 To Do\n\n{yellow}\n"
        f"## 🟢 Watching\n\n{green}\n## 🅿️ Parked\n\n{parked}\n## ✅ Recently Completed\n\n",
        encoding="utf-8",
    )
    return path


def _ctx_note(vault: Path, tag: str, *entries: str) -> None:
    (vault / "action-items" / "context" / f"{tag}.md").write_text(_note(*entries), encoding="utf-8")


OVRD = _task("OVRD", f"due {d(-10)}; no reply or follow-up since.") + "\n"
LATE = _task("LATE", f"due {d(-2)}; the next step is Alex's.") + "\n"
PARKED_TASK = _task("PARK", f"due {d(-25)}; still want this?") + "\n"
QUESTION = _task("OVRD", f"due {d(-10):%b} {d(-10).day}; still want this?") + "\n"


def test_past_due_tasks_reads_the_newest_file_before_today(vault: Path) -> None:
    _file(vault, d(-2), red=_task("OLD", f"due {d(-20)}") + "\n")
    _file(vault, d(-1), red=OVRD + LATE, yellow=PARKED_TASK, green=_task("GRN", f"due {d(-20)}") + "\n")
    _ctx_note(vault, "PARK", f"{d(-25)}: due; nothing back", f"{d(-16)}: stepped down, past due with no news")
    source, tasks = past_due_tasks(vault, TODAY)
    assert source == d(-1).isoformat()
    assert {t.tag: (t.section, t.branch) for t in tasks} == {
        "OVRD": ("🔴", STEP_DOWN),
        "LATE": ("🔴", HOLD),
        "PARK": ("🟡", PARK),
    }


def test_a_parked_task_counts_only_when_this_rule_parked_it(vault: Path) -> None:
    mine = _task("MINE", f"due {d(-40)}") + "\n"
    ruled = _task("RULE", f"due {d(-40)}; still want this?") + "\n"
    _file(vault, TODAY, parked=mine + ruled)
    _ctx_note(vault, "RULE", f"{d(-20)}: stepped down, past due with no news")
    _, tasks = past_due_tasks(vault, TODAY)
    assert [(t.tag, t.branch) for t in tasks] == [("RULE", PARK)]


def test_comments_are_dated_by_the_first_file_they_appear_in(vault: Path) -> None:
    block = _task("CMT", f"due {d(-30)}") + "\n  - alex: on it, by Friday\n"
    _file(vault, d(-6), red=_task("CMT", f"due {d(-30)}") + "\n")
    for back in (5, 4, 3):
        _file(vault, d(-back), red=block)
    _, tasks = past_due_tasks(vault, TODAY)
    assert [(t.tag, t.last_evidence, t.branch) for t in tasks] == [("CMT", d(-5), HOLD)]


def test_history_stops_at_the_first_day_without_the_line(vault: Path) -> None:
    _file(vault, d(-3), red="- x\n")
    _file(vault, d(-2), red="- y\n")
    _file(vault, d(-1), red="- x\n")
    assert History(vault).first_seen("- x", upto=d(-1)) == d(-1)
    assert History(vault, overrides={TODAY: ["- x"]}).first_seen("- x", upto=TODAY) == d(-1)
    assert History(vault).first_seen("- never", upto=TODAY) == TODAY


def test_no_daily_file_lists_nothing(vault: Path) -> None:
    assert past_due_tasks(vault, TODAY) == (None, [])


# ----- check-demotion ---------------------------------------------------------------


def _context(vault: Path, rows: list[dict[str, Any]] | None) -> None:
    data: dict[str, Any] = {"generated_at": f"{TODAY}T07:00:00", "session_type": "morning-briefing"}
    if rows is not None:
        data["past_due_tasks"] = rows
    (vault / ".scout-cache" / "session-context.json").write_text(json.dumps(data), encoding="utf-8")


def _prefetch(vault: Path) -> None:
    """Yesterday's file with the three eval seeds, pre-fetched as the runner would."""
    _file(vault, d(-1), red=OVRD + LATE, yellow=PARKED_TASK)
    _ctx_note(vault, "PARK", f"{d(-16)}: stepped down, past due with no news")
    _context(vault, [t.to_json() for t in past_due_tasks(vault, TODAY)[1]])


def _check() -> Any:
    return check_demotion(today=TODAY.isoformat())


def _problems() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in _check().findings:
        out.setdefault(f.tag, []).append(f.problem)
    return out


def test_a_run_that_follows_every_branch_passes(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=LATE, yellow=QUESTION, parked=PARKED_TASK)
    _ctx_note(vault, "OVRD", f"{TODAY}: stepped down, past due with no news")
    result = _check()
    assert result.passed
    assert {t.tag: t.branch for t in result.checked} == {"OVRD": STEP_DOWN, "LATE": HOLD, "PARK": PARK}


def test_a_step_down_left_in_red_is_named_three_ways(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=OVRD + LATE, parked=PARKED_TASK)
    problems = _problems()
    assert list(problems) == ["OVRD"]
    assert problems["OVRD"] == [
        "still in 🔴; move it to 🟡 at most",
        f"body doesn't say `due {d(-10):%b} {d(-10).day}; still want this?`",
        f"context note has no dated `- {TODAY}: stepped down, past due with no news` line",
    ]


def test_a_step_down_line_needs_a_date(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=LATE, yellow=QUESTION, parked=PARKED_TASK)
    (vault / "action-items" / "context" / "OVRD.md").write_text("Stepped down, past due with no news.\n")
    assert _problems() == {"OVRD": [f"context note has no dated `- {TODAY}: stepped down, past due with no news` line"]}


def test_a_park_task_left_out_of_parked_or_rewritten_is_named(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "OVRD", f"{TODAY}: stepped down, past due with no news")
    _file(vault, TODAY, red=LATE, yellow=QUESTION + _task("PARK", f"due {d(-25)}; renew or drop?") + "\n")
    assert _problems() == {"PARK": ["in 🟡; move it to 🅿️ Parked", "its text changed; park it as it stood"]}


def test_a_hold_task_wrongly_demoted_is_named(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "OVRD", f"{TODAY}: stepped down, past due with no news")
    _ctx_note(vault, "LATE", f"{TODAY}: stepped down, past due with no news")
    late = _task("LATE", f"due {d(-2)}; still want this?") + "\n"
    _file(vault, TODAY, yellow=QUESTION, parked=PARKED_TASK + late)
    assert _problems() == {
        "LATE": [
            "parked under 2 quiet days",
            "asks 'still want this?' under the step-down threshold",
            "context note gained a `stepped down, past due with no news` line under the step-down threshold",
        ]
    }


def test_news_this_run_found_restarts_the_count(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "OVRD", f"{TODAY}: Sam sent the answers back for review")
    _file(vault, TODAY, red=OVRD + LATE, parked=PARKED_TASK)
    result = _check()
    assert result.passed and {t.tag: t.branch for t in result.checked}["OVRD"] == HOLD


def test_news_restart_then_wrong_question_says_why(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "OVRD", f"{TODAY}: moved to 🟡 and asked Alex about it")
    _file(vault, TODAY, red=LATE, yellow=QUESTION, parked=PARKED_TASK)
    problems = _problems()["OVRD"]
    assert problems[0] == "asks 'still want this?' under the step-down threshold"
    assert problems[-1].startswith(f"(the count restarted on {TODAY}")


def test_new_evidence_unparks(vault: Path) -> None:
    _file(vault, d(-1), parked=PARKED_TASK)
    _ctx_note(vault, "PARK", f"{d(-16)}: stepped down, past due with no news")
    _context(vault, [t.to_json() for t in past_due_tasks(vault, TODAY)[1]])
    _ctx_note(vault, "PARK", f"{d(-16)}: stepped down, past due with no news", f"{TODAY}: Alex wants it renewed")
    _file(vault, TODAY, parked=PARKED_TASK)
    assert _problems() == {
        "PARK": [
            "new evidence since it was parked; place it under the usual rules again",
            f"(the count restarted on {TODAY}: a dated note line, a comment or a new due date reads as news; "
            "if it isn't, reword it)",
        ]
    }


def test_a_new_due_date_or_snooze_releases_the_task(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "PARK", f"{d(-16)}: stepped down, past due with no news")
    ovrd = _task("OVRD", f"due {d(7)}") + "\n"
    late = LATE + f"  - snoozed-until: {d(3)}\n"
    _file(vault, TODAY, red=ovrd + late, parked=PARKED_TASK)
    result = _check()
    assert result.passed and set(result.released) == {"OVRD", "LATE"}


def test_a_dropped_task_is_named(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=LATE, parked=PARKED_TASK)
    assert _problems() == {"OVRD": ["missing from today's file"]}


def test_it_cannot_check_without_the_fetch(vault: Path) -> None:
    _file(vault, TODAY)
    _context(vault, None)
    with pytest.raises(PrefetchCheckError, match="past_due_tasks was not pre-fetched"):
        _check()
    _context(vault, [{"tag": "A"}])
    with pytest.raises(PrefetchCheckError, match="unreadable entry"):
        _check()
    _context(vault, {"tag": "A"})  # type: ignore[arg-type]
    with pytest.raises(PrefetchCheckError, match="not a list"):
        _check()


# ----- CLI -----------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["check-demotion", "--date", TODAY.isoformat(), *args])


def test_cli_exit_0(vault: Path) -> None:
    _prefetch(vault)
    _ctx_note(vault, "OVRD", f"{TODAY}: stepped down, past due with no news")
    _file(vault, TODAY, red=LATE, yellow=QUESTION, parked=PARKED_TASK)
    result = _cli()
    assert result.exit_code == 0
    assert "every past-due task is on its branch (1 hold, 1 step_down, 1 park)" in result.stdout


def test_cli_exit_0_with_nothing_past_due(vault: Path) -> None:
    _file(vault, TODAY)
    _context(vault, [])
    result = _cli()
    assert result.exit_code == 0 and "no past-due 🔴/🟡 tasks" in result.stdout


def test_cli_exit_1_names_each_problem(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=OVRD + LATE, parked=PARKED_TASK)
    result = _cli()
    assert result.exit_code == 1
    assert "1 past-due task off their branch" in result.stdout
    assert "[#OVRD] (step_down) still in 🔴; move it to 🟡 at most" in result.stdout
    assert "`- YYYY-MM-DD: stepped down, past due with no news`" in result.stdout


def test_cli_json(vault: Path) -> None:
    _prefetch(vault)
    _file(vault, TODAY, red=OVRD + LATE, parked=PARKED_TASK)
    result = _cli("--json")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["passed"] is False and [c["tag"] for c in out["checked"]] == ["OVRD", "LATE", "PARK"]
    assert {f["tag"] for f in out["findings"]} == {"OVRD"}


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli()
    assert result.exit_code == 2 and "check-demotion: cannot check" in result.stdout


# ----- the pre-fetch -------------------------------------------------------------------


def test_gather_pre_fetches_the_past_due_set(vault: Path) -> None:
    from scout.scripts.pre_session_data import gather

    _file(vault, d(-1), red=OVRD + LATE)
    ctx = gather("morning-briefing", scout_dir=vault, now=dt.datetime(2026, 4, 15, 7, 0, tzinfo=dt.UTC))
    assert ctx.past_due_source == d(-1).isoformat()
    assert [(t["tag"], t["branch"]) for t in ctx.past_due_tasks or []] == [("OVRD", STEP_DOWN), ("LATE", HOLD)]


def test_a_failed_pre_fetch_is_none(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.action_items.past_due as past_due
    from scout.scripts.pre_session_data import get_past_due_tasks

    def boom(*a: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(past_due, "past_due_tasks", boom)
    assert get_past_due_tasks(vault, TODAY) == ("", None)


def test_a_reader_that_sees_nothing_gets_no_past_due_set(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts.pre_session_data import gather

    _file(vault, d(-1), red=OVRD)
    monkeypatch.setenv("SCOUT_READER", "agent:person:priya@example.com")
    ctx = gather("morning-briefing", scout_dir=vault, now=dt.datetime(2026, 4, 15, 7, 0, tzinfo=dt.UTC))
    assert ctx.past_due_tasks == [] and ctx.past_due_source == ""

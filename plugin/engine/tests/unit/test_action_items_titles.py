"""*Clean Title, Prose Body, Refs Block* held to a length: `action-items check-titles`.

A new or changed task title is at most 80 characters; of the carried ones over
it, the 5 longest in 🔴 are named per run; Today's Focus holds at most 5
bullets of at most 300 characters.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.stale import STALE_AFTER_DAYS
from scout.action_items.titles import (
    FOCUS_MAX_BULLETS,
    LEGACY_PER_RUN,
    TITLE_MAX,
    check_titles,
    judge,
    list_long_titles,
    title_of,
)

runner = CliRunner()
TODAY = dt.date(2026, 4, 15)
SHORT = "Reply to Alex about the quota migration"
LONG = (
    "Reply to Alex about the quota migration and loop in Priya on the onboarding plan "
    "before the review with Sam on Thursday"
)
assert len(SHORT) <= TITLE_MAX < len(LONG)


def _task(tag: str, title: str = SHORT, body: str = "next step is yours.") -> str:
    return f"- [ ] [#{tag}] **{title}** — {body}\n"


def _lines(focus: str = "", red: str = "", yellow: str = "", green: str = "", parked: str = "") -> list[str]:
    return (
        f"# Action Items — {TODAY}\n\n## 💡 Today's Focus\n\n{focus}\n## 🔴 Urgent\n\n{red}\n## 🟡 To Do\n\n{yellow}\n"
        f"## 🟢 Watching\n\n{green}\n## 🅿️ Parked\n\n{parked}\n## ✅ Recently Completed\n\n## 🪵 Run notes\n"
    ).splitlines()


def _labels(**sections: str) -> list[str]:
    return [f.label for f in judge(_lines(**sections)).findings]


# ----- the title ----------------------------------------------------------------------


def test_title_is_the_first_bold_segment() -> None:
    assert title_of("- [ ] [#REPLYX] **Reply to Alex** — about **this**") == "Reply to Alex"


def test_title_without_bold_is_the_text_before_the_separator() -> None:
    assert title_of("- [ ] [#REPLYX] Reply to Alex — about the quota") == "Reply to Alex"


def test_title_ignores_a_lead_before_the_bold() -> None:
    assert title_of("- [ ] [#REPLYX] 🔴 🆕 **Reply to Alex** — now") == "Reply to Alex"


# ----- new and changed titles ---------------------------------------------------------


def test_a_short_title_passes() -> None:
    result = judge(_lines(red=_task("QMIG"), yellow=_task("NTX")))
    assert result.passed and result.checked == 2


def test_a_new_long_title_is_named_in_any_section() -> None:
    assert _labels(yellow=_task("QMIG", LONG)) == ["[#QMIG]"]
    assert _labels(green=_task("QMIG", LONG)) == ["[#QMIG]"]
    assert _labels(parked=_task("QMIG", LONG)) == ["[#QMIG]"]


def test_exactly_the_limit_passes() -> None:
    assert judge(_lines(red=_task("QMIG", "x" * TITLE_MAX))).passed
    assert not judge(_lines(red=_task("QMIG", "x" * (TITLE_MAX + 1)))).passed


def test_a_changed_long_title_is_named_even_when_it_was_long_before() -> None:
    earlier = _lines(yellow=_task("QMIG", LONG))
    result = judge(_lines(yellow=_task("QMIG", LONG + " now")), earlier=earlier)
    assert [f.tag for f in result.findings] == ["QMIG"]
    assert not result.findings[0].carried


def test_shortening_a_carried_title_passes() -> None:
    earlier = _lines(red=_task("QMIG", LONG))
    assert judge(_lines(red=_task("QMIG", SHORT)), earlier=earlier).passed


def test_a_body_change_alone_does_not_make_a_title_new() -> None:
    earlier = _lines(yellow=_task("QMIG", LONG))
    result = judge(_lines(yellow=_task("QMIG", LONG, "now waiting on Sam since Apr 14.")), earlier=earlier)
    assert result.passed and result.long_carried == 1


# ----- carried titles -----------------------------------------------------------------


def test_carried_long_titles_outside_red_are_left_for_a_later_touch() -> None:
    earlier = _lines(yellow=_task("QMIG", LONG), parked=_task("NTX", LONG))
    result = judge(_lines(yellow=_task("QMIG", LONG), parked=_task("NTX", LONG)), earlier=earlier)
    assert result.passed and result.long_carried == 2


def test_carried_red_titles_are_named_longest_first_and_capped_per_run() -> None:
    reds = "".join(_task(f"R{i}", LONG + " " + "x" * i) for i in range(LEGACY_PER_RUN + 2))
    earlier = _lines(red=reds)
    result = judge(_lines(red=reds), earlier=earlier)
    assert len(result.findings) == LEGACY_PER_RUN
    assert all(f.carried for f in result.findings)
    expected = [f"R{i}" for i in range(LEGACY_PER_RUN + 1, 1, -1)]
    assert [f.tag for f in result.findings] == expected
    assert result.long_carried == 2


def test_the_quota_comes_from_the_baseline_section_not_todays() -> None:
    # Moved into 🔴 this run with its title unchanged: carried, but not in the baseline's 🔴 quota.
    earlier = _lines(yellow=_task("QMIG", LONG))
    result = judge(_lines(red=_task("QMIG", LONG)), earlier=earlier)
    assert result.passed and result.long_carried == 1


def _reds(n: int, shortened: int = 0) -> str:
    """n long 🔴 tasks, R0 shortest … R(n-1) longest; the `shortened` longest have short titles."""
    return "".join(_task(f"R{i}", SHORT if i >= n - shortened else LONG + " " + "x" * i) for i in range(n))


def test_re_running_after_shortening_the_named_titles_passes() -> None:
    # The finding that motivated this: the quota re-picked from today's file named 5, then 5 more,
    # then 2, so one run rewrote every long 🔴 title (and restarted every one's stale clock).
    earlier = _lines(red=_reds(12))
    first = judge(_lines(red=_reds(12)), earlier=earlier)
    assert [f.tag for f in first.findings] == ["R11", "R10", "R9", "R8", "R7"]
    again = judge(_lines(red=_reds(12, shortened=LEGACY_PER_RUN)), earlier=earlier)
    assert again.passed and again.long_carried == 12 - LEGACY_PER_RUN


def test_a_named_title_shortened_but_still_long_is_named_as_changed() -> None:
    earlier = _lines(red=_task("QMIG", LONG + " and more"))
    result = judge(_lines(red=_task("QMIG", LONG)), earlier=earlier)
    assert [(f.tag, f.carried) for f in result.findings] == [("QMIG", False)]


def test_a_later_run_the_same_day_takes_the_next_titles_from_the_committed_file() -> None:
    yesterday = _lines(red=_reds(7))
    morning = _lines(red=_reds(7, shortened=LEGACY_PER_RUN))  # committed by the morning run
    result = judge(morning, earlier=yesterday, committed=morning)
    assert [f.tag for f in result.findings] == ["R1", "R0"]
    assert result.long_carried == 0


def test_a_long_title_a_run_committed_today_is_still_new() -> None:
    # A run that committed a long new title (it skipped the check, or the check couldn't run)
    # must not have it pass a later check that day: only the earlier file makes a title carried.
    yesterday = _lines(yellow=_task("QMIG", SHORT))
    committed = _lines(yellow=_task("QMIG", LONG), red=_task("NEW", LONG + " too"))
    result = judge(committed, earlier=yesterday, committed=committed)
    assert sorted((f.tag, f.carried) for f in result.findings) == [("NEW", False), ("QMIG", False)]


def test_nested_and_closed_rows_are_not_checked() -> None:
    nested = _task("PRNT") + "  " + _task("CHLD", LONG)
    assert judge(_lines(yellow=nested)).passed
    closed = f"- [x] [#DONE] **{LONG}** — done Apr 14.\n"
    assert judge(_lines(yellow=closed)).passed


# ----- Today's Focus ------------------------------------------------------------------


def _focus(n: int, text: str = "**Finish the migration** — the window closes today.") -> str:
    return "".join(f"- {text}\n" for _ in range(n))


def test_focus_within_limits_passes() -> None:
    assert judge(_lines(focus=_focus(FOCUS_MAX_BULLETS))).passed


def test_focus_with_too_many_bullets_is_named() -> None:
    result = judge(_lines(focus=_focus(FOCUS_MAX_BULLETS + 1)))
    assert [f.problem for f in result.findings] == [f"has {FOCUS_MAX_BULLETS + 1} bullets, over {FOCUS_MAX_BULLETS}"]


def test_a_paragraph_focus_bullet_is_named() -> None:
    result = judge(_lines(focus=_focus(1, "**Finish** — " + "word " * 80)))
    assert [f.label for f in result.findings] == ["Today's Focus"]
    assert result.findings[0].problem.startswith("bullet 1 is ")


# ----- from disk and the CLI ----------------------------------------------------------


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


def test_the_newest_earlier_file_is_the_baseline(vault: Path) -> None:
    _file(vault, TODAY - dt.timedelta(days=3), yellow=_task("QMIG", LONG))
    _file(vault, TODAY, yellow=_task("QMIG", LONG))
    result = check_titles(today=TODAY.isoformat())
    assert result.passed and result.long_carried == 1


def test_without_an_earlier_file_every_title_is_new(vault: Path) -> None:
    _file(vault, TODAY, yellow=_task("QMIG", LONG))
    assert [f.tag for f in check_titles(today=TODAY.isoformat()).findings] == ["QMIG"]


def test_today_as_last_committed_is_read_from_git(vault: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "alex@example.com")
    git("config", "user.name", "Alex")
    _file(vault, TODAY - dt.timedelta(days=1), red=_reds(7))
    _file(vault, TODAY, red=_reds(7, shortened=LEGACY_PER_RUN))  # the morning run's work
    git("add", "-A")
    git("commit", "-q", "-m", "morning")
    # The next run that day takes its quota from the committed file: the 2 still long.
    assert [f.tag for f in check_titles(today=TODAY.isoformat()).findings] == ["R1", "R0"]


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["check-titles", "--date", TODAY.isoformat(), *args])


def test_cli_exit_0(vault: Path) -> None:
    _file(vault, TODAY, red=_task("QMIG"))
    result = _cli()
    assert result.exit_code == 0 and "within the limits" in result.stdout


def test_cli_exit_1_names_each_problem(vault: Path) -> None:
    _file(vault, TODAY, red=_task("QMIG", LONG), focus=_focus(FOCUS_MAX_BULLETS + 1))
    result = _cli()
    assert result.exit_code == 1
    assert f"[#QMIG] title is {len(LONG)} characters, over {TITLE_MAX}" in result.stdout
    assert "Today's Focus has 6 bullets" in result.stdout
    assert "keep every fact, and keep the [#TAG]" in result.stdout


def test_cli_json(vault: Path) -> None:
    _file(vault, TODAY, red=_task("QMIG", LONG))
    result = _cli("--json")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["passed"] is False and out["checked"] == 1
    assert out["findings"][0] == {
        "label": "[#QMIG]",
        "tag": "QMIG",
        "problem": f"title is {len(LONG)} characters, over {TITLE_MAX}",
        "chars": len(LONG),
        "carried": False,
    }


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli()
    assert result.exit_code == 2 and "check-titles: cannot check" in result.stdout


# ----- the whole-file listing (`--all`) -----------------------------------------------
#
# A read-only inventory for the one-off title cleanup (#687): every open title
# over the budget, carried or not, with its stale clock. It names tags and
# lengths only, never a title, so its output can be pasted anywhere.

LONGER = LONG + " and the follow-up"
LONGEST = LONGER + " notes for the quarterly review"


def _all_tags(vault: Path) -> list[tuple[str, int]]:
    return [(t.tag, t.chars) for t in list_long_titles(today=TODAY.isoformat()).long]


def test_all_lists_every_long_title_in_every_section_longest_first(vault: Path) -> None:
    _file(
        vault,
        TODAY,
        red=_task("RED1", LONG),
        yellow=_task("YEL1", LONGEST) + _task("YEL2"),
        green=_task("GRN1", LONGER),
        parked=_task("PRK1", LONG),
    )
    assert _all_tags(vault) == [("YEL1", len(LONGEST)), ("GRN1", len(LONGER)), ("PRK1", len(LONG)), ("RED1", len(LONG))]


def test_all_counts_carried_and_new_alike_and_says_which(vault: Path) -> None:
    _file(vault, TODAY - dt.timedelta(days=1), yellow=_task("OLD1", LONG))
    _file(vault, TODAY, yellow=_task("OLD1", LONG) + _task("NEW1", LONG))
    listing = list_long_titles(today=TODAY.isoformat())
    assert {t.tag: t.carried for t in listing.long} == {"OLD1": True, "NEW1": False}
    assert listing.checked == 2


def test_all_skips_short_nested_and_closed_rows(vault: Path) -> None:
    rows = _task("OPEN", SHORT) + f"  - [ ] [#KIDX] **{LONG}** — nested\n" + f"- [x] [#DONE] **{LONG}** — done\n"
    _file(vault, TODAY, yellow=rows)
    assert _all_tags(vault) == []


def test_all_reports_each_items_stale_clock(vault: Path) -> None:
    first = TODAY - dt.timedelta(days=40)
    _file(vault, first, yellow=_task("IDLE", LONG) + _task("BUSY", LONG))
    _file(vault, TODAY - dt.timedelta(days=1), yellow=_task("IDLE", LONG) + _task("BUSY", LONG))
    _file(vault, TODAY, yellow=_task("IDLE", LONG) + _task("BUSY", LONG, body="a reply came in."))
    by_tag = {t.tag: t for t in list_long_titles(today=TODAY.isoformat()).long}
    assert by_tag["IDLE"].last_changed == first
    assert by_tag["IDLE"].archive_due == first + dt.timedelta(days=STALE_AFTER_DAYS)
    assert by_tag["BUSY"].last_changed == TODAY  # touched today: its clock restarts today


def test_all_writes_nothing(vault: Path) -> None:
    _file(vault, TODAY - dt.timedelta(days=1), yellow=_task("IDLE", LONG))
    _file(vault, TODAY, yellow=_task("IDLE", LONG))
    before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    list_long_titles(today=TODAY.isoformat())
    _cli("--all", "--json")
    assert {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()} == before


def test_cli_all_json_names_tags_and_lengths_never_titles(vault: Path) -> None:
    _file(vault, TODAY - dt.timedelta(days=1), red=_task("RED1", LONGEST))
    _file(vault, TODAY, red=_task("RED1", LONGEST) + _task("SHRT"), yellow=_task("YEL1", LONG))
    result = _cli("--all", "--json")
    assert result.exit_code == 0  # an inventory, not a gate
    out = json.loads(result.stdout)
    assert out["date"] == TODAY.isoformat() and out["limit"] == TITLE_MAX
    assert out["checked"] == 3 and out["over"] == 2 and out["longest"] == len(LONGEST)
    assert out["buckets"] == {"81-100": 0, "101-120": 1, "121+": 1}
    assert out["items"][0] == {
        "tag": "RED1",
        "chars": len(LONGEST),
        "section": "🔴",
        "carried": True,
        "last_changed": (TODAY - dt.timedelta(days=1)).isoformat(),
        "archive_due": (TODAY - dt.timedelta(days=1) + dt.timedelta(days=STALE_AFTER_DAYS)).isoformat(),
    }
    assert [i["tag"] for i in out["items"]] == ["RED1", "YEL1"]
    assert "quota migration" not in result.stdout and "Alex" not in result.stdout


def test_cli_all_text_is_a_count_line_then_one_line_per_tag(vault: Path) -> None:
    _file(vault, TODAY, yellow=_task("YEL1", LONG) + _task("YEL2"))
    result = _cli("--all")
    assert result.exit_code == 0
    head, *rows = result.stdout.splitlines()
    assert head == (
        f"check-titles --all: 1 of 2 open titles over {TITLE_MAX} characters "
        f"(longest {len(LONG)}; 81-100: 0, 101-120: 1, 121+: 0)"
    )
    due = TODAY + dt.timedelta(days=STALE_AFTER_DAYS)
    assert rows == [f"  [#YEL1] {len(LONG)} chars · 🟡 · new · unchanged since {TODAY} · archives {due} if untouched"]
    assert "quota migration" not in result.stdout


def test_cli_all_with_nothing_long(vault: Path) -> None:
    _file(vault, TODAY, yellow=_task("YEL2"))
    result = _cli("--all")
    assert result.exit_code == 0
    assert result.stdout.startswith(f"check-titles --all: 0 of 1 open titles over {TITLE_MAX} characters")


def test_cli_all_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli("--all", "--json")
    assert result.exit_code == 2 and json.loads(result.stdout)["error"]

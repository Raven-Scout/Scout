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
from scout.action_items.titles import (
    FOCUS_MAX_BULLETS,
    LEGACY_PER_RUN,
    TITLE_MAX,
    check_titles,
    judge,
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


def test_a_task_moved_into_red_with_its_title_unchanged_counts_as_carried() -> None:
    earlier = _lines(yellow=_task("QMIG", LONG))
    result = judge(_lines(red=_task("QMIG", LONG)), earlier=earlier)
    assert [f.carried for f in result.findings] == [True]


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

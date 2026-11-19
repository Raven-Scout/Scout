"""``scoutctl action-items fit`` and ``check-size``: the daily file's budget.

Fixtures are anonymized per CLAUDE.md (Alex / Priya / Sam, PROJ-1234,
example-org) and every `[#TAG]` here is invented.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items import stale
from scout.action_items.budget import ActionItemsSettings, Budget, measure, sub_bullet_groups
from scout.action_items.cli import app
from scout.action_items.fit import fit, note_with_moved, plan_fit
from scout.action_items.materialize import materialize
from scout.action_items.past_due import History

TODAY = _dt.date(2026, 11, 19)
SOON = "due 2026-11-25"
FAR = "due 2026-12-31"
LATE = "due 2026-11-10; still want this?"


def _task(tag: str, title: str, body: str = "", *subs: str) -> list[str]:
    line = f"- [ ] [#{tag}] **{title}**" + (f" — {body}" if body else "")
    return [line, *(f"  - {s}" for s in subs)]


def _doc(*sections: tuple[str, list[str]]) -> list[str]:
    out = ["# Action Items — Thursday, Nov 19, 2026", ""]
    for heading, body in sections:
        out += [heading, "", *body, ""]
    return out


def _settings(**budget: int) -> ActionItemsSettings:
    return ActionItemsSettings(budget=Budget(**budget)) if budget else ActionItemsSettings()


def _plan(daily: list[str], backlog: list[str] | None = None, settings: ActionItemsSettings | None = None):  # noqa: ANN202
    return plan_fit(
        daily,
        backlog or [],
        today=TODAY,
        settings=settings or _settings(),
        history=History(None, overrides={TODAY: daily}),
    )


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "action-items" / "archive").mkdir(parents=True)
    return tmp_path


def _write_daily(vault: Path, lines: list[str], day: _dt.date = TODAY) -> Path:
    path = vault / "action-items" / f"action-items-{day.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tags(lines: list[str]) -> list[str]:
    return [b.tag for b in stale.task_blocks(lines)]


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # History(None) and load_settings() must never read a real vault.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "no-vault"))


# --- settings ------------------------------------------------------------------


def test_settings_defaults_match_the_packaged_config() -> None:
    from scout import config

    assert ActionItemsSettings.from_config(config._read_packaged_defaults()) == ActionItemsSettings()


def test_bad_settings_fall_back_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    got = ActionItemsSettings.from_config(
        {
            "action_items": {
                "fit": "always",
                "backlog_due_days": -1,
                "recently_completed_days": "soon",
                "budget": {"target_open": 60, "hard_bytes": True},
            }
        }
    )
    assert got == ActionItemsSettings(budget=Budget(target_open=60))
    err = capsys.readouterr().err
    assert "action_items.fit" in err and "backlog_due_days" in err and "hard_bytes" in err


def test_settings_read_apply_and_a_non_mapping_budget(capsys: pytest.CaptureFixture[str]) -> None:
    got = ActionItemsSettings.from_config({"action_items": {"fit": "apply", "budget": 5}})
    assert got.fit == "apply" and got.budget == Budget()
    assert "expected a mapping" in capsys.readouterr().err
    assert ActionItemsSettings.from_config({"action_items": "nope"}) == ActionItemsSettings()


# --- classifying sub-bullets -----------------------------------------------------


def test_machine_lines_are_never_prose() -> None:
    block = _task(
        "ABCD",
        "Reply to Alex",
        "",
        "🔬 Research: the vendor answered twice.",
        "Refs: [[PROJ-1234]]",
        "Action: Reply",
        "Topic: #PROJ",
        "estimate: 30m",
        "snoozed-until: 2026-12-01",
        "alex: please keep this short",
        "[ ] [#CHLD] **A nested task**",
        "📎 a second note",
    )
    kinds = [is_prose for is_prose, _ in sub_bullet_groups(block)]
    assert kinds == [True, False, False, False, False, False, False, False, True]


def test_a_group_keeps_its_deeper_lines() -> None:
    block = ["- [ ] [#ABCD] **T**", "  - first", "    - detail", "  continuation", "  - second"]
    groups = sub_bullet_groups(block)
    assert [g for _, g in groups] == [["  - first", "    - detail", "  continuation"], ["  - second"]]
    assert sub_bullet_groups(["- [ ] [#ABCD] **T**", "  plain text"]) == [(False, ["  plain text"])]
    assert sub_bullet_groups(["- [ ] [#ABCD] **T**"]) == []


# --- measuring ---------------------------------------------------------------------


def test_measure_levels_and_problems() -> None:
    lines = _doc(("## 🟡 To Do", _task("AAAA", "One", SOON, "a", "b") + _task("BBBB", "Two", SOON)))
    budget = Budget(target_bytes=10_000, target_open=1, hard_bytes=20_000, hard_open=5)
    report = measure("\n".join(lines), budget)
    assert report.open_tasks == 2 and report.over_prose == ("AAAA",)
    assert report.level(budget) == "target"
    problems = report.problems(budget)
    assert any("over the target of 1" in p for p in problems)
    assert any("1 task with more than 1 prose sub-bullet" in p for p in problems)
    assert report.to_json(budget)["level"] == "target"

    tight = Budget(target_bytes=10, hard_bytes=20, target_open=0, hard_open=1)
    assert report.level(tight) == "hard"
    assert any("hard limit" in p for p in report.problems(tight))
    small = measure("- [ ] [#AAAA] **x**\n", Budget())
    assert small.level(Budget()) == "ok" and small.problems(Budget()) == []


def test_measure_ignores_fenced_code() -> None:
    text = "```\n- [ ] [#AAAA] **in a fence**\n```\n- [ ] [#BBBB] **real**\n"
    assert measure(text, Budget()).open_tasks == 1


def test_measure_target_bytes_message() -> None:
    report = measure("x" * 2048, Budget(target_bytes=1024, hard_bytes=4096))
    assert report.problems(Budget(target_bytes=1024, hard_bytes=4096)) == ["2.0 KB is over the 1 KB target"]


# --- the plan --------------------------------------------------------------------------


def test_parked_watching_and_far_yellow_move_to_the_backlog() -> None:
    daily = _doc(
        ("## 🔴 Urgent", _task("RRRR", "Ship it", "due 2026-11-01")),
        (
            "## 🟡 To Do",
            _task("SOON", "Due soon", SOON)
            + _task("LATE", "Past due", LATE)
            + _task("NODU", "No due date", "ask Sam")
            + _task("FARR", "Far off", FAR),
        ),
        ("## 🟢 Watching", _task("WTCH", "Waiting on Priya", "waiting on Priya since Nov 2")),
        ("## 🅿️ Parked", _task("PARK", "Parked one", "due Oct 1; still want this?")),
        ("## 🐾 Personal", _task("PERS", "Dentist")),
    )
    result = _plan(daily)
    assert _tags(result.daily) == ["RRRR", "SOON", "LATE", "PERS"]
    assert {m.tag: m.reason for m in result.moves} == {
        "NODU": "no due date",
        "FARR": "due 2026-12-31",
        "WTCH": "watching",
        "PARK": "parked",
    }
    assert "## 🅿️ Parked" not in result.daily
    backlog = "\n".join(result.backlog)
    assert backlog.startswith("# Backlog")
    assert backlog.index("## 🟡 To Do (soon)") < backlog.index("[#NODU]") < backlog.index("## 🟢 Watching")
    assert backlog.index("## 🟢 Watching") < backlog.index("[#WTCH]") < backlog.index("## 🅿️ Parked")
    assert backlog.index("## 🅿️ Parked") < backlog.index("[#PARK]")
    assert "🗂️ Backlog: [[backlog]] (4 items)" in result.daily
    assert result.open_before == 8 and result.open_after == 4
    assert "4 to the backlog" in result.summary()


def test_backlog_yellow_coming_due_returns_soonest_first() -> None:
    daily = _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON)))
    backlog = [
        "# Backlog",
        "",
        "## 🟡 To Do (soon)",
        "",
        *_task("LATR", "Due later in window", "due 2026-11-30"),
        *_task("FIRS", "Due first", "due 2026-11-20"),
        *_task("STAY", "Still far", FAR),
        "",
        "## 🅿️ Parked",
        "",
        *_task("OLDP", "Parked and past due", "due Oct 1; still want this?"),
    ]
    result = _plan(daily, backlog)
    assert _tags(result.daily) == ["KEEP", "FIRS", "LATR"]
    assert _tags(result.backlog) == ["STAY", "OLDP"]
    assert [m.to for m in result.moves] == ["daily", "daily"]
    assert "back from the backlog" in result.summary()


def test_the_cap_spills_the_bottom_of_yellow_and_holds_promotions() -> None:
    yellow = [ln for i in range(5) for ln in _task(f"Y{i}AA", f"Task {i}", SOON)]
    daily = _doc(("## 🔴 Urgent", _task("RRRR", "Red", SOON)), ("## 🟡 To Do", yellow))
    backlog = ["## 🟡 To Do (soon)", *_task("BACK", "Comes due", SOON)]
    result = _plan(daily, backlog, _settings(target_open=4))
    assert _tags(result.daily) == ["RRRR", "Y0AA", "Y1AA", "Y2AA"]
    assert {m.tag for m in result.moves} == {"Y3AA", "Y4AA"}
    assert all(m.reason == "over the open-task cap" for m in result.moves)
    assert "BACK" in _tags(result.backlog)
    # Red never spills.
    red_only = _doc(("## 🔴 Urgent", _task("R1AA", "a", SOON) + _task("R2AA", "b", SOON)))
    assert _plan(red_only, settings=_settings(target_open=1)).moves == []


def test_extra_prose_moves_to_the_note_and_machine_lines_stay() -> None:
    daily = _doc(
        (
            "## 🟡 To Do",
            _task("ABCD", "Reply to Alex", SOON, "🔬 first note", "Refs: [[PROJ-1234]]", "📎 second", "alex: keep")
            + ["    - deeper detail"],
        )
    )
    result = _plan(daily)
    block = result.daily[result.daily.index("- [ ] [#ABCD] **Reply to Alex** — due 2026-11-25") :][:4]
    assert block[1:] == ["  - 🔬 first note", "  - Refs: [[PROJ-1234]]", "  - alex: keep"]
    assert result.to_notes == {"ABCD": ["  - 📎 second"]}
    assert result.titles == {"ABCD": "Reply to Alex"}
    assert len(result.rewrites) == 1 and result.rewrites[0].tag == "ABCD"
    assert "1 context line from 1 tasks" in result.summary()


def test_blocks_moving_to_the_backlog_are_trimmed_too() -> None:
    daily = _doc(("## 🟢 Watching", _task("WTCH", "Waiting", "waiting on Sam", "one", "two", "three")))
    result = _plan(daily)
    assert result.to_notes == {"WTCH": ["  - two", "  - three"]}
    assert "  - two" not in result.backlog


def test_old_recently_completed_entries_are_dropped() -> None:
    daily = _doc(
        ("## 🟡 To Do", _task("KEEP", "Kept", SOON)),
        (
            "## ✅ Recently Completed",
            [
                "- [x] [#NEW1] ✅ ~~**Done today**~~ — closed 2026-11-19",
                "  - detail",
                "- [x] [#OLD1] ✅ ~~**Done last week**~~ — closed 2026-11-12",
                "  - detail that goes with it",
                "### 2026-11-10",
                "- [x] [#OLD2] ✅ ~~**Under an old heading**~~",
                "### 2026-11-18",
                "- [x] [#NEW2] ✅ ~~**Under a recent heading**~~",
                "- [x] [#UNDT] ✅ ~~**No date anywhere**~~",
            ],
        ),
    )
    result = _plan(daily)
    text = "\n".join(result.daily)
    assert "NEW1" in text and "NEW2" in text and "UNDT" in text
    assert "OLD1" not in text and "detail that goes with it" not in text
    assert "OLD2" not in text and "### 2026-11-10" not in text
    assert result.completed_dropped == 2


def test_fit_is_idempotent_and_needs_no_backlog_line_when_empty() -> None:
    daily = _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON)))
    first = _plan(daily)
    assert not first.changed and "🗂️" not in "\n".join(first.daily)
    busy = _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON) + _task("NODU", "None")))
    once = _plan(busy)
    twice = _plan(once.daily, once.backlog)
    assert not twice.changed
    assert twice.daily == once.daily and twice.backlog == once.backlog


def test_the_backlog_line_updates_in_place_and_leaves_with_the_backlog() -> None:
    busy = _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON) + _task("NODU", "None")))
    once = _plan(busy)
    more = _plan(once.daily, [*once.backlog, *_task("MORE", "Another", FAR)])
    assert "🗂️ Backlog: [[backlog]] (2 items)" in more.daily
    assert [ln for ln in more.daily if ln.startswith("🗂️")] == ["🗂️ Backlog: [[backlog]] (2 items)"]
    emptied = _plan(once.daily, [])
    assert emptied.daily == _plan(_doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON))), []).daily


def test_fit_writes_a_backlog_count_change_alone(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    daily = _write_daily(vault, _busy_daily())
    fit(data_dir=vault, date=TODAY, apply=True)
    stale.backlog_path(vault).write_text("# Backlog\n", encoding="utf-8")  # e.g. the stale archive emptied it
    result = fit(data_dir=vault, date=TODAY, apply=True)
    assert result is not None and not result.changed
    assert "🗂️" not in daily.read_text()


def test_a_yellow_section_is_created_for_promotions_and_the_backlog_line() -> None:
    daily = _doc(("## 🔴 Urgent", _task("RRRR", "Red", SOON)), ("## 🐾 Personal", _task("PERS", "Dentist")))
    backlog = ["## 🟡 To Do (soon)", *_task("BACK", "Comes due", SOON), *_task("STAY", "Later", FAR)]
    result = _plan(daily, backlog)
    text = "\n".join(result.daily)
    assert text.index("## 🔴") < text.index("## 🟡 To Do") < text.index("## 🐾")
    assert "[#BACK]" in text and "🗂️ Backlog: [[backlog]] (1 item)" in text


# --- context notes -------------------------------------------------------------------------


def test_note_with_moved_creates_or_appends() -> None:
    new = note_with_moved(None, tag="ABCD", title='Reply to "Alex"', day="2026-11-19", moved=["  - one", "    - two"])
    assert new.startswith('---\ntag: ABCD\ntitle: "Reply to \\"Alex\\""\ncreated: 2026-11-19\nupdated: 2026-11-19\n---')
    assert "## Moved from the daily file (2026-11-19)\n\n- one\n  - two\n" in new
    existing = "---\ntag: ABCD\ntitle: x\ncreated: 2026-10-01\nupdated: 2026-10-02\n---\n\nBody."
    appended = note_with_moved(existing, tag="ABCD", title="x", day="2026-11-19", moved=["  - one"])
    assert "updated: 2026-11-19" in appended and "created: 2026-10-01" in appended
    assert appended.endswith("Body.\n\n## Moved from the daily file (2026-11-19)\n\n- one\n")
    bare = note_with_moved("Just text", tag="ABCD", title="x", day="2026-11-19", moved=["  - one"])
    assert bare.startswith("Just text\n\n## Moved")


# --- on disk -------------------------------------------------------------------------------


def _busy_daily() -> list[str]:
    return _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON, "one", "two") + _task("NODU", "No due")))


def test_report_mode_writes_only_the_report(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    daily = _write_daily(vault, _busy_daily())
    before = daily.read_text()
    result = fit(data_dir=vault, date=TODAY)
    assert result is not None and result.changed
    assert daily.read_text() == before
    assert not stale.backlog_path(vault).exists()
    assert not (vault / "action-items" / "context").exists()
    report = json.loads((vault / ".scout-logs" / "action-items-fit.json").read_text())
    assert report["mode"] == "report" and report["applied"] is False
    assert report["moves"] == [{"tag": "NODU", "from": "🟡", "to": "backlog", "reason": "no due date"}]
    assert report["to_notes"] == {"KEEP": 1}


def test_apply_writes_the_backlog_notes_and_rewrite_records(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    daily = _write_daily(vault, _busy_daily())
    (vault / "scout-config.yaml").write_text("action_items:\n  fit: apply\n", encoding="utf-8")
    result = fit(data_dir=vault, date=TODAY)
    assert result is not None
    text = daily.read_text()
    assert "[#NODU]" not in text and "  - two" not in text and "🗂️ Backlog: [[backlog]] (1 item)" in text
    assert "[#NODU]" in stale.backlog_path(vault).read_text()
    assert "- two" in (vault / "action-items" / "context" / "KEEP.md").read_text()
    records = stale.load_rewrites(vault)
    assert ("KEEP", TODAY) in records
    assert fit(data_dir=vault, date=TODAY) is not None
    assert daily.read_text() == text  # idempotent on disk


def test_a_trim_does_not_restart_the_stale_clock(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    yesterday = TODAY - _dt.timedelta(days=1)
    lines = _busy_daily()
    _write_daily(vault, lines, yesterday)
    _write_daily(vault, lines)
    fit(data_dir=vault, date=TODAY, apply=True)
    ledger = stale.ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY + _dt.timedelta(days=1))
    assert ledger.last_changed("KEEP") == yesterday
    # NODU moved to the backlog the same way.
    ledger.observe_backlog(stale.backlog_path(vault).read_text().splitlines(), on=TODAY + _dt.timedelta(days=1))
    assert ledger.last_changed("NODU") == yesterday


def test_fit_without_a_daily_file_returns_none(tmp_path: Path) -> None:
    assert fit(data_dir=_vault(tmp_path), date=TODAY) is None


def test_materialize_reports_by_default_and_fits_on_apply(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, _busy_daily(), TODAY - _dt.timedelta(days=1))
    created = materialize(data_dir=vault, date=TODAY)
    assert created is not None and "[#NODU]" in created.read_text()
    assert (vault / ".scout-logs" / "action-items-fit.json").exists()

    other = _vault(tmp_path / "apply")
    (other / "scout-config.yaml").write_text("action_items:\n  fit: apply\n", encoding="utf-8")
    _write_daily(other, _busy_daily(), TODAY - _dt.timedelta(days=1))
    created = materialize(data_dir=other, date=TODAY)
    assert created is not None and "[#NODU]" not in created.read_text()
    assert "[#NODU]" in stale.backlog_path(other).read_text()


def test_materialize_survives_a_failing_fit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.action_items.fit as fit_module

    def boom(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(fit_module, "fit", boom)
    vault = _vault(tmp_path)
    _write_daily(vault, _busy_daily(), TODAY - _dt.timedelta(days=1))
    assert materialize(data_dir=vault, date=TODAY) is not None


# --- the CLI -------------------------------------------------------------------------------

runner = CliRunner()


def test_cli_fit_dry_run_apply_and_json(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    daily = _write_daily(vault, _busy_daily())
    args = ["fit", "--date", TODAY.isoformat(), "--data-dir", str(vault)]
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert "would fit" in out.output and "[#NODU] 🟡 → backlog (no due date)" in out.output
    assert "[#NODU]" in daily.read_text()
    out = runner.invoke(app, [*args, "--json"])
    assert json.loads(out.output)["applied"] is False
    out = runner.invoke(app, [*args, "--apply"])
    assert out.exit_code == 0 and "fit: fitted" in out.output
    out = runner.invoke(app, args)
    assert "nothing to move" in out.output


def test_cli_fit_errors(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    out = runner.invoke(app, ["fit", "--date", "nope", "--data-dir", str(vault)])
    assert out.exit_code != 0
    out = runner.invoke(app, ["fit", "--date", TODAY.isoformat(), "--data-dir", str(vault)])
    assert out.exit_code != 0


def test_cli_check_size(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    args = ["check-size", "--date", TODAY.isoformat(), "--data-dir", str(vault)]
    out = runner.invoke(app, args)
    assert out.exit_code == 2 and "cannot check" in out.output

    _write_daily(vault, _doc(("## 🟡 To Do", _task("KEEP", "Kept", SOON))))
    out = runner.invoke(app, args)
    assert out.exit_code == 0 and "within budget" in out.output

    _write_daily(vault, _busy_daily())
    out = runner.invoke(app, args)
    assert out.exit_code == 1 and "over the target" in out.output and "Run notes" in out.output
    out = runner.invoke(app, [*args, "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["over_prose"] == ["KEEP"]

    (vault / "scout-config.yaml").write_text("action_items:\n  budget:\n    hard_open: 1\n", encoding="utf-8")
    out = runner.invoke(app, args)
    assert out.exit_code == 1 and "over the hard limit" in out.output


# --- the doctor ----------------------------------------------------------------------------


def test_doctor_warns_only_past_the_hard_limit(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import _check_action_items_size

    vault = _vault(tmp_path)
    assert _check_action_items_size(vault=vault) == []
    _write_daily(vault, _busy_daily())
    assert _check_action_items_size(vault=vault) == []  # over the target only
    (vault / "scout-config.yaml").write_text("action_items:\n  budget:\n    hard_open: 1\n", encoding="utf-8")
    [warning] = _check_action_items_size(vault=vault)
    assert warning.startswith(f"action items: action-items-{TODAY.isoformat()}.md is past its hard limit")
    assert "fit --dry-run" in warning
    assert _check_action_items_size(vault=tmp_path / "missing") == []


# --- edges ------------------------------------------------------------------------------------


def test_settings_without_fit_or_budget_keys_keep_defaults() -> None:
    got = ActionItemsSettings.from_config({"action_items": {"backlog_due_days": 3}})
    assert got == ActionItemsSettings(backlog_due_days=3)


def test_text_before_the_first_sub_bullet_is_not_prose() -> None:
    groups = sub_bullet_groups(["- [ ] [#ABCD] **T**", "  a wrapped line", "  - a bullet"])
    assert groups == [(False, ["  a wrapped line"]), (True, ["  - a bullet"])]


def test_a_moved_group_drops_its_trailing_blank() -> None:
    daily = _doc(("## 🟡 To Do", [*_task("ABCD", "T", SOON, "one", "two"), "", "  - Refs: [[PROJ-1234]]"]))
    assert _plan(daily).to_notes == {"ABCD": ["  - two"]}


def test_completed_dates_from_bad_stamps_and_history() -> None:
    entries = [
        "- [x] [#BADD] ✅ ~~**Bad stamp**~~ — closed 2026-13-45",
        "- [x] [#NOSD] ✅ ~~**No stamp, seen only today**~~",
        "### 2026-02-30",
        "- [x] [#BADH] ✅ ~~**Under a bad heading**~~",
    ]
    result = _plan(_doc(("## ✅ Recently Completed", entries)))
    assert result.completed_dropped == 0
    assert all(f"[#{t}]" in "\n".join(result.daily) for t in ("BADD", "NOSD", "BADH"))
    assert "old Recently Completed" not in result.summary()
    dropped = _plan(_doc(("## ✅ Recently Completed", ["- [x] [#OLD1] ✅ ~~**x**~~ — closed 2026-11-01"])))
    assert "1 old Recently Completed entries dropped" in dropped.summary()


def test_a_parked_section_with_other_content_stays() -> None:
    daily = _doc(("## 🅿️ Parked", [*_task("PARK", "Parked"), "- [x] [#DONE] ✅ ~~**finished**~~"]))
    result = _plan(daily)
    assert "## 🅿️ Parked" in result.daily and "PARK" not in _tags(result.daily)


def test_an_unwritable_report_is_skipped(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, _busy_daily())
    (vault / ".scout-logs").write_text("not a folder", encoding="utf-8")
    assert fit(data_dir=vault, date=TODAY) is not None

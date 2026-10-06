"""Unit tests for scout.action_items.stale — auto-archiving open items nobody
has touched for STALE_AFTER_DAYS.

Fixtures are anonymized per CLAUDE.md (Alex / Priya / Sam, PROJ-1234,
example-org) and every `[#TAG]` here is invented.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items import stale
from scout.action_items.cli import app
from scout.action_items.materialize import materialize
from scout.action_items.stale import (
    STALE_AFTER_DAYS,
    ActivityLedger,
    archive_stale,
    split_stale,
    task_blocks,
)

TODAY = _dt.date(2026, 10, 4)
OLD = TODAY - _dt.timedelta(days=STALE_AFTER_DAYS)  # exactly at the threshold
RECENT = TODAY - _dt.timedelta(days=STALE_AFTER_DAYS - 1)

STALE_BLOCK = [
    "- [ ] [#ZQOLD] **Send Priya the migration notes** — she asked in July.",
    "  - Source: Slack",
    "  - Refs: [[people/priya]] · [[PROJ-1234]]",
]
FRESH_BLOCK = [
    "- [ ] [#ZQNEW] **Review Sam's PR** — example-org/app#42 is waiting.",
    "  - Source: GitHub",
]


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "action-items" / "archive").mkdir(parents=True)
    return tmp_path


def _daily(vault: Path, day: _dt.date, lines: list[str], *, archived: bool = False) -> Path:
    folder = vault / "action-items" / ("archive" if archived else "")
    path = folder / f"action-items-{day.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _doc(*sections: tuple[str, list[str]]) -> list[str]:
    out = ["# Action Items — test", ""]
    for heading, body in sections:
        out += [heading, "", *body, ""]
    return out


# --- task_blocks -------------------------------------------------------------


def test_block_spans_its_indented_sub_bullets() -> None:
    lines = ["## 🟡 To Do", "", *STALE_BLOCK, "", *FRESH_BLOCK]
    blocks = task_blocks(lines)
    assert [(b.tag, b.start, b.end) for b in blocks] == [("ZQOLD", 2, 5), ("ZQNEW", 6, 8)]


def test_block_continues_across_a_blank_line_when_indented_content_follows() -> None:
    lines = [
        "- [ ] [#ZQOLD] **Reply to Alex**",
        "  - Source: Slack",
        "",
        "  <details><summary>Earlier framing</summary>",
        "  </details>",
        "",
        "- [ ] [#ZQNEW] **Next**",
    ]
    assert [(b.tag, b.start, b.end) for b in task_blocks(lines)] == [("ZQOLD", 0, 5), ("ZQNEW", 6, 7)]


def test_blocks_skip_done_untagged_nested_fenced_and_commented_rows() -> None:
    lines = [
        "- [x] [#ZQDONE] **Already done**",
        "- [ ] **No tag at all**",
        "- [ ] [#ZQPAR] **Parent**",
        "  - [ ] [#ZQKID] **Nested child moves with its parent**",
        "```",
        "- [ ] [#ZQFENCE] **Example in a code fence**",
        "```",
        "<!-- deleted rows:",
        "- [ ] [#ZQGONE] **Kept for the audit trail**",
        "-->",
    ]
    assert [b.tag for b in task_blocks(lines)] == ["ZQPAR"]


# --- ActivityLedger ----------------------------------------------------------


def test_ledger_dates_an_unchanged_item_from_its_first_appearance(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _daily(vault, OLD + _dt.timedelta(days=1), _doc(("## 🟢 Watching", STALE_BLOCK)), archived=True)
    _daily(vault, TODAY - _dt.timedelta(days=1), _doc(("## 🟢 Watching", STALE_BLOCK)))

    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)

    # Moving between sections is not a touch; only the item's own text is.
    assert ledger.last_changed("ZQOLD") == OLD


def test_ledger_restarts_the_clock_when_the_text_changes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    edited = [*STALE_BLOCK, "  - alex: chased again, no reply yet"]
    _daily(vault, RECENT, _doc(("## 🟡 To Do", edited)))

    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)

    assert ledger.last_changed("ZQOLD") == RECENT


def test_ledger_treats_a_reappearing_item_as_new(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    back = TODAY - _dt.timedelta(days=10)
    _daily(vault, back - _dt.timedelta(days=1), _doc(("## 🟡 To Do", FRESH_BLOCK)), archived=True)
    _daily(vault, back, _doc(("## 🟡 To Do", STALE_BLOCK)))

    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)

    assert ledger.last_changed("ZQOLD") == back


def test_ledger_persists_and_only_replays_newer_files(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    first = ActivityLedger.load(vault)
    first.catch_up(vault, before=TODAY)
    first.save()

    # A file already replayed is not re-read: deleting it changes nothing.
    (vault / "action-items" / "archive" / f"action-items-{OLD.isoformat()}.md").unlink()
    again = ActivityLedger.load(vault)
    again.catch_up(vault, before=TODAY)
    assert again.last_changed("ZQOLD") == OLD


# --- split_stale -------------------------------------------------------------


def _ledger_with(vault: Path, history: dict[_dt.date, list[str]]) -> ActivityLedger:
    for day, lines in history.items():
        _daily(vault, day, lines, archived=True)
    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)
    return ledger


def test_split_removes_only_items_at_or_past_the_threshold(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    doc = _doc(("## 🟡 To Do", [*STALE_BLOCK, "", *FRESH_BLOCK]))
    ledger = _ledger_with(
        vault,
        {
            OLD: _doc(("## 🟡 To Do", STALE_BLOCK)),
            RECENT: doc,
        },
    )

    result = split_stale(doc, ledger, today=TODAY)

    assert [a.tag for a in result.archived] == ["ZQOLD"]
    assert result.archived[0].last_changed == OLD
    assert result.archived[0].lines == STALE_BLOCK
    assert "[#ZQOLD]" not in "\n".join(result.lines)
    assert FRESH_BLOCK[0] in result.lines
    # No doubled blank line left where the block was.
    assert "\n\n\n" not in "\n".join(result.lines)


def test_split_keeps_an_item_edited_today(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ledger = _ledger_with(vault, {OLD: _doc(("## 🟡 To Do", STALE_BLOCK))})
    today_doc = _doc(("## 🟡 To Do", [*STALE_BLOCK, "  - sam: picking this up"]))

    assert split_stale(today_doc, ledger, today=TODAY).archived == []


def test_split_keeps_an_item_the_ledger_has_never_seen(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ledger = _ledger_with(vault, {})
    doc = _doc(("## 🟡 To Do", STALE_BLOCK))

    assert split_stale(doc, ledger, today=TODAY).archived == []


def test_split_keeps_an_item_snoozed_past_today(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    snoozed = [*STALE_BLOCK, "  - snoozed-until: 2026-11-01"]
    ledger = _ledger_with(vault, {OLD: _doc(("## 🟢 Watching", snoozed))})

    assert split_stale(_doc(("## 🟢 Watching", snoozed)), ledger, today=TODAY).archived == []


def test_split_drops_a_subsection_heading_it_emptied_and_no_other(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    doc = _doc(
        (
            "## 🟡 To Do",
            [
                "### From the Jul run",
                "",
                *STALE_BLOCK,
                "",
                "### From this run",
                "",
                *FRESH_BLOCK,
                "",
                "### Empty to begin with",
            ],
        )
    )
    ledger = _ledger_with(vault, {OLD: _doc(("## 🟡 To Do", STALE_BLOCK))})

    out = split_stale(doc, ledger, today=TODAY).lines

    assert "### From the Jul run" not in out
    assert "### From this run" in out
    assert "### Empty to begin with" in out
    assert "## 🟡 To Do" in out


def test_split_archives_every_row_sharing_a_stale_tag(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    doc = _doc(("## 🔴 Urgent", STALE_BLOCK), ("## 🟡 To Do", STALE_BLOCK))
    ledger = _ledger_with(vault, {OLD: doc})

    result = split_stale(doc, ledger, today=TODAY)

    assert [a.tag for a in result.archived] == ["ZQOLD", "ZQOLD"]
    assert "[#ZQOLD]" not in "\n".join(result.lines)


# --- materialize integration -------------------------------------------------


def test_materialize_archives_stale_items_into_the_monthly_file(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    yesterday = _doc(("## 🟡 To Do", [*STALE_BLOCK, "", *FRESH_BLOCK]))
    _daily(vault, TODAY - _dt.timedelta(days=1), yesterday)

    created = materialize(data_dir=vault, date=TODAY)

    assert created is not None
    text = created.read_text(encoding="utf-8")
    assert "[#ZQOLD]" not in text
    assert FRESH_BLOCK[0] in text
    assert "1 item untouched for 60+ days auto-archived" in text
    assert "[[archive/stale-items-2026-10]]" in text

    archive = (vault / "action-items" / "archive" / "stale-items-2026-10.md").read_text(encoding="utf-8")
    assert STALE_BLOCK[0] in archive
    assert f"  - _auto-archived {TODAY.isoformat()} · unchanged since {OLD.isoformat()}_" in archive


def test_materialize_copies_verbatim_when_archiving_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _daily(vault, TODAY - _dt.timedelta(days=1), _doc(("## 🟡 To Do", STALE_BLOCK)))

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(stale, "split_stale", boom)
    created = materialize(data_dir=vault, date=TODAY)

    assert created is not None
    assert STALE_BLOCK[0] in created.read_text(encoding="utf-8")


def test_materialize_still_copies_when_nothing_is_stale(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, TODAY - _dt.timedelta(days=1), _doc(("## 🟡 To Do", FRESH_BLOCK)))

    created = materialize(data_dir=vault, date=TODAY)

    assert created is not None
    text = created.read_text(encoding="utf-8")
    assert FRESH_BLOCK[0] in text
    assert "auto-archived" not in text
    assert not (vault / "action-items" / "archive" / "stale-items-2026-10.md").exists()


# --- archive_stale (in place) + CLI ------------------------------------------


def test_archive_stale_rewrites_the_day_in_place_and_appends_to_the_archive(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    today = _daily(vault, TODAY, _doc(("## 🟡 To Do", [*STALE_BLOCK, "", *FRESH_BLOCK])))

    archived = archive_stale(data_dir=vault, date=TODAY)
    again = archive_stale(data_dir=vault, date=TODAY)

    assert [a.tag for a in archived] == ["ZQOLD"]
    assert again == []
    assert "[#ZQOLD]" not in today.read_text(encoding="utf-8")
    archive = (vault / "action-items" / "archive" / "stale-items-2026-10.md").read_text(encoding="utf-8")
    assert archive.count("[#ZQOLD]") == 1


def test_archive_stale_dry_run_writes_nothing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    today = _daily(vault, TODAY, _doc(("## 🟡 To Do", STALE_BLOCK)))
    before = today.read_text(encoding="utf-8")

    archived = archive_stale(data_dir=vault, date=TODAY, dry_run=True)

    assert [a.tag for a in archived] == ["ZQOLD"]
    assert today.read_text(encoding="utf-8") == before
    assert not (vault / "action-items" / "archive" / "stale-items-2026-10.md").exists()


def test_cli_archive_stale_reports_what_it_moved(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _daily(vault, TODAY, _doc(("## 🟡 To Do", STALE_BLOCK)))

    result = CliRunner().invoke(
        app, ["archive-stale", "--date", TODAY.isoformat(), "--data-dir", str(vault), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "would archive 1 item" in result.stdout
    assert f"[#ZQOLD] unchanged since {OLD.isoformat()}" in result.stdout

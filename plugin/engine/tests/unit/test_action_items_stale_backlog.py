"""The stale-item ledger across item-less days and the backlog.

Fixtures are anonymized per CLAUDE.md (Alex / Priya / Sam, PROJ-1234,
example-org) and every `[#TAG]` here is invented.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from scout.action_items import stale
from scout.action_items.materialize import materialize
from scout.action_items.stale import STALE_AFTER_DAYS, ActivityLedger, archive_stale

TODAY = _dt.date(2026, 10, 4)
OLD = TODAY - _dt.timedelta(days=STALE_AFTER_DAYS)
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


def _backlog(vault: Path, lines: list[str]) -> Path:
    path = vault / "action-items" / "backlog.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _day(n: int) -> _dt.date:
    return OLD + _dt.timedelta(days=n)


def _seed_ledger(vault: Path, *, before: _dt.date) -> None:
    """A cache built earlier, so the next catch-up is incremental, not a rebuild."""
    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=before)
    ledger.save()


def test_an_item_less_day_does_not_restart_every_clock(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    # A truncated rewrite: the day's file kept only its run notes.
    _daily(vault, _day(1), _doc(("## 🪵 Run notes", ["- 08:00 briefing"])), archived=True)
    _daily(vault, _day(2), _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)
    assert ledger.last_changed("ZQOLD") == OLD
    assert ledger.seen_through == _day(2)


def test_a_tag_gone_from_both_files_is_still_forgotten(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK + FRESH_BLOCK)), archived=True)
    _daily(vault, _day(1), _doc(("## 🟡 To Do", FRESH_BLOCK)), archived=True)
    _daily(vault, _day(2), _doc(("## 🟡 To Do", STALE_BLOCK + FRESH_BLOCK)), archived=True)
    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)
    assert ledger.last_changed("ZQOLD") == _day(2)


def test_moving_to_the_backlog_keeps_the_clock(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK + FRESH_BLOCK)), archived=True)
    _seed_ledger(vault, before=_day(1))
    _daily(vault, _day(1), _doc(("## 🟡 To Do", FRESH_BLOCK)), archived=True)
    backlog = _backlog(vault, ["# Backlog", "", "## 🟡 To Do (soon)", "", *STALE_BLOCK])
    ledger = ActivityLedger.load(vault)
    ledger.catch_up(vault, before=TODAY)
    assert ledger.last_changed("ZQOLD") == OLD
    ledger.observe_backlog(backlog.read_text().splitlines(), on=TODAY)
    assert ledger.last_changed("ZQOLD") == OLD


def test_a_backlog_item_never_seen_starts_its_clock_when_first_observed(tmp_path: Path) -> None:
    ledger = ActivityLedger.load(_vault(tmp_path))
    ledger.observe_backlog(STALE_BLOCK, on=TODAY)
    assert ledger.last_changed("ZQOLD") == TODAY
    ledger.observe_backlog(STALE_BLOCK, on=TODAY + _dt.timedelta(days=5))
    assert ledger.last_changed("ZQOLD") == TODAY
    edited = [STALE_BLOCK[0] + " Priya replied.", *STALE_BLOCK[1:]]
    ledger.observe_backlog(edited, on=TODAY + _dt.timedelta(days=6))
    assert ledger.last_changed("ZQOLD") == TODAY + _dt.timedelta(days=6)


def test_observe_backlog_leaves_tags_open_in_the_daily_file_alone(tmp_path: Path) -> None:
    ledger = ActivityLedger.load(_vault(tmp_path))
    ledger.observe(STALE_BLOCK, on=OLD)
    ledger.observe_backlog([STALE_BLOCK[0] + " (a stale copy)"], on=TODAY, skip=frozenset({"ZQOLD"}))
    assert ledger.last_changed("ZQOLD") == OLD


def test_a_recorded_rewrite_keeps_a_backlog_item_s_clock(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ledger = ActivityLedger.load(vault)
    ledger.observe(STALE_BLOCK, on=OLD)
    trimmed = STALE_BLOCK[:1] + STALE_BLOCK[2:]
    before, after = stale.fingerprints(STALE_BLOCK)["ZQOLD"], stale.fingerprints(trimmed)["ZQOLD"]
    stale.rewrites_path(vault).parent.mkdir(parents=True, exist_ok=True)
    stale.rewrites_path(vault).write_text(stale.Rewrite("ZQOLD", RECENT, before, after).to_json() + "\n")
    ledger.rewrites = stale.load_rewrites(vault)
    ledger.observe_backlog(trimmed, on=TODAY)
    assert ledger.last_changed("ZQOLD") == OLD
    # Only that exact pair: any other edit is a touch.
    ledger.observe_backlog([*trimmed, "  - alex: still on it"], on=TODAY)
    assert ledger.last_changed("ZQOLD") == TODAY


def test_archive_stale_archives_from_the_backlog_too(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _seed_ledger(vault, before=_day(1))
    daily = _daily(vault, TODAY, _doc(("## 🟡 To Do", FRESH_BLOCK)))
    backlog = _backlog(vault, ["# Backlog", "", "## 🟡 To Do (soon)", "", *STALE_BLOCK])

    assert [i.tag for i in archive_stale(data_dir=vault, date=TODAY, dry_run=True)] == ["ZQOLD"]
    assert "ZQOLD" in backlog.read_text()

    archived = archive_stale(data_dir=vault, date=TODAY)
    assert [i.tag for i in archived] == ["ZQOLD"]
    assert "ZQOLD" not in backlog.read_text()
    assert "## 🟡 To Do (soon)" in backlog.read_text()
    assert "ZQOLD" in stale.archive_path(vault, TODAY).read_text()
    assert "ZQNEW" in daily.read_text()


def test_a_backlog_copy_of_a_daily_item_is_not_archived(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _daily(vault, TODAY, _doc(("## 🟡 To Do", [STALE_BLOCK[0] + " Sam replied."])))
    _backlog(vault, ["## 🟡 To Do (soon)", *STALE_BLOCK])
    assert archive_stale(data_dir=vault, date=TODAY, dry_run=True) == []


def test_archive_stale_without_a_backlog_is_unchanged(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)), archived=True)
    _daily(vault, TODAY, _doc(("## 🟡 To Do", STALE_BLOCK + FRESH_BLOCK)))
    assert [i.tag for i in archive_stale(data_dir=vault, date=TODAY)] == ["ZQOLD"]
    assert not stale.backlog_path(vault).exists()


def test_materialize_archives_stale_backlog_items(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK)))
    _seed_ledger(vault, before=_day(1))
    _daily(vault, TODAY - _dt.timedelta(days=1), _doc(("## 🟡 To Do", FRESH_BLOCK)))
    backlog = _backlog(vault, ["# Backlog", "", "## 🟡 To Do (soon)", "", *STALE_BLOCK])
    created = materialize(data_dir=vault, date=TODAY)
    assert created is not None
    assert "ZQOLD" not in backlog.read_text()
    assert "1 item untouched" in created.read_text()


def test_a_rebuild_restarts_backlog_only_clocks(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _daily(vault, OLD, _doc(("## 🟡 To Do", STALE_BLOCK + FRESH_BLOCK)), archived=True)
    _daily(vault, _day(1), _doc(("## 🟡 To Do", FRESH_BLOCK)), archived=True)
    _backlog(vault, ["## 🟡 To Do (soon)", *STALE_BLOCK])
    ledger = ActivityLedger.load(vault)  # no cache: a full rebuild
    ledger.catch_up(vault, before=TODAY)
    assert ledger.last_changed("ZQOLD") == TODAY
    assert ledger.last_changed("ZQNEW") == OLD
    # So a lost cache can't archive the backlog the same morning.
    _daily(vault, TODAY, _doc(("## 🟡 To Do", FRESH_BLOCK)))
    assert [i.tag for i in archive_stale(data_dir=vault, date=TODAY, dry_run=True)] == ["ZQNEW"]


def test_the_backlog_archives_at_most_the_daily_cap_oldest_first(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    cap = stale.BACKLOG_ARCHIVE_DAILY_CAP
    rows = [[f"- [ ] [#Z{i:03d}] **Old task {i}** — filed long ago."] for i in range(cap + 3)]
    _backlog(vault, ["## 🟡 To Do (soon)", *(ln for r in rows for ln in r)])
    ledger = ActivityLedger.load(vault)
    # The last three were first seen a day later, so they are the youngest.
    ledger.observe_backlog([ln for r in rows[:cap] for ln in r], on=OLD - _dt.timedelta(days=1))
    ledger.observe_backlog([ln for r in rows for ln in r], on=OLD)
    split = stale.split_backlog(vault, ledger, today=TODAY, skip=frozenset())
    assert split is not None
    assert [i.tag for i in split[1].archived] == [f"Z{i:03d}" for i in range(cap)]


def test_an_unreadable_backlog_raises_and_a_missing_one_is_none(tmp_path: Path) -> None:
    import pytest

    vault = _vault(tmp_path)
    assert stale.read_backlog(vault) is None
    stale.backlog_path(vault).write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(UnicodeDecodeError):
        stale.read_backlog(vault)


def test_a_day_with_only_completed_rows_is_skipped(tmp_path: Path) -> None:
    ledger = ActivityLedger.load(_vault(tmp_path))
    ledger.observe(STALE_BLOCK, on=OLD)
    ledger.observe(["## ✅ Recently Completed", "- [x] [#ZQDNE] **Done**"], on=_day(1))
    assert ledger.last_changed("ZQOLD") == OLD
    assert ledger.seen_through == _day(1)

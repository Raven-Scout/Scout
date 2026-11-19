"""A 🔴 item left untouched for 3 days is recorded once as a ``red-carried`` row (noise)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

import pytest

from scout.action_items import materialize as mat
from scout.changes import carried
from scout.changes.carried import RED_AFTER_DAYS, record_red_carried
from scout.changes.store import read_signals

NOW = _dt.datetime(2026, 7, 6, 9, 0, tzinfo=_dt.UTC)
BODY = "## 🔴 Urgent\n- [ ] [#AAAA] **call the bank about the wire**\n"


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "action-items").mkdir()
    return tmp_path


def _daily(vault: Path, day: str, body: str) -> list[str]:
    text = f"# Action Items — {day}\n\n{body}"
    (vault / "action-items" / f"action-items-{day}.md").write_text(text, encoding="utf-8")
    return text.splitlines()


def _days(vault: Path, first: int, last: int, body: str = BODY) -> list[str]:
    lines: list[str] = []
    for d in range(first, last + 1):
        lines = _daily(vault, f"2026-07-{d:02d}", body)
    return lines


def _rows(vault: Path):
    rows, _ = read_signals(vault, {})
    return [r for r in rows if r.kind == "red-carried"]


def _record(vault: Path, lines: list[str], today: _dt.date):
    return record_red_carried(vault, lines, today=today, now=NOW, env={})


def test_a_red_item_untouched_3_days_is_recorded_once(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    lines = _days(vault, 3, 5)  # first seen 07-03, unchanged
    today = _dt.date(2026, 7, 6)

    assert RED_AFTER_DAYS == 3
    assert _record(vault, lines, today) == 1
    assert _record(vault, lines, today) == 0

    (row,) = _rows(vault)
    assert row.surface == "action-items"
    assert row.ts == NOW
    expected = hashlib.sha256(b"AAAA").hexdigest()[:16] + "@2026-07-03"
    assert row.ref == expected


def test_a_red_item_untouched_2_days_is_not(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    lines = _days(vault, 4, 5)
    assert _record(vault, lines, _dt.date(2026, 7, 6)) == 0
    assert _rows(vault) == []


def test_yellow_done_and_untagged_items_are_skipped(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    body = (
        "## 🟡 To Do\n"
        "- [ ] [#YYYY] **a yellow one**\n"
        "## 🔴 Urgent\n"
        "- [ ] **a red one without a tag**\n"
        "- [x] [#DONE] **a finished red one** 🔴\n"
        "## 🔴 Done\n"
        "- [x] [#DDDD] **section-red but done**\n"
    )
    lines = _days(vault, 1, 5, body)
    assert _record(vault, lines, _dt.date(2026, 7, 6)) == 0
    assert _rows(vault) == []


def test_touched_then_stale_again_counts_again(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    v1 = BODY
    v2 = BODY + "  - waiting on the bank to answer\n"
    for d in (1, 2, 3):
        lines_v1 = _daily(vault, f"2026-07-{d:02d}", v1)
    assert _record(vault, lines_v1, _dt.date(2026, 7, 4)) == 1  # since 07-01

    for d in (4, 5, 6):
        lines_v2 = _daily(vault, f"2026-07-{d:02d}", v2)
    assert _record(vault, lines_v2, _dt.date(2026, 7, 7)) == 1  # v2 first seen 07-04, 3 days by 07-07
    refs = sorted(r.ref for r in _rows(vault))
    assert len(refs) == 2
    assert refs[0].endswith("@2026-07-01") and refs[1].endswith("@2026-07-04")


def test_ref_holds_no_title_text(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    lines = _days(vault, 1, 5)
    assert _record(vault, lines, _dt.date(2026, 7, 6)) == 1
    raw = (vault / ".scout-logs" / "engagement.jsonl").read_text(encoding="utf-8")
    for word in ("bank", "wire", "call", "AAAA"):
        assert word not in raw
    assert json.loads(raw.splitlines()[0])["kind"] == "red-carried"


def test_the_stale_tag_is_the_key_not_the_parsed_prefix(tmp_path: Path) -> None:
    """A status emoji before the tag gives the parser a prefix but stale none: stale's rule decides."""
    vault = _vault(tmp_path)
    lines = _days(vault, 1, 5, "## 🔴 Urgent\n- [ ] 🔄 [#EMOJ] **in flight**\n")
    assert _record(vault, lines, _dt.date(2026, 7, 6)) == 0
    assert _rows(vault) == []


def test_materialize_survives_a_recorder_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    _daily(vault, "2026-07-05", BODY)

    def boom(*a, **k):
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(carried, "record_red_carried", boom)
    created = mat.materialize(data_dir=vault, date=_dt.date(2026, 7, 6))
    assert created == vault / "action-items" / "action-items-2026-07-06.md"
    assert "[#AAAA]" in created.read_text(encoding="utf-8")


def test_materialize_records_a_carried_red_item(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    for d in (1, 2, 3, 4, 5):
        _daily(vault, f"2026-07-{d:02d}", BODY)
    mat.materialize(data_dir=vault, date=_dt.date(2026, 7, 6))
    (row,) = _rows(vault)
    assert row.ref.endswith("@2026-07-01")

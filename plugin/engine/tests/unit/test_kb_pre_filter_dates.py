"""#201: the KB pre-filter must read the dates runs actually write.

Shapes below mirror real vault date lines (anonymized): a configured zone
other than US Eastern, `CEST` / `ET` tails, `~` approximate times, `9:3x`
minutes, a leading weekday, and the `last_updated:` frontmatter key that
kb-management.md asks for and nothing used to read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import scout.hooks.kb_pre_filter as kpf
from scout.hooks.kb_pre_filter import classify, discover_kb_files, parse_date, render_output, resolve_date

PRAGUE = ZoneInfo("Europe/Prague")
NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def _at(zone: ZoneInfo, *args: int) -> datetime:
    return datetime(*args, tzinfo=zone)


# -- prose parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A zone tail outside US Eastern used to fail every format (#201 defect 2).
        ("2026-07-15 09:31 CEST", _at(BERLIN, 2026, 7, 15, 9, 31)),
        ("2026-07-15 09:31 CET", _at(BERLIN, 2026, 7, 15, 9, 31)),
        ("2026-07-15 09:31 EST", _at(NEW_YORK, 2026, 7, 15, 9, 31)),
        ("2026-07-15 ~9:30 PM ET", _at(NEW_YORK, 2026, 7, 15, 21, 30)),
        ("2026-07-15 ~10:4x AM ET", _at(NEW_YORK, 2026, 7, 15, 10, 40)),
        ("2026-07-15 09:5x CEST (`morning-briefing`)", _at(BERLIN, 2026, 7, 15, 9, 50)),
        ("2026-07-15 ~11:20 PM ET — follow-up run", _at(NEW_YORK, 2026, 7, 15, 23, 20)),
        ("July 15, 2026 ~9:30 PM ET", _at(NEW_YORK, 2026, 7, 15, 21, 30)),
        ("July 15, 2026 at ~11:05 PM ET", _at(NEW_YORK, 2026, 7, 15, 23, 5)),
        ("Aug 5, 2026 ~9:3x AM ET", _at(NEW_YORK, 2026, 8, 5, 9, 30)),
        ("Fri 2026-07-17 ~9:30 PM ET", _at(NEW_YORK, 2026, 7, 17, 21, 30)),
        ("2026-07-15T10:20:00+02:00", datetime(2026, 7, 15, 10, 20, tzinfo=timezone(timedelta(hours=2)))),
        ("2026-07-15T08:20:00Z", datetime(2026, 7, 15, 8, 20, tzinfo=UTC)),
    ],
)
def test_parse_date_reads_real_run_renderings(text, expected):
    got = parse_date(text, tz=PRAGUE)
    assert got is not None, text
    assert got.timestamp() == expected.timestamp(), (text, got)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # No zone named: the configured zone applies.
        ("2026-07-15 14:05", _at(PRAGUE, 2026, 7, 15, 14, 5)),
        ("2026-07-15 14:05 morning briefing", _at(PRAGUE, 2026, 7, 15, 14, 5)),
        ("2026-07-15 09:31 XYZ", _at(PRAGUE, 2026, 7, 15, 9, 31)),
        # PM is a meridiem, never a zone: 07:30 PM is 19:30, not 07:30.
        ("April 22, 2026 07:30 PM", _at(PRAGUE, 2026, 4, 22, 19, 30)),
        ("2026-04-22 07:30 PM", _at(PRAGUE, 2026, 4, 22, 19, 30)),
        ("2026-04-22 12:10 AM", _at(PRAGUE, 2026, 4, 22, 0, 10)),
        ("Sept 3, 2026", _at(PRAGUE, 2026, 9, 3)),
    ],
)
def test_parse_date_uses_the_configured_zone_when_none_is_named(text, expected):
    got = parse_date(text, tz=PRAGUE)
    assert got is not None, text
    assert got.timestamp() == expected.timestamp(), (text, got)


def test_parse_date_drops_an_impossible_time_but_keeps_the_date():
    got = parse_date("2026-07-15 27:90", tz=PRAGUE)
    assert got == _at(PRAGUE, 2026, 7, 15)


@pytest.mark.parametrize(
    "text", ["", "yesterday", "after the review", "Sometime 99, 2026", "2026-13-45", "2026-13-45T10:00:00+02:00"]
)
def test_parse_date_returns_none_without_a_date(text):
    assert parse_date(text, tz=PRAGUE) is None


# -- frontmatter property ---------------------------------------------------


def test_resolve_date_reads_the_last_updated_property():
    lines = ["---", "title: Example", "last_updated: 2026-08-12T10:20:00+02:00", "---", "# Example"]
    dt, source, raw = resolve_date(lines, PRAGUE)
    assert source == "property"
    assert dt == datetime(2026, 8, 12, 10, 20, tzinfo=timezone(timedelta(hours=2)))
    assert raw == "2026-08-12T10:20:00+02:00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-12", _at(PRAGUE, 2026, 8, 12)),
        ('"2026-08-12 10:20"', _at(PRAGUE, 2026, 8, 12, 10, 20)),
        ("2026-08-12  # Fri ~10 AM ET morning briefing", _at(PRAGUE, 2026, 8, 12)),
        ("2026-08-12 ~9:30 PM ET", _at(NEW_YORK, 2026, 8, 12, 21, 30)),
    ],
)
def test_resolve_date_property_value_forms(value, expected):
    dt, source, _ = resolve_date(["---", f"last_updated: {value}", "---"], PRAGUE)
    assert source == "property"
    assert dt is not None and dt.timestamp() == expected.timestamp()


def test_resolve_date_falls_back_to_last_verified_property():
    dt, source, _ = resolve_date(["---", "last_verified: 2026-08-12", "---"], PRAGUE)
    assert source == "property"
    assert dt == _at(PRAGUE, 2026, 8, 12)


def test_resolve_date_prefers_the_property_over_the_prose_line():
    lines = ["---", "last_updated: 2026-08-12", "---", "**Last updated:** 2026-01-01"]
    dt, source, _ = resolve_date(lines, PRAGUE)
    assert (source, dt) == ("property", _at(PRAGUE, 2026, 8, 12))


def test_resolve_date_falls_back_to_prose_when_the_property_is_unreadable():
    lines = ["---", "last_updated: soon", "---", "**Last updated:** 2026-07-15 09:31 CEST"]
    dt, source, raw = resolve_date(lines, PRAGUE)
    assert source == "prose"
    assert dt == _at(BERLIN, 2026, 7, 15, 9, 31)
    assert raw == "2026-07-15 09:31 CEST"


def test_resolve_date_reads_unterminated_frontmatter_to_the_end_of_the_head():
    dt, source, _ = resolve_date(["---", "title: Example", "last_updated: 2026-08-12"], PRAGUE)
    assert (source, dt) == ("property", _at(PRAGUE, 2026, 8, 12))


def test_resolve_date_ignores_a_last_updated_key_outside_frontmatter():
    dt, source, _ = resolve_date(["# Notes", "last_updated: 2026-08-12"], PRAGUE)
    assert (dt, source) == (None, "none")


def test_classify_reads_a_property_below_line_25_of_long_frontmatter(tmp_path):
    """#230: long frontmatter pushed `last_updated:` past the 25-line head window."""
    kb = tmp_path / "knowledge-base" / "projects"
    kb.mkdir(parents=True)
    f = kb / "long.md"
    fields = [f"field_{i}: value" for i in range(40)]
    f.write_text("\n".join(["---", *fields, "last_updated: 2026-08-12T10:00:00+02:00", "---", "# Long"]) + "\n")
    label, details = classify(f, _at(PRAGUE, 2026, 8, 13, 10, 0), tmp_path, tz=PRAGUE)
    assert label == "FRESH"
    assert details["source"] == "property"
    assert details["age_hours"] == 24


# -- discovery --------------------------------------------------------------


@pytest.mark.parametrize("folder", sorted(kpf.SKIP_KB_SUBDIRS))
def test_discover_skips_per_item_record_folders(tmp_path, folder):
    kb = tmp_path / "knowledge-base"
    (kb / folder).mkdir(parents=True)
    (kb / folder / "2026-08-12-item.md").write_text("---\ndate: 2026-08-12\n---\n")
    (kb / "projects").mkdir()
    (kb / "projects" / "kept.md").write_text("# Kept\n")
    names = [p.name for p in discover_kb_files(tmp_path)]
    assert names == ["kept.md"]


def test_discover_keeps_a_doc_whose_name_merely_starts_like_a_record_folder(tmp_path):
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "research-queue-notes.md").write_text("# Notes\n")
    (kb / "projects").mkdir()
    (kb / "projects" / "session-log-design.md").write_text("# Design\n")
    names = sorted(p.name for p in discover_kb_files(tmp_path))
    assert names == ["research-queue-notes.md", "session-log-design.md"]


# -- reporting --------------------------------------------------------------


def test_render_output_warns_when_no_file_has_a_readable_date():
    out = render_output([], [{"rel": "knowledge-base/a.md"}], [], session_type="dreaming", now_et="now")
    assert "⚠" in out
    assert "staleness check did not run" in out
    # The summary line stays last-but-one and parseable.
    assert "Stale: 0 | No date: 1 | Fresh: 0" in out


def test_render_output_reports_where_dates_came_from():
    fresh = [
        {"rel": "knowledge-base/a.md", "age_hours": 1, "source": "property"},
        {"rel": "knowledge-base/b.md", "age_hours": 2, "source": "prose"},
    ]
    out = render_output([], [], fresh, session_type="dreaming", now_et="now")
    assert "Dates read from: 1 last_updated property, 1 prose line" in out
    assert "⚠" not in out


def test_render_output_without_files_has_no_warning():
    out = render_output([], [], [], session_type="dreaming", now_et="now")
    assert "⚠" not in out


# -- regression: a non-US-Eastern vault classifies its files ----------------


def test_run_on_a_non_eastern_vault_classifies_real_run_renderings(tmp_path, monkeypatch):
    """The gap that let #201 ship: a vault where nothing parsed passed the suite."""
    kb = tmp_path / "knowledge-base"
    (kb / "projects").mkdir(parents=True)
    (kb / "projects" / "a.md").write_text("# A\n\n**Last updated:** 2026-08-12 09:31 CEST (`morning-briefing`)\n")
    (kb / "projects" / "b.md").write_text("---\npriority: 🔴\nlast_updated: 2026-08-01T10:00:00+02:00\n---\n# B\n")
    (kb / "projects" / "c.md").write_text("# C\n\n**Last updated:** Aug 11, 2026 ~9:3x PM ET\n")
    (kb / "research-queue").mkdir()
    (kb / "research-queue" / "item.md").write_text("---\ndate: 2026-08-01\n---\n")
    monkeypatch.setattr(kpf.paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(kpf, "_boundary_zone", lambda: PRAGUE)

    event = kpf.run(session_type="dreaming", now=_at(PRAGUE, 2026, 8, 12, 12, 0))

    assert event is not None
    assert (event.payload["stale"], event.payload["no_date"], event.payload["fresh"]) == (1, 0, 2)
    assert event.payload["dated_by"] == {"property": 1, "prose": 2}
    out = (tmp_path / ".scout-cache" / "kb-filter.md").read_text()
    assert "Stale: 1 | No date: 0 | Fresh: 2" in out
    assert "knowledge-base/projects/b.md" in out  # 🔴 budget 72h, 11 days old
    assert "research-queue" not in out


def test_parse_date_default_zone_is_the_configured_one(monkeypatch):
    monkeypatch.setattr(kpf, "_boundary_zone", lambda: PRAGUE)
    assert parse_date("2026-07-15 14:05") == _at(PRAGUE, 2026, 7, 15, 14, 5)


def test_classify_reports_an_unreadable_date_as_no_date(tmp_path):
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    f = kb / "x.md"
    f.write_text("**Last updated:** after the review\n")
    assert classify(f, _at(PRAGUE, 2026, 8, 12), tmp_path, tz=PRAGUE)[0] == "NO_DATE"

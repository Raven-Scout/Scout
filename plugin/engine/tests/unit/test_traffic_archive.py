"""scripts/metrics/archive_traffic.py keeps history across GitHub's 14-day traffic window."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location("archive_traffic", REPO / "scripts" / "metrics" / "archive_traffic.py")
assert _spec and _spec.loader
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)


def test_fresh_rows_overwrite_the_partial_day_and_keep_older_history():
    stored = [
        {"date": "2026-09-01", "count": "5", "uniques": "4"},  # fell out of GitHub's window long ago
        {"date": "2026-10-12", "count": "100", "uniques": "40"},  # partial when it was stored
    ]
    fresh = [
        {"date": "2026-10-12", "count": "2261", "uniques": "365"},
        {"date": "2026-10-13", "count": "7", "uniques": "6"},
    ]
    merged = at.upsert_dated(stored, fresh)
    assert [r["date"] for r in merged] == ["2026-09-01", "2026-10-12", "2026-10-13"]
    assert merged[1]["count"] == "2261"


def test_rerunning_a_snapshot_day_replaces_it_instead_of_duplicating():
    stored = [
        {"snapshot_date": "2026-10-12", "referrer": "github.com", "count": "41", "uniques": "18"},
        {"snapshot_date": "2026-10-13", "referrer": "github.com", "count": "1", "uniques": "1"},
    ]
    fresh = [{"snapshot_date": "2026-10-13", "referrer": "linkedin.com", "count": "21", "uniques": "15"}]
    merged = at.replace_snapshot(stored, fresh, "2026-10-13")
    assert [(r["snapshot_date"], r["referrer"]) for r in merged] == [
        ("2026-10-12", "github.com"),
        ("2026-10-13", "linkedin.com"),
    ]


def test_csv_round_trips_with_a_stable_column_order():
    rows = [{"date": "2026-10-13", "uniques": "6", "count": "7"}]
    text = at.render_csv(rows, at.DATED["clones.csv"])
    assert text == "date,count,uniques\n2026-10-13,7,6\n"
    assert at.parse_csv(text) == [{"date": "2026-10-13", "count": "7", "uniques": "6"}]
    assert at.parse_csv("") == []


def test_traffic_payload_becomes_one_row_per_utc_day():
    payload = {"count": 9, "uniques": 7, "clones": [{"timestamp": "2026-10-12T00:00:00Z", "count": 9, "uniques": 7}]}
    assert at.traffic_rows(payload, "clones") == [{"date": "2026-10-12", "count": "9", "uniques": "7"}]


def test_ci_counts_cover_the_traffic_window_through_today():
    rows = [{"date": "2026-10-11"}, {"date": "2026-10-12"}]
    assert at.window_dates(rows, "2026-10-13") == ["2026-10-11", "2026-10-12", "2026-10-13"]


def test_without_traffic_the_ci_window_is_githubs_14_days():
    dates = at.window_dates([], "2026-10-13")
    assert len(dates) == 14
    assert (dates[0], dates[-1]) == ("2026-09-30", "2026-10-13")

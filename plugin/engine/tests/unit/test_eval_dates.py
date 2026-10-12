"""Date macros for the eval fixture vault (spec 2026-10-12 §5.2)."""

from __future__ import annotations

from datetime import date

from tests.eval.harness.dates import UNRENDERED, render_dates

MON = date(2026, 10, 12)
WED = date(2026, 10, 14)
FRI = date(2026, 10, 16)
SAT = date(2026, 10, 17)


def test_calendar_days_back_and_forward() -> None:
    assert render_dates("{{d-1}} {{d+5}} {{d+0}}", WED) == "2026-10-13 2026-10-19 2026-10-14"


def test_today_and_month() -> None:
    assert render_dates("{{today}} / {{month}}", WED) == "2026-10-14 / 2026-10"


def test_working_days_skip_the_weekend() -> None:
    assert render_dates("{{wd-2}}", MON) == "2026-10-08"
    assert render_dates("{{wd+1}}", FRI) == "2026-10-19"
    assert render_dates("{{wd-1}}", WED) == "2026-10-13"


def test_working_days_from_a_weekend_count_from_friday() -> None:
    assert render_dates("{{wd-1}}", SAT) == "2026-10-15"
    assert render_dates("{{wd+0}}", SAT) == "2026-10-16"


def test_unknown_macro_is_left_for_the_unrendered_check() -> None:
    out = render_dates("keep {{bogus}} and {{USER_NAME}}", WED)
    assert out == "keep {{bogus}} and {{USER_NAME}}"
    assert UNRENDERED.findall(out) == ["{{bogus}}", "{{USER_NAME}}"]

"""Output checks on the post-run daily file (spec 2026-10-12 §6.1): placement (#371) and no_drop."""

from __future__ import annotations

import pytest

from scout.action_items.parser import parse_lines
from tests.eval.harness.checks import CheckResult
from tests.eval.harness.checks.no_drop import no_drop
from tests.eval.harness.checks.placement import placement

YESTERDAY = """# Action Items
## 🔴 Urgent
- [ ] [#RV] **Review the rate limits** — requested from Alex by name
## 🟡 To Do
- [ ] [#QT] **Answer the quota question** — Priya asked
- [ ] [#OLD] **Tidy the runbook** — no news
## 🟢 Watching
- [ ] [#WS] **Billing dry run** — waiting on Sam since 2026-10-12
- [ ] [#TK] **Launch checklist ticket** — waiting on Priya since 2026-10-10
## ✅ Recently Completed
- [x] [#GONE] **Old thing** — closed 2026-10-09
"""

GOOD = """# Action Items
## 🎯 Today's Focus
- [ ] [#QT] **Answer the quota question** — Priya asked
## 🔴 Urgent
- [ ] [#RV] **Review the rate limits** — requested from Alex by name
## 🟡 To Do
- [ ] [#QT] **Answer the quota question** — Priya asked
- [ ] [#OLD] **Tidy the runbook** — no news
- [ ] [#NEW] **Merge the catalog PR** — approved and clean
  - Refs: example-org/widgets#20
## 🟢 Watching
- [ ] [#WS] **Billing dry run** — waiting on Sam since 2026-10-12
## ✅ Recently Completed
- [x] [#TK] **Launch checklist ticket** — closed overnight
"""

BAD = """# Action Items
## 🔴 Urgent
- [ ] [#RV] **Review the rate limits** — requested from Alex by name
- [ ] [#WS] **Billing dry run** — waiting on Sam since 2026-10-12
## 🟡 To Do
- [ ] [#NEW] **Merge the catalog PR** — approved and clean
  - Refs: example-org/widgets#20
## 🟢 Watching
- [ ] [#QT] **Answer the quota question** — Priya asked
"""

EXPECTED = {
    "RV": {"🔴", "🟡"},
    "QT": {"🔴", "🟡"},
    "WS": {"🟢"},
    "TK": {"✅"},
    "ref:example-org/widgets#20": {"🔴", "🟡"},
}


def _items(text: str):
    return parse_lines(text.splitlines())


@pytest.mark.parametrize("focus", ["🎯", "💡"])
def test_placement_passes_on_the_good_file_and_ignores_the_focus_list(focus: str) -> None:
    """Either Today's Focus heading is a list, never the item's placement."""
    r = placement(_items(GOOD.replace("## 🎯", f"## {focus}")), EXPECTED)
    assert r == CheckResult("placement", True, ())


def test_placement_names_each_misplaced_and_missing_item() -> None:
    r = placement(_items(BAD), EXPECTED)
    assert r.passed is False
    assert r.details == (
        "QT: in 🟢 Watching, expected one of 🔴 🟡",
        "TK: missing",
        "WS: in 🔴 Urgent, expected one of 🟢",
    )


def test_placement_matches_new_items_by_ref() -> None:
    r = placement(_items(BAD), {"ref:example-org/widgets#20": {"🟢"}})
    assert r.details == ("ref:example-org/widgets#20 (NEW): in 🟡 To Do, expected one of 🟢",)


def test_no_drop_passes_when_every_open_item_survives_or_closes_properly() -> None:
    r = no_drop(_items(YESTERDAY), _items(GOOD), archived_tags=set(), expected_closures={"TK"})
    assert r == CheckResult("no_drop", True, ())


def test_no_drop_names_the_silently_dropped_items() -> None:
    r = no_drop(_items(YESTERDAY), _items(BAD), archived_tags=set(), expected_closures=set())
    assert r.passed is False
    assert r.details == ("OLD: open yesterday, gone today", "TK: open yesterday, gone today")


def test_no_drop_accepts_an_archived_or_expected_closure() -> None:
    r = no_drop(_items(YESTERDAY), _items(BAD), archived_tags={"OLD"}, expected_closures={"TK"})
    assert r.passed is True


def test_missing_output_is_not_a_pass_or_a_fail() -> None:
    assert placement(None, EXPECTED) == CheckResult("placement", None, ("missing_output",))
    assert no_drop(_items(YESTERDAY), None, archived_tags=set(), expected_closures=set()) == CheckResult(
        "no_drop", None, ("missing_output",)
    )

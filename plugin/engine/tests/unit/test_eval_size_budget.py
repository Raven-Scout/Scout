"""The eval checks for *The Backlog and the Budget* (phases/core/action-items.md): ``size_budget``,
``backlog`` and ``no_drop``'s backlog carry. Tier 0: no model.

Fixtures are anonymized per CLAUDE.md; every `[#TAG]` here is invented.
"""

from __future__ import annotations

from scout.action_items.budget import Budget
from scout.action_items.parser import parse_lines
from tests.eval.harness.checks.daily import backlog, size_budget
from tests.eval.harness.checks.no_drop import no_drop
from tests.eval.harness.scenario import load_scenario

ONE = "- [ ] [#ONEP] **Reply to Alex** — due 2026-11-25\n  - the blocker\n"
TWO = "- [ ] [#TWOP] **Reply to Sam** — due 2026-11-25\n  - first\n  - second\n  - Refs: [[PROJ-1234]]\n"


def _doc(body: str) -> str:
    return f"# Action Items — test\n\n## 🟡 To Do\n\n{body}"


def test_size_budget_passes_a_tidy_file_and_needs_output() -> None:
    assert size_budget(_doc(ONE), None).passed is True
    assert size_budget(None, None).passed is None


def test_size_budget_fails_new_or_changed_extra_prose_only() -> None:
    result = size_budget(_doc(ONE + TWO), _doc(ONE))
    assert result.passed is False and result.details == (
        "TWOP: 2 prose sub-bullets (limit 1); context goes in its note",
    )
    # Carried verbatim from yesterday: fit's to trim, not the run's.
    assert size_budget(_doc(TWO), _doc(TWO)).passed is True


def test_size_budget_fails_past_the_hard_limit() -> None:
    result = size_budget(_doc(ONE), _doc(ONE), Budget(hard_open=0, target_open=0))
    assert result.passed is False and "hard limit" in result.details[0]


def test_backlog_check() -> None:
    log = "## 🟡 To Do (soon)\n\n- [ ] [#BKLG] **Write up the metrics** — no due date\n"
    today = parse_lines(_doc(ONE).splitlines())
    assert backlog(["BKLG"], today, log).passed is True
    copied = parse_lines(_doc(ONE + "- [ ] [#BKLG] **Write up the metrics**\n").splitlines())
    assert backlog(["BKLG"], copied, log).details == ("BKLG: copied into the daily file with no news",)
    assert backlog(["BKLG"], today, "").details == ("BKLG: no longer open in the backlog",)
    assert backlog(["BKLG"], None, log).passed is None


def test_no_drop_counts_the_backlog_as_carried() -> None:
    yesterday = parse_lines(_doc(ONE + TWO).splitlines())
    today = parse_lines(_doc(ONE).splitlines())
    assert no_drop(yesterday, today, archived_tags=set(), expected_closures=set()).passed is False
    assert no_drop(yesterday, today, archived_tags=set(), expected_closures=set(), backlog_tags={"TWOP"}).passed


def test_busy_weekday_seeds_the_backlog() -> None:
    scenario = load_scenario("busy-weekday")
    assert scenario.expected_backlog == ("BKLG",)
    assert {"size_budget", "backlog"} <= set(scenario.checks)
    assert "[#BKLG]" in (scenario.path / "vault" / "action-items" / "backlog.md").read_text(encoding="utf-8")

"""Slice 5 checks over the daily file and the run's outputs (spec 2026-10-12 §6.1)."""

from __future__ import annotations

import pytest

from scout.action_items.parser import parse_lines
from tests.eval.harness.checks.daily import body_budget, context_note, no_collapse, waiting_format
from tests.eval.harness.checks.run import dropoff_caught, no_false_outage, outage_reported

YESTERDAY = """## 🟡 To Do
- [ ] [#OLD] **Tidy the runbook** — no news
"""

TODAY = """## 🟡 To Do
- [ ] [#OLD] **Tidy the runbook** — no news
- [ ] [#NEW] **Answer the quota question** — Priya needs a yes or no today.
- [ ] [#BARE] **Reply to Sam** — about the billing export.
## 🟢 Watching
- [ ] [#BILL] **Billing dry run results** — waiting on Sam since 2026-10-09
- [ ] [#VAGUE] **Docs restart date** — Priya will get back to us
"""

NOTE = """---
tag: NEW
title: Answer the quota question
created: 2026-10-12
updated: 2026-10-12
---

Priya asked in a DM.
"""


def _items(text: str):
    return parse_lines(text.splitlines())


def test_context_note_requires_a_note_with_frontmatter_for_each_new_open_tag() -> None:
    r = context_note(
        _items(TODAY), _items(YESTERDAY), {"NEW": NOTE, "BARE": None, "BILL": NOTE, "VAGUE": "no frontmatter"}
    )
    assert r.passed is False
    assert r.details == ("BARE: no context note", "VAGUE: note missing frontmatter tag, title, created, updated")
    ok = context_note(_items(TODAY), _items(YESTERDAY), dict.fromkeys(("NEW", "BARE", "BILL", "VAGUE"), NOTE))
    assert ok.passed is True


def test_waiting_format_checks_the_expected_waiting_items() -> None:
    r = waiting_format(_items(TODAY), ["BILL", "VAGUE"])
    assert r.passed is False
    assert r.details == ("VAGUE: body doesn't say 'waiting on <who> since <date>'",)
    assert waiting_format(_items(TODAY), ["BILL"]).passed is True
    assert waiting_format(_items(TODAY), ["GONE"]).details == ("GONE: missing",)


def test_no_collapse_flags_rows_that_summarise_several_items() -> None:
    collapsed = TODAY + "- [ ] [#MANY] **Review the open PRs** — #12, #20 and 3 more\n"
    assert no_collapse(_items(TODAY)).passed is True
    r = no_collapse(_items(collapsed))
    assert (r.passed, r.details) == (False, ("MANY: summarises several items",))
    assert no_collapse(_items(TODAY + "- [ ] [#ETC] **Tidy things** — runbook, alerts, etc.\n")).passed is False


def test_body_budget_fails_over_40_words_and_notes_over_25() -> None:
    long = " ".join(["word"] * 41)
    medium = " ".join(["word"] * 30)
    r = body_budget(_items(f"## 🟡 To Do\n- [ ] [#LONG] **Long** — {long}\n- [ ] [#MED] **Medium** — {medium}\n"))
    assert r.passed is False
    assert r.details == ("LONG: body is 41 words (limit 40)", "MED: body is 30 words (aim for 25)")
    assert body_budget(_items(f"## 🟡 To Do\n- [ ] [#MED] **Medium** — {medium}\n")).passed is True


def test_missing_output_is_not_a_pass_for_any_daily_check() -> None:
    assert context_note(None, [], {}).passed is None
    assert waiting_format(None, ["X"]).passed is None
    assert no_collapse(None).passed is None
    assert body_budget(None).passed is None


WRAP = (
    "Morning run done. ⚠️ Linear unreachable: the login expired, reconnect it. Slack: 3 DMs triaged. Calendar: 2 events."
)
NOTES = "- 2026-10-12 morning-consolidation: Slack ✓, Calendar ✓, Linear ✗ (auth expired), Gmail ✗ unavailable"


def test_outage_reported_needs_wrap_notes_and_an_attempt() -> None:
    calls = {"linear": 1, "email": 1, "slack": 4, "calendar": 1}
    r = outage_reported(["linear", "email"], WRAP, NOTES, calls)
    assert r.passed is False
    assert r.details == ("email: not named as an outage in the wrap",)
    r2 = outage_reported(["linear"], WRAP, NOTES, {"linear": 0})
    assert r2.details == ("linear: declared down with no recorded call (attempt before declaring)",)
    assert outage_reported(["linear"], WRAP, NOTES, calls).passed is True


def test_outage_reported_without_a_wrap_fails() -> None:
    r = outage_reported(["linear"], None, NOTES, {"linear": 1})
    assert (r.passed, r.details) == (False, ("no wrap message was sent",))


def test_naming_a_working_connector_in_passing_is_not_a_false_outage() -> None:
    assert no_false_outage(["slack", "calendar"], WRAP, NOTES).passed is True
    r = no_false_outage(["slack"], WRAP + " Slack unreachable too.", NOTES)
    assert (r.passed, r.details) == (False, ("slack: reported as an outage but it answered",))


def test_dropoff_caught_needs_re_add_wrap_and_review_entry() -> None:
    today = _items("## 🟡 To Do\n- [ ] [#RUNB] **Update the runbook** — re-added [carried-via-audit]\n")
    wrap = "🚨 2 items silently dropped from yesterday — please verify"
    r = dropoff_caught(["RUNB", "INVX"], today, wrap, review_added="- dropoff: RUNB, INVX")
    assert r.passed is False
    assert r.details == ("INVX: not re-added with [carried-via-audit]",)
    r2 = dropoff_caught(["RUNB"], today, "all good", review_added="")
    assert r2.details == ("wrap doesn't say items were silently dropped", "review-queue.md gained no entry")
    assert dropoff_caught(["RUNB"], today, wrap, review_added="- dropoff: RUNB").passed is True


LONG_DIGEST = (
    "- Files to review — the gateway runbook, the billing cleanup plan, the docs refresh outline, "
    "the widgets launch checklist, the quarterly roadmap and the survey results, plus two more notes "
    "from the weekend that nobody has opened yet."
)
LONG_RUN_NOTE = (
    "- 2026-10-12 12:00 UTC (morning-consolidation) — 0 new / 0 completed / 2 restored via dropoff "
    "audit / 5 carried forward. Connectors: degraded: Linear (OAuth token expired), Gmail (503). "
    "Slack, GitHub, Calendar answered, and the standup event carried forward unchanged from "
    "yesterday's run, etc."
)
NON_TASK_LINES = "\n".join(
    [
        "## 🟡 To Do",
        "- [ ] [#NEW] **Answer the quota question** — Priya needs a yes or no today.",
        "## 📅 Meetings",
        "- Widgets launch standup — daily 09:30 UTC, attendees Alex, Priya, Sam, plus the on-call engineer etc.",
        "## 📋 Scout Digest — 2026-10-12 (09:00)",
        LONG_DIGEST,
        "## 🪵 Run notes & connector availability",
        LONG_RUN_NOTE,
        "",
    ]
)


def test_daily_checks_score_task_rows_only_not_meetings_digest_or_run_notes() -> None:
    items = _items(NON_TASK_LINES)
    assert body_budget(items).passed is True
    assert no_collapse(items).passed is True


def test_waiting_format_is_case_insensitive() -> None:
    items = _items("## 🟢 Watching\n- [ ] [#WT] **x** — Waiting on Sam since 2026-10-09\n")
    assert waiting_format(items, ["WT"]).passed is True


# (text, persona, is an outage claim) — the slack.md footer format and the real wraps
CLAIMS = [
    ("Slack ✓ · Calendar ✓ · Linear ✗ · Gmail ✗", "slack", False),
    ("Slack ✓ · Calendar ✓ · Linear ✗ · Gmail ✗", "calendar", False),
    ("Slack ✓ · Calendar ✓ · Linear ✗ · Gmail ✗", "linear", True),
    ("Slack ✓ · Calendar ✓ · Linear ✗ · Gmail ✗", "email", True),
    ("Sources checked: 3 Slack threads · 0 Linear updates (auth expired)", "slack", False),
    ("Sources checked: 3 Slack threads · 0 Linear updates (auth expired)", "linear", True),
    ("Linear unavailable (auth expired) — Slack and Calendar answered", "slack", False),
    ("Linear unavailable (auth expired) — Slack and Calendar answered", "linear", True),
    ("Linear: 3 issues authored by Alex", "linear", False),
    ("Slack countdown thread for the launch", "slack", False),
    ("Slack: skipped 2 bot messages", "slack", False),
    ("Calendar: 0 errors", "calendar", False),
    ("Linear: 0 updates · Gmail unavailable", "linear", False),
    ("Degraded connectors: Linear (OAuth token expired) and Gmail (503)", "linear", True),
    ("Degraded connectors: Linear (OAuth token expired) and Gmail (503)", "email", True),
    ("Connectors: degraded: Linear (OAuth token expired), Gmail (503 service unavailable).", "email", True),
    ("Couldn't reach Linear this run: reconnect it in Settings.", "linear", True),
    ("Slack, GitHub, Calendar answered.", "calendar", False),
]


@pytest.mark.parametrize(("text", "persona", "claimed"), CLAIMS)
def test_outage_claims_on_realistic_lines(text: str, persona: str, claimed: bool) -> None:
    from tests.eval.harness.checks.run import claims_outage

    assert claims_outage(persona, text) is claimed


def test_dropoff_wording_counts_the_act_of_surfacing_it() -> None:
    today = _items("## 🟡 To Do\n- [ ] [#RUNB] **Update the runbook** — re-added `[carried-via-audit]`\n")
    wrap = "7 carried forward (2 came back after a silent dropoff from the earlier run today; flagged for your review)"
    assert dropoff_caught(["RUNB"], today, wrap, review_added="- dropoff: RUNB").passed is True

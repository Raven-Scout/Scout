"""#372 Past-Due Tasks Step Down (phases/core/action-items.md:67-78) as a check."""

from __future__ import annotations

from scout.action_items.parser import parse_lines
from tests.eval.harness.checks.demotion import demotion

YESTERDAY = """## 🔴 Urgent
- [ ] [#OVRD] **Send the vendor survey answers** — the form was due Sep 28; no reply since.
- [ ] [#LATE] **Send the gateway capacity numbers** — due Oct 10; the next step is Alex's.
## 🟡 To Do
- [ ] [#PARK] **Renew the design tool seats** — due Sep 17; still want this?
"""

GOOD = """## 🔴 Urgent
- [ ] [#LATE] **Send the gateway capacity numbers** — due Oct 10; the next step is Alex's.
## 🟡 To Do
- [ ] [#OVRD] **Send the vendor survey answers** — due Sep 28; still want this?
## 🅿️ Parked
- [ ] [#PARK] **Renew the design tool seats** — due Sep 17; still want this?
"""

BAD = """## 🔴 Urgent
- [ ] [#OVRD] **Send the vendor survey answers** — the form was due Sep 28; now 14 days overdue.
## 🟡 To Do
- [ ] [#LATE] **Send the gateway capacity numbers** — due Oct 10; the next step is Alex's.
- [ ] [#PARK] **Renew the design tool seats** — due Sep 17; still want this? (no reply yet)
"""

NOTES_GOOD = {
    "OVRD": "---\ntag: OVRD\n---\n- 2026-10-12: stepped down, past due with no news\n",
    "PARK": "---\ntag: PARK\n---\n- 2026-09-26: stepped down, past due with no news\n",
}
EXPECTED = {"OVRD": "step_down", "LATE": "hold", "PARK": "park"}


def _items(text: str):
    return parse_lines(text.splitlines())


def test_each_branch_passes_when_the_rule_is_followed() -> None:
    r = demotion(_items(GOOD), _items(YESTERDAY), NOTES_GOOD, EXPECTED)
    assert (r.name, r.passed, r.details) == ("demotion", True, ())


def test_each_branch_names_how_it_was_broken() -> None:
    r = demotion(_items(BAD), _items(YESTERDAY), {}, EXPECTED)
    assert r.passed is False
    assert r.details == (
        "LATE (hold): moved from 🔴 to 🟡",
        "OVRD (step_down): still in 🔴",
        "OVRD (step_down): body doesn't say 'due <date>; still want this?'",
        "OVRD (step_down): context note has no dated 'stepped down, past due with no news' line",
        "PARK (park): in 🟡, expected 🅿️",
        "PARK (park): text changed",
    )


def test_a_hold_whose_body_was_rewritten_fails() -> None:
    edited = GOOD.replace("the next step is Alex's.", "the next step is Alex's (2 days overdue).")
    r = demotion(_items(edited), _items(YESTERDAY), NOTES_GOOD, {"LATE": "hold"})
    assert r.details == ("LATE (hold): text changed (age is not a touch)",)


def test_missing_items_and_missing_output() -> None:
    assert demotion(_items(GOOD), _items(YESTERDAY), {}, {"GONE": "hold"}).details == ("GONE: missing",)
    assert demotion(None, _items(YESTERDAY), {}, EXPECTED).passed is None


def test_busy_weekday_seeds_each_demotion_branch() -> None:
    from tests.eval.harness.scenario import load_scenario

    busy = load_scenario("busy-weekday")
    assert "demotion" in busy.checks
    assert busy.expected_demotion == {"OVRD": "step_down", "LATE": "hold", "PARK": "park"}
    yesterday = (busy.path / "vault" / "action-items" / "action-items-{{d-1}}.md").read_text()
    for tag in busy.expected_demotion:
        assert f"[#{tag}]" in yesterday
    note = (busy.path / "vault" / "action-items" / "context" / "PARK.md").read_text()
    assert "{{d-16}}: stepped down, past due with no news" in note

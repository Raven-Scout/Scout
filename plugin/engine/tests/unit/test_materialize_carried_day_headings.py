"""The carry-forward must not let yesterday's day labels pass as today's.

Before this, ``materialize`` re-dated exactly two lines — the H1 and the banner —
so a file could open ``# Action Items — Sunday, Oct 18, 2026`` and then, 449 lines
down, say ``## 📅 Today's Meetings (Saturday Oct 17)``. Measured over one vault:
39 of 193 daily files carried at least one such heading.

The fix **marks**, it does not re-date: the content under the heading really is
yesterday's, and re-dating the label would turn a visible staleness into an
invisible one.
"""

import datetime as _dt

import pytest

from scout.action_items.materialize import _mark_carried_day_headings

PREV = _dt.date(2026, 10, 17)  # a Saturday
MARK = "⚠️ carried from 2026-10-17, not re-verified"


@pytest.mark.parametrize(
    "heading",
    [
        "## 📅 Today's Meetings (Saturday Oct 17)",
        "## 📋 Scout Digest — Saturday Oct 17",
        "## 📋 Scout Digest — Saturday, Oct 17, 2026 (09:4x EDT)",
        "## 📅 Today — Sat Oct 17",
        "## 📅 Today's Schedule (Oct 17)",
    ],
)
def test_marks_a_heading_naming_the_day_being_carried_from(heading):
    out = _mark_carried_day_headings(heading + "\n", PREV)
    assert out.rstrip("\n").endswith(MARK)


@pytest.mark.parametrize(
    "heading",
    [
        # Not a day label at all.
        "## 🔴 Urgent / Time-Sensitive",
        # Names a different day — a lookahead section is correct as written.
        "## 📅 Monday Preview — Monday Oct 19",
        # Window boundary, not the section's own day.
        "## ✅ Recently Completed (since Oct 17 1:11 PM consolidation)",
        "## 🆕 From chrome sweep (Oct 18, 09:09 EDT — messages since Oct 17 09:01)",
        # Deeper headings are run provenance, not the file's own day labels.
        "### 🆕 From this run (Sat Oct 17, 8:0x PM `evening-consolidation`)",
    ],
)
def test_leaves_everything_else_alone(heading):
    assert _mark_carried_day_headings(heading + "\n", PREV) == heading + "\n"


def test_is_idempotent_across_a_two_day_gap():
    """A file carried twice must not accumulate two marks for the same heading."""
    once = _mark_carried_day_headings("## 📅 Today (Saturday Oct 17)\n", PREV)
    twice = _mark_carried_day_headings(once, PREV)
    assert twice == once
    assert twice.count("carried from") == 1


def test_does_not_rewrite_the_date_itself():
    """The label must survive verbatim — the mark is additive, never a re-dating."""
    out = _mark_carried_day_headings("## 📅 Today's Meetings (Saturday Oct 17)\n", PREV)
    assert "(Saturday Oct 17)" in out


def test_preserves_body_lines_and_trailing_newline_shape():
    body = "## 📅 Today's Meetings (Saturday Oct 17)\n\n_No events today._\n- [ ] [#FOO] something\n"
    out = _mark_carried_day_headings(body, PREV)
    assert out.count("\n") == body.count("\n")
    assert "- [ ] [#FOO] something\n" in out
    assert "_No events today._\n" in out

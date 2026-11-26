"""The page writer's refusals on bad input, and the edits it makes to pages shaped unlike the fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import writer

FRONT = (Path(__file__).parent.parent / "fixtures" / "lab" / "summary-drift" / "summary-drift.md").read_text("utf-8")


def test_prose_is_refused_when_empty_or_too_long() -> None:
    with pytest.raises(LabError, match="understanding is empty"):
        writer.set_understanding(FRONT, "   \n")
    with pytest.raises(LabError, match="the limit is"):
        writer.set_understanding(FRONT, "x" * (20 * writer.MAX_TEXT + 1))


def test_understanding_keeps_the_blank_lines_before_the_next_heading() -> None:
    out = writer.set_understanding(FRONT, "Fresh prose.")
    assert "## Current understanding\nFresh prose.\n\n## Open questions" in out


@pytest.mark.parametrize("target", ["", "a]b", "a|b", "a\nb"])
def test_a_bad_link_target_is_refused(target: str) -> None:
    with pytest.raises(LabError, match="bad link target"):
        writer.add_evidence(FRONT, target, "why")


def test_a_bad_question_id_is_refused() -> None:
    with pytest.raises(LabError, match="is not a question ID"):
        writer.answer_question(FRONT, "3", "note", "2026-11-22")


@pytest.mark.parametrize("value", ["two\nlines", "a\rb"])
def test_frontmatter_values_stay_on_one_line(value: str) -> None:
    with pytest.raises(LabError, match="one line"):
        writer.set_frontmatter(FRONT, last_run=value)


def test_frontmatter_values_yaml_would_misread_are_quoted() -> None:
    out = writer.set_frontmatter(FRONT, last_run="yes", next_q=9, created="2026-11-20", note="a: b")
    assert 'last_run: "yes"\n' in out and "next_q: 9\n" in out and "created: 2026-11-20\n" in out
    assert 'note: "a: b"\n' in out
    assert writer.set_frontmatter(FRONT, note="[unclosed").count('note: "[unclosed"') == 1


def test_a_page_without_frontmatter_cant_take_fields() -> None:
    with pytest.raises(LabError, match="no frontmatter block"):
        writer.set_frontmatter("# Just a page\n", status="active")


@pytest.mark.parametrize("stamp", ["2026-11-22\n01:00", "2026) x"])
def test_a_bad_comment_stamp_is_refused(stamp: str) -> None:
    with pytest.raises(LabError, match="bad comment stamp"):
        writer.add_reply(FRONT, "Q3", 2, "Noted.", stamp)


def test_a_bad_next_q_refuses_a_new_question() -> None:
    with pytest.raises(LabError, match="next_q"):
        writer.add_question(FRONT.replace("next_q: 8", "next_q: zero", 1), "Another?", "2026-11-22")


def test_visibility_private_is_added_with_or_without_a_frontmatter_block() -> None:
    assert writer.with_visibility("# T\n", "normal") == "# T\n"
    assert writer.with_visibility("# T\n", "secret") == "---\nvisibility: private\n---\n# T\n"
    assert "visibility: private\n" in writer.with_visibility(FRONT, "Private")


def test_understanding_with_no_blank_line_before_the_next_heading() -> None:
    tight = FRONT.replace(
        "[[regeneration-cost-notes]].\n\n## Open questions", "[[regeneration-cost-notes]].\n## Open questions"
    )
    assert tight != FRONT
    assert "## Current understanding\nFresh prose.\n## Open questions" in writer.set_understanding(
        tight, "Fresh prose."
    )


def test_evidence_goes_under_an_empty_section_heading() -> None:
    start = FRONT.index("## Evidence\n") + len("## Evidence\n")
    empty = FRONT[:start] + FRONT[FRONT.index("## Comments") :]
    out = writer.add_evidence(empty, "summary-drift-notes", "why")
    assert "## Evidence\n- [[summary-drift-notes]] — why\n" in out


def test_an_empty_understanding_is_filled() -> None:
    start = FRONT.index("## Current understanding\n") + len("## Current understanding\n")
    empty = FRONT[:start] + FRONT[FRONT.index("## Open questions") :]
    out = writer.set_understanding(empty, "Fresh prose.")
    assert "## Current understanding\nFresh prose.\n## Open questions" in out

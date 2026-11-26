"""Every problem the parser reports on a malformed page, one damage at a time, from the fixture thread."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.lab.parser import parse_experiment, parse_front_page

THREAD = Path(__file__).parent.parent / "fixtures" / "lab" / "summary-drift"
FRONT = (THREAD / "summary-drift.md").read_text(encoding="utf-8")
E2 = (THREAD / "experiments" / "summary-drift-E2-section-hash.md").read_text(encoding="utf-8")
Q5 = "- **Q5** Which page types change most often between summary refreshes? · open · raised 2026-11-21"


def _messages(text: str) -> list[str]:
    return [p.message for p in parse_front_page("summary-drift", text).problems]


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("## Evidence\n", "## Evidence\n- [[x]] — y\n\n## Evidence\n", "duplicate heading '## Evidence'"),
        ("## Comments\n", "## Stray\n\n## Comments\n", "unrecognised heading '## Stray'"),
        (Q5, Q5.replace("· open ·", "· pondering ·"), "unknown question status 'pondering'"),
        (Q5, Q5.replace("· open · raised", "· answered ·"), "is 'answered' but sits under '## Open questions'"),
        (Q5, Q5 + "\nsome loose prose", "unrecognised line under '## Open questions'"),
        ("- alex: keep this thread", "loose prose\n- alex: keep this thread", "unrecognised line under '## Comments'"),
        ("planned · runs after 2026-11-21 13:00", "no tail at all", "line is not '<text> · <status> · <when>'"),
    ],
)
def test_one_damage_one_problem(old: str, new: str, expected: str) -> None:
    assert old in FRONT
    messages = _messages(FRONT.replace(old, new, 1))
    assert any(expected in m for m in messages), messages


def test_headings_out_of_order() -> None:
    head, rest = FRONT.split("## Evidence\n", 1)
    evidence, comments = rest.split("## Comments\n", 1)
    swapped = head + "## Comments\n" + comments.rstrip("\n") + "\n\n## Evidence\n" + evidence
    assert any("headings are out of order" in m for m in _messages(swapped))


def test_frontmatter_that_isnt_a_mapping() -> None:
    page = parse_front_page("summary-drift", "---\n- just\n- a list\n---\n# T\n")
    assert [p.message for p in page.problems if p.line == 1][0] == "frontmatter is not a mapping"


def test_without_an_h1_the_title_is_the_frontmatters() -> None:
    page = parse_front_page("summary-drift", FRONT.replace("# Keeping cached summaries honest\n", "", 1))
    assert page.title == "Keeping cached summaries honest"


def test_an_experiment_with_a_bad_id_or_status() -> None:
    bad = E2.replace("id: E2", "id: X2", 1).replace("status: planned", "status: brewing", 1)
    messages = [p.message for p in parse_experiment("summary-drift", bad).problems]
    assert "experiment id 'X2' is not E<n>" in messages
    assert "unknown experiment status 'brewing'" in messages

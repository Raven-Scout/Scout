"""Parser and writer robustness: CRLF pages, fenced code blocks, very long lines."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scout.lab.model import TimelineEntry
from scout.lab.parser import parse_experiment, parse_front_page, parse_timeline
from scout.lab.writer import (
    add_comment,
    add_evidence,
    add_question,
    add_reply,
    answer_question,
    prepend_timeline,
    set_frontmatter,
    set_understanding,
)

THREAD_DIR = Path(__file__).parent.parent / "fixtures" / "lab" / "summary-drift"
SLUG = "summary-drift"


def _front() -> str:
    return (THREAD_DIR / "summary-drift.md").read_text(encoding="utf-8")


def _crlf(text: str) -> str:
    return text.replace("\n", "\r\n")


def _only_crlf(text: str) -> bool:
    return text.count("\r\n") == text.count("\n") and "\r" not in text.replace("\r\n", "")


def _in_understanding(text: str, block: str) -> str:
    """Put ``block`` at the end of ``## Current understanding`` (a free-prose section)."""
    return text.replace("\n## Open questions\n", f"\n{block}\n\n## Open questions\n", 1)


# --- CRLF ------------------------------------------------------------------------------


def test_crlf_page_parses_without_stray_carriage_returns() -> None:
    lf = parse_front_page(SLUG, _front())
    t = parse_front_page(SLUG, _crlf(_front()))
    assert t.problems == []
    assert (t.title, t.visibility, t.question) == (lf.title, lf.visibility, lf.question)
    assert [q.when for q in t.open] == [q.when for q in lf.open]
    assert [q.text for q in t.answered] == [q.text for q in lf.answered]
    for q in [*t.open, *t.answered]:
        assert "\r" not in f"{q.text}{q.status}{q.when}{q.note or ''}"
        assert all("\r" not in c.text and "\r" not in (c.stamp or "") for c in q.comments)
    assert all("\r" not in f"{e.title}{e.when}{e.note or ''}" for e in t.experiments)
    assert all("\r" not in e.why for e in t.evidence)


def test_crlf_timeline_and_experiment_parse_like_lf() -> None:
    tl = (THREAD_DIR / f"{SLUG}-timeline.md").read_text(encoding="utf-8")
    assert parse_timeline(_crlf(tl)) == parse_timeline(tl)
    exp = next((THREAD_DIR / "experiments").glob("*.md")).read_text(encoding="utf-8")
    a = parse_experiment(SLUG, exp)
    b = parse_experiment(SLUG, _crlf(exp))
    assert (b.sections, b.problems, b.frontmatter) == (a.sections, a.problems, a.frontmatter)


def test_writes_keep_crlf_line_endings() -> None:
    crlf = _crlf(_front())
    out = add_comment(crlf, "Q3", "alex", "one more thought", "2026-11-22 09:00")
    assert _only_crlf(out)
    assert out == _crlf(add_comment(_front(), "Q3", "alex", "one more thought", "2026-11-22 09:00"))
    out2, qid = add_question(crlf, "Does x hold?", "2026-11-22")
    assert qid and _only_crlf(out2)
    assert _only_crlf(answer_question(crlf, "Q3", "yes", "2026-11-22"))
    assert _only_crlf(set_understanding(crlf, "Line one.\nLine two."))
    assert _only_crlf(add_reply(crlf, "Q3", 1, "ok", "2026-11-22 09:00"))
    assert _only_crlf(set_frontmatter(crlf, status="paused"))
    assert _only_crlf(add_evidence(crlf, "new-note", "because"))
    assert parse_front_page(SLUG, out).problems == []


def test_lf_writes_stay_lf() -> None:
    assert "\r" not in add_comment(_front(), "Q3", "alex", "hi", "2026-11-22 09:00")


def test_timeline_prepend_keeps_crlf() -> None:
    tl = "# T - timeline\r\n\r\n### 2026-11-20 10:00 · note\r\n- old\r\n"
    out = prepend_timeline(tl, TimelineEntry("2026-11-21 10:00", "owner", ["new"]))
    assert _only_crlf(out)
    assert [e.stamp for e in parse_timeline(out)] == ["2026-11-21 10:00", "2026-11-20 10:00"]


# --- code fences -----------------------------------------------------------------------


@pytest.mark.parametrize(("opener", "closer"), [("```", "```"), ("~~~", "~~~"), ("````", "````"), ("```text", "```")])
def test_h2_inside_code_fence_is_not_a_heading(opener: str, closer: str) -> None:
    base = parse_front_page(SLUG, _front())
    text = _in_understanding(_front(), f"{opener}\n## Comments\n## Not a heading\n{closer}")
    t = parse_front_page(SLUG, text)
    assert t.problems == base.problems == []
    assert len(t.comments) == len(base.comments)


def test_unclosed_fence_runs_to_end_of_file() -> None:
    t = parse_front_page(SLUG, _in_understanding(_front(), "~~~\n```"))
    assert any("missing heading '## Open questions'" in p.message for p in t.problems)


def test_fence_helper_rules() -> None:
    from scout.lab.parser import fenced

    lines = ["a", "```", "x", "~~~", "```", "b", "```x```", "c"]
    # the ~~~ inside a ``` block does not close it; an inline ```x``` is not an opener
    assert fenced(lines) == [False, True, True, True, True, False, False, False]
    assert fenced(["````", "```", "````", "d"]) == [True, True, True, False]


def test_h1_and_timeline_heading_inside_fence_are_ignored() -> None:
    page = "---\nstatus: active\n---\n```\n# Fake title\n```\n# Real title\n"
    assert parse_front_page("x", page).title == "Real title"
    tl = "# T — timeline\n\n### 2026-11-20 10:00 · note\n- real\n```\n### 2026-11-21 10:00 · note\n- fake\n```\n"
    assert [e.stamp for e in parse_timeline(tl)] == ["2026-11-20 10:00"]


def test_writes_ignore_fenced_headings_and_leave_the_fence_intact() -> None:
    fence = "```\n## Evidence\n## Comments\n```"
    text = _in_understanding(_front(), fence)
    out = add_evidence(text, "new-note", "because")
    assert fence in out
    assert out.index("- [[new-note]]") > out.index("\n## Evidence\n", out.index(fence))
    out = add_comment(text, "thread", "alex", "hi", "2026-11-22 09:00")
    assert fence in out
    assert out.rstrip().endswith("- alex (2026-11-22 09:00): hi")
    out, _ = add_question(text, "Another?", "2026-11-22")
    assert fence in out
    assert parse_front_page(SLUG, out).problems == []
    # the fence sits in the CRLF page too
    assert fence.replace("\n", "\r\n") in add_evidence(_crlf(text), "new-note", "because")


# --- long lines ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        "- **Q1** " + "word · open · " * 7200,  # many overlapping " · " candidates
        "- **Q2** " + "a" * 100_000,  # no separator at all
        "- **Q2** " + "a · " * 25_000 + "b",  # separators, never a status tail
        "- **Q2** x · open · " + "y — " * 25_000,  # many note dashes
    ],
)
def test_long_single_line_parses_quickly(item: str) -> None:
    assert len(item) > 99_000
    page = _front().replace("## Open questions\n", f"## Open questions\n{item}\n", 1)
    start = time.perf_counter()
    parse_front_page(SLUG, page)
    assert time.perf_counter() - start < 5  # ~0.01 s on a quiet Mac; loose enough for a loaded CI runner


def test_long_heading_and_spaces_parse_quickly() -> None:
    page = _front().replace("## Open questions\n", "## Open questions" + " " * 100_000 + "x\n", 1)
    start = time.perf_counter()
    parse_front_page(SLUG, page)
    assert time.perf_counter() - start < 5  # ~0.01 s on a quiet Mac; loose enough for a loaded CI runner


def test_tail_split_keeps_last_separator_and_first_note_dash() -> None:
    item = "- **Q9** a · b · c · open · raised 2026-11-21 — n1 — n2"
    page = _front().replace("## Open questions\n", f"## Open questions\n{item}\n", 1)
    q = next(q for q in parse_front_page(SLUG, page).open if q.qid == "Q9")
    assert (q.text, q.status, q.when, q.note) == ("a · b · c", "open", "raised 2026-11-21", "n1 — n2")

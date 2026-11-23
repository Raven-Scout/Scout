from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab.model import TimelineEntry
from scout.lab.parser import parse_front_page, parse_timeline
from scout.lab.writer import (
    add_comment,
    add_evidence,
    add_question,
    add_reply,
    answer_question,
    drop_question,
    new_front_page,
    new_timeline,
    prepend_timeline,
    set_experiment_line,
    set_frontmatter,
    set_understanding,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
STAMP = "2026-11-22 09:00"


def _front() -> str:
    return (FIXTURES / "summary-drift" / "summary-drift.md").read_text(encoding="utf-8")


def _parse(text: str):  # type: ignore[no-untyped-def]
    return parse_front_page("summary-drift", text)


def test_round_trip_comment_with_format_tokens() -> None:
    body = "Q: is **Q3** · open · raised x — y\nscout (1): z"
    out = add_comment(_front(), "Q3", "alex", body, STAMP)
    t = _parse(out)
    q3 = t.open[0]
    assert t.problems == []
    assert [q.qid for q in t.open] == ["Q3", "Q5"]
    assert len(q3.comments) == 3
    last = q3.comments[-1]
    assert last.author == "alex"
    assert last.text == "Q: is **Q3** · open · raised x — y scout (1): z"
    assert last.stamp == STAMP
    assert last.replies == []
    assert q3.comments[0].replies[0].is_scout


def test_add_comment_thread_and_experiment() -> None:
    out = add_comment(_front(), "thread", "alex", "more", STAMP)
    out = add_comment(out, "E2", "alex", "why a hash?", STAMP)
    t = _parse(out)
    assert t.problems == []
    assert [c.text for c in t.comments] == ["keep this thread on detection, not on how summaries get rewritten", "more"]
    assert t.comments[0].replies[0].is_scout
    assert t.experiments[0].comments[0].text == "why a hash?"


def test_add_comment_unknown_target_names_it() -> None:
    with pytest.raises(LabError, match="Q99"):
        add_comment(_front(), "Q99", "alex", "x", STAMP)
    with pytest.raises(LabError, match="E9"):
        add_comment(_front(), "E9", "alex", "x", STAMP)


def test_comment_normalization_refuses_empty_and_overlong() -> None:
    with pytest.raises(LabError, match="empty"):
        add_comment(_front(), "thread", "alex", "  \n ", STAMP)
    with pytest.raises(LabError, match="2000"):
        add_comment(_front(), "thread", "alex", "x" * 2001, STAMP)


def test_add_reply_marks_comment_answered() -> None:
    out = add_reply(_front(), "Q3", 2, "Added the baseline to E2.", STAMP)
    t = _parse(out)
    assert t.problems == []
    assert t.unanswered_owner_comments() == []
    reply = t.open[0].comments[1].replies[0]
    assert reply.is_scout and reply.text == "Added the baseline to E2."


def test_add_reply_bad_index() -> None:
    with pytest.raises(LabError, match="comment 3"):
        add_reply(_front(), "Q3", 3, "x", STAMP)


def test_add_question_increments_next_q() -> None:
    out, qid = add_question(_front(), "Does X hold?", "2026-11-22")
    assert qid == "Q8"
    t = _parse(out)
    assert t.problems == []
    assert t.frontmatter["next_q"] == 9
    assert [q.qid for q in t.open] == ["Q3", "Q5", "Q8"]
    assert out.split("\n")[[ln.startswith("- **Q8**") for ln in out.split("\n")].index(True)].endswith(
        "· open · raised 2026-11-22"
    )
    out2, qid2 = add_question(out, "Another?", "2026-11-22", by="alex")
    assert qid2 == "Q9"
    assert _parse(out2).open[-1].when == "raised 2026-11-22 by alex"


def test_add_question_lands_after_last_comment_of_last_question() -> None:
    base = add_comment(_front(), "Q5", "alex", "hm", STAMP)
    out, _ = add_question(base, "Next?", "2026-11-22")
    t = _parse(out)
    assert t.problems == []
    assert [q.qid for q in t.open] == ["Q3", "Q5", "Q8"]
    assert [c.text for c in t.open[1].comments] == ["hm"]


def test_answer_moves_with_comments() -> None:
    out = answer_question(_front(), "Q3", "The hash check held up on the sample.", "2026-11-22")
    t = _parse(out)
    assert t.problems == []
    assert [q.qid for q in t.open] == ["Q5"]
    assert [q.qid for q in t.answered] == ["Q3", "Q1", "Q2"]
    q3 = t.answered[0]
    assert q3.status == "answered"
    assert q3.when == "2026-11-22"
    assert q3.note == "The hash check held up on the sample."
    assert [c.text for c in q3.comments] == [
        "try it on a real sample of pages, not only synthetic ones",
        "and report how many false alarms it raises",
    ]
    assert q3.comments[0].replies[0].is_scout


def test_answer_note_with_middle_dots_reparses() -> None:
    out = answer_question(_front(), "Q5", "a · b · c", "2026-11-22")
    t = _parse(out)
    assert t.problems == []
    q = t.answered[0]
    assert (q.qid, q.status, q.note) == ("Q5", "answered", "a - b - c")
    assert q.text == "Which page types change most often between summary refreshes?"


def test_answer_refuses_non_open_and_unknown() -> None:
    with pytest.raises(LabError, match="Q1"):
        answer_question(_front(), "Q1", "n", "2026-11-22")
    with pytest.raises(LabError, match="Q42"):
        answer_question(_front(), "Q42", "n", "2026-11-22")


def test_drop_refuses_owner_commented_question() -> None:
    with pytest.raises(LabError, match="owner comment"):
        drop_question(_front(), "Q3", "x", "2026-11-22")
    with pytest.raises(LabError, match="owner comment"):
        drop_question(_front(), "Q3", "x", "2026-11-22", owner_comment=3)
    out = drop_question(_front(), "Q3", "owner: not needed", "2026-11-22", owner_comment=2)
    t = _parse(out)
    assert t.problems == []
    assert t.answered[0].qid == "Q3"
    assert t.answered[0].status == "dropped"
    assert t.answered[0].when == "2026-11-22"
    assert t.answered[0].note == "owner: not needed"
    assert len(t.answered[0].comments) == 2


def test_drop_uncommented_question_needs_no_owner_comment() -> None:
    out = drop_question(_front(), "Q5", "superseded", "2026-11-22")
    assert [q.qid for q in _parse(out).open] == ["Q3"]


def test_missing_heading_refuses() -> None:
    text = _front().replace("## Open questions\n", "## Open qs\n")
    with pytest.raises(LabError, match="Open questions"):
        add_question(text, "Does X hold?", "2026-11-22")
    with pytest.raises(LabError, match="Answered questions"):
        answer_question(_front().replace("## Answered questions", "## Done"), "Q3", "n", "2026-11-22")
    with pytest.raises(LabError, match="Comments"):
        add_comment(_front().replace("## Comments", "## Chatter"), "thread", "alex", "x", STAMP)


def test_set_frontmatter_replaces_and_adds_keeping_order() -> None:
    out = set_frontmatter(_front(), status="paused", last_run="2026-11-22T01:11", extra=3)
    t = _parse(out)
    assert t.problems == []
    assert t.status == "paused"
    assert t.frontmatter["extra"] == 3
    keys = list(t.frontmatter)
    assert keys[:3] == ["type", "title", "status"]
    assert keys[-1] == "extra"
    assert out.count("last_run:") == 1
    assert "last_run: 2026-11-22T01:11\n" in out
    assert out.split("---\n", 2)[2] == _front().split("---\n", 2)[2]


def test_set_frontmatter_quotes_unsafe_strings() -> None:
    out = set_frontmatter(_front(), title="Why: a # b")
    assert _parse(out).frontmatter["title"] == "Why: a # b"


def test_set_understanding_replaces_only_that_body() -> None:
    out = set_understanding(_front(), "New view.\n\nSecond para.")
    t = _parse(out)
    assert t.problems == []
    assert t.understanding == "New view.\n\nSecond para."
    assert t.question == _parse(_front()).question
    assert out.split("## Open questions")[1] == _front().split("## Open questions")[1]
    assert "\n\n## Open questions" in out


def test_set_understanding_refuses_heading_lines() -> None:
    with pytest.raises(LabError, match="heading"):
        set_understanding(_front(), "ok\n## Evidence\nbad")
    with pytest.raises(LabError, match="empty"):
        set_understanding(_front(), "   ")


def test_add_evidence_appends_and_dedupes() -> None:
    out = add_evidence(_front(), "new-topic", "shows\nsomething")
    t = _parse(out)
    assert t.problems == []
    assert [e.target for e in t.evidence][-1] == "new-topic"
    assert t.evidence[-1].why == "shows something"
    assert add_evidence(out, "new-topic", "again") == out
    assert add_evidence(_front(), "summary-drift-notes", "dup") == _front()


def test_set_experiment_line_inserts_newest_first_and_updates_in_place() -> None:
    out = set_experiment_line(_front(), "E3", "summary-drift-E3-x", "Third try", "planned", "runs after 2026-11-23")
    t = _parse(out)
    assert t.problems == []
    assert [e.eid for e in t.experiments] == ["E3", "E2", "E1"]
    assert t.experiments[0].target == "summary-drift-E3-x"
    assert t.experiments[0].note is None

    out = add_comment(out, "E2", "alex", "hi", STAMP)
    out = set_experiment_line(
        out, "E2", "summary-drift-E2-section-hash", "Hash check", "done", "2026-11-22", "agreed on most · ok"
    )
    t = _parse(out)
    assert t.problems == []
    assert [e.eid for e in t.experiments] == ["E3", "E2", "E1"]
    e2 = t.experiments[1]
    assert (e2.status, e2.when, e2.note, e2.title) == ("done", "2026-11-22", "agreed on most - ok", "Hash check")
    assert [c.text for c in e2.comments] == ["hi"]


def test_set_experiment_line_rejects_bad_status() -> None:
    with pytest.raises(LabError, match="status"):
        set_experiment_line(_front(), "E3", "t", "x", "finished", "now")


def test_new_front_page_parses_clean() -> None:
    text = new_front_page(
        "probes",
        "Do probes generalise: a test",
        "Do linear probes transfer across model families?",
        ["Do they survive fine-tuning?", "What about · middle dots?"],
        [("probe-topic", "baseline results")],
        "2026-11-22",
        "normal",
    )
    t = parse_front_page("probes", text)
    assert t.problems == []
    assert t.status == "proposed"
    assert t.title == "Do probes generalise: a test"
    assert t.frontmatter["next_q"] == 3
    assert t.frontmatter["next_e"] == 1
    assert t.visibility == "normal"
    assert [q.qid for q in t.open] == ["Q1", "Q2"]
    assert t.open[1].text == "What about - middle dots?"
    assert [e.target for e in t.evidence] == ["probe-topic"]
    out, qid = add_question(text, "Third?", "2026-11-23")
    assert qid == "Q3"
    assert parse_front_page("probes", out).problems == []


def test_new_front_page_without_questions_or_evidence() -> None:
    t = parse_front_page("p", new_front_page("p", "T", "Q?", [], [], "2026-11-22", "private"))
    assert t.problems == []
    assert t.visibility == "private"
    assert t.frontmatter["next_q"] == 1


def test_new_front_page_rejects_bad_slug() -> None:
    with pytest.raises(LabError, match="slug"):
        new_front_page("Bad Slug", "T", "Q?", [], [], "2026-11-22", "normal")


def test_new_front_page_rejects_trailing_newline_slug() -> None:
    with pytest.raises(LabError, match="slug"):
        new_front_page("abc\n", "T", "Q?", [], [], "2026-11-22", "normal")


def test_ids_with_trailing_newline_refused() -> None:
    from scout.lab.writer import add_comment, set_experiment_line

    page = new_front_page("p", "T", "Q?", ["one"], [], "2026-11-22", "normal")
    with pytest.raises(LabError):
        add_comment(page, "Q1\n", "alex", "hi", "2026-11-22 09:30")
    with pytest.raises(LabError):
        set_experiment_line(page, "E1\n", "p-E1-x", "t", "planned", "2026-11-22")


def test_prepend_timeline_newest_first() -> None:
    text = new_timeline("summary-drift", "Keeping cached summaries honest")
    assert text.startswith("# Keeping cached summaries honest — timeline\n")
    text = prepend_timeline(text, TimelineEntry("2026-11-20 01:11", "research run", ["Read two papers", "Opened Q3"]))
    text = prepend_timeline(text, TimelineEntry("2026-11-21 01:11", "experiment", ["Planned E2"]))
    entries = parse_timeline(text)
    assert [(e.stamp, e.tag) for e in entries] == [
        ("2026-11-21 01:11", "experiment"),
        ("2026-11-20 01:11", "research run"),
    ]
    assert entries[1].bullets == ["Read two papers", "Opened Q3"]
    assert text.startswith("# Keeping cached summaries honest — timeline\n\n### 2026-11-21 01:11")
    assert text.endswith("\n")
    assert "\n\n\n" not in text


def test_prepend_timeline_validates() -> None:
    base = new_timeline("m", "M")
    with pytest.raises(LabError, match="tag"):
        prepend_timeline(base, TimelineEntry("2026-11-21 01:11", "nope", ["x"]))
    with pytest.raises(LabError, match="stamp"):
        prepend_timeline(base, TimelineEntry("yesterday", "owner", ["x"]))
    with pytest.raises(LabError, match="bullets"):
        prepend_timeline(base, TimelineEntry("2026-11-21 01:11", "owner", []))
    with pytest.raises(LabError, match="heading"):
        prepend_timeline("no h1\n", TimelineEntry("2026-11-21 01:11", "owner", ["x"]))


def test_trailing_newline_is_refused_in_author_key_and_stamp() -> None:
    with pytest.raises(LabError, match="author"):
        add_comment(_front(), "Q3", "alex\n", "hi", STAMP)
    with pytest.raises(LabError, match="key"):
        set_frontmatter(_front(), **{"status\n": "paused"})
    with pytest.raises(LabError, match="stamp"):
        prepend_timeline(new_timeline("m", "M"), TimelineEntry("2026-11-21 01:11\n", "owner", ["x"]))

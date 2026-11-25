from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import paths
from scout.lab.model import ModelRef
from scout.lab.parser import load_thread, parse_experiment, parse_front_page, parse_timeline

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
THREAD_DIR = FIXTURES / "summary-drift"


def _front() -> str:
    return (THREAD_DIR / "summary-drift.md").read_text(encoding="utf-8")


def test_parses_fixture_thread() -> None:
    t = parse_front_page("summary-drift", _front())
    assert t.status == "active"
    assert t.visibility == "normal"
    assert t.title == "Keeping cached summaries honest"
    assert [q.qid for q in t.open] == ["Q3", "Q5"]
    assert [q.status for q in t.answered] == ["answered", "dropped"]
    assert t.open[0].comments[0].author == "alex"
    assert t.open[0].comments[0].replies[0].is_scout
    assert t.open[0].comments[0].replies[0].stamp == "2026-11-21 01:11"
    assert t.experiments[1].status == "done"
    assert t.experiments[1].note == "length rule missed 7 rewrites"
    assert t.experiments[0].eid == "E2"
    assert t.experiments[0].target == "summary-drift-E2-section-hash"
    assert t.experiments[0].status == "planned"
    assert [e.target for e in t.evidence] == [
        "summary-drift-notes",
        "regeneration-cost-notes",
    ]
    assert t.answered[0].note is not None and t.answered[0].note.startswith("no: it missed")
    assert t.answered[0].when == "2026-11-20"
    assert t.problems == []


def test_unanswered_owner_comments() -> None:
    t = parse_front_page("summary-drift", _front())
    got = t.unanswered_owner_comments()
    assert got == [("Q3", t.open[0].comments[1])]
    assert got[0][1].text == "and report how many false alarms it raises"


def test_question_text_may_contain_middle_dot() -> None:
    text = _front().replace(
        "## Open questions\n",
        "## Open questions\n- **Q9** a · b · open · raised 2026-11-21\n",
    )
    t = parse_front_page("summary-drift", text)
    q9 = next(q for q in t.open if q.qid == "Q9")
    assert q9.text == "a · b"
    assert q9.status == "open"
    assert q9.when == "raised 2026-11-21"
    assert t.problems == []


def test_renamed_heading_is_a_problem_not_a_crash() -> None:
    t = parse_front_page("summary-drift", _front().replace("## Open questions", "## Open Qs"))
    assert t.open == []
    assert any("Open questions" in p.message for p in t.problems)
    assert [q.qid for q in t.answered] == ["Q1", "Q2"]
    assert len(t.experiments) == 2
    assert len(t.evidence) == 2
    assert t.question.startswith("What is the cheapest signal")


def test_unknown_status_is_a_problem() -> None:
    text = _front().replace("· open · raised 2026-11-21", "· pending · raised 2026-11-21")
    t = parse_front_page("summary-drift", text)
    q5 = next(q for q in t.open if q.qid == "Q5")
    assert q5.status == "pending"
    assert any("pending" in p.message and p.line is not None for p in t.problems)


def test_unrecognised_line_in_a_section_is_a_problem() -> None:
    text = _front().replace("## Evidence\n", "## Evidence\nstray prose\n")
    t = parse_front_page("summary-drift", text)
    assert any(p.message and "stray prose" in p.message for p in t.problems)
    assert len(t.evidence) == 2


def test_missing_frontmatter_is_a_problem() -> None:
    body = _front().split("---\n", 2)[2]
    t = parse_front_page("summary-drift", body)
    assert t.frontmatter == {}
    assert any("frontmatter" in p.message for p in t.problems)


def test_malformed_frontmatter_date_is_a_problem_not_a_crash() -> None:
    t = parse_front_page("summary-drift", _front().replace("created: 2026-11-20", "created: 2026-13-45"))
    assert t.frontmatter == {}
    assert any("frontmatter" in p.message for p in t.problems)
    assert [q.qid for q in t.open] == ["Q3", "Q5"]
    assert len(t.experiments) == 2

    path = THREAD_DIR / "experiments" / "summary-drift-E2-section-hash.md"
    text = path.read_text(encoding="utf-8").replace("planned: 2026-11-21T01:11", "planned: 2026-13-45")
    e = parse_experiment("summary-drift", text)
    assert any("frontmatter" in p.message for p in e.problems)
    assert "Hypothesis" in e.sections and "Method" in e.sections


def test_parse_timeline_fixture() -> None:
    entries = parse_timeline((THREAD_DIR / "summary-drift-timeline.md").read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert entries[0].tag == "research run"
    assert entries[0].stamp == "2026-11-21 01:11"
    assert entries[0].bullets[0].startswith("**Did:**")
    assert entries[1].tag == "owner"


def test_parse_experiment_fixture() -> None:
    path = THREAD_DIR / "experiments" / "summary-drift-E1-baseline.md"
    e = parse_experiment("summary-drift", path.read_text(encoding="utf-8"))
    assert e.status == "done"
    assert e.eid == "E1"
    assert e.questions == ["Q1"]
    assert "Result" in e.sections
    assert e.sections["Result"].startswith("The length rule missed 7")
    assert e.problems == []


def test_parse_planned_experiment_fixture() -> None:
    path = THREAD_DIR / "experiments" / "summary-drift-E2-section-hash.md"
    e = parse_experiment("summary-drift", path.read_text(encoding="utf-8"))
    assert e.status == "planned"
    assert e.questions == ["Q3"]
    assert e.sections["Result"] == ""
    assert e.sections["What it means"] == ""
    assert e.problems == []


def test_paths(tmp_path: Path) -> None:
    assert paths.lab_dir(tmp_path) == tmp_path / "knowledge-base" / "lab"
    assert paths.index_path(tmp_path).name == "lab.md"
    assert paths.front_page(tmp_path, "summary-drift").name == "summary-drift.md"
    assert paths.timeline_path(tmp_path, "summary-drift").name == "summary-drift-timeline.md"
    assert paths.experiment_note(tmp_path, "summary-drift", "E1") is None
    exp = paths.thread_dir(tmp_path, "summary-drift") / "experiments"
    exp.mkdir(parents=True)
    (exp / "summary-drift-E1-baseline.md").write_text("x", encoding="utf-8")
    (exp / "summary-drift-E10-other.md").write_text("x", encoding="utf-8")
    note = paths.experiment_note(tmp_path, "summary-drift", "E1")
    assert note is not None and note.name == "summary-drift-E1-baseline.md"
    assert paths.experiment_dir(note) == exp / "summary-drift-E1-baseline"


def test_load_thread(tmp_path: Path) -> None:
    d = paths.thread_dir(tmp_path, "summary-drift")
    d.mkdir(parents=True)
    (d / "summary-drift.md").write_text(_front(), encoding="utf-8")
    assert load_thread(tmp_path, "summary-drift").slug == "summary-drift"
    with pytest.raises(LabError):
        load_thread(tmp_path, "missing")


def test_experiment_ml_fields_parse() -> None:
    sha1, sha2 = "1" * 40, "2" * 40
    extra = (
        "timeout_hours: 6\ndisk_budget_bytes: 8589934592\n"
        f'models:\n  - repo: org/model-a\n    revision: "{sha1}"\n  - repo: org/model-b.v2\n    revision: "{sha2}"\n'
    )
    path = THREAD_DIR / "experiments" / "summary-drift-E2-section-hash.md"
    text = path.read_text(encoding="utf-8").replace("visibility: normal\n", f"visibility: normal\n{extra}", 1)
    e = parse_experiment("summary-drift", text)
    assert e.timeout_hours == 6.0
    assert e.disk_budget_bytes == 8589934592
    assert e.models == [ModelRef("org/model-a", sha1), ModelRef("org/model-b.v2", sha2)]
    plain = parse_experiment("summary-drift", path.read_text(encoding="utf-8"))
    assert (plain.timeout_hours, plain.models, plain.disk_budget_bytes) == (None, [], None)

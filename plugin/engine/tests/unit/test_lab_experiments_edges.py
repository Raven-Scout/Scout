"""The experiment verbs' refusals on bad input and damaged pages, and how a frontmatter time is read."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import experiments, ops, paths

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG = "summary-drift"
BODY = "## Hypothesis\nH.\n\n## Method\nM.\n\n## Data\nD.\n"
LATE = dt.datetime(2026, 11, 21, 13, 30, tzinfo=dt.UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(tmp_path, SLUG))
    return tmp_path


def _edit_front(vault: Path, old: str, new: str) -> None:
    front = paths.front_page(vault, SLUG)
    text = front.read_text("utf-8")
    assert old in text
    front.write_text(text.replace(old, new, 1), "utf-8")


def test_frontmatter_times_read_as_aware_datetimes() -> None:
    tz = dt.timezone(dt.timedelta(hours=1))
    assert experiments.as_datetime(dt.date(2026, 11, 21), tz, "planned") == dt.datetime(2026, 11, 21, tzinfo=tz)
    assert experiments.as_datetime("2026-11-21T13:00", tz, "planned") == dt.datetime(2026, 11, 21, 13, tzinfo=tz)
    aware = dt.datetime(2026, 11, 21, 13, tzinfo=dt.UTC)
    assert experiments.as_datetime(aware, tz, "planned") == aware
    with pytest.raises(LabError, match="bad planned time"):
        experiments.as_datetime("soon", tz, "planned")


@pytest.mark.parametrize("eid", ["X1", "E", "e2"])
def test_a_bad_experiment_id_is_refused(vault: Path, eid: str) -> None:
    with pytest.raises(LabError, match="is not an experiment ID"):
        experiments.start(vault, SLUG, eid, now=LATE)


def test_an_experiment_without_a_note_is_refused(vault: Path) -> None:
    with pytest.raises(LabError, match="no experiment note for E9"):
        experiments.start(vault, SLUG, "E9", now=LATE)


def test_an_experiment_missing_from_the_front_page_is_refused(vault: Path) -> None:
    ops.reply(vault, SLUG, "Q3", 2, "Will report the false alarms.", now=LATE)  # the hold on E2, answered
    _edit_front(
        vault,
        "- [[summary-drift-E2-section-hash|E2]] Section-hash",
        "- [[summary-drift-E2-section-hash|E3]] Section-hash",
    )
    with pytest.raises(LabError, match="E2 is not listed under '## Experiments'"):
        experiments.start(vault, SLUG, "E2", now=LATE)


@pytest.mark.parametrize("value", ["x", "0", "true"])
def test_a_bad_next_e_refuses_a_plan(vault: Path, value: str) -> None:
    _edit_front(vault, "next_e: 3", f"next_e: {value}")
    with pytest.raises(LabError, match="next_e"):
        experiments.plan(vault, SLUG, "again", "Again", ["Q5"], BODY, 0.5, now=LATE)


@pytest.mark.parametrize(
    ("settings", "match"),
    [
        ({"experiment_notice_hours": True}, "must be a number"),
        ({"experiment_notice_hours": "soon"}, "must be a number"),
        ({"experiment_notice_hours": -1}, "0 or more"),
        ({"experiment_cost_cap_usd": 0}, "greater than 0"),
    ],
)
def test_bad_lab_settings_refuse_a_plan(
    vault: Path, monkeypatch: pytest.MonkeyPatch, settings: dict[str, object], match: str
) -> None:
    monkeypatch.setattr(ops, "_lab_settings", lambda _v: settings)
    with pytest.raises(LabError, match=match):
        experiments.plan(vault, SLUG, "again", "Again", ["Q5"], BODY, now=LATE)


def test_a_veto_by_a_bad_author_is_refused(vault: Path) -> None:
    with pytest.raises(LabError, match="bad author"):
        experiments.veto(vault, SLUG, "E2", "1x", now=LATE)


def test_ops_refuses_an_unknown_verb_a_missing_page_and_a_taken_next_q(vault: Path) -> None:
    with pytest.raises(LabError, match="unknown owner verb 'explode'"):
        ops.owner_verb(vault, SLUG, "explode", "alex", None)
    with pytest.raises(LabError, match="does not exist"):
        ops._read(vault / "missing.md")
    _edit_front(vault, "next_q: 8", "next_q: 3")
    with pytest.raises(LabError, match="Q3 already exists"):
        ops.question_add(vault, SLUG, "Another?", "alex")


def test_mutate_without_a_slug_writes_without_the_heading_guard(vault: Path) -> None:
    page = vault / "note.md"
    page.write_text("x\n", encoding="utf-8")
    ops.mutate(vault, page, lambda text: text + "```\n")  # an open fence: only a thread page is guarded
    assert page.read_text(encoding="utf-8") == "x\n```\n"


def test_a_next_q_that_isnt_a_number_refuses_a_question(vault: Path) -> None:
    _edit_front(vault, "next_q: 8", "next_q: soon")
    with pytest.raises(LabError, match="next_q"):
        ops.question_add(vault, SLUG, "Another?", "alex")

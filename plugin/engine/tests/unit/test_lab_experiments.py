from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import experiments, ops, paths
from scout.lab.parser import load_thread, parse_experiment, parse_front_page, parse_timeline

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
PLAN_NOW = dt.datetime(2026, 11, 21, 1, 11, tzinfo=dt.UTC)
LATE = dt.datetime(2026, 11, 21, 13, 30, tzinfo=dt.UTC)
BODY = "## Hypothesis\nA section hash keeps false alarms rare.\n\n## Method\nRun it.\n\n## Data\nSampled pages.\n"


def _vault(tmp_path: Path, status: str = "active") -> Path:
    shutil.copytree(FIXTURES / "summary-drift", paths.thread_dir(tmp_path, "summary-drift"))
    front = paths.front_page(tmp_path, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("status: active", f"status: {status}", 1), "utf-8")
    return tmp_path


def _front(vault: Path) -> str:
    return paths.front_page(vault, "summary-drift").read_text(encoding="utf-8")


def _line(vault: Path, eid: str) -> str:
    return next(ln for ln in _front(vault).splitlines() if ln.startswith("- [[") and f"|{eid}]]" in ln)


def _clear_q3_comments(vault: Path) -> None:
    """The fixture's Q3 has an unanswered owner comment (#2); answer it so E2 is not held."""
    ops.reply(vault, "summary-drift", "Q3", 2, "noted", now=LATE)


def _note_status(vault: Path, eid: str) -> str:
    note = paths.experiment_note(vault, "summary-drift", eid)
    assert note is not None
    return str(parse_experiment("summary-drift", note.read_text(encoding="utf-8")).status)


def test_plan_sets_notice_and_cap_from_config(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    exp = experiments.plan(vault, "summary-drift", "bytype", "Hash check by page type", ["Q5"], BODY, now=PLAN_NOW)
    assert exp.eid == "E3"
    assert exp.status == "planned"
    assert exp.questions == ["Q5"]
    assert str(exp.frontmatter["not_before"]) == "2026-11-21T07:11"
    assert float(exp.frontmatter["cost_cap_usd"]) == 1.0
    assert exp.frontmatter["visibility"] == "normal"
    assert exp.problems == []
    note = paths.thread_dir(vault, "summary-drift") / "experiments" / "summary-drift-E3-bytype.md"
    assert note.is_file()
    assert exp.sections["Result"] == "" and "What it means" in exp.sections
    assert "planned · runs after 2026-11-21 07:11" in _line(vault, "E3")
    t = load_thread(vault, "summary-drift")
    assert t.problems == []
    assert t.frontmatter["next_e"] == 4


def test_plan_explicit_cap(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    exp = experiments.plan(vault, "summary-drift", "x", "T", [], BODY, cap_usd=0.25, now=PLAN_NOW)
    assert float(exp.frontmatter["cost_cap_usd"]) == 0.25
    assert exp.frontmatter["visibility"] == "normal"


def test_plan_refuses_a_front_page_whose_frontmatter_is_broken(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("visibility: normal", "visibility: [x", 1), "utf-8")
    with pytest.raises(LabError):
        experiments.plan(vault, "summary-drift", "x", "T", [], BODY, now=PLAN_NOW)


def test_plan_refuses_in_a_private_thread(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("visibility: normal", "visibility: private"), "utf-8")
    before = sorted(p.name for p in (paths.thread_dir(vault, "summary-drift") / "experiments").iterdir())
    with pytest.raises(LabError, match="experiments in a private thread aren't supported yet"):
        experiments.plan(vault, "summary-drift", "x", "T", [], BODY, now=PLAN_NOW)
    after = sorted(p.name for p in (paths.thread_dir(vault, "summary-drift") / "experiments").iterdir())
    assert after == before


@pytest.mark.parametrize(
    ("short", "body", "match"),
    [
        ("Bad Name", BODY, "bad experiment short name"),
        ("ok", "## Hypothesis\nx\n\n## Method\ny\n", "'## Data'"),
        ("ok", BODY + "\n## Result\nalready\n", "must not contain"),
    ],
)
def test_plan_validates_input(tmp_path: Path, short: str, body: str, match: str) -> None:
    vault = _vault(tmp_path)
    before = _front(vault)
    with pytest.raises(LabError, match=match):
        experiments.plan(vault, "summary-drift", short, "T", ["Q5"], body, now=PLAN_NOW)
    assert _front(vault) == before


def _assert_refused(vault: Path, match: str, **kwargs: float) -> None:
    before = _front(vault)
    with pytest.raises(LabError, match=match):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q5"], BODY, now=PLAN_NOW, **kwargs)
    assert _front(vault) == before
    assert paths.experiment_note(vault, "summary-drift", "E3") is None


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), float("-inf"), True, 0, -1])
def test_plan_refuses_bad_cap_argument(tmp_path: Path, cap: float) -> None:
    _assert_refused(_vault(tmp_path), "cost cap", cap_usd=cap)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("experiment_cost_cap_usd", ".nan"),
        ("experiment_cost_cap_usd", ".inf"),
        ("experiment_cost_cap_usd", "0"),
        ("experiment_notice_hours", "-1"),
        ("experiment_notice_hours", ".nan"),
        ("experiment_notice_hours", ".inf"),
    ],
)
def test_plan_refuses_bad_config_values(tmp_path: Path, key: str, value: str) -> None:
    vault = _vault(tmp_path)
    (vault / "scout-config.yaml").write_text(f"lab:\n  {key}: {value}\n", encoding="utf-8")
    _assert_refused(vault, f"lab.{key}")


def test_plan_refuses_answered_question(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    before = _front(vault)
    with pytest.raises(LabError, match="Q1 is not an open question"):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q1"], BODY, now=PLAN_NOW)
    assert _front(vault) == before
    assert paths.experiment_note(vault, "summary-drift", "E3") is None


def test_plan_refuses_inactive_thread(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "paused")
    with pytest.raises(LabError, match="paused"):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q5"], BODY, now=PLAN_NOW)


def test_plan_refuses_existing_eid(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("next_e: 3", "next_e: 2"), "utf-8")
    before = _front(vault)
    with pytest.raises(LabError, match="E2 already exists"):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q5"], BODY, now=PLAN_NOW)
    assert _front(vault) == before


def test_plan_refuses_damaged_page(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("## Evidence", "## Proof"), "utf-8")
    before = _front(vault)
    with pytest.raises(LabError, match="front page is damaged"):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q5"], BODY, now=PLAN_NOW)
    assert _front(vault) == before


def test_start_refuses_before_not_before(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _clear_q3_comments(vault)
    before = _front(vault)
    early = dt.datetime(2026, 11, 21, 12, 59, tzinfo=dt.UTC)
    with pytest.raises(LabError, match="not before 2026-11-21 13:00"):
        experiments.start(vault, "summary-drift", "E2", now=early)
    assert _front(vault) == before
    assert _note_status(vault, "E2") == "planned"


def test_start_refuses_when_held(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _clear_q3_comments(vault)
    ops.comment(vault, "summary-drift", "E2", "alex", "wait, not yet", now=LATE)
    with pytest.raises(LabError, match="held by 1 comment"):
        experiments.start(vault, "summary-drift", "E2", now=LATE)
    assert _note_status(vault, "E2") == "planned"


def test_start_held_by_question_comment_only_counts_owner(tmp_path: Path) -> None:
    vault = _vault(tmp_path)  # Q3 has one unanswered owner comment; its first one has a scout reply
    t = load_thread(vault, "summary-drift")
    exp = parse_experiment("summary-drift", paths.experiment_note(vault, "summary-drift", "E2").read_text("utf-8"))  # type: ignore[union-attr]
    held = experiments.holds(t, exp)
    assert [c.text for c in held] == ["and report how many false alarms it raises"]
    with pytest.raises(LabError, match="held by 1 comment"):
        experiments.start(vault, "summary-drift", "E2", now=LATE)
    _clear_q3_comments(vault)
    assert experiments.holds(load_thread(vault, "summary-drift"), exp) == []


def test_start_refuses_when_not_planned_or_inactive(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(LabError, match="E1 is done, not planned"):
        experiments.start(vault, "summary-drift", "E1", now=LATE)
    paused = _vault(tmp_path / "p", "paused")
    with pytest.raises(LabError, match="paused"):
        experiments.start(paused, "summary-drift", "E2", now=LATE)


def test_start_then_finish(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _clear_q3_comments(vault)
    started = experiments.start(vault, "summary-drift", "E2", now=LATE)
    assert started.status == "running"
    assert str(started.frontmatter["started"]) == "2026-11-21T13:30"
    assert "running · started 2026-11-21 13:30" in _line(vault, "E2")
    with pytest.raises(LabError, match="running"):
        experiments.start(vault, "summary-drift", "E2", now=LATE)

    done = experiments.finish(
        vault,
        "summary-drift",
        "E2",
        "The hash check agreed with the hand verdicts on most sampled pages.",
        "A section hash is a workable staleness signal for Q3.",
        "the section-hash check flagged every stale sample",
        now=LATE + dt.timedelta(minutes=20),
    )
    assert done.status == "done"
    assert done.problems == []
    assert done.sections["Result"] == "The hash check agreed with the hand verdicts on most sampled pages."
    assert done.sections["What it means"] == "A section hash is a workable staleness signal for Q3."
    assert done.sections["Hypothesis"].startswith("Hashing only")
    line = _line(vault, "E2")
    assert line.endswith("— the section-hash check flagged every stale sample")
    assert "· done · 2026-11-21" in line
    assert load_thread(vault, "summary-drift").problems == []


def test_finish_requires_running(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(LabError, match="E2 is planned, not running"):
        experiments.finish(vault, "summary-drift", "E2", "r", "m", "x", now=LATE)


def test_abandon_planned_and_running_but_not_done(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    exp = experiments.abandon(vault, "summary-drift", "E2", "the data moved", now=LATE)
    assert exp.status == "abandoned"
    assert _line(vault, "E2").endswith("— the data moved")
    with pytest.raises(LabError, match="only a planned or running"):
        experiments.abandon(vault, "summary-drift", "E1", "no", now=LATE)
    with pytest.raises(LabError, match="only a planned or running"):
        experiments.abandon(vault, "summary-drift", "E2", "again", now=LATE)


def test_veto_only_from_planned(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(LabError, match="E1 is done"):
        experiments.veto(vault, "summary-drift", "E1", "alex", now=LATE)
    exp = experiments.veto(vault, "summary-drift", "E2", "alex", now=LATE)
    assert exp.status == "vetoed"
    assert "· vetoed · 2026-11-21 — by alex" in _line(vault, "E2")
    first = parse_timeline(paths.timeline_path(vault, "summary-drift").read_text(encoding="utf-8"))[0]
    assert (first.stamp, first.tag, first.bullets) == ("2026-11-21 13:30", "owner", ["**Veto:** E2 by alex"])
    with pytest.raises(LabError, match="E2 is vetoed"):
        experiments.veto(vault, "summary-drift", "E2", "alex", now=LATE)
    with pytest.raises(LabError, match="vetoed, not planned"):
        experiments.start(vault, "summary-drift", "E2", now=LATE)


def test_veto_allowed_on_paused_not_on_closed(tmp_path: Path) -> None:
    paused = _vault(tmp_path / "a", "paused")
    assert experiments.veto(paused, "summary-drift", "E2", "alex", now=LATE).status == "vetoed"
    closed = _vault(tmp_path / "b", "closed")
    with pytest.raises(LabError, match="closed thread"):
        experiments.veto(closed, "summary-drift", "E2", "alex", now=LATE)


def test_failed_write_restores_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    before_front = _front(vault)
    real = ops.atomic_write_text
    calls = {"n": 0}

    def flaky(path: Path, text: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the front page, after the note was written
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(ops, "atomic_write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        experiments.plan(vault, "summary-drift", "x", "T", ["Q5"], BODY, now=PLAN_NOW)
    assert _front(vault) == before_front
    assert paths.experiment_note(vault, "summary-drift", "E3") is None
    assert parse_front_page("summary-drift", _front(vault)).frontmatter["next_e"] == 3

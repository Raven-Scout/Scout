from __future__ import annotations

import datetime as dt
import multiprocessing
import multiprocessing.synchronize
import shutil
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import ops, paths, writer
from scout.lab.model import Thread, TimelineEntry
from scout.lab.parser import parse_front_page, parse_timeline

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
NOW = dt.datetime(2026, 11, 22, 9, 30, tzinfo=dt.UTC)


def _vault(tmp_path: Path, status: str = "active") -> Path:
    dest = paths.thread_dir(tmp_path, "monitors")
    shutil.copytree(FIXTURES / "monitors", dest)
    front = paths.front_page(tmp_path, "monitors")
    text = front.read_text(encoding="utf-8").replace("status: active", f"status: {status}", 1)
    front.write_text(text, encoding="utf-8")
    return tmp_path


def _front(vault: Path) -> str:
    return paths.front_page(vault, "monitors").read_text(encoding="utf-8")


def test_stamp_format() -> None:
    assert ops.stamp(Path("."), NOW) == "2026-11-22 09:30"


def test_approve_proposed(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "proposed")
    thread = ops.owner_verb(vault, "monitors", "approve", "alex", now=NOW)
    assert thread.status == "active"
    t = parse_front_page("monitors", _front(vault))
    assert t.status == "active"
    assert str(t.frontmatter["approved"]) == "2026-11-22"
    assert t.problems == []
    first = parse_timeline(paths.timeline_path(vault, "monitors").read_text(encoding="utf-8"))[0]
    assert (first.stamp, first.tag, first.bullets) == ("2026-11-22 09:30", "owner", ["**Approve:** by alex"])


def test_pause_with_reason_and_missing_timeline_created(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    paths.timeline_path(vault, "monitors").unlink()
    ops.owner_verb(vault, "monitors", "pause", "alex", reason="away this week", now=NOW)
    entries = parse_timeline(paths.timeline_path(vault, "monitors").read_text(encoding="utf-8"))
    assert entries[0].bullets == ["**Pause:** by alex — away this week"]
    assert parse_front_page("monitors", _front(vault)).status == "paused"


def test_illegal_transition(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front_before = _front(vault)
    tl_before = paths.timeline_path(vault, "monitors").read_bytes()
    with pytest.raises(LabError, match="cannot resume an active thread"):
        ops.owner_verb(vault, "monitors", "resume", "alex", now=NOW)
    assert _front(vault) == front_before
    assert paths.timeline_path(vault, "monitors").read_bytes() == tl_before


def test_comment_on_paused_allowed_rejected_refused(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "paused")
    ops.comment(vault, "monitors", "thread", "alex", "still thinking", now=NOW)
    t = parse_front_page("monitors", _front(vault))
    assert t.comments[-1].text == "still thinking"
    assert t.comments[-1].stamp == "2026-11-22 09:30"

    rej = _vault(tmp_path / "r", "rejected")
    before = _front(rej)
    with pytest.raises(LabError, match="rejected"):
        ops.comment(rej, "monitors", "thread", "alex", "hello", now=NOW)
    with pytest.raises(LabError, match="rejected"):
        ops.reply(rej, "monitors", "thread", 1, "hi", now=NOW)
    assert _front(rej) == before


def test_reply_lands(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.reply(vault, "monitors", "Q3", 2, "Noted.", now=NOW)
    t = parse_front_page("monitors", _front(vault))
    assert t.open[0].comments[1].replies[0].is_scout


def test_write_refuses_on_broken_heading(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "monitors")
    front.write_text(_front(vault).replace("## Open questions", "## Questions to ask"), encoding="utf-8")
    before = front.read_bytes()
    with pytest.raises(LabError, match="heading"):
        ops.comment(vault, "monitors", "Q3", "alex", "hello", now=NOW)
    with pytest.raises(LabError, match="heading"):
        ops.owner_verb(vault, "monitors", "pause", "alex", now=NOW)
    assert front.read_bytes() == before


def test_unknown_status_line_does_not_block_comment(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "monitors")
    front.write_text(
        _front(vault).replace("· planned ·", "· wobbling ·"),
        encoding="utf-8",
    )
    ops.comment(vault, "monitors", "thread", "alex", "fine", now=NOW)
    assert "- alex (2026-11-22 09:30): fine" in _front(vault)


def test_mutate_failure_writes_nothing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "monitors")
    before = front.read_bytes()

    def boom(_: str) -> str:
        raise LabError("nope")

    with pytest.raises(LabError):
        ops.mutate(vault, front, boom)
    assert front.read_bytes() == before


def test_lock_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "LOCK_TIMEOUT_S", 0.2)
    with ops.lab_lock(tmp_path):
        # flock is per open-file-description, so a second acquisition in-process contends.
        with pytest.raises(LabError, match="lab write"), ops.lab_lock(tmp_path):
            pass


def test_require_active() -> None:
    t = parse_front_page("monitors", (FIXTURES / "monitors" / "monitors.md").read_text(encoding="utf-8"))
    ops.require_active(t)
    t.frontmatter["status"] = "paused"
    with pytest.raises(LabError, match="monitors is paused; research writes need an active thread"):
        ops.require_active(t)


def test_timeline_entry_type_used() -> None:
    assert TimelineEntry("2026-11-22 09:30", "owner", ["x"]).tag == "owner"


def test_owner_verb_on_fixture_timeline(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.owner_verb(vault, "monitors", "pause", "alex", now=NOW)
    text = paths.timeline_path(vault, "monitors").read_text(encoding="utf-8")
    assert text.startswith("# Monitors under adaptive attack \u2014 timeline\n")
    entries = parse_timeline(text)
    assert [e.stamp for e in entries] == ["2026-11-22 09:30", "2026-11-21 01:11", "2026-11-20 13:05"]


def test_owner_verb_front_write_failure_restores_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    front, tl = paths.front_page(vault, "monitors"), paths.timeline_path(vault, "monitors")
    real = ops.atomic_write_text

    def flaky(path: Path | str, text: str, **kw: object) -> None:
        if Path(path) == front:
            raise OSError("disk full")
        real(path, text)

    before = (front.read_bytes(), tl.read_bytes())
    monkeypatch.setattr(ops, "atomic_write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        ops.owner_verb(vault, "monitors", "pause", "alex", now=NOW)
    assert (front.read_bytes(), tl.read_bytes()) == before

    tl.unlink()
    with pytest.raises(OSError, match="disk full"):
        ops.owner_verb(vault, "monitors", "pause", "alex", now=NOW)
    assert not tl.exists()


def test_owner_verb_bad_timeline_names_file(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    paths.timeline_path(vault, "monitors").write_text("no heading here\n", encoding="utf-8")
    with pytest.raises(LabError, match="monitors: cannot update monitors-timeline.md"):
        ops.owner_verb(vault, "monitors", "pause", "alex", now=NOW)


# --- concurrency -----------------------------------------------------------------------

N = 150


def _comment_worker(vault: str, barrier: multiprocessing.synchronize.Barrier) -> None:
    barrier.wait(60)
    for i in range(N):
        ops.comment(Path(vault), "monitors", "thread", "alex", f"one {i}", now=NOW)


def _evidence_worker(vault: str, barrier: multiprocessing.synchronize.Barrier) -> None:
    front = paths.front_page(Path(vault), "monitors")
    barrier.wait(60)
    for i in range(N):
        ops.mutate(Path(vault), front, lambda t, i=i: writer.add_evidence(t, f"note-{i}", "why"))


def test_concurrent_writers_both_land(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [
        ctx.Process(target=_comment_worker, args=(str(vault), barrier)),
        ctx.Process(target=_evidence_worker, args=(str(vault), barrier)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(240)
        assert p.exitcode == 0
    t = parse_front_page("monitors", _front(vault))
    assert t.problems == []
    assert len([c for c in t.comments if c.text.startswith("one ")]) == N
    assert len([e for e in t.evidence if e.target.startswith("note-")]) == N


# --- proposals, run verbs, index --------------------------------------------------------


def _propose(vault: Path, slug: str = "probe-drift") -> Thread:
    return ops.propose(
        vault,
        slug,
        "Probe drift",
        "Do linear probes drift across checkpoints?",
        ["Does drift track data mix?"],
        [("probe-notes", "background")],
        now=NOW,
    )


def test_propose_creates_three_files_and_index(tmp_path: Path) -> None:
    assert _propose(tmp_path).status == "proposed"
    t = parse_front_page("probe-drift", paths.front_page(tmp_path, "probe-drift").read_text(encoding="utf-8"))
    assert t.status == "proposed"
    assert t.problems == []
    assert [q.qid for q in t.open] == ["Q1"]
    entries = parse_timeline(paths.timeline_path(tmp_path, "probe-drift").read_text(encoding="utf-8"))
    assert entries[0].tag == "proposal"
    assert entries[0].bullets == ["**Proposed:** Do linear probes drift across checkpoints?"]
    index = paths.index_path(tmp_path).read_text(encoding="utf-8")
    needs = index.split("## Needs you")[1].split("## Active")[0]
    assert "- [[probe-drift]] — Do linear probes drift across checkpoints?" in needs


def test_a_private_threads_timeline_says_it_is_private(tmp_path: Path) -> None:
    """I4: the timeline labels by its own frontmatter, so a private thread's timeline carries the visibility."""
    from scout.kb.privacy import classify, load_policy
    from scout.lab.lint import lab_findings

    ops.propose(tmp_path, "quiet", "Quiet", "Is it quiet?", ["Why?"], [], visibility="private", now=NOW)
    timeline = paths.timeline_path(tmp_path, "quiet")
    text = timeline.read_text(encoding="utf-8")
    assert text.startswith("---\nvisibility: private\n---\n# Quiet — timeline\n")
    assert parse_timeline(text)[0].tag == "proposal"
    rel = timeline.relative_to(tmp_path).as_posix()
    assert classify(rel, text, load_policy(tmp_path)).visibility == "private"
    assert lab_findings(tmp_path, rel, text) == []
    assert "quiet" not in paths.index_path(tmp_path).read_text(encoding="utf-8")
    # A timeline written before this rule gets the frontmatter on its next entry.
    timeline.write_text(text.split("---\n", 2)[2], encoding="utf-8")
    ops.owner_verb(tmp_path, "quiet", "approve", "alex", now=NOW)
    timeline.write_text(timeline.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")
    ops.timeline_add(tmp_path, "quiet", "owner", "read the notes", "still quiet", "none answered", now=NOW)
    again = timeline.read_text(encoding="utf-8")
    assert again.startswith("---\nvisibility: private\n---\n# Quiet — timeline\n")
    assert [e.tag for e in parse_timeline(again)] == ["owner", "owner", "proposal"]
    # A normal thread's timeline has no frontmatter.
    _propose(tmp_path)
    assert paths.timeline_path(tmp_path, "probe-drift").read_text(encoding="utf-8").startswith("# Probe drift")


def test_propose_refuses_sixth_open_proposal(tmp_path: Path) -> None:
    for i in range(5):
        _propose(tmp_path, f"idea-{i}")
    with pytest.raises(LabError, match="open proposals"):
        _propose(tmp_path, "idea-5")
    assert not paths.thread_dir(tmp_path, "idea-5").exists()
    ops.owner_verb(tmp_path, "idea-0", "approve", "alex", now=NOW)
    _propose(tmp_path, "idea-5")  # an approved thread no longer counts


def test_propose_refuses_bad_or_existing_slug(tmp_path: Path) -> None:
    with pytest.raises(LabError, match="slug"):
        _propose(tmp_path, "Bad Slug")
    _propose(tmp_path)
    before = paths.front_page(tmp_path, "probe-drift").read_bytes()
    with pytest.raises(LabError, match="already exists"):
        _propose(tmp_path)
    assert paths.front_page(tmp_path, "probe-drift").read_bytes() == before


def test_propose_failure_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = ops.atomic_write_text

    def flaky(path: Path | str, text: str, **kw: object) -> None:
        if Path(path) == paths.index_path(tmp_path):
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(ops, "atomic_write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        _propose(tmp_path)
    assert not paths.thread_dir(tmp_path, "probe-drift").exists()


def test_research_write_refused_on_paused(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "paused")
    before = _front(vault)
    with pytest.raises(LabError, match="paused"):
        ops.question_add(vault, "monitors", "Is this allowed?", now=NOW)
    assert _front(vault) == before
    ops.reply(vault, "monitors", "thread", 1, "ok", now=NOW)
    assert "scout (2026-11-22 09:30): ok" in _front(vault)


def test_run_verbs_refuse_damaged_headings(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "monitors")
    front.write_text(_front(vault).replace("## Evidence", "## Evidnce"), encoding="utf-8")
    before = front.read_bytes()
    with pytest.raises(LabError, match="damaged"):
        ops.understanding_set(vault, "monitors", "new")
    with pytest.raises(LabError, match="damaged"):
        ops.timeline_add(vault, "monitors", "research run", "d", "f", "q", now=NOW)
    assert front.read_bytes() == before


def test_question_add_answer_drop(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert ops.question_add(vault, "monitors", "What about  new\nlines?", by="scout", now=NOW) == "Q8"
    t = parse_front_page("monitors", _front(vault))
    assert t.frontmatter["next_q"] == 9
    assert [q.qid for q in t.open][-1] == "Q8"
    ops.question_answer(vault, "monitors", "Q5", "no, it does not", now=NOW)
    ops.question_drop(vault, "monitors", "Q8", "out of scope", now=NOW)
    t = parse_front_page("monitors", _front(vault))
    status = {q.qid: q.status for q in t.answered}
    assert (status["Q5"], status["Q8"]) == ("answered", "dropped")
    assert t.problems == []


def test_question_add_refuses_duplicate_id(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    front = paths.front_page(vault, "monitors")
    front.write_text(_front(vault).replace("next_q: 8", "next_q: 5"), encoding="utf-8")
    before = front.read_bytes()
    with pytest.raises(LabError, match="Q5"):
        ops.question_add(vault, "monitors", "dup?", now=NOW)
    assert front.read_bytes() == before


def test_understanding_and_evidence(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.understanding_set(vault, "monitors", "Short and new.")
    ops.evidence_add(vault, "monitors", "fresh-note", "because")
    t = parse_front_page("monitors", _front(vault))
    assert t.understanding == "Short and new."
    assert "fresh-note" in [e.target for e in t.evidence]


def test_timeline_research_run_sets_last_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.timeline_add(
        vault,
        "monitors",
        "research run",
        "read two papers",
        "nothing new",
        "Q3 still open",
        steers="alex on Q3 -> replied",
        evidence="[[fresh-note]]",
        now=NOW,
    )
    entries = parse_timeline(paths.timeline_path(vault, "monitors").read_text(encoding="utf-8"))
    assert (entries[0].stamp, entries[0].tag) == ("2026-11-22 09:30", "research run")
    assert entries[0].bullets == [
        "**Did:** read two papers",
        "**Found:** nothing new",
        "**Questions:** Q3 still open",
        "**Steers handled:** alex on Q3 -> replied",
        "Evidence: [[fresh-note]]",
    ]
    assert "last_run: 2026-11-22T09:30\n" in _front(vault)
    assert parse_front_page("monitors", _front(vault)).problems == []


def test_timeline_non_research_leaves_last_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.timeline_add(vault, "monitors", "experiment", "ran E1", "ok", "none", now=NOW)
    assert "last_run: 2026-11-21T01:11\n" in _front(vault)


def test_timeline_add_front_failure_restores_timeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    front, tl = paths.front_page(vault, "monitors"), paths.timeline_path(vault, "monitors")
    real = ops.atomic_write_text

    def flaky(path: Path | str, text: str, **kw: object) -> None:
        if Path(path) == front:
            raise OSError("disk full")
        real(path, text)

    before = (front.read_bytes(), tl.read_bytes())
    monkeypatch.setattr(ops, "atomic_write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        ops.timeline_add(vault, "monitors", "research run", "d", "f", "q", now=NOW)
    assert (front.read_bytes(), tl.read_bytes()) == before


def _extra_thread(vault: Path, slug: str, status: str) -> None:
    shutil.copytree(paths.thread_dir(vault, "monitors"), paths.thread_dir(vault, slug))
    folder = paths.thread_dir(vault, slug)
    for old in ("monitors.md", "monitors-timeline.md"):
        (folder / old).rename(folder / old.replace("monitors", slug, 1))
    front = paths.front_page(vault, slug)
    text = front.read_text(encoding="utf-8").replace("status: active", f"status: {status}", 1)
    front.write_text(text, encoding="utf-8")


def test_write_index_sections_and_keeps_before_the_lab(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _propose(vault)
    _extra_thread(vault, "slow-one", "paused")
    _extra_thread(vault, "done-one", "closed")
    _extra_thread(vault, "nope", "rejected")
    paths.thread_dir(vault, "no-front-page").mkdir()
    paths.index_path(vault).write_text(
        "# Lab\n\n## Active\n- stale\n\n## Before the lab\nOld notes, [[kept]].\n\n- one\n  - two\n\n## Other\nx\n",
        encoding="utf-8",
    )
    ops.write_index(vault)
    text = paths.index_path(vault).read_text(encoding="utf-8")
    sections = {s.split("\n", 1)[0].strip(): s for s in text.split("## ")[1:]}
    assert list(sections) == ["Needs you", "Active", "Paused", "Closed", "Before the lab"]
    assert "[[probe-drift]]" in sections["Needs you"]
    assert "[[monitors]] — Do behaviour monitors" in sections["Active"]
    assert "stale" not in text and "[[lab]]" not in text and "no-front-page" not in text
    assert "[[slow-one]]" in sections["Paused"]
    assert "[[done-one]]" in sections["Closed"] and "[[nope]]" in sections["Closed"]
    assert sections["Before the lab"] == "Before the lab\nOld notes, [[kept]].\n\n- one\n  - two\n"
    ops.write_index(vault)
    assert paths.index_path(vault).read_text(encoding="utf-8") == text


def test_owner_verb_updates_index(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "proposed")
    ops.owner_verb(vault, "monitors", "approve", "alex", now=NOW)
    text = paths.index_path(vault).read_text(encoding="utf-8")
    assert "[[monitors]]" in text.split("## Active")[1].split("## Paused")[0]


def test_propose_refuses_slug_with_trailing_newline(tmp_path: Path) -> None:
    with pytest.raises(LabError, match="slug"):
        _propose(tmp_path, "abc\n")
    assert not paths.lab_dir(tmp_path).exists() or not list(paths.lab_dir(tmp_path).iterdir())


def test_index_omits_private_threads_with_count(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ops.write_index(vault)
    assert "private thread" not in paths.index_path(vault).read_text(encoding="utf-8")
    ops.propose(vault, "secret-one", "Secret", "A hidden question?", [], [], visibility="private", now=NOW)
    text = paths.index_path(vault).read_text(encoding="utf-8")
    assert "secret-one" not in text and "hidden question" not in text
    assert "_1 private thread(s) not listed._" in text
    assert "[[monitors]]" in text

from __future__ import annotations

import datetime as dt
import multiprocessing
import shutil
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import ops, paths, writer
from scout.lab.model import TimelineEntry
from scout.lab.parser import parse_front_page, parse_timeline

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
NOW = dt.datetime(2026, 11, 22, 9, 30, tzinfo=dt.UTC)


def _vault(tmp_path: Path, status: str = "active") -> Path:
    dest = paths.thread_dir(tmp_path, "monitors")
    shutil.copytree(FIXTURES / "monitors", dest)
    front = paths.front_page(tmp_path, "monitors")
    text = front.read_text(encoding="utf-8").replace("status: active", f"status: {status}", 1)
    front.write_text(text, encoding="utf-8")
    paths.timeline_path(tmp_path, "monitors").write_text(
        writer.new_timeline("monitors", "Monitors under adaptive attack"), encoding="utf-8"
    )
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
    with pytest.raises(LabError, match="cannot resume a active thread"):
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


# --- concurrency -----------------------------------------------------------------------

N = 20


def _comment_worker(vault: str) -> None:
    for i in range(N):
        ops.comment(Path(vault), "monitors", "thread", "alex", f"one {i}", now=NOW)


def _evidence_worker(vault: str) -> None:
    front = paths.front_page(Path(vault), "monitors")
    for i in range(N):
        ops.mutate(Path(vault), front, lambda t, i=i: writer.add_evidence(t, f"note-{i}", "why"))


def test_concurrent_writers_both_land(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_comment_worker, args=(str(vault),)),
        ctx.Process(target=_evidence_worker, args=(str(vault),)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    t = parse_front_page("monitors", _front(vault))
    assert t.problems == []
    assert len([c for c in t.comments if c.text.startswith("one ")]) == N
    assert len([e for e in t.evidence if e.target.startswith("note-")]) == N

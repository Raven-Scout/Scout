from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import paths, summary
from scout.scripts import session_context_kb as kbs

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
BEFORE = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)
AFTER = dt.datetime(2026, 11, 21, 13, 30, tzinfo=dt.UTC)
REL = "knowledge-base/lab/monitors/monitors.md"


def _vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / "monitors", paths.thread_dir(tmp_path, "monitors"))
    return tmp_path


def _edit_front(vault: Path, old: str, new: str) -> None:
    front = paths.front_page(vault, "monitors")
    text = front.read_text(encoding="utf-8")
    assert old in text
    front.write_text(text.replace(old, new, 1), encoding="utf-8")


def _thread(doc: dict) -> dict:
    assert len(doc["threads"]) == 1
    return doc["threads"][0]


def test_list_counts_fixture(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    doc = summary.lab_list(vault, {REL: "normal"}, full=False, now=BEFORE)
    assert doc["schema_version"] == 1
    t = _thread(doc)
    assert t["slug"] == "monitors" and t["status"] == "active" and t["visibility"] == "normal"
    assert t["counts"] == {"open": 2, "answered": 1, "dropped": 1, "raised_7d": 2}
    assert t["unanswered_comments"] == 1
    assert t["experiments"]["planned"] == ["E2"] and t["experiments"]["running"] == []
    assert t["experiments"]["due"] == []
    assert t["last_run"] == "2026-11-21T01:11"
    assert t["problems"] == 0
    assert t["scout_replies"] == [
        {"target": "Q3", "stamp": "2026-11-21 01:11"},  # equal stamps keep document order
        {"target": "thread", "stamp": "2026-11-21 01:11"},
    ]
    assert doc["needs_you"] == {"proposals": 0, "close_proposals": 0}
    json.dumps(doc)  # plain json.dumps works: no dates or other Python objects


def test_due_depends_on_not_before_only(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    after = summary.lab_list(vault, {REL: "normal"}, full=False, now=AFTER)
    assert _thread(after)["experiments"]["due"] == ["E2"]  # the owner comment on Q3 does not change `due`


def test_raised_7d_window(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    late = dt.datetime(2026, 11, 28, 12, 0, tzinfo=dt.UTC)  # Q3 (11-20) is 8 days back, Q5 (11-21) is 7
    assert _thread(summary.lab_list(vault, {REL: "normal"}, full=False, now=late))["counts"]["raised_7d"] == 1


def test_private_thread_hidden_from_delegate(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _edit_front(vault, "visibility: normal", "visibility: private")
    assert summary.lab_list(vault, {}, full=False, now=BEFORE)["threads"] == []
    # visible in the labels map but private in its own frontmatter: still hidden
    assert summary.lab_list(vault, {REL: "normal"}, full=False, now=BEFORE)["threads"] == []
    full = summary.lab_list(vault, {}, full=True, now=BEFORE)
    assert _thread(full)["visibility"] == "private"


def test_thread_problems_surface(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _edit_front(vault, "## Evidence", "## Sources")
    t = _thread(summary.lab_list(vault, {REL: "normal"}, full=False, now=BEFORE))
    assert t["problems"] >= 1
    detail = summary.lab_thread(vault, "monitors", now=BEFORE)
    assert any("Evidence" in p["message"] for p in detail["problems"])
    assert set(detail["problems"][0]) == {"line", "message"}


def test_thread_ordering_and_needs_you(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    shutil.copytree(FIXTURES / "monitors", paths.thread_dir(vault, "aaa"))
    (paths.thread_dir(vault, "aaa") / "monitors.md").rename(paths.front_page(vault, "aaa"))
    (paths.thread_dir(vault, "aaa") / "monitors-timeline.md").unlink()
    _edit_front(vault, "status: active", "status: proposed")  # monitors becomes proposed
    front = paths.front_page(vault, "aaa")
    front.write_text(front.read_text("utf-8").replace("Monitors under", "Alpha under", 1), "utf-8")
    front.write_text(front.read_text("utf-8") + "- scout (2026-11-21 02:00): Proposing to close: done.\n", "utf-8")
    labels = {"knowledge-base/lab/aaa/aaa.md": "normal", REL: "normal"}
    doc = summary.lab_list(vault, labels, full=False, now=BEFORE)
    assert [t["slug"] for t in doc["threads"]] == ["monitors", "aaa"]  # proposed before active
    assert doc["needs_you"] == {"proposals": 1, "close_proposals": 1}


def test_lab_thread_detail(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    doc = summary.lab_thread(vault, "monitors", now=BEFORE)
    json.dumps(doc)
    assert doc["schema_version"] == 1 and doc["slug"] == "monitors" and doc["problems"] == []
    assert doc["frontmatter"]["created"] == "2026-11-20"
    assert [q["qid"] for q in doc["open"]] == ["Q3", "Q5"]
    assert doc["open"][0]["comments"][0]["replies"][0]["author"] == "scout"
    e2 = next(e for e in doc["experiments"] if e["eid"] == "E2")
    assert e2["frontmatter"]["not_before"] == "2026-11-21T13:00" or "2026-11-21" in str(e2["frontmatter"]["not_before"])
    assert len(doc["timeline"]) == 2 and doc["timeline"][0]["tag"] == "research run"
    assert doc["evidence"][0]["target"] == "sandbagging-and-monitor-evasion"


def test_unknown_slug_raises(tmp_path: Path) -> None:
    with pytest.raises(LabError):
        summary.lab_thread(tmp_path, "nope", now=BEFORE)


def test_session_context_has_lab_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    vault = _vault(tmp_path)
    (vault / "scout-config.yaml").write_text("schema_version: 1\nuser:\n  name: Sam Rivera\n", encoding="utf-8")
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])
    ctx = psd.gather("research", scout_dir=vault, now=dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.UTC))
    out = tmp_path / "session-context.json"
    psd.write_context(ctx, out)
    data = json.loads(out.read_text())
    assert data["lab"]["schema_version"] == 1
    assert [t["slug"] for t in data["lab"]["threads"]] == ["monitors"]
    assert list(data).index("lab") == list(data).index("research_queue") + 1


def test_lab_block_is_trimmed_closed_first_then_oldest_run() -> None:
    def row(slug: str, status: str, last_run: str | None) -> dict:
        return {"slug": slug, "status": status, "last_run": last_run, "title": "t" * 400}

    lab = {
        "schema_version": 1,
        "threads": [
            row("a", "active", "2026-11-20T01:00"),
            row("b", "active", "2026-11-01T01:00"),
            row("c", "closed", "2026-11-21T01:00"),
            row("d", "rejected", None),
        ],
        "needs_you": {"proposals": 0, "close_proposals": 0},
    }
    full = kbs._size(lab)
    out = kbs._trim_lab(lab, full - 100)
    assert [t["slug"] for t in out["threads"]] == ["a", "b", "c"] and out["truncated"]  # rejected went first
    out = kbs._trim_lab(lab, kbs._size({**lab, "threads": lab["threads"][:2], "truncated": True}))
    assert [t["slug"] for t in out["threads"]] == ["a", "b"]
    out = kbs._trim_lab(lab, kbs._size({**lab, "threads": lab["threads"][:1], "truncated": True}))
    assert [t["slug"] for t in out["threads"]] == ["a"]  # b has the oldest last_run
    assert out["needs_you"] == lab["needs_you"]
    assert kbs.LAB_MAX_BYTES == 16_000


def test_lab_block_respects_visibility(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert kbs.lab_block(vault, {}, full=False)["threads"] == []
    assert len(kbs.lab_block(vault, {}, full=True)["threads"]) == 1

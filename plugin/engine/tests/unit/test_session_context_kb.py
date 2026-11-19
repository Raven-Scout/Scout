"""The KB sections of session-context.json: project knowledge, people context, research queue, size budget."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from scout.kb.ontology import clear_frontmatter_cache
from scout.scripts import session_context_kb as kbs

DAY = dt.date(2026, 4, 15)
PROJECTS = "knowledge-base/projects"


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


FRONT = """---
name: widgets
type: project
status: "Active"
priority: "🔴"
as_of: 2026-04-10
---
# Widgets

## Where it stands
- Release slipped a week. [[widgets-release]]

## Go deeper
- **Release**: [[widgets-release]], how the release train works.
- **Decisions**: [[widgets-decisions]], the full record.

## History
Started in 2025.
"""


def _note(as_of: str | None) -> str:
    head = f"as_of: {as_of}\n" if as_of else ""
    return f"---\ndomain: widgets\n{head}---\n# Note\n"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    root = tmp_path / "vault"
    _write(root, "scout-config.yaml", "schema_version: 1\nuser:\n  name: Sam Rivera\n")
    _write(root, f"{PROJECTS}/widgets/widgets.md", FRONT)
    _write(root, f"{PROJECTS}/widgets/knowledge/widgets-release.md", _note("2026-04-12"))
    _write(root, f"{PROJECTS}/widgets/knowledge/widgets-decisions.md", _note("2026-03-01"))
    _write(root, f"{PROJECTS}/widgets/knowledge/widgets-notes.md", _note(None))
    _write(root, f"{PROJECTS}/legacy/legacy.md", "---\nname: legacy\ntype: project\nstatus: Done\npriority: low\n---\n")
    _write(root, f"{PROJECTS}/legacy/knowledge/legacy-why.md", _note("2025-01-01"))
    _write(
        root, f"{PROJECTS}/gateway/gateway.md", "---\nname: gateway\ntype: project\nstatus: active\n---\n# Gateway\n"
    )
    return root


def _labels(vault: Path, private: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        p.relative_to(vault).as_posix(): ("private" if p.relative_to(vault).as_posix() in private else "normal")
        for p in (vault / "knowledge-base").rglob("*.md")
    }


# ----- project knowledge -----------------------------------------------------------


def test_each_active_project_lists_its_go_deeper_map_and_notes(vault: Path) -> None:
    rows = {r["project"]: r for r in kbs.project_knowledge(vault, _labels(vault), full=True)}
    assert set(rows) == {"widgets", "gateway"}  # status Done is not active
    widgets = rows["widgets"]
    assert widgets["path"] == f"{PROJECTS}/widgets/widgets.md" and widgets["as_of"] == "2026-04-10"
    assert widgets["go_deeper"] == [
        "- **Release**: [[widgets-release]], how the release train works.",
        "- **Decisions**: [[widgets-decisions]], the full record.",
    ]
    assert [(n["path"].rsplit("/", 1)[1], n["as_of"]) for n in widgets["knowledge"]] == [
        ("widgets-release.md", "2026-04-12"),
        ("widgets-decisions.md", "2026-03-01"),
        ("widgets-notes.md", None),
    ]
    assert widgets["knowledge_total"] == 3
    assert rows["gateway"]["knowledge"] == [] and rows["gateway"]["go_deeper"] == []


def test_labels_carry_through_and_a_delegate_sees_only_its_paths(vault: Path) -> None:
    private = f"{PROJECTS}/widgets/knowledge/widgets-decisions.md"
    rows = kbs.project_knowledge(vault, _labels(vault, (private,)), full=True)
    notes = {n["path"]: n["visibility"] for r in rows for n in r["knowledge"]}
    assert notes[private] == "private"
    unlabelled = kbs.project_knowledge(vault, {}, full=True)
    assert {n["visibility"] for r in unlabelled for n in r["knowledge"]} == {"private"}  # fail closed
    shown = {f"{PROJECTS}/widgets/widgets.md": "normal", f"{PROJECTS}/widgets/knowledge/widgets-release.md": "normal"}
    delegate = kbs.project_knowledge(vault, shown, full=False)
    assert [r["project"] for r in delegate] == ["widgets"]
    assert [n["path"] for n in delegate[0]["knowledge"]] == [f"{PROJECTS}/widgets/knowledge/widgets-release.md"]


@pytest.mark.parametrize(
    ("status", "active"),
    [("Active", True), ("🟡 at risk", True), ("⚪ paused", False), ("Archived", False), ("on hold", False), ("", True)],
)
def test_is_active_project(status: str, active: bool) -> None:
    assert kbs.is_active_project({"status": status}) is active


def test_long_maps_are_capped(vault: Path) -> None:
    for i in range(kbs.KNOWLEDGE_MAX_NOTES + 5):
        _write(vault, f"{PROJECTS}/gateway/knowledge/n{i:02}.md", _note("2026-01-01"))
    gateway = next(r for r in kbs.project_knowledge(vault, _labels(vault), full=True) if r["project"] == "gateway")
    assert len(gateway["knowledge"]) == kbs.KNOWLEDGE_MAX_NOTES
    assert gateway["knowledge_total"] == kbs.KNOWLEDGE_MAX_NOTES + 5


# ----- people ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frontmatter", "head", "state"),
    [
        ({"status": "on paternity leave"}, "", "leave"),
        ({"status": "OOF"}, "", "leave"),
        ({"status": "back from vacation"}, "", None),
        ({"status": "active"}, "", None),
        ({"status": "departed"}, "", "departed"),
        ({"oof": {"from": "2026-04-10", "until": "2026-04-20"}}, "", "leave"),
        ({"oof": {"from": "2026-04-16", "until": "2026-04-20"}}, "", None),  # starts tomorrow
        ({"oof": {"from": "2026-04-01", "until": "2026-04-14"}}, "", None),  # ended yesterday
        ({"oof": {"from_approx": "2026-04-01", "status": "ended"}}, "", None),
        ({"leave": {"from": "2026-04-01"}}, "", "leave"),  # open-ended
        ({}, "---\nname: x\n---\n### 🚨 ACTIVE STATUS\nOut until further notice.\n", "active_status_block"),
        ({}, "---\nname: x\n---\n# Alex\nNothing special.\n", None),
        ({"status": "back from leave"}, "---\nname: x\n---\n### 🚨 ACTIVE STATUS\nOld block.\n", None),
    ],
)
def test_leave_state(frontmatter: dict, head: str, state: str | None) -> None:
    found = kbs.leave_state(frontmatter, head, DAY)
    assert (found or {}).get("state") == state


def test_a_dated_block_reports_its_dates() -> None:
    found = kbs.leave_state({"oof": {"from": "2026-04-10", "until": "2026-04-20"}}, "", DAY)
    assert found == {"state": "leave", "since": "2026-04-10", "until": "2026-04-20", "source": "oof"}


def _person(vault: Path, slug: str, name: str, extra: str = "", rels: str = "", body: str = "") -> None:
    relationships = f"relationships:\n{rels}" if rels else ""
    _write(
        vault, f"knowledge-base/people/{slug}.md", f"---\nname: {name}\ntype: person\n{extra}{relationships}---\n{body}"
    )


@pytest.fixture
def people_vault(vault: Path) -> Path:
    _person(
        vault,
        "sam-rivera",
        "Sam Rivera",
        "email: sam@example.com\n",
        '  - type: reports_to\n    target: "[[Priya Shah]]"\n',
    )
    _person(vault, "priya-shah", "Priya Shah", "role: Director\nteam: Platform\n")
    _person(
        vault,
        "alex-chen",
        "Alex Chen",
        "role: Engineer\nstatus: on leave\n",
        '  - type: managed_by\n    target: "[[priya-shah]]"\n',
    )
    _person(vault, "jo-park", "Jo Park", "role: Designer\n", body="### 🚨 ACTIVE STATUS\nOut this week.\n")
    _person(vault, "archive/old-hand", "Old Hand", "status: on leave\n")
    return vault


def _graph(vault: Path):
    from scout.kb.read import load_graph

    return load_graph(vault, "owner")


def test_people_context_lists_leave_relations_and_the_owners_managers(people_vault: Path) -> None:
    items = "- [ ] [#WIDG] Ask [[people/alex-chen|Alex]] about the release\n- 🟢 Waiting on Jo Park for mocks\n"
    ctx = kbs.people_context(_graph(people_vault), DAY, items_text=items, owner_keys=["Sam Rivera"])
    on_leave = {r["name"]: r["state"] for r in ctx["on_leave"]}
    assert on_leave == {"Alex Chen": "leave", "Jo Park": "active_status_block"}  # archive/ is left out
    relevant = {r["name"]: r for r in ctx["relevant"]}
    assert set(relevant) == {"Alex Chen", "Jo Park"}
    assert relevant["Alex Chen"]["managers"] == ["Priya Shah"] and relevant["Alex Chen"]["leave"] == "leave"
    assert relevant["Alex Chen"]["role"] == "Engineer" and relevant["Alex Chen"]["path"].endswith("alex-chen.md")
    assert relevant["Jo Park"]["leave"] == "active_status_block"
    assert [m["name"] for m in ctx["owner_managers"]] == ["Priya Shah"]
    assert ctx["relevant_total"] == 2


def test_the_owner_is_found_by_email_too(people_vault: Path) -> None:
    ctx = kbs.people_context(_graph(people_vault), DAY, owner_keys=["", "sam@example.com"])
    assert [m["name"] for m in ctx["owner_managers"]] == ["Priya Shah"]
    assert ctx["relevant"] == []


def test_people_context_without_a_graph_is_empty() -> None:
    assert kbs.people_context(None, DAY) == {"on_leave": [], "relevant": [], "relevant_total": 0, "owner_managers": []}


def test_mentioned_people_needs_a_link_or_the_full_name() -> None:
    people = {
        "Alex Chen": {"_label": {"path": "knowledge-base/people/alex-chen.md"}, "aliases": ["AC"]},
        "Priya Shah": {"_label": {"path": "knowledge-base/people/priya-shah.md"}},
    }
    assert kbs.mentioned_people("Alex said hi; Priya too", people) == []
    assert kbs.mentioned_people("[[alex-chen]] and Priya Shah", people) == ["Alex Chen", "Priya Shah"]
    assert kbs.mentioned_people("[[AC]]", people) == ["Alex Chen"]


def test_items_text_falls_back_to_the_newest_earlier_daily_file(tmp_path: Path) -> None:
    _write(tmp_path, "action-items/action-items-2026-04-13.md", "older")
    _write(tmp_path, "action-items/action-items-2026-04-14.md", "yesterday")
    _write(tmp_path, "action-items/action-items-2026-04-16.md", "tomorrow")
    assert kbs.items_text_for(tmp_path, DAY) == "yesterday"
    _write(tmp_path, "action-items/action-items-2026-04-15.md", "today")
    assert kbs.items_text_for(tmp_path, DAY) == "today"
    assert kbs.items_text_for(tmp_path / "none", DAY) == ""


# ----- research queue ----------------------------------------------------------------


def _entry(vault: Path, slug: str, front: str) -> None:
    _write(vault, f"knowledge-base/research-queue/{slug}.md", f"---\n{front}---\n# Brief\n")


def test_the_research_queue_keeps_open_entries_urgent_first(vault: Path) -> None:
    _entry(vault, "2026-04-01-a", "title: Low one\nstatus: open\npriority: low\ndate: 2026-04-01\n")
    _entry(vault, "2026-04-02-b", "title: Urgent one\nstatus: open\npriority: urgent\ndate: 2026-04-02\n")
    _entry(vault, "2026-04-03-c", "title: Doing\nstatus: in-progress\npriority: medium\ndate: 2026-04-03\n")
    _entry(vault, "2026-04-04-d", "title: Finished\nstatus: done\n")
    _entry(vault, "2026-04-05-e", "title: Gone\nstatus: dropped\n")
    _entry(vault, "2026-04-06-f", "title: No status\n")
    queue = kbs.research_queue(vault, _labels(vault), full=True)
    assert [r["id"] for r in queue["open"]] == ["2026-04-02-b", "2026-04-03-c", "2026-04-01-a", "2026-04-06-f"]
    assert queue["open"][0] == {
        "id": "2026-04-02-b",
        "path": "knowledge-base/research-queue/2026-04-02-b.md",
        "status": "open",
        "priority": "urgent",
        "title": "Urgent one",
        "area": None,
        "date": "2026-04-02",
        "visibility": "normal",
    }
    assert queue["counts"] == {"done": 1, "dropped": 1, "in-progress": 1, "none": 1, "open": 2}
    assert queue["open_total"] == 4


def test_the_research_queue_respects_the_readers_paths(vault: Path) -> None:
    _entry(vault, "a", "title: Shown\nstatus: open\n")
    _entry(vault, "b", "title: Hidden\nstatus: open\n")
    queue = kbs.research_queue(vault, {"knowledge-base/research-queue/a.md": "normal"}, full=False)
    assert [r["id"] for r in queue["open"]] == ["a"]
    assert kbs.research_queue(vault / "missing", {}, full=True) == {"open": [], "open_total": 0, "counts": {}}


# ----- budget ------------------------------------------------------------------------


def _size(value: object) -> int:
    return len(json.dumps(value, indent=2, default=str).encode("utf-8"))


def _big_sections() -> dict:
    note = {
        "path": "knowledge-base/projects/p/knowledge/" + "n" * 60 + ".md",
        "as_of": "2026-01-01",
        "visibility": "normal",
    }
    projects = [
        {
            "project": f"p{i}",
            "path": "x",
            "visibility": "normal",
            "as_of": None,
            "go_deeper": ["- " + "g" * 190] * 25,
            "go_deeper_total": 25,
            "knowledge": [note] * 40,
            "knowledge_total": 40,
        }
        for i in range(30)
    ]
    people = {
        "on_leave": [{"name": "n" * 30, "path": "p" * 60, "state": "leave"}] * 300,
        "relevant": [{"name": "r" * 40}] * 400,
        "relevant_total": 400,
        "owner_managers": [],
    }
    queue = {"open": [{"id": "i" * 40, "title": "t" * 140}] * 300, "open_total": 300, "counts": {"open": 300}}
    return {"project_knowledge": projects, "people_context": people, "research_queue": queue}


def test_each_section_is_kept_within_its_budget() -> None:
    data = {"generated_at": "x", "kb_file_dates": {"a.md": {"last_updated": "y"}}, **_big_sections()}
    out = kbs.fit_budget(data)
    assert _size(out["project_knowledge"]) <= kbs.PROJECT_KNOWLEDGE_MAX_BYTES
    assert _size(out["people_context"]) <= kbs.PEOPLE_CONTEXT_MAX_BYTES
    assert _size(out["research_queue"]) <= kbs.RESEARCH_QUEUE_MAX_BYTES
    assert out["project_knowledge"] and all(r.get("truncated") for r in out["project_knowledge"])
    assert out["research_queue"]["truncated"] and out["people_context"]["truncated"]
    assert out["kb_file_dates"] == data["kb_file_dates"]
    assert _size(out) <= kbs.SESSION_CONTEXT_MAX_BYTES


def test_a_large_file_shrinks_only_the_new_sections() -> None:
    dates = {f"knowledge-base/n{i:05}.md": {"last_updated": "2026-01-01", "visibility": "normal"} for i in range(2600)}
    data = {"kb_file_dates": dates, **_big_sections()}
    assert _size(dates) < kbs.SESSION_CONTEXT_MAX_BYTES < _size(kbs.fit_budget({**_big_sections()})) + _size(dates)
    out = kbs.fit_budget(data)
    assert out["kb_file_dates"] == dates
    assert _size(out) <= kbs.SESSION_CONTEXT_MAX_BYTES


def test_a_small_context_is_unchanged() -> None:
    data = {"project_knowledge": [{"project": "p", "go_deeper": ["- a"], "knowledge": []}], "people_context": {}}
    assert kbs.fit_budget(data) == data


# ----- gather ------------------------------------------------------------------------


def test_gather_and_write_carry_the_sections_near_the_top(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.scripts import pre_session_data as psd

    _entry(vault, "2026-04-02-b", "title: Urgent one\nstatus: open\npriority: urgent\n")
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])
    ctx = psd.gather("research", scout_dir=vault, now=dt.datetime(2026, 4, 15, 9, 0, tzinfo=dt.UTC))
    assert {r["project"] for r in ctx.project_knowledge} == {"widgets", "gateway"}
    assert [r["id"] for r in ctx.research_queue["open"]] == ["2026-04-02-b"]
    assert set(ctx.people_context) >= {"on_leave", "relevant", "owner_managers"}
    out = tmp_path / "session-context.json"
    psd.write_context(ctx, out)
    keys = list(json.loads(out.read_text()))
    assert keys.index("project_knowledge") < keys.index("kb_file_dates")
    assert keys.index("research_queue") < keys.index("git_recent")


def test_a_reader_that_sees_nothing_gets_no_kb_sections(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    monkeypatch.setenv("SCOUT_READER", "agent:person:priya@example.com")
    ctx = psd.gather("research", scout_dir=vault)
    assert ctx.project_knowledge == [] and ctx.people_context == {} and ctx.research_queue == {}


def test_a_section_that_fails_is_empty(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(kbs, "project_knowledge", broken)
    monkeypatch.setattr(kbs, "research_queue", broken)
    projects, people, queue = psd.get_kb_sections(vault, "owner", {}, None, DAY)
    assert projects == [] and queue == {} and people["on_leave"] == []

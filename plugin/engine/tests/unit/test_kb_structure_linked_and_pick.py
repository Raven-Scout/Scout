"""`kb structure`: staleness counts source and topic notes that link to a project, and the nightly
pick goes by priority × staleness and leaves pages over `nightly_max_bytes` for an interactive
session, so one big page can't take night after night."""

from __future__ import annotations

import datetime as dt

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.config import today
from scout.kb.structure import cmd_structure, next_target, pick_score, structure_rows

runner = CliRunner()
P = "knowledge-base/projects"
SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]


def page(name: str, *, priority: str = "🟢", as_of: str | None = "2026-10-01", extra: str = "", fm: str = "") -> str:
    head = f'name: {name}\ntype: project\npriority: "{priority} P2"\n' + (f"as_of: {as_of}\n" if as_of else "") + fm
    body = "".join(f"## {s}\nNone right now.\n\n" for s in SECTIONS)
    return f"---\n{head}---\n# {name.title()}\n\n{body}{extra}"


def commit_at(kb_repo, day: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"{day}T12:00:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", f"{day}T12:00:00+00:00")
    kb_repo.git("add", "-A")
    kb_repo.commit(f"c {day}")


def rows(kb_repo) -> dict[str, dict]:
    return {r["project"]: r for r in structure_rows(kb_repo.root)}


def picked(kb_repo) -> str | None:
    target = next_target(structure_rows(kb_repo.root))
    return target["project"] if target else None


# ----- staleness through linked notes ----------------------------------------------------


def test_a_source_note_linking_to_the_project_after_as_of_makes_it_stale(kb_repo, monkeypatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "---\nkind: meeting\n---\nSee [[widgets]].\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    r = rows(kb_repo)["widgets"]
    assert r["stale"] and r["linked_last_change"] == "2026-10-09" and r["folder_last_change"] is None
    assert r["problems"] == ["stale (as_of 2026-10-01, linked note changed 2026-10-09)"]


@pytest.mark.parametrize(
    "link",
    [
        "[[widgets|the widgets project]]",
        "[[Widgets#Where it stands]]",
        "[[projects/widgets/widgets]]",
        "[[knowledge-base/projects/widgets/knowledge/widgets-pricing]]",
        "[[Widget Platform]]",  # an alias on the front page
    ],
)
def test_every_way_of_linking_counts(kb_repo, monkeypatch, link: str) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", fm="aliases: [Widget Platform]\n"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/topics/infra/infra-costs.md", f"# Costs\n\nPer {link}.\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert rows(kb_repo)["widgets"]["stale"]


@pytest.mark.parametrize(
    "text",
    [
        "Mentions widgets in plain text only.\n",
        "See [[topics/infra/widgets]], a topic that shares the name.\n",
        "See [[gadgets]].\n",
    ],
)
def test_a_note_that_doesnt_link_to_the_project_does_not_count(kb_repo, monkeypatch, text: str) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of="2026-10-20"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", text)
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert not rows(kb_repo)["widgets"]["stale"]


def test_a_linked_change_within_stale_days_is_not_stale(kb_repo, monkeypatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-03-sync.md", "[[widgets]]\n")
    commit_at(kb_repo, "2026-10-03", monkeypatch)
    r = rows(kb_repo)["widgets"]
    assert not r["stale"] and r["linked_last_change"] == "2026-10-03"


def test_a_private_linking_note_counts_without_showing_its_date(kb_repo, monkeypatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "---\nvisibility: private\n---\n[[widgets]]\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    r = rows(kb_repo)["widgets"]
    assert r["stale"] and r["linked_last_change"] is None
    assert r["problems"] == ["stale (as_of 2026-10-01, a linked note changed)"]


def test_an_excluded_linking_note_does_not_count(kb_repo, monkeypatch) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  exclude_globs: ['knowledge-base/sources/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "[[widgets]]\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert not rows(kb_repo)["widgets"]["stale"]


def test_linked_dirs_is_a_setting(kb_repo, monkeypatch) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  linked_dirs: [knowledge-base/topics]\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "[[widgets]]\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert not rows(kb_repo)["widgets"]["stale"]


def test_linked_history_is_one_git_call_bounded_by_the_oldest_as_of(kb_repo, monkeypatch) -> None:
    import subprocess

    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of="2026-09-15"))
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "[[widgets]]\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        if cmd[:2] == ["git", "log"]:
            calls.append(list(cmd))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    structure_rows(kb_repo.root)
    linked = [c for c in calls if "knowledge-base/sources" in c]
    assert len(linked) == 1 and "--since=2026-09-15" in linked[0]


# ----- the pick: priority × staleness ----------------------------------------------------


def test_pick_score_is_priority_times_days_behind() -> None:
    day = dt.date(2026, 11, 19)
    assert pick_score("🔴", dt.date(2026, 11, 12), day) == 4 * 8
    assert pick_score("🟢", dt.date(2026, 10, 29), day) == 2 * 22
    assert pick_score("", dt.date(2026, 11, 19), day) == 1
    assert pick_score("🟡", None, day) == 3 * 366
    assert pick_score("🟡", dt.date(2020, 1, 1), day) == pick_score("🟡", None, day)  # capped


def test_a_red_page_a_week_behind_beats_a_green_page_two_weeks_behind(kb_repo) -> None:
    day = today(kb_repo.root)
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=str(day - dt.timedelta(days=14))))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", as_of=str(day - dt.timedelta(days=7))))
    for name in ("widgets", "gadgets"):
        kb_repo.write(f"{P}/{name}/notes.md", "a loose file\n")
    assert picked(kb_repo) == "gadgets"


def test_a_stale_low_priority_page_beats_a_fresh_one_of_the_same_priority(kb_repo) -> None:
    day = today(kb_repo.root)
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=str(day - dt.timedelta(days=2))))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=str(day - dt.timedelta(days=40))))
    for name in ("widgets", "gadgets"):
        kb_repo.write(f"{P}/{name}/notes.md", "a loose file\n")
    assert picked(kb_repo) == "gadgets"


def test_a_page_with_no_priority_waits_behind_a_green_one(kb_repo) -> None:
    day = str(today(kb_repo.root) - dt.timedelta(days=10))
    kb_repo.write(f"{P}/skills-howto/skills-howto.md", page("skills-howto", priority="", as_of=day))
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=day))
    for name in ("skills-howto", "widgets"):
        kb_repo.write(f"{P}/{name}/notes.md", "a loose file\n")
    assert picked(kb_repo) == "widgets"


# ----- pages too big for a night -----------------------------------------------------------


def rebuild_page(name: str, **kwargs) -> str:
    """A 13 KB page missing a required section: it needs a full rebuild, not a refresh."""
    return page(name, extra="x" * 13000, **kwargs).replace("## History", "### History")


def test_a_full_rebuild_over_the_nightly_cap_is_left_for_an_interactive_session(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", rebuild_page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🟢", as_of=None))
    r = rows(kb_repo)
    assert r["widgets"]["interactive"] and not r["widgets"]["rebuild_target"] and not r["widgets"]["resume"]
    assert not r["gadgets"]["interactive"]
    assert picked(kb_repo) == "gadgets"


def test_a_page_over_budget_needs_a_full_rebuild_too(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", extra="x" * 17000))
    assert rows(kb_repo)["widgets"]["interactive"]


@pytest.mark.parametrize("problem", ["no as_of", "stale", "narration", "a loose file"])
def test_a_big_page_that_only_needs_a_refresh_keeps_its_nightly_refresh(kb_repo, monkeypatch, problem: str) -> None:
    extra = "x" * 13000 + ("\nFinding 3 holds.\n" if problem == "narration" else "")
    kb_repo.write(
        f"{P}/widgets/widgets.md", page("widgets", as_of=None if problem == "no as_of" else "2026-10-01", extra=extra)
    )
    if problem == "a loose file":
        kb_repo.write(f"{P}/widgets/notes.md", "a note\n")
    if problem == "stale":
        commit_at(kb_repo, "2026-10-01", monkeypatch)
        kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "[[widgets]]\n")
        commit_at(kb_repo, "2026-10-09", monkeypatch)
    r = rows(kb_repo)["widgets"]
    assert not r["conforms"] and not r["interactive"] and r["rebuild_target"]
    assert picked(kb_repo) == "widgets"


def test_the_nightly_cap_is_a_setting(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  nightly_max_bytes: 40000\n")
    kb_repo.write(f"{P}/widgets/widgets.md", rebuild_page("widgets", as_of=None))
    r = rows(kb_repo)["widgets"]
    assert not r["interactive"] and r["rebuild_target"]


def test_a_half_rebuilt_page_over_the_cap_finishes(kb_repo) -> None:
    day = today(kb_repo.root)
    kb_repo.write(f"{P}/widgets/widgets.md", rebuild_page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/archive/widgets-{day - dt.timedelta(days=3)}-pre-restructure.md", "old\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    r = rows(kb_repo)["widgets"]
    assert r["resume"] and not r["interactive"]
    assert picked(kb_repo) == "widgets"


def _shared(kb_repo, edits: str = "") -> None:
    kb_repo.write(
        "scout-config.yaml",
        f"privacy:\n{edits}  federation: {{enabled: true}}\n  federation_defaults:\n"
        f"    - {{glob: '{P}/widgets/**', scope: ['person:alex@example.com']}}\n",
    )


def test_a_shared_page_over_the_cap_may_still_be_trimmed(kb_repo) -> None:
    # `shared_edits: refuse`: the pass may only cut lines from a shared page, and a trim has no size limit.
    _shared(kb_repo, "  shared_edits: refuse\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", extra="x" * 17000))
    r = rows(kb_repo)["widgets"]
    assert r["trim_only"] and r["rebuild_target"] and not r["interactive"]
    assert picked(kb_repo) == "widgets"


def test_a_shared_page_a_full_rebuild_would_take_waits_for_a_session(kb_repo) -> None:
    # `checked` (the default): a shared page is rebuilt like any other, so the size limit applies.
    _shared(kb_repo)
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", extra="x" * 17000))
    r = rows(kb_repo)["widgets"]
    assert r["interactive"] and not r["trim_only"] and not r["rebuild_target"]


def test_next_names_the_pages_left_for_an_interactive_session(kb_repo, monkeypatch, capsys) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", rebuild_page("widgets", as_of=None))
    commit_at(kb_repo, str(today(kb_repo.root) - dt.timedelta(days=9)), monkeypatch)
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    kb_repo.write(f"{P}/secret/secret.md", rebuild_page("secret", as_of=None, fm="visibility: private\n"))
    assert cmd_structure(kb_repo.root, "owner", next_only=True).lines[0] == "gadgets"
    err = capsys.readouterr().err
    assert f"{P}/widgets/widgets.md (waiting 9 days)" in err and "interactive session" in err and "over 12 KB" in err
    assert "secret" not in err  # a private page is never picked, so it isn't named either
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).stdout
    assert any("widgets" in line and "[interactive]" in line for line in table.splitlines())


def test_next_is_silent_when_no_page_is_too_big(kb_repo, capsys) -> None:
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    assert cmd_structure(kb_repo.root, "owner", next_only=True).lines[0] == "gadgets"
    assert capsys.readouterr().err == ""


def test_doctor_notes_the_pages_waiting_for_an_interactive_rebuild(kb_repo, monkeypatch) -> None:
    from scout.scripts.bootstrap_doctor import _check_kb_interactive

    assert _check_kb_interactive(vault=kb_repo.root) == []
    kb_repo.write(f"{P}/widgets/widgets.md", rebuild_page("widgets", as_of=None))
    commit_at(kb_repo, str(today(kb_repo.root) - dt.timedelta(days=4)), monkeypatch)
    [note] = _check_kb_interactive(vault=kb_repo.root)
    assert (
        note.startswith("kb: 1 project page waits for an interactive rebuild")
        and f"{P}/widgets/widgets.md (4 d)" in note
    )


def test_an_as_of_that_cant_be_read_is_reported_as_such(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-11"))
    assert "unreadable as_of" in rows(kb_repo)["widgets"]["problems"]
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert "no as_of" in rows(kb_repo)["widgets"]["problems"]


def test_the_linked_walk_stops_at_the_window(kb_repo, monkeypatch) -> None:
    import subprocess

    from scout.kb.structure import LINKED_WINDOW_DAYS

    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2020-01-01"))
    kb_repo.write("knowledge-base/sources/2026-10/2026-10-09-sync.md", "[[widgets]]\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        if cmd[:2] == ["git", "log"]:
            calls.append(list(cmd))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    structure_rows(kb_repo.root)
    [linked] = [c for c in calls if "knowledge-base/sources" in c]
    assert f"--since={today(kb_repo.root) - dt.timedelta(days=LINKED_WINDOW_DAYS)}" in linked


def test_an_over_budget_page_mid_shrink_keeps_its_nights(kb_repo) -> None:
    """All sections present, over budget, an archive copy from three nights ago: a multi-night shrink
    under way finishes instead of going [interactive] on its second night."""
    day = today(kb_repo.root)
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", extra="x" * 17000))
    kb_repo.write(f"{P}/widgets/archive/widgets-{day - dt.timedelta(days=3)}-pre-restructure.md", "old\n")
    r = rows(kb_repo)["widgets"]
    assert r["over_budget"] and r["missing_count"] == 0
    assert not r["interactive"] and r["rebuild_target"] and not r["resume"]
    assert picked(kb_repo) == "widgets"


# ----- edges -------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../outside", "/"])
def test_linked_dirs_must_stay_inside_the_vault(kb_repo, bad: str) -> None:
    from scout.errors import KBError
    from scout.kb.structure import load_structure_config

    kb_repo.write("scout-config.yaml", f"kb_structure:\n  linked_dirs: ['{bad}']\n")
    with pytest.raises(KBError):
        load_structure_config(kb_repo.root)


def test_newest_changes_of_no_paths_is_empty(kb_repo) -> None:
    from scout.kb.structure import newest_changes

    assert newest_changes(kb_repo.root, [], dt.date(2026, 10, 10)) == {}


def test_a_linking_note_deleted_since_and_an_empty_link_do_not_count(kb_repo, monkeypatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    gone = "knowledge-base/sources/2026-10/2026-10-09-sync.md"
    kb_repo.write(gone, "See [[widgets]].\n")
    kb_repo.write("knowledge-base/topics/infra/infra.md", "See [[/]] and [[ ]].\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    (kb_repo.root / gone).unlink()  # still in the history, gone from disk
    r = rows(kb_repo)["widgets"]
    assert not r["stale"] and r["linked_last_change"] is None

"""Tests for `kb structure --next` on a shared front page.

With ``privacy.shared_edits: refuse`` the nightly pass may only cut lines from one (trim-only). With
``checked``, the default, it rebuilds one like any other page, unless a note the rebuild could write
would be shared more widely than the page; then it falls back to trim-only."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.kb.lint_privacy import privacy_staged
from scout.kb.structure import cmd_structure, next_target, structure_rows
from tests.unit.test_kb_structure_next import PRIVATE_NOTE, P, archive_copy, old_shape_page, page

runner = CliRunner()
RUN = {"SCOUT_MODE": "dreaming-nightly"}
FRONT = f"{P}/widgets/widgets.md"
ALEX = "['person:alex@example.com']"


REFUSE = "  shared_edits: refuse\n"


def federated(*defaults: str, scope: str = ALEX, edits: str = REFUSE) -> str:
    """Folder shares; ``refuse`` unless ``edits=""`` (the default mode, ``checked``)."""
    rows = "".join(f"    - {{glob: '{g}', scope: {scope}}}\n" for g in defaults)
    return f"privacy:\n  federation: {{enabled: true}}\n{edits}  federation_defaults:\n{rows}"


def rows_by_name(root) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    return {r["project"]: r for r in structure_rows(root)}


def picked(root) -> str | None:  # type: ignore[no-untyped-def]
    target = next_target(structure_rows(root))
    return target["project"] if target else None


def test_a_shared_front_page_over_budget_is_picked_trim_only(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["trim_only"] and row["rebuild_target"]
    assert not row["share_blocked"] and not row["resume"]
    out = cmd_structure(kb_repo.root, "owner", next_only=True)
    assert out.lines == ["widgets", "trim-only", f"commit: nightly KB pass — {FRONT}"]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) == {
        "project": "widgets",
        "path": FRONT,
        "commit": f"nightly KB pass — {FRONT}",
        "mode": "trim",
        "move_to": [],
    }


@pytest.mark.parametrize(
    ("config", "frontmatter"),
    [
        pytest.param(f"privacy:\n  shared_files_add: ['{FRONT}']\n", "", id="shared by path"),
        pytest.param(
            "privacy:\n  federation: {enabled: false}\n", f"federation_scope: {ALEX}\n", id="declared, sharing off"
        ),
    ],
)
def test_a_page_shared_without_an_effective_scope_is_still_skipped(kb_repo, config: str, frontmatter: str) -> None:
    """The hook's shared-note check (#569) only guards an effective ``federation_scope``, so these get no trim,
    and no rebuild in the default ``checked`` mode either (these configs don't set ``shared_edits``)."""
    kb_repo.write("scout-config.yaml", config)
    kb_repo.write(FRONT, page("widgets", frontmatter=frontmatter, extra="x" * 17000))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["trim_only"] and not row["rebuild_target"]
    assert picked(kb_repo.root) is None


def test_narration_on_a_shared_page_is_trimmed(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", extra="Last verified today.\n"))
    assert rows_by_name(kb_repo.root)["widgets"]["trim_only"]
    assert picked(kb_repo.root) == "widgets"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"as_of": None}, id="no as_of"),
        pytest.param({"extra": "x" * 9000}, id="over target, under budget"),
    ],
)
def test_a_shared_page_that_cutting_cant_fix_is_still_skipped(kb_repo, kwargs: dict) -> None:
    """Adding a date or a section would add to a shared note, so only size and narration count."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", **kwargs).replace("## History", "## Old notes"))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["conforms"] and not row["trim_only"] and not row["rebuild_target"]
    assert picked(kb_repo.root) is None
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert "[--next skips]" in table


@pytest.mark.parametrize(
    ("config", "frontmatter"),
    [
        pytest.param(federated(f"{P}/widgets/**"), "visibility: private\n", id="private"),
        pytest.param(federated(f"{P}/widgets/**") + f"  local_only_add: ['{P}/widgets/**']\n", "", id="local-only"),
    ],
)
def test_a_private_or_local_only_page_is_never_trimmed(kb_repo, config: str, frontmatter: str) -> None:
    kb_repo.write("scout-config.yaml", config)
    kb_repo.write(FRONT, page("widgets", frontmatter=frontmatter, extra="x" * 17000))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["trim_only"] and not row["rebuild_target"]
    assert picked(kb_repo.root) is None


def test_an_unshared_page_is_rebuilt_not_trimmed(kb_repo) -> None:
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["rebuild_target"] and not row["trim_only"]
    assert cmd_structure(kb_repo.root, "owner", next_only=True).lines == [
        "widgets",
        f"commit: nightly KB pass — {FRONT}",
    ]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) == {"project": "widgets", "path": FRONT, "commit": f"nightly KB pass — {FRONT}"}


def test_shared_source_notes_do_not_stop_a_trim(kb_repo) -> None:
    """A trim writes no new note, so the shares that stop a rebuild don't apply."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", "knowledge-base/sources/**"))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert rows["gadgets"]["share_blocked"] and not rows["widgets"]["share_blocked"]
    assert picked(kb_repo.root) == "widgets"


def test_a_trim_waits_behind_a_page_that_needs_a_rebuild(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, old_shape_page("widgets", priority="🔴"))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🟢", as_of=None))
    assert picked(kb_repo.root) == "gadgets"


def test_a_trim_waits_behind_an_unshared_page_that_only_needs_shrinking(kb_repo) -> None:
    """A trim that finds nothing to cut must not hold the pick ahead of shrinks it could leave to other nights."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", priority="🔴", extra="x" * 20000))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🟢", extra="Last verified today.\n"))
    assert [r["project"] for r in structure_rows(kb_repo.root)][:2] == ["gadgets", "widgets"]
    assert picked(kb_repo.root) == "gadgets"


def test_a_trim_is_never_a_resumed_rebuild(kb_repo) -> None:
    """An archive copy from before the folder was shared doesn't put the page first."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, old_shape_page("widgets"))
    kb_repo.write(f"{P}/widgets/archive/{archive_copy('widgets', kb_repo.root, 3)}", "the old page\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["trim_only"] and not row["resume"]
    assert picked(kb_repo.root) == "gadgets"


def test_the_table_marks_a_trim(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    line = next(line for line in table.splitlines() if "widgets" in line)
    assert "[trim-only]" in line and "[--next skips]" not in line


# ----- where a trim may move detail ----------------------------------------------------


def test_a_trim_moves_detail_only_into_existing_narrower_notes(kb_repo) -> None:
    """Unshared or private notes in the folder; never one shared as widely, or more widely."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/widgets.md"))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    k = f"{P}/widgets/knowledge"
    kb_repo.write(f"{k}/widgets-history.md", "---\nname: history\n---\nolder detail\n")
    kb_repo.write(f"{k}/widgets-private.md", PRIVATE_NOTE)
    kb_repo.write(f"{k}/widgets-team.md", f"---\nfederation_scope: {ALEX}\n---\nsame readers\n")
    kb_repo.write(f"{k}/widgets-wide.md", "---\nfederation_scope: org\n---\nmore readers\n")
    kb_repo.write(f"{P}/widgets/archive/{archive_copy('widgets', kb_repo.root, 90)}", "the old page\n")
    kb_repo.write(f"{P}/gadgets/knowledge/gadgets-notes.md", "another project\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets"))
    out = cmd_structure(kb_repo.root, "owner", next_only=True)
    move_to = [f"{k}/widgets-history.md", f"{k}/widgets-private.md"]
    assert out.data == {
        "project": "widgets",
        "path": FRONT,
        "commit": f"nightly KB pass — {FRONT}",
        "mode": "trim",
        "move_to": move_to,
    }
    assert out.lines == [
        "widgets",
        "trim-only",
        *(f"move-to: {p}" for p in move_to),
        f"commit: nightly KB pass — {FRONT}",
    ]
    assert {label["path"] for label in out.labels} == {FRONT, *move_to}


def test_a_folder_shared_whole_leaves_nowhere_to_move(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-history.md", "older detail\n")
    assert cmd_structure(kb_repo.root, "owner", next_only=True).data["move_to"] == []


# ----- the privacy hook (#569 / #587) --------------------------------------------------


def test_a_trim_commit_passes_the_scheduled_run_privacy_check(kb_repo) -> None:
    """Lines leave the shared page, verbatim, for an unshared note: nothing new reaches its readers.

    #587 refuses a scheduled run that adds or rewords a line of a note shared at HEAD and in the
    commit; removing lines is allowed. This commit is the shape a trim night makes."""
    detail = "- 2026-09-02: an old rollout detail, long since superseded.\n"
    kb_repo.stage("scout-config.yaml", federated(f"{P}/widgets/widgets.md"))
    kb_repo.stage(FRONT, page("widgets", extra=detail * 3 + "Still current.\n"))
    history = f"{P}/widgets/knowledge/widgets-history.md"
    kb_repo.stage(history, "---\nname: history\n---\n# History\n")
    kb_repo.commit("owner: share the widgets page")
    kb_repo.stage(FRONT, page("widgets", extra="Still current.\n"))
    kb_repo.stage(history, "---\nname: history\n---\n# History\n" + detail * 3)
    assert [f for f in privacy_staged(kb_repo.root, env=RUN) if f.blocking] == []


# ----- shared_edits: checked (the default): a shared page is rebuilt --------------------------


def test_checked_rebuilds_a_shared_page_over_budget(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["rebuild_target"] and not row["trim_only"] and not row["share_blocked"]
    assert cmd_structure(kb_repo.root, "owner", next_only=True).lines == [
        "widgets",
        f"commit: nightly KB pass — {FRONT}",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"as_of": None}, id="no as_of"),
        pytest.param({"extra": "x" * 9000}, id="over target, under budget"),
    ],
)
def test_checked_picks_a_shared_page_cutting_cant_fix(kb_repo, kwargs: dict) -> None:
    """Under ``refuse`` these are skipped (above); a rebuild can add the date or the section."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    kb_repo.write(FRONT, page("widgets", **kwargs).replace("## History", "## Old notes"))
    assert picked(kb_repo.root) == "widgets"


def test_checked_rebuilds_a_page_shared_alone_in_an_empty_folder(kb_repo) -> None:
    """A share on the page only: the rebuild's new notes aren't shared, which is narrower."""
    kb_repo.write("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.write(FRONT, old_shape_page("widgets"))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["rebuild_target"] and not row["trim_only"]


@pytest.mark.parametrize(
    ("config", "note"),
    [
        pytest.param(federated(f"{P}/widgets/**", edits=""), PRIVATE_NOTE, id="a private note"),
        pytest.param(federated(FRONT, edits=""), "older detail\n", id="an unshared note"),
        pytest.param(
            federated(f"{P}/widgets/**", edits=""),
            "---\nfederation_scope: ['person:sam@example.com']\n---\nother readers\n",
            id="a note shared with other people",
        ),
        pytest.param(
            federated(f"{P}/widgets/**", edits="") + f"  local_only_add: ['{P}/widgets/knowledge/**']\n",
            "local\n",
            id="a local-only note",
        ),
    ],
)
@pytest.mark.parametrize(
    ("kwargs", "trim"),
    [
        pytest.param({"extra": "x" * 17000}, True, id="over budget: trim instead"),
        pytest.param({"as_of": None}, False, id="no as_of: skipped"),
    ],
)
def test_checked_never_rebuilds_a_page_from_narrower_notes(kb_repo, config: str, note: str, kwargs, trim) -> None:
    """A rebuild reads the folder and rewrites the shared page: a paraphrase of a narrower note would reach
    readers who can't see it, and the hook can't see a paraphrase."""
    kb_repo.write("scout-config.yaml", config)
    kb_repo.write(FRONT, page("widgets", **kwargs))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", note)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["trim_only"] is trim and row["rebuild_target"] is trim and row["share_blocked"] is not trim


@pytest.mark.parametrize(
    ("kwargs", "trim"),
    [
        pytest.param({"extra": "x" * 17000}, True, id="over budget: trim instead"),
        pytest.param({"as_of": None}, False, id="no as_of: skipped"),
    ],
)
def test_checked_never_rebuilds_a_page_that_declares_its_scope(kb_repo, kwargs: dict, trim: bool) -> None:
    """Step 5.1's archive copy would declare the scope too, which the hook refuses every night."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    kb_repo.write(FRONT, page("widgets", frontmatter=f"federation_scope: {ALEX}\n", **kwargs))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["trim_only"] is trim and row["rebuild_target"] is trim


def test_refuse_in_the_commit_wins_over_checked_on_disk(kb_repo) -> None:
    """The pass reads the mode as the hook does: refuse committed, checked only on disk, is refuse."""
    kb_repo.stage("scout-config.yaml", federated(f"{P}/widgets/**"))
    kb_repo.stage(FRONT, page("widgets", as_of=None))
    kb_repo.commit("owner: share, frozen")
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    assert picked(kb_repo.root) is None
    kb_repo.stage("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    kb_repo.commit("owner: checked")
    assert picked(kb_repo.root) == "widgets"


def test_checked_rebuilds_into_notes_shared_with_the_same_people(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", edits=""))
    kb_repo.write(FRONT, old_shape_page("widgets"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-team.md", f"---\nfederation_scope: {ALEX}\n---\nsame readers\n")
    assert picked(kb_repo.root) == "widgets"


@pytest.mark.parametrize(
    ("kwargs", "trim"),
    [
        pytest.param({"extra": "x" * 17000}, True, id="over budget: trim instead"),
        pytest.param({"as_of": None}, False, id="no as_of: skipped"),
    ],
)
def test_checked_never_rebuilds_into_a_wider_note(kb_repo, kwargs: dict, trim: bool) -> None:
    """A note in the folder shared with the whole org, the page with Alex only."""
    kb_repo.write("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.write(FRONT, page("widgets", **kwargs))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-wide.md", "---\nfederation_scope: org\n---\nmore readers\n")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["trim_only"] is trim and row["rebuild_target"] is trim and row["share_blocked"] is not trim
    if trim:
        assert cmd_structure(kb_repo.root, "owner", next_only=True).lines[:2] == ["widgets", "trim-only"]


def test_checked_never_rebuilds_when_new_source_notes_are_shared_wider(kb_repo) -> None:
    """Meeting notes shared with Priya, the page with Alex: Priya would read the page's facts."""
    priya = "['person:priya@example.com']"
    config = federated(f"{P}/widgets/**", edits="") + f"    - {{glob: 'knowledge-base/sources/**', scope: {priya}}}\n"
    kb_repo.write("scout-config.yaml", config)
    kb_repo.write(FRONT, page("widgets", as_of=None))
    assert rows_by_name(kb_repo.root)["widgets"]["share_blocked"]
    assert picked(kb_repo.root) is None


def test_checked_rebuilds_when_new_source_notes_are_shared_with_the_same_people(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/**", "knowledge-base/sources/**", edits=""))
    kb_repo.write(FRONT, page("widgets", as_of=None))
    assert picked(kb_repo.root) == "widgets"


def test_checked_still_skips_an_unshared_page_whose_rebuild_would_share(kb_repo) -> None:
    """The front page's scope is ``none``: any shared note the rebuild could write is wider, as before."""
    kb_repo.write("scout-config.yaml", federated(f"{P}/widgets/knowledge/**", edits=""))
    kb_repo.write(FRONT, page("widgets", as_of=None))
    assert rows_by_name(kb_repo.root)["widgets"]["share_blocked"]
    assert picked(kb_repo.root) is None


def test_a_checked_rebuild_commit_passes_the_scheduled_run_privacy_check(kb_repo) -> None:
    """The shape of a rebuild night on a shared page: the page rewritten, detail moved into a narrower note."""
    detail = "- 2026-09-02: an old rollout detail, long since superseded.\n"
    kb_repo.stage("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.stage(FRONT, page("widgets", extra=detail * 3))
    kb_repo.commit("owner: share the widgets page")
    history = f"{P}/widgets/knowledge/widgets-history.md"
    kb_repo.stage(FRONT, page("widgets", as_of="2026-11-19", extra="- The pilot runs through December.\n"))
    kb_repo.stage(history, "---\nname: history\n---\n# History\n" + detail * 3)
    assert [f for f in privacy_staged(kb_repo.root, env=RUN) if f.blocking] == []


def test_a_checked_rebuild_that_brings_in_a_private_note_is_refused(kb_repo) -> None:
    kb_repo.stage("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.stage(FRONT, page("widgets"))
    kb_repo.stage(f"{P}/widgets/knowledge/widgets-secret.md", PRIVATE_NOTE)
    kb_repo.commit("owner: share the widgets page")
    kb_repo.stage(FRONT, page("widgets", extra="- See [[widgets-secret]].\n"))
    blocking = [f.check for f in privacy_staged(kb_repo.root, env=RUN) if f.blocking]
    assert blocking == ["private-link"]


REBUILD = f"nightly KB pass — {FRONT}"


def _commit_on(kb_repo, day, monkeypatch: pytest.MonkeyPatch, message: str = "owner edit") -> None:  # type: ignore[no-untyped-def]
    with monkeypatch.context() as m:
        m.setenv("GIT_COMMITTER_DATE", f"{day}T12:00:00+00:00")
        m.setenv("GIT_AUTHOR_DATE", f"{day}T12:00:00+00:00")
        kb_repo.git("add", "-A")
        kb_repo.commit(message)


def _ago(kb_repo, days: int) -> str:  # type: ignore[no-untyped-def]
    import datetime as dt

    from scout.config import today

    return (today(kb_repo.root) - dt.timedelta(days=days)).isoformat()


def _half_rebuilt(  # type: ignore[no-untyped-def]
    kb_repo, monkeypatch: pytest.MonkeyPatch, *, copy_days_ago: int = 3, committed_days_ago: int = 2, message: str = ""
) -> None:
    """A page shared on its own, 30 days old, then a rebuild night: its archive copy and an unshared topic note."""
    kb_repo.write("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.write(FRONT, page("widgets").replace("## History", "## Old notes"))
    _commit_on(kb_repo, _ago(kb_repo, 30), monkeypatch)
    kb_repo.write(f"{P}/widgets/archive/{archive_copy('widgets', kb_repo.root, copy_days_ago)}", "the old page\n")
    kb_repo.write(f"{P}/widgets/knowledge/widgets-topic.md", "moved out of the page\n")
    kb_repo.write(FRONT, page("widgets").replace("## History", "## Old notes") + "- moved one detail out\n")
    _commit_on(kb_repo, _ago(kb_repo, committed_days_ago), monkeypatch, message or REBUILD)


def test_a_page_shared_on_its_own_resumes_its_rebuild(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Night 1 writes the archive copy and new unshared notes; they must not stop night 2."""
    _half_rebuilt(kb_repo, monkeypatch)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["resume"] and row["rebuild_target"] and not row["trim_only"]
    assert picked(kb_repo.root) == "widgets"


def test_an_older_unshared_note_still_stops_the_resume(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/research/widgets-old.md", "an older note its readers never saw\n")
    _half_rebuilt(kb_repo, monkeypatch)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["resume"] and not row["rebuild_target"] and row["share_blocked"]


def test_a_note_the_owner_added_mid_rebuild_still_stops_it(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the rebuild's own commits are exempt: an owner commit in the window isn't."""
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.write(f"{P}/widgets/knowledge/widgets-hr-notes.md", "the owner's notes\n")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, "owner: hr notes")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["resume"] and row["share_blocked"]


@pytest.mark.parametrize(
    "subject",
    [f"Nightly KB Pass — {FRONT}", f"dreaming: nightly KB pass - {FRONT}"],
    ids=["capitalised", "prefixed, plain dash"],
)
def test_a_variant_of_the_printed_subject_still_resumes(kb_repo, monkeypatch: pytest.MonkeyPatch, subject: str) -> None:
    _half_rebuilt(kb_repo, monkeypatch, message=subject)
    assert rows_by_name(kb_repo.root)["widgets"]["resume"]


def test_a_rebuild_in_two_commits_with_the_printed_subject_resumes(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 5.2's moves may be a commit of their own; each carries the line --next printed."""
    _half_rebuilt(kb_repo, monkeypatch)
    printed = cmd_structure(kb_repo.root, "owner", next_only=True).lines[-1].removeprefix("commit: ")
    kb_repo.write(f"{P}/widgets/documents/widgets-brief.md", "a loose file, filed\n")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, printed)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["resume"] and row["rebuild_target"]


def test_another_pages_pass_does_not_vouch_for_files_here(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.write(f"{P}/widgets/knowledge/widgets-extra.md", "added under another page's subject\n")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, f"nightly KB pass — {P}/gadgets/gadgets.md")
    assert rows_by_name(kb_repo.root)["widgets"]["share_blocked"]


def test_a_file_moved_into_the_folder_is_not_the_rebuilds_own(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """A move in keeps git's R status: the note was the vault's before, so it still counts."""
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets"))
    kb_repo.write(f"{P}/gadgets/knowledge/gadgets-notes.md", "a note from another project, unshared\n" * 20)
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.git("mv", f"{P}/gadgets/knowledge/gadgets-notes.md", f"{P}/widgets/knowledge/gadgets-notes.md")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, REBUILD)
    assert rows_by_name(kb_repo.root)["widgets"]["share_blocked"]


def test_an_owner_commit_that_also_edits_the_page_is_not_the_rebuilds(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the rebuild's own subject marks its commits: the owner may add a note and edit the page together."""
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.write(f"{P}/widgets/knowledge/widgets-hr-notes.md", "the owner's notes\n")
    kb_repo.write(FRONT, (kb_repo.root / FRONT).read_text(encoding="utf-8") + "- the owner's edit\n")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, "owner: hr notes and a page edit")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["resume"] and row["share_blocked"]


def test_a_narrower_note_the_rebuild_added_still_stops_it(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exempt means unshared and the rebuild's own; a note shared with someone else never is."""
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.write(
        f"{P}/widgets/knowledge/widgets-sam.md", "---\nfederation_scope: ['person:sam@example.com']\n---\nn\n"
    )
    kb_repo.write(FRONT, (kb_repo.root / FRONT).read_text(encoding="utf-8") + "- one more\n")
    _commit_on(kb_repo, _ago(kb_repo, 1), monkeypatch, REBUILD)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["resume"] and row["share_blocked"]


def test_a_backdated_archive_name_opens_no_window(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window opens at the commit that added the copy, not the date in its name."""
    kb_repo.write("scout-config.yaml", federated(FRONT, edits=""))
    kb_repo.write(FRONT, page("widgets").replace("## History", "## Old notes"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-owner.md", "an owner note, 30 days old\n")
    _commit_on(kb_repo, _ago(kb_repo, 30), monkeypatch, REBUILD)
    kb_repo.write(f"{P}/widgets/archive/{archive_copy('widgets', kb_repo.root, 44)}", "a copy named 44 days ago\n")
    _commit_on(kb_repo, _ago(kb_repo, 0), monkeypatch, REBUILD)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["rebuild_target"] and row["share_blocked"]


def test_an_uncommitted_archive_copy_opens_no_window(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _half_rebuilt(kb_repo, monkeypatch)
    kb_repo.git("rm", "-q", "--cached", f"{P}/widgets/archive/{archive_copy('widgets', kb_repo.root, 3)}")
    kb_repo.commit("owner: untrack the copy")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["resume"] and row["share_blocked"]

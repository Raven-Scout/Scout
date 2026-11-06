"""Tests for `kb structure --next` trim-only mode: a shared front page the nightly pass may cut, not rebuild."""

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


def federated(*defaults: str, scope: str = ALEX) -> str:
    rows = "".join(f"    - {{glob: '{g}', scope: {scope}}}\n" for g in defaults)
    return f"privacy:\n  federation: {{enabled: true}}\n  federation_defaults:\n{rows}"


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
    assert out.lines == ["widgets", "trim-only"]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) == {"project": "widgets", "path": FRONT, "mode": "trim", "move_to": []}


def test_a_page_shared_by_path_is_trimmed_too(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{FRONT}']\n")
    kb_repo.write(FRONT, page("widgets", extra="x" * 17000))
    assert rows_by_name(kb_repo.root)["widgets"]["trim_only"]
    assert picked(kb_repo.root) == "widgets"


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
    assert cmd_structure(kb_repo.root, "owner", next_only=True).lines == ["widgets"]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) == {"project": "widgets", "path": FRONT}


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
    assert out.data == {"project": "widgets", "path": FRONT, "mode": "trim", "move_to": move_to}
    assert out.lines == ["widgets", "trim-only", *(f"move-to: {p}" for p in move_to)]
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

"""Tests for `scoutctl kb structure --next`: what the nightly KB pass can't pick, and what it resumes."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.config import today
from scout.kb.structure import RESUME_DAYS, load_structure_config, missing_sections, next_target, structure_rows

runner = CliRunner()


def archive_copy(name: str, vault: Path, days_ago: int) -> str:
    return f"{name}-{today(vault) - dt.timedelta(days=days_ago)}-pre-restructure.md"


SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]
P = "knowledge-base/projects"
PRIVATE_NOTE = "---\nvisibility: private\n---\nnot movable\n"


def page(
    name: str, *, priority: str = "🟢", as_of: str | None = "2026-10-01", frontmatter: str = "", extra: str = ""
) -> str:
    fm = f'name: {name}\ntype: project\npriority: "{priority} P2"\n'
    fm += f"as_of: {as_of}\n" if as_of else ""
    body = "".join(f"## {s}\nNone right now.\n\n" for s in SECTIONS)
    return f"---\n{fm}{frontmatter}---\n# {name.title()}\n\n{body}{extra}"


def rows_by_name(root: Path) -> dict[str, dict]:
    return {r["project"]: r for r in structure_rows(root)}


def picked(root: Path) -> str | None:
    target = next_target(structure_rows(root))
    return target["project"] if target else None


def test_next_skips_pages_the_pass_must_not_copy(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{P}/gadgets/gadgets.md']\n")
    kb_repo.write(
        f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None, frontmatter="visibility: private\n")
    )
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/sprockets/sprockets.md", page("sprockets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert not rows["widgets"]["rebuild_target"] and not rows["widgets"]["conforms"]
    assert not rows["gadgets"]["rebuild_target"] and rows["sprockets"]["rebuild_target"]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.output == "sprockets\n"
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert sum("[--next skips]" in line for line in table.splitlines()) == 2


def test_next_skips_a_local_only_page(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  local_only_add: ['{P}/widgets/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    assert picked(kb_repo.root) == "gadgets"


def test_next_is_empty_when_only_unfixable_pages_are_left(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None, frontmatter="visibility: private\n"))
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.output == "\n"


def test_a_loose_private_file_alone_does_not_pin_the_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴"))
    kb_repo.write(f"{P}/widgets/secret.md", PRIVATE_NOTE)
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert not rows["widgets"]["conforms"] and not rows["widgets"]["rebuild_target"]
    assert picked(kb_repo.root) == "gadgets"


def test_a_loose_private_file_with_other_work_left_stays_a_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/secret.md", PRIVATE_NOTE)
    kb_repo.write(f"{P}/widgets/notes.md", "movable\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    kb_repo.write(f"{P}/gadgets/secret.md", PRIVATE_NOTE)
    rows = rows_by_name(kb_repo.root)
    assert rows["widgets"]["rebuild_target"] and rows["gadgets"]["rebuild_target"]


def old_shape_page(name: str, **kwargs) -> str:
    """A big page that hasn't been rewritten yet: no standard sections."""
    return page(name, **kwargs).replace("## ", "### ") + "x" * 17000


def test_next_resumes_a_half_rebuilt_page_first(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", old_shape_page("gadgets"))
    kb_repo.write(f"{P}/gadgets/archive/{archive_copy('gadgets', kb_repo.root, 3)}", "the old page\n")
    assert rows_by_name(kb_repo.root)["gadgets"]["resume"]
    assert picked(kb_repo.root) == "gadgets"


def test_an_old_archive_copy_does_not_jump_the_queue(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", old_shape_page("gadgets"))
    kb_repo.write(f"{P}/gadgets/archive/{archive_copy('gadgets', kb_repo.root, RESUME_DAYS + 1)}", "the old page\n")
    assert not rows_by_name(kb_repo.root)["gadgets"]["resume"]
    assert picked(kb_repo.root) == "widgets"


def test_a_rewritten_page_still_over_budget_does_not_resume(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="x" * 17000))
    kb_repo.write(f"{P}/gadgets/archive/{archive_copy('gadgets', kb_repo.root, 3)}", "the old page\n")
    row = rows_by_name(kb_repo.root)["gadgets"]
    assert row["rebuild_target"] and not row["resume"]
    assert picked(kb_repo.root) == "widgets"


# ----- pages that only need shrinking ---------------------------------------------------


def test_a_page_failing_only_on_size_waits_behind_a_lower_priority_page(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="x" * 17000))
    assert picked(kb_repo.root) == "widgets"


def test_a_page_failing_only_on_narration_waits_behind_a_lower_priority_page(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="Last verified today.\n"))
    assert picked(kb_repo.root) == "widgets"


def test_a_big_page_is_still_picked_once_nothing_else_is_left(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", extra="x" * 17000))
    assert picked(kb_repo.root) == "gadgets"


def test_pages_that_only_need_shrinking_go_by_priority_then_size(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", extra="x" * 20000))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="x" * 16000))
    kb_repo.write(f"{P}/gizmos/gizmos.md", page("gizmos", priority="🔴", extra="x" * 18000))
    assert [r["project"] for r in structure_rows(kb_repo.root)] == ["gizmos", "gadgets", "widgets"]


@pytest.mark.parametrize(
    "stray",
    [
        pytest.param({"odd/note.md": "a note\n"}, id="an odd subfolder"),
        pytest.param({"notes.md": PRIVATE_NOTE}, id="a pinned loose file"),
    ],
)
def test_what_no_night_can_clear_does_not_lift_a_big_page(kb_repo, stray: dict[str, str]) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="x" * 17000))
    for name, text in stray.items():
        kb_repo.write(f"{P}/gadgets/{name}", text)
    assert rows_by_name(kb_repo.root)["gadgets"]["rebuild_target"]
    assert picked(kb_repo.root) == "widgets"


@pytest.mark.parametrize(
    ("big", "extra_file"),
    [
        pytest.param({"as_of": None}, None, id="no as_of"),
        pytest.param({}, "notes.md", id="a loose file a night can move"),
    ],
)
def test_a_big_page_with_other_work_left_keeps_its_place(kb_repo, big: dict, extra_file: str | None) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", extra="x" * 17000, **big))
    if extra_file:
        kb_repo.write(f"{P}/gadgets/{extra_file}", "a note\n")
    assert picked(kb_repo.root) == "gadgets"


# ----- a share where the rebuild writes ------------------------------------------------


def test_next_skips_a_folder_with_a_shared_subfolder(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "n\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    args = ["kb", "share", f"{P}/widgets/knowledge", "--with", "alex@example.com", "--yes", "--repo", str(kb_repo.root)]
    assert runner.invoke(cli.app, args).exit_code == 0
    assert not rows_by_name(kb_repo.root)["widgets"]["rebuild_target"]
    assert picked(kb_repo.root) == "gadgets"


def test_next_skips_a_share_on_a_subfolder_with_nothing_in_it_yet(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{P}/widgets/archive/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert not rows_by_name(kb_repo.root)["widgets"]["rebuild_target"]
    assert picked(kb_repo.root) is None


def test_next_skips_a_folder_holding_one_shared_note(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "---\nfederation_scope: org\n---\nn\n")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["rebuild_target"] and not row["resume"] and not row["conforms"]
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert "[--next skips]" in table


def test_next_skips_a_shared_note_in_a_sub_project(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/widgets-api/widgets-api.md", "---\nfederation_scope: org\n---\nn\n")
    assert picked(kb_repo.root) is None


def test_next_skips_a_path_share_that_only_an_existing_note_matches(kb_repo) -> None:
    """A folder the probes don't look in: only classifying the note itself finds this share."""
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{P}/widgets/widgets-api/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/widgets-api/widgets-api.md", "no frontmatter\n")
    assert picked(kb_repo.root) is None


def test_a_note_with_a_quoted_frontmatter_key_is_still_found(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", '---\n"federation_scope": org\n---\nn\n')
    assert picked(kb_repo.root) is None


def test_a_note_with_an_escaped_frontmatter_key_is_still_found(kb_repo) -> None:
    """The escape is the one way text can hide the key from a substring check."""
    note = '---\n"federation\\x5fscope": org\n---\nn\n'
    assert "federation_scope" not in note
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", note)
    assert picked(kb_repo.root) is None


def test_a_harmless_backslash_in_frontmatter_is_classified_and_not_a_share(kb_repo, monkeypatch) -> None:
    from scout.kb import structure

    rel = f"{P}/widgets/knowledge/widgets-notes.md"
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(rel, '---\nname: "a \\"quoted\\" name"\n---\nn\n')
    seen: list[str] = []
    real = structure.classify

    def spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(structure, "classify", spy)
    assert picked(kb_repo.root) == "widgets"
    assert rel in seen


def test_a_backslash_in_the_body_is_not_classified(kb_repo, monkeypatch) -> None:
    from scout.kb import structure

    rel = f"{P}/widgets/knowledge/widgets-notes.md"
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(rel, "---\nname: plain\n---\nA path like C:\\temp in the body.\n")
    seen: list[str] = []
    real = structure.classify

    def spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(structure, "classify", spy)
    assert picked(kb_repo.root) == "widgets"
    assert rel not in seen


def test_plain_notes_are_not_classified_when_nothing_could_share_them(kb_repo, monkeypatch) -> None:
    from scout.kb import structure

    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "---\nname: a plain note\n---\nn\n")
    seen: list[str] = []
    real = structure.classify

    def spy(rel, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(rel)
        return real(rel, *args, **kwargs)

    monkeypatch.setattr(structure, "classify", spy)
    assert picked(kb_repo.root) == "widgets"
    assert f"{P}/widgets/knowledge/widgets-notes.md" not in seen


def test_a_share_elsewhere_does_not_skip_the_folder(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{P}/gadgets/knowledge/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) == "widgets"


@pytest.mark.parametrize(
    "glob",
    [
        "knowledge-base/projects/*/knowledge/*-decisions.md",  # the note step 5.2 names
        "knowledge-base/projects/*/knowledge/topic-*.md",
        "knowledge-base/projects/widgets/knowledge/widgets-decisions.md",
        "knowledge-base/projects/*/archive/*-pre-restructure.md",
    ],
)
def test_next_skips_a_share_on_any_note_name_a_rebuild_could_write(kb_repo, glob: str) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{glob}']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) is None


def test_next_skips_a_federation_default_on_one_note(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "privacy:\n  federation: {enabled: true}\n  federation_defaults:\n"
        f"    - {{glob: '{P}/widgets/knowledge/widgets-decisions.md', scope: ['person:alex@example.com']}}\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) is None


def test_a_default_that_shares_nothing_does_not_shadow_a_real_share(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "privacy:\n  federation: {enabled: true}\n  federation_defaults:\n"
        f"    - {{glob: '{P}/widgets/knowledge/private-*.md', scope: none}}\n"
        f"    - {{glob: '{P}/widgets/knowledge/**', scope: ['person:alex@example.com']}}\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) is None


def test_next_skips_every_page_when_source_notes_are_shared(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "privacy:\n  shared_files_add: ['knowledge-base/sources/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert not rows["widgets"]["rebuild_target"] and not rows["gadgets"]["rebuild_target"]
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert sum("[--next skips]" in line for line in table.splitlines()) == 2


def test_next_skips_a_federation_default_on_meeting_source_notes(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "privacy:\n  federation: {enabled: true}\n  federation_defaults:\n"
        "    - {glob: 'knowledge-base/sources/**', scope: ['person:alex@example.com']}\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) is None


def test_a_federation_default_with_sharing_off_does_not_skip(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "privacy:\n  federation: {enabled: false}\n  federation_defaults:\n"
        "    - {glob: 'knowledge-base/sources/**', scope: ['person:alex@example.com']}\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) == "widgets"


def test_a_trimmed_allowed_dirs_still_probes_the_folders_the_step_writes(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        f"kb_structure:\n  allowed_dirs: [knowledge]\nprivacy:\n  shared_files_add: ['{P}/widgets/archive/**']\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    assert picked(kb_repo.root) is None


# ----- odd subfolders can't hold the pick ----------------------------------------------


def test_an_odd_subfolder_alone_never_holds_the_pick(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴"))
    kb_repo.stage(f"{P}/widgets/assets/diagram.md", "tracked\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["odd_dirs"] and not row["conforms"] and not row["rebuild_target"]
    assert picked(kb_repo.root) == "gadgets"
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert any("widgets" in line and "[--next skips]" in line for line in table.splitlines())


def test_an_odd_subfolder_with_real_work_left_stays_a_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    kb_repo.stage(f"{P}/widgets/assets/diagram.md", "tracked\n")
    assert picked(kb_repo.root) == "widgets"


def test_a_folder_git_doesnt_track_is_not_odd(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/images/shot.md", "untracked\n")
    kb_repo.write(f"{P}/widgets/.obsidian/workspace.md", "untracked\n")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["odd_dirs"] == [] and row["conforms"]


def test_a_gitignored_file_is_not_loose(kb_repo) -> None:
    kb_repo.write(".gitignore", "scratch.md\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/scratch.md", "ignored\n")
    kb_repo.write(f"{P}/widgets/notes.md", "untracked, not ignored\n")
    assert rows_by_name(kb_repo.root)["widgets"]["loose"] == [f"{P}/widgets/notes.md"]


def test_outside_a_repo_the_folder_itself_is_what_counts(tmp_path: Path) -> None:
    (tmp_path / P / "widgets" / "assets").mkdir(parents=True)
    (tmp_path / P / "widgets" / "widgets.md").write_text(page("widgets"), encoding="utf-8")
    (tmp_path / P / "widgets" / "notes.md").write_text("n\n", encoding="utf-8")
    row = rows_by_name(tmp_path)["widgets"]
    assert row["odd_dirs"] == [f"{P}/widgets/assets"] and row["loose"] == [f"{P}/widgets/notes.md"]


def test_a_finished_rebuild_that_lost_its_as_of_does_not_jump_the_queue(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    kb_repo.write(f"{P}/gadgets/archive/gadgets-2026-10-30-pre-restructure.md", "the old page\n")
    assert not rows_by_name(kb_repo.root)["gadgets"]["resume"]
    assert picked(kb_repo.root) == "widgets"


def test_the_defaults_match_the_seeded_standard() -> None:
    template = Path(__file__).parents[3] / "templates" / "docs" / "kb-page-standard.md"
    text = template.read_text(encoding="utf-8")
    cfg = load_structure_config()
    assert "**Target: under 8 KB. Hard budget: 15 KB.**" in text
    assert (cfg.page_target_bytes, cfg.page_budget_bytes) == (8 * 1024, 15 * 1024)
    assert missing_sections(text, cfg.required_sections) == []
    for name in cfg.allowed_dirs:
        assert f"  {name}/ " in text

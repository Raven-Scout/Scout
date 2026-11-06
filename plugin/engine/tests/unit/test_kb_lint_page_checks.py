"""Tests for kb lint's page checks (`page-loose`, `page-sections`, `page-narration`): new debt on a project folder."""

from __future__ import annotations

import io
from pathlib import Path

from scout.kb.lint import lint_staged, run_hook
from scout.kb.lint_config import MODE_CHECKS, REPORT_UNTIL_NAMED, load_lint_config

SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]
FRONT = "knowledge-base/projects/widgets/widgets.md"
LOOSE = "knowledge-base/projects/widgets/notes.md"
PAGE_CHECKS = {"page-loose", "page-sections", "page-narration"}
NAMED_BLOCK = (
    "kb_lint:\n  path_modes:\n"
    "    - {glob: 'knowledge-base/projects/**', mode: block, checks: [page-loose, page-sections, page-narration]}\n"
)


def page(sections: list[str] = SECTIONS, extra: str = "", frontmatter: str = "") -> str:
    body = "".join(f"## {s}\nNone right now.\n\n" for s in sections)
    return f"---\nname: widgets\ntype: project\nas_of: 2026-10-01\n{frontmatter}---\n# Widgets\n\n{body}{extra}"


def findings(kb_repo) -> list:
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [f for f in result.findings if f.check in PAGE_CHECKS or f.check == "page-config"]


def committed_page(kb_repo, text: str | None = None) -> None:
    kb_repo.stage(FRONT, text if text is not None else page())
    kb_repo.commit()


# ----- page-loose ----------------------------------------------------------------


def test_a_new_file_beside_the_front_page_is_reported(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    [f] = findings(kb_repo)
    assert (f.path, f.check, f.blocking) == (LOOSE, "page-loose", True)
    assert "documents/" in f.message and "knowledge/" in f.message


def test_a_new_front_page_and_its_loose_file_in_one_commit(kb_repo) -> None:
    kb_repo.stage(FRONT, page())
    kb_repo.stage(LOOSE, "n\n")
    assert [f.check for f in findings(kb_repo)] == ["page-loose"]


def test_filed_or_frontless_or_skipped_files_are_not_loose(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage("knowledge-base/projects/widgets/knowledge/widgets-notes.md", "n\n")
    kb_repo.stage("knowledge-base/projects/drafts/notes.md", "no front page here\n")
    kb_repo.stage("knowledge-base/projects/archived/archived.md", "skipped folder\n")
    kb_repo.stage("knowledge-base/projects/archived/notes.md", "skipped folder\n")
    kb_repo.stage("knowledge-base/topics/widgets.md", "elsewhere\n")
    assert findings(kb_repo) == []


def test_editing_or_renaming_an_existing_loose_file_adds_no_debt(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "a line of notes\n" * 20)
    kb_repo.commit()
    kb_repo.stage(LOOSE, "a line of notes\n" * 21)
    assert findings(kb_repo) == []
    kb_repo.commit()
    kb_repo.git("mv", LOOSE, "knowledge-base/projects/widgets/other.md")
    assert findings(kb_repo) == []


def test_moving_a_filed_note_up_beside_the_front_page_is_new_debt(kb_repo) -> None:
    filed = "knowledge-base/projects/widgets/knowledge/notes.md"
    committed_page(kb_repo)
    kb_repo.stage(filed, "a line of notes\n" * 20)
    kb_repo.commit()
    kb_repo.git("mv", filed, LOOSE)
    assert [f.check for f in findings(kb_repo)] == ["page-loose"]


def test_excluded_paths_are_left_out(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  exclude_globs: ['knowledge-base/projects/widgets/scratch.md']\n")
    kb_repo.write(
        "knowledge-base/ontology/schema.yaml",
        "entity_types: {}\nrelationship_types: {}\ngraph:\n  exclude: ['projects/old-*/**']\n",
    )
    committed_page(kb_repo)
    kb_repo.stage("knowledge-base/projects/widgets/scratch.md", "n\n")
    kb_repo.stage("knowledge-base/projects/old-widgets/old-widgets.md", "no sections\n")
    kb_repo.stage("knowledge-base/projects/old-widgets/notes.md", "n\n")
    assert findings(kb_repo) == []


# ----- page-sections -------------------------------------------------------------


def test_a_new_front_page_needs_every_section(kb_repo) -> None:
    kb_repo.stage(FRONT, page(SECTIONS[:5]))
    [f] = findings(kb_repo)
    assert f.check == "page-sections" and "new front page lacks" in f.message
    assert "Go deeper, History" in f.message


def test_an_edit_may_not_remove_a_section(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(FRONT, page(SECTIONS[:6]))
    [f] = findings(kb_repo)
    assert f.check == "page-sections" and "commit removes" in f.message and "History" in f.message


def test_a_page_already_missing_sections_can_still_be_edited(kb_repo) -> None:
    committed_page(kb_repo, page(SECTIONS[:2]))
    kb_repo.stage(FRONT, page(SECTIONS[:2], extra="A new plain fact.\n"))
    assert findings(kb_repo) == []
    # Adding a section with the others still missing is progress, not a finding.
    kb_repo.stage(FRONT, page(SECTIONS[:3]))
    assert findings(kb_repo) == []


def test_a_renamed_front_page_compares_with_its_old_self(kb_repo) -> None:
    committed_page(kb_repo, page(SECTIONS[:2], extra="a plain line\n" * 40))
    kb_repo.git("mv", "knowledge-base/projects/widgets", "knowledge-base/projects/gadgets")
    kb_repo.git("mv", "knowledge-base/projects/gadgets/widgets.md", "knowledge-base/projects/gadgets/gadgets.md")
    assert findings(kb_repo) == []


# ----- page-narration ------------------------------------------------------------


def test_an_added_narration_line_is_reported_with_its_line_number(kb_repo) -> None:
    committed_page(kb_repo)
    text = page(extra="Finding 12 says so.\nPlain prose.\n")
    kb_repo.stage(FRONT, text)
    [f] = findings(kb_repo)
    assert f.check == "page-narration" and "Finding N" in f.message
    assert f.line == text.split("\n").index("Finding 12 says so.") + 1
    assert "says so" not in f.message  # a marker's name, never the line's text


def test_existing_narration_is_debt_and_only_new_lines_count(kb_repo) -> None:
    committed_page(kb_repo, page(extra="Finding 12 says so.\n"))
    kb_repo.stage(FRONT, page(extra="Finding 12 says so.\nA new plain fact.\n"))
    assert findings(kb_repo) == []


def test_frontmatter_is_where_provenance_belongs(kb_repo) -> None:
    kb_repo.stage(FRONT, page(frontmatter="note: last verified by the evening-consolidation\n"))
    assert findings(kb_repo) == []


def test_narration_outside_a_front_page_is_not_this_checks_business(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage("knowledge-base/projects/widgets/knowledge/widgets-notes.md", "Finding 12 says so.\n")
    assert findings(kb_repo) == []


def test_editing_a_line_that_already_has_a_marker_is_not_new_debt(kb_repo) -> None:
    committed_page(kb_repo, page(extra="Last verified Oct 3 against the tracker.\nFinding 3 from §4 holds.\n"))
    kb_repo.stage(FRONT, page(extra="Last verified Oct 9 against the tracker.\nFinding 3 from §4 holds, mostly.\n"))
    assert findings(kb_repo) == []


def test_a_removed_marker_excuses_one_added_line_and_only_its_own_kind(kb_repo) -> None:
    committed_page(kb_repo, page(extra="Finding 3 holds.\n"))
    kb_repo.stage(FRONT, page(extra="Finding 3 holds, mostly.\nFinding 4 is new.\nPer §9 too.\n"))
    found = findings(kb_repo)
    assert [f.check for f in found] == ["page-narration", "page-narration"]
    assert "§N" in found[1].message


def test_a_line_with_a_new_marker_beside_an_old_one_is_still_new_debt(kb_repo) -> None:
    committed_page(kb_repo, page(extra="Finding 3 holds.\n"))
    kb_repo.stage(FRONT, page(extra="Finding 3 holds, per §9.\n"))
    [f] = findings(kb_repo)
    assert f.check == "page-narration" and "§N" in f.message


def test_a_big_rewrite_prints_a_capped_list_of_narrated_lines(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(FRONT, page(extra="".join(f"Finding {n} holds.\n" for n in range(40))))
    found = findings(kb_repo)
    assert len(found) == 11 and all(f.check == "page-narration" for f in found)
    assert found[-1].line is None and "30 more" in found[-1].message


# ----- renames ---------------------------------------------------------------------


def test_a_note_renamed_into_the_front_page_slot_is_a_new_front_page(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage("knowledge-base/projects/gadgets/notes.md", "a line of notes\n" * 20)
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/projects/gadgets/notes.md", "knowledge-base/projects/gadgets/gadgets.md")
    [f] = findings(kb_repo)
    assert f.check == "page-sections" and "new front page lacks" in f.message


def test_a_loose_file_moved_in_from_another_project_is_new_debt_there(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage("knowledge-base/projects/gadgets/gadgets.md", page())
    kb_repo.stage("knowledge-base/projects/gadgets/notes.md", "a line of notes\n" * 20)
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/projects/gadgets/notes.md", LOOSE)
    [f] = findings(kb_repo)
    assert (f.path, f.check) == (LOOSE, "page-loose")


def test_renaming_a_whole_project_moves_its_loose_files_without_new_debt(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "a line of notes\n" * 20)
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/projects/widgets", "knowledge-base/projects/gadgets")
    kb_repo.git("mv", "knowledge-base/projects/gadgets/widgets.md", "knowledge-base/projects/gadgets/gadgets.md")
    assert findings(kb_repo) == []


# ----- privacy ------------------------------------------------------------------


def test_a_private_page_gets_only_paths_line_numbers_and_counts(kb_repo) -> None:
    kb_repo.stage(FRONT, page(SECTIONS[:5], extra="Finding 12 says so.\n", frontmatter="visibility: private\n"))
    found = {f.check: f for f in findings(kb_repo)}
    assert set(found) == {"page-sections", "page-narration"}
    assert "2 required section(s)" in found["page-sections"].message
    assert "Go deeper" not in found["page-sections"].message and "History" not in found["page-sections"].message
    assert "Finding" not in found["page-narration"].message and found["page-narration"].line


# ----- modes: report until a rule names them ---------------------------------------


def test_page_checks_are_mode_checks_that_report_until_named() -> None:
    assert PAGE_CHECKS == REPORT_UNTIL_NAMED and PAGE_CHECKS <= MODE_CHECKS


def test_they_report_by_default(kb_repo) -> None:
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "[page-loose]" in out.getvalue() and "report mode" in out.getvalue()


def test_a_global_block_mode_does_not_make_them_block(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "[page-loose] (report)" in out.getvalue() and "0 enforced" in out.getvalue()


def test_a_rule_with_no_checks_does_not_make_them_block(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  path_modes:\n    - {glob: 'knowledge-base/**', mode: block}\n")
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 0


def test_a_rule_naming_them_blocks(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", NAMED_BLOCK)
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1
    assert "[page-loose]" in out.getvalue() and "Commit blocked" in out.getvalue()
    assert "config:" not in out.getvalue()


def test_the_override_still_works_when_they_block(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", NAMED_BLOCK)
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "n\n")
    assert run_hook(kb_repo.root, env={"SCOUT_LINT_OVERRIDE": "moving next"}, out=io.StringIO()) == 0


# ----- a bad setting costs the page checks only --------------------------------------


def test_a_bad_marker_pattern_warns_once_and_keeps_the_rest_of_lint(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  mode: block\nkb_structure:\n  narration_markers:\n    - {label: bad, pattern: '('}\n",
    )
    committed_page(kb_repo)
    kb_repo.stage(FRONT, page(extra="x" * 20000 + "\n"))
    kb_repo.stage(LOOSE, "n\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1  # the size and line lint still block
    text = out.getvalue()
    assert text.count("[page-config]") == 1 and "bad pattern" in text
    assert "[mega-line]" in text and "[page-loose]" not in text


def test_a_malformed_schema_costs_the_page_checks_only(kb_repo) -> None:
    kb_repo.write("knowledge-base/ontology/schema.yaml", "graph:\n  exclude: not-a-list\n")
    committed_page(kb_repo)
    kb_repo.stage(LOOSE, "x" * 20000 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "[mega-line]" in out.getvalue() and "[page-loose]" not in out.getvalue()


def test_a_commit_outside_the_projects_folder_never_reads_the_schema(kb_repo, monkeypatch) -> None:
    from scout.kb import structure

    def boom(_vault: Path) -> tuple[str, ...]:
        raise AssertionError("the schema was read")

    monkeypatch.setattr(structure, "_graph_excludes", boom)
    kb_repo.stage("knowledge-base/sources/2026-10/a-note.md", "n\n")
    assert findings(kb_repo) == []

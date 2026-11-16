"""The line ratchet in scout.kb.lint: existing debt can drain, new debt can't land."""

from __future__ import annotations

import io

import pytest

from scout.kb.git_io import staged_line_changes
from scout.kb.lint import lint_staged, run_hook
from scout.kb.lint_config import load_lint_config

PAGE = "knowledge-base/projects/acme-pilot/documents/acme-pilot.md"
NOTE = "knowledge-base/projects/acme-pilot/documents/retrieval.md"
OTHER = "knowledge-base/projects/acme-pilot/documents/other.md"
SHARD = "knowledge-base/session-log/2026-09.md"
DAY1 = "action-items/action-items-2026-09-27.md"
DAY2 = "action-items/action-items-2026-09-28.md"
DIARY = "## §59 · 2026-09-07 (overnight-research)"


def _long(chars: int = 2000, word: str = "alpha") -> str:
    """A prose line of exactly ``chars`` characters."""
    return (f"{word} " * (chars // len(f"{word} ") + 1))[:chars]


def _setup(kb_repo, config: str = "kb_lint:\n  mode: block\n", **files: str) -> None:
    kb_repo.stage("scout-config.yaml", config)
    for rel, text in files.items():
        kb_repo.stage(_REL[rel], text)
    kb_repo.commit("start")


_REL = {"page": PAGE, "note": NOTE, "other": OTHER, "shard": SHARD, "day1": DAY1}


def _findings(kb_repo) -> list[tuple[str, str]]:
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return sorted((f.path, f.check) for f in result.blocking)


# ----- editing a line in place --------------------------------------------------


def test_shortening_an_over_limit_line_passes(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, _long(1800) + "\n")
    assert _findings(kb_repo) == []


def test_changing_a_word_at_the_same_length_passes(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, "omega" + _long()[5:] + "\n")
    assert _findings(kb_repo) == []


def test_growing_an_over_limit_line_blocks(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, _long(2001) + "\n")
    assert _findings(kb_repo) == [(PAGE, "mega-line")]


def test_a_line_growing_past_the_limit_blocks(kb_repo) -> None:
    _setup(kb_repo, page=_long(1400) + "\n")
    kb_repo.stage(PAGE, _long(1600) + "\n")
    assert _findings(kb_repo) == [(PAGE, "mega-line")]


def test_splitting_one_long_line_into_two_long_ones_blocks_one(kb_repo) -> None:
    _setup(kb_repo, page=_long(3400) + "\n")
    kb_repo.stage(PAGE, _long(1700, "beta") + "\n\n" + _long(1600, "gamma") + "\n")
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    # The removed line excuses the longer one, and the shorter is new debt.
    assert [(f.check, f.line) for f in result.blocking] == [("mega-line", 3)]


def test_each_removed_line_goes_to_the_longest_line_it_can_excuse(kb_repo) -> None:
    _setup(kb_repo, page=_long(2000, "a1") + "\n" + _long(1800, "a2") + "\n")
    kb_repo.stage(PAGE, _long(1700, "b1") + "\n" + _long(1900, "b2") + "\n")
    assert _findings(kb_repo) == []


def test_replacing_a_short_line_with_a_long_one_blocks(kb_repo) -> None:
    _setup(kb_repo, page="short line\n" + _long() + "\n")
    kb_repo.stage(PAGE, _long(1900, "beta") + "\n" + _long() + "\n")
    assert _findings(kb_repo) == [(PAGE, "mega-line")]


def test_a_line_moved_within_the_file_excuses_nothing_else(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\nend\n")
    kb_repo.stage(PAGE, "end\n" + _long() + "\n" + _long(1600, "beta") + "\n")
    assert _findings(kb_repo) == [(PAGE, "mega-line")]


def test_a_brand_new_long_line_blocks(kb_repo) -> None:
    _setup(kb_repo, page="short\n")
    kb_repo.stage(PAGE, "short\n" + _long() + "\n")
    assert _findings(kb_repo) == [(PAGE, "mega-line")]


def test_a_new_line_in_a_file_with_old_debt_blocks_and_the_debt_does_not(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, _long() + "\n" + _long(1600, "beta") + "\n")
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert [(f.check, f.line) for f in result.blocking] == [("mega-line", 2)]


def test_a_renamed_file_edit_counts_as_in_place(kb_repo) -> None:
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, page=body + _long() + "\n")
    kb_repo.git("mv", PAGE, NOTE)
    kb_repo.stage(NOTE, body + _long(1800) + "\n")
    assert _findings(kb_repo) == []


# ----- moving a line to another file --------------------------------------------


def test_moving_a_long_line_to_a_new_note_passes(kb_repo) -> None:
    _setup(kb_repo, page="intro\n" + _long() + "\n")
    kb_repo.stage(PAGE, "intro\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert _findings(kb_repo) == []


def test_moving_a_long_line_to_an_existing_note_passes(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n", note="notes\n")
    kb_repo.stage(PAGE, "see [[retrieval]]\n")
    kb_repo.stage(NOTE, "notes\n" + _long() + "\n")
    assert _findings(kb_repo) == []


def test_a_move_ignores_cosmetic_differences(kb_repo) -> None:
    _setup(kb_repo, page="- [ ] " + _long() + "\n")
    kb_repo.stage(PAGE, "intro\n")
    kb_repo.stage(NOTE, "- [x] " + _long() + "\n")
    assert _findings(kb_repo) == []


def test_moving_a_line_out_of_a_deleted_file_passes(kb_repo) -> None:
    """A rewrite below git's rename threshold reads as a delete and an add."""
    _setup(kb_repo, page=_long() + "\n" + "".join(f"old {i}\n" for i in range(40)))
    kb_repo.git("rm", "-q", PAGE)
    kb_repo.stage(NOTE, _long() + "\n")
    assert _findings(kb_repo) == []


def test_a_reworded_move_blocks(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, "see the note\n")
    kb_repo.stage(NOTE, _long(1800) + "\n")
    assert _findings(kb_repo) == [(NOTE, "mega-line")]


def test_a_copy_blocks(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert _findings(kb_repo) == [(NOTE, "mega-line")]


def test_a_copy_alongside_a_rename_of_its_source_blocks(kb_repo) -> None:
    """A true rename removes only what it changed, so its kept lines excuse nothing."""
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, page=body + _long() + "\n")
    kb_repo.git("mv", PAGE, OTHER)
    kb_repo.stage(NOTE, _long() + "\n")
    assert _findings(kb_repo) == [(NOTE, "mega-line")]


def test_a_moved_line_excuses_one_copy(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, "moved\n")
    kb_repo.stage(NOTE, _long() + "\n")
    kb_repo.stage(OTHER, _long() + "\n")
    assert len(_findings(kb_repo)) == 1


def test_one_removed_line_cant_excuse_an_edit_and_a_move(kb_repo) -> None:
    _setup(kb_repo, page=_long() + "\n")
    kb_repo.stage(PAGE, _long(1800) + "\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert len(_findings(kb_repo)) == 1


def test_a_line_within_the_limit_at_its_source_is_not_debt(kb_repo) -> None:
    """Fine in a page (limit 1500), over the strict limit in a daily file: that's new debt."""
    line = _long(600)
    _setup(kb_repo, page=line + "\n", day1="old\n")
    kb_repo.stage(PAGE, "moved\n")
    kb_repo.stage(DAY1, "old\n" + line + "\n")
    assert _findings(kb_repo) == [(DAY1, "mega-line")]


def test_a_line_from_an_unlinted_file_is_not_debt(kb_repo) -> None:
    source = "knowledge-base/personal/pet-records/extracted/doc1.md"
    _setup(kb_repo)
    kb_repo.stage(source, _long() + "\n")
    kb_repo.commit("source")
    kb_repo.stage(source, "moved\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert _findings(kb_repo) == [(NOTE, "mega-line")]


# ----- diary headings -----------------------------------------------------------


def test_moving_a_diary_heading_to_a_note_passes(kb_repo) -> None:
    _setup(kb_repo, page="intro\n" + DIARY + "\n")
    kb_repo.stage(PAGE, "intro\n")
    kb_repo.stage(NOTE, DIARY + "\n")
    assert _findings(kb_repo) == []


def test_editing_a_diary_heading_in_place_passes(kb_repo) -> None:
    _setup(kb_repo, page=DIARY + "\n")
    kb_repo.stage(PAGE, DIARY.replace("§59", "§60") + "\n")
    assert _findings(kb_repo) == []


def test_a_new_diary_heading_blocks(kb_repo) -> None:
    _setup(kb_repo, page="intro\n")
    kb_repo.stage(PAGE, "intro\n" + DIARY + "\n")
    assert _findings(kb_repo) == [(PAGE, "diary")]


def test_a_diary_heading_from_a_log_file_is_not_debt(kb_repo) -> None:
    """It's allowed in a log, so moving it into a page adds a diary heading there."""
    _setup(kb_repo, shard=DIARY + "\n")
    kb_repo.stage(SHARD, "moved\n")
    kb_repo.stage(PAGE, DIARY + "\n")
    assert _findings(kb_repo) == [(PAGE, "diary")]


# ----- the path modes -----------------------------------------------------------

KB_BLOCKS = (
    "kb_lint:\n  mode: report\n  path_modes:\n"
    "    - {glob: 'knowledge-base/**', mode: block, exclude_logs: true, checks: [mega-line, diary]}\n"
)


def _blocked(kb_repo) -> int:
    return run_hook(kb_repo.root, env={}, out=io.StringIO())


def test_debt_does_not_move_from_a_report_path_into_a_block_path(kb_repo) -> None:
    _setup(kb_repo, KB_BLOCKS, shard=_long() + "\n")
    kb_repo.stage(SHARD, "moved\n")
    kb_repo.stage(PAGE, _long() + "\n")
    assert _blocked(kb_repo) == 1


def test_debt_moves_between_block_paths(kb_repo) -> None:
    _setup(kb_repo, KB_BLOCKS, page=_long() + "\n")
    kb_repo.stage(PAGE, "moved\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert _blocked(kb_repo) == 0


def test_debt_moves_from_a_block_path_to_a_report_path(kb_repo) -> None:
    _setup(kb_repo, KB_BLOCKS, page=_long() + "\n")
    kb_repo.stage(PAGE, "moved\n")
    kb_repo.stage(SHARD, _long() + "\n")
    assert _findings(kb_repo) == []


def test_a_rename_is_linted_under_its_new_path_mode(kb_repo) -> None:
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, KB_BLOCKS, shard=body)
    (kb_repo.root / PAGE).parent.mkdir(parents=True)
    kb_repo.git("mv", SHARD, PAGE)
    kb_repo.stage(PAGE, body + _long() + "\n")
    assert _blocked(kb_repo) == 1
    kb_repo.stage(PAGE, body)
    assert _blocked(kb_repo) == 0


def test_a_rename_from_a_report_path_carries_no_debt_into_a_block_path(kb_repo) -> None:
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, KB_BLOCKS, shard=body + _long() + "\n")
    (kb_repo.root / PAGE).parent.mkdir(parents=True)
    kb_repo.git("mv", SHARD, PAGE)
    assert _blocked(kb_repo) == 0  # unchanged: the rename adds nothing to check
    kb_repo.stage(PAGE, body + _long(1800) + "\n")
    assert _blocked(kb_repo) == 1  # shortened, but it's a new long line here
    kb_repo.stage(PAGE, body + _long(1800, "omega") + "\n")
    assert _blocked(kb_repo) == 1


def test_a_rename_between_block_paths_still_drains(kb_repo) -> None:
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, KB_BLOCKS, page=body + _long() + "\n")
    kb_repo.git("mv", PAGE, NOTE)
    kb_repo.stage(NOTE, body + _long(1800) + "\n")
    assert _blocked(kb_repo) == 0


def test_a_block_path_claims_debt_before_a_report_path_copy(kb_repo) -> None:
    """action-items/ sorts first, so without the claim order its copy would spend the debt."""
    copy = "action-items/context/PROJ-1234.md"
    _setup(kb_repo, KB_BLOCKS, page=_long() + "\n")
    kb_repo.stage(PAGE, "moved\n")
    kb_repo.stage(copy, _long() + "\n")
    kb_repo.stage(NOTE, _long() + "\n")
    assert _blocked(kb_repo) == 0
    assert _findings(kb_repo) == [(copy, "mega-line")]  # logged as report, not enforced


def test_one_mode_everywhere_keeps_the_staged_order(kb_repo) -> None:
    _setup(kb_repo)
    kb_repo.stage(OTHER, _long() + "\n")
    kb_repo.stage(NOTE, _long() + "\n")
    kb_repo.stage(DAY1, _long() + "\n")
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert [f.path for f in result.blocking] == [DAY1, OTHER, NOTE]


# ----- a new daily file against its baseline ------------------------------------


def test_shortening_a_carried_line_in_a_new_daily_file_passes(kb_repo) -> None:
    _setup(kb_repo, day1=_long(600) + "\n")
    kb_repo.stage(DAY2, _long(550) + "\n")
    assert _findings(kb_repo) == []


def test_a_new_long_line_in_a_new_daily_file_blocks(kb_repo) -> None:
    _setup(kb_repo, day1=_long(600) + "\n")
    kb_repo.stage(DAY2, _long(600) + "\n" + _long(550, "beta") + "\n")
    assert _findings(kb_repo) == [(DAY2, "mega-line")]


def test_a_baseline_line_excuses_edits_not_moves(kb_repo) -> None:
    """It stays in the earlier file, so putting it in a note is a copy."""
    line = _long()
    _setup(kb_repo, day1=line + "\n")
    kb_repo.stage(DAY2, "today\n")
    kb_repo.stage(NOTE, line + "\n")
    assert (NOTE, "mega-line") in _findings(kb_repo)


# ----- the commit-wide diff -----------------------------------------------------


def test_staged_line_changes_covers_edits_renames_deletes_and_new_files(kb_repo) -> None:
    body = "".join(f"line {i}\n" for i in range(40))
    _setup(kb_repo, page="old\nkeep\n", note=body, other="gone\n")
    kb_repo.stage(PAGE, "new\nkeep\n")
    kb_repo.git("mv", NOTE, "knowledge-base/projects/acme-pilot/renamed.md")
    kb_repo.git("rm", "-q", OTHER)
    kb_repo.stage(DAY2, "fresh\n")
    changes = {(c.old_rel, c.new_rel): (c.removed, c.added) for c in staged_line_changes(kb_repo.root)}
    assert changes == {
        (None, DAY2): ([], ["fresh"]),
        (PAGE, PAGE): (["old"], ["new"]),
        (OTHER, None): (["gone"], []),
    }


def test_staged_line_changes_reads_lines_that_look_like_diff_headers(kb_repo) -> None:
    _setup(kb_repo, page="-- a/fake.md\n++ b/fake.md\nkeep\n")
    kb_repo.stage(PAGE, "keep\n++ b/other.md\n@@ -1 +1 @@\n")
    [change] = staged_line_changes(kb_repo.root)
    assert (change.old_rel, change.new_rel) == (PAGE, PAGE)
    assert change.removed == ["-- a/fake.md", "++ b/fake.md"]
    assert change.added == ["++ b/other.md", "@@ -1 +1 @@"]


def test_staged_line_changes_holds_up_against_git_config_and_odd_bytes(kb_repo) -> None:
    kb_repo.git("config", "diff.noprefix", "true")
    kb_repo.git("config", "diff.mnemonicPrefix", "true")
    odd = 'knowledge-base/topics/a "quoted" name.md'
    _setup(kb_repo, page="old\n")
    kb_repo.stage(PAGE, "new\n")
    path = kb_repo.root / odd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"caf\xe9 line\n")
    kb_repo.git("add", "--", odd)
    changes = {c.new_rel: (c.old_rel, c.added) for c in staged_line_changes(kb_repo.root)}
    assert changes[PAGE] == (PAGE, ["new"])
    assert changes[odd] == (None, ["caf\N{REPLACEMENT CHARACTER} line"])


def test_staged_line_changes_is_empty_with_nothing_staged(kb_repo) -> None:
    assert staged_line_changes(kb_repo.root) == []


def test_a_clean_commit_never_reads_the_commit_wide_diff(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    def boom(_repo: object) -> None:
        raise AssertionError("read the commit-wide diff for a clean commit")

    monkeypatch.setattr(lint, "staged_line_changes", boom)
    _setup(kb_repo, page="short\n")
    kb_repo.stage(PAGE, "short and sweet\n")
    assert _findings(kb_repo) == []

"""`scoutctl wishlist check-log-move`: a log leaving a wishlist card lands verbatim in its progress note.

The wishlist phase (Step 3d) keeps the card to the request and a `## Status`
section; dated entries move to `docs/wishlist/progress/<item file name>`,
verbatim, in the same commit. These build a throwaway vault, commit a card with
a log, edit the working tree the way a run would, and judge it before commit.
All content is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.wishlist.cli import app
from scout.wishlist.log_move import (
    LogMoveCheckError,
    check_log_move,
    dated_lines,
    near_miss,
    removed_dated_lines,
)

CARD = "docs/wishlist/2026-01-05-decision-template.md"
NOTE = "docs/wishlist/progress/2026-01-05-decision-template.md"

FRONT = """---
title: Decision template
status: in-progress
priority: medium
date: 2026-01-05
---
"""

REQUEST = "Add a KB template for architecture decisions, linked from the index.\n"

LOG = """## Progress — 2026-01-07
- Linked the template from the index.
- Verified the link resolves.

## 2026-01-05
Created `templates/decision.md`.
- [x] Draft the template — completed 2026-01-05
"""

STATUS = """## Status
Two of four sub-tasks done; the index link is next.
- [ ] Backfill the three existing decisions

[[docs/wishlist/progress/2026-01-05-decision-template|Progress log]]
"""

LOG_LINES = [line for line in LOG.split("\n") if line.strip()]


def _card(*parts: str) -> str:
    return FRONT + "\n" + "\n".join(parts)


@pytest.fixture
def vault(kb_repo):  # type: ignore[no-untyped-def]
    """A committed card that still carries its log in the body, and no progress note yet."""
    kb_repo.stage(CARD, _card(REQUEST, STATUS, LOG))
    kb_repo.commit("card with a log")
    return kb_repo


def _move(vault, note_text: str, card_text: str | None = None) -> None:  # type: ignore[no-untyped-def]
    vault.write(CARD, card_text if card_text is not None else _card(REQUEST, STATUS))
    vault.write(NOTE, note_text)


# ---- what counts as a dated log line -------------------------------------------------------------


def test_dated_lines_are_dated_sections_and_dated_entries_not_frontmatter_or_the_link() -> None:
    lines = _card(REQUEST, STATUS, LOG).split("\n")
    assert [lines[i] for i in sorted(dated_lines(lines))] == LOG_LINES


def test_a_dated_section_ends_at_the_next_heading_of_its_level() -> None:
    lines = ["## 2026-02-01", "- did a thing", "### Verification", "- checked", "## Status", "- [ ] next"]
    assert sorted(dated_lines(lines)) == [0, 1, 2, 3]


def test_dated_entries_outside_a_dated_section() -> None:
    lines = [
        "- 2026-02-01: shipped the first cut",
        "**2026-02-02** — reviewed",
        "- [x] Wrote the index entry (2026-02-03)",
        "- [ ] Due 2026-03-01, still open",  # an open sub-task with a deadline is not a log line
        "Requested 2026-01-05 by Alex.",  # request prose that mentions a date is not a log line
        "See [[docs/wishlist/progress/2026-01-05-x|Progress log]].",
    ]
    assert sorted(dated_lines(lines)) == [0, 1, 2]


def test_a_heading_that_only_mentions_a_date_is_not_a_log_heading() -> None:
    lines = [
        "# Prep for the 2026-12-01 offsite",
        "Book the room.",
        "## Status (updated 2026-11-18)",
        "Half done.",
        "## Status — 2026-11-18",
        "Still half done.",
        "## Ideas for 2026-12-01",
        "- a thing",
    ]
    assert dated_lines(lines) == set()


def test_log_headings_put_the_date_first_or_after_a_short_label() -> None:
    lines = ["## Run 3: 2026-01-05", "- did a", "### 2026-01-06 — verified", "- ok", "## Status", "x"]
    assert sorted(dated_lines(lines)) == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "heading",
    [
        "## Run 2026-11-18",
        "## Session 4 2026-11-18",
        "## Dreaming 2026-11-18",
        "## Update (2026-11-18)",
        "## Dreaming run 3 — 2026-11-18",
        "## log: 2026-11-18",
    ],
)
def test_label_headings_without_a_separator_are_log_headings(heading: str) -> None:
    assert sorted(dated_lines([heading, "Undated work under it."])) == [0, 1]


def test_a_fence_after_a_dated_entry_and_a_blank_line_ends_the_entry() -> None:
    lines = ["- 2026-11-18: did X", "", "```", "not part of the entry", "```", "Plain prose after."]
    assert sorted(dated_lines(lines)) == [0]


def test_a_fence_indented_under_a_dated_entry_belongs_to_it() -> None:
    lines = ["- 2026-11-18: did X", "", "  ```", "  output", "  ```", "Plain prose after."]
    assert sorted(dated_lines(lines)) == [0, 2, 3, 4]


def test_a_dated_entry_takes_its_sub_bullets_and_continuation_lines() -> None:
    lines = [
        "- 2026-11-18: did X",
        "  - sub-step one",
        "  - sub-step two",
        "    detail of step two",
        "",
        "  - after a blank, still nested",
        "- [ ] Next open sub-task",
        "  - its own child",
        "- [x] Drafted 2026-11-17",
        "  wrapped continuation",
        "",
        "A new paragraph.",
    ]
    assert sorted(dated_lines(lines)) == [0, 1, 2, 3, 5, 8, 9]


def test_a_dated_line_moved_within_the_card_was_not_removed() -> None:
    old = ["## Status", "- [x] Draft it — completed 2026-01-05", "- [ ] Link it"]
    new = ["## Status", "- [ ] Link it", "- [x] Draft it — completed 2026-01-05"]
    assert removed_dated_lines(old, new) == []


# ---- the required cases ----------------------------------------------------------------------------


def test_a_verbatim_move_passes(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, "# Progress log\n\n" + LOG)
    result = check_log_move(data_dir=vault.root)
    assert result.passed
    assert result.checked == [CARD] and result.moved == len(LOG_LINES)


def test_a_reworded_line_fails_naming_the_item_and_the_line(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, LOG.replace("- Verified the link resolves.", "- Checked the link."))
    result = check_log_move(data_dir=vault.root)
    (finding,) = result.findings
    assert finding.item == "2026-01-05-decision-template.md"
    assert finding.note == NOTE
    assert finding.missing == "- Verified the link resolves."


def test_a_dropped_line_fails(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, LOG.replace("Created `templates/decision.md`.\n", ""))
    (finding,) = check_log_move(data_dir=vault.root).findings
    assert finding.missing == "Created `templates/decision.md`."


def test_a_dropped_log_with_no_progress_note_fails(vault) -> None:  # type: ignore[no-untyped-def]
    vault.write(CARD, _card(REQUEST, STATUS))
    (finding,) = check_log_move(data_dir=vault.root).findings
    assert finding.missing == "## Progress — 2026-01-07"


def test_reordering_fails(vault) -> None:  # type: ignore[no-untyped-def]
    newest, oldest = LOG.split("\n\n", 1)
    _move(vault, oldest + "\n" + newest + "\n")
    result = check_log_move(data_dir=vault.root)
    assert not result.passed
    # The note holds the 2026-01-05 section above the 2026-01-07 one, so once the card's first section is
    # matched, the note has nothing left below it for the card's 2026-01-05 heading.
    assert result.findings[0].missing == "## 2026-01-05"


def test_unrelated_edits_pass(vault) -> None:  # type: ignore[no-untyped-def]
    edited = _card(REQUEST.replace("architecture", "design"), STATUS.replace("Two of four", "Three of four"), LOG)
    vault.write(CARD, edited.replace("status: in-progress", "status: done"))
    vault.write("docs/wishlist/2026-02-01-new-item.md", FRONT + "\nA brand-new item.\n")
    result = check_log_move(data_dir=vault.root)
    assert result.passed and result.moved == 0


def test_a_status_heading_with_a_date_can_be_rewritten(kb_repo) -> None:  # type: ignore[no-untyped-def]
    """A Status heading that carries a date is the card's status, not its log: rewriting it every run passes."""
    status = "## Status (updated 2026-11-18)\nTwo of four sub-tasks done.\n- [ ] Backfill the decisions\n"
    kb_repo.stage(CARD, _card(REQUEST, status))
    kb_repo.commit("card")
    kb_repo.write(CARD, _card(REQUEST, "## Status (updated 2026-11-19)\nThree of four done.\n"))
    assert check_log_move(data_dir=kb_repo.root).passed


def test_a_dropped_sub_bullet_of_a_dated_entry_fails(kb_repo) -> None:  # type: ignore[no-untyped-def]
    entry = "- 2026-11-18: wired the template\n  - linked it from the index\n  - checked the link\n"
    kb_repo.stage(CARD, _card(REQUEST, STATUS, entry))
    kb_repo.commit("card")
    kb_repo.write(CARD, _card(REQUEST, STATUS))
    kb_repo.write(NOTE, entry.replace("  - checked the link\n", ""))
    (finding,) = check_log_move(data_dir=kb_repo.root).findings
    assert finding.missing == "  - checked the link"
    kb_repo.write(NOTE, entry)
    assert check_log_move(data_dir=kb_repo.root).passed


def test_a_line_differing_only_in_invisible_characters_is_named_as_such(vault) -> None:  # type: ignore[no-untyped-def]
    vault.stage(CARD, _card(REQUEST, STATUS, "## 2026-02-01\nShipped it.  \n- [x] Done today 2026-02-01\n"))
    vault.commit("hard break and nbsp")
    vault.write(CARD, _card(REQUEST, STATUS))
    vault.write(NOTE, "## 2026-02-01\nShipped it.\n- [x] Done today 2026-02-01\n")
    (finding,) = check_log_move(data_dir=vault.root).findings
    assert finding.missing == "Shipped it.  " and finding.near == "Shipped it."

    out = CliRunner().invoke(app, ["check-log-move", "--data-dir", str(vault.root)]).stdout
    assert "'Shipped it.  '" in out and "differing only in invisible characters" in out


def test_a_visible_difference_is_not_called_invisible() -> None:
    assert near_miss("Done today.  ", ["Done today."]) == "Done today."
    assert near_miss("Waited…", ["Waited..."]) is None  # NFKC would fold these; they look different
    assert near_miss("ﬁxed the ﬂag", ["fixed the flag"]) is None


# ---- the commit boundary ----------------------------------------------------------------------------


def test_directories_in_paths_cover_what_is_under_them(vault) -> None:  # type: ignore[no-untyped-def]
    """git-safe-commit.sh takes directories; so does the check."""
    _move(vault, LOG)
    assert check_log_move(data_dir=vault.root, paths=[CARD, "docs/wishlist/progress/"]).passed
    assert check_log_move(data_dir=vault.root, paths=["docs/wishlist"]).passed
    assert check_log_move(data_dir=vault.root, paths=["docs/wishlist"]).checked == [CARD]
    (finding,) = check_log_move(data_dir=vault.root, paths=["docs/wishlist/2026-01-05-decision-template.md"]).findings
    assert finding.missing == "## Progress — 2026-01-07"
    assert check_log_move(data_dir=vault.root, paths=["."]).passed


def test_a_progress_note_left_out_of_the_commit_does_not_count(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, LOG)
    assert check_log_move(data_dir=vault.root, paths=[CARD, NOTE]).passed
    (finding,) = check_log_move(data_dir=vault.root, paths=[CARD]).findings
    assert finding.missing == "## Progress — 2026-01-07"


def test_only_cards_in_the_commit_are_checked(vault) -> None:  # type: ignore[no-untyped-def]
    vault.write(CARD, _card(REQUEST, STATUS))  # the log dropped, but this card isn't being committed
    assert check_log_move(data_dir=vault.root, paths=["docs/wishlist/other.md"]).checked == []


def test_lines_the_note_already_holds_count(vault) -> None:  # type: ignore[no-untyped-def]
    """An earlier run copied the log without removing it; this run only removes it."""
    vault.stage(NOTE, LOG)
    vault.commit("copied earlier")
    vault.write(CARD, _card(REQUEST, STATUS))
    assert check_log_move(data_dir=vault.root, paths=[CARD]).passed


def test_new_entries_on_top_of_the_moved_log_pass(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, "## 2026-01-09\n- This run's work.\n\n" + LOG)
    assert check_log_move(data_dir=vault.root).passed


def test_a_deleted_card_must_have_moved_its_log(vault) -> None:  # type: ignore[no-untyped-def]
    (vault.root / CARD).unlink()
    assert not check_log_move(data_dir=vault.root).passed
    vault.write(NOTE, LOG)
    assert check_log_move(data_dir=vault.root).passed


def test_byte_for_byte_trailing_whitespace_counts(vault) -> None:  # type: ignore[no-untyped-def]
    _move(vault, LOG.replace("- Linked the template from the index.", "- Linked the template from the index. "))
    assert not check_log_move(data_dir=vault.root).passed


# ---- cannot check -------------------------------------------------------------------------------------


def test_not_a_git_repo_cannot_check(tmp_path: Path) -> None:
    (tmp_path / "docs" / "wishlist").mkdir(parents=True)
    with pytest.raises(LogMoveCheckError):
        check_log_move(data_dir=tmp_path)


def test_an_undecodable_card_cannot_check(vault) -> None:  # type: ignore[no-untyped-def]
    (vault.root / CARD).write_bytes(b"\xff\xfe not utf-8\n")
    with pytest.raises(LogMoveCheckError):
        check_log_move(data_dir=vault.root)


def test_a_vault_with_no_wishlist_passes(kb_repo) -> None:  # type: ignore[no-untyped-def]
    result = check_log_move(data_dir=kb_repo.root)
    assert result.passed and result.checked == []


# ---- CLI ------------------------------------------------------------------------------------------------


def test_cli_exit_codes_and_message(vault) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    args = ["check-log-move", "--data-dir", str(vault.root), CARD, NOTE]

    _move(vault, LOG.replace("- Verified the link resolves.", "- Checked the link."))
    failed = runner.invoke(app, args)
    assert failed.exit_code == 1
    assert "2026-01-05-decision-template.md" in failed.stdout
    assert "- Verified the link resolves." in failed.stdout

    vault.write(NOTE, LOG)
    passed = runner.invoke(app, args)
    assert passed.exit_code == 0, passed.stdout
    assert f"{len(LOG_LINES)} dated line" in passed.stdout


def test_cli_json(vault) -> None:  # type: ignore[no-untyped-def]
    vault.write(CARD, _card(REQUEST, STATUS))
    result = CliRunner().invoke(app, ["check-log-move", "--data-dir", str(vault.root), "--json"])
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert payload["findings"][0]["item"] == "2026-01-05-decision-template.md"


def test_cli_cannot_check_exits_2(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["check-log-move", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "check-log-move: cannot check" in result.stdout


def test_cli_an_unexpected_error_exits_2_not_1(vault, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import scout.wishlist.log_move as log_move

    def boom(**_kw: object) -> None:
        raise RuntimeError("surprise")

    monkeypatch.setattr(log_move, "check_log_move", boom)
    result = CliRunner().invoke(app, ["check-log-move", "--data-dir", str(vault.root)])
    assert result.exit_code == 2
    assert "cannot check — internal error: RuntimeError: surprise" in result.stdout


def test_cli_help_states_exit_codes() -> None:
    result = CliRunner().invoke(app, ["check-log-move", "--help"])
    assert result.exit_code == 0 and "Exit codes: 0" in result.stdout

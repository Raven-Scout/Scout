"""Content shape the app can show: short task titles, wishlist cards without logs, live-only KB health.

The app UI audit (2026-11) found action-item titles written as paragraphs,
wishlist cards carrying their whole progress log, and KB health counted over
archive copies and receipts. These pin the rules that fix it in the phase
text, and that the command a phase tells a run to use exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.titles import FOCUS_MAX_BULLETS, FOCUS_MAX_CHARS, LEGACY_PER_RUN, TITLE_MAX
from scout.scripts.phase_assembly import parse_phase_file

PLUGIN = Path(__file__).resolve().parents[3]
ACTION_ITEMS = PLUGIN / "phases" / "core" / "action-items.md"
WISHLIST = PLUGIN / "phases" / "modes" / "wishlist.md"
STATUS = PLUGIN / "commands" / "scout-status.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_action_items_phase_states_the_title_limit_the_check_enforces() -> None:
    text = _text(ACTION_ITEMS)
    assert f"at most {TITLE_MAX} characters" in text
    assert f"≤ {TITLE_MAX} chars" in text  # the file-format template
    assert f"the {LEGACY_PER_RUN} longest carried 🔴 titles" in text
    assert f"over {FOCUS_MAX_BULLETS} bullets" in text and f"over {FOCUS_MAX_CHARS} characters" in text


def test_action_items_phase_runs_check_titles_before_commit() -> None:
    text = _text(ACTION_ITEMS)
    assert "scripts/scoutctl action-items check-titles --data-dir {{SCOUT_DIR}}` until it exits 0" in text
    # Carried titles are left alone: a rewording with no news would restart the stale clock.
    assert "Leave other carried titles until a run touches the item for a real reason" in text


def test_check_titles_is_a_real_subcommand() -> None:
    result = CliRunner().invoke(app, ["check-titles", "--help"])
    assert result.exit_code == 0 and "Exit codes: 0" in result.stdout


def test_action_items_phase_still_reaches_briefing_and_consolidation() -> None:
    (section,) = parse_phase_file(ACTION_ITEMS)
    assert set(section.mode) >= {"briefing", "consolidation"}


def test_wishlist_cards_keep_status_and_link_a_progress_note() -> None:
    text = _text(WISHLIST)
    assert "**The item file is a card, not a log.**" in text
    assert "`## Status`" in text
    assert "`docs/wishlist/progress/<item file name>`" in text
    assert "[[docs/wishlist/progress/<item stem>|Progress log]]" in text
    # A log is moved, never dropped.
    assert "Never delete a log" in text and "move it verbatim to the note in the same commit" in text


def test_wishlist_phase_reads_items_but_not_their_progress_notes() -> None:
    assert "`docs/wishlist/` (not its `progress/` folder)" in _text(WISHLIST)


def test_wishlist_phase_no_longer_appends_dated_notes_to_the_card() -> None:
    text = _text(WISHLIST)
    assert not re.search(r"add a brief \"what was delivered\" note \(with the date\) to the body", text)
    assert "- [x] Sub-task 1 — completed [date]" not in text


def test_scout_status_sets_archives_and_receipts_aside() -> None:
    text = _text(STATUS)
    assert "folder named `archive` or `archived`" in text
    assert "`receipt`, `receipts`, `invoice` or `invoices`" in text
    assert "Not counted:    A archived, R receipts" in text

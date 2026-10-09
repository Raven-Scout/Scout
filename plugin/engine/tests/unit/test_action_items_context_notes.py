"""Unit tests for scout.action_items.context_notes — per-item context notes."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.context_notes import (
    backfill_context,
    context_note_rel,
    render_note,
    snapshot_blocks,
)

runner = CliRunner()

SNAPSHOT = """# Action Items — Monday, Oct 5, 2026

## 🔴 Urgent

- [ ] [#DETX] **Order the roadmap items before the review**
  - 🗓️ Due Tuesday and it has never left Todo.
  - 🔑 The other three wait on the customer; this one does not.

- [ ] [#NESTX] **Plan the cycle with Priya**
  - parent context
  - [ ] [#LBL] **Book the room**
    - child context
  - more parent context
Section prose that ends the block.

- [ ] [#DETX] **A second row that reused the tag**
  - must not win
- [ ] [#OLDTG] **Item that was re-tagged later**
  - context kept under the old tag
"""

DAILY = """# Action Items — Tuesday, Oct 6, 2026

## 🔴 Urgent

- [ ] [#DETX] **Order the roadmap items before the review** — due today …
- [ ] [#NESTX] **Plan the cycle with Priya** …
  - [ ] [#LBL] **Book the room**
- [ ] [#NEWTG] **Item that was re-tagged later** _(re-tagged from `[#OLDTG]` on 2026-10-05)_
- [ ] [#PLN] **Brand-new item with no snapshot entry**
- [x] [#SAM1] **Done item is ignored**
"""

BACKLOG = """# Backlog

## 🟢 Watching

- [ ] [#PLN2] **Backlog item** — watching
"""


def _vault(tmp_path: Path, *, backlog: str | None = BACKLOG, config: str | None = None) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    ai = vault / "action-items"
    (ai / "archive").mkdir(parents=True)
    snap = ai / "archive" / "action-items-2026-10-05-pre-restructure.md"
    snap.write_text(SNAPSHOT + "- [ ] [#PLN2] **Backlog item**\n  - backlog context\n", encoding="utf-8")
    daily = ai / "action-items-2026-10-06.md"
    daily.write_text(DAILY, encoding="utf-8")
    if backlog is not None:
        (ai / "backlog.md").write_text(backlog, encoding="utf-8")
    if config is not None:
        (vault / "scout-config.yaml").write_text(config, encoding="utf-8")
    return vault, snap, daily


def test_context_note_rel() -> None:
    assert context_note_rel("DETX") == "action-items/context/DETX.md"


def test_first_occurrence_wins() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["DETX"][0].startswith("- [ ] [#DETX] **Order the roadmap")
    assert all("must not win" not in ln for ln in blocks["DETX"])


def test_nested_child_gets_its_own_block() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["LBL"] == ["  - [ ] [#LBL] **Book the room**", "    - child context"]
    assert "  - [ ] [#LBL] **Book the room**" in blocks["NESTX"]
    assert blocks["NESTX"][-1] == "  - more parent context"


def test_block_ends_at_column_zero_and_drops_trailing_blanks() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["DETX"][-1] == "  - 🔑 The other three wait on the customer; this one does not."
    assert all("Section prose" not in ln for ln in blocks["NESTX"])


def test_render_note_frontmatter_and_verbatim_body() -> None:
    text = render_note("DETX", 'Order "the" items', "2026-10-05", ["- [ ] [#DETX] x", "  - y"])
    assert text.startswith(
        '---\ntag: DETX\ntitle: "Order \\"the\\" items"\ncreated: 2026-10-05\nupdated: 2026-10-05\n---\n'
    )
    assert "Restored verbatim from the 2026-10-05 pre-restructure snapshot." in text
    assert text.endswith("- [ ] [#DETX] x\n  - y\n")


def test_backfill_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=False)
    assert sorted(report.to_write) == ["DETX", "LBL", "NESTX", "NEWTG", "PLN2"]
    assert report.missing == ["PLN"]
    assert not (vault / "action-items" / "context").exists()


def test_backfill_write_creates_notes(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("DETX")).read_text(encoding="utf-8")
    assert 'title: "Order the roadmap items before the review"' in note
    assert "  - 🗓️ Due Tuesday and it has never left Todo." in note
    assert (vault / context_note_rel("PLN2")).exists()  # backlog item


def test_retag_marker_finds_old_block(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("NEWTG")).read_text(encoding="utf-8")
    assert "tag: NEWTG" in note
    assert "context kept under the old tag" in note


def test_never_overwrites_existing_note(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    existing = vault / context_note_rel("DETX")
    existing.parent.mkdir(parents=True)
    existing.write_text("written by a session\n", encoding="utf-8")
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    assert report.existing == ["DETX"]
    assert existing.read_text(encoding="utf-8") == "written by a session\n"


def test_second_run_writes_nothing(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    again = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    assert again.to_write == []
    assert sorted(again.existing) == ["DETX", "LBL", "NESTX", "NEWTG", "PLN2"]


def test_over_budget_written_in_full_and_reported(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, config="kb_budgets:\n  'action-items/context/**': 200\n")
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    tags = {t for t, _ in report.over_budget}
    assert "DETX" in tags
    assert "🔑 The other three" in (vault / context_note_rel("DETX")).read_text(encoding="utf-8")


def test_unbolded_title_and_undated_snapshot(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    ai = vault / "action-items"
    ai.mkdir(parents=True)
    snap = ai / "snapshot.md"
    snap.write_text("- [ ] [#PLN] plain line with no bold\n  - ctx\n", encoding="utf-8")
    daily = ai / "action-items-2026-10-06.md"
    daily.write_text("- [ ] [#PLN] plain line with no bold\n", encoding="utf-8")
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("PLN")).read_text(encoding="utf-8")
    assert 'title: "plain line with no bold"' in note
    assert "created: \n" in note


def test_untagged_lines_and_repeated_tags_are_skipped(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, backlog="- [ ] [#DETX] **Same tag again in the backlog**\n")
    daily.write_text(DAILY + "- [ ] **Untagged open line**\n", encoding="utf-8")
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=False)
    assert report.to_write.count("DETX") == 1
    assert sorted(report.to_write) == ["DETX", "LBL", "NESTX", "NEWTG"]


def test_missing_backlog_is_fine(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, backlog=None)
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=False)
    assert "PLN2" not in report.to_write


def test_cli_dry_run_then_write(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, config="kb_budgets:\n  'action-items/context/**': 200\n")
    dry = runner.invoke(app, ["backfill-context", "--from", str(snap), "--daily", str(daily)])
    assert dry.exit_code == 0, dry.output
    assert "would write 5 note(s)" in dry.stdout
    assert "no snapshot entry: PLN" in dry.stdout
    assert "'action-items/context/DETX.md': null" in dry.stdout
    assert not (vault / "action-items" / "context").exists()
    wrote = runner.invoke(app, ["backfill-context", "--from", str(snap), "--daily", str(daily), "--write"])
    assert wrote.exit_code == 0, wrote.output
    assert "wrote 5 note(s)" in wrote.stdout
    assert (vault / context_note_rel("LBL")).exists()


def test_cli_missing_snapshot_exits_nonzero(tmp_path: Path) -> None:
    vault, _, daily = _vault(tmp_path)
    result = runner.invoke(app, ["backfill-context", "--from", str(vault / "nope.md"), "--daily", str(daily)])
    assert result.exit_code == 2

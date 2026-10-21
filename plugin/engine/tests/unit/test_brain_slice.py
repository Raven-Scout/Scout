"""Mode markers and the per-run slice of SKILL.md (#84, scout/scripts/brain_slice.py).

The shipped-assembly properties (every section lands in the right slices, the safety
rules reach every mode) live in test_brain_slice_assembly.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.brain_slice import (
    CLOSE_MARKER,
    SLICE_DIR,
    MarkerError,
    join_marked,
    only_classes,
    slice_text,
    slot_class,
    strip_markers,
    write_slice,
)

BOTH = {"briefing", "consolidation"}
B, C = "<!-- scout:only briefing -->", "<!-- scout:only consolidation -->"


def _brain(*entries: tuple[str, tuple[str, ...] | None]) -> str:
    return "\n\n".join(["# SKILL", *join_marked(list(entries))]) + "\n"


SAMPLE = _brain(
    ("## Shared A\na", None),
    ("## Briefing only\nb", ("briefing",)),
    ("## Consolidation only\nc", ("consolidation",)),
    ("## Shared D\nd", None),
    ("## Briefing tail\ne", ("briefing",)),
)


# ---------- only_classes ----------


@pytest.mark.parametrize(
    ("modes", "target", "expected"),
    [
        ([], BOTH, None),  # no mode: every target
        (["briefing", "consolidation"], BOTH, None),  # runs in every mode the target serves
        (["consolidation", "briefing", "research"], BOTH, None),
        (["briefing"], BOTH, ("briefing",)),
        (["consolidation", "research"], BOTH, ("consolidation",)),
        (["dreaming"], {"dreaming"}, None),  # single-mode targets are never marked
        (["research"], BOTH, None),  # not in this target at all: select_sections drops it
    ],
)
def test_only_classes(modes: list[str], target: set[str], expected: tuple[str, ...] | None) -> None:
    assert only_classes(modes, target) == expected


# ---------- join_marked: the layout ----------


def test_close_marker_follows_the_blank_line_so_section_appends_merge_cleanly() -> None:
    assert SAMPLE == (
        "# SKILL\n\n## Shared A\na\n\n"
        f"{B}\n## Briefing only\nb\n\n"
        f"{CLOSE_MARKER}\n{C}\n## Consolidation only\nc\n\n"
        f"{CLOSE_MARKER}\n## Shared D\nd\n\n"
        f"{B}\n## Briefing tail\ne\n\n"
        f"{CLOSE_MARKER}\n"
    )


def test_strip_markers_gives_back_the_unmarked_assembly() -> None:
    unmarked = "\n\n".join(["# SKILL", "## Shared A\na", "## Briefing only\nb", "## Consolidation only\nc",
                            "## Shared D\nd", "## Briefing tail\ne"]) + "\n\n"  # fmt: skip
    assert strip_markers(SAMPLE) == unmarked


# ---------- slice_text ----------


def test_briefing_slice_drops_consolidation_blocks_and_every_marker() -> None:
    cut = slice_text(SAMPLE, "briefing")
    assert cut.text == "# SKILL\n\n## Shared A\na\n\n## Briefing only\nb\n\n## Shared D\nd\n\n## Briefing tail\ne\n\n"
    assert (cut.marked_blocks, cut.dropped_blocks) == (3, 1)


def test_consolidation_slice_keeps_one_blank_line_between_sections() -> None:
    cut = slice_text(SAMPLE, "consolidation")
    assert cut.text == "# SKILL\n\n## Shared A\na\n\n## Consolidation only\nc\n\n## Shared D\nd\n\n"
    assert (cut.marked_blocks, cut.dropped_blocks) == (3, 2)


def test_a_vault_rule_appended_to_a_block_stays_in_that_block() -> None:
    edited = SAMPLE.replace("## Consolidation only\nc\n", "## Consolidation only\nc\nvault rule\n")
    assert "vault rule" not in slice_text(edited, "briefing").text
    assert "vault rule" in slice_text(edited, "consolidation").text


def test_a_block_can_name_both_classes() -> None:
    text = f"x\n<!-- scout:only briefing consolidation -->\ny\n{CLOSE_MARKER}\n"
    assert slice_text(text, "briefing").text == slice_text(text, "consolidation").text == "x\ny\n"


def test_unmarked_text_slices_to_itself() -> None:
    cut = slice_text("# SKILL\n\nall of it\n", "briefing")
    assert (cut.text, cut.marked_blocks, cut.dropped_blocks) == ("# SKILL\n\nall of it\n", 0, 0)


def test_crlf_marker_lines_still_count() -> None:
    text = f"a\r\n{B}\r\nb\r\n{CLOSE_MARKER}\r\nc\r\n"
    assert slice_text(text, "consolidation").text == "a\r\nc\r\n"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (f"a\n{B}\nb\n", "never closed"),
        (f"a\n{CLOSE_MARKER}\nb\n", "without an open"),
        (f"{B}\n{C}\nx\n{CLOSE_MARKER}\n{CLOSE_MARKER}\n", "nested"),
        ("<!-- scout:only weekend -->\nx\n<!-- /scout:only -->\n", "unknown mode class"),
        ("<!-- scout:only Briefing -->\nx\n<!-- /scout:only -->\n", "malformed"),
        ("<!--scout:only briefing-->\nx\n<!-- /scout:only -->\n", "malformed"),
        ("  <!-- /scout:only -->\n", "malformed"),
    ],
)
def test_damaged_markers_refuse_to_slice(text: str, why: str) -> None:
    with pytest.raises(MarkerError, match=why):
        slice_text(text, "briefing")


def test_unknown_mode_class_is_refused() -> None:
    with pytest.raises(MarkerError):
        slice_text(SAMPLE, "dreaming")


def test_prose_mentioning_markers_mid_line_is_kept() -> None:
    text = "Markers look like `<!-- scout:only briefing -->` in SKILL.md.\n"
    assert slice_text(text, "consolidation").text == text


# ---------- slot_class ----------


@pytest.mark.parametrize(
    ("slot", "cls"),
    [
        ("morning-briefing", "briefing"),
        ("weekend-briefing", "briefing"),
        ("morning-consolidation", "consolidation"),
        ("midday-consolidation", "consolidation"),
        ("afternoon-consolidation", "consolidation"),
        ("evening-consolidation", "consolidation"),
        ("manual", None),
        (None, None),
        ("", None),
        ("dreaming-nightly", None),
        ("research", None),
    ],
)
def test_slot_class(slot: str | None, cls: str | None) -> None:
    assert slot_class(slot) == cls


# ---------- write_slice: the runner's view, with its fail-safes ----------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "SKILL.md").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def test_write_slice_writes_a_read_only_view_and_returns_it(vault: Path) -> None:
    read, line = write_slice(vault, "SKILL", "morning-consolidation")
    assert read == vault / SLICE_DIR / "SKILL.md"
    text = read.read_text(encoding="utf-8")
    assert text.startswith("> **Read-only view.**") and str(vault / "SKILL.md") in text.splitlines()[0]
    assert text.endswith(slice_text(SAMPLE, "consolidation").text)
    assert line.startswith("brain slice: SKILL ") and "(consolidation, morning-consolidation)" in line
    assert (vault / "SKILL.md").read_text(encoding="utf-8") == SAMPLE  # the live file is never touched


@pytest.mark.parametrize("slot", ["manual", None, "dreaming-nightly"])
def test_write_slice_reads_the_whole_file_for_a_slot_with_no_mode_class(vault: Path, slot: str | None) -> None:
    stale = vault / SLICE_DIR / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("an earlier run's slice", encoding="utf-8")
    read, line = write_slice(vault, "SKILL", slot)
    assert read == vault / "SKILL.md"
    assert not stale.exists(), "a stale slice must never outlive the run that wrote it"
    assert "reads the whole file" in line


def test_write_slice_falls_back_to_the_whole_file_on_damaged_markers(vault: Path) -> None:
    (vault / "SKILL.md").write_text(SAMPLE.replace(CLOSE_MARKER, "", 1), encoding="utf-8")
    read, line = write_slice(vault, "SKILL", "morning-briefing")
    assert read == vault / "SKILL.md"
    assert "MarkerError" in line and not (vault / SLICE_DIR / "SKILL.md").exists()


def test_write_slice_reads_an_unmarked_file_whole(vault: Path) -> None:
    (vault / "SKILL.md").write_text("# SKILL\n\na vault's own restructured brain\n", encoding="utf-8")
    read, line = write_slice(vault, "SKILL", "morning-briefing")
    assert read == vault / "SKILL.md" and "marked blocks: 0" in line


def test_write_slice_survives_a_missing_brain_file(tmp_path: Path) -> None:
    read, line = write_slice(tmp_path, "SKILL", "morning-briefing")
    assert read == tmp_path / "SKILL.md" and "FileNotFoundError" in line


def test_write_slice_survives_an_unwritable_cache(vault: Path) -> None:
    (vault / ".scout-cache").write_text("a file where the cache dir should be", encoding="utf-8")
    read, line = write_slice(vault, "SKILL", "morning-briefing")
    assert read == vault / "SKILL.md" and "brain slice: none" in line


# ---------- the CLI ----------

runner = CliRunner()


def test_cli_brain_slice_prints_the_path_and_logs_the_saving(vault: Path) -> None:
    res = runner.invoke(app, ["brain", "slice", "--slot", "morning-briefing", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == str(vault / SLICE_DIR / "SKILL.md")
    assert "brain slice: SKILL" in res.stderr


def test_cli_brain_slice_never_fails_the_run(tmp_path: Path) -> None:
    res = runner.invoke(app, ["brain", "slice", "--slot", "morning-briefing", "--vault", str(tmp_path)])
    assert res.exit_code == 0
    assert res.stdout.strip() == str(tmp_path / "SKILL.md")


def test_cli_brain_check_reports_each_mode(vault: Path) -> None:
    res = runner.invoke(app, ["brain", "check", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "briefing: ~" in res.stdout and "(1 of 3 blocks dropped)" in res.stdout
    assert "consolidation: ~" in res.stdout and "(2 of 3 blocks dropped)" in res.stdout


def test_cli_brain_check_fails_on_damaged_markers(vault: Path) -> None:
    (vault / "SKILL.md").write_text(SAMPLE + B + "\n", encoding="utf-8")
    res = runner.invoke(app, ["brain", "check", "--vault", str(vault)])
    assert res.exit_code == 1 and "markers damaged" in res.stderr


def test_cli_brain_check_on_an_unmarked_vault(vault: Path) -> None:
    (vault / "SKILL.md").write_text("# SKILL\n\nrestructured\n", encoding="utf-8")
    res = runner.invoke(app, ["brain", "check", "--vault", str(vault)])
    assert res.exit_code == 0 and "no markers" in res.stdout

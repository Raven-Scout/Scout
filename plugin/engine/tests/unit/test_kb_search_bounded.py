"""``scoutctl kb search`` prints a bounded result, and says what it left out.

A search with no ``--glob`` walks the whole vault, so one common word can match
tens of thousands of lines in the dated ``action-items/`` files alone. The output
is capped (matches per file, matches in all, characters per line), every
top-level folder with matches keeps a share of it, and the closing lines give
the real totals and how to narrow or lift the caps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb import read as kb_read
from scout.kb.ontology import clear_frontmatter_cache

runner = CliRunner()

DAILY_FILES = 60
LINES_PER_DAILY = 30
KB_NOTES = 5
LONG_LINE = "start " + "x" * 5_000 + " needle in the middle " + "y" * 5_000
TOTAL = DAILY_FILES * LINES_PER_DAILY + KB_NOTES * 2 + 1


@pytest.fixture(scope="module")
def big_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A vault whose ``action-items/`` tree dwarfs its knowledge base."""
    vault = tmp_path_factory.mktemp("kb-search-bounded")
    (vault / "scout-config.yaml").write_text("user:\n  name: Alex\n", encoding="utf-8")
    daily = vault / "action-items"
    daily.mkdir()
    for i in range(DAILY_FILES):
        body = "".join(f"- item {n}: the needle again\n" for n in range(LINES_PER_DAILY))
        (daily / f"action-items-{i:04d}.md").write_text(body, encoding="utf-8")
    topics = vault / "knowledge-base" / "topics"
    topics.mkdir(parents=True)
    for i in range(KB_NOTES):
        (topics / f"note-{i}.md").write_text("# Note\nneedle one\nNEEDLE two\nother\n", encoding="utf-8")
    (vault / "export.json").write_text(LONG_LINE + "\n", encoding="utf-8")
    return vault


@pytest.fixture(params=["rg", "python"])
def vault(request: pytest.FixtureRequest, big_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if request.param == "python":
        monkeypatch.setattr(kb_read, "find_rg", lambda: None)
    elif kb_read.find_rg() is None:
        pytest.skip("rg not installed")
    clear_frontmatter_cache()
    return big_vault


def search(vault: Path, *args: str) -> Result:
    result = runner.invoke(app, ["kb", "search", "needle", "-i", *args, "--repo", str(vault)])
    assert result.exit_code == 0, result.output
    return result


def test_unscoped_search_is_bounded_and_says_what_it_left_out(vault: Path) -> None:
    out = search(vault).stdout
    lines = out.splitlines()
    matches = [line for line in lines if ":" in line and not line.startswith("(")]

    assert len(matches) == kb_read.SEARCH_MAX_MATCHES
    assert len(out) < 40_000, f"{len(out):,} characters for one search"
    assert max(len(line) for line in lines) < kb_read.SEARCH_MAX_LINE + 200

    # The small folders are not crowded out by the big one.
    assert sum(line.startswith("knowledge-base/") for line in matches) == KB_NOTES * 2
    assert any(line.startswith("export.json:1:") and "needle in the middle" in line for line in matches)

    notice = "\n".join(line for line in lines if line.startswith("("))
    assert f"{kb_read.SEARCH_MAX_MATCHES} of {TOTAL:,} matching lines" in notice
    assert f"{DAILY_FILES + KB_NOTES + 1} files" in notice
    left_out = DAILY_FILES * LINES_PER_DAILY - (kb_read.SEARCH_MAX_MATCHES - KB_NOTES * 2 - 1)
    assert f"action-items/ {left_out:,}" in notice
    assert "--glob" in notice and "--max-matches" in notice and "--all" in notice
    assert "1 long line" in notice and "--max-line" in notice


def test_per_file_cap(vault: Path) -> None:
    out = search(vault, "--glob", "action-items/action-items-000*.md").stdout
    matches = [line for line in out.splitlines() if line.startswith("action-items/")]
    assert len(matches) == 10 * kb_read.SEARCH_MAX_PER_FILE
    assert f"of {10 * LINES_PER_DAILY} matching lines in 10 files" in out
    assert f"at most {kb_read.SEARCH_MAX_PER_FILE} per file" in out


def test_caps_can_be_lifted(vault: Path) -> None:
    out = search(vault, "--max-matches", "0", "--max-per-file", "0", "--max-line", "0").stdout
    lines = out.splitlines()
    assert len(lines) == TOTAL
    assert f"export.json:1:{LONG_LINE}" in lines
    assert not any(line.startswith("(") for line in lines)


def test_a_small_result_is_printed_as_before(vault: Path) -> None:
    out = search(vault, "--glob", "knowledge-base/**").stdout
    assert out.splitlines() == [
        f"knowledge-base/topics/note-{i}.md:{n}:{text}"
        for i in range(KB_NOTES)
        for n, text in ((2, "needle one"), (3, "NEEDLE two"))
    ]


def test_all_prints_every_match_with_long_lines_still_cut(vault: Path) -> None:
    lines = search(vault, "--all").stdout.splitlines()
    assert sum(not line.startswith("(") for line in lines) == TOTAL
    assert max(len(line) for line in lines) < kb_read.SEARCH_MAX_LINE + 200
    assert [line for line in lines if line.startswith("(")] == [
        f"(1 long line cut to {kb_read.SEARCH_MAX_LINE} characters around the match; --max-line 0 prints whole lines.)"
    ]


def test_json_is_complete_unless_a_cap_is_asked_for(vault: Path) -> None:
    """A program reading ``--json`` gets every match whole; with a cap, the notice goes to stderr."""
    result = search(vault, "--json")
    rows = json.loads(result.stdout)
    assert len(rows) == TOTAL and result.stderr == ""
    assert all({"path", "line", "text", "label"} <= set(row) for row in rows)
    assert LONG_LINE in [row["text"] for row in rows]

    capped = search(vault, "--json", "--max-matches", "7")
    assert len(json.loads(capped.stdout)) == 7
    assert f"Showing 7 of {TOTAL:,} matching lines" in capped.stderr


def test_search_function_still_returns_every_match(vault: Path) -> None:
    assert len(kb_read.search(vault, "owner", "needle", (), ignore_case=True)) == TOTAL

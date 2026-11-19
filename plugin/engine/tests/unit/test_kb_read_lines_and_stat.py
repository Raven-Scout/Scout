"""`scoutctl kb read --lines` and `scoutctl kb stat`: the labelled replacements for `sed -n`, `head`,
`tail` and `wc` that the raw-read gate now points a run to (KB usage audit 2026-11-19)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb.read import UsageError, parse_lines

runner = CliRunner()
NOTE = "knowledge-base/topics/infra/infra-costs.md"
SECRET = "knowledge-base/personal/renewal.md"
BODY = "".join(f"line {i}\n" for i in range(1, 11))


def kb(vault, *args: str, reader: str | None = None):
    env = {"SCOUT_READER": reader} if reader else {}
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


@pytest.fixture
def vault(kb_repo):
    kb_repo.write(NOTE, BODY)
    kb_repo.write(SECRET, "---\nvisibility: private\n---\nrenew the permit\nby friday\n")
    return kb_repo.root


# ----- --lines -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "want"),
    [
        ("3:5", "line 3\nline 4\nline 5\n"),  # sed -n 3,5p
        (":2", "line 1\nline 2\n"),  # head -n 2
        ("-2:", "line 9\nline 10\n"),  # tail -n 2
        ("9:", "line 9\nline 10\n"),
        ("8:50", "line 8\nline 9\nline 10\n"),
        ("20:30", ""),
        ("-50:", BODY),
    ],
)
def test_lines_selects_like_sed_head_and_tail(vault, spec: str, want: str) -> None:
    result = kb(vault, "read", NOTE, "--lines", spec)
    assert result.exit_code == 0 and result.stdout == want


def test_lines_json_says_where_the_slice_sits(vault) -> None:
    data = json.loads(kb(vault, "read", NOTE, "--lines", "3:5", "--json").stdout)
    assert data["content"] == "line 3\nline 4\nline 5\n"
    assert (data["first_line"], data["last_line"], data["total_lines"]) == (3, 5, 10)
    assert data["label"]["path"] == NOTE


def test_lines_keeps_the_private_header(vault) -> None:
    result = kb(vault, "read", SECRET, "--lines", "-1:")
    assert result.exit_code == 0 and result.stdout == f"[private] {SECRET}\nby friday\n"


@pytest.mark.parametrize("spec", ["", "3", "a:b", "0:2", "5:3", "-2:4", "1,5"])
def test_a_bad_range_is_a_usage_error(vault, spec: str) -> None:
    with pytest.raises(UsageError):
        parse_lines(spec)
    assert kb(vault, "read", NOTE, "--lines", spec).exit_code == 2


def test_lines_is_denied_like_read_for_a_reader_that_sees_nothing(vault) -> None:
    result = kb(vault, "read", NOTE, "--lines", "1:2", reader="agent:person:priya@example.com")
    assert result.exit_code == 3 and "line 1" not in result.stdout


def test_a_plain_read_is_unchanged(vault) -> None:
    result = kb(vault, "read", NOTE)
    assert result.exit_code == 0 and result.stdout == BODY


# ----- kb stat -----------------------------------------------------------------------------


def test_stat_counts_bytes_lines_and_words(vault) -> None:
    result = kb(vault, "stat", NOTE)
    assert result.exit_code == 0
    assert result.stdout.strip() == f"{NOTE}  {len(BODY)} bytes  10 lines  20 words"


def test_stat_marks_private_files_and_totals_several(vault) -> None:
    result = kb(vault, "stat", NOTE, SECRET)
    lines = result.stdout.splitlines()
    assert lines[1].startswith(f"[private] {SECRET}  ")
    assert lines[-1].startswith("2 file(s), ")


def test_stat_takes_globs(vault) -> None:
    rows = json.loads(kb(vault, "stat", "--glob", "knowledge-base/topics/**", "--json").stdout)
    assert [r["path"] for r in rows] == [NOTE]
    assert rows[0]["bytes"] == len(BODY) and rows[0]["label"]["visibility"] == "normal"


def test_stat_without_arguments_is_a_usage_error(vault) -> None:
    assert kb(vault, "stat").exit_code == 2


def test_stat_of_a_missing_file_is_a_usage_error_for_the_owner(vault) -> None:
    assert kb(vault, "stat", "knowledge-base/nope.md").exit_code == 2


def test_stat_gives_a_reader_that_sees_nothing_no_counts(vault) -> None:
    result = kb(vault, "stat", NOTE, reader="agent:person:priya@example.com")
    assert result.exit_code == 3 and "bytes" not in result.stdout
    data = json.loads(kb(vault, "stat", NOTE, "--json", reader="agent:person:priya@example.com").stdout)
    assert data == [{"path": NOTE, "denied": True, "reason": "not visible to agent:person:priya@example.com"}]


def test_a_delegate_may_run_stat() -> None:
    from scout.cli import _DELEGATE_COMMANDS

    assert ("kb", "stat") in _DELEGATE_COMMANDS

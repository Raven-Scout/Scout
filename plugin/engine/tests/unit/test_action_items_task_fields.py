"""Unit tests for scout.action_items.task_fields — optional per-task board fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items._common import list_comment_lines
from scout.action_items.parser import parse_lines
from scout.action_items.task_fields import (
    TaskFields,
    apply_field,
    match_field_line,
    parse_depends,
    parse_priority,
    parse_state,
    parse_topic,
)

_TASK = [
    "## 🔴 Urgent",
    "- [ ] [#REPLYX] **Reply to Alex about her purchase question** — still open",
    "  - Topic: #STRM",
    "  - Parent: #RTALL",
    "  - State: waiting",
    "  - Action: Review + decide",
    "  - Depends: #XREF, [#QBLK]",
    "  - Session: https://claude.ai/code/session_0123",
    "  - Priority: 2",
    "  - Cycle: Current",
    "  - alex: pinged in thread",
]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("  - State: waiting", ("state", "waiting")),
        ("\t- depends: #XREF", ("depends", "#XREF")),
        ("  - PRIORITY: 1 ", ("priority", "1")),
        ("  - Cycle:", ("cycle", "")),
        ("  - State : waiting", ("state", "waiting")),
    ],
)
def test_match_field_line_accepts_reserved_keys(line: str, expected: tuple[str, str]) -> None:
    assert match_field_line(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "- State: waiting",  # not indented: a task-level bullet, not a sub-bullet
        "  - alex: State: waiting",
        "  - Source: Linear",
        "  - snoozed-until: 2026-10-12",
    ],
)
def test_match_field_line_ignores_other_lines(line: str) -> None:
    assert match_field_line(line) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("mine", "mine"), (" Doing ", "doing"), ("HOLD", "hold"), ("done", None), ("soon", None), ("", None)],
)
def test_parse_state(value: str, expected: str | None) -> None:
    assert parse_state(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#XREF", ("XREF",)),
        ("[#XREF], #QBLK", ("XREF", "QBLK")),
        ("xref · 7391K  QBLK", ("XREF", "7391K", "QBLK")),
        ("#X, not-a-tag, #TOOLONGTAG, #1234", ()),
        ("", ()),
    ],
)
def test_parse_depends(value: str, expected: tuple[str, ...]) -> None:
    assert parse_depends(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0), ("4", 4), (" 2 ", 2), ("5", None), ("-1", None), ("high", None), ("²", None), ("２", None)],
)
def test_parse_priority(value: str, expected: int | None) -> None:
    assert parse_priority(value) == expected


def test_apply_field_replaces_only_the_given_key() -> None:
    fields = apply_field(TaskFields(state="mine"), "priority", "3")
    assert fields == TaskFields(state="mine", priority=3)


def test_apply_field_with_an_invalid_value_clears_the_key() -> None:
    assert apply_field(TaskFields(state="mine"), "state", "sometime") == TaskFields()


def test_parser_collects_fields_on_the_task() -> None:
    [item] = parse_lines(_TASK)
    assert item.fields == TaskFields(
        topic="STRM",
        parent="RTALL",
        state="waiting",
        action="Review + decide",
        depends=("XREF", "QBLK"),
        session="https://claude.ai/code/session_0123",
        priority=2,
        cycle="current",
    )


def test_parser_defaults_to_empty_fields() -> None:
    [item] = parse_lines(["## 🟡 To Do", "- [ ] [#REPLYX] **Reply to Alex** — still open", "  - Source: Slack"])
    assert item.fields == TaskFields()


def test_comment_index_skips_fields(tmp_path: Path) -> None:
    path = tmp_path / "today.md"
    path.write_text("\n".join(_TASK) + "\n", encoding="utf-8")
    assert list_comment_lines(path, task_line_number=2) == [(11, "alex", "pinged in thread")]


def test_render_keeps_fields_out_of_comments(tmp_path: Path) -> None:
    from scout.action_items.render import parse

    md = tmp_path / "scout.md"
    md.write_text("# Title\n\n" + "\n".join(_TASK) + "\n", encoding="utf-8")
    _title, _preamble, sections = parse(md)
    assert [(c.author, c.text) for c in sections[0].tasks[0].comments] == [("alex", "pinged in thread")]


def test_apply_field_rejects_an_unknown_key() -> None:
    with pytest.raises(KeyError):
        apply_field(TaskFields(), "owner", "alex")


@pytest.mark.parametrize(
    ("value", "expected"), [("#STRM", "STRM"), ("[#STRM] #QBLK", "STRM"), ("strm", "STRM"), ("#1", None)]
)
def test_parse_topic_takes_the_first_tag(value: str, expected: str | None) -> None:
    assert parse_topic(value) == expected

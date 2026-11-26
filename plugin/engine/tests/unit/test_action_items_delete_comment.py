"""Unit tests for scout.action_items.delete_comment."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items._common import list_comment_lines
from scout.action_items.delete_comment import delete_comment
from scout.errors import ActionItemError
from scout.events import Event
from scout.id_map import IdMap, IdMapEntry


def _make_daily(fake_data_dir: Path, body: str) -> Path:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(body)
    return daily


def _register_prefix(fake_data_dir: Path, prefix: str = "A3F7") -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", prefix, "task", "action-items-2026-04-26.md", 5))
    m.save()


def test_delete_comment_by_index(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "## In Progress\n\n"
        "- [ ] [#A3F7] task\n"
        "  - alex: first\n"
        "  - alex: second\n"
        "  - alex: third\n"
        "- [ ] another task\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    event = delete_comment(by_id="A3F7", index=2, data_dir=fake_data_dir)

    text = daily.read_text()
    assert "first" in text
    assert "second" not in text
    assert "third" in text
    assert "another task" in text
    assert isinstance(event, Event)
    assert event.kind == "action_item.comment_deleted"
    assert event.source == "cli:delete_comment"
    assert event.payload["comment_text"] == "second"


def test_delete_comment_by_text_substring(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "## In Progress\n\n- [ ] [#A3F7] task\n  - alex: vendor confirmed\n  - alex: legal review needed\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    delete_comment(by_id="A3F7", text="legal", data_dir=fake_data_dir)

    text = (fake_data_dir / "action-items" / "action-items-2026-04-26.md").read_text()
    assert "vendor confirmed" in text
    assert "legal review" not in text


def test_delete_comment_ignores_snoozed_until_marker(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The snoozed-until sub-bullet shares the comment shape but is machine
    metadata. `--index 1` must point at the first real comment, not the marker."""
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "## In Progress\n\n- [ ] [#A3F7] task\n  - snoozed-until: 2026-05-01\n  - alex: real comment\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    delete_comment(by_id="A3F7", index=1, data_dir=fake_data_dir)

    text = daily.read_text()
    assert "snoozed-until: 2026-05-01" in text
    assert "real comment" not in text


def test_delete_comment_index_out_of_range(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n  - alex: only one\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="--index 5 out of range"):
        delete_comment(by_id="A3F7", index=5, data_dir=fake_data_dir)


def test_delete_comment_ambiguous_text(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n  - alex: vendor ping\n  - alex: vendor confirmed\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="ambiguous"):
        delete_comment(by_id="A3F7", text="vendor", data_dir=fake_data_dir)


def test_delete_comment_requires_exactly_one_selector(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(fake_data_dir, "- [ ] [#A3F7] task\n  - alex: hi\n")
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="exactly one of --index or --text"):
        delete_comment(by_id="A3F7", data_dir=fake_data_dir)

    with pytest.raises(ActionItemError, match="exactly one of --index or --text"):
        delete_comment(by_id="A3F7", index=1, text="hi", data_dir=fake_data_dir)


# --- #556: the lister must not run on into the next task's lines -----------

_ONE_SPACE_LIST = (
    "## In Progress\n\n"
    " - [ ] [#A3F7] first task\n"
    "   //==<< an inline note the lister never lists >>==//\n"
    " - [ ] [#B4G8] second task\n"
    "{neighbour}"
    "   - sam: the neighbour's own comment\n"
)


def _line_numbers(daily: Path, needle: str) -> list[int]:
    return [n for n, line in enumerate(daily.read_text().splitlines(), start=1) if needle in line]


def test_delete_comment_cannot_reach_the_next_tasks_plan_marker(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's case. In a list indented by one space the next task line is
    indented too, so the lister used to walk on into its sub-bullets: the app
    shows the inline note as comment 1, and `--index 1` deleted the next
    task's `estimate:` marker."""
    _register_prefix(fake_data_dir)
    body = _ONE_SPACE_LIST.format(neighbour="   - estimate: 45m (raw: 30m, kind: deep)\n")
    daily = _make_daily(fake_data_dir, body)
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="no comments found"):
        delete_comment(by_id="A3F7", index=1, data_dir=fake_data_dir)

    assert daily.read_text() == body


def test_delete_comment_cannot_reach_the_next_tasks_ordinary_sub_bullet(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overrun never needed plan markers: any `- word: text` sub-bullet of
    the next task was in reach."""
    _register_prefix(fake_data_dir)
    body = _ONE_SPACE_LIST.format(neighbour="")
    daily = _make_daily(fake_data_dir, body)
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="no comments found"):
        delete_comment(by_id="A3F7", index=1, data_dir=fake_data_dir)
    with pytest.raises(ActionItemError, match="no comments found"):
        delete_comment(by_id="A3F7", text="neighbour", data_dir=fake_data_dir)

    assert daily.read_text() == body


def test_delete_comment_cannot_reach_a_child_tasks_comment(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same overrun one level down: a child task's comment is not the parent's."""
    _register_prefix(fake_data_dir)
    body = (
        "## In Progress\n\n"
        "- [ ] [#A3F7] parent task\n"
        "  - alex: on the parent\n"
        "  - [ ] child task\n"
        "    - sam: on the child\n"
        "    - [ ] grandchild task\n"
        "      - sam: on the grandchild\n"
        "  - alex: on the parent, after the child\n"
        "- [ ] another task\n"
    )
    daily = _make_daily(fake_data_dir, body)
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda *a, **kw: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="--index 3 out of range"):
        delete_comment(by_id="A3F7", index=3, data_dir=fake_data_dir)
    assert daily.read_text() == body

    # The parent's second comment is the one after the child, not the child's.
    event = delete_comment(by_id="A3F7", index=2, data_dir=fake_data_dir)
    assert event.payload["comment_text"] == "on the parent, after the child"
    assert _line_numbers(daily, "on the child") == [6]
    assert _line_numbers(daily, "on the grandchild") == [8]


def test_list_comment_lines_stops_at_the_next_task_line(tmp_path: Path) -> None:
    """A task's own markers and comments still count, in file order, so
    `--index` means what it always has for every app version."""
    path = tmp_path / "action-items-2026-04-26.md"
    path.write_text(
        " - [ ] [#A3F7] first task\n"
        "   - estimate: 45m\n"
        "   - alex: mine\n"
        " - [x] [#B4G8] second task\n"
        "   - estimate: 30m\n"
        "   - sam: theirs\n",
        encoding="utf-8",
    )
    assert list_comment_lines(path, task_line_number=1) == [(2, "estimate", "45m"), (3, "alex", "mine")]
    assert list_comment_lines(path, task_line_number=4) == [(5, "estimate", "30m"), (6, "sam", "theirs")]

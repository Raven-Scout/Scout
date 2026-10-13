"""Unit tests for the pure core of scout.action_items.watch.

The watchdog wiring is tested in tests/integration/test_action_items_watch.py.
"""

from __future__ import annotations

import datetime as dt

from scout.action_items.watch import process_change, read_settled

_NOW = dt.datetime(2026, 4, 26, 14, 32, 15)


def _reads(*texts: str | None):
    """A reader that returns `texts` in turn, then repeats the last one."""
    queue = list(texts)

    def read() -> str | None:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return read


def _no_sleep(_: float) -> None:
    pass


def test_read_settled_waits_out_the_truncated_read_of_a_truncate_then_write_save() -> None:
    prev = "- [ ] [#A3F7] task\n"
    curr = "- [x] [#A3F7] task\n"
    assert read_settled(_reads("", curr), prev_text=prev, sleep=_no_sleep) == curr


def test_read_settled_waits_out_a_partially_written_file() -> None:
    prev = "- [ ] [#A3F7] task A\n"
    curr = "- [ ] [#A3F7] task A\n- [ ] [#B5K2] task B\n"
    assert read_settled(_reads(curr[:25], curr), prev_text=prev, sleep=_no_sleep) == curr


def test_read_settled_accepts_a_file_really_emptied_once_attempts_run_out() -> None:
    sleeps: list[float] = []
    got = read_settled(_reads(""), prev_text="- [ ] [#A3F7] task\n", sleep=sleeps.append, attempts=4)
    assert got == ""
    assert len(sleeps) == 4


def test_read_settled_does_not_wait_when_nothing_changed() -> None:
    text = "- [ ] [#A3F7] task\n"
    sleeps: list[float] = []
    assert read_settled(_reads(text), prev_text=text, sleep=sleeps.append) == text
    assert sleeps == []


def test_read_settled_passes_an_unreadable_file_back_as_none() -> None:
    assert read_settled(_reads(None), prev_text="x\n", sleep=_no_sleep) is None
    assert read_settled(_reads("", None), prev_text="x\n", sleep=_no_sleep) is None


def test_process_change_emits_no_lines_when_text_identical() -> None:
    text = "## In Progress\n\n- [ ] [#A3F7] task A\n"
    lines = process_change(prev_text=text, curr_text=text, now=_NOW, color=False)
    assert lines == []


def test_process_change_emits_completed_line_when_checkbox_flipped() -> None:
    prev = "## In Progress\n\n- [ ] [#A3F7] task A\n"
    curr = "## In Progress\n\n- [x] [#A3F7] task A\n"
    lines = process_change(prev_text=prev, curr_text=curr, now=_NOW, color=False)
    assert len(lines) == 1
    assert "completed" in lines[0]
    assert "[#A3F7]" in lines[0]


def test_process_change_emits_added_line_when_new_item_appears() -> None:
    prev = "## In Progress\n\n- [ ] [#A3F7] task A\n"
    curr = "## In Progress\n\n- [ ] [#A3F7] task A\n- [ ] [#B5K2] task B\n"
    lines = process_change(prev_text=prev, curr_text=curr, now=_NOW, color=False)
    assert len(lines) == 1
    assert "added" in lines[0]
    assert "[#B5K2]" in lines[0]


def test_process_change_handles_unparseable_prev_gracefully() -> None:
    """Initial seed (empty file) shouldn't crash the watcher."""
    lines = process_change(prev_text="", curr_text="- [ ] [#A3F7] task\n", now=_NOW, color=False)
    # Single added event for the new task.
    assert len(lines) == 1
    assert "added" in lines[0]

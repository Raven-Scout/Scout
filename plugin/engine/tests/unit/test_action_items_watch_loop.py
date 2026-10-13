"""Coverage of `run_watch_loop` — the watchdog wiring behind
`scoutctl action-items watch`.

`test_action_items_watch.py` covers `process_change`, the pure text→text→lines
core. The loop around it is untested and holds all the fiddly bits: the
bytes-vs-str `src_path` from watchdog, the "some other file in the directory
changed" filter, the mid-rename `FileNotFoundError` (atomic writes replace the
daily file, so the watcher *will* see a vanished path), the no-op guard when
content is unchanged, and the state advance that keeps the next diff correct.

The loop blocks on `observer.join()` forever, so these tests substitute a fake
Observer and invoke the captured handler directly.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("watchdog")

from scout.action_items.watch import run_watch_loop  # noqa: E402


class _FakeObserver:
    """Records what the loop schedules, and returns from join() immediately."""

    instances: list[_FakeObserver] = []

    def __init__(self) -> None:
        self.handler = None
        self.watched_path: str | None = None
        self.recursive: bool | None = None
        self.started = False
        self.stopped = False
        self.joined = 0
        _FakeObserver.instances.append(self)

    def schedule(self, handler, path: str, recursive: bool = False) -> None:
        self.handler = handler
        self.watched_path = path
        self.recursive = recursive

    def start(self) -> None:
        self.started = True

    def join(self) -> None:
        self.joined += 1

    def stop(self) -> None:
        self.stopped = True


class _Event:
    def __init__(self, src_path: str | bytes, dest_path: str | bytes | None = None) -> None:
        self.src_path = src_path
        if dest_path is not None:
            self.dest_path = dest_path


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> type[_FakeObserver]:
    _FakeObserver.instances = []
    monkeypatch.setattr("watchdog.observers.Observer", _FakeObserver)
    return _FakeObserver


@pytest.fixture(autouse=True)
def _instant_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settle loop's pauses are real sleeps; these tests don't need them."""
    monkeypatch.setattr("scout.action_items.watch._sleep", lambda _: None)


@pytest.fixture
def daily(tmp_path: Path) -> Path:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text("## To Do\n\n- [ ] first task\n", encoding="utf-8")
    return p


def _handler(observed: type[_FakeObserver]):
    obs = observed.instances[-1]
    assert obs.handler is not None
    return obs.handler


def test_missing_target_raises_before_any_watcher_is_started(tmp_path: Path, observed: type[_FakeObserver]) -> None:
    with pytest.raises(FileNotFoundError):
        run_watch_loop(tmp_path / "nope.md", color=False)
    assert observed.instances == []


def test_loop_watches_the_parent_directory_non_recursively(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """watchdog watches directories, not files — atomic renames would break a
    file-level watch. Recursion is off so a big vault stays cheap."""
    run_watch_loop(daily, color=False)
    obs = observed.instances[-1]
    assert obs.watched_path == str(daily.parent)
    assert obs.recursive is False
    assert obs.started is True
    assert obs.joined == 1
    # The "how to stop" hint goes to stderr so stdout stays a pure change stream.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Watching {daily.name} for changes" in captured.err


def test_a_change_prints_one_line_per_detected_event(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    run_watch_loop(daily, color=False)
    capsys.readouterr()  # drop the startup banner

    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily)))

    out = capsys.readouterr().out
    assert out.strip(), "a completed task should emit a change line"
    assert "first task" in out


def test_a_bytes_src_path_is_decoded(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """watchdog's inotify backend hands back bytes paths; comparing those to a
    str Path silently matches nothing and the watcher goes deaf."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily).encode()))

    assert capsys.readouterr().out.strip()


def test_a_change_to_a_different_file_is_ignored(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """The watch is directory-wide, so every sibling file's events arrive here
    too — including the `.tmp` files the writers create."""
    sibling = daily.parent / "action-items-2026-04-14.md"
    sibling.write_text("## To Do\n\n- [ ] yesterday\n", encoding="utf-8")

    run_watch_loop(daily, color=False)
    capsys.readouterr()

    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(sibling)))

    assert capsys.readouterr().out == ""


def test_an_unchanged_file_emits_nothing(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """A touch or a rewrite with identical bytes must not print an empty diff."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    daily.write_text(daily.read_text(), encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily)))

    assert capsys.readouterr().out == ""


def test_a_vanished_file_mid_rename_is_skipped(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """The writers use write-tmp-then-replace, so the watcher can observe the
    path between unlink and rename. That must be a no-op, not a crash — the
    next event delivers the new contents."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    handler = _handler(observed)
    daily.unlink()
    handler.on_modified(_Event(str(daily)))  # must not raise
    assert capsys.readouterr().out == ""

    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    handler.on_modified(_Event(str(daily)))
    assert capsys.readouterr().out.strip()


def test_state_advances_so_each_change_diffs_against_the_previous_one(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the state advance, every event re-reports the whole history."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()
    handler = _handler(observed)

    daily.write_text("## To Do\n\n- [ ] first task\n- [ ] second task\n", encoding="utf-8")
    handler.on_modified(_Event(str(daily)))
    first_out = capsys.readouterr().out
    assert "second task" in first_out

    daily.write_text("## To Do\n\n- [ ] first task\n- [ ] second task\n- [ ] third task\n", encoding="utf-8")
    handler.on_modified(_Event(str(daily)))
    second_out = capsys.readouterr().out
    assert "third task" in second_out
    # "second task" was already reported; it must not repeat.
    assert "second task" not in second_out


def test_a_truncate_then_write_save_reports_only_the_real_change(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path.write_text` and most editors truncate, then write. The event for
    the truncate can be handled before the write lands, and the read sees an
    empty file. Diffing that printed every item "removed", then "added" on
    the next event, and the checkbox flip was never reported (seen on macOS
    CI under load). Here the write lands during the watcher's first settle
    pause, which is exactly that interleaving, made deterministic."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()
    handler = _handler(observed)

    def write_lands(_: float) -> None:
        daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")

    monkeypatch.setattr("scout.action_items.watch._sleep", write_lands)
    daily.write_text("", encoding="utf-8")  # the truncate
    handler.on_modified(_Event(str(daily)))  # its event, handled before the write
    handler.on_modified(_Event(str(daily)))  # the write's own event

    out = capsys.readouterr().out
    assert "completed" in out
    assert "first task" in out
    assert "removed" not in out
    assert "added" not in out


def test_a_file_really_emptied_still_reports_its_removals(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """Waiting out empty reads must not hide a file that stays empty."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    daily.write_text("", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily)))

    out = capsys.readouterr().out
    assert "removed" in out
    assert "first task" in out


def test_an_atomic_save_renamed_over_the_target_is_seen(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """Write-temp-then-rename fires a move onto the target, not a modify."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    tmp = daily.with_suffix(".md.tmp")
    tmp.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    tmp.replace(daily)
    _handler(observed).on_moved(_Event(str(tmp), str(daily)))

    assert "completed" in capsys.readouterr().out


def test_a_target_recreated_in_place_is_seen(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    run_watch_loop(daily, color=False)
    capsys.readouterr()

    daily.unlink()
    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_created(_Event(str(daily)))

    assert "completed" in capsys.readouterr().out


def test_moves_and_creates_of_other_files_are_ignored(
    daily: Path, observed: type[_FakeObserver], capsys: pytest.CaptureFixture[str]
) -> None:
    """A temp file being created, or the target moved *away*, isn't a save."""
    run_watch_loop(daily, color=False)
    capsys.readouterr()
    handler = _handler(observed)

    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    tmp = daily.with_suffix(".md.tmp")
    handler.on_created(_Event(str(tmp)))
    handler.on_moved(_Event(str(daily), str(tmp)))
    handler.on_moved(_Event(str(tmp), str(daily.parent / "other.md")))

    assert capsys.readouterr().out == ""


def test_color_flag_reaches_the_renderer(
    daily: Path, observed: type[_FakeObserver], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(
        "scout.action_items.watch.process_change",
        lambda *, prev_text, curr_text, now, color: seen.append(color) or [],
    )
    run_watch_loop(daily, color=True)
    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily)))
    assert seen == [True]


def test_keyboard_interrupt_stops_and_drains_the_observer(
    daily: Path, observed: type[_FakeObserver], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C is the documented way to stop this command, so it must shut the
    observer thread down rather than leaving it running."""
    calls: list[str] = []

    class InterruptingObserver(_FakeObserver):
        def join(self) -> None:
            calls.append("join")
            if len(calls) == 1:
                raise KeyboardInterrupt

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr("watchdog.observers.Observer", InterruptingObserver)
    run_watch_loop(daily, color=False)
    assert calls == ["join", "stop", "join"]


def test_process_change_uses_a_real_timestamp(
    daily: Path, observed: type[_FakeObserver], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dt.datetime] = []
    monkeypatch.setattr(
        "scout.action_items.watch.process_change",
        lambda *, prev_text, curr_text, now, color: seen.append(now) or [],
    )
    run_watch_loop(daily, color=False)
    daily.write_text("## To Do\n\n- [x] first task\n", encoding="utf-8")
    _handler(observed).on_modified(_Event(str(daily)))
    assert len(seen) == 1 and isinstance(seen[0], dt.datetime)

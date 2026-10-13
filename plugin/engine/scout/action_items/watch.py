"""scoutctl action-items watch — projection-consumer over today's action items.

Public CLI contract per spec §13.3: this command *streams changes to
today's action items as they happen*. The v0.4 implementation watches
the underlying markdown file via `watchdog`; v0.5 will substitute an
event-store subscriber. The CLI surface and stdout shape are stable.

Heavy imports (watchdog, rich) live inside function bodies so
`scoutctl --help` and other subcommands stay under the latency budget
(spec §4).
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from collections.abc import Callable
from pathlib import Path

from scout.action_items.diff import diff
from scout.action_items.parser import ActionItem
from scout.action_items.render import render_changes

# How long to wait between the reads that decide a file has finished being
# written, and how many re-reads to allow before taking what is there.
_SETTLE_DELAY_S = 0.05
_SETTLE_ATTEMPTS = 10
_sleep = time.sleep  # module-level so tests can step through the settle loop


def process_change(
    *,
    prev_text: str,
    curr_text: str,
    now: dt.datetime,
    color: bool,
) -> list[str]:
    """Pure core of the watcher: text → text → list of formatted lines.

    Used directly by tests; called from `run_watch_loop` once the file has
    settled (see `read_settled`).
    """
    prev_items = _parse_text(prev_text)
    curr_items = _parse_text(curr_text)
    events = diff(prev=prev_items, curr=curr_items)
    return render_changes(events, now=now, color=color)


def _parse_text(text: str) -> list[ActionItem]:
    """Run the parser over an in-memory string by writing to a tempfile."""
    # parser.parse_file expects a Path. The watcher always has a real
    # path; this helper exists so process_change() can be tested with
    # arbitrary strings without hitting the real filesystem.
    import tempfile

    from scout.action_items.parser import parse_file

    if not text:
        return []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    try:
        return parse_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def read_settled(
    read: Callable[[], str | None],
    *,
    prev_text: str,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = _SETTLE_DELAY_S,
    attempts: int = _SETTLE_ATTEMPTS,
) -> str | None:
    """Read the watched file once it has stopped changing.

    A save that truncates then writes (``Path.write_text``, most editors)
    fires events in between, so a read can see an empty file, or the first
    chunk of a long one. Diffing that reports every item removed, then
    re-added on the next event — and the real change (a checkbox flip) is
    lost. So re-read after a short pause until two reads agree. An empty read
    of a file that had content never counts as settled until the attempts run
    out: a file really emptied still reports its removals, just later.

    ``read`` returns None when the file can't be read right now (mid-rename);
    that is passed straight back so the caller skips the event.
    """
    text = read()
    if text is None or text == prev_text:
        return text
    for _ in range(attempts):
        sleep(delay)
        again = read()
        if again is None:
            return None
        if again == text and (again or not prev_text):
            return again
        text = again
    return text


def run_watch_loop(target: Path, *, color: bool) -> None:
    """Block until SIGINT, emitting one line per detected change.

    Heavy imports inside the body — `scoutctl action-items watch` is
    interactive (a long-running process), so the cost is paid once.
    """
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    if not target.exists():
        raise FileNotFoundError(target)

    state = {"prev_text": target.read_text(encoding="utf-8")}

    def read() -> str | None:
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None  # mid-rename; the next event will deliver the new contents
        except UnicodeDecodeError:
            return None  # the read split a multi-byte character mid-write; ditto

    def on_change() -> None:
        # watchdog dispatches events one at a time, so the events a save fires
        # while this settles queue up behind it and then find nothing new.
        curr_text = read_settled(read, prev_text=state["prev_text"], sleep=_sleep)
        if curr_text is None or curr_text == state["prev_text"]:
            return
        lines = process_change(
            prev_text=state["prev_text"],
            curr_text=curr_text,
            now=dt.datetime.now(),
            color=color,
        )
        for line in lines:
            print(line, flush=True)
        state["prev_text"] = curr_text

    def is_target(path: str | bytes | None) -> bool:
        if path is None:
            return False
        if isinstance(path, bytes):
            path = path.decode()
        return Path(path) == target

    class _Handler(FileSystemEventHandler):
        # In-place saves arrive as modified events. Atomic saves (write a temp
        # file, rename it over the target) arrive as a move whose destination
        # is the target, or as a create on backends that report it that way.
        def on_modified(self, event: FileSystemEvent) -> None:
            if is_target(event.src_path):
                on_change()

        def on_created(self, event: FileSystemEvent) -> None:
            if is_target(event.src_path):
                on_change()

        def on_moved(self, event: FileSystemEvent) -> None:
            if is_target(getattr(event, "dest_path", None)):
                on_change()

    observer = Observer()
    observer.schedule(_Handler(), str(target.parent), recursive=False)
    observer.start()
    print(
        f"Watching {target.name} for changes — Ctrl-C to stop.",
        file=sys.stderr,
    )
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()

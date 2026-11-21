"""Locked read/transform/atomic-write for lab files, plus the owner verbs and comments.

Every lab write goes through :func:`lab_lock` (one exclusive ``flock`` on
``<vault>/.scout-state/lab.lock``), so two writers on one thread (the app's comment and a run's
question answer) both land. Transforms are the pure ``str -> str`` functions of
:mod:`scout.lab.writer`; nothing is written when a transform raises. This module never commits.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from scout import config
from scout.errors import LabError
from scout.lab import paths, writer
from scout.lab.model import Thread, TimelineEntry
from scout.lab.parser import parse_front_page
from scout.safe_write import atomic_write_text, open_lock

LOCK_TIMEOUT_S = 30.0
_POLL_S = 0.02

OWNER_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "approve": (frozenset({"proposed"}), "active"),
    "reject": (frozenset({"proposed"}), "rejected"),
    "pause": (frozenset({"active"}), "paused"),
    "resume": (frozenset({"paused"}), "active"),
    "close": (frozenset({"active", "paused"}), "closed"),
    "reopen": (frozenset({"closed", "rejected"}), "active"),
}


# --- lock, read, write -----------------------------------------------------------------


@contextlib.contextmanager
def lab_lock(vault: Path) -> Iterator[None]:
    """Hold the vault's lab lock; wait up to ``LOCK_TIMEOUT_S`` seconds, then raise ``LabError``."""
    state = vault / ".scout-state"
    state.mkdir(parents=True, exist_ok=True)
    handle = open_lock(state / "lab.lock")
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LabError(
                        f"another lab write holds {state / 'lab.lock'}; gave up after {LOCK_TIMEOUT_S:.0f}s"
                    ) from None
                time.sleep(_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LabError(f"{path} does not exist") from None


def mutate(vault: Path, path: Path, fn: Callable[[str], str]) -> None:
    """Under the lab lock: read ``path``, apply ``fn``, write atomically. Nothing is written if ``fn`` raises."""
    with lab_lock(vault):
        atomic_write_text(path, fn(_read(path)))


def stamp(vault: Path, now: _dt.datetime | None = None) -> str:
    """``YYYY-MM-DD HH:MM`` in the vault's zone (``now`` is injected by tests)."""
    moment = now if now is not None else config.now(vault)
    return moment.strftime("%Y-%m-%d %H:%M")


# --- damaged-page guard ----------------------------------------------------------------


def guard_headings(slug: str, text: str) -> Thread:
    """Parse a front page and refuse when its ``##`` headings are damaged.

    The writers silently use the first of a duplicated heading, so a page with a missing, renamed,
    duplicated or out-of-order heading must not be written at all. Other problem kinds do not block.
    """
    thread = parse_front_page(slug, text)
    for problem in thread.problems:
        if "heading" in problem.message:
            where = f" (line {problem.line})" if problem.line else ""
            raise LabError(f"{slug}: front page is damaged: {problem.message}{where}; fix the page by hand")
    return thread


def front_mutate(vault: Path, slug: str, fn: Callable[[str], str]) -> None:
    """:func:`mutate` for a thread's front page, refusing a page with damaged headings."""

    def guarded(text: str) -> str:
        guard_headings(slug, text)
        return fn(text)

    mutate(vault, paths.front_page(vault, slug), guarded)


# --- owner verbs -----------------------------------------------------------------------


def owner_verb(
    vault: Path,
    slug: str,
    verb: str,
    author: str,
    reason: str | None = None,
    now: _dt.datetime | None = None,
) -> Thread:
    """Apply an owner status transition: front page and timeline change under one lock."""
    if verb not in OWNER_TRANSITIONS:
        raise LabError(f"unknown owner verb '{verb}'")
    allowed, target = OWNER_TRANSITIONS[verb]
    when = stamp(vault, now)
    front = paths.front_page(vault, slug)
    timeline = paths.timeline_path(vault, slug)
    bullet = f"**{verb.capitalize()}:** by {author}"
    if reason:
        bullet += f" — {reason}"

    with lab_lock(vault):
        text = _read(front)
        thread = guard_headings(slug, text)
        if thread.status not in allowed:
            raise LabError(f"cannot {verb} a {thread.status} thread")
        fields: dict[str, str | int] = {"status": target}
        if verb == "approve":
            fields["approved"] = when[:10]
        new_front = writer.set_frontmatter(text, **fields)
        if timeline.is_file():
            old_timeline = timeline.read_text(encoding="utf-8")
        else:
            old_timeline = writer.new_timeline(slug, thread.title or slug)
        new_timeline = writer.prepend_timeline(old_timeline, TimelineEntry(when, "owner", [bullet]))
        atomic_write_text(front, new_front)
        atomic_write_text(timeline, new_timeline)
        return parse_front_page(slug, new_front)


# --- comments --------------------------------------------------------------------------


def _check_commentable(thread: Thread) -> None:
    if thread.status == "rejected":
        raise LabError(f"{thread.slug} is rejected; reopen it before commenting")


def comment(
    vault: Path,
    slug: str,
    target: str,
    author: str,
    body: str,
    now: _dt.datetime | None = None,
) -> None:
    """Add an owner comment to ``thread``, a ``Q<n>`` or an ``E<n>``; allowed in every status but rejected."""
    when = stamp(vault, now)

    def fn(text: str) -> str:
        _check_commentable(guard_headings(slug, text))
        return writer.add_comment(text, target, author, body, when)

    mutate(vault, paths.front_page(vault, slug), fn)


def reply(
    vault: Path,
    slug: str,
    target: str,
    index: int,
    body: str,
    now: _dt.datetime | None = None,
) -> None:
    """Scout's reply to the ``index``-th (1-based) top-level comment of ``target``."""
    when = stamp(vault, now)

    def fn(text: str) -> str:
        _check_commentable(guard_headings(slug, text))
        return writer.add_reply(text, target, index, body, when)

    mutate(vault, paths.front_page(vault, slug), fn)


def require_active(thread: Thread) -> None:
    if thread.status != "active":
        raise LabError(f"{thread.slug} is {thread.status}; research writes need an active thread")

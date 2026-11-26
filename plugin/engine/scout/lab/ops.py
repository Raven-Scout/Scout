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
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from scout import config, plural
from scout.errors import LabError
from scout.lab import paths, writer
from scout.lab.model import CLOSE_PREFIX, Thread, TimelineEntry
from scout.lab.parser import fenced, parse_front_page, split_lines
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


def _article(word: str) -> str:
    """``a`` or ``an`` for ``word`` (status names: an active, a paused)."""
    return "an" if word[:1].lower() in "aeiou" else "a"


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


def _read_text(path: Path) -> str:
    """The file's text with its line endings untouched (``read_text`` would turn CRLF into LF)."""
    return path.read_bytes().decode("utf-8")


def _read(path: Path) -> str:
    try:
        return _read_text(path)
    except FileNotFoundError:
        raise LabError(f"{path} does not exist") from None


def mutate(vault: Path, path: Path, fn: Callable[[str], str], *, slug: str | None = None) -> None:
    """Under the lab lock: read ``path``, apply ``fn``, write atomically. Nothing is written if ``fn`` raises.

    With ``slug`` the result must still have undamaged ``##`` headings: a write that would lock the page
    (an unbalanced code fence, say) is refused.
    """
    with lab_lock(vault):
        new = fn(_read(path))
        if slug is not None:
            guard_headings(slug, new)
        atomic_write_text(path, new)


def stamp(vault: Path, now: _dt.datetime | None = None) -> str:
    """``YYYY-MM-DD HH:MM`` in the vault's zone (``now`` is injected by tests)."""
    moment = now if now is not None else config.now(vault)
    return moment.strftime("%Y-%m-%d %H:%M")


_Content = str | Callable[[], str]


def _write_files(steps: list[tuple[Path, _Content]]) -> None:
    """Write ``steps`` in order; if one fails, restore the files already written and re-raise.

    A step's content may be a callable, evaluated when its turn comes (the index is rendered from
    the files written before it). Call under the lab lock.
    """
    done: list[tuple[Path, str | None]] = []
    try:
        for path, content in steps:
            text = content if isinstance(content, str) else content()
            prior = _read_text(path) if path.is_file() else None
            atomic_write_text(path, text)
            done.append((path, prior))
    except BaseException:
        for path, prior in reversed(done):
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, prior)
        raise


def _timeline_with(slug: str, thread: Thread, entry: TimelineEntry, timeline: Path) -> str:
    """The timeline text with ``entry`` prepended; a missing timeline starts fresh."""
    if timeline.is_file():
        old = _read_text(timeline)
    else:
        old = writer.new_timeline(slug, thread.title or slug, thread.visibility)
    try:
        return writer.with_visibility(writer.prepend_timeline(old, entry), thread.visibility)
    except LabError as exc:
        raise LabError(f"{slug}: cannot update {timeline.name}: {exc}") from exc


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
    paths.check_slug(slug)

    def guarded(text: str) -> str:
        guard_headings(slug, text)
        return fn(text)

    mutate(vault, paths.front_page(vault, slug), guarded, slug=slug)


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
    paths.check_slug(slug)
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
            raise LabError(f"cannot {verb} {_article(thread.status)} {thread.status} thread")
        fields: dict[str, str | int] = {"status": target}
        if verb == "approve":
            fields["approved"] = when[:10]
        new_front = writer.set_frontmatter(text, **fields)
        new_timeline = _timeline_with(slug, thread, TimelineEntry(when, "owner", [bullet]), timeline)
        # Timeline first: a failure after it leaves an audit entry with no status change, never the reverse.
        _write_files([(timeline, new_timeline), (front, new_front), (paths.index_path(vault), _index_step(vault))])
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
    paths.check_slug(slug)
    when = stamp(vault, now)

    def fn(text: str) -> str:
        _check_commentable(guard_headings(slug, text))
        return writer.add_comment(text, target, author, body, when)

    mutate(vault, paths.front_page(vault, slug), fn, slug=slug)


def reply(
    vault: Path,
    slug: str,
    target: str,
    index: int,
    body: str,
    now: _dt.datetime | None = None,
) -> None:
    """Scout's reply to the ``index``-th (1-based) top-level comment of ``target``."""
    paths.check_slug(slug)
    when = stamp(vault, now)

    def fn(text: str) -> str:
        _check_commentable(guard_headings(slug, text))
        return writer.add_reply(text, target, index, body, when)

    mutate(vault, paths.front_page(vault, slug), fn, slug=slug)


def propose_close(vault: Path, slug: str, reason: str, now: _dt.datetime | None = None) -> None:
    """Scout asks the owner to close ``slug``: a thread-level comment ``Proposing to close: <reason>``.

    Allowed while the thread is active or paused. Only the comment is written; the run's own ``research run``
    timeline entry records the rest.
    """
    paths.check_slug(slug)
    when = stamp(vault, now)

    def fn(text: str) -> str:
        thread = guard_headings(slug, text)
        if thread.status not in ("active", "paused"):
            raise LabError(f"{slug} is {thread.status}; only an active or paused thread can be proposed for closing")
        return writer.add_comment(text, "thread", "scout", f"{CLOSE_PREFIX}: {reason.strip()}", when)

    mutate(vault, paths.front_page(vault, slug), fn, slug=slug)


def require_active(thread: Thread) -> None:
    if thread.status != "active":
        raise LabError(f"{thread.slug} is {thread.status}; research writes need an active thread")


# --- run verbs -------------------------------------------------------------------------


def _run_mutate(vault: Path, slug: str, fn: Callable[[str, Thread], str]) -> None:
    """:func:`front_mutate` for run verbs: heading guard first, then the thread must be active."""

    def guarded(text: str) -> str:
        thread = parse_front_page(slug, text)
        require_active(thread)
        return fn(text, thread)

    front_mutate(vault, slug, guarded)


def question_add(vault: Path, slug: str, qtext: str, by: str | None = None, now: _dt.datetime | None = None) -> str:
    """Raise a new open question; returns its ID (taken from ``next_q``, never reused)."""
    day = stamp(vault, now)[:10]
    qids: list[str] = []

    def fn(text: str, thread: Thread) -> str:
        raw = thread.frontmatter.get("next_q")
        if isinstance(raw, int) and not isinstance(raw, bool):
            taken = {q.qid for q in [*thread.open, *thread.answered]}
            if f"Q{raw}" in taken:
                raise LabError(
                    f"{slug}: next_q is {raw} but Q{raw} already exists; fix next_q on the front page by hand"
                )
        new, qid = writer.add_question(text, qtext, day, by)
        qids.append(qid)
        return new

    _run_mutate(vault, slug, fn)
    return qids[0]


def question_answer(vault: Path, slug: str, qid: str, note: str, now: _dt.datetime | None = None) -> None:
    day = stamp(vault, now)[:10]
    _run_mutate(vault, slug, lambda text, _t: writer.answer_question(text, qid, note, day))


def question_drop(
    vault: Path,
    slug: str,
    qid: str,
    reason: str,
    owner_comment: int | None = None,
    now: _dt.datetime | None = None,
) -> None:
    day = stamp(vault, now)[:10]
    _run_mutate(vault, slug, lambda text, _t: writer.drop_question(text, qid, reason, day, owner_comment))


def understanding_set(vault: Path, slug: str, prose: str) -> None:
    _run_mutate(vault, slug, lambda text, _t: writer.set_understanding(text, prose))


def evidence_add(vault: Path, slug: str, target: str, why: str) -> None:
    _run_mutate(vault, slug, lambda text, _t: writer.add_evidence(text, target, why))


def timeline_add(
    vault: Path,
    slug: str,
    tag: str,
    did: str,
    found: str,
    questions: str,
    steers: str | None = None,
    evidence: str | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Prepend a timeline entry; a ``research run`` also sets ``last_run`` on the front page."""
    paths.check_slug(slug)
    when = stamp(vault, now)
    bullets = [f"**Did:** {did}", f"**Found:** {found}", f"**Questions:** {questions}"]
    if steers is not None:
        bullets.append(f"**Steers handled:** {steers}")
    if evidence is not None:
        bullets.append(f"Evidence: {evidence}")
    front = paths.front_page(vault, slug)
    timeline = paths.timeline_path(vault, slug)

    with lab_lock(vault):
        text = _read(front)
        thread = guard_headings(slug, text)
        require_active(thread)
        new_timeline = _timeline_with(slug, thread, TimelineEntry(when, tag, bullets), timeline)
        steps: list[tuple[Path, _Content]] = [(timeline, new_timeline)]
        if tag == "research run":
            steps.append((front, writer.set_frontmatter(text, last_run=when.replace(" ", "T"))))
        _write_files(steps)


# --- proposals -------------------------------------------------------------------------


def _lab_settings(vault: Path) -> dict[str, Any]:
    raw = config.load_config(vault).get("lab")
    return raw if isinstance(raw, dict) else {}


def _threads(vault: Path) -> list[Thread]:
    """Every readable thread under ``knowledge-base/lab/``, by slug. Folders without a front page are skipped."""
    root = paths.lab_dir(vault)
    out: list[Thread] = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        front = folder / f"{folder.name}.md"
        try:
            out.append(parse_front_page(folder.name, _read_text(front)))
        except (OSError, UnicodeDecodeError):
            continue
    return out


def propose(
    vault: Path,
    slug: str,
    title: str,
    question: str,
    open_questions: list[str],
    evidence: list[tuple[str, str]],
    visibility: str = "normal",
    now: _dt.datetime | None = None,
) -> Thread:
    """Create a ``proposed`` thread: front page, timeline and index entry, or nothing at all."""
    paths.check_slug(slug)
    when = stamp(vault, now)
    page = writer.new_front_page(slug, title, question, open_questions, evidence, when[:10], visibility)
    parsed = parse_front_page(slug, page)
    entry = TimelineEntry(when, "proposal", [f"**Proposed:** {question}"])
    timeline_text = writer.prepend_timeline(writer.new_timeline(slug, title, parsed.visibility), entry)
    limit = int(_lab_settings(vault).get("max_open_proposals", 5))
    folder = paths.thread_dir(vault, slug)

    with lab_lock(vault):
        if folder.exists():
            raise LabError(f"thread '{slug}' already exists")
        open_proposals = sum(1 for t in _threads(vault) if t.status == "proposed")
        if open_proposals >= limit:
            raise LabError(f"{open_proposals} open proposals already (limit {limit}); the owner must decide some first")
        folder.mkdir(parents=True)
        try:
            _write_files(
                [
                    (paths.timeline_path(vault, slug), timeline_text),
                    (paths.front_page(vault, slug), page),
                    (paths.index_path(vault), _index_step(vault)),
                ]
            )
        except BaseException:
            with contextlib.suppress(OSError):
                folder.rmdir()
            raise
    return parsed


# --- lab.md ----------------------------------------------------------------------------

_INDEX_SECTIONS = (
    ("Needs you", ("proposed",)),
    ("Active", ("active",)),
    ("Paused", ("paused",)),
    ("Closed", ("closed", "rejected")),
)
_BEFORE_HEADING = "## Before the lab"
_H2 = re.compile(r"^## ")


def _before_the_lab(existing: str) -> list[str]:
    """The ``## Before the lab`` section of an existing index, verbatim, trailing blank lines dropped."""
    lines = split_lines(existing)
    in_fence = fenced(lines)
    for i, ln in enumerate(lines):
        if not in_fence[i] and ln.rstrip() == _BEFORE_HEADING:
            end = next((j for j in range(i + 1, len(lines)) if not in_fence[j] and _H2.match(lines[j])), len(lines))
            block = lines[i:end]
            while block and not block[-1].strip():
                block.pop()
            return block
    return []


def _render_index(vault: Path) -> str:
    threads = _threads(vault)
    out = ["# Lab", ""]
    hidden = sum(1 for t in threads if t.visibility == "private")
    for heading, statuses in _INDEX_SECTIONS:
        out.append(f"## {heading}")
        for t in threads:
            if t.status in statuses and t.visibility != "private":
                text = " ".join(t.question.split()) or t.title or t.slug
                out.append(f"- [[{t.slug}]] — {text}")
        out.append("")
    try:
        existing = _read_text(paths.index_path(vault))
    except (OSError, UnicodeDecodeError):
        existing = ""
    if hidden:
        out.extend([f"_{plural.count(hidden, 'private thread')} not listed._", ""])
    keep = _before_the_lab(existing)
    if keep:
        out.extend(keep)
        out.append("")
    return "\n".join(out)


def _index_step(vault: Path) -> Callable[[], str]:
    return lambda: _render_index(vault)


def write_index(vault: Path) -> None:
    """Rewrite ``lab.md`` from the thread folders, keeping any ``## Before the lab`` section verbatim."""
    with lab_lock(vault):
        _write_files([(paths.index_path(vault), _index_step(vault))])

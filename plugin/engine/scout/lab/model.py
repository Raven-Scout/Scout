"""Lab data model: vocabularies, ID regexes and the parsed dataclasses. No I/O."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# A comment author's handle, as `scoutctl lab --author` takes it; `scout` is Scout's own.
HANDLE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
RESERVED_HANDLE = "scout"
_HANDLE_FALLBACK = "user"
# Latin letters NFKD leaves whole; the app's ICU `Latin-ASCII` transform spells them out.
_LATIN_ASCII = str.maketrans(
    {
        "ß": "ss", "ẞ": "SS", "Ł": "L", "ł": "l", "Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae", "Œ": "OE",
        "œ": "oe", "Đ": "D", "đ": "d", "Þ": "Th", "þ": "th", "Ð": "D", "ð": "d", "ı": "i", "Ħ": "H", "ħ": "h",
    }
)  # fmt: skip


def owner_handle(name: str) -> str:
    """The handle the app signs the owner's comments with (``CommentAuthor.handle`` in the macOS app).

    Spell letters out as ASCII, then keep letters, digits, ``.`` and ``_``: a run of whitespace and dashes is one
    ``-``, and anything before the first letter is dropped. An empty result is ``user``. Other scripts, which the app
    transliterates through ICU, fall back to ``user`` here. The result always matches :data:`HANDLE_RE` and is never
    ``scout`` (that would be refused, so it falls back too).
    """
    decomposed = unicodedata.normalize("NFKD", name.translate(_LATIN_ASCII))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    out: list[str] = []
    pending_dash = False
    for ch in folded:
        if ch.isspace() or ch == "-":
            pending_dash = bool(out)
            continue
        is_letter = ch.isascii() and ch.isalpha()
        if not (is_letter or (ch.isascii() and ch.isdigit()) or ch in "._"):
            continue
        if not out and not is_letter:
            continue
        if pending_dash:
            out.append("-")
            pending_dash = False
        out.append(ch)
    handle = "".join(out)
    return _HANDLE_FALLBACK if not handle or handle.lower() == RESERVED_HANDLE else handle


THREAD_STATUSES = ("proposed", "active", "paused", "closed", "rejected")
QUESTION_STATUSES = ("open", "answered", "dropped")
EXPERIMENT_STATUSES = ("planned", "running", "done", "abandoned", "vetoed")
TIMELINE_TAGS = ("research run", "experiment", "owner", "proposal")
FRONT_HEADINGS = (
    "Question",
    "Current understanding",
    "Open questions",
    "Answered questions",
    "Experiments",
    "Evidence",
    "Comments",
)

SLUG_RE = r"^[a-z0-9][a-z0-9-]{0,63}\Z"
QID_RE = r"Q[1-9]\d{0,3}"
EID_RE = r"E[1-9]\d{0,3}"

EXPERIMENT_HEADINGS = ("Hypothesis", "Method", "Data", "Result", "What it means")


CLOSE_PREFIX = "Proposing to close"  # a Scout thread comment that starts with this proposes closing the thread


@dataclass
class Comment:
    author: str
    text: str
    stamp: str | None
    line: int
    replies: list[Comment] = field(default_factory=list)

    @property
    def is_scout(self) -> bool:
        return self.author == "scout"


@dataclass
class Question:
    qid: str
    text: str
    status: str
    when: str
    note: str | None
    line: int
    comments: list[Comment] = field(default_factory=list)


@dataclass
class ExperimentRef:
    eid: str
    target: str
    title: str
    status: str
    when: str
    note: str | None
    line: int
    comments: list[Comment] = field(default_factory=list)


@dataclass
class EvidenceRef:
    target: str
    why: str
    line: int


@dataclass
class Problem:
    line: int | None
    message: str


def _unanswered(comments: list[Comment]) -> list[Comment]:
    return [c for c in comments if not c.is_scout and not any(r.is_scout for r in c.replies)]


@dataclass
class Thread:
    slug: str
    frontmatter: dict[str, Any]
    title: str
    question: str
    understanding: str
    open: list[Question]
    answered: list[Question]
    experiments: list[ExperimentRef]
    evidence: list[EvidenceRef]
    comments: list[Comment]
    problems: list[Problem] = field(default_factory=list)
    # The page's own visibility, ``normal`` or ``private``, as the privacy layer reads it
    # (:func:`scout.kb.privacy.own_visibility`: ``Private`` or ``secret`` is private, and so is frontmatter that
    # doesn't parse). The parser sets it; a Thread built any other way is private until it says otherwise.
    visibility: str = "private"

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", ""))

    def unanswered_owner_comments(self) -> list[tuple[str, Comment]]:
        """Owner comments with no Scout reply, as ``(target, comment)``.

        ``target`` is a question ID, an experiment ID or ``"thread"``.
        """
        out: list[tuple[str, Comment]] = []
        for q in [*self.open, *self.answered]:
            out.extend((q.qid, c) for c in _unanswered(q.comments))
        for e in self.experiments:
            out.extend((e.eid, c) for c in _unanswered(e.comments))
        out.extend(("thread", c) for c in _unanswered(self.comments))
        return out


@dataclass
class TimelineEntry:
    stamp: str
    tag: str
    bullets: list[str]


MODEL_REPO_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
MODEL_REVISION_RE = r"[0-9a-f]{40}"


@dataclass(frozen=True)
class ModelRef:
    """A model repo pinned to one commit: ``repo`` is ``owner/name``, ``revision`` a 40-hex commit."""

    repo: str
    revision: str


def timeout_hours_of(value: object) -> float | None:
    """``value`` as a timeout in hours when it is a positive, finite number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def disk_budget_of(value: object) -> int | None:
    """``value`` as a disk budget in bytes when it is a positive integer (not a bool), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def model_repo_ok(repo: object) -> bool:
    return isinstance(repo, str) and re.fullmatch(MODEL_REPO_RE, repo) is not None


def model_revision_ok(revision: object) -> bool:
    return isinstance(revision, str) and re.fullmatch(MODEL_REVISION_RE, revision) is not None


@dataclass
class Experiment:
    slug: str
    eid: str
    frontmatter: dict[str, Any]
    status: str
    questions: list[str]
    sections: dict[str, str]
    problems: list[Problem] = field(default_factory=list)
    visibility: str = "private"  # as :attr:`Thread.visibility`
    timeout_hours: float | None = None  # frontmatter ``timeout_hours`` when positive and finite
    models: list[ModelRef] = field(default_factory=list)  # the valid entries of frontmatter ``models``
    disk_budget_bytes: int | None = None  # frontmatter ``disk_budget_bytes`` when a positive integer

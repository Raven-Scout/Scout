"""Lab data model: vocabularies, ID regexes and the parsed dataclasses. No I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

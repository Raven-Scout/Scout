"""Index data model (spec §4.8). Plain dataclasses; ``Index.to_dict`` is the
JSON shape scout-app decodes, so field names here are a contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

# Severity order — the app and the digest sort by position in this tuple.
STATES: tuple[str, ...] = ("needs_you", "running", "waiting", "parked", "stale", "done")

TERMINAL_PR_STATES = frozenset({"MERGED", "CLOSED"})


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        return _iso(datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC))
    except (OverflowError, OSError, ValueError):
        return None


def ns_to_iso(ns: int) -> str:
    return _iso(datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC))


def dt_to_iso(dt: datetime) -> str:
    return _iso(dt)


def parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


@dataclass
class WorktreeInfo:
    path: str | None
    name: str | None
    branch: str | None
    source_branch: str | None
    dirty: bool


@dataclass
class PRInfo:
    number: int
    repo: str
    url: str | None
    state: str  # OPEN | MERGED | CLOSED | unknown
    is_draft: bool
    review_decision: str  # "" | APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | unknown
    review_requested: bool
    checks: str  # passing | failing | pending | none | unknown
    merge_state: str  # gh mergeStateStatus, e.g. CLEAN | DIRTY | BLOCKED | unknown
    fetched_at: str | None
    stale: bool
    updated_at: str | None

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class LastTurn:
    at: str | None
    kind: str  # end_turn | tool_use | question | unknown


@dataclass
class TranscriptInfo:
    path: str
    first_prompt: str
    files_touched: list[str]
    tool_calls: int
    last_turn: LastTurn
    mtime_ns: int


@dataclass
class AgentSession:
    id: str
    cli_session_id: str | None
    title: str | None
    title_source: str | None
    project_key: str
    group_name: str | None
    cwd: str
    origin_cwd: str
    worktree: WorktreeInfo | None
    created_at: str | None
    last_activity_at: str | None
    model: str | None
    effort: str | None
    turns: int | None
    is_archived: bool
    is_open: bool
    is_scout_run: bool
    parent_session_id: str | None
    spawned_task_id: str | None
    scheduled_task_id: str | None
    prs: list[PRInfo]
    pr: PRInfo | None
    transcript: TranscriptInfo | None
    state: str = "parked"
    state_reasons: list[str] = field(default_factory=list)


@dataclass
class Project:
    key: str
    name: str
    group_id: str | None
    counts: dict[str, int]


@dataclass
class SourceError:
    source: str
    message: str


@dataclass
class Index:
    generated_at: str
    source_counts: dict[str, int]
    source_errors: list[SourceError]
    display: dict[str, int]
    projects: list[Project]
    sessions: list[AgentSession]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body.pop("schema_version")
        return {"schema_version": self.schema_version, **body}


__all__ = [
    "SCHEMA_VERSION",
    "STATES",
    "TERMINAL_PR_STATES",
    "AgentSession",
    "Index",
    "LastTurn",
    "PRInfo",
    "Project",
    "SourceError",
    "TranscriptInfo",
    "WorktreeInfo",
    "dt_to_iso",
    "ms_to_iso",
    "now_utc",
    "ns_to_iso",
    "parse_iso",
]

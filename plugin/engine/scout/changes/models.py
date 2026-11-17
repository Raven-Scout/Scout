"""Row schemas for the change ledger and the engagement ledger. Rows carry no content (no prompt or message text,
no path outside the vault); readers ignore unknown keys."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1
SUMMARY_MAX = 120

CHANGE_KINDS = frozenset({"brain-edit", "driver", "phase", "kb-structure", "wishlist-done", "upgrade", "rollback"})
WHY_TYPES = frozenset({"pattern", "owner-ask", "wishlist", "research", "unknown"})
LANES = frozenset({"briefing", "consolidation", "dreaming", "research", "work", "interactive", "update"})
OWNER_KINDS = frozenset({"session-reliance", "app-edit", "inline-comment", "approval"})
RUN_KINDS = frozenset({"dm-reply", "dm-reaction", "dm-sent", "red-carried"})
SURFACES = frozenset({"slack-dm", "telegram", "kb", "action-items", "skill", "scoutctl", "vault-session", "app"})
SENTIMENTS = frozenset({"positive", "negative", "neutral"})


def _check(value: Any, allowed: frozenset[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name}: {value!r} not in {sorted(allowed)}")
    return value


def _str(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name}: expected a string, got {value!r}")
    return value


def _ts(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(_str(value, name) or "")
    except ValueError as exc:
        raise ValueError(f"{name}: not an ISO timestamp: {value!r}") from exc


def _strs(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name}: expected a list of strings")
    return tuple(value)


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name}: expected a bool, got {value!r}")
    return value


@dataclass(frozen=True)
class Why:
    type: str
    ref: str
    gloss: str | None = None
    inferred: bool = False

    def to_row(self) -> dict[str, Any]:
        return {"type": self.type, "ref": self.ref, "gloss": self.gloss, "inferred": self.inferred}

    @classmethod
    def from_row(cls, row: Any) -> Why:
        if not isinstance(row, dict):
            raise ValueError("why: expected an object")
        return cls(
            type=_check(row.get("type"), WHY_TYPES, "why.type"),
            ref=_str(row.get("ref"), "why.ref") or "",
            gloss=_str(row.get("gloss"), "why.gloss", optional=True),
            inferred=_bool(row.get("inferred", False), "why.inferred"),
        )


@dataclass(frozen=True)
class Change:
    id: str
    ts: datetime
    kind: str
    summary: str
    why: Why
    commit: str
    files: tuple[str, ...]
    targets: tuple[str, ...] = ()
    governance: bool = False
    author_lane: str = "interactive"
    backfilled: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "ts": self.ts.isoformat(),
            "kind": self.kind,
            "summary": self.summary,
            "why": self.why.to_row(),
            "commit": self.commit,
            "files": list(self.files),
            "targets": list(self.targets),
            "governance": self.governance,
            "author_lane": self.author_lane,
            "backfilled": self.backfilled,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Change:
        summary = _str(row.get("summary"), "summary") or ""
        if len(summary) > SUMMARY_MAX:
            raise ValueError(f"summary: {len(summary)} chars, max {SUMMARY_MAX}")
        return cls(
            id=_str(row.get("id"), "id") or "",
            ts=_ts(row.get("ts"), "ts"),
            kind=_check(row.get("kind"), CHANGE_KINDS, "kind"),
            summary=summary,
            why=Why.from_row(row.get("why")),
            commit=_str(row.get("commit"), "commit") or "",
            files=_strs(row.get("files", []), "files"),
            targets=_strs(row.get("targets", []), "targets"),
            governance=_bool(row.get("governance", False), "governance"),
            author_lane=_check(row.get("author_lane", "interactive"), LANES, "author_lane"),
            backfilled=_bool(row.get("backfilled", False), "backfilled"),
        )


@dataclass(frozen=True)
class Signal:
    ts: datetime
    kind: str
    surface: str
    ref: str | None = None
    sentiment: str | None = None
    session: str | None = None
    vault_key: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ts": self.ts.isoformat(),
            "kind": self.kind,
            "surface": self.surface,
            "ref": self.ref,
            "sentiment": self.sentiment,
            "session": self.session,
            "vault_key": self.vault_key,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Signal:
        sentiment = row.get("sentiment")
        if sentiment is not None:
            _check(sentiment, SENTIMENTS, "sentiment")
        return cls(
            ts=_ts(row.get("ts"), "ts"),
            kind=_check(row.get("kind"), OWNER_KINDS | RUN_KINDS, "kind"),
            surface=_check(row.get("surface"), SURFACES, "surface"),
            ref=_str(row.get("ref"), "ref", optional=True),
            sentiment=sentiment,
            session=_str(row.get("session"), "session", optional=True),
            vault_key=_str(row.get("vault_key"), "vault_key", optional=True),
        )

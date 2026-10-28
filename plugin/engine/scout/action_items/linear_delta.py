"""The user's own Linear activity a run must report.

The Linear connector phase asks every briefing and consolidation to report
what the user did in Linear since the last run. The runner pre-fetches it into
``.scout-cache/session-context.json`` (``linear_own_activity``, see
:mod:`scout.linear_activity`) when the vault has a Linear API key. Each
pre-fetched issue must then be named in today's 📋 Scout Digest, under a
``### Your Linear Activity`` heading.

Without a key, or when the fetch failed, nothing was pre-fetched: the session
builds the report from the Linear connector, and the check asks only that the
heading exists with at least one bullet (``- None since <when>`` counts, and
so does a line saying Linear could not be read).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scout.action_items.prefetched import (
    PrefetchCheckError,
    context_path,
    daily_lines,
    load_context,
    section,
)

DIGEST = "📋 Scout Digest"
HEADING = "Your Linear Activity"
_BULLET = re.compile(r"^\s*[-*]\s+\S")


@dataclass(frozen=True)
class LinearIssue:
    identifier: str
    title: str
    actions: tuple[str, ...]
    state: str

    def mentioned_in(self, text: str) -> bool:
        return bool(re.search(rf"(?<![\w-]){re.escape(self.identifier)}(?!\d)", text, re.IGNORECASE))


@dataclass(frozen=True)
class LinearDeltaCheck:
    status: str  # the pre-fetch's status: "fetched", "no_key", "failed: …", "skipped" or "" (older engine)
    since: str
    prefetched: tuple[LinearIssue, ...] | None  # None: nothing was pre-fetched
    unreported: tuple[LinearIssue, ...]
    reported: bool  # the heading exists with a bullet (what the fallback asks for)

    @property
    def passed(self) -> bool:
        if self.prefetched is None:
            return self.reported
        return not self.unreported


def _issues(raw: object) -> tuple[LinearIssue, ...] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict) or not entry.get("identifier"):
            raise PrefetchCheckError(f"linear_own_activity entry {i} names no identifier")
        out.append(
            LinearIssue(
                identifier=str(entry["identifier"]),
                title=str(entry.get("title") or ""),
                actions=tuple(str(a) for a in entry.get("actions") or ()),
                state=str(entry.get("state") or ""),
            )
        )
    return tuple(out)


def report_lines(lines: Sequence[str]) -> list[str] | None:
    """The lines under ``### Your Linear Activity`` in the 📋 Scout Digest, or None."""
    digest = section(lines, DIGEST, level=2)
    return None if digest is None else section(digest, HEADING, level=3)


def check_linear_delta(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> LinearDeltaCheck:
    """Compare the pre-fetched Linear activity with today's 📋 Scout Digest in ``data_dir``."""
    data = load_context(context_path(data_dir, context), today=today)
    prefetched = _issues(data.get("linear_own_activity"))
    status = str(data.get("linear_own_activity_status") or "")
    since = str(data.get("linear_own_activity_since") or "")
    if prefetched == ():
        return LinearDeltaCheck(status, since, (), (), True)
    report = report_lines(daily_lines(data_dir, today))
    text = "\n".join(report or [])
    reported = report is not None and any(_BULLET.match(line) for line in report)
    unreported = tuple(i for i in prefetched or () if not i.mentioned_in(text))
    return LinearDeltaCheck(status, since, prefetched, unreported, reported)

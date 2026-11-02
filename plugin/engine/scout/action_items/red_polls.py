"""The 🔴 project channels a run must poll.

The Slack connector's 🔴 Project Channel Poll reads every channel a 🔴
project's ``slack_channels:`` frontmatter declares, directly, each briefing and
consolidation, and lists them on the run's 🪵 Run notes entry as
``🔴 channels polled: #a, #b``. The runner pre-fetches the list from the KB
into ``.scout-cache/session-context.json`` (``red_project_channels``), so the
session doesn't have to find the projects, and this check names each channel
that no ``channels polled:`` line on this run's Run notes entry lists (by
``#name`` or id). This run's entry is one that wasn't in the file before the
session (``run_notes_before``): an earlier run's line doesn't count.

A 🔴 project with no ``slack_channels:`` is reported, not failed: the phase
asks the run to add the field.
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
    run_note_entries,
    strings,
)

POLLED_LINE_FORMAT = "🔴 channels polled: #a, #b"
_POLLED = re.compile(r"channels polled:\s*(?P<list>.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class RedChannel:
    project: str
    id: str
    name: str

    @property
    def label(self) -> str:
        return " ".join(p for p in (self.name, f"({self.id})" if self.id and self.name else self.id) if p)

    def listed_in(self, text: str) -> bool:
        def has(token: str) -> bool:
            return bool(token) and bool(re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text, re.IGNORECASE))

        return has(self.id) or has(self.name) or has(self.name.lstrip("#"))


@dataclass(frozen=True)
class RedPollCheck:
    channels: tuple[RedChannel, ...]
    unpolled: tuple[RedChannel, ...]
    unconfigured: tuple[str, ...]  # 🔴 projects with no slack_channels
    has_entry: bool = True  # today's 🪵 Run notes has an entry this run added


def _projects(raw: object) -> tuple[tuple[RedChannel, ...], tuple[str, ...]]:
    if raw is None:
        return (), ()
    if not isinstance(raw, list):
        raise PrefetchCheckError("red_project_channels is not a list")
    channels: list[RedChannel] = []
    bare: list[str] = []
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise PrefetchCheckError(f"red_project_channels entry {i} is not an object")
        project = str(entry.get("project") or entry.get("path") or f"entry {i}")
        found = [c for c in entry.get("channels") or [] if isinstance(c, dict) and (c.get("id") or c.get("name"))]
        if not found:
            bare.append(project)
        channels += [RedChannel(project, str(c.get("id") or ""), str(c.get("name") or "")) for c in found]
    return tuple(channels), tuple(bare)


def polled_text(lines: Sequence[str]) -> str:
    """Everything after ``channels polled:`` on any of ``lines``."""
    return "\n".join(m.group("list") for line in lines if (m := _POLLED.search(line)))


def check_red_polls(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> RedPollCheck:
    """Compare the pre-fetched 🔴 project channels with today's ``channels polled:`` lines."""
    data = load_context(context_path(data_dir, context), today=today)
    channels, bare = _projects(data.get("red_project_channels"))
    if not channels:
        return RedPollCheck((), (), bare)
    before = {line.strip() for line in strings(data.get("run_notes_before"))}
    fresh = [e for e in run_note_entries(daily_lines(data_dir, today)) if e[0].strip() not in before]
    text = polled_text([line for entry in fresh for line in entry])
    return RedPollCheck(channels, tuple(c for c in channels if not c.listed_in(text)), bare, bool(fresh))

"""`action-items/topics.md`: one `## [#TAG] Title` heading per workstream.

Scout runs write each topic's title, `- Session:` and `- Summary:`. The
`- Renamed:` line is the user's own name for the topic and only
`rename_topic` writes it, so a run never overrides it. Topic tags share the
namespace of task tags, so `new-prefix` never mints one a topic uses.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout import paths
from scout.action_items.writer import _read_lines_with_style, atomic_write_lines
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid

TOPICS_FILE = "topics.md"

_HEADING_RE = re.compile(r"^##\s+\[#(?P<tag>(?=[A-Z0-9]{2,8}\])[A-Z0-9]*[A-Z][A-Z0-9]*)\]")
_FIELD_RE = re.compile(r"^\s*-\s+[A-Za-z]+:")
_RENAMED_RE = re.compile(r"^\s*-\s+Renamed:", re.IGNORECASE)


def topics_path(data_dir: Path | None = None) -> Path:
    return paths.action_items_dir(data_dir) / TOPICS_FILE


def topic_tags(data_dir: Path | None = None) -> set[str]:
    """Tags of the topics in `topics.md`; empty when the file is missing."""
    path = topics_path(data_dir)
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {m.group("tag") for m in map(_HEADING_RE.match, lines) if m is not None}


def rename_topic(tag: str, name: str, *, data_dir: Path | None = None) -> Event:
    """Set the user's name for topic `tag`; an empty `name` clears it."""
    if "\n" in name or "\r" in name:
        raise ActionItemError("rename-topic: the name must be one line")
    path = topics_path(data_dir)
    if not path.exists():
        raise ActionItemError(f"rename-topic: {path} does not exist")
    lines, newline, trailing_newline = _read_lines_with_style(path)
    start = _heading_index(lines, tag.upper())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    block = [line for line in lines[start + 1 : end] if not _RENAMED_RE.match(line)]
    clean = name.strip()
    if clean:
        last_field = max((i for i, line in enumerate(block) if _FIELD_RE.match(line)), default=-1)
        block.insert(last_field + 1, f"- Renamed: {clean}")
    atomic_write_lines(
        path, lines[: start + 1] + block + lines[end:], newline=newline, trailing_newline=trailing_newline
    )
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.topic_renamed",
        source="cli:rename-topic",
        payload={"tag": tag.upper(), "name": clean or None},
    )


def _heading_index(lines: list[str], tag: str) -> int:
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m is not None and m.group("tag") == tag:
            return i
    raise ActionItemError(f"rename-topic: no topic [#{tag}] in {TOPICS_FILE}")

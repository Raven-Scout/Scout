"""`action-items/topic-names.md`: the user's own names for topics.

One `- [#TAG] Name` line per renamed topic. Only `rename_topic` writes it and
runs never do, so a name survives every rewrite of `topics.md` and a run that
has `topics.md` in memory can't overwrite it.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout import paths
from scout.action_items.topics import TOPIC_TAG, TOPICS_FILE, topic_tags
from scout.action_items.writer import _read_lines_with_style, atomic_write_lines
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid

TOPIC_NAMES_FILE = "topic-names.md"
_HEADER = ["# Topic names", "", "Your own names for topics, set in the app. Scout runs never write this file.", ""]
_LINE_RE = re.compile(rf"^\s*-\s+\[#(?P<tag>{TOPIC_TAG})\]\s+(?P<name>.*?)\s*$")


def topic_names_path(data_dir: Path | None = None) -> Path:
    return paths.action_items_dir(data_dir) / TOPIC_NAMES_FILE


def parse_topic_names(text: str) -> dict[str, str]:
    """`{tag: name}`; a later line for the same tag wins."""
    names: dict[str, str] = {}
    for m in map(_LINE_RE.match, text.splitlines()):
        if m is not None and m.group("name"):
            names[m.group("tag")] = m.group("name")
    return names


def rename_topic(tag: str, name: str, *, data_dir: Path | None = None) -> Event:
    """Set the user's name for topic `tag`; an empty `name` clears it. Names
    of topics no longer in `topics.md` are dropped on the way."""
    if "\n" in name or "\r" in name:
        raise ActionItemError("rename-topic: the name must be one line")
    tag = tag.upper()
    live = topic_tags(data_dir)
    if tag not in live:
        raise ActionItemError(f"rename-topic: no topic [#{tag}] in {TOPICS_FILE}")
    path = topic_names_path(data_dir)
    lines, newline, trailing = _read_lines_with_style(path) if path.exists() else (list(_HEADER), "\n", True)
    kept = [line for line in lines if (named := _named_tag(line)) is None or (named in live and named != tag)]
    clean = name.strip()
    if clean:
        kept.append(f"- [#{tag}] {clean}")
    atomic_write_lines(path, kept, newline=newline, trailing_newline=trailing)
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.topic_renamed",
        source="cli:rename-topic",
        payload={"tag": tag, "name": clean or None},
    )


def _named_tag(line: str) -> str | None:
    m = _LINE_RE.match(line)
    return m.group("tag") if m is not None else None

"""Inbox and Loose ends bullets: the key `dismiss` records for each.

Mirrors the app's `SideEntry`: the key is the entry's link, else its bare URL,
else its text after any `Source — ` prefix. `drop_dismissed` removes listed
entries deterministically, so a dismissal holds even if a run re-adds one.
"""

from __future__ import annotations

import re

SIDE_SECTION_TITLES = frozenset({"inbox", "loose ends"})

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_BULLET_RE = re.compile(r"^\s*-\s+(?P<rest>.+?)\s*$")
_TASK_RE = re.compile(r"^\s*-\s+\[[ xX]\]")
_H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_TRAILING_NOTE_RE = re.compile(r"\s*\(.*?\)\s*$")


def side_entry_key(bullet: str) -> str:
    parts = bullet.split(" — ")
    rest = (" — ".join(parts[1:]) if len(parts) > 1 else bullet).strip()
    if m := _LINK_RE.search(rest):
        return m.group(2)
    if m := _BARE_URL_RE.search(rest):
        return m.group(0)
    return rest


def is_side_section(heading: str) -> bool:
    """An Inbox or Loose ends `##` heading, with or without a leading emoji
    or a trailing note such as `(3)`. The app's `SideEntry` agrees."""
    m = _H2_RE.match(heading)
    if m is None:
        return False
    title = _TRAILING_NOTE_RE.sub("", m.group("title")).lower()
    words = title.split(maxsplit=1)
    after_emoji = words[1] if len(words) == 2 and not words[0][0].isalnum() else None
    return title in SIDE_SECTION_TITLES or after_emoji in SIDE_SECTION_TITLES


def drop_dismissed(lines: list[str], dismissed: set[str]) -> list[str]:
    """`lines` without the Inbox and Loose ends bullets whose key is dismissed,
    and without the lines indented under them."""
    if not dismissed:
        return lines
    out: list[str] = []
    in_side = False
    dropped_indent: int | None = None
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if dropped_indent is not None and line.strip() and indent > dropped_indent:
            continue
        dropped_indent = None
        if line.startswith("## "):
            in_side = is_side_section(line)
        elif line.startswith("# "):
            in_side = False
        elif in_side and not _TASK_RE.match(line) and (m := _BULLET_RE.match(line)):
            if side_entry_key(m.group("rest")) in dismissed:
                dropped_indent = indent
                continue
        out.append(line)
    return out

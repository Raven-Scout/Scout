"""`action-items/dismissed.md`: Inbox and Loose ends entries the user closed.

One `- <key>` bullet per entry, where the key is the entry's URL or else its
text. Runs read it and never re-add a listed entry; only `dismiss` writes it.
"""

from __future__ import annotations

from pathlib import Path

from scout import paths
from scout.action_items.writer import _read_lines_with_style, atomic_write_lines
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid

DISMISSED_FILE = "dismissed.md"
_HEADER = ["# Dismissed", "", "Inbox and Loose ends entries closed in the app. Scout never re-adds these.", ""]


def dismissed_path(data_dir: Path | None = None) -> Path:
    return paths.action_items_dir(data_dir) / DISMISSED_FILE


def dismissed_keys(data_dir: Path | None = None) -> list[str]:
    path = dismissed_path(data_dir)
    if not path.exists():
        return []
    lines, _newline, _trailing = _read_lines_with_style(path)
    return [line[2:].strip() for line in lines if line.startswith("- ") and line[2:].strip()]


def dismiss(key: str, *, data_dir: Path | None = None) -> Event:
    """Record `key` as dismissed. Dismissing a listed key again changes nothing."""
    clean = key.strip()
    if not clean or "\n" in clean or "\r" in clean:
        raise ActionItemError("dismiss: the key must be one non-empty line")
    path = dismissed_path(data_dir)
    added = clean not in dismissed_keys(data_dir)
    if added:
        lines, newline, trailing = _read_lines_with_style(path) if path.exists() else (list(_HEADER), "\n", True)
        atomic_write_lines(path, [*lines, f"- {clean}"], newline=newline, trailing_newline=trailing)
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.dismissed",
        source="cli:dismiss",
        payload={"key": clean, "added": added},
    )

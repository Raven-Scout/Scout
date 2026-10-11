"""`action-items/topics.md`: one `## [#TAG] Title` heading per workstream.

Topic tags share the namespace of task tags, so `new-prefix` never mints one
that a topic already uses.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout import paths

TOPICS_FILE = "topics.md"

_HEADING_RE = re.compile(r"^##\s+\[#(?P<tag>(?=[A-Z0-9]{2,8}\])[A-Z0-9]*[A-Z][A-Z0-9]*)\]")


def topics_path(data_dir: Path | None = None) -> Path:
    return paths.action_items_dir(data_dir) / TOPICS_FILE


def topic_tags(data_dir: Path | None = None) -> set[str]:
    """Tags of the topics in `topics.md`; empty when the file is missing."""
    path = topics_path(data_dir)
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {m.group("tag") for m in map(_HEADING_RE.match, lines) if m is not None}

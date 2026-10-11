"""Locate a markdown file's YAML frontmatter block by its fence lines.

A fence is a line that is exactly ``---`` (trailing whitespace allowed). A
``---`` run anywhere else — inside a ``#`` comment, a value, or an indented
block scalar — is content, not a fence (#239). The vault's own
``knowledge-base/ontology/parser.py`` cannot import this module and carries a
copy of the same rule.
"""

from __future__ import annotations

from collections.abc import Sequence

FENCE = "---"


def frontmatter_block(lines: Sequence[str], *, require_close: bool = True) -> list[str] | None:
    """The lines between the opening fence on line one and the closing fence.

    None when line one is not a fence, or when no closing fence follows and
    ``require_close`` is set: a truncated block is not a parse. Pass
    ``require_close=False`` when ``lines`` is a head window that may stop
    inside a long block; the rest of the window is then returned.
    """
    if not lines or lines[0].rstrip() != FENCE:
        return None
    for i, raw in enumerate(lines[1:], 1):
        if raw.rstrip() == FENCE:
            return list(lines[1:i])
    return None if require_close else list(lines[1:])

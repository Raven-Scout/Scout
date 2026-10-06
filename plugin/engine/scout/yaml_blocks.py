"""Comment-preserving splitter for block-style YAML mappings.

Shared by ``custom_connectors.write`` (``connectors.custom.yaml``, entries
nested under a ``connectors:`` key) and ``bootstrap._dump_keeping_comments``
(``scout-config.yaml``, entries at the top level). Kept import-light (stdlib +
``yaml`` only, no sibling ``scout.*`` imports) because ``custom_connectors`` is
imported by the connector-log hook on every Claude Code tool call.
"""

from __future__ import annotations

import yaml

Segment = tuple[str, list[str], list[str]]  # (key, leading comment/blank lines, key line + its body)


def split_blocks(
    text: str, *, indent: int = 0, after_key: str | None = None
) -> tuple[list[str], list[Segment], list[str]] | None:
    """Split ``text`` into ``(header, segments, trailer)``.

    ``segments`` are ``(key, leading, block)`` triples in file order: ``leading``
    is the run of comment/blank lines directly above ``key``, and ``block`` is
    the key's own line plus everything nested under it, at ``indent`` spaces.
    ``trailer`` is the comment/blank lines after the last segment.

    If ``after_key`` is given, the mapping being split is the value of a bare
    ``<after_key>:`` line at column 0 (nothing else on that line, i.e.
    block-style — or exactly ``<after_key>: {}``, treated as an empty block so a
    later rewrite can still take the fast path). Everything up to and including
    that line is returned verbatim as ``header``; ``indent`` is then relative to
    column 0 (e.g. 2 for entries nested one level under it). If ``after_key`` is
    ``None``, the whole text is the mapping (``indent`` usually 0) and ``header``
    is always ``[]``.

    Returns ``None`` whenever the layout can't be split with confidence, so the
    caller falls back to a plain dump: tabs anywhere, a document marker, no bare
    ``<after_key>:`` line when one is required, a line that dedents below
    ``indent`` inside the block (unexpected content after it), a candidate entry
    line that doesn't parse as a single-key mapping, or duplicate keys. A
    sequence item (``- foo``) at ``indent`` is treated as part of the previous
    entry's content, matching a mapping whose value is a block sequence.
    """
    if "\t" in text:
        return None
    lines = text.splitlines(keepends=True)

    header: list[str] = []
    if after_key is not None:
        bare = f"{after_key}:"
        empty = f"{after_key}: {{}}"
        conn_idx: int | None = None
        for i, line in enumerate(lines):
            if line.startswith(("---", "...")):
                return None
            if conn_idx is not None:
                continue
            rstripped = line.rstrip("\r\n")
            if rstripped == bare or rstripped == empty:
                conn_idx = i
        if conn_idx is None:
            return None
        header = lines[: conn_idx + 1]
        lines = lines[conn_idx + 1 :]

    def is_filler(line: str) -> bool:
        return not line.strip() or line.lstrip().startswith("#")

    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if is_filler(line):
            continue
        if line.startswith(("---", "...")):
            return None
        own_indent = len(line) - len(line.lstrip(" "))
        if own_indent < indent:
            return None  # dedented below entry level: unexpected content
        if own_indent > indent:
            continue  # content nested inside the current entry
        candidate = line[indent:]
        if candidate.startswith("- ") or candidate.rstrip("\r\n") == "-":
            continue  # a sequence item at entry level: part of the previous key's value
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict) or len(parsed) != 1:
            return None
        starts.append((i, str(next(iter(parsed)))))

    if len({key for _, key in starts}) != len(starts):
        return None

    def is_trailing_filler(line: str) -> bool:
        """Whether ``line`` may be reattributed from one segment's tail to the
        next segment's leading run: a blank line never is (it may be a
        significant trailing line of a ``|+``/``>+`` block scalar), and a
        comment only is when its own indentation is no deeper than entry level
        — deeper means it's inside the current entry's content (e.g. a literal
        block-scalar line that happens to start with ``#``), not a genuine gap
        comment documenting the next one."""
        if not line.strip():
            return False
        stripped = line.lstrip(" ")
        if not stripped.startswith("#"):
            return False
        comment_indent = len(line) - len(stripped)
        return comment_indent <= indent

    segments: list[Segment] = []
    lead_from = 0
    for n, (start, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        while end > start + 1 and is_trailing_filler(lines[end - 1]):
            end -= 1
        segments.append((key, lines[lead_from:start], lines[start:end]))
        lead_from = end
    return header, segments, lines[lead_from:]

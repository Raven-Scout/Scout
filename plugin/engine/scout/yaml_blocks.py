"""Comment-preserving splitter for block-style YAML mappings.

Shared by ``custom_connectors.write`` (``connectors.custom.yaml``, entries
nested under a ``connectors:`` key) and ``bootstrap._dump_keeping_comments``
(``scout-config.yaml``, entries at the top level). Kept import-light (stdlib +
``yaml`` only, no sibling ``scout.*`` imports) because ``custom_connectors`` is
imported by the connector-log hook on every Claude Code tool call.
"""

from __future__ import annotations

from typing import Any

import yaml

Segment = tuple[str, list[str], list[str]]  # (key, leading comment/blank lines, key line + its body)


def _safe_load_or_sentinel(text: str) -> Any:
    """``yaml.safe_load``, or a fresh, unique object when it can't be parsed or
    compared (a malformed fragment, or a cyclic alias that makes ``==`` raise
    ``RecursionError``). The sentinel is equal only to itself, so two separate
    "couldn't tell" results never compare equal to one another by accident."""
    try:
        return yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError):
        return object()  # a fresh instance, equal only to itself


def _leading_whitespace(line: str) -> str:
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    return line[:i]


def _has_tab_indentation(text: str) -> bool:
    """True when some line's own indentation (not its content) contains a tab.
    A tab elsewhere on a line -- inside a comment or a quoted value -- is fine;
    only a tab doing the job of indentation defeats the column-counting this
    module's splitting depends on."""
    return any("\t" in _leading_whitespace(line) for line in text.splitlines())


def _is_filler(line: str) -> bool:
    """A blank line, or a comment at any indent."""
    return not line.strip() or line.lstrip().startswith("#")


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
    caller falls back to a plain dump: a tab doing the job of indentation
    anywhere, a document marker, no bare ``<after_key>:`` line when one is
    required, a line that dedents below ``indent`` inside the block (unexpected
    content after it), a candidate entry line that doesn't parse as a
    single-key mapping, or duplicate keys. A sequence item (``- foo``) at
    ``indent`` is treated as part of the previous entry's content, matching a
    mapping whose value is a block sequence.
    """
    if _has_tab_indentation(text):
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

    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if _is_filler(line):
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

    segments: list[Segment] = []
    lead_from = 0
    for n, (start, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)

        # The candidate run: the maximal trailing stretch of blank/comment
        # lines, at any indent, right before the next entry (or EOF).
        run_start = end
        while run_start > start + 1 and _is_filler(lines[run_start - 1]):
            run_start -= 1

        # Trim the longest suffix of that run that is safe to move to the
        # next segment's leading run: the one whose removal doesn't change
        # what this block parses to. A deep `#`-led line inside a `|`/`>`
        # scalar, or a significant trailing blank line of a `|+`/`>+` scalar,
        # changes the value when dropped, so it stays; a genuine gap comment
        # or blank line does not, so it moves. Shrinks from the top of the
        # run (the earliest, i.e. least trimming) until the values match;
        # trimming nothing at all is always a match, so this always ends.
        full_value = _safe_load_or_sentinel("".join(lines[start:end]))
        cut = end
        for c in range(run_start, end):
            try:
                equal = _safe_load_or_sentinel("".join(lines[start:c])) == full_value
            except RecursionError:
                equal = False
            if equal:
                cut = c
                break

        segments.append((key, lines[lead_from:start], lines[start:cut]))
        lead_from = cut
    return header, segments, lines[lead_from:]

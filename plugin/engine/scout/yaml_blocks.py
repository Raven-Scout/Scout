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


def _has_tab_indentation(text: str, indent: int) -> bool:
    """True when a tab appears within the first ``indent + 1`` columns of some
    line's own leading whitespace -- the zone this module's own structural
    indentation counting (``own_indent = len(line) - len(line.lstrip(" "))``,
    which only counts spaces) could be thrown off by a tab standing in for a
    space. A tab deeper than that -- inside an entry's own nested content,
    e.g. a shell command line in a ``|`` scalar -- is just data and doesn't
    affect any column-counting this module does, so it's left alone."""
    limit = indent + 1
    return any("\t" in _leading_whitespace(line)[:limit] for line in text.splitlines())


def _is_filler(line: str) -> bool:
    """A blank line, or a comment at any indent."""
    return not line.strip() or line.lstrip().startswith("#")


def _is_shallow_comment(line: str, indent: int) -> bool:
    """A comment indented no deeper than ``indent`` -- never part of a
    ``|``/``>`` scalar's content (which always sits deeper than its
    announcing key, itself deeper than ``indent``), so it ends any such
    scalar above it. Blank lines are deliberately excluded: a blank line
    *before* that point may be a significant ``|+``/``>+`` keep-tail line,
    which this cheap, line-only check can't tell."""
    stripped = line.lstrip(" ")
    return stripped.startswith("#") and (len(line) - len(stripped)) <= indent


def _parses_to(text: str, value: Any) -> bool:
    """Whether ``text`` parses to exactly ``value`` (itself already a parsed
    result, e.g. from `_safe_load_or_sentinel`) -- one ``safe_load`` call."""
    try:
        return _safe_load_or_sentinel(text) == value
    except RecursionError:
        return False


def fit_leading(prev_block: str, leading: list[str], indent: int) -> list[str]:
    """A safe version of ``leading`` (a run of comment/blank lines, as returned
    by `split_blocks`) to emit directly after ``prev_block``, when
    ``prev_block`` is NOT what originally, in the source text, preceded
    ``leading`` -- because an entry in between was dropped, or ``prev_block``
    itself was rewritten.

    The hazard: if ``prev_block`` ends inside a ``|``/``>`` scalar, lines that
    were originally a harmless gap comment somewhere else in the file can get
    silently absorbed into that scalar's value once they're moved to sit
    right after it, instead of staying inert comments -- corrupting
    ``prev_block``'s value (caught by `split_blocks`'s/the caller's own
    round-trip check, but only by falling back to a full plain dump, losing
    every comment in the file, not just this one run).

    Returns ``leading`` verbatim when appending it doesn't change what
    ``prev_block`` parses to (the common case -- most boundaries are between
    a mapping value and a sibling key, which nothing can absorb into). If
    that's not safe, tries a reduced form instead: every comment line
    re-indented to ``indent`` (shallower than any scalar's own content, so it
    can't be absorbed) with blank lines dropped entirely (a significant
    `` |+``/``>+`` keep-tail blank line would otherwise still be swallowed).
    If even that isn't safe, returns ``[]`` -- dropping just this one leading
    run, never the whole file's comments, which the caller's own full
    round-trip check would otherwise do. At most 3 ``safe_load`` calls.
    """
    prev_value = _safe_load_or_sentinel(prev_block)
    if _parses_to(prev_block + "".join(leading), prev_value):
        return leading
    reduced = [" " * indent + line.lstrip(" \t") for line in leading if line.strip()]
    if reduced and _parses_to(prev_block + "".join(reduced), prev_value):
        return reduced
    return []


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
    caller falls back to a plain dump: a tab that could be doing the job of
    indentation (within the first ``indent + 1`` columns of some line), a
    document marker, no bare ``<after_key>:`` line when one is required, a
    line that dedents below ``indent`` inside the block (unexpected content
    after it), a candidate entry line that doesn't parse as a single-key
    mapping, or duplicate keys. A sequence item (``- foo``) at ``indent`` is
    treated as part of the previous entry's content, matching a mapping whose
    value is a block sequence.
    """
    if _has_tab_indentation(text, indent):
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

        # Trim the longest safe-to-move suffix of that run, at a bounded cost
        # (at most 3 `safe_load` calls, regardless of the run's length): a
        # deep `#`-led line inside a `|`/`>` scalar, or a significant trailing
        # blank line of a `|+`/`>+` scalar, changes the value when dropped, so
        # it must stay; a genuine gap comment or blank line does not, so it's
        # safe to move to the next segment's leading run.
        #
        # (a) Try the whole run first -- cheap, and the common case (nothing
        #     in the run is scalar content) needs only this one comparison.
        # (b) If that changes the value, cut at the run's FIRST comment
        #     indented no deeper than entry level instead. Such a comment
        #     ends any `|`/`>` scalar above it (its content always sits
        #     deeper), so every line from it on -- blank lines and deeper
        #     comments included -- is a comment or a gap, never scalar
        #     content. The lines before it stay with this block: they may be
        #     deep `#`-led content or a `|+`/`>+` keep-tail's significant
        #     blank lines, which this cheap check can't tell apart. (Cutting
        #     only at a trailing run of such comments, as before, recovered
        #     nothing as soon as a blank line followed the comment.)
        # (c) If even that changes the value, trim nothing -- leave the whole
        #     run with this segment. Trimming nothing is always a match (it's
        #     the same text), so (b) or (c) always ends this.
        block_text = "".join(lines[start:end])
        full_value = _safe_load_or_sentinel(block_text)
        cut = end
        if _parses_to("".join(lines[start:run_start]), full_value):
            cut = run_start
        else:
            shallow_cut = next((i for i in range(run_start, end) if _is_shallow_comment(lines[i], indent)), end)
            if shallow_cut < end and _parses_to("".join(lines[start:shallow_cut]), full_value):
                cut = shallow_cut

        segments.append((key, lines[lead_from:start], lines[start:cut]))
        lead_from = cut
    return header, segments, lines[lead_from:]

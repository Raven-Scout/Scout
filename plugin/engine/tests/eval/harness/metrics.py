"""Process metrics from a ``claude -p --output-format stream-json`` transcript (spec 2026-10-12 §6.2).

Pure: it takes lines and returns values, never touches files or processes
(``tests/unit/test_eval_purity.py`` enforces that), so every metric is tested
against a committed synthetic stream.

Two ideas are borrowed from tool-position analysis of agent traces:

- each call's **position** in the run, ``(seq - 1) / (total - 1)`` (a lone
  call sits at 0.5), so "the run now writes action items later" is visible
  whatever the run's length;
- a **self-consistent sample record**: every count is derived from the call
  list, and ``conforms()`` re-derives it, so a parsing bug shows up as a
  conformance failure, not as a quietly wrong median.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

SEARCH_TOOLS = {"Grep", "Glob"}
_SEARCH_BASH = re.compile(r"^\s*(?:rg|grep|egrep|fgrep|find|ag|fd)\b")
GRAPH_VERB_RE = re.compile(
    r"\bscoutctl\s+kb\s+(?:neighbors|paths|path|traverse|related|backlinks|link-gaps)\b"
    r"|\bscoutctl\s+kb\s+search\b[^\n;|&]*--ranked\b"
)
FILE_TOOLS = {"Read", "Edit", "Write", "NotebookEdit"}
# The static instructions a run loads: the brain files, and any slice or deferred
# block cut from them (spec 2026-10-13-static-instruction-trim §8.1).
BRAIN_FILES = {"SKILL.md", "DREAMING.md", "RESEARCH.md"}
BRAIN_CACHE_DIR = "/.scout-cache/brain/"
GATE_MARK = re.compile(r"\[scout:(kb-read-gate|outbound-gate)\]")


@dataclass(frozen=True)
class ToolCall:
    seq: int
    name: str
    input: dict[str, Any]
    is_error: bool
    position: float
    # The Scout PreToolUse gate that denied the call (``kb-read-gate``, ``outbound-gate``), read from
    # the ``[scout:<gate>]`` mark its error result carries; None when it ran. None in an older metrics.json.
    gate: str | None = None
    # Claude Code's own permission check refused it (the result's ``permission_denials``), not a hook or the
    # tool. False in an older metrics.json.
    denied: bool = False


@dataclass(frozen=True)
class SampleMetrics:
    calls: tuple[ToolCall, ...]
    tool_call_count: int
    error_count: int
    turns: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float | None
    duration_s: float | None
    denials: tuple[str, ...]
    model: str | None
    mcp_servers: tuple[str, ...]
    result_subtype: str | None
    skipped_lines: int
    # None in a metrics.json stored before these existed: unknown, not zero.
    cache_creation_tokens: int | None = None
    # chars/4 of what successful brain-file Reads returned, and that times the
    # assistant turns after each read: every later turn re-reads it from cache.
    brain_tokens: int | None = None
    brain_token_turns: int | None = None
    # The API retries claude logged (`system/api_retry`), and the result's text when the
    # session ended in an error (`is_error`), e.g. "API Error: Can't reach the API server".
    api_retries: int | None = None
    api_error: str | None = None


def _position(seq: int, total: int) -> float:
    return 0.5 if total == 1 else (seq - 1) / (total - 1)


def _is_brain_read(name: str, inp: dict[str, Any]) -> bool:
    path = str(inp.get("file_path", ""))
    return name == "Read" and (path.rsplit("/", 1)[-1] in BRAIN_FILES or BRAIN_CACHE_DIR in path)


def _result_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(b.get("text", ""))) for b in content if isinstance(b, dict))
    return 0


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def parse_stream(lines: Iterable[str]) -> SampleMetrics:
    uses: list[tuple[str, str, dict[str, Any]]] = []
    errors: set[str] = set()
    gates: dict[str, str] = {}
    message_ids: set[str] = set()
    brain_uses: set[str] = set()
    # (tokens, assistant messages seen when the result arrived) per brain read
    brain_reads: list[tuple[int, int]] = []
    init: dict[str, Any] = {}
    result: dict[str, Any] = {}
    retries = 0
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(event, dict):
            skipped += 1
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = event
        elif kind == "system" and event.get("subtype") == "api_retry":
            retries += 1
        elif kind == "assistant":
            message = event.get("message") or {}
            if message.get("id"):
                message_ids.add(message["id"])
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    uses.append((block.get("id", ""), block.get("name", ""), block.get("input") or {}))
                    if _is_brain_read(uses[-1][1], uses[-1][2]):
                        brain_uses.add(uses[-1][0])
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if block.get("is_error"):
                    errors.add(block.get("tool_use_id", ""))
                    mark = GATE_MARK.search(_result_text(block.get("content")))
                    if mark:
                        gates[block.get("tool_use_id", "")] = mark.group(1)
                elif block.get("tool_use_id") in brain_uses:
                    brain_reads.append((_result_chars(block.get("content")) // 4, len(message_ids)))
        elif kind == "result":
            result = event
    total = len(uses)
    denied = {d.get("tool_use_id") for d in result.get("permission_denials") or [] if isinstance(d, dict)}
    calls = tuple(
        ToolCall(
            seq=i,
            name=name,
            input=inp,
            is_error=tid in errors,
            position=_position(i, total),
            gate=gates.get(tid),
            denied=bool(tid) and tid in denied,
        )
        for i, (tid, name, inp) in enumerate(uses, start=1)
    )
    usage = result.get("usage") or {}
    duration = result.get("duration_ms")
    return SampleMetrics(
        calls=calls,
        tool_call_count=len(calls),
        error_count=sum(c.is_error for c in calls),
        turns=int(result.get("num_turns") or len(message_ids)),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cost_usd=result.get("total_cost_usd"),
        duration_s=None if duration is None else duration / 1000,
        denials=tuple(d.get("tool_name", "") for d in result.get("permission_denials") or []),
        model=init.get("model"),
        mcp_servers=tuple(s.get("name", "") for s in init.get("mcp_servers") or []),
        result_subtype=result.get("subtype"),
        skipped_lines=skipped,
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        brain_tokens=sum(tokens for tokens, _ in brain_reads),
        brain_token_turns=sum(tokens * (len(message_ids) - seen) for tokens, seen in brain_reads),
        api_retries=retries,
        api_error=str(result.get("result") or "unknown error") if result.get("is_error") else None,
    )


def metrics_from_dict(d: dict[str, Any]) -> SampleMetrics:
    """The inverse of ``dataclasses.asdict`` on a ``SampleMetrics`` (a stored ``metrics.json``)."""
    tuple_fields = {"denials", "mcp_servers"}
    fields = {k: (tuple(v) if k in tuple_fields else v) for k, v in d.items() if k != "calls"}
    return SampleMetrics(calls=tuple(ToolCall(**c) for c in d.get("calls", [])), **fields)


def conforms(m: SampleMetrics) -> list[str]:
    problems = []
    if m.tool_call_count != len(m.calls):
        problems.append(f"tool_call_count {m.tool_call_count} != len(calls) {len(m.calls)}")
    errors = sum(c.is_error for c in m.calls)
    if m.error_count != errors:
        problems.append(f"error_count {m.error_count} != errors in calls {errors}")
    positions = [c.position for c in m.calls]
    if any(not 0.0 <= p <= 1.0 for p in positions) or positions != sorted(positions):
        problems.append("positions must be in [0, 1] and non-decreasing")
    return problems


def _is_search(call: ToolCall) -> bool:
    if call.name in SEARCH_TOOLS:
        return True
    return call.name == "Bash" and bool(_SEARCH_BASH.match(str(call.input.get("command", ""))))


def longest_search_run(calls: Sequence[ToolCall]) -> int:
    """The grep spiral: the longest run of consecutive search calls (#196's 7 → 11 regression)."""
    best = run = 0
    for call in calls:
        run = run + 1 if _is_search(call) else 0
        best = max(best, run)
    return best


def whole_reads(calls: Sequence[ToolCall], filename: str) -> int:
    """Reads of ``filename`` with no ``offset``/``limit``: the trap of reading the big index whole."""
    return sum(
        1
        for c in calls
        if c.name == "Read"
        and str(c.input.get("file_path", "")).endswith(filename)
        and "offset" not in c.input
        and "limit" not in c.input
    )


def repeated_calls(calls: Sequence[ToolCall]) -> int:
    seen: set[str] = set()
    repeats = 0
    for c in calls:
        key = c.name + json.dumps(c.input, sort_keys=True)
        repeats += key in seen
        seen.add(key)
    return repeats


def outside_reads(calls: Sequence[ToolCall], roots: Sequence[str]) -> int:
    prefixes = [r.rstrip("/") + "/" for r in roots]
    return sum(
        1
        for c in calls
        if c.name in FILE_TOOLS
        and (path := str(c.input.get("file_path", "")))
        and not any(path.startswith(p) for p in prefixes)
    )


def position_buckets(calls: Sequence[ToolCall], name: str) -> dict[str, int]:
    buckets = {"early": 0, "early_mid": 0, "late_mid": 0, "late": 0}
    for c in calls:
        if c.name != name:
            continue
        if c.position <= 0.2:
            buckets["early"] += 1
        elif c.position <= 0.5:
            buckets["early_mid"] += 1
        elif c.position <= 0.8:
            buckets["late_mid"] += 1
        else:
            buckets["late"] += 1
    return buckets


def is_graph_verb_call(call: ToolCall) -> bool:
    """Whether ``call`` invokes a ``scoutctl kb`` graph traversal or ranked-search verb."""
    if call.name != "Bash" or call.gate is not None:
        return False
    return bool(GRAPH_VERB_RE.search(str(call.input.get("command", ""))))


def graph_verb_calls(calls: Sequence[ToolCall]) -> int:
    """Number of ``scoutctl kb`` graph verb invocations in one sample's transcript."""
    return sum(1 for c in calls if is_graph_verb_call(c))


def verb_uptake_rate(samples_calls: Sequence[Sequence[ToolCall]]) -> float:
    """Share of samples in ``[0.0, 1.0]`` that invoked at least one ``scoutctl kb`` graph verb."""
    if not samples_calls:
        return 0.0
    used = sum(1 for calls in samples_calls if graph_verb_calls(calls) > 0)
    return used / len(samples_calls)


# One name per verb, so a report can tell ranked search (which needs no graph engine) apart
# from the traversal verbs. ``path`` is the older spelling of ``paths``.
_VERB_NAME_RE = re.compile(r"\bscoutctl\s+kb\s+(neighbors|paths|path|traverse|related|backlinks|link-gaps|search)\b")
GRAPH_VERB_NAMES = ("search-ranked", "neighbors", "paths", "traverse", "related", "backlinks", "link-gaps")
_SESSION_CONTEXT = "session-context.json"
_GRAPH_CONTEXT_KEY = re.compile(r"graph_context")


def graph_verb_breakdown(calls: Sequence[ToolCall]) -> dict[str, int]:
    """Count each graph verb a sample called, keyed by :data:`GRAPH_VERB_NAMES` (gate-denied calls excluded)."""
    out = dict.fromkeys(GRAPH_VERB_NAMES, 0)
    for c in calls:
        if not is_graph_verb_call(c):
            continue
        m = _VERB_NAME_RE.search(str(c.input.get("command", "")))
        if m is None:
            continue
        verb = m.group(1)
        if verb == "search":
            verb = "search-ranked"
        elif verb == "path":
            verb = "paths"
        out[verb] += 1
    return out


def traversal_verb_calls(calls: Sequence[ToolCall]) -> int:
    """Graph verb calls other than ``kb search --ranked``: the ones only a graph answers."""
    return sum(n for verb, n in graph_verb_breakdown(calls).items() if verb != "search-ranked")


def graph_context_reads(calls: Sequence[ToolCall]) -> int:
    """Calls that opened ``session-context.json`` (a ``Read``, or a Bash command naming it) without being denied.

    A Bash command that names the file only counts when it also names ``graph_context`` or reads
    the whole file (no key filter), so a ``jq .project_knowledge`` probe isn't credited to Arm C.
    """
    n = 0
    for c in calls:
        if c.is_error or c.gate is not None or c.denied:
            continue
        if c.name == "Read" and str(c.input.get("file_path", "")).endswith(_SESSION_CONTEXT):
            n += 1
        elif c.name == "Bash":
            cmd = str(c.input.get("command", ""))
            if _SESSION_CONTEXT not in cmd:
                continue
            if _GRAPH_CONTEXT_KEY.search(cmd) or not re.search(r"\bjq\b|\[\s*['\"]\w+['\"]\s*\]", cmd):
                n += 1
    return n

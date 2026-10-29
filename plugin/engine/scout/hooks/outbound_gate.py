"""PreToolUse hook — private content never leaves through a Slack or connector write tool.

``kb check-outbound`` (#453) is what keeps a private note's text, title or link
off shared surfaces, but before this gate a run had to remember to call it on a
draft file before every Slack send (one of 24 eval runs sent without it; hard-rule
audit 2026-10, row 2b). This gate runs the same matcher
(:func:`scout.kb.outbound.check_outbound`) on the tool call's own input, so the
check can't be skipped:

  - **Which tools**: an MCP tool whose action writes (send, create, update,
    post, comment, draft, schedule …; never get/list/search/read …). A Slack
    tool is checked as surface ``slack``, any other as ``connector_write``.
    Both are shared in the default policy. Every string in the input is
    checked, field by field. Queries (``connector_query``) are not gated here.
  - **Scheduled runs fail closed** (#504 option B, with #517's rule:
    ``SCOUT_MODE`` set, or a reader other than exactly ``owner``): a hit
    denies the call with the field and line numbers only, records the block in
    ``.scout-logs/kb-reads.jsonl`` and a ``privacy.outbound_blocked`` event;
    a payload or vault that can't be checked denies too.
  - **The owner's interactive sessions get a warning, not a block**: a hit
    shows a warning to the owner and to the session and lets the call go on
    (normal permission prompts still apply), with an audit row
    (``event: privacy.outbound_warned``).

Telegram groups are checked at the source (``notify telegram`` refuses a shared
chat with private content), not here. hooks.json finds the vault in the shell
the way the restrict_tools gate does: a session in no vault that isn't a run
never starts Python.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import IO, Any

from scout import paths

GATE = "outbound-gate"
MARK = f"[scout:{GATE}]"

# A service prefix (`slack_read_channel`), then a read verb. Only known prefixes are skipped, so
# `create_list` and `add_list_record` stay writes.
_READ_ACTION = re.compile(
    r"^(?:(?:slack|gmail|gcal|google|calendar|linear|github|gh|drive|notion|jira|confluence)_)?"
    r"(?:get|list|search|read|find|fetch|query|lookup|view|describe|count)(?:_|$)",
    re.IGNORECASE,
)
_WRITE_ACTION = re.compile(
    r"(?:^|[_\s-])(?:send|create|update|save|post|add|delete|remove|schedule|comment|reply|draft|upload|"
    r"edit|merge|close|reopen|set|archive|review|forward|complete|write|publish|share|invite)",
    re.IGNORECASE,
)


def surface_for(tool_name: str) -> str | None:
    """The shared surface an MCP tool writes to (``slack`` or ``connector_write``), or None for a read."""
    parts = tool_name.split("__", 2)
    if len(parts) < 3 or parts[0] != "mcp" or not parts[2]:
        return None
    server, action = parts[1], parts[2]
    if _READ_ACTION.search(action) or not _WRITE_ACTION.search(action):
        return None
    return "slack" if "slack" in f"{server}__{action}".casefold() else "connector_write"


def _strings(value: Any, field: str = "") -> Iterator[tuple[str, str]]:
    """``(field, text)`` for every string in a tool input; nested fields are dotted (``blocks.0.text``)."""
    if isinstance(value, str):
        yield field or "input", value
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            yield from _strings(inner, f"{field}.{key}" if field else str(key))
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            yield from _strings(inner, f"{field}.{i}" if field else str(i))


def flagged_fields(vault: Path, tool_input: Mapping[str, Any], surface: str) -> dict[str, list[int]]:
    """Field → line numbers that name a private note. Never the matched text."""
    from scout.kb.outbound import check_outbound

    out: dict[str, list[int]] = {}
    for field, text in _strings(tool_input):
        lines = check_outbound(vault, text, surface)
        if lines:
            out[field] = lines
    return out


def _where(found: Mapping[str, list[int]]) -> str:
    return "; ".join(
        f"`{field}` line{'s' if len(lines) > 1 else ''} {', '.join(map(str, lines))}" for field, lines in found.items()
    )


def session_vault(payload: Mapping[str, Any]) -> Path | None:
    """SCOUT_DATA_DIR, else the vault containing the session's cwd (the payload's, else ours), else None."""
    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        vault = paths.resolve_path(env)
        return vault if (vault / paths.VAULT_MARKER).is_file() else None
    cwd = payload.get("cwd")
    return paths.vault_containing(cwd if isinstance(cwd, str) and cwd else os.getcwd())


def _record(
    vault: Path, *, reader: str, tool: str, surface: str, found: Mapping[str, list[int]], blocked: bool
) -> None:
    """The audit row (no content) and, for a block, the run-health event. Never raises."""
    from scout.kb.read_log import log_event

    try:
        log_event(
            vault,
            reader=reader,
            command=f"hook {GATE}",
            surface=surface,
            args=[surface, tool],
            returned=0,
            denied=0,
            private_paths=0,
            blocked=blocked,
            event=None if blocked else "privacy.outbound_warned",
        )
        if blocked:
            from scout.events import append_event

            append_event(
                paths.logs_dir(vault),
                kind="privacy.outbound_blocked",
                source=f"hook:{GATE}",
                payload={"surface": surface, "tool": tool, "fields": dict(found)},
            )
    except Exception:  # noqa: BLE001, S110 - the decision stands without its record
        pass


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _warn(text: str) -> dict[str, Any]:
    """Shown to the owner and to the session; the call goes on through the normal permission flow."""
    return {
        "systemMessage": text,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text},
    }


def _blocked_reason(tool: str, surface: str, found: Mapping[str, list[int]]) -> str:
    return (
        f"{MARK} Not sent: {_where(found)} of this {tool} call name a private note, and {surface} is a shared "
        "surface. Move those lines to the daily file or the owner's Telegram chat, or rephrase them without "
        "private names, titles or links, then send again; if it still fails, send only the lines that pass plus "
        '"Some items are in your daily file." Never drop the wrap.'
    )


def decide(payload: Mapping[str, Any], *, scheduled: bool, reader: str) -> dict[str, Any] | None:
    """The hook's output for one payload, or None to let the call through."""
    tool = payload.get("tool_name")
    surface = surface_for(tool) if isinstance(tool, str) else "unknown"
    if surface is None:
        return None  # not an MCP write: nothing leaves through it
    try:
        tool_input = payload.get("tool_input")
        if not isinstance(tool, str) or not isinstance(tool_input, Mapping):
            raise ValueError("not a tool-call payload")
        vault = session_vault(payload)
        if vault is None:
            if not scheduled:
                return None  # the owner, in no vault: no private notes to check against
            raise LookupError("no Scout vault to check against")
        found = flagged_fields(vault, tool_input, surface)
    except Exception as exc:  # noqa: BLE001 — a run fails closed, the owner is warned
        why = f"Scout's outbound privacy check could not run ({type(exc).__name__})"
        if scheduled:
            return _deny(
                f"{MARK} {why}, so this call is blocked in a scheduled run. Put the content in the daily "
                "file or the owner's Telegram chat instead, and say so in the run notes."
            )
        return _warn(f"{MARK} Warning: {why}; this call was not checked for private content.")
    if not found:
        return None
    _record(vault, reader=reader, tool=tool, surface=surface, found=found, blocked=scheduled)
    if scheduled:
        return _deny(_blocked_reason(tool, surface, found))
    return _warn(
        f"{MARK} Privacy warning: {_where(found)} of this {tool} call name a private note, and {surface} is a "
        "shared surface. It is not blocked because this is the owner's own session; check the text before it goes."
    )


def run(*, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Read one PreToolUse payload; return the hook output, or None to let the call through."""
    from scout.kb.lint import is_scheduled
    from scout.kb.reader import resolve_reader

    env = os.environ if env is None else env
    scheduled = is_scheduled(env)
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
    except Exception:  # noqa: BLE001
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    return decide(payload, scheduled=scheduled, reader=resolve_reader(None, env))


def main() -> int:
    """CLI entry point: scoutctl hook outbound-gate."""
    decision = run()
    if decision is not None:
        print(json.dumps(decision))
    return 0

"""Thin entry for the per-tool-call hooks: ``python -m scout.hooks <name>``.

``scoutctl hook <name>`` goes through ``scout.cli``, whose import (typer and
every subcommand) costs ~0.7 s, and these hooks run on tool calls: the
kb-read-label hook on every Read of a KB note, in the owner's sessions too,
and mcp-tool-gate on every MCP call in a vault that uses ``restrict_tools``.
``engine/bin/scoutctl`` sends ``hook <name>`` here instead for every name in
``HOOKS``, so hooks.json is unchanged. Nothing reachable from this module
may import typer or ``scout.cli`` (``tests/unit/test_hooks_entry.py``).

The ``scoutctl hook …`` commands stay as they are, for a direct call.
"""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module

HOOKS = {
    "mcp-tool-gate": "scout.hooks.mcp_tool_gate",
    "kb-read-gate": "scout.hooks.kb_read_gate",
    "kb-write-gate": "scout.hooks.kb_write_gate",
    "kb-read-label": "scout.hooks.kb_read_label",
    "front-page-reads": "scout.hooks.front_page_reads",
    "outbound-gate": "scout.hooks.outbound_gate",
    "shared-read-record": "scout.hooks.shared_read_record",
}

# Hooks with no fallback in hooks.json: an import failure here must deny, not exit 1 (which allows).
FAIL_CLOSED = frozenset({"mcp-tool-gate"})


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in HOOKS:
        print(f"usage: python -m scout.hooks {{{','.join(HOOKS)}}}", file=sys.stderr)
        return 2
    try:
        hook_main = import_module(HOOKS[argv[0]]).main
    except Exception as exc:
        if argv[0] not in FAIL_CLOSED:
            raise
        return _deny_mcp_call(exc)
    return int(hook_main())


def _deny_mcp_call(exc: Exception) -> int:
    """The gate's module could not be imported: deny the MCP call, as the gate itself does on an error.

    hooks.json execs this hook with no shell fallback, and Claude Code lets a call through when its
    hook exits 1, so a traceback here would lift every ``restrict_tools`` restriction. As in
    ``mcp_tool_gate.run``, a payload that is not an MCP tool call is left alone, and so is a session
    in no vault: hooks.json is plugin-wide, and a broken engine must not block MCP everywhere.
    """
    try:
        payload = json.load(sys.stdin)
        tool_name = payload.get("tool_name")
    except Exception:
        return 0
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
        return 0
    if not _may_be_in_a_vault(payload):
        return 0
    reason = (
        f"Scout's MCP tool gate could not start ({type(exc).__name__}), so {tool_name} is blocked in this vault's "
        "sessions. To fix it, run /scout-update or `scoutctl bootstrap doctor`."
    )
    decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": decision}))
    return 0


def _may_be_in_a_vault(payload: dict[str, object]) -> bool:
    """Whether the gate would find a vault for this session: ``mcp_tool_gate.session_vault``, stdlib only.

    No scout import, so it can't fail the way the gate's import just did. SCOUT_DATA_DIR, else a
    ``scout-config.yaml`` at or above the payload's cwd. True when that can't be decided (no cwd, an
    OS error): unknown stays closed.
    """
    if os.environ.get("SCOUT_DATA_DIR"):
        return True
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return True
    try:
        here = os.path.realpath(os.path.expanduser(cwd))
        while not os.path.isfile(os.path.join(here, "scout-config.yaml")):
            parent = os.path.dirname(here)
            if parent == here:
                return False
            here = parent
    except Exception:
        return True
    return True


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

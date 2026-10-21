"""PreToolUse hook — limits a ``restrict_tools`` custom connector to its listed tools.

A custom connector's tool list is otherwise only guidance: scheduled runs use
``--permission-mode auto`` with no ``--allowedTools``, so a session could call
any tool its MCP servers expose. For a server whose credential can write (an
MCP server authenticated with a person's full-access token, say), that is too
loose. With ``restrict_tools: true`` in the definition, this hook denies every
call to that server's tools except the probe and the activity tools the entry
lists (``custom_connectors.tool_gate``).

  - Acts in EVERY session in a vault that uses ``restrict_tools`` — a runner's
    run, a sandbox running the runner's prompt, and an interactive session
    alike. Whether a session is a run depends on prompt wording (``run_context``),
    which a launcher or template change can lose and the model's own inputs can
    influence, so it is used for accounting only, never for this gate. A
    connector that needs writes is defined without ``restrict_tools``.
  - The vault is SCOUT_DATA_DIR, else the one containing the session's cwd; a
    session in no vault is not gated (no ``~/Scout`` fallback). hooks.json
    finds it the same way in the shell and greps its file for
    ``restrict_tools``, so Python starts (~0.15 s a call) only for sessions in
    a vault that uses it.
  - Fails closed: a gate that can't be computed denies the call.
  - Prints a PreToolUse deny decision, or nothing to let the call through.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import IO, Any

from scout import paths
from scout.custom_connectors import GATE_ALL, tool_gate


def decide(tool_name: str, gate: dict[str, frozenset[str]]) -> str | None:
    """The deny reason for ``tool_name``, or None to allow it."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    server = parts[1] if len(parts) >= 3 else ""
    if GATE_ALL in gate:
        return (
            "Scout could not read connectors.custom.yaml, so every MCP tool is blocked in this session "
            "(a connector there restricts its tools). Carry on without MCP tools; "
            "`scoutctl connectors custom list` shows the problem."
        )
    # Match the server case-insensitively so a differently-cased name can't slip past.
    restricted = next((s for s in gate if s.casefold() == server.casefold()), None)
    if restricted is None or tool_name in gate[restricted]:
        return None
    allowed = ", ".join(sorted(gate[restricted])) or "none"
    return (
        f"{tool_name} is not one of the tools Scout's custom connector for {restricted!r} allows "
        f"(restrict_tools). Allowed: {allowed}. Use one of those, or carry on without it."
    )


def session_vault(payload: dict[str, Any]) -> Path | None:
    """SCOUT_DATA_DIR, else the vault containing the session's cwd (the payload's, else ours), else None."""
    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        return paths.resolve_path(env)
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    return paths.vault_containing(start)


def run(*, stdin: IO[str] | None = None) -> dict[str, Any] | None:
    """Read one PreToolUse payload; return the deny decision, or None to allow."""
    src = stdin if stdin is not None else sys.stdin
    try:
        data = json.load(src)
        tool_name = data.get("tool_name")
    except Exception:
        return None  # not a tool-call payload: nothing to gate
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
        return None
    try:
        vault = session_vault(data)
        if vault is None:
            return None  # not in a vault: no connectors.custom.yaml applies
        reason = decide(tool_name, tool_gate(vault))
    except Exception as exc:  # noqa: BLE001 — fail closed: an unknown gate denies
        reason = f"Scout's MCP tool gate failed ({exc!r}), so {tool_name} is blocked in this session."
    if reason is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    """CLI entry point: scoutctl hook mcp-tool-gate."""
    decision = run()
    if decision is not None:
        print(json.dumps(decision))
    return 0

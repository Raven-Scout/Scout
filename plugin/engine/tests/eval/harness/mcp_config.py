"""Write the ``--mcp-config`` file that gives an eval session its stub servers (spec 2026-10-12 §4.1).

Server keys are the production names, so tool names
(``mcp__claude_ai_Slack__slack_search_public_and_private``) and
``connector_log.classify()`` match a real run. The returned key set is what
the init-event guard allows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[3]
CATALOG_DIR = ENGINE / "tests" / "eval" / "stubs" / "catalog"
PERSONA_SERVERS = {
    "slack": "claude_ai_Slack",
    "linear": "claude_ai_Linear",
    "calendar": "claude_ai_Google_Calendar",
    "email": "claude_ai_Gmail",
}


def write_mcp_config(
    dest: Path, *, personas: Iterable[str], script: Path, calls: Path, today: date, python: str
) -> set[str]:
    servers = {}
    for persona in personas:
        servers[PERSONA_SERVERS[persona]] = {
            "type": "stdio",
            "command": python,
            "args": [
                "-m",
                "tests.eval.harness.stubs.mcp_server",
                "--persona",
                persona,
                "--script",
                str(script.resolve()),
                "--catalog",
                str((CATALOG_DIR / f"{persona}.yaml").resolve()),
                "--calls",
                str(calls.resolve()),
                "--today",
                today.isoformat(),
            ],
            "cwd": str(ENGINE),
            "env": {"PYTHONPATH": str(ENGINE)},
        }
    dest.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return set(servers)

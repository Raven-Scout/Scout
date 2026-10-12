"""A stdio MCP server that plays one connector persona from a script (spec 2026-10-12 §5.4).

Stdlib only: newline-delimited JSON-RPC 2.0 over stdin/stdout, with just the
methods a Claude Code session uses (``initialize``, ``tools/list``,
``tools/call``, ``ping``). Every ``tools/call`` is appended to the calls log
before it is answered, so a session killed mid-call still leaves the row.

Run as ``python -m tests.eval.harness.stubs.mcp_server --persona slack
--script connectors.yaml --catalog catalog/slack.yaml --calls calls.jsonl
--today YYYY-MM-DD`` with ``plugin/engine`` as the working directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .calls_log import CallsLog
from .rules import Script

SERVER_INFO = {"name": "scout-eval-stub", "version": "1"}
DEFAULT_PROTOCOL = "2025-06-18"


def _result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class StubServer:
    def __init__(self, persona: str, script: Script, tools: list[dict[str, Any]], log: CallsLog) -> None:
        self.persona, self.script, self.tools, self.log = persona, script, tools, log

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            return None  # a notification
        params = msg.get("params") or {}
        if method == "initialize":
            return _result(
                msg_id,
                {
                    "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": self.tools})
        if method == "tools/call":
            return _result(msg_id, self._call(params.get("name", ""), params.get("arguments") or {}))
        return _error(msg_id, -32601, f"method not found: {method}")

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        reply = self.script.reply(self.persona, tool, args, prior_hits=self.log.hits)
        self.log.append(persona=self.persona, tool=tool, args=args, reply=reply)
        if reply.kind == "ok":
            text = reply.payload if isinstance(reply.payload, str) else json.dumps(reply.payload)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        return {"content": [{"type": "text", "text": str(reply.payload)}], "isError": True}


def serve(server: StubServer, stdin: Any = sys.stdin, stdout: Any = sys.stdout) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            response: dict[str, Any] | None = _error(None, -32700, f"parse error: {exc}")
        else:
            response = server.handle(msg) if isinstance(msg, dict) else _error(None, -32600, "invalid request")
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--persona", required=True)
    p.add_argument("--script", required=True, type=Path)
    p.add_argument("--catalog", required=True, type=Path)
    p.add_argument("--calls", required=True, type=Path)
    p.add_argument("--today", required=True)
    a = p.parse_args(argv)
    tools = (yaml.safe_load(a.catalog.read_text(encoding="utf-8")) or {}).get("tools", [])
    script = Script.load(a.script, today=date.fromisoformat(a.today))
    serve(StubServer(a.persona, script, tools, CallsLog(a.calls)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

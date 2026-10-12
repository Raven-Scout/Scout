"""The shared calls log: one JSON row per stubbed connector call (spec 2026-10-12 §5.4).

Every MCP persona and every ``gh`` invocation appends here, and rule hit
counts (``times: N``) are read back from it, so separate processes agree.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Any

from .rules import Reply


class CallsLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def hits(self, rule: str) -> int:
        return sum(1 for r in self.rows() if r.get("rule") == rule)

    def append(self, *, persona: str, tool: str, args: Any, reply: Reply) -> None:
        row = {
            "ts": round(time.time(), 3),
            "persona": persona,
            "tool": tool,
            "args": args,
            "kind": reply.kind,
            "rule": reply.rule,
            "capture": reply.capture,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(row, sort_keys=True) + "\n")

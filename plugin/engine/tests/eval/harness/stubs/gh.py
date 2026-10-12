"""A fake ``gh`` for eval sessions (spec 2026-10-12 §5.4).

Scout calls GitHub through the ``gh`` CLI, not MCP, so the harness puts
``tests/eval/stubs/bin/gh`` first on PATH. It answers from the scenario
script's ``gh`` persona: every rule sits under the ``"*"`` tool and matches
``argv``, the arguments joined by spaces. A response is
``{stdout: <text, a file next to the script, or JSON>, exit: 0}``.

Unmatched calls exit 1 and are logged as ``unstubbed``, so the real ``gh``
(and the developer's GitHub login) is never reached.

Environment: ``SCOUT_EVAL_SCRIPT``, ``SCOUT_EVAL_CALLS``, ``SCOUT_EVAL_TODAY``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from ..dates import render_dates
from .calls_log import CallsLog
from .rules import Script

GH_BIN_DIR = Path(__file__).resolve().parents[2] / "stubs" / "bin"


def _stdout(value: Any, base: Path, today: date) -> str:
    if isinstance(value, str):
        candidate = base / value
        if "\n" not in value and candidate.is_file():
            return render_dates(candidate.read_text(encoding="utf-8"), today)
        return value
    return json.dumps(value)


def main(argv: list[str]) -> int:
    script_path = Path(os.environ["SCOUT_EVAL_SCRIPT"])
    today = date.fromisoformat(os.environ["SCOUT_EVAL_TODAY"])
    log = CallsLog(Path(os.environ["SCOUT_EVAL_CALLS"]))
    args = {"argv": " ".join(argv)}
    script = Script.load(script_path, today=today)
    reply = log.record(
        persona="gh", tool="gh", args=args, decide=lambda hits: script.reply("gh", "*", args, prior_hits=hits)
    )
    if reply.kind != "ok":
        print(f"gh: {reply.payload}", file=sys.stderr)
        return 1
    payload = reply.payload if isinstance(reply.payload, dict) else {"stdout": reply.payload}
    out = _stdout(payload.get("stdout", ""), script_path.parent, today)
    sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")
    return int(payload.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

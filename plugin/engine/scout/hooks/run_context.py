"""Which vault and run mode a Stop hook is reporting for.

The scheduled runners export ``SCOUT_DATA_DIR`` and ``SCOUT_MODE`` into the
``claude`` child, and that stays the first answer. A session started some other
way (a sandbox that runs the runner's prompt itself, with no runner around it)
has neither, so the hooks fall back to:

- the vault: the one containing the session's ``cwd`` from the hook payload
  (``paths.data_dir(cwd=...)``), else ``~/Scout`` as before;
- the mode: read from the run prompt, the transcript's first user message. Every
  runner prompt names it, as "Your run mode is `<slot>`" (run-scout.sh) or
  "running in RESEARCH|DREAMING mode" (run-research.sh, run-dreaming.sh).

The mode is only inferred inside a vault: an interactive session elsewhere stays
what it was (no tool log, ``manual`` tokens row).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import paths

# A source of "env" means a runner set SCOUT_MODE and records the run's cost itself.
SOURCE_ENV = "env"
SOURCE_PROMPT = "prompt"

_SLOT = re.compile(r"Your run mode is `([a-z0-9][a-z0-9-]*)`")
_NAMED = re.compile(r"\brunning in (RESEARCH|DREAMING) mode\b")
# The run prompt is the first user message; give up after this many transcript rows.
_MAX_ROWS = 50


@dataclass(frozen=True)
class RunContext:
    vault: Path
    mode: str | None
    mode_source: str | None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def mode_from_prompt(transcript_path: Path | None) -> str | None:
    """The run mode named in the transcript's first user message, else None."""
    if transcript_path is None:
        return None
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for n, raw in enumerate(f):
                if n >= _MAX_ROWS:
                    return None
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg = row.get("message") if isinstance(row, dict) else None
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                text = _text_of(msg.get("content"))
                if not text:
                    continue  # a tool_result-only row is not the prompt
                slot = _SLOT.search(text)
                if slot:
                    return slot.group(1)
                named = _NAMED.search(text)
                return named.group(1).lower() if named else None
    except OSError:
        return None
    return None


def may_be_a_run(payload: dict[str, Any]) -> bool:
    """Cheap pre-check: SCOUT_MODE is set, or the session can name its mode (it runs in a vault)."""
    if os.environ.get("SCOUT_MODE") or os.environ.get("SCOUT_DATA_DIR"):
        return True
    cwd = payload.get("cwd")
    return isinstance(cwd, str) and bool(cwd) and paths.vault_containing(cwd) is not None


def resolve(payload: dict[str, Any], transcript_path: Path | None) -> RunContext:
    """The vault and mode for one Stop-hook payload. Never raises."""
    cwd = payload.get("cwd")
    vault = paths.data_dir(cwd=cwd if isinstance(cwd, str) else None)
    env_mode = os.environ.get("SCOUT_MODE")
    if env_mode:
        return RunContext(vault, env_mode, SOURCE_ENV)
    if not may_be_a_run(payload):
        return RunContext(vault, None, None)
    mode = mode_from_prompt(transcript_path)
    return RunContext(vault, mode, SOURCE_PROMPT if mode else None)

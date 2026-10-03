"""Plan 1's transcript parser, frozen as the exactness reference for 1b (spec §3.4).

This is a verbatim copy of ``parse_transcript`` and ``extract_first_message`` (with their
helpers) from ``scout.sessions.transcript`` as of scout-plugin 34a3ee8, before the 1b rewrite.
Do not edit it to make a test pass: a difference from it is a regression. Tests only.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from scout.sessions.model import LastTurn, TranscriptInfo, dt_to_iso, parse_iso

_HEAD_LINES_FOR_FIRST_MSG = 50
_MAX_FILES_TOUCHED = 10
_FIRST_MSG_MAX_CHARS = 500
_FILES_NOISE_RE = re.compile(
    r"(/\.claude/projects/.*/tool-results/"
    r"|/\.claude/projects/.*/tasks/"
    r"|/\.claude/plugins/cache/"
    r"|/node_modules/"
    r"|^/private/tmp/claude-"
    r"|/\.claude/projects/.*/memory/)"
)
_FILE_PATH_LINE_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


def _extract_first_message(jsonl_path: Path) -> str:
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= _HEAD_LINES_FOR_FIRST_MSG:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type") or obj.get("role")
                if kind not in ("user", "human"):
                    continue
                msg = obj.get("message")
                content: Any
                content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            raw_text = part.get("text")
                            if not isinstance(raw_text, str):
                                continue
                            text = raw_text[:_FIRST_MSG_MAX_CHARS]
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content[:_FIRST_MSG_MAX_CHARS]
    except OSError:
        return "(parse error)"
    return "(could not extract first message)"


def _blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _last_turn_kind(last_assistant: dict[str, Any] | None, answered: set[str]) -> str:
    if last_assistant is None:
        return "unknown"
    blocks = _blocks(last_assistant)
    tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
    for b in tool_uses:
        if b.get("name") == "AskUserQuestion" and str(b.get("id")) not in answered:
            return "question"
    if tool_uses:
        return "tool_use"
    texts = [t for b in blocks if b.get("type") == "text" and isinstance(t := b.get("text"), str)]
    if texts and texts[-1].rstrip().endswith("?"):
        return "question"
    return "end_turn"


def reference_parse(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    stat = st or path.stat()
    home_str = str(home or Path.home())
    files: set[str] = set()
    tool_calls = 0
    last_assistant: dict[str, Any] | None = None
    answered: set[str] = set()
    last_ts: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in _FILE_PATH_LINE_RE.finditer(line):
                    p = m.group(1)
                    if _FILES_NOISE_RE.search(p):
                        continue
                    if p.startswith(home_str + "/"):
                        p = "~/" + p[len(home_str) + 1 :]
                    files.add(p)
                if '"tool_use"' not in line and '"assistant"' not in line and '"user"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type")
                if kind not in ("assistant", "user"):
                    continue
                ts = obj.get("timestamp")
                if isinstance(ts, str) and parse_iso(ts) is not None:
                    last_ts = ts
                blocks = _blocks(obj)
                if kind == "assistant":
                    last_assistant = obj
                    tool_calls += sum(1 for b in blocks if b.get("type") == "tool_use")
                else:
                    for b in blocks:
                        if b.get("type") == "tool_result" and b.get("tool_use_id") is not None:
                            answered.add(str(b["tool_use_id"]))
    except OSError:
        pass
    at = parse_iso(last_ts)
    return TranscriptInfo(
        path=str(path),
        first_prompt=_extract_first_message(path),
        files_touched=sorted(files)[:_MAX_FILES_TOUCHED],
        tool_calls=tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=_last_turn_kind(last_assistant, answered)),
        mtime_ns=stat.st_mtime_ns,
    )

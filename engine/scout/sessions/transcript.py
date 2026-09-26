"""One-pass transcript facts (spec §4.5) plus the mtime-keyed cache.

``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
``scout.scripts.cc_session_cache`` (that module re-exports them). ``parse_transcript``
walks the file once and returns everything the index needs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scout.sessions._atomic import atomic_write_text
from scout.sessions.model import LastTurn, TranscriptInfo, dt_to_iso, parse_iso

TRANSCRIPT_CACHE_FILENAME = "sessions-transcripts.cache.json"

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


# ----- moved extractors (unchanged behaviour) --------------------------------


def extract_first_message(jsonl_path: Path) -> str:
    """Return the first user-typed prompt from a CC JSONL (first 50 lines, 500 chars)."""
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
                                continue  # malformed part (e.g. a dict) — skip it, keep looking
                            text = raw_text[:_FIRST_MSG_MAX_CHARS]
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content[:_FIRST_MSG_MAX_CHARS]
    except OSError:
        return "(parse error)"
    return "(could not extract first message)"


def extract_files_touched(jsonl_path: Path, home: Path | None = None) -> list[str]:
    """Return up to 10 unique user-meaningful files referenced in the JSONL."""
    home_str = str(home or Path.home())
    seen: set[str] = set()
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in _FILE_PATH_LINE_RE.finditer(line):
                    path = m.group(1)
                    if _FILES_NOISE_RE.search(path):
                        continue
                    if path.startswith(home_str + "/"):
                        path = "~/" + path[len(home_str) + 1 :]
                    seen.add(path)
    except OSError:
        return []
    return sorted(seen)[:_MAX_FILES_TOUCHED]


# ----- one-pass parse --------------------------------------------------------


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


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """Walk the JSONL once: first prompt, files touched, tool-call count, last-turn shape.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
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
        first_prompt=extract_first_message(path),
        files_touched=sorted(files)[:_MAX_FILES_TOUCHED],
        tool_calls=tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=_last_turn_kind(last_assistant, answered)),
        mtime_ns=stat.st_mtime_ns,
    )


# ----- cache -------------------------------------------------------------------


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cached_entry(payload: dict[str, Any]) -> TranscriptInfo | None:
    """Rebuild one cache entry, or None when any field is missing or has the wrong type."""
    lt = payload.get("last_turn")
    files = payload.get("files_touched")
    if not (
        isinstance(payload.get("path"), str)
        and isinstance(payload.get("first_prompt"), str)
        and isinstance(files, list)
        and all(isinstance(x, str) for x in files)
        and _is_int(payload.get("tool_calls"))
        and isinstance(lt, dict)
        and isinstance(lt.get("kind"), str)
        and (lt.get("at") is None or isinstance(lt.get("at"), str))
        and _is_int(payload.get("mtime_ns"))
    ):
        return None
    return TranscriptInfo(
        path=payload["path"],
        first_prompt=payload["first_prompt"],
        files_touched=list(files),
        tool_calls=payload["tool_calls"],
        last_turn=LastTurn(at=lt.get("at"), kind=lt["kind"]),
        mtime_ns=payload["mtime_ns"],
    )


def load_transcript_cache(cache_path: Path) -> dict[str, TranscriptInfo]:
    """Load the cache; entries with a missing or wrongly-typed field are skipped (re-parsed later)."""
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, TranscriptInfo] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        entry = _cached_entry(payload)
        if entry is not None:
            out[key] = entry
    return out


def write_transcript_cache(cache_path: Path, entries: dict[str, TranscriptInfo]) -> None:
    """Atomically replace the cache file (unique temp + ``os.replace``). Best-effort — never raises."""
    try:
        atomic_write_text(cache_path, json.dumps({k: asdict(v) for k, v in entries.items()}))
    except OSError:
        pass


def transcript_info(path: Path, *, cache: dict[str, TranscriptInfo], home: Path | None = None) -> TranscriptInfo:
    """Cached lookup keyed by path; re-parses only when ``mtime_ns`` changed.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    st = path.stat()
    prior = cache.get(str(path))
    if prior is not None and prior.mtime_ns == st.st_mtime_ns:
        return prior
    info = parse_transcript(path, st=st, home=home)
    cache[str(path)] = info
    return info


__all__ = [
    "TRANSCRIPT_CACHE_FILENAME",
    "extract_files_touched",
    "extract_first_message",
    "load_transcript_cache",
    "parse_transcript",
    "transcript_info",
    "write_transcript_cache",
]

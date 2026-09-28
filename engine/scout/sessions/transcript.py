"""Transcript facts (spec §4.5): one forward pass over the bytes, plus the mtime-keyed cache.

``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
``scout.scripts.cc_session_cache`` (that module re-exports them).
"""

from __future__ import annotations

import io
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
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


def _first_message(lines: Iterable[str]) -> str:
    for i, raw in enumerate(lines):
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
    return "(could not extract first message)"


def extract_first_message(jsonl_path: Path) -> str:
    """Return the first user-typed prompt from a CC JSONL (first 50 lines, 500 chars)."""
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            return _first_message(f)
    except OSError:
        return "(parse error)"


def _first_message_of(data: bytes) -> str:
    """``extract_first_message`` over bytes already read, decoded exactly as ``open(..., "r")`` decodes."""
    return _first_message(io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace"))


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


# ----- one forward pass (1b spec §3.4) -------------------------------------------------
#
# The pass reads bytes. A line is decoded only when it can change a published fact, and
# the file-path scan still runs on every line. Claude Code writes compact JSON, so a user
# row carries the bytes "type":"user". Such a row is held undecoded until it can matter:
# it is released when it is among the rows after the final assistant row, or when the
# next assistant row has no usable timestamp. Assistant rows, and anything unusual, are
# decoded at once and dispatched on their real type, exactly as plan 1 did. The result
# is exact because a tool result always follows its tool call, and a row's own "type"
# is never written with escapes.


def _home_prefix(home: Path | None) -> str:
    return str(home or Path.home()) + "/"


def _lines(chunk: bytes) -> list[bytes]:
    """Split like text mode's universal newlines: ``\\r\\n``, ``\\r`` and ``\\n`` each end a line."""
    if b"\r" in chunk:
        chunk = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return chunk.split(b"\n")


def _valid_ts(ts: Any) -> bool:
    return isinstance(ts, str) and parse_iso(ts) is not None


def _decode(line: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(line.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


@dataclass
class _State:
    """What a forward pass knows about the lines it has consumed."""

    files: set[str] = field(default_factory=set)
    tool_calls: int = 0
    last_assistant: str | None = None  # the last assistant row's own kind: tool_use | question | end_turn
    pending: set[str] = field(default_factory=set)  # its AskUserQuestion ids with no tool result yet
    last_ts: str | None = None  # the last user/assistant row timestamp that parses
    held: list[bytes] = field(default_factory=list)  # user rows after the last assistant row, undecoded

    def copy(self) -> _State:
        return replace(self, files=set(self.files), pending=set(self.pending), held=list(self.held))


def _assistant_kind(blocks: list[dict[str, Any]], tool_uses: list[dict[str, Any]]) -> str:
    """An assistant row's kind before any answer arrives: plan 1's rule minus the pending-question check."""
    if tool_uses:
        return "tool_use"
    texts = [t for b in blocks if b.get("type") == "text" and isinstance(t := b.get("text"), str)]
    return "question" if texts and texts[-1].rstrip().endswith("?") else "end_turn"


def _apply_user(state: _State, obj: dict[str, Any]) -> None:
    if _valid_ts(obj.get("timestamp")):
        state.last_ts = obj["timestamp"]
    for b in _blocks(obj):
        if b.get("type") == "tool_result" and b.get("tool_use_id") is not None:
            state.pending.discard(str(b["tool_use_id"]))


def _release(state: _State) -> None:
    """Decode the held user rows, oldest first, and apply them."""
    held, state.held = state.held, []
    for line in held:
        obj = _decode(line)
        if obj is not None and obj.get("type") == "user":
            _apply_user(state, obj)


def _apply(state: _State, obj: dict[str, Any] | None) -> None:
    if obj is None:
        return
    kind = obj.get("type")
    if kind == "assistant":
        if _valid_ts(obj.get("timestamp")):
            state.held.clear()  # every held row is older, so none can hold the last timestamp
            state.last_ts = obj["timestamp"]
        else:
            _release(state)  # the last usable timestamp may be on a held row
        blocks = _blocks(obj)
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        state.tool_calls += len(tool_uses)
        state.pending = {str(b.get("id")) for b in tool_uses if b.get("name") == "AskUserQuestion"}
        state.last_assistant = _assistant_kind(blocks, tool_uses)
    elif kind == "user":
        _release(state)  # held rows come first in file order
        _apply_user(state, obj)


def _feed(state: _State, line: bytes, home_prefix: str) -> None:
    if b'"file_path"' in line:
        for m in _FILE_PATH_LINE_RE.finditer(line.decode("utf-8", "replace")):
            p = m.group(1)
            if _FILES_NOISE_RE.search(p):
                continue
            if p.startswith(home_prefix):
                p = "~/" + p[len(home_prefix) :]
            state.files.add(p)
    is_assistant = b'"assistant"' in line
    if not is_assistant and b'"user"' not in line and b'"tool_use"' not in line:
        return  # plan 1 never decoded these either
    if not is_assistant and b'"type":"user"' in line:
        state.held.append(line)
        return
    _apply(state, _decode(line))


def _consume(state: _State, data: bytes, home_prefix: str) -> tuple[int, bytes]:
    """Feed every complete line of *data*, then release held rows.

    Returns (bytes consumed, the trailing partial line). A partial line is not consumed.
    """
    end = data.rfind(b"\n") + 1
    for line in _lines(data[:end]):
        _feed(state, line, home_prefix)
    _release(state)
    return end, data[end:]


def _info(
    path: Path, st: os.stat_result, state: _State, partial: bytes, first_prompt: str, home_prefix: str
) -> TranscriptInfo:
    """The published facts: the consumed state plus the trailing partial line, which plan 1 read too."""
    view = state
    if partial:
        view = state.copy()
        for line in _lines(partial):
            _feed(view, line, home_prefix)
        _release(view)
    at = parse_iso(view.last_ts)
    kind = "unknown" if view.last_assistant is None else ("question" if view.pending else view.last_assistant)
    return TranscriptInfo(
        path=str(path),
        first_prompt=first_prompt,
        files_touched=sorted(view.files)[:_MAX_FILES_TOUCHED],
        tool_calls=view.tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=kind),
        mtime_ns=st.st_mtime_ns,
    )


def _unreadable(path: Path, st: os.stat_result) -> TranscriptInfo:
    """Plan 1 kept going with no facts when a transcript could not be read after its stat."""
    return TranscriptInfo(
        path=str(path),
        first_prompt=extract_first_message(path),
        files_touched=[],
        tool_calls=0,
        last_turn=LastTurn(at=None, kind="unknown"),
        mtime_ns=st.st_mtime_ns,
    )


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """The full parse: first prompt, files touched, tool-call count, last-turn shape.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    stat = st or path.stat()
    prefix = _home_prefix(home)
    try:
        with path.open("rb") as f:
            data = f.read(stat.st_size)
    except OSError:
        return _unreadable(path, stat)
    state = _State()
    _, partial = _consume(state, data, prefix)
    return _info(path, stat, state, partial, _first_message_of(data), prefix)


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

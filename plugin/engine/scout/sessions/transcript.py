"""Transcript facts (spec §4.5): one forward pass, checkpointed so a growing transcript is
parsed only from where the last build stopped (1b spec §3.3–§3.4), plus the cache.

``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
``scout.scripts.cc_session_cache`` (that module re-exports them).
"""

from __future__ import annotations

import hashlib
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
from scout.sessions.stats import BuildStats

TRANSCRIPT_CACHE_FILENAME = "sessions-transcripts.cache.json"

_CACHE_VERSION = 2
_HEAD_LINES_FOR_FIRST_MSG = 50
_MAX_FILES_TOUCHED = 10
_FIRST_MSG_MAX_CHARS = 500
_HEAD_CHECK_BYTES = 4096
_NO_FIRST_MESSAGE = ("(could not extract first message)", "(parse error)")
_ASSISTANT_KINDS = ("tool_use", "question", "end_turn")
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


# ----- checkpoints (1b spec §3.3) ------------------------------------------------------


@dataclass
class Checkpoint:
    """Where a forward pass stopped in a file, and what it knew there."""

    dev: int
    ino: int
    size: int  # the file's size and mtime when the checkpoint was taken
    mtime_ns: int
    offset: int  # end of the last complete line; <= size (they differ after a half-written line)
    head_sha1: str  # SHA-1 of the first min(4096, offset) bytes: the grown-same-file check
    lines: int  # newlines before offset
    first_prompt_final: bool
    files_smallest: list[str]  # the 10 alphabetically smallest paths: all a merge needs
    tool_calls: int
    last_assistant: str | None
    pending_questions: list[str]
    last_ts: str | None


@dataclass
class CachedTranscript:
    info: TranscriptInfo
    checkpoint: Checkpoint | None  # None when the file could not be read: always parsed again


def _first_prompt_final(first: str, lines: int, partial: bytes) -> bool:
    """The first prompt can no longer change: 50 complete lines exist, or a prompt was found
    with no half-written line after it (the head of an append-only file is fixed)."""
    return lines >= _HEAD_LINES_FOR_FIRST_MSG or (not partial and first not in _NO_FIRST_MESSAGE)


def _checkpoint(
    state: _State, st: os.stat_result, *, offset: int, head: bytes, lines: int, first_final: bool
) -> Checkpoint:
    return Checkpoint(
        dev=st.st_dev,
        ino=st.st_ino,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        offset=offset,
        head_sha1=hashlib.sha1(head).hexdigest(),
        lines=lines,
        first_prompt_final=first_final,
        files_smallest=sorted(state.files)[:_MAX_FILES_TOUCHED],
        tool_calls=state.tool_calls,
        last_assistant=state.last_assistant,
        pending_questions=sorted(state.pending),
        last_ts=state.last_ts,
    )


def _resume(cp: Checkpoint) -> _State:
    return _State(
        files=set(cp.files_smallest),
        tool_calls=cp.tool_calls,
        last_assistant=cp.last_assistant,
        pending=set(cp.pending_questions),
        last_ts=cp.last_ts,
    )


def _full(path: Path, st: os.stat_result, home_prefix: str, stats: BuildStats) -> CachedTranscript:
    """Parse from byte 0 and take a fresh checkpoint."""
    try:
        with path.open("rb") as f:
            data = f.read(st.st_size)
    except OSError:
        return CachedTranscript(info=_unreadable(path, st), checkpoint=None)
    stats.transcript_bytes_read += len(data)
    state = _State()
    end, partial = _consume(state, data, home_prefix)
    first = _first_message_of(data)
    lines = data.count(b"\n", 0, end)
    cp = _checkpoint(
        state,
        st,
        offset=end,
        head=data[: min(_HEAD_CHECK_BYTES, end)],
        lines=lines,
        first_final=_first_prompt_final(first, lines, partial),
    )
    return CachedTranscript(info=_info(path, st, state, partial, first, home_prefix), checkpoint=cp)


def _tail(
    path: Path, st: os.stat_result, cp: Checkpoint, first_prompt: str, home_prefix: str, stats: BuildStats
) -> CachedTranscript | None:
    """Parse only what was appended after *cp*; None when the file's head no longer matches."""
    with path.open("rb") as f:
        head = f.read(min(_HEAD_CHECK_BYTES, cp.offset))
        if hashlib.sha1(head).hexdigest() != cp.head_sha1:
            return None
        f.seek(cp.offset)
        data = f.read(st.st_size - cp.offset)
    stats.transcript_bytes_read += len(data)
    state = _resume(cp)
    end, partial = _consume(state, data, home_prefix)
    offset = cp.offset + end
    lines = cp.lines + data.count(b"\n", 0, end)
    if cp.first_prompt_final:
        first, final = first_prompt, True
    else:
        first = extract_first_message(path)
        final = _first_prompt_final(first, lines, partial)
    if len(head) < _HEAD_CHECK_BYTES:  # the identity check grows with the file, up to 4 KB
        head = (head + data)[: min(_HEAD_CHECK_BYTES, offset)]
    return CachedTranscript(
        info=_info(path, st, state, partial, first, home_prefix),
        checkpoint=_checkpoint(state, st, offset=offset, head=head, lines=lines, first_final=final),
    )


def _route(prior: CachedTranscript | None, st: os.stat_result) -> str:
    """unchanged | tail | full, from the file's identity against its checkpoint."""
    cp = prior.checkpoint if prior is not None else None
    if cp is None:
        return "full"
    if (cp.dev, cp.ino, cp.size, cp.mtime_ns) == (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns):
        return "unchanged"
    if (cp.dev, cp.ino) == (st.st_dev, st.st_ino) and st.st_size > cp.size:
        return "tail"
    return "full"


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """The full parse: first prompt, files touched, tool-call count, last-turn shape.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    return _full(path, st or path.stat(), _home_prefix(home), BuildStats()).info


def transcript_info(
    path: Path,
    *,
    cache: dict[str, CachedTranscript],
    home: Path | None = None,
    stats: BuildStats | None = None,
) -> TranscriptInfo:
    """Reuse an unchanged transcript, parse only the appended tail of a grown one, and fully
    parse anything else (1b spec §3.3). Updates *cache* in place.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    if stats is None:
        stats = BuildStats()
    st = path.stat()
    key = str(path)
    prior = cache.get(key)
    route = _route(prior, st)
    if route == "unchanged" and prior is not None:
        return prior.info
    home_prefix = _home_prefix(home)
    entry: CachedTranscript | None = None
    if route == "tail" and prior is not None and prior.checkpoint is not None:
        try:
            entry = _tail(path, st, prior.checkpoint, prior.info.first_prompt, home_prefix, stats)
        except Exception:  # 1b spec §4: any failure in a tail parse falls back to a full parse
            entry = None
        if entry is not None:
            stats.transcripts_tail_parsed += 1
    if entry is None:
        entry = _full(path, st, home_prefix, stats)
        stats.transcripts_full_parsed += 1
    cache[key] = entry
    return entry.info


# ----- cache file (version 2) ----------------------------------------------------------


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cached_info(payload: Any) -> TranscriptInfo | None:
    """Rebuild a cached TranscriptInfo, or None when any field is missing or has the wrong type."""
    if not isinstance(payload, dict):
        return None
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


def _cached_checkpoint(v: Any) -> Checkpoint | None:
    """Rebuild a cached Checkpoint, or None when any field is missing, mistyped or inconsistent."""
    if not isinstance(v, dict) or "last_assistant" not in v or "last_ts" not in v:
        return None
    ints = ("dev", "ino", "size", "mtime_ns", "offset", "lines", "tool_calls")
    if not all(_is_int(v.get(k)) for k in ints) or not 0 <= v["offset"] <= v["size"]:
        return None
    files, pending, last_ts = v.get("files_smallest"), v.get("pending_questions"), v["last_ts"]
    if not (
        isinstance(v.get("head_sha1"), str)
        and isinstance(v.get("first_prompt_final"), bool)
        and isinstance(files, list)
        and len(files) <= _MAX_FILES_TOUCHED
        and all(isinstance(x, str) for x in files)
        and isinstance(pending, list)
        and all(isinstance(x, str) for x in pending)
        and (v["last_assistant"] is None or v["last_assistant"] in _ASSISTANT_KINDS)
        and (last_ts is None or isinstance(last_ts, str))
    ):
        return None
    return Checkpoint(
        dev=v["dev"],
        ino=v["ino"],
        size=v["size"],
        mtime_ns=v["mtime_ns"],
        offset=v["offset"],
        head_sha1=v["head_sha1"],
        lines=v["lines"],
        first_prompt_final=v["first_prompt_final"],
        files_smallest=list(files),
        tool_calls=v["tool_calls"],
        last_assistant=v["last_assistant"],
        pending_questions=list(pending),
        last_ts=last_ts,
    )


def _cached_transcript(v: Any) -> CachedTranscript | None:
    if not isinstance(v, dict) or "checkpoint" not in v:
        return None
    info = _cached_info(v.get("info"))
    if info is None:
        return None
    if v["checkpoint"] is None:
        return CachedTranscript(info=info, checkpoint=None)
    cp = _cached_checkpoint(v["checkpoint"])
    return None if cp is None else CachedTranscript(info=info, checkpoint=cp)


def load_transcript_cache(cache_path: Path) -> dict[str, CachedTranscript]:
    """Load the cache. A missing, corrupt or pre-1b (plan 1) file is empty; a bad entry is skipped."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or not _is_int(raw.get("version")) or raw["version"] != _CACHE_VERSION:
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, CachedTranscript] = {}
    for key, value in entries.items():
        entry = _cached_transcript(value)
        if entry is not None:
            out[key] = entry
    return out


def write_transcript_cache(cache_path: Path, entries: dict[str, CachedTranscript]) -> bool:
    """Atomically replace the cache file (unique temp + ``os.replace``). Best-effort: False instead of raising."""
    payload = {"version": _CACHE_VERSION, "entries": {k: asdict(v) for k, v in entries.items()}}
    try:
        atomic_write_text(cache_path, json.dumps(payload))
    except OSError:
        return False
    return True


__all__ = [
    "TRANSCRIPT_CACHE_FILENAME",
    "CachedTranscript",
    "Checkpoint",
    "extract_files_touched",
    "extract_first_message",
    "load_transcript_cache",
    "parse_transcript",
    "transcript_info",
    "write_transcript_cache",
]

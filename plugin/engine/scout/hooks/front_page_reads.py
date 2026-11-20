"""PostToolUse hook — a run that edits a front page without reading its project's knowledge/ notes is warned.

A usage audit of scheduled runs found that only a third of front-page edits came
after the run had read any of that project's ``knowledge/`` notes: "Where it
stands" got rewritten from the day's inbox alone, against the notes that hold the
detail. This hook makes the gap visible at the moment it happens.

  - **When**: after a scheduled run's ``Write``/``Edit``/``MultiEdit`` of a
    project's front page, ``<projects_dir>/<name>/<name>.md`` (``kb_structure.
    projects_dir``, default ``knowledge-base/projects``), when the run's reader may
    see that page and at least one note under its ``knowledge/`` folder.
  - **What counts as reading one**: anything earlier in this session's transcript,
    or a subagent's transcript beside it, that touched a note under that
    ``knowledge/`` folder: a ``Read``, ``Write``, ``Edit`` or ``MultiEdit`` of it,
    or a ``scoutctl kb read|search|entity|query|related`` whose command or result
    names a path there. The transcript is the source because
    ``.scout-logs/kb-reads.jsonl`` holds counts and an argument hash, never a path
    (#453 §7.4), so it can't say which project a read was about. The scan streams
    the transcript, stops at the first match, and gives up silently past
    :data:`TRANSCRIPT_MAX_BYTES`.
  - **What it names**: only notes the reader may see (``scoutctl kb``'s labels, so
    a ``delegate:<id>`` reader never learns a hidden note exists, #550), newest
    ``as_of`` first, each private one marked ``(private)``.
  - **Warn only**: it never blocks, never changes the call, and any failure (no
    transcript, an unreadable vault, labels it can't get) adds nothing.

hooks.json starts Python only for a run's write whose ``file_path`` is shaped
``<dir>/<x>/<x>.md``; owner sessions exit in the shell. :func:`check` then runs
its cheap tests before importing the labels or the transcript code.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import IO, Any

GATE = "front-page-reads"
MARK = f"[scout:{GATE}]"
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
FILE_TOOLS = frozenset({"Read", *WRITE_TOOLS})
NOTES_NAMED = 5
TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024
READ_CHUNK = 1 << 20
_KB_READ = re.compile(r"\bkb\s+(read|search|entity|query|related)\b")


class _TooBig(Exception):
    """The transcripts ran past TRANSCRIPT_MAX_BYTES: the scan can't tell, so it stays silent."""


def front_page_project(rel: str, projects_dir: str) -> str | None:
    """The project name when ``rel`` is its front page ``<projects_dir>/<name>/<name>.md``, else None."""
    parts, root = PurePosixPath(rel).parts, PurePosixPath(projects_dir).parts
    if len(parts) == len(root) + 2 and parts[: len(root)] == root and parts[-1] == f"{parts[-2]}.md":
        return parts[-2]
    return None


def _result_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, Mapping))
    return ""


def _rel(vault: Path, raw: object, cwd: Path) -> str | None:
    from scout.kb.privacy import normalize_rel

    if not isinstance(raw, str) or not raw:
        return None
    p = Path(raw).expanduser()
    try:
        return normalize_rel(vault, p if p.is_absolute() else cwd / p)
    except ValueError:
        return None


def _regular_file(path: Path) -> int | None:
    """``path``'s size when it is a regular file itself (not a symlink, FIFO or device), else None."""
    try:
        st = path.lstat()
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def transcript_lines(transcript: Path, max_bytes: int = TRANSCRIPT_MAX_BYTES) -> Iterator[str]:
    """The session's JSONL lines, then each subagent transcript's (``<session>/subagents/*.jsonl``).

    Only regular files are read: a symlink, FIFO or device file is skipped, so nothing in a
    run-writable folder can make the hook hang or stream without end. Raises :class:`_TooBig`
    before reading when the files' sizes add up past ``max_bytes``, and while reading once
    more than that has come back. A line longer than :data:`READ_CHUNK` comes back in pieces.
    """
    paths = [transcript]
    sub = transcript.with_suffix("") / "subagents"
    if sub.is_dir() and not sub.is_symlink():
        paths.extend(sorted(sub.glob("*.jsonl")))
    sized = [(path, size) for path in paths if (size := _regular_file(path)) is not None]
    if sum(size for _, size in sized) > max_bytes:
        raise _TooBig
    seen = 0
    for path, _ in sized:
        try:
            # No symlink, and no blocking open: a file swapped for a FIFO since lstat fails fstat below.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            f = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
        except OSError:
            os.close(fd)
            continue
        with f:
            while line := f.readline(READ_CHUNK):
                seen += len(line)
                if seen > max_bytes:
                    raise _TooBig
                yield line


def touched_knowledge(lines: Iterable[str], knowledge_rel: str, vault: Path, cwd: Path) -> bool:
    """Whether any transcript line records a read or write of a note under ``knowledge_rel``.

    Only lines that name the folder (``<name>/knowledge``) or a ``kb`` read command are parsed.
    A ``kb`` call counts when its command names the folder, or when its result does.
    """
    prefix = knowledge_rel.rstrip("/") + "/"
    needle = "/".join(PurePosixPath(knowledge_rel).parts[-2:]).lower()
    kb_calls: set[str] = set()
    for line in lines:
        if needle not in line.lower() and not _KB_READ.search(line):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name, tool_input = block.get("name"), block.get("input")
                tool_input = tool_input if isinstance(tool_input, dict) else {}
                if name in FILE_TOOLS:
                    rel = _rel(vault, tool_input.get("file_path"), cwd)
                    if rel is not None and rel.startswith(prefix):
                        return True
                elif name == "Bash":
                    command = str(tool_input.get("command") or "")
                    if _KB_READ.search(command):
                        if prefix in command:
                            return True
                        kb_calls.add(str(block.get("id")))
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id")) in kb_calls:
                if prefix in _result_text(block):
                    return True
    return False


def visible_notes(vault: Path, knowledge_rel: str, labels: Mapping[str, str]) -> list[dict[str, Any]]:
    """The notes under ``knowledge_rel`` the reader may see (``labels``), newest ``as_of`` first."""
    from scout.scripts.session_context_kb import knowledge_notes

    return knowledge_notes(vault, knowledge_rel, labels, full=False)


def warning(front: str, notes: list[dict[str, Any]]) -> str:
    def name(n: Mapping[str, Any]) -> str:
        return f"`{n['path']}`" + (" (private)" if n.get("visibility") != "normal" else "")

    shown = ", ".join(name(n) for n in notes[:NOTES_NAMED])
    more = f" and {len(notes) - NOTES_NAMED} more" if len(notes) > NOTES_NAMED else ""
    return (
        f"{MARK} You edited the front page {front}, but this session has read none of its project's "
        f'{len(notes)} knowledge/ note{"s" if len(notes) != 1 else ""}: {shown}{more}. Before "Where it stands" '
        "changes, read the notes this edit draws on with `scripts/scoutctl kb read <path>` and check the page "
        "against them; `.scout-cache/session-context.json` → `project_knowledge` lists each project's notes with "
        "their `as_of` and the page's Go deeper map. This is a warning: the edit stands."
    )


def _looks_like_a_front_page(raw: object) -> bool:
    """The cheap string test: a ``.md`` file named after its own folder (``…/<x>/<x>.md``)."""
    if not isinstance(raw, str) or not raw.endswith(".md"):
        return False
    path = PurePosixPath(raw)
    return path.parent.name == path.stem


def check(payload: Mapping[str, Any], *, reader: str) -> str | None:
    """The warning for one PostToolUse payload as ``reader`` sees the vault, or None.

    The cheap tests come first (tool, path shape, vault, projects_dir), so a write that isn't a
    front page returns before the labels, the KB reader or the transcript code is imported.
    """
    from scout.hooks.kb_read_label import session_vault

    tool_input = payload.get("tool_input")
    if payload.get("tool_name") not in WRITE_TOOLS or not isinstance(tool_input, Mapping):
        return None
    if not _looks_like_a_front_page(tool_input.get("file_path")):
        return None
    vault = session_vault(payload)
    if vault is None:
        return None
    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else vault
    rel = _rel(vault, tool_input.get("file_path"), cwd)
    if rel is None:
        return None
    from scout.kb.structure import load_structure_config

    projects_dir = load_structure_config(vault).projects_dir
    name = front_page_project(rel, projects_dir)
    if name is None:
        return None
    from scout.hooks.session_tool_log import _resolve_transcript_path
    from scout.kb.privacy import reads_any
    from scout.kb.read import visibility_map

    if not reads_any(reader):
        return None
    knowledge_rel = f"{projects_dir}/{name}/knowledge"
    labels = visibility_map(vault, reader, [rel, f"{knowledge_rel}/**"], who=GATE)
    if rel not in labels:
        return None  # a page this reader may not see (or labels unavailable): say nothing about it
    notes = visible_notes(vault, knowledge_rel, labels)
    if not notes:
        return None
    transcript = _resolve_transcript_path(dict(payload))
    if transcript is None:
        return None
    try:
        if touched_knowledge(transcript_lines(transcript), knowledge_rel, vault, cwd):
            return None
    except _TooBig:
        return None
    return warning(rel, notes)


def run(*, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Read one PostToolUse payload; return the hook output, or None for nothing to add. Never raises."""
    from scout.kb.reader import is_scheduled, resolve_reader

    env = os.environ if env is None else env
    try:
        if not is_scheduled(env):
            return None
        payload = json.load(stdin if stdin is not None else sys.stdin)
        text = check(payload, reader=resolve_reader(None, env)) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 — warn only: a check that can't run adds nothing
        return None
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def main() -> int:
    """CLI entry point: scoutctl hook front-page-reads. Always 0."""
    out = run()
    if out is not None:
        print(json.dumps(out))
    return 0


__all__ = [
    "TRANSCRIPT_MAX_BYTES",
    "check",
    "front_page_project",
    "main",
    "run",
    "touched_knowledge",
    "transcript_lines",
    "visible_notes",
    "warning",
]

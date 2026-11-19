"""PostToolUse hook — a run that edits a front page without reading its project's knowledge/ notes is warned.

A usage audit of scheduled runs found that only a third of front-page edits came
after the run had read any of that project's ``knowledge/`` notes: "Where it
stands" got rewritten from the day's inbox alone, against the notes that hold the
detail. This hook makes the gap visible at the moment it happens.

  - **When**: after a scheduled run's ``Write``/``Edit``/``MultiEdit`` of a
    project's front page, ``<projects_dir>/<name>/<name>.md`` (``kb_structure.
    projects_dir``, default ``knowledge-base/projects``), whose ``knowledge/``
    folder holds at least one note.
  - **What counts as reading one**: anything earlier in this session's transcript
    that touched a note under that ``knowledge/`` folder: a ``Read``, ``Write``,
    ``Edit`` or ``MultiEdit`` of it, or a ``scoutctl kb read``/``kb search`` whose
    command or result names a path there. The transcript is the source because
    ``.scout-logs/kb-reads.jsonl`` holds counts and an argument hash, never a
    path (#453 §7.4), so it can't say which project a read was about.
  - **What it does**: adds ``additionalContext`` naming the front page and the
    project's newest notes. **Warn only**: it never blocks, never changes the
    call, and any failure (no transcript, an unreadable vault) adds nothing.

hooks.json starts Python only for a run's write whose payload names
``knowledge-base``; owner sessions exit in the shell.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import IO, Any

GATE = "front-page-reads"
MARK = f"[scout:{GATE}]"
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
FILE_TOOLS = frozenset({"Read", *WRITE_TOOLS})
NOTES_NAMED = 5
_KB_READ = re.compile(r"\bkb\s+(read|search)\b")


def front_page_project(rel: str, projects_dir: str) -> str | None:
    """The project name when ``rel`` is its front page ``<projects_dir>/<name>/<name>.md``, else None."""
    parts, root = PurePosixPath(rel).parts, PurePosixPath(projects_dir).parts
    if len(parts) == len(root) + 2 and parts[: len(root)] == root and parts[-1] == f"{parts[-2]}.md":
        return parts[-2]
    return None


def _result_text(response: Mapping[str, Any]) -> str:
    content = response.get("content")
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


def touched_knowledge(calls: Iterable[Any], knowledge_rel: str, vault: Path, cwd: Path) -> bool:
    """Whether any of ``calls`` (``ToolCallRecord``-shaped) read or wrote a note under ``knowledge_rel``."""
    prefix = knowledge_rel.rstrip("/") + "/"
    for call in calls:
        tool_input = call.tool_input if isinstance(call.tool_input, Mapping) else {}
        if call.tool_name in FILE_TOOLS:
            rel = _rel(vault, tool_input.get("file_path"), cwd)
            if rel is not None and rel.startswith(prefix):
                return True
        elif call.tool_name == "Bash":
            command = str(tool_input.get("command") or "")
            if _KB_READ.search(command) and (prefix in command or prefix in _result_text(call.tool_response)):
                return True
    return False


def knowledge_notes(vault: Path, knowledge_rel: str) -> list[str]:
    """The notes under ``knowledge_rel``, newest file first."""
    folder = vault / knowledge_rel
    if not folder.is_dir():
        return []
    notes = [p for p in folder.rglob("*.md") if p.is_file()]
    notes.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return [p.relative_to(vault).as_posix() for p in notes]


def warning(front: str, notes: list[str]) -> str:
    shown = ", ".join(f"`{n}`" for n in notes[:NOTES_NAMED])
    more = f" and {len(notes) - NOTES_NAMED} more" if len(notes) > NOTES_NAMED else ""
    return (
        f"{MARK} You edited the front page {front}, but this session has read none of its project's "
        f'{len(notes)} knowledge/ note{"s" if len(notes) != 1 else ""}: {shown}{more}. Before "Where it stands" '
        "changes, read the notes this edit draws on with `scripts/scoutctl kb read <path>` and check the page "
        "against them; `.scout-cache/session-context.json` → `project_knowledge` lists each project's notes with "
        "their `as_of` and the page's Go deeper map. This is a warning: the edit stands."
    )


def check(payload: Mapping[str, Any]) -> str | None:
    """The warning for one PostToolUse payload, or None."""
    from scout.hooks.kb_read_label import session_vault
    from scout.hooks.session_tool_log import _iter_rows, _resolve_transcript_path, extract_tool_calls
    from scout.kb.structure import load_structure_config

    if payload.get("tool_name") not in WRITE_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    vault = session_vault(payload)
    if vault is None or not isinstance(tool_input, Mapping):
        return None
    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else vault
    rel = _rel(vault, tool_input.get("file_path"), cwd)
    projects_dir = load_structure_config(vault).projects_dir
    name = front_page_project(rel, projects_dir) if rel else None
    if rel is None or name is None:
        return None
    knowledge_rel = f"{projects_dir}/{name}/knowledge"
    notes = knowledge_notes(vault, knowledge_rel)
    if not notes:
        return None
    transcript = _resolve_transcript_path(dict(payload))
    if transcript is None:
        return None
    if touched_knowledge(extract_tool_calls(_iter_rows(transcript)), knowledge_rel, vault, cwd):
        return None
    return warning(rel, notes)


def run(*, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Read one PostToolUse payload; return the hook output, or None for nothing to add. Never raises."""
    from scout.kb.reader import is_scheduled

    env = os.environ if env is None else env
    try:
        if not is_scheduled(env):
            return None
        payload = json.load(stdin if stdin is not None else sys.stdin)
        text = check(payload) if isinstance(payload, dict) else None
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


__all__ = ["check", "front_page_project", "knowledge_notes", "main", "run", "touched_knowledge", "warning"]

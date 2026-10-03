"""Read-only loaders for ``~/.claude`` (spec §4.4, §4.1).

* ``sessions/<pid>.json`` exists only while that Claude Code process is alive;
  we double-check with a zero signal so a crash-leftover file is not "open".
* ``projects/<encoded-cwd>/<uuid>.jsonl`` is the transcript; the same uuid can
  appear under two encoded dirs when a session moved cwd — the newest wins.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scout.sessions.model import SourceError


def default_claude_home() -> Path:
    return Path.home() / ".claude"


def pid_alive(pid: int) -> bool:
    # 0 and -1 address a process group / every process — never "this session is alive".
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):  # OverflowError: pid beyond the C pid_t range
        return False
    return True


@dataclass(frozen=True)
class LiveProcess:
    pid: int
    cli_session_id: str
    cwd: str | None
    started_at_ms: int | None


def load_live_processes(
    claude_home: Path, *, is_alive: Callable[[int], bool] = pid_alive
) -> tuple[dict[str, LiveProcess], list[SourceError]]:
    root = claude_home / "sessions"
    live: dict[str, LiveProcess] = {}
    errors: list[SourceError] = []
    if not root.is_dir():
        return live, errors
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            errors.append(SourceError(source="claude-home", message=f"{path.name}: {e}"))
            continue
        if not isinstance(raw, dict):
            errors.append(SourceError(source="claude-home", message=f"{path.name}: not a JSON object"))
            continue
        pid, sid = raw.get("pid"), raw.get("sessionId")
        # Validate pid as a positive int (not bool), and sessionId as non-empty str
        if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0) or not (isinstance(sid, str) and sid):
            msg = f"{path.name}: pid or sessionId missing or malformed"
            errors.append(SourceError(source="claude-home", message=msg))
            continue
        if not is_alive(pid):
            continue
        cwd = raw.get("cwd") if isinstance(raw.get("cwd"), str) else None
        # Accept startedAt only as int that is not a bool
        started_at = raw.get("startedAt")
        started = started_at if (isinstance(started_at, int) and not isinstance(started_at, bool)) else None
        live[sid] = LiveProcess(pid=pid, cli_session_id=sid, cwd=cwd, started_at_ms=started)
    return live, errors


def transcript_paths(claude_home: Path) -> dict[str, Path]:
    root = claude_home / "projects"
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in root.glob("*/*.jsonl"):
        uuid = path.stem
        prev = out.get(uuid)
        if prev is None:
            out[uuid] = path
            continue
        try:
            if path.stat().st_mtime_ns > prev.stat().st_mtime_ns:
                out[uuid] = path
        except OSError:
            continue
    return out


def project_path_from_dirname(dirname: str) -> str:
    """Decode Claude Code's project-dir naming back into a filesystem path.

    ``-Users-alex-code-repo`` → ``/Users/alex/code/repo``. Lossy for folder
    names that contain hyphens — callers prefer the desktop record's ``cwd``
    whenever one exists.
    """
    if not dirname.startswith("-"):
        return dirname
    return "/" + dirname[1:].replace("-", "/")


__all__ = [
    "LiveProcess",
    "default_claude_home",
    "load_live_processes",
    "pid_alive",
    "project_path_from_dirname",
    "transcript_paths",
]

"""Builders for fake Claude desktop-store and ~/.claude trees.

Everything is rooted at ``Path.home()``, which the autouse ``_hermetic_env``
fixture points at a per-test tmp dir. All identifiers are synthetic
(``example-org/…``, ``Alex``) per CLAUDE.md.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ORG = "org-0000"
USER = "user-0000"


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


def claude_home() -> Path:
    return Path.home() / ".claude"


def write_desktop_record(support: Path, session_id: str, **fields: Any) -> Path:
    """Write one ``local_<id>.json``. Unspecified fields get plausible defaults."""
    record: dict[str, Any] = {
        "sessionId": session_id,
        "cliSessionId": fields.pop("cliSessionId", f"{session_id[-8:]:0>8}-0000-0000-0000-000000000000"),
        "cwd": "/Users/alex/code/example-repo",
        "originCwd": "/Users/alex/code/example-repo",
        "createdAt": 1_788_400_000_000,
        "lastActivityAt": 1_788_800_000_000,
        "model": "claude-opus-5",
        "effort": "high",
        "isArchived": False,
        "permissionMode": "auto",
        "title": "Fix the parser",
        "titleSource": "auto",
        "completedTurns": 4,
        "enabledMcpTools": {"": True},
    }
    record.update(fields)
    d = support / "claude-code-sessions" / ORG / USER
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.json"
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


def write_desktop_config(support: Path, groups: dict[str, str], assignments: dict[str, str]) -> Path:
    """``groups`` maps group id → name; ``assignments`` maps session id → group id."""
    support.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {},
        "preferences": {
            "epitaxyPrefs": {
                "dframe-group-scopes": {
                    f"{ORG}/{USER}": {
                        "groups": [{"id": gid, "name": name} for gid, name in groups.items()],
                        "assignments": {f"code:{sid}": gid for sid, gid in assignments.items()},
                    }
                }
            }
        },
    }
    p = support / "claude_desktop_config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def write_worktrees(support: Path, leases: dict[str, dict[str, Any]]) -> Path:
    support.mkdir(parents=True, exist_ok=True)
    p = support / "git-worktrees.json"
    p.write_text(json.dumps({"worktrees": leases}), encoding="utf-8")
    return p


def write_transcript(
    home: Path, encoded_dir: str, uuid: str, rows: list[dict[str, Any]], *, mtime_ago_hours: float = 1.0
) -> Path:
    """Write rows the way Claude Code does: one compact JSON object per line."""
    p = home / "projects" / encoded_dir / f"{uuid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n", encoding="utf-8")
    ts = (datetime.now(tz=UTC) - timedelta(hours=mtime_ago_hours)).timestamp()
    os.utime(p, (ts, ts))
    return p


def write_pid_file(home: Path, pid: int, uuid: str, cwd: str) -> Path:
    d = home / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{pid}.json"
    p.write_text(
        json.dumps({"pid": pid, "sessionId": uuid, "cwd": cwd, "startedAt": 1_788_800_000_000, "entrypoint": "cli"}),
        encoding="utf-8",
    )
    return p

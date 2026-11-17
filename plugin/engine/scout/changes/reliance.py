"""Owner-session reliance: which Scout surfaces a session used, recorded as one ``session-reliance`` signal each.

Rows hold the surface, a hashed session id and a timestamp only: never the path read, the prompt, the session id in
clear, or the project the session ran in. Scheduled runs never count, and a session outside the vault counts only
when the owner has consented to out-of-vault activity."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout import paths
from scout.changes.consent import out_of_vault_answer
from scout.changes.models import Signal
from scout.changes.store import append_signal, is_owner_session, owner_dir
from scout.hooks.run_context import RunContext, _text_of

_PATH_TOOLS = frozenset({"Read", "Grep", "Glob", "Edit"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
SEEN_NAME = "reliance-seen.json"
_SEEN_SCHEMA = 1
_SEEN_TTL = timedelta(days=7)


def _resolve(raw: str, base: Path) -> Path | None:
    try:
        p = Path(raw).expanduser()
        return (p if p.is_absolute() else base / p).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _in_vault(cwd: str, vault: Path) -> bool:
    if not cwd:
        return False
    resolved = _resolve(cwd, Path("/"))
    return resolved is not None and _is_under(resolved, _resolve(str(vault), Path("/")) or vault)


def _rows(transcript_path: Path):
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def surfaces_used(transcript_path: Path, vault: Path, *, cwd: str) -> set[str]:
    vault_root = _resolve(str(vault), Path("/")) or vault
    kb_root = vault_root / "knowledge-base"
    ai_root = vault_root / "action-items"
    base = _resolve(cwd, Path("/")) if cwd else None
    base = base or vault_root
    surfaces: set[str] = set()
    if _in_vault(cwd, vault):
        surfaces.add("vault-session")
    for row in _rows(transcript_path):
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if msg.get("role") == "user":
            text = _text_of(content)
            if "<command-name>/scout-" in text or text.lstrip().startswith("/scout-"):
                surfaces.add("skill")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue
            if name in _PATH_TOOLS:
                for key in _PATH_KEYS:
                    raw = inp.get(key)
                    if not isinstance(raw, str) or not raw:
                        continue
                    p = _resolve(raw, base)
                    if p is None:
                        continue
                    if _is_under(p, kb_root):
                        surfaces.add("kb")
                    elif _is_under(p, ai_root):
                        surfaces.add("action-items")
            elif name == "Skill":
                skill = inp.get("skill")
                if isinstance(skill, str) and skill.startswith("scout:"):
                    surfaces.add("skill")
            elif name == "Bash":
                command = inp.get("command")
                if isinstance(command, str) and "scoutctl" in command:
                    surfaces.add("scoutctl")
    return surfaces


def _seen_path(env: Mapping[str, str]) -> Path:
    return owner_dir(env) / SEEN_NAME


def _read_seen(env: Mapping[str, str]) -> dict[str, Any]:
    """The per-session surfaces already counted. A missing or malformed marker is empty."""
    try:
        data = json.loads(_seen_path(env).read_text(encoding="utf-8"))
        sessions = data["sessions"] if data.get("schema") == _SEEN_SCHEMA else {}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}
    if not isinstance(sessions, dict):
        return {}
    return {
        k: v
        for k, v in sessions.items()
        if isinstance(v, dict)
        and isinstance(v.get("surfaces"), list)
        and all(isinstance(x, str) for x in v["surfaces"])
    }


def _write_seen(env: Mapping[str, str], sessions: dict[str, Any], now: datetime) -> None:
    """Atomic 0600 write, dropping sessions not touched for a week. Best-effort: the rows are already written."""
    kept: dict[str, Any] = {}
    for key, entry in sessions.items():
        try:
            if now - datetime.fromisoformat(str(entry.get("at"))) > _SEEN_TTL:
                continue
        except (ValueError, TypeError):
            continue
        kept[key] = entry
    path = _seen_path(env)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"schema": _SEEN_SCHEMA, "sessions": kept}, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def record_reliance(
    payload: dict[str, Any],
    transcript_path: Path | None,
    ctx: RunContext,
    env: Mapping[str, str],
    *,
    now: datetime,
) -> int:
    """Rows written. Zero for runs, non-owner sessions, a missing transcript, or unconsented out-of-vault sessions."""
    if ctx.mode or not is_owner_session(env) or transcript_path is None:
        return 0
    vault = paths.data_dir()
    cwd = str(payload.get("cwd") or "")
    if not _in_vault(cwd, vault) and out_of_vault_answer(env) is not True:
        return 0
    surfaces = surfaces_used(transcript_path, vault, cwd=cwd)
    session = hashlib.sha256(str(payload.get("session_id") or "").encode()).hexdigest()[:16]
    # Stop fires after every turn; count each (session, surface) once.
    seen = _read_seen(env)
    already = set((seen.get(session) or {}).get("surfaces") or [])
    new = sorted(surfaces - already)
    if not new:
        return 0
    for surface in new:
        append_signal(Signal(ts=now, kind="session-reliance", surface=surface, session=session), vault=vault, env=env)
    seen[session] = {"surfaces": sorted(already | surfaces), "at": now.isoformat()}
    _write_seen(env, seen, now)
    return len(new)

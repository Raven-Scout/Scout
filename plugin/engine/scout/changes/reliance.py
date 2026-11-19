"""Owner-session reliance: which Scout surfaces a session used, recorded as one ``session-reliance`` signal each.

Rows hold the surface, a hashed session id and a timestamp only: never the path read, the prompt, the session id in
clear, or the project the session ran in. The hash is unsalted (see scout.changes.store): it keeps the id out of the
clear in a local-only ledger, not away from someone who holds the transcripts. Scheduled runs never count, and a
session outside the vault counts only when the owner has consented to out-of-vault activity.

The Stop hook runs this after every turn of every owner session, in any project, so the cheap refusals go first:
a run or a non-owner session returns before anything is read; a cwd inside the vault folder needs no git; anywhere
else the consent record is read before anything else, and the one git call (is this cwd a worktree of the vault?)
is answered from a short-lived cache. An unconsented session outside the vault never opens its transcript."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout.changes.consent import out_of_vault_answer
from scout.changes.models import Signal
from scout.changes.store import append_signal, is_owner_session, owner_dir, vault_key
from scout.changes.vault import installed_vault, vault_roots
from scout.hooks.run_context import RunContext, _text_of

_PATH_TOOLS = frozenset({"Read", "Grep", "Glob", "Edit"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
# The Scout plugin's own commands only: /scout-work, and the namespaced /scout:scout-briefing.
_COMMAND_PREFIXES = ("/scout-", "/scout:")
# Every surface surfaces_used can report. A session already counted on all it could reach has nothing left to find.
SURFACES = frozenset({"vault-session", "kb", "action-items", "skill", "scoutctl"})
SEEN_NAME = "reliance-seen.json"
_SEEN_SCHEMA = 1
_SEEN_TTL = timedelta(days=7)
# vault_roots runs `git worktree list`; repeated Stops reuse its answer for this long.
ROOTS_NAME = "vault-roots-cache.json"
_ROOTS_SCHEMA = 1
_ROOTS_TTL = timedelta(minutes=10)


def _resolve(raw: str, base: Path) -> Path | None:
    try:
        p = Path(raw).expanduser()
        return (p if p.is_absolute() else base / p).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _under_any(path: Path, roots: Sequence[Path]) -> bool:
    return any(_is_under(path, r) for r in roots)


def _in_vault(cwd: str, roots: Sequence[Path]) -> bool:
    """Whether the session's cwd is the vault or inside one of its worktrees (``roots``: :func:`vault_roots`)."""
    if not cwd:
        return False
    resolved = _resolve(cwd, Path("/"))
    return resolved is not None and _under_any(resolved, roots)


def _scout_command(text: str) -> bool:
    """A Scout slash command: ``/scout-…`` or the plugin-namespaced ``/scout:…``, typed or as a command tag."""
    return any(f"<command-name>{p}" in text for p in _COMMAND_PREFIXES) or text.lstrip().startswith(_COMMAND_PREFIXES)


def _rows(transcript_path: Path):
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    row = json.loads(raw)
                except (ValueError, RecursionError):  # RecursionError: a line nested deeper than the decoder goes
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def surfaces_used(transcript_path: Path, vault: Path, *, cwd: str, roots: Sequence[Path] | None = None) -> set[str]:
    """``roots`` are the vault's folders (default :func:`vault_roots`): KB and action-item paths count under any."""
    roots = tuple(roots) if roots is not None else vault_roots(vault)
    kb_roots = [r / "knowledge-base" for r in roots]
    ai_roots = [r / "action-items" for r in roots]
    base = _resolve(cwd, Path("/")) if cwd else None
    base = base or roots[0]
    surfaces: set[str] = set()
    if _in_vault(cwd, roots):
        surfaces.add("vault-session")
    for row in _rows(transcript_path):
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if msg.get("role") == "user":
            if _scout_command(_text_of(content)):
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
                    if _under_any(p, kb_roots):
                        surfaces.add("kb")
                    elif _under_any(p, ai_roots):
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


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Atomic 0600 JSON write. Best-effort: a failure leaves the old file (or none) and no temp file."""
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _write_seen(env: Mapping[str, str], sessions: dict[str, Any], now: datetime) -> None:
    """Drops sessions not touched for a week. Best-effort: the rows are already written."""
    kept: dict[str, Any] = {}
    for key, entry in sessions.items():
        try:
            if now - datetime.fromisoformat(str(entry.get("at"))) > _SEEN_TTL:
                continue
        except (ValueError, TypeError):
            continue
        kept[key] = entry
    _atomic_write(_seen_path(env), {"schema": _SEEN_SCHEMA, "sessions": kept})


def _fresh(entry: Any, now: datetime) -> datetime | None:
    """When a cache entry was made, if it is a well-formed entry younger than the TTL (and not from the future)."""
    try:
        at = datetime.fromisoformat(entry["at"])
        roots = entry["roots"]
        age = now - at  # TypeError for a zoneless time
    except (TypeError, KeyError, ValueError):
        return None
    if not (isinstance(roots, list) and roots and all(isinstance(r, str) for r in roots)):
        return None
    return at if timedelta(0) <= age <= _ROOTS_TTL else None


def _newer_worktree(cwd: str, since: datetime) -> bool:
    """Whether ``cwd`` sits in a linked git worktree made after ``since`` (its ``.git`` file is newer): a worktree
    the cache can't know about yet. Stats only, no git."""
    start = _resolve(cwd, Path("/")) if cwd else None
    if start is None:
        return False
    for d in (start, *start.parents):
        try:
            st = (d / ".git").lstat()
        except OSError:
            continue
        return stat.S_ISREG(st.st_mode) and st.st_mtime >= since.timestamp()
    return False


def cached_vault_roots(vault: Path, env: Mapping[str, str], *, now: datetime, cwd: str = "") -> tuple[Path, ...]:
    """:func:`vault_roots`, reused from the owner store for :data:`_ROOTS_TTL` so repeated Stops don't re-run git.

    Refreshed early when ``cwd`` is in a linked worktree newer than the cached answer. The cache lives in the owner
    store (a run can't write it) and holds the vault's own folders only, never a session's project."""
    path = owner_dir(env) / ROOTS_NAME
    key = vault_key(vault)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vaults = data["vaults"] if data.get("schema") == _ROOTS_SCHEMA else {}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        vaults = {}
    if not isinstance(vaults, dict):
        vaults = {}
    entry = vaults.get(key)
    at = _fresh(entry, now)
    if at is not None and entry is not None:  # _fresh only returns a time for a well-formed entry
        cached = tuple(Path(r) for r in entry["roots"])
        if _in_vault(cwd, cached) or not _newer_worktree(cwd, at):
            return cached
    roots = vault_roots(vault)
    kept = {k: v for k, v in vaults.items() if k != key and _fresh(v, now) is not None}
    kept[key] = {"roots": [str(r) for r in roots], "at": now.isoformat()}
    _atomic_write(path, {"schema": _ROOTS_SCHEMA, "vaults": kept})
    return roots


def record_reliance(
    payload: dict[str, Any],
    transcript_path: Path | None,
    ctx: RunContext,
    env: Mapping[str, str],
    *,
    now: datetime,
) -> int:
    """Rows written. Zero for runs, non-owner sessions, a missing transcript, or unconsented out-of-vault sessions.

    Ordered cheapest-first (module docstring): no git and no transcript read until the session is known to count,
    and no transcript read once the session is already counted on every surface it could reach."""
    if ctx.mode or not is_owner_session(env) or transcript_path is None:
        return 0
    # The installed vault, never the cwd's: a session in a vault worktree counts for the vault (spec §6.3).
    vault = installed_vault(env)
    cwd = str(payload.get("cwd") or "")
    roots: tuple[Path, ...] | None = None
    if _in_vault(cwd, (vault,)):
        in_vault = True
    else:
        # Consent first: only the worktree question below can need git, and its answer is cached.
        consented = out_of_vault_answer(env) is True
        roots = cached_vault_roots(vault, env, now=now, cwd=cwd)
        in_vault = _in_vault(cwd, roots)
        if not in_vault and not consented:
            return 0
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return 0  # every session without an id would hash to one shared key and count once, ever
    session = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    # Stop fires after every turn; count each (session, surface) once.
    seen = _read_seen(env)
    already = set((seen.get(session) or {}).get("surfaces") or [])
    if (SURFACES if in_vault else SURFACES - {"vault-session"}) <= already:
        return 0
    if roots is None:
        roots = cached_vault_roots(vault, env, now=now, cwd=cwd)
    surfaces = surfaces_used(transcript_path, vault, cwd=cwd, roots=roots)
    new = sorted(surfaces - already)
    if not new:
        return 0
    for surface in new:
        append_signal(Signal(ts=now, kind="session-reliance", surface=surface, session=session), vault=vault, env=env)
    seen[session] = {"surfaces": sorted(already | surfaces), "at": now.isoformat()}
    _write_seen(env, seen, now)
    return len(new)

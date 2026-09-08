"""Read-only loaders for the Claude desktop app's on-disk session store (spec §1 table, §4.1).

Layout (macOS)::

    ~/Library/Application Support/Claude/
      claude-code-sessions/<org>/<user>/local_<uuid>.json   one record per session
      claude-code-sessions/<org>/<user>/deleted_<uuid>       tombstone (skipped)
      claude_desktop_config.json                            sidebar groups + assignments
      git-worktrees.json                                    worktree leases

Nothing here writes. Every loader returns ``(data, errors)``; a missing
directory or file is *not* an error, a malformed file is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.sessions.model import SourceError


def default_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


@dataclass(frozen=True)
class PRRef:
    number: int
    repo: str
    url: str | None
    legacy_state: str | None  # only the pre-`prs[]` schema carried `prState`


@dataclass(frozen=True)
class DesktopRecord:
    session_id: str
    cli_session_id: str | None
    title: str | None
    title_source: str | None
    cwd: str
    origin_cwd: str
    worktree_path: str | None
    worktree_name: str | None
    branch: str | None
    source_branch: str | None
    created_at_ms: int | None
    last_activity_at_ms: int | None
    model: str | None
    effort: str | None
    is_archived: bool
    completed_turns: int | None
    prs: list[PRRef]
    parent_session_id: str | None
    spawned_task_id: str | None
    scheduled_task_id: str | None
    kept_dirty_worktree: bool
    transcript_unavailable: bool


@dataclass(frozen=True)
class Groups:
    names: dict[str, str]  # group id -> display name
    assignments: dict[str, str]  # session id -> group id


@dataclass(frozen=True)
class WorktreeLease:
    path: str
    branch: str | None
    source_branch: str | None
    base_repo: str | None


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _prs(raw: dict[str, Any]) -> list[PRRef]:
    out: list[PRRef] = []
    seen: set[str] = set()
    items = raw.get("prs")
    if "prs" in raw and items is not None and not isinstance(items, list):
        raise ValueError("prs must be a list")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            number, repo = _int(item.get("prNumber")), _str(item.get("repo"))
            if number is None or repo is None or f"{repo}#{number}" in seen:
                continue
            seen.add(f"{repo}#{number}")
            out.append(PRRef(number=number, repo=repo, url=_str(item.get("url")), legacy_state=None))
    number, repo = _int(raw.get("prNumber")), _str(raw.get("prRepository"))
    if number is not None and repo is not None and f"{repo}#{number}" not in seen:
        out.append(PRRef(number=number, repo=repo, url=_str(raw.get("prUrl")), legacy_state=_str(raw.get("prState"))))
    return out


def _record(raw: dict[str, Any], fallback_id: str) -> DesktopRecord:
    spawned = raw.get("spawnedFrom") if isinstance(raw.get("spawnedFrom"), dict) else {}
    cwd = _str(raw.get("cwd")) or ""
    return DesktopRecord(
        session_id=_str(raw.get("sessionId")) or fallback_id,
        cli_session_id=_str(raw.get("cliSessionId")),
        title=_str(raw.get("title")),
        title_source=_str(raw.get("titleSource")),
        cwd=cwd,
        origin_cwd=_str(raw.get("originCwd")) or cwd,
        worktree_path=_str(raw.get("worktreePath")),
        worktree_name=_str(raw.get("worktreeName")),
        branch=_str(raw.get("branch")),
        source_branch=_str(raw.get("sourceBranch")),
        created_at_ms=_int(raw.get("createdAt")),
        last_activity_at_ms=_int(raw.get("lastActivityAt")),
        model=_str(raw.get("model")),
        effort=_str(raw.get("effort")),
        is_archived=bool(raw.get("isArchived", False)),
        completed_turns=_int(raw.get("completedTurns")),
        prs=_prs(raw),
        parent_session_id=_str(spawned.get("sessionId")),
        spawned_task_id=_str(spawned.get("taskId")),
        scheduled_task_id=_str(raw.get("scheduledTaskId")),
        kept_dirty_worktree=bool(raw.get("keptDirtyWorktree", False)),
        transcript_unavailable=bool(raw.get("transcriptUnavailable", False)),
    )


def load_desktop_records(support_dir: Path) -> tuple[list[DesktopRecord], list[SourceError]]:
    root = support_dir / "claude-code-sessions"
    records: list[DesktopRecord] = []
    errors: list[SourceError] = []
    if not root.is_dir():
        return records, errors
    for path in sorted(root.glob("*/*/local_*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                errors.append(SourceError(source="desktop", message=f"{path.name}: not a JSON object"))
                continue
            records.append(_record(raw, fallback_id=path.stem))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            AttributeError,
            ValueError,
            KeyError,
        ) as e:
            errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            continue
    return records, errors


def _read_json(path: Path, source: str, errors: list[SourceError]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        errors.append(SourceError(source=source, message=f"{path.name}: {e}"))
        return None
    if not isinstance(raw, dict):
        errors.append(SourceError(source=source, message=f"{path.name}: not a JSON object"))
        return None
    return raw


def load_groups(support_dir: Path) -> tuple[Groups, list[SourceError]]:
    errors: list[SourceError] = []
    raw = _read_json(support_dir / "claude_desktop_config.json", "desktop-config", errors)
    names: dict[str, str] = {}
    assignments: dict[str, str] = {}
    prefs = raw.get("preferences") if raw else None
    epitaxy = prefs.get("epitaxyPrefs") if isinstance(prefs, dict) else None
    scopes = epitaxy.get("dframe-group-scopes") if isinstance(epitaxy, dict) else None
    if isinstance(scopes, dict):
        for scope_key, scope in scopes.items():
            if not isinstance(scope, dict):
                continue
            groups = scope.get("groups")
            if "groups" in scope and not isinstance(groups, list):
                errors.append(SourceError(source="desktop-config", message=f"{scope_key}: groups is not a list"))
            elif isinstance(groups, list):
                for g in groups:
                    if isinstance(g, dict) and _str(g.get("id")) and _str(g.get("name")):
                        names[g["id"]] = g["name"]
            assignments_val = scope.get("assignments")
            if "assignments" in scope and not isinstance(assignments_val, dict):
                errors.append(
                    SourceError(
                        source="desktop-config",
                        message=f"{scope_key}: assignments is not an object",
                    )
                )
            elif isinstance(assignments_val, dict):
                for key, gid in assignments_val.items():
                    if isinstance(key, str) and isinstance(gid, str):
                        assignments[key.removeprefix("code:")] = gid
    return Groups(names=names, assignments=assignments), errors


def load_worktree_leases(support_dir: Path) -> tuple[dict[str, WorktreeLease], list[SourceError]]:
    errors: list[SourceError] = []
    raw = _read_json(support_dir / "git-worktrees.json", "desktop-worktrees", errors)
    leases: dict[str, WorktreeLease] = {}
    worktrees = (raw or {}).get("worktrees")
    if raw is not None and "worktrees" in raw and not isinstance(worktrees, dict):
        errors.append(SourceError(source="desktop-worktrees", message="worktrees is not an object"))
    elif isinstance(worktrees, dict):
        for entry in worktrees.values():
            if not isinstance(entry, dict):
                continue
            holder, path = _str(entry.get("leasedBy")), _str(entry.get("path"))
            if holder is None or path is None:
                continue
            leases[holder] = WorktreeLease(
                path=path,
                branch=_str(entry.get("branch")),
                source_branch=_str(entry.get("sourceBranch")),
                base_repo=_str(entry.get("baseRepo")),
            )
    return leases, errors


__all__ = [
    "DesktopRecord",
    "Groups",
    "PRRef",
    "WorktreeLease",
    "default_support_dir",
    "load_desktop_records",
    "load_groups",
    "load_worktree_leases",
]

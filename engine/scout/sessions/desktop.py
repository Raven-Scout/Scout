"""Read-only loaders for the Claude desktop app's on-disk session store (spec §1 table, §4.1).

Layout (macOS)::

    ~/Library/Application Support/Claude/
      claude-code-sessions/<org>/<user>/local_<uuid>.json   one record per session
      claude-code-sessions/<org>/<user>/deleted_<uuid>       tombstone (skipped)
      claude_desktop_config.json                            sidebar groups + assignments
      git-worktrees.json                                    worktree leases

Nothing here writes to the desktop store. The one file written is Scout's own
desktop cache (``.scout-cache/sessions-desktop.cache.json``), which holds only the
``DesktopRecord`` fields. Every loader returns ``(data, errors)``; a missing
directory or file is *not* an error, a malformed file is.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from scout.sessions._atomic import atomic_write_text
from scout.sessions.model import SourceError
from scout.sessions.stats import BuildStats

DESKTOP_CACHE_FILENAME = "sessions-desktop.cache.json"
_DESKTOP_CACHE_VERSION = 1
_DECODE_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError, ValueError, KeyError)


def default_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


@dataclass(frozen=True)
class PRRef:
    number: int
    repo: str
    url: str | None
    legacy_state: str | None  # only the pre-`prs[]` schema carried `prState`

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


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
    raw_spawned = raw.get("spawnedFrom")
    spawned = raw_spawned if isinstance(raw_spawned, dict) else {}
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


@dataclass(frozen=True)
class CachedRecord:
    """One desktop-cache entry (1b spec §3.1): the record as last decoded, keyed by file identity."""

    size: int
    mtime_ns: int
    record: DesktopRecord
    failed: tuple[int, int] | None = None  # (size, mtime_ns) of a version that would not decode


def _decode_record(path: Path) -> DesktopRecord:
    """Read and extract one record. Raises one of ``_DECODE_ERRORS`` when it cannot."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    return _record(raw, fallback_id=path.stem)


def load_desktop_records(
    support_dir: Path,
    *,
    cache: dict[str, CachedRecord] | None = None,
    stats: BuildStats | None = None,
) -> tuple[list[DesktopRecord], list[SourceError]]:
    """Every ``local_*.json`` record, decoding only files whose (size, mtime_ns) changed (1b spec §3.1).

    *cache* is updated in place: changed files are decoded again and files that are gone are
    dropped. A file that fails to decode is served from its last good entry. That happens
    silently the first time, because the desktop app rewrites records mid-turn. If the same
    version is still unreadable on a later build, the build also reports a ``SourceError``.
    """
    if cache is None:
        cache = {}
    if stats is None:
        stats = BuildStats()
    root = support_dir / "claude-code-sessions"
    records: list[DesktopRecord] = []
    errors: list[SourceError] = []
    if not root.is_dir():
        cache.clear()
        return records, errors
    seen: set[str] = set()
    for path in sorted(root.glob("*/*/local_*.json")):
        key = str(path)
        try:
            st = path.stat()
        except OSError as e:  # gone between the glob and the stat
            errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            continue
        ident = (st.st_size, st.st_mtime_ns)
        prior = cache.get(key)
        if prior is not None and (prior.size, prior.mtime_ns) == ident:
            seen.add(key)
            records.append(prior.record)
            continue
        try:
            rec = _decode_record(path)
        except _DECODE_ERRORS as e:
            if prior is None:
                errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
                continue
            if prior.failed == ident:  # the same unreadable version as last build: say so
                errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            cache[key] = replace(prior, failed=ident)
            seen.add(key)
            records.append(prior.record)
            stats.desktop_served_last_good += 1
            continue
        stats.desktop_decoded += 1
        cache[key] = CachedRecord(size=st.st_size, mtime_ns=st.st_mtime_ns, record=rec)
        seen.add(key)
        records.append(rec)
    for key in [k for k in cache if k not in seen]:
        del cache[key]
    return records, errors


# ----- desktop cache file ---------------------------------------------------------------

_REQUIRED_STR = ("session_id", "cwd", "origin_cwd")
_OPTIONAL_STR = (
    "cli_session_id",
    "title",
    "title_source",
    "worktree_path",
    "worktree_name",
    "branch",
    "source_branch",
    "model",
    "effort",
    "parent_session_id",
    "spawned_task_id",
    "scheduled_task_id",
)
_OPTIONAL_INT = ("created_at_ms", "last_activity_at_ms", "completed_turns")
_BOOL = ("is_archived", "kept_dirty_worktree", "transcript_unavailable")
_RECORD_FIELDS = (*_REQUIRED_STR, *_OPTIONAL_STR, *_OPTIONAL_INT, *_BOOL)  # every DesktopRecord field but prs


def _cached_pr(v: Any) -> PRRef | None:
    if not isinstance(v, dict) or not {"number", "repo", "url", "legacy_state"} <= v.keys():
        return None
    number, repo, url, legacy = v["number"], v["repo"], v["url"], v["legacy_state"]
    if _int(number) is None or not isinstance(repo, str):
        return None
    if not (url is None or isinstance(url, str)) or not (legacy is None or isinstance(legacy, str)):
        return None
    return PRRef(number=number, repo=repo, url=url, legacy_state=legacy)


def _cached_desktop_record(v: Any) -> DesktopRecord | None:
    """Rebuild a cached record, or None when a field is missing or fails the checks fresh records pass."""
    if not isinstance(v, dict) or not {*_RECORD_FIELDS, "prs"} <= v.keys():
        return None
    ok = (
        all(isinstance(v[k], str) for k in _REQUIRED_STR)
        and all(v[k] is None or isinstance(v[k], str) for k in _OPTIONAL_STR)
        and all(v[k] is None or _int(v[k]) is not None for k in _OPTIONAL_INT)
        and all(isinstance(v[k], bool) for k in _BOOL)
        and isinstance(v["prs"], list)
    )
    if not ok:
        return None
    prs = [_cached_pr(p) for p in v["prs"]]
    if any(p is None for p in prs):
        return None
    return DesktopRecord(**{k: v[k] for k in _RECORD_FIELDS}, prs=[p for p in prs if p is not None])


def _cached_entry(v: Any) -> CachedRecord | None:
    if not isinstance(v, dict) or _int(v.get("size")) is None or _int(v.get("mtime_ns")) is None:
        return None
    record = _cached_desktop_record(v.get("record"))
    if record is None:
        return None
    failed = v.get("failed")
    if failed is None:
        return CachedRecord(size=v["size"], mtime_ns=v["mtime_ns"], record=record)
    if isinstance(failed, list) and len(failed) == 2 and all(_int(x) is not None for x in failed):
        return CachedRecord(size=v["size"], mtime_ns=v["mtime_ns"], record=record, failed=(failed[0], failed[1]))
    return None


def load_desktop_cache(cache_path: Path) -> dict[str, CachedRecord]:
    """Load the desktop cache. A missing, corrupt or other-version file is empty; bad entries are skipped."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or _int(raw.get("version")) != _DESKTOP_CACHE_VERSION:
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, CachedRecord] = {}
    for key, value in entries.items():
        entry = _cached_entry(value)
        if entry is not None:
            out[key] = entry
    return out


def write_desktop_cache(cache_path: Path, cache: dict[str, CachedRecord]) -> bool:
    """Atomically replace the desktop cache. Best-effort: returns False instead of raising.

    It stores only the ``DesktopRecord`` fields, never a raw record: a raw record's MCP
    configuration can hold credentials.
    """
    payload = {
        "version": _DESKTOP_CACHE_VERSION,
        "entries": {
            key: {
                "size": e.size,
                "mtime_ns": e.mtime_ns,
                "record": asdict(e.record),
                "failed": list(e.failed) if e.failed is not None else None,
            }
            for key, e in cache.items()
        },
    }
    try:
        atomic_write_text(cache_path, json.dumps(payload))
    except OSError:
        return False
    return True


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
            # An explicit JSON null is the same as the key being absent; only a wrong non-null shape reports.
            groups = scope.get("groups")
            if groups is not None and not isinstance(groups, list):
                errors.append(SourceError(source="desktop-config", message=f"{scope_key}: groups is not a list"))
            elif isinstance(groups, list):
                for g in groups:
                    if isinstance(g, dict) and _str(g.get("id")) and _str(g.get("name")):
                        names[g["id"]] = g["name"]
            assignments_val = scope.get("assignments")
            if assignments_val is not None and not isinstance(assignments_val, dict):
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
    if worktrees is not None and not isinstance(worktrees, dict):  # null ≡ absent
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
    "DESKTOP_CACHE_FILENAME",
    "CachedRecord",
    "DesktopRecord",
    "Groups",
    "PRRef",
    "WorktreeLease",
    "default_support_dir",
    "load_desktop_cache",
    "load_desktop_records",
    "load_groups",
    "load_worktree_leases",
    "write_desktop_cache",
]

"""Pre-fetch Claude Code session summaries for the next Scout session.

Port of ``~/Scout/scripts/cc-session-cache.sh`` (#74 + #75). The bash version
walked every JSONL file under ``~/.claude/projects/*`` modified in the
lookback window and, for each one, paid:

- a separate ``python3`` cold start to parse the first 50 lines
- five+ piped subprocesses (``grep | sed | sed | grep -Ev | sort -u | head``)
  to extract ``file_path`` mentions

For an active Claude Code user with dozens of recent sessions that becomes
dozens of Python startups + hundreds of subprocess forks per Scout
session-start — the dominant cost of the pre-session phase.

This module does the same work in one Python process and adds an
mtime-keyed cache at ``.scout-cache/cc-sessions.cache.json`` so unchanged
JSONLs from the previous run are reused without re-parsing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout import config as scout_config
from scout import paths
from scout.sessions.transcript import extract_files_touched, extract_first_message  # moved (Agent Sessions plan 1)

DEFAULT_HOURS_LOOKBACK = 24
# Kept as a named export for back-compat; the runtime default is the
# CONFIGURED zone (tz_name=None -> scout.config.resolve_timezone, #207).
DEFAULT_TZ = scout_config.DEFAULT_TIMEZONE
CACHE_FILENAME = "cc-sessions.cache.json"
OUTPUT_FILENAME = "cc-sessions.md"

# Default instance suffixes that mean "Scout's own sessions" — matches the
# bash case glob ``*-Scout|*-scout|*-{INSTANCE_NAME}``. The CLI lets the
# caller add more via --exclude-suffix.
_DEFAULT_SCOUT_DIR_SUFFIXES = ("-Scout", "-scout")


@dataclass(frozen=True)
class SessionEntry:
    """One JSONL session's metadata. Serialised verbatim to the cache file."""

    jsonl_path: str
    project_path: str
    session_id: str
    mtime_ns: int
    size_bytes: int
    first_msg: str
    files_touched: list[str]


# ----- discovery & filtering -----------------------------------------------


def _excluded_suffixes(extra: Iterable[str] = ()) -> tuple[str, ...]:
    return tuple({*_DEFAULT_SCOUT_DIR_SUFFIXES, *extra})


def _is_scout_dir(dirname: str, suffixes: tuple[str, ...]) -> bool:
    return any(dirname.endswith(suffix) for suffix in suffixes)


def _project_path_from_dirname(dirname: str) -> str:
    """Decode Claude Code's project-dir naming back into a filesystem path.

    Claude Code stores per-project sessions under
    ``~/.claude/projects/-Users-foo-bar/<session>.jsonl``. The leading dash
    is the root ``/`` and subsequent dashes are path separators.
    """
    if not dirname.startswith("-"):
        return dirname
    return "/" + dirname[1:].replace("-", "/")


def iter_session_jsonls(
    cc_projects: Path,
    *,
    cutoff_ts: float,
    exclude_suffixes: tuple[str, ...],
) -> Iterable[tuple[Path, os.stat_result]]:
    """Yield ``(path, stat)`` for every JSONL modified since *cutoff_ts*.

    Skips entire project directories whose name ends in any of the
    *exclude_suffixes* (Scout's own sessions). Errors from individual
    ``stat`` calls are swallowed silently — matches the bash original.
    """
    if not cc_projects.is_dir():
        return
    cutoff_ns = int(cutoff_ts * 1_000_000_000)
    for projdir in sorted(cc_projects.iterdir()):
        if not projdir.is_dir():
            continue
        if _is_scout_dir(projdir.name, exclude_suffixes):
            continue
        for jsonl in projdir.glob("*.jsonl"):
            try:
                st = jsonl.stat()
            except OSError:
                continue
            if st.st_mtime_ns < cutoff_ns:
                continue
            yield jsonl, st


# ----- per-file extraction (slow path) -------------------------------------


def build_session_entry(jsonl_path: Path, st: os.stat_result) -> SessionEntry:
    """Compose a :class:`SessionEntry` from a JSONL and its stat result."""
    return SessionEntry(
        jsonl_path=str(jsonl_path),
        project_path=_project_path_from_dirname(jsonl_path.parent.name),
        session_id=jsonl_path.stem,
        mtime_ns=st.st_mtime_ns,
        size_bytes=st.st_size,
        first_msg=extract_first_message(jsonl_path),
        files_touched=extract_files_touched(jsonl_path),
    )


# ----- cache ---------------------------------------------------------------


def _load_cache(cache_path: Path) -> dict[str, SessionEntry]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries: dict[str, SessionEntry] = {}
    for path, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            entries[path] = SessionEntry(
                jsonl_path=str(payload["jsonl_path"]),
                project_path=str(payload["project_path"]),
                session_id=str(payload["session_id"]),
                mtime_ns=int(payload["mtime_ns"]),
                size_bytes=int(payload["size_bytes"]),
                first_msg=str(payload["first_msg"]),
                files_touched=list(payload.get("files_touched") or []),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def _write_cache(cache_path: Path, entries: dict[str, SessionEntry]) -> None:
    """Atomically replace the cache file. Best-effort — never raises."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    payload = {path: asdict(entry) for path, entry in entries.items()}
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


# ----- markdown rendering --------------------------------------------------


def render_markdown(
    entries: list[SessionEntry],
    *,
    hours: int,
    instance_name: str,
    now_local_str: str,
    tz: ZoneInfo,
) -> str:
    """Build the cc-sessions.md content the SCOUT skill consumes."""
    sessions_label = f"non-{instance_name} sessions only"
    out: list[str] = [
        f"# Claude Code Sessions — Last {hours}h",
        f"**Generated:** {now_local_str}",
        f"**Source:** ~/.claude/projects/ ({sessions_label})",
        "",
    ]
    for idx, entry in enumerate(entries, start=1):
        local_dt = datetime.fromtimestamp(entry.mtime_ns / 1_000_000_000, tz=tz)
        session_time = local_dt.strftime("%Y-%m-%d %H:%M %Z")
        size_kb = entry.size_bytes // 1024
        files_block = "\n".join(f"- {p}" for p in entry.files_touched) if entry.files_touched else "- (none detected)"
        out.extend(
            [
                "---",
                "",
                f"## Session {idx}: {entry.project_path}",
                f"**Last active:** {session_time} | **Size:** {size_kb} KB | **ID:** `{entry.session_id}`",
                "",
                "**First message/context:**",
                f"> {entry.first_msg}",
                "",
                "**Files touched:**",
                files_block,
                "",
            ]
        )
    if not entries:
        out.append(f"*No non-{instance_name} CC sessions found in the last {hours} hours.*")
    out.append("")
    out.append(f"**Total:** {len(entries)} session(s) found.")
    return "\n".join(out) + "\n"


# ----- driver --------------------------------------------------------------


def run(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str | None = None,
    extra_exclude_suffixes: Iterable[str] = (),
    data_dir: Path | None = None,
    cc_projects_dir: Path | None = None,
    now: datetime | None = None,
) -> tuple[Path, int]:
    """Refresh the cc-sessions cache and rerender the markdown summary.

    Returns ``(output_path, session_count)``. The function is total: even
    when the projects dir doesn't exist it still writes a (possibly empty)
    summary so downstream consumers can rely on the file being present.
    """
    target = data_dir or paths.data_dir()
    cc_projects = cc_projects_dir or (Path.home() / ".claude" / "projects")
    cache_dir = paths.cache_dir(target)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Explicit tz_name wins (invalid names fall back inside the resolver);
    # None means "the vault's configured zone" (#207).
    tz = scout_config.timezone_or_default(tz_name) if tz_name else scout_config.resolve_timezone(target)
    now_dt = now or datetime.now(tz=tz)
    cutoff_ts = (now_dt - timedelta(hours=hours)).timestamp()

    instance_suffix = f"-{instance_name}"
    instance_suffix_lower = f"-{instance_name.lower()}"
    exclude = _excluded_suffixes((instance_suffix, instance_suffix_lower, *extra_exclude_suffixes))

    cache_path = cache_dir / CACHE_FILENAME
    cached = _load_cache(cache_path)
    next_cache: dict[str, SessionEntry] = {}
    entries: list[SessionEntry] = []

    for jsonl, st in iter_session_jsonls(cc_projects, cutoff_ts=cutoff_ts, exclude_suffixes=exclude):
        key = str(jsonl)
        prior = cached.get(key)
        if prior is not None and prior.mtime_ns == st.st_mtime_ns:
            # Unchanged since last run — reuse the cached extraction.
            entry = prior
        else:
            entry = build_session_entry(jsonl, st)
        next_cache[key] = entry
        entries.append(entry)

    # Order matches bash: by JSONL mtime newest-first is more useful than the
    # bash's discovery order (which followed inode order). Sort here once.
    entries.sort(key=lambda e: e.mtime_ns, reverse=True)

    _write_cache(cache_path, next_cache)

    output_path = cache_dir / OUTPUT_FILENAME
    now_local_str = now_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    output_path.write_text(
        render_markdown(
            entries,
            hours=hours,
            instance_name=instance_name,
            now_local_str=now_local_str,
            tz=tz,
        ),
        encoding="utf-8",
    )
    return output_path, len(entries)


def main(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str | None = None,
) -> int:
    """CLI entry — never raises. Prints the summary path so runner logs show
    where the cache landed."""
    try:
        output_path, count = run(hours=hours, instance_name=instance_name, tz_name=tz_name)
    except Exception:
        return 0  # match bash: never break the pre-session phase
    print(f"CC session cache written to {output_path} ({count} sessions, {hours}h lookback)")
    return 0


__all__ = [
    "CACHE_FILENAME",
    "DEFAULT_HOURS_LOOKBACK",
    "DEFAULT_TZ",
    "OUTPUT_FILENAME",
    "SessionEntry",
    "build_session_entry",
    "extract_files_touched",
    "extract_first_message",
    "iter_session_jsonls",
    "main",
    "render_markdown",
    "run",
]


# UTC re-export so tests that import this module can grab it for assertions
# without re-importing zoneinfo themselves.
UTC = UTC

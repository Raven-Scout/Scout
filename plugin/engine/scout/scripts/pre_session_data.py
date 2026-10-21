"""Gather routine pre-session data so the skill doesn't have to.

Port of ``~/Scout/scripts/pre-session-data.sh`` (#74 + #76). The bash version:

- ran ``head -5 | grep | head -1 | sed | sed`` per KB markdown file
  (4–5 subprocess forks each)
- shelled out to ``python3 -c`` twice just to JSON-encode stdin
- spawned a third Python (``ontology/parser.py``) for the tasks query

This module does the same work in one Python process. The KB date
extraction is also cached by ``(path, mtime_ns)`` at
``.scout-cache/kb-dates.cache.json`` so files unchanged since the last
run are reused without reopening (closes #76).

**Privacy labels (#453, spec §6.3).** The cache is read as the reader the
environment names (``resolve_reader(None, os.environ)``: ``autonomous`` in a
scheduled run). Every KB path and every task in it carries its
``visibility`` from the engine classifier, the same label ``scoutctl kb``
gives. Tasks come from the engine's labelled ``KnowledgeGraph`` in process,
not the vault ``parser.py`` shim, so nothing depends on PATH. A reader that
sees nothing (``agent:*``) gets empty KB fields and no git log.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from scout import config as scout_config
from scout import paths
from scout.kb.privacy import FULL_READERS
from scout.kb.read import visibility_map
from scout.kb.reader import resolve_reader

OUTPUT_FILENAME = "session-context.json"
KB_DATES_CACHE_FILENAME = "kb-dates.cache.json"
# Kept as a named export for back-compat; the runtime default is the
# CONFIGURED zone (tz_name=None -> scout.config.resolve_timezone, #207).
DEFAULT_TZ = scout_config.DEFAULT_TIMEZONE
# A `scoutctl kb delta --since` duration (<n>h, <n>d or <n>w).
DEFAULT_GIT_LOG_LOOKBACK = "12h"
PR_LIST_LIMIT = 10
# `--review-requested @me` expands team membership, so the list runs longer than
# the authored one, and `action-items check-review-requests` only sees what is
# fetched here (#404).
REVIEW_REQUEST_LIMIT = 50
KB_HEAD_SCAN_LINES = 5

# Match the bash find filter: skip */ontology/* and *archive*.
_SKIP_PATH_FRAGMENTS = ("/ontology/", "archive")

# Mirrors the *intent* of the bash's ``grep -i 'last updated\|last verified'``
# followed by ``sed 's/.*: *//'``. The bash sed is greedy on the LAST colon,
# which silently truncates dates that contain a colon (e.g. "2:30 PM"). Here
# we anchor on the "last updated/verified" label, eat any markdown bold
# markers, then take everything after the first colon — preserving dates with
# embedded times instead of corrupting them.
_LAST_UPDATED_RE = re.compile(
    r"(?:last\s+updated|last\s+verified)[\s*]*:[\s*]*(.*?)\s*$",
    re.IGNORECASE,
)


@dataclass
class SessionContext:
    generated_at: str
    session_type: str
    # The vault's commits since DEFAULT_GIT_LOG_LOOKBACK, newest first, labelled (see get_git_recent).
    git_recent: list[dict[str, Any]] = field(default_factory=list)
    # {path: {"last_updated": ..., "visibility": "normal" | "private"}}
    kb_file_dates: dict[str, dict[str, str]] = field(default_factory=dict)
    pr_authored: list[dict] = field(default_factory=list)
    pr_review_requested: list[dict] = field(default_factory=list)
    # One row per task entity: its frontmatter fields, "path" and "visibility".
    personal_tasks: list[dict[str, Any]] = field(default_factory=list)


# ----- KB date extraction with mtime cache --------------------------------


@dataclass(frozen=True)
class _KbEntry:
    mtime_ns: int
    last_updated: str  # empty string means "no line found" (still cached!)


def _kb_path_excluded(rel_posix: str) -> bool:
    return any(frag in rel_posix for frag in _SKIP_PATH_FRAGMENTS)


def extract_last_updated(path: Path) -> str:
    """Read the first ``KB_HEAD_SCAN_LINES`` lines and return the cleaned date string.

    Replaces ``head -5 | grep | head -1 | sed | sed`` — one file open instead
    of a 4-subprocess pipeline.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= KB_HEAD_SCAN_LINES:
                    break
                m = _LAST_UPDATED_RE.search(raw)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return ""


def _load_kb_dates_cache(cache_path: Path) -> dict[str, _KbEntry]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, _KbEntry] = {}
    for rel, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            out[rel] = _KbEntry(
                mtime_ns=int(payload["mtime_ns"]),
                last_updated=str(payload.get("last_updated", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _write_kb_dates_cache(cache_path: Path, entries: dict[str, _KbEntry]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    payload = {rel: asdict(entry) for rel, entry in entries.items()}
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def gather_kb_file_dates(
    kb_root: Path,
    *,
    scout_dir: Path,
    cache_path: Path,
) -> dict[str, str]:
    """Return ``{relpath: last_updated_string}`` for KB files that have a date line.

    Uses the cache for unchanged files (mtime match) and only opens the files
    whose mtime moved. Output dict only includes files with a non-empty
    ``last_updated`` value, matching the bash behavior.
    """
    if not kb_root.is_dir():
        return {}

    cached = _load_kb_dates_cache(cache_path)
    next_cache: dict[str, _KbEntry] = {}
    out: dict[str, str] = {}

    for md_file in sorted(kb_root.rglob("*.md")):
        try:
            rel = md_file.relative_to(scout_dir).as_posix()
        except ValueError:
            continue
        if _kb_path_excluded(rel):
            continue
        try:
            mtime_ns = md_file.stat().st_mtime_ns
        except OSError:
            continue
        prior = cached.get(rel)
        if prior is not None and prior.mtime_ns == mtime_ns:
            entry = prior
        else:
            entry = _KbEntry(mtime_ns=mtime_ns, last_updated=extract_last_updated(md_file))
        next_cache[rel] = entry
        if entry.last_updated:
            out[rel] = entry.last_updated

    _write_kb_dates_cache(cache_path, next_cache)
    return out


# ----- side-effect helpers ------------------------------------------------


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 10) -> str:
    """Run *cmd* and return stdout. Returns "" on any failure — never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def get_git_recent(
    scout_dir: Path, since: str = DEFAULT_GIT_LOG_LOOKBACK, reader: str | None = None
) -> list[dict[str, Any]]:
    """The vault's commits since ``since``, each labelled, from ``scout.kb.delta``.

    Was ``git log --oneline``, whose subjects can name private notes with no
    label. Each row is ``{"sha", "subject", "visibility", "paths": [{"path",
    "visibility"}], "line"}``. A commit is ``private`` when any path it touched
    is, or when its paths are unknown (a shallow-clone boundary). ``line`` is
    the text form, prefixed ``[private] `` for a private commit. Empty for a
    reader that sees nothing, without a ``.git`` dir, and on any failure.
    """
    from scout.kb.delta import delta

    who = reader if reader is not None else resolve_reader(None, os.environ)
    if who not in FULL_READERS or not (scout_dir / ".git").exists():
        return []
    try:
        commits = delta(scout_dir, who, since, with_diff=False)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for c in commits:
        paths = [{"path": p, "visibility": label.visibility} for p, label in c.paths]
        private = c.shallow_boundary or any(p["visibility"] != "normal" for p in paths)
        visibility = "private" if private else "normal"
        line = f"{'[private] ' if private else ''}{c.sha[:7]} {c.subject}"
        rows.append({"sha": c.sha, "subject": c.subject, "visibility": visibility, "paths": paths, "line": line})
    return rows


def _gh_json(args: list[str], timeout: int = 10) -> list[dict]:
    """Invoke ``gh`` and parse the JSON array on stdout; on any failure return []."""
    raw = _run(["gh", *args], timeout=timeout)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def get_pr_authored() -> list[dict]:
    return _gh_json(
        [
            "pr",
            "list",
            "--author",
            "@me",
            "--state",
            "open",
            "--json",
            "number,title,repository,reviewDecision,updatedAt",
            "--limit",
            str(PR_LIST_LIMIT),
        ]
    )


def get_pr_review_requested() -> list[dict]:
    return _gh_json(
        [
            "search",
            "prs",
            "--review-requested",
            "@me",
            "--state",
            "open",
            "--json",
            "number,title,url,repository",
            "--limit",
            str(REVIEW_REQUEST_LIMIT),
        ]
    )


def get_personal_tasks(scout_dir: Path, reader: str | None = None) -> list[dict[str, Any]]:
    """Every task entity, labelled, from the engine's ``KnowledgeGraph``.

    Was the vault ``parser.py query --type task`` text, which needed a
    ``python3`` (and now ``scoutctl``) on PATH. Each row is the entity's
    frontmatter plus ``path`` and ``visibility``. Empty on any failure, and
    for a reader that sees nothing.
    """
    from scout.kb.read import load_graph

    who = reader if reader is not None else resolve_reader(None, os.environ)
    if who not in FULL_READERS or not (scout_dir / "knowledge-base").is_dir():
        return []
    try:
        graph = load_graph(scout_dir, who)
        rows = graph.query(type="task")
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for entity in rows:
        label = entity.get("_label") or {}
        row = {k: v for k, v in entity.items() if not k.startswith("_")}
        row["path"] = label.get("path", "")
        row["visibility"] = "normal" if label.get("visibility") == "normal" else "private"
        out.append(row)
    return sorted(out, key=lambda r: r["path"])


# ----- driver -------------------------------------------------------------


def gather(
    session_type: str,
    *,
    scout_dir: Path | None = None,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> SessionContext:
    """Collect all routine pre-session data into a :class:`SessionContext`.

    Side-effect heavy: walks the KB, runs git, runs gh twice, loads the
    labelled knowledge graph. Each upstream call is wrapped to never raise.
    """
    target = scout_dir or paths.data_dir()
    # Explicit tz_name wins (invalid names fall back inside the resolver);
    # None means "the vault's configured zone" (#207).
    tz = scout_config.timezone_or_default(tz_name) if tz_name else scout_config.resolve_timezone(target)
    now_dt = now or datetime.now(tz=tz)
    generated_at = now_dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S")

    reader = resolve_reader(None, os.environ)
    full = reader in FULL_READERS
    kb_dates: dict[str, dict[str, str]] = {}
    if full:
        dates = gather_kb_file_dates(
            target / "knowledge-base",
            scout_dir=target,
            cache_path=paths.cache_dir(target) / KB_DATES_CACHE_FILENAME,
        )
        labels = visibility_map(target, reader, ["knowledge-base/**"], who="pre-session-data")
        kb_dates = {
            rel: {"last_updated": when, "visibility": labels.get(rel, "private")} for rel, when in dates.items()
        }

    return SessionContext(
        generated_at=generated_at,
        session_type=session_type,
        git_recent=get_git_recent(target, reader=reader),
        kb_file_dates=kb_dates,
        pr_authored=get_pr_authored(),
        pr_review_requested=get_pr_review_requested(),
        personal_tasks=get_personal_tasks(target, reader),
    )


def write_context(ctx: SessionContext, output_path: Path) -> None:
    """Atomically write the gathered context as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(asdict(ctx), f, indent=2, default=str)
        os.replace(tmp, output_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def run(session_type: str = "unknown", *, data_dir: Path | None = None) -> Path:
    """End-to-end: gather, write, return the output path."""
    target = data_dir or paths.data_dir()
    ctx = gather(session_type, scout_dir=target)
    output_path = paths.cache_dir(target) / OUTPUT_FILENAME
    write_context(ctx, output_path)
    return output_path


def main(session_type: str = "unknown") -> int:
    try:
        output_path = run(session_type)
    except Exception:
        return 0  # never block a session
    print(f"Pre-session data written to {output_path} ({session_type})")
    return 0


__all__ = [
    "DEFAULT_GIT_LOG_LOOKBACK",
    "DEFAULT_TZ",
    "KB_DATES_CACHE_FILENAME",
    "KB_HEAD_SCAN_LINES",
    "OUTPUT_FILENAME",
    "PR_LIST_LIMIT",
    "REVIEW_REQUEST_LIMIT",
    "SessionContext",
    "extract_last_updated",
    "gather",
    "gather_kb_file_dates",
    "get_git_recent",
    "get_personal_tasks",
    "get_pr_authored",
    "get_pr_review_requested",
    "main",
    "run",
    "write_context",
]


_ = Iterable  # forward-compat re-export

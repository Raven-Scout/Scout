"""PR review/CI state through the local ``gh`` CLI (spec §4.6).

Sequential, bounded, cached: at most ``cap`` fetches per run, each with a 10 s
timeout; entries younger than ``ttl`` and terminal states (MERGED/CLOSED) are
never refetched; a failure keeps the cached value flagged ``stale``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout.sessions.desktop import PRRef
from scout.sessions.model import TERMINAL_PR_STATES, PRInfo, SourceError, dt_to_iso, parse_iso

PR_CACHE_FILENAME = "sessions-pr.cache.json"
GH_FIELDS = "state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url"
GH_TIMEOUT_SECONDS = 10
MAX_CONSECUTIVE_FAILURES = 3  # after this many gh failures in a row, stop calling gh for the rest of the run

Runner = Callable[[list[str]], str | None]

_FAILING = {"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE"}
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def gh_available() -> bool:
    return shutil.which("gh") is not None


def default_runner(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def summarize_checks(rollup: list[dict[str, Any]] | None) -> str:
    if not rollup:
        return "none"
    saw_pending = False
    for c in rollup:
        if not isinstance(c, dict):
            continue
        conclusion = str(c.get("conclusion") or c.get("state") or "").upper()
        status = str(c.get("status") or "").upper()
        if conclusion in _FAILING:
            return "failing"
        if (status and status != "COMPLETED") or (not conclusion and not status):
            saw_pending = True
        elif conclusion and conclusion not in _PASSING and conclusion not in _FAILING:
            saw_pending = True  # e.g. ACTION_REQUIRED, STALE
    return "pending" if saw_pending else "passing"


def pr_info_from_payload(ref: PRRef, payload: dict[str, Any], fetched_at: str) -> PRInfo:
    decision = payload.get("reviewDecision")
    return PRInfo(
        number=ref.number,
        repo=ref.repo,
        url=payload.get("url") or ref.url,
        state=str(payload.get("state") or "unknown").upper(),
        is_draft=bool(payload.get("isDraft", False)),
        review_decision=str(decision) if isinstance(decision, str) else "",
        review_requested=bool(payload.get("reviewRequests")) or decision == "REVIEW_REQUIRED",
        checks=summarize_checks(payload.get("statusCheckRollup")),
        merge_state=str(payload.get("mergeStateStatus") or "unknown").upper(),
        fetched_at=fetched_at,
        stale=False,
        updated_at=payload.get("updatedAt") if isinstance(payload.get("updatedAt"), str) else None,
    )


def unknown_pr_info(ref: PRRef, *, stale: bool = False) -> PRInfo:
    return PRInfo(
        number=ref.number,
        repo=ref.repo,
        url=ref.url,
        state="unknown",
        is_draft=False,
        review_decision="unknown",
        review_requested=False,
        checks="unknown",
        merge_state="unknown",
        fetched_at=None,
        stale=stale,
        updated_at=None,
    )


def terminal_pr_info(ref: PRRef, state: str) -> PRInfo:
    return PRInfo(
        number=ref.number,
        repo=ref.repo,
        url=ref.url,
        state=state.upper(),
        is_draft=False,
        review_decision="",
        review_requested=False,
        checks="none",
        merge_state="unknown",
        fetched_at=None,
        stale=False,
        updated_at=None,
    )


def load_pr_cache(cache_path: Path) -> dict[str, PRInfo]:
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, PRInfo] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            out[key] = PRInfo(**{k: payload[k] for k in PRInfo.__dataclass_fields__})
        except (KeyError, TypeError):
            continue
    return out


def write_pr_cache(cache_path: Path, cache: dict[str, PRInfo]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({k: asdict(v) for k, v in cache.items()}), encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _fetched_sort_key(info: PRInfo | None) -> float:
    if info is None or info.fetched_at is None:
        return float("-inf")
    dt = parse_iso(info.fetched_at)
    return dt.timestamp() if dt else float("-inf")


def refresh_pr_states(
    refs: list[PRRef],
    *,
    cache: dict[str, PRInfo],
    now: datetime,
    ttl: timedelta,
    cap: int,
    runner: Runner,
) -> tuple[dict[str, PRInfo], list[SourceError], int]:
    """Resolve every ref to a PRInfo, fetching from gh only where the cache is cold.

    Mutates ``cache`` with fresh results so callers can persist it.
    """
    out: dict[str, PRInfo] = {}
    errors: list[SourceError] = []
    pending: list[PRRef] = []
    seen: set[str] = set()
    for ref in refs:
        key = f"{ref.repo}#{ref.number}"
        if key in seen:
            continue
        seen.add(key)
        cached = cache.get(key)
        if cached is not None and cached.state in TERMINAL_PR_STATES:
            out[key] = cached
            continue
        if cached is None and ref.legacy_state and ref.legacy_state.upper() in TERMINAL_PR_STATES:
            out[key] = cache[key] = terminal_pr_info(ref, ref.legacy_state)
            continue
        fetched_dt = parse_iso(cached.fetched_at) if cached else None
        if cached is not None and fetched_dt is not None and now - fetched_dt < ttl:
            out[key] = cached
            continue
        pending.append(ref)

    pending.sort(key=lambda r: _fetched_sort_key(cache.get(f"{r.repo}#{r.number}")))
    attempts = 0  # every gh call counts against the cap, success or not
    fetched = 0  # successful fetches (reported in source_counts)
    consecutive_failures = 0
    for ref in pending:
        key = f"{ref.repo}#{ref.number}"
        cached = cache.get(key)
        if attempts >= cap or consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            # Out of budget, or gh is clearly down (offline / not authed):
            # serve what we have without paying another 10 s timeout.
            out[key] = cached if cached is not None else unknown_pr_info(ref)
            continue
        attempts += 1
        raw = runner(["pr", "view", str(ref.number), "--repo", ref.repo, "--json", GH_FIELDS])
        payload: dict[str, Any] | None = None
        if raw is not None:
            try:
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            consecutive_failures += 1
            errors.append(SourceError(source="gh", message=f"pr view failed: {key}"))
            if cached is not None:
                out[key] = PRInfo(**{**asdict(cached), "stale": True})
            else:
                out[key] = unknown_pr_info(ref, stale=True)
            continue
        consecutive_failures = 0
        fetched += 1
        info = pr_info_from_payload(ref, payload, fetched_at=dt_to_iso(now))
        out[key] = cache[key] = info
    return out, errors, fetched


__all__ = [
    "GH_FIELDS",
    "PR_CACHE_FILENAME",
    "Runner",
    "default_runner",
    "gh_available",
    "load_pr_cache",
    "pr_info_from_payload",
    "refresh_pr_states",
    "summarize_checks",
    "terminal_pr_info",
    "unknown_pr_info",
    "write_pr_cache",
]

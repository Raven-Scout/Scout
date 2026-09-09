"""Pure derivations: project key, Scout-run flag, PR choice, state (spec §4.2, §4.3, §4.7)."""

from __future__ import annotations

import functools
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from scout.sessions.model import TERMINAL_PR_STATES, AgentSession, PRInfo, parse_iso

SCOUT_RUN_TITLE_RE = re.compile(r"^scout-[a-z-]+-\d{8}-\d{4}$")
_WORKTREE_RE = re.compile(r"^(.*?)/\.claude/worktrees/[^/]+(?:/.*)?$")


# ----- project ------------------------------------------------------------------


def strip_worktree(path: str) -> str:
    m = _WORKTREE_RE.match(path)
    return m.group(1) if m else path


@functools.lru_cache(maxsize=256)
def git_toplevel(path: str) -> str | None:
    """`git rev-parse --show-toplevel` for *path*, memoised per path; None when not a repo."""
    if not Path(path).is_dir():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return top or None


def resolve_project_key(origin_cwd: str, *, toplevel: Callable[[str], str | None]) -> str:
    top = toplevel(origin_cwd)
    if top:
        return top
    return strip_worktree(origin_cwd)


def is_scout_run(*, origin_cwd: str, title: str | None, scheduled_task_id: str | None, vault: Path) -> bool:
    try:
        in_vault = Path(origin_cwd).resolve() == vault.resolve()
    except OSError:
        in_vault = origin_cwd.rstrip("/") == str(vault).rstrip("/")
    if not in_vault:
        return False
    if scheduled_task_id and scheduled_task_id.startswith("scout-"):
        return True
    return bool(title and SCOUT_RUN_TITLE_RE.match(title))


# ----- PR choice -------------------------------------------------------------------


def _updated_key(pr: PRInfo) -> float:
    dt = parse_iso(pr.updated_at) or parse_iso(pr.fetched_at)
    return dt.timestamp() if dt else float("-inf")


def choose_pr(prs: list[PRInfo]) -> PRInfo | None:
    """The most recently updated OPEN PR; else the most recently updated of any; else None."""
    if not prs:
        return None
    open_prs = [p for p in prs if p.state == "OPEN"]
    pool = open_prs or prs
    return max(pool, key=_updated_key)


# ----- formatting ------------------------------------------------------------------


def fmt_ago(td: timedelta | None) -> str:
    if td is None:
        return "unknown"
    s = int(td.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def fmt_days(td: timedelta) -> str:
    return f"{int(td.total_seconds()) // 86400}d"


# ----- state -------------------------------------------------------------------------


def derive_state(
    session: AgentSession, *, now: datetime, stale_after: timedelta, running_window: timedelta
) -> tuple[str, list[str]]:
    """Spec §4.7, first match wins. ``reasons`` lists every matched signal of the winning rule."""
    last = parse_iso(session.last_activity_at)
    idle = (now - last) if last else None
    pr = session.pr

    if session.is_archived:  # 1
        return "done", ["archived"]

    if session.is_open and idle is not None and idle <= running_window:  # 2
        return "running", [f"active {fmt_ago(idle)}"]

    if pr is not None and pr.state in TERMINAL_PR_STATES:  # 3
        return "done", [f"PR #{pr.number} {pr.state.lower()}"]

    needs: list[str] = []  # 4
    live_pr = pr is not None and pr.state == "OPEN" and not pr.is_draft
    if live_pr:
        assert pr is not None
        if pr.review_decision == "CHANGES_REQUESTED":
            needs.append(f"changes requested on PR #{pr.number}")
        if pr.checks == "failing":
            needs.append("CI failing")
        if pr.merge_state == "DIRTY":
            needs.append("merge conflict")
        ready_review = pr.review_decision == "APPROVED" or (pr.review_decision == "" and not pr.review_requested)
        if pr.checks in ("passing", "none") and pr.merge_state == "CLEAN" and ready_review:
            needs.append(f"PR #{pr.number} ready to merge")
    if session.transcript is not None and session.transcript.last_turn.kind == "question":
        needs.append("ended on a question")
    if needs:
        return "needs_you", needs

    if live_pr:  # 5
        assert pr is not None
        waiting: list[str] = []
        if pr.review_requested or pr.review_decision == "REVIEW_REQUIRED":
            age = parse_iso(pr.updated_at)
            suffix = f" {fmt_days(now - age)}" if age else ""
            waiting.append(f"PR #{pr.number} awaiting review{suffix}")
        if pr.checks == "pending":
            waiting.append("checks pending")
        if waiting:
            return "waiting", waiting

    extra: list[str] = []
    if pr is not None and pr.is_draft:
        extra.append(f"draft PR #{pr.number}")
    elif pr is not None and pr.state not in TERMINAL_PR_STATES and pr.state != "OPEN":
        extra.append(f"PR #{pr.number} open (state unknown)")
    elif live_pr:
        assert pr is not None
        extra.append(f"PR #{pr.number} open")

    if idle is not None and idle > stale_after:  # 6
        if session.worktree is not None and session.worktree.dirty:
            return "stale", [f"dirty worktree, idle {fmt_days(idle)}", *extra]
        return "stale", [f"idle {fmt_days(idle)}", *extra]

    if session.is_open:  # 7
        return "parked", [f"open, idle {fmt_ago(idle)}", *extra]
    return "parked", [f"last active {fmt_ago(idle)}", *extra]


__all__ = [
    "SCOUT_RUN_TITLE_RE",
    "choose_pr",
    "derive_state",
    "fmt_ago",
    "fmt_days",
    "git_toplevel",
    "is_scout_run",
    "resolve_project_key",
    "strip_worktree",
]

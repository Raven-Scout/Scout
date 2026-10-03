"""Pure derivations: project key, Scout-run flag, PR choice, state (spec §4.2, §4.3, §4.7)."""

from __future__ import annotations

import os
import re
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


def _root_at(folder: Path) -> str | None:
    """The repository root that a ``.git`` directly inside *folder* names, or None if there is none
    or it cannot be used (unreadable, no ``gitdir:`` line, pointing at a git dir that is gone)."""
    dot = folder / ".git"
    try:
        if dot.is_dir():
            return str(folder)
        if not dot.is_file():
            return None
        first = dot.read_text(encoding="utf-8", errors="replace").partition("\n")[0].strip()
        if not first.startswith("gitdir:"):
            return None
        target = first.removeprefix("gitdir:").strip()
        if not target:
            return None
        gitdir = Path(target) if os.path.isabs(target) else folder / target
        if not gitdir.is_dir():
            return None
        commondir = gitdir / "commondir"
        common = commondir.read_text(encoding="utf-8", errors="replace").strip() if commondir.is_file() else ""
        if common:
            main = (gitdir / common).resolve()  # an absolute commondir replaces gitdir in the join
            if main.name == ".git":
                return str(main.parent)  # a linked worktree: its main repository
    except (OSError, RuntimeError):
        return None
    return str(folder)  # a submodule, a worktree of one, or a separate git dir: --show-toplevel's answer


def repo_root(path: str) -> str | None:
    """The main repository root for *path*, or None. Pure Python, no ``git`` (1b spec §3.2).

    The walk starts at the nearest existing ancestor, so a deleted worktree still names its
    repository, and goes up to the first usable ``.git``. It never raises. A relative path
    returns None, like the empty one (``Path("")`` is "."), rather than resolving against the
    caller's own working directory. The result is the real path, as ``git rev-parse`` reports.
    """
    if not path or not os.path.isabs(path):
        return None
    try:
        start = Path(path)
        while not start.exists():
            start = start.parent
        start = start.resolve()
        for folder in (start, *start.parents):
            root = _root_at(folder)
            if root is not None:
                return root
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def resolve_project_key(origin_cwd: str, *, toplevel: Callable[[str], str | None]) -> str:
    if not origin_cwd:
        return ""
    top = toplevel(origin_cwd)
    if top:
        return top
    return strip_worktree(origin_cwd)


def is_scout_run(*, origin_cwd: str, title: str | None, scheduled_task_id: str | None, vault: Path) -> bool:
    try:
        in_vault = Path(origin_cwd).resolve() == vault.resolve()
    except (OSError, ValueError):  # ValueError: a NUL byte in a recorded path
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
    """Spec §4.7: the state is the first matching rule; ``reasons`` lists every matched signal.

    Rule-4 (needs you), rule-5 (waiting) and rule-6 (stale) signals are computed
    independently, so e.g. a running session with changes requested says both, and a
    waiting PR that has also gone idle past ``stale_after`` says ``idle Nd`` too.

    A session that ended on a question counts as needs_you only while fresh (idle <= stale_after);
    when idle > stale_after, the question signal moves to stale reasons instead.
    """
    last = parse_iso(session.last_activity_at)
    idle = (now - last) if last else None
    pr = session.pr

    if session.is_archived:  # 1
        return "done", ["archived"]

    running = session.is_open and idle is not None and idle <= running_window  # 2
    if not running and pr is not None and pr.state in TERMINAL_PR_STATES:  # 3
        return "done", [f"PR #{pr.number} {pr.state.lower()}"]

    # Drafts contribute no PR-based rule-4/5 signals; the question signal still applies.
    live_pr = pr is not None and pr.state == "OPEN" and not pr.is_draft
    needs: list[str] = []  # rule-4 signals
    waiting: list[str] = []  # rule-5 signals
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
        if pr.review_requested or pr.review_decision == "REVIEW_REQUIRED":
            age = parse_iso(pr.updated_at)
            suffix = f" {fmt_days(now - age)}" if age else ""
            waiting.append(f"PR #{pr.number} awaiting review{suffix}")
        if pr.checks == "pending":
            waiting.append("checks pending")
    question_is_fresh = True  # whether the question signal goes to needs (vs stale)
    if session.transcript is not None and session.transcript.last_turn.kind == "question":
        if idle is None or idle <= stale_after:
            needs.append("ended on a question")
        else:
            question_is_fresh = False
    stale: list[str] = []  # rule-6 signals: idle/dirty, then a question too old to count as needs_you
    if idle is not None and idle > stale_after:
        dirty = session.worktree is not None and session.worktree.dirty
        stale.append(f"dirty worktree, idle {fmt_days(idle)}" if dirty else f"idle {fmt_days(idle)}")
        if not question_is_fresh:
            stale.append("ended on a question")

    if running:
        return "running", [f"active {fmt_ago(idle)}", *needs, *waiting]
    if needs:  # 4
        return "needs_you", [*needs, *waiting, *stale]
    if waiting:  # 5
        return "waiting", [*waiting, *stale]

    extra: list[str] = []
    if pr is not None and pr.is_draft:
        extra.append(f"draft PR #{pr.number}")
    elif pr is not None and pr.state not in TERMINAL_PR_STATES and pr.state != "OPEN":
        extra.append(f"PR #{pr.number} open (state unknown)")
    elif live_pr:
        assert pr is not None
        extra.append(f"PR #{pr.number} open")

    if stale:  # 6
        return "stale", [*stale, *extra]

    if session.is_open:  # 7
        return "parked", [f"open, idle {fmt_ago(idle)}", *extra]
    return "parked", [f"last active {fmt_ago(idle)}", *extra]


__all__ = [
    "SCOUT_RUN_TITLE_RE",
    "choose_pr",
    "derive_state",
    "fmt_ago",
    "fmt_days",
    "is_scout_run",
    "repo_root",
    "resolve_project_key",
    "strip_worktree",
]

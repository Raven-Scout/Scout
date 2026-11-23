"""The run-commit ledger: which commits a Scout run made, recorded by the runner, never by the run.

A commit's subject prefix (``dreaming [...]:``) is the run's own say-so: neither the broker nor
``git-safe-commit.sh`` enforces it, so a run could commit a brain edit as ``fix: ...`` and the changes map would
file it as the owner's (``interactive``), untested and never offered for undo. This ledger is the signal a run
can't write. ``scoutctl sandbox verify`` (:func:`record`) runs outside the sandbox after claude exits and before the
integrity check makes any restore commit. It lists every non-merge commit reachable from a branch (or a detached
HEAD) that moved during the run and from nothing the start snapshot held, and appends one row per commit to
``run-commits.jsonl`` in the owner store (scout.changes.store.owner_dir), a protected engine root a sandboxed run
can't write. The scanner (scout.changes.scan) looks every commit up here first.

Owner commits made while a run holds the lock: the integrity check's exemption is reused as it is
(scout.sandbox.integrity._vouching_worktree). A branch checked out in another linked worktree, whose own index
holds the branch tip's entries for what changed, is the owner's: its commits are recorded ``by: worktree`` and stay
``interactive``. Like the integrity check, that exemption holds only under an enforced sandbox, and never for the
vault's own branch; any other commit in the window is the run's (fail closed: tested and undoable, never hidden).

Cutover: the ledger starts with the first run after upgrading to the release that ships it. A commit not in it (all
earlier history, and a run whose start snapshot couldn't be taken, so integrity wasn't checked either) is classified
by its subject prefix, as before.

Rows carry a commit sha, which names the same commit in any clone, so they are not keyed to a vault."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout.changes import store

RUN_COMMITS_NAME = "run-commits.jsonl"
SCHEMA = 1
# One run records at most this many commits (newest first); any beyond fall back to their subject prefix.
MAX_PER_RUN = 2000
# A run lane the mode names; a mode that names none (manual, respond, a custom slot) is the generic ``run`` lane.
_MODE_LANES = ("briefing", "consolidation", "dreaming", "research")
_MODE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
BY = frozenset({"run", "worktree"})


@dataclass(frozen=True)
class RunCommit:
    sha: str
    mode: str
    by: str  # "run": the run made it; "worktree": an owner worktree vouched for it (scout.sandbox.integrity)


def path() -> Path:
    return store.owner_dir(os.environ) / RUN_COMMITS_NAME


def _mode(label: str) -> str:
    return label if _MODE_RE.fullmatch(label) else "unknown"


def mode_lane(mode: str) -> str:
    """The lane a run's mode names (``morning-briefing`` → briefing, ``dreaming-manual`` → dreaming), else ``run``."""
    words = mode.split("-")
    return next((lane for lane in _MODE_LANES if lane in words), "run")


def read() -> dict[str, RunCommit]:
    """Every recorded commit by sha. Never raises: an unreadable ledger is an empty one (the prefix fallback).

    A sha recorded more than once is the run's if any row says so, so a later row can never take a commit out of
    a run's lane."""
    try:
        rows, _ = store._read(path())
    except Exception:  # noqa: BLE001 - a ledger that can't be read classifies by prefix, as before it existed
        return {}
    out: dict[str, RunCommit] = {}
    for row in rows:
        sha, mode, by = row.get("sha"), row.get("mode"), row.get("by")
        if row.get("schema") != SCHEMA or not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            continue
        if not isinstance(mode, str) or not isinstance(by, str) or by not in BY:
            continue
        seen = out.get(sha)
        if seen is None or (seen.by != "run" and by == "run"):
            out[sha] = RunCommit(sha, _mode(mode), by)
    return out


def window(vault: Path, start: dict, env: Mapping[str, str], *, label: str) -> list[RunCommit]:
    """The commits made since the start snapshot ``start`` (scout.sandbox.integrity.snapshot), each with who made
    it. Raises :class:`scout.sandbox.integrity.IntegrityError` when git can't be read."""
    from scout.sandbox import integrity as ig

    genv = ig._git_env(env)
    if not start.get("repo"):
        return []
    start_refs: dict[str, str] = start["refs"]
    start_head: str | None = start["head"]
    head, branch = ig._head(vault, genv)
    now_refs = ig._refs(vault, genv)
    tips = {r: sha for r, sha in now_refs.items() if r.startswith("refs/heads/") and start_refs.get(r) != sha}
    if branch is None and head and head != start_head:
        tips["HEAD"] = head
    if not tips:
        return []
    known = sorted({*start_refs.values(), *([start_head] if start_head else [])})
    trust_worktrees = env.get("SCOUT_SANDBOX_STATUS") == "enforced"
    own_branches = {b for b in (start.get("branch"), branch) if b}
    run: dict[str, None] = {}
    owner: dict[str, None] = {}
    for ref, tip in sorted(tips.items()):
        listed = ig._git(
            vault,
            ["rev-list", "--no-merges", f"--max-count={MAX_PER_RUN}", "--stdin"],
            genv,
            input_text="".join(f"{s}\n" for s in [tip, *(f"^{k}" for k in known)]),
        ).split()
        vouched = (
            trust_worktrees
            and ref.startswith("refs/heads/")
            and ref not in own_branches
            and _vouched(vault, start, ref, tip, start_refs.get(ref) or start_head, genv)
        )
        (owner if vouched else run).update(dict.fromkeys(listed))
    label = _mode(label)
    out = [RunCommit(s, label, "run") for s in run]
    out += [RunCommit(s, label, "worktree") for s in owner if s not in run]
    return out[:MAX_PER_RUN]


def _vouched(vault: Path, start: dict, ref: str, tip: str, base: str | None, genv: Mapping[str, str]) -> bool:
    """Whether a linked worktree vouches for every path ``ref`` changed since the snapshot, by the integrity check's
    own rule (scout.sandbox.integrity._vouching_worktree) over the whole tree, not only protected files."""
    from scout.sandbox import integrity as ig

    if base is None:
        changed = ig._git(vault, ["ls-tree", "-r", "-z", "--name-only", "--full-tree", tip], genv)
    else:
        changed = ig._git(vault, ["diff-tree", "-r", "-z", "--name-only", "--no-renames", base, tip, "--"], genv)
    paths = [p for p in changed.split("\0") if p]
    scope_list = [ig.Scope(ig._norm(p), False) for p in paths]
    tip_entries = ig._tree_entries(vault, tip, scope_list, genv)
    return ig._vouching_worktree(vault, start, ref, tip_entries, paths, scope_list, genv) is not None


def record(vault: Path, start: dict, env: Mapping[str, str] | None = None, *, label: str) -> int | None:
    """Append a row per commit in :func:`window`; how many, or None when the window or the ledger couldn't be
    read or written. Never raises: the integrity check that follows must run whatever happens here."""
    env = os.environ if env is None else env
    try:
        commits = window(vault, start, env, label=label)
        ts = datetime.now(UTC).isoformat()
        for c in commits:
            store._append(path(), {"schema": SCHEMA, "sha": c.sha, "mode": c.mode, "by": c.by, "ts": ts})
    except Exception:  # noqa: BLE001 - recording is best-effort; the prefix fallback covers a run it missed
        return None
    return len(commits)

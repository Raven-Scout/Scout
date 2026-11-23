"""The run-commit ledger: which commits a Scout run made, recorded by the runner, never by the run.

A commit's subject prefix (``dreaming [...]:``) is the run's own say-so: neither the broker nor
``git-safe-commit.sh`` enforces it, so a run could commit a brain edit as ``fix: ...`` and the changes map would
file it as the owner's (``interactive``), untested and never offered for undo. This ledger is the signal a run
can't write. ``scoutctl sandbox verify`` (:func:`record`) runs outside the sandbox after claude exits and before the
integrity check makes any restore commit. Every ref under the integrity check's ``REF_PREFIXES`` that moved or
appeared during the run, and a detached HEAD that moved, is a tip (one that isn't a commit is skipped, as the
integrity check skips it); every commit the start snapshot's refs and HEAD held is known. Each non-merge commit
reachable from a tip and from nothing known is the run's, and gets one row in ``run-commits.jsonl`` in the owner
store (scout.changes.store.owner_dir), a protected engine root a sandboxed run can't write. The scanner
(scout.changes.scan) looks every commit up here first.

Fails closed: when a tip's commits number more than :data:`MAX_PER_RUN` or git can't list them, the row records the
window itself (its tips and the known commits) instead, and every commit in it is the run's, worked out again when
read. When nothing can be recorded at all, ``sandbox verify`` says so and exits 12 (the integrity check couldn't
run), so run health goes red.

Owner commits made while a run holds the lock: the integrity check's exemption is reused as it is
(scout.sandbox.integrity._vouching_worktree). A branch checked out in another linked worktree, whose own index
holds the branch tip's entries for what changed, is the owner's: its commits are recorded ``by: worktree`` and stay
``interactive``. Like the integrity check, that exemption holds only under an enforced sandbox, and never for the
vault's own branch; any other commit in the window is the run's (tested and undoable, never hidden).

Cutover: the ledger starts with the first run after upgrading to the release that ships it. A commit not in it (all
earlier history, and a run whose start snapshot couldn't be taken, so integrity wasn't checked either) is classified
by its subject prefix, as before.

Rows carry commit shas, which name the same commits in any clone, so they are not keyed to a vault. The file is
append-only and nothing compacts it yet: a row is about 150 bytes, a few per run."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from scout.changes import store

RUN_COMMITS_NAME = "run-commits.jsonl"
SCHEMA = 1
# Past this many commits on one tip, the run's row records the window instead of each commit.
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
    ts: datetime | None = None  # when the runner recorded it


@dataclass(frozen=True)
class Window:
    """Every non-merge commit reachable from ``tips`` and from none of ``known`` is the run's."""

    tips: tuple[str, ...]
    known: tuple[str, ...]
    mode: str = "unknown"
    ts: datetime | None = None

    def rev_list_input(self) -> bytes:
        return "".join(f"{s}\n" for s in [*self.tips, *(f"^{k}" for k in self.known)]).encode()


@dataclass(frozen=True)
class Ledger:
    commits: dict[str, RunCommit] = field(default_factory=dict)
    windows: tuple[Window, ...] = ()


@dataclass(frozen=True)
class Recorded:
    commits: int
    windows: int


def path() -> Path:
    return store.owner_dir(os.environ) / RUN_COMMITS_NAME


def _mode(label: object) -> str:
    return label if isinstance(label, str) and _MODE_RE.fullmatch(label) else "unknown"


def mode_lane(mode: str) -> str:
    """The lane a run's mode names (``morning-briefing`` → briefing, ``dreaming-manual`` → dreaming), else ``run``."""
    words = mode.split("-")
    return next((lane for lane in _MODE_LANES if lane in words), "run")


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def _shas(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(s, str) and _SHA_RE.fullmatch(s) for s in value):
        return None
    return tuple(value)


def read() -> Ledger:
    """Every recorded commit by sha, and every recorded window. Never raises: an unreadable ledger is an empty one
    (the prefix fallback).

    A sha recorded more than once is the run's if any row says so, so a later row can never take a commit out of
    a run's lane."""
    try:
        rows, _ = store._read(path())
    except Exception:  # noqa: BLE001 - a ledger that can't be read classifies by prefix, as before it existed
        return Ledger()
    commits: dict[str, RunCommit] = {}
    windows: list[Window] = []
    for row in rows:
        if row.get("schema") != SCHEMA:
            continue
        mode, ts = _mode(row.get("mode")), _ts(row.get("ts"))
        if "window" in row:
            w = row["window"]
            tips = _shas(w.get("tips")) if isinstance(w, dict) else None
            known = _shas(w.get("known")) if isinstance(w, dict) else None
            if tips and known is not None:
                windows.append(Window(tips, known, mode, ts))
            continue
        sha, by = row.get("sha"), row.get("by")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha) or by not in BY:
            continue
        seen = commits.get(sha)
        if seen is None or (seen.by != "run" and by == "run"):
            commits[sha] = RunCommit(sha, mode, str(by), ts)
    return Ledger(commits, tuple(windows))


def window(vault: Path, start: dict, env: Mapping[str, str], *, label: str) -> tuple[list[RunCommit], list[Window]]:
    """The commits made since the start snapshot ``start`` (scout.sandbox.integrity.snapshot), each with who made
    it, and the windows too big or unreadable to list. Raises :class:`scout.sandbox.integrity.IntegrityError` only
    when the refs themselves can't be read."""
    from scout.sandbox import integrity as ig

    if not start.get("repo"):
        return [], []
    genv = ig._git_env(env)
    mode = _mode(label)
    start_refs: dict[str, str] = start["refs"]
    start_head: str | None = start["head"]
    # The snapshot's refs and HEAD, each already peeled to its commit (a ref that isn't one has no entry).
    known = tuple(sorted(start["trees"]))
    head, branch = ig._head(vault, genv)
    tips: dict[str, str] = {}
    for ref, sha in ig._refs(vault, genv).items():
        if start_refs.get(ref) != sha:
            commit = ig._commit_of(vault, sha, genv)
            if commit is not None:  # a ref at a blob or a tree holds no commit for the map to read
                tips[ref] = commit
    if branch is None and head and head != start_head:
        tips["HEAD"] = head
    trust_worktrees = env.get("SCOUT_SANDBOX_STATUS") == "enforced"
    own_branches = {b for b in (start.get("branch"), branch) if b}
    run: dict[str, None] = {}
    owner: dict[str, None] = {}
    whole: list[str] = []
    for ref, tip in sorted(tips.items()):
        try:
            listed = ig._git(
                vault,
                ["rev-list", "--no-merges", f"--max-count={MAX_PER_RUN + 1}", "--stdin"],
                genv,
                input_text=Window((tip,), known).rev_list_input().decode(),
            ).split()
        except ig.IntegrityError:
            listed = None
        if listed is None or len(listed) > MAX_PER_RUN:
            whole.append(tip)
            continue
        vouched = (
            trust_worktrees
            and ref.startswith("refs/heads/")
            and ref not in own_branches
            and _vouched(vault, start, ref, tip, start_refs.get(ref) or start_head, genv)
        )
        (owner if vouched else run).update(dict.fromkeys(listed))
    commits = [RunCommit(s, mode, "run") for s in run]
    commits += [RunCommit(s, mode, "worktree") for s in owner if s not in run]
    windows = [Window(tuple(dict.fromkeys(whole)), known, mode)] if whole else []
    return commits, windows


def _vouched(vault: Path, start: dict, ref: str, tip: str, base: str | None, genv: Mapping[str, str]) -> bool:
    """Whether a linked worktree vouches for every path ``ref`` changed since the snapshot, by the integrity check's
    own rule (scout.sandbox.integrity._vouching_worktree) over the whole tree, not only protected files. Any
    failure is no vouch."""
    from scout.sandbox import integrity as ig

    try:
        base_commit = ig._commit_of(vault, base, genv) if base else None
        if base_commit is None:
            changed = ig._git(vault, ["ls-tree", "-r", "-z", "--name-only", "--full-tree", tip], genv)
        else:
            args = ["diff-tree", "-r", "-z", "--name-only", "--no-renames", base_commit, tip, "--"]
            changed = ig._git(vault, args, genv)
        paths = [p for p in changed.split("\0") if p]
        scope_list = [ig.Scope(ig._norm(p), False) for p in paths]
        tip_entries = ig._tree_entries(vault, tip, scope_list, genv)
        return ig._vouching_worktree(vault, start, ref, tip_entries, paths, scope_list, genv) is not None
    except (ig.IntegrityError, KeyError, TypeError, ValueError):
        return False


def record(vault: Path, start: dict, env: Mapping[str, str] | None = None, *, label: str) -> Recorded | None:
    """Append a row per commit in :func:`window`, and one per window it couldn't list. None when the refs couldn't
    be read or the ledger couldn't be written; never raises, so the integrity check that follows always runs."""
    env = os.environ if env is None else env
    try:
        commits, windows = window(vault, start, env, label=label)
        ts = datetime.now(UTC).isoformat()
        rows: list[dict] = [{"schema": SCHEMA, "sha": c.sha, "mode": c.mode, "by": c.by, "ts": ts} for c in commits]
        rows += [
            {"schema": SCHEMA, "window": {"tips": list(w.tips), "known": list(w.known)}, "mode": w.mode, "ts": ts}
            for w in windows
        ]
        for row in rows:
            store._append(path(), row)
    except Exception:  # noqa: BLE001 - reported by the caller (exit 12), and the integrity check still runs
        return None
    return Recorded(len(commits), len(windows))

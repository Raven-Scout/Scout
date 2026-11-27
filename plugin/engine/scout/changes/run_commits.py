"""The run-commit ledger: which commits a Scout run made, recorded outside the run, never by it.

A commit's subject prefix (``dreaming [...]:``) is the run's own say-so: nothing enforces it, so a run could commit
a brain edit as ``fix: ...`` and the changes map would file it as the owner's (``interactive``), untested and never
offered for undo. The ledger, ``run-commits.jsonl`` in the owner store (scout.changes.store.owner_dir, a protected
engine root a sandboxed run can't write), is the signal a run can't forge. The scanner (scout.changes.scan) looks
every commit up there first; a commit with no row is classified by its subject prefix (history from before the
ledger, and the owner's own commits).

**The broker's record (exact).** Under Phase 1d (spec 2026-11-16) a run can't write the vault's git dirs: its only
way to commit is the run broker, which runs ``git-safe-commit.sh`` outside the sandbox. ``Broker.commit`` writes a
``begin`` row before the helper runs and an ``end`` row after (:func:`broker_begin`, :func:`broker_end`), each with
the broker's run id and the run's mode. The helper commits with ``GIT_REFLOG_ACTION`` set to a nonce of its own and
prints the commit whose HEAD reflog entry carries it (``scout-commit <sha>``; ``scout-commit unresolved`` when a
commit may exist but can't be named; ``scout-commit none`` when its commit failed and HEAD didn't move), so an owner
commit landing around it is never taken for the run's. A begin with no end (the broker stopped mid-commit, or the end
row couldn't be written), or an end with no sha that doesn't show no commit was made (exit 2–4, which the helper
gives only before it commits, or ``none``), is a *gap*: a commit that may be the run's but can't be named.
``scoutctl broker profile`` writes a ``profile`` row for the broker's run, ``tightened`` or not
(:func:`mark_broker_profile`). An end row's sha is exact (``by: broker``) only when its run's profile was tightened:
a broker whose profile wasn't, or the periodic tick's drain (no run of its own; it records no queued entry's claimed
run, and its commits carry no mode, so they land in the generic ``run`` lane), runs the helper where a run can still
write ``.git/logs``, so its sha is best effort (``by: run``). The helper names only a reflog entry added since its
commit started, so an older planted line can't win, and a helper stopped mid-commit leaves a gap.
``sandbox verify`` records nothing from git for a run whose every broker in the window was tightened and left no gap
(:func:`broker_state` ``on``); otherwise it records the git window too. The owner's
commits never go through the broker, so they stay ``interactive``, in a linked worktree or the vault's own
checkout.

**The git window (best effort, NOT SOUND).** A run without its broker (Phase 1c, or the sandbox off) can write the
vault's objects, refs, logs and index, so nothing worked out from git's state is sound against it: it can hide a
commit from any reading of git it controls. ``sandbox verify`` still records what it can, to catch a run that just
used the wrong prefix, failing closed where it can tell (:func:`window`, ``by: run``): every ref that moved or
appeared and a HEAD that moved are tips, every commit the start snapshot's refs held is known (a known commit gone
by the end is refused), git reads ignore replace refs, grafts and ``.git/shallow``, a tip with more than
:data:`MAX_PER_RUN` commits or that git can't list is recorded as a window whose every commit is the run's, and an
owner worktree vouches (the integrity check's rule, enforced sandbox only) for exactly one new commit on its
snapshot tip. Run health says when a run was attributed this way, and auto-rollback acts only on ``by: broker``
commits (scout.changes.actions).

When ``sandbox verify`` can't record a broker-less run at all it exits 12 if the integrity check was otherwise clean,
which run health shows (yellow, for the last run) as an integrity check that couldn't run.

Reader API (stable; scout.changes.scan, scout.changes.actions, scout.health and the integrity check use it):
:func:`read` (a :class:`Ledger`: ``commits`` by sha, ``windows``, ``profiles``, ``gaps``, ``unbrokered``),
:func:`is_broker_recorded`, :func:`broker_runs`, :func:`broker_gaps`, :func:`broker_state`,
:func:`broker_was_on`,
:func:`unbrokered_since`, :func:`mode_lane`.

Cutover: the ledger starts with the first run after upgrading to the release that ships it; the broker's exact record
starts once ``/scout-update`` has re-rendered ``scout/scripts/git-safe-commit.sh``: until then the helper names no sha,
so every broker commit is a gap and ``sandbox verify`` records the git window as for a broker-less run.

Commits are looked up by sha alone, not by vault: a sha names the same commit in any clone, and a commit a run made
stays the run's wherever it travels; the broker marks, gaps and health readers do filter by vault. Rows are
about 150 bytes (a window row holds every known commit, about 45 bytes each); the file is append-only and nothing
compacts it yet."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout.changes import store
from scout.plural import agree, count

RUN_COMMITS_NAME = "run-commits.jsonl"
SCHEMA = 1
# Past this many commits on one tip, the run's row records the window instead of each commit.
MAX_PER_RUN = 2000
# A run lane the mode names; a mode that names none (manual, respond, a custom slot) is the generic ``run`` lane.
_MODE_LANES = ("briefing", "consolidation", "dreaming", "research")
_MODE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# How much a ``by`` value is trusted: a later, weaker row never takes a commit out of a stronger one's lane.
BY_RANK = {"worktree": 0, "run": 1, "broker": 2}
# The line git-safe-commit.sh prints last on stdout, under its mutex.
SHA_LINE = re.compile(r"^scout-commit ([0-9a-f]{40}|[0-9a-f]{64})$")
# The helper's exits that come before it runs `git commit` (usage, the mutex, nothing to commit): no commit was made.
NO_COMMIT_EXITS = frozenset({2, 3, 4})
# git reads that a run without its broker can't steer: replace refs, grafts and a planted .git/shallow are ignored.
HARDENED_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_SHALLOW_FILE": os.devnull}


@dataclass(frozen=True)
class RunCommit:
    sha: str
    mode: str
    by: str  # "broker": exact; "run": the git window (best effort); "worktree": an owner worktree vouched for it
    ts: datetime | None = None  # when it was recorded


@dataclass(frozen=True)
class Window:
    """Every non-merge commit reachable from ``tips`` and from none of ``known`` is the run's (best effort)."""

    tips: tuple[str, ...]
    known: tuple[str, ...]
    mode: str = "unknown"
    ts: datetime | None = None
    taken_at: datetime | None = None  # when the run's start snapshot was taken

    def rev_list_input(self) -> bytes:
        return "".join(f"{s}\n" for s in [*self.tips, *(f"^{k}" for k in self.known)]).encode()


@dataclass(frozen=True)
class BrokerRun:
    """A broker's run, from its ``broker profile`` row: whether the run got the tightened (Phase 1d) profile."""

    vault_key: str
    run: str
    tightened: bool
    ts: datetime


@dataclass(frozen=True)
class Gap:
    """A broker commit that may be a run's but can't be named: no end row, or an end with no sha."""

    vault_key: str
    run: str
    mode: str
    ts: datetime


@dataclass(frozen=True)
class Ledger:
    commits: dict[str, RunCommit] = field(default_factory=dict)
    windows: tuple[Window, ...] = ()
    profiles: tuple[BrokerRun, ...] = ()
    gaps: tuple[Gap, ...] = ()
    # Every sha any row names as a commit (end rows, and `by` rows of any value, a bare `by: broker` one too),
    # ungraded: :func:`recorded_shas`.
    named: frozenset[str] = frozenset()
    # (vault key, when, why) of each run whose commits the git window recorded: "no-broker" (Phase 1c, the
    # sandbox off, a profile that wasn't tightened) or "broker-gap" (a broker commit it couldn't name).
    unbrokered: tuple[tuple[str, datetime, str], ...] = ()


@dataclass(frozen=True)
class Recorded:
    commits: int
    windows: int
    brokered: bool = False


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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def read() -> Ledger:
    """Every recorded commit by sha (the most trusted row for it), every window and every broker mark. Never
    raises: an unreadable ledger is an empty one (the prefix fallback)."""
    try:
        rows, _ = store._read(path())
    except Exception:  # noqa: BLE001 - a ledger that can't be read classifies by prefix, as before it existed
        return Ledger()
    commits: dict[str, RunCommit] = {}
    windows: list[Window] = []
    profiles: list[BrokerRun] = []
    begun: dict[str, Gap] = {}
    gaps: list[Gap] = []
    unbrokered: list[tuple[str, datetime, str]] = []
    named: set[str] = set()
    ends: list[tuple[str, str, str, datetime, str]] = []

    def add(sha: str, mode: str, by: str, ts: datetime | None) -> None:
        seen = commits.get(sha)
        if seen is None or BY_RANK[by] > BY_RANK[seen.by]:
            commits[sha] = RunCommit(sha, mode, by, ts)

    for row in rows:
        if row.get("schema") != SCHEMA:
            continue
        mode, ts, key = _mode(row.get("mode")), _ts(row.get("ts")), row.get("vault_key")
        raw_run = row.get("run")
        run: str = raw_run if isinstance(raw_run, str) else ""
        kind = row.get("broker")
        if kind in ("profile", "begin", "end"):
            if not isinstance(key, str) or ts is None:
                continue
            if kind == "profile":
                profiles.append(BrokerRun(key, run, row.get("tightened") is True, ts))
            elif kind == "begin" and isinstance(row.get("id"), str):
                begun[row["id"]] = Gap(key, run, mode, ts)
            elif kind == "end":
                started = begun.pop(row["id"], None) if isinstance(row.get("id"), str) else None
                sha = row.get("sha")
                if isinstance(sha, str) and _SHA_RE.fullmatch(sha):
                    named.add(sha)
                uncertain = row.get("uncertain") is True
                if isinstance(sha, str) and _SHA_RE.fullmatch(sha) and not uncertain:
                    ends.append((key, run, mode, ts, sha))
                elif uncertain or not (row.get("none") is True or row.get("exit") in NO_COMMIT_EXITS):
                    # No sha, and nothing shows no commit was made (a helper stopped or failing after `git commit`)
                    gaps.append(started or Gap(key, run, mode, ts))
            continue
        if "window" in row:
            w = row["window"]
            tips = _shas(w.get("tips")) if isinstance(w, dict) else None
            known = _shas(w.get("known")) if isinstance(w, dict) else None
            if tips and known is not None:
                taken = _ts(w.get("taken_at")) if isinstance(w, dict) else None
                windows.append(Window(tips, known, mode, ts, taken))
                if isinstance(key, str) and ts is not None:
                    unbrokered.append((key, ts, _why(row)))
            continue
        sha, by = row.get("sha"), row.get("by")
        if isinstance(sha, str) and _SHA_RE.fullmatch(sha) and isinstance(by, str):
            named.add(sha)
        # A broker's record is an end row; a bare `by: broker` row is never trusted.
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha) or by not in ("run", "worktree"):
            continue
        add(sha, mode, str(by), ts)
        if by == "run" and isinstance(key, str) and ts is not None:
            unbrokered.append((key, ts, _why(row)))
    # A broker's sha is exact only when its run got the tightened profile: a broker whose profile wasn't (the run
    # kept the 1c sandbox and could write .git/logs) or a drain with no run of its own (the periodic tick) runs the
    # helper where a run can still steer git, so its sha is best effort, `run` grade.
    tightened = {(b.vault_key, b.run) for b in profiles if b.tightened and b.run}
    for key, run, mode, ts, sha in ends:
        add(sha, mode, "broker" if (key, run) in tightened else "run", ts)
    gaps.extend(begun.values())
    gaps_by_time = tuple(sorted(gaps, key=lambda g: g.ts))
    return Ledger(commits, tuple(windows), tuple(profiles), gaps_by_time, frozenset(named), tuple(unbrokered))


def recorded_shas(ledger: Ledger | None = None) -> frozenset[str]:
    """Every sha a ledger row names outright, whatever its grade: each end row's sha, and each row with a sha and a
    ``by`` of any value (``broker``, ``run``, ``worktree``, one this version doesn't know). It says nothing about
    trust: that is :func:`broker_state`'s.

    It leaves out the git windows (:attr:`Ledger.windows`): a window names tips and known commits, not members, and
    its members are a best-effort walk of git that can take in the owner's commits made during the run, so a blame
    set built from them would blame runs for others' commits. A run attributed only by a window has no sha here."""
    return (read() if ledger is None else ledger).named


def is_broker_recorded(sha: str, ledger: Ledger | None = None) -> bool:
    hit = (read() if ledger is None else ledger).commits.get(sha)
    return hit is not None and hit.by == "broker"


def _why(row: Mapping[str, Any]) -> str:
    return "broker-gap" if row.get("why") == "broker-gap" else "no-broker"


def unbrokered_since(vault: Path, since: datetime) -> list[tuple[datetime, str]]:
    """``(when, why)`` of each run of ``vault`` since ``since`` whose commits the git window attributed: why is
    ``no-broker`` or ``broker-gap`` (the broker was on but couldn't name a commit, usually an old helper)."""
    key = store.vault_key(vault)
    return sorted({(ts, why) for k, ts, why in read().unbrokered if k == key and ts >= since})


# --- the broker's record (exact) -------------------------------------------------------------------------------------


def _append(row: dict[str, Any]) -> None:
    store._append(path(), {"schema": SCHEMA, **row, "ts": _now()})


def mark_broker_profile(vault: Path, run: str, *, tightened: bool) -> bool:
    """Record what ``broker profile`` gave the broker's run ``run``: the tightened profile, or not (the run keeps
    the 1c sandbox). Written outside the sandbox before claude starts. Whether it saved; never raises."""
    try:
        _append({"broker": "profile", "run": run, "tightened": tightened, "vault_key": store.vault_key(vault)})
    except Exception:  # noqa: BLE001 - without the row, verify records the git window: fail closed
        return False
    return True


def broker_runs(vault: Path, since: datetime) -> list[BrokerRun]:
    """Each broker run of ``vault`` whose profile was decided at or after ``since``, oldest first."""
    key = store.vault_key(vault)
    return sorted((p for p in read().profiles if p.vault_key == key and p.ts >= since), key=lambda p: p.ts)


def broker_gaps(vault: Path, since: datetime) -> list[Gap]:
    """Each broker commit of ``vault`` since ``since`` that can't be named: begun and never ended, or ended with
    no sha."""
    key = store.vault_key(vault)
    return [g for g in read().gaps if g.vault_key == key and g.ts >= since]


def broker_state(vault: Path, start: Mapping[str, Any]) -> str:
    """How this run's commits stand in the broker's record: ``on`` (at least one broker run of this vault had its
    profile decided after the run's start snapshot, all of them tightened, and no broker commit since is a gap),
    ``gap`` (all tightened, but a broker commit couldn't be named: usually an old git-safe-commit.sh) or ``off``.
    The session lock serialises a vault's runs, so the window is this run's."""
    extra = start.get("changes")
    taken = _ts(extra.get("taken_at")) if isinstance(extra, dict) else None
    if taken is None:
        return "off"
    runs = broker_runs(vault, taken)
    if not runs or not all(r.tightened for r in runs):
        return "off"
    return "gap" if broker_gaps(vault, taken) else "on"


def broker_was_on(vault: Path, start: Mapping[str, Any]) -> bool:
    """Whether every commit this run made is in the broker's exact record (:func:`broker_state` is ``on``)."""
    return broker_state(vault, start) == "on"


def sha_from_helper(stdout: str) -> tuple[str, str | None]:
    """``(what the helper said, its sha)`` from git-safe-commit.sh's last stdout line: ``sha`` (``scout-commit
    <sha>``), ``unresolved`` (a commit may exist but can't be named), ``none`` (no commit was made) or ``silent``
    (a helper from before the line, or one that stopped before it)."""
    lines = stdout.strip().splitlines()
    last = lines[-1].strip() if lines else ""
    m = SHA_LINE.match(last)
    if m:
        return "sha", m.group(1)
    return {"scout-commit unresolved": "unresolved", "scout-commit none": "none"}.get(last, "silent"), None


def broker_begin(vault: Path, run: str | None, mode: object) -> str | None:
    """Write the begin row of a broker commit; its id, or None when it couldn't be written. Never raises."""
    rid = uuid.uuid4().hex
    try:
        _append(
            {"broker": "begin", "id": rid, "run": run or "", "mode": _mode(mode), "vault_key": store.vault_key(vault)}
        )
    except Exception:  # noqa: BLE001 - logged by the broker; the end row alone still records the sha
        return None
    return rid


def broker_end(
    vault: Path,
    rid: str | None,
    run: str | None,
    mode: object,
    *,
    exit_code: int,
    sha: str | None,
    uncertain: bool = False,
    none: bool = False,
) -> bool:
    """Write the end row of a broker commit. ``uncertain``: the helper was stopped (a timeout), so git may have
    committed without the sha being named, a gap. ``none``: the helper said its commit failed with HEAD unmoved.
    Whether it saved; never raises."""
    row: dict[str, Any] = {
        "broker": "end", "id": rid or uuid.uuid4().hex, "run": run or "", "mode": _mode(mode), "exit": exit_code,
        "vault_key": store.vault_key(vault),
    }  # fmt: skip
    if sha is not None and _SHA_RE.fullmatch(sha):
        row["sha"] = sha
    if uncertain:
        row["uncertain"] = True
    elif none and "sha" not in row:
        row["none"] = True
    try:
        _append(row)
    except Exception:  # noqa: BLE001 - a begin with no end is a gap the readers see
        return False
    return True


# --- the git window (best effort, NOT SOUND against a run that can write .git) --------------------------------------


def _git_env(env: Mapping[str, str]) -> dict[str, str]:
    from scout.sandbox import integrity as ig

    return {**ig._git_env(env), **HARDENED_GIT_ENV}


def _all_refs(vault: Path, genv: Mapping[str, str]) -> dict[str, tuple[str, str | None]]:
    """Every ref (any namespace): ``{ref: (object, its commit or None)}``, a tag peeled once."""
    from scout.sandbox import integrity as ig

    fmt = "%(objectname) %(objecttype) %(*objectname) %(*objecttype) %(refname)"
    out: dict[str, tuple[str, str | None]] = {}
    for line in ig._git(vault, ["for-each-ref", f"--format={fmt}"], genv).splitlines():
        parts = line.split(" ", 4)
        if len(parts) != 5 or not parts[4]:
            continue
        sha, kind, peeled, peeled_kind, ref = parts
        commit = sha if kind == "commit" else peeled if kind == "tag" and peeled_kind == "commit" else None
        out[ref] = (sha, commit)
    return out


def snapshot_extra(vault: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What the git window needs from the start, beside the integrity snapshot: when it was taken, every ref and
    every commit a ref or HEAD held. Raises :class:`scout.sandbox.integrity.IntegrityError`."""
    from scout.sandbox import integrity as ig

    genv = _git_env(os.environ if env is None else env)
    taken_at = _now()
    if not ig._is_repo(vault, genv):
        return {"taken_at": taken_at, "refs": {}, "known": []}
    refs = _all_refs(vault, genv)
    head, _ = ig._head(vault, genv)
    known = {c for _, c in refs.values() if c} | ({head} if head else set())
    return {"taken_at": taken_at, "refs": {r: s for r, (s, _) in refs.items()}, "known": sorted(known)}


def _missing(vault: Path, shas: list[str], genv: Mapping[str, str]) -> list[str]:
    from scout.sandbox import integrity as ig

    if not shas:
        return []
    out = ig._git(vault, ["cat-file", "--batch-check=%(objecttype)"], genv, input_text="".join(f"{s}\n" for s in shas))
    return [s for s, kind in zip(shas, out.splitlines(), strict=False) if kind.strip() != "commit"]


def window(vault: Path, start: dict, env: Mapping[str, str], *, label: str) -> tuple[list[RunCommit], list[Window]]:
    """The commits made since the start snapshot ``start`` (scout.sandbox.integrity.snapshot plus
    :func:`snapshot_extra` under ``changes``), each with who made it, and the windows too big or unreadable to list.
    Best effort, not sound against a run that wrote .git. Raises :class:`scout.sandbox.integrity.IntegrityError`
    when the refs can't be read or a commit the run started with is gone (refused, never ignored)."""
    from scout.sandbox import integrity as ig

    if not start.get("repo"):
        return [], []
    genv = _git_env(env)
    mode = _mode(label)
    raw_extra = start.get("changes")
    extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
    refs_at_start = extra.get("refs")
    start_refs: dict[str, str] = refs_at_start if isinstance(refs_at_start, dict) else start["refs"]
    start_head: str | None = start["head"]
    listed_known = extra.get("known")
    known_at_start: list[str] = listed_known if isinstance(listed_known, list) else list(start["trees"])
    known: list[str] = sorted({str(k) for k in known_at_start} | ({start_head} if start_head else set()))
    gone = _missing(vault, known, genv)
    if gone:
        raise ig.IntegrityError(
            f"{count(len(gone), 'commit')} the run started with {agree(len(gone), 'is', 'are')} gone"
        )
    head, branch = ig._head(vault, genv)
    tips: dict[str, str] = {}
    for ref, (sha, commit) in _all_refs(vault, genv).items():
        if start_refs.get(ref) != sha and commit is not None:  # a ref at a blob or tree holds no commit to read
            tips[ref] = commit
    if head and head != start_head:  # symbolic or detached: the run may have pointed HEAD anywhere
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
                input_text=Window((tip,), tuple(known)).rev_list_input().decode(),
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
            and listed == [tip]
            and _vouched(vault, start, ref, tip, start_refs.get(ref), genv)
        )
        (owner if vouched else run).update(dict.fromkeys(listed))
    commits = [RunCommit(s, mode, "run") for s in run]
    commits += [RunCommit(s, mode, "worktree") for s in owner if s not in run]
    taken = _ts(extra.get("taken_at"))
    windows = [Window(tuple(dict.fromkeys(whole)), tuple(known), mode, None, taken)] if whole else []
    return commits, windows


def _vouched(vault: Path, start: dict, ref: str, tip: str, base: str | None, genv: Mapping[str, str]) -> bool:
    """Whether ``tip`` is one new commit on ``ref``'s snapshot tip ``base`` that a linked worktree vouches for, by
    the integrity check's own rule (scout.sandbox.integrity._vouching_worktree) over every path it changed. Any
    failure is no vouch."""
    from scout.sandbox import integrity as ig

    try:
        base_commit = ig._commit_of(vault, base, genv) if base else None
        parents = ig._git(vault, ["rev-list", "--parents", "-n", "1", tip], genv).split()[1:]
        if base_commit is None or parents != [base_commit]:
            return False
        args = ["diff-tree", "-r", "-z", "--name-only", "--no-renames", base_commit, tip, "--"]
        paths = [p for p in ig._git(vault, args, genv).split("\0") if p]
        scope_list = [ig.Scope(ig._norm(p), False) for p in paths]
        tip_entries = ig._tree_entries(vault, tip, scope_list, genv)
        return ig._vouching_worktree(vault, start, ref, tip_entries, paths, scope_list, genv) is not None
    except (ig.IntegrityError, KeyError, TypeError, ValueError):
        return False


def record(
    vault: Path, start: dict, env: Mapping[str, str] | None = None, *, label: str, why: str = "no-broker"
) -> Recorded | None:
    """Record a broker-less run's commits by the git window: a row per commit, one per window it couldn't list.
    None when the refs couldn't be read, a commit the run started with is gone, or the ledger couldn't be written;
    never raises, so the integrity check that follows always runs."""
    env = os.environ if env is None else env
    try:
        commits, windows = window(vault, start, env, label=label)
        key = store.vault_key(vault)
        for c in commits:
            _append({"sha": c.sha, "mode": c.mode, "by": c.by, "vault_key": key, "why": why})
        for w in windows:
            taken = w.taken_at.isoformat() if w.taken_at else None
            win = {"tips": list(w.tips), "known": list(w.known), "taken_at": taken}
            _append({"window": win, "mode": w.mode, "vault_key": key, "why": why})
    except Exception:  # noqa: BLE001 - reported by the caller (exit 12), and the integrity check still runs
        return None
    return Recorded(len(commits), len(windows))

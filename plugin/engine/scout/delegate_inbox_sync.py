"""The inbox step the schedule tick starts (#550 V4b1, spec §7.1b): ``scoutctl inbox sync``.

``inbox pull`` and ``inbox apply`` (V4a) were the owner's to run by hand. The
schedule tick now starts this step on its own, detached, between runs, with
``SCOUT_MODE`` and ``SCOUT_READER`` removed so it runs as the owner (a run
can't host it: a run holds the session lock, and apply refuses runs by design).

In order:

1. **Pull every inbox,** under the inbox lock only: the network part never
   holds the vault's session lock, so a scheduled run is never kept waiting
   on it.
2. **Commit the ledgers.** Each delegate's ``taken.ledger`` (ULIDs only),
   and ``.scout-inbox/agents/.gitignore``, which keeps the copies of what
   agents wrote uncommitted, are committed through
   ``scripts/git-safe-commit.sh``. The copies themselves are never committed.
3. **Apply,** within :data:`APPLY_BUDGET_S` seconds: no new copy is started
   after that, so the session lock is held briefly. A run that fires
   meanwhile waits for it (``run-*.sh`` waits up to ``SCOUT_LOCK_WAIT_SECS``,
   30 minutes by default) and is never skipped.

A live run (the session lock held) means this sync's commits wait for the
next tick (``busy``, not an error). The tick doesn't start a sync within
:data:`SLOT_GUARD` of a slot's time.

**An Agnes-linked vault** (settled with the Agnes sub-coordinator): the
commit-and-apply phase takes the one Repository Writer Lease, as a run does,
under a holder of its own (``laptop:<machine>:inbox-sync-…``, labelled
"inbox-sync"), and gives it back in a ``finally``. When Agnes or another
process holds it (``lease_held``), or Agnes can't grant it
(``lease_unavailable``), the tick commits nothing: never a commit without the
lease. The phase is bounded well under the lease's TTL, so no keepalive.

Agent text never reaches a run: runs see only ids, ops, tags, dates and
commits, from the signed state (``pre-session data``'s ``delegate_changes``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import delegate_apply as da
from scout import delegate_inbox as di
from scout.repo_sync import RepoSyncError, git, headless_gate

APPLY_BUDGET_S = 60.0  # seconds the apply phase may start new work in
SYNC_INTERVAL = dt.timedelta(minutes=15)  # the tick starts a sync at most this often
SLOT_GUARD = dt.timedelta(minutes=5)  # and never this close before a slot's time
STARTED_NAME = "sync.started"  # in the inbox's own folder: when the tick last started a sync (its mtime)
LEDGER_MESSAGE = "inbox: pull (delegate ledgers)"


@dataclass
class SyncResult:
    pulled: list[dict[str, Any]] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    ledger_commit: str | None = None
    skipped: str | None = None  # why commits and applies waited: busy | lease_held | lease_unavailable | no_inbox
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _row(r: Any) -> dict[str, Any]:
    if isinstance(r, RepoSyncError):
        return {"ok": False, "delegate": r.detail.get("delegate"), "code": r.code}
    out: dict[str, Any] = r.to_json()
    return {"ok": True, **out}


def ledger_paths(vault: Path) -> list[str]:
    """The files the ledger commit takes: each delegate's ledger and the copies' ``.gitignore``, plain files only."""
    rels: list[str] = []
    agents = "/".join(di.INBOX_DIR)
    root = vault.joinpath(*di.INBOX_DIR)
    if not root.is_dir():
        return rels
    if da._plain(vault, f"{agents}/{di.COPIES_IGNORE}"):
        rels.append(f"{agents}/{di.COPIES_IGNORE}")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return rels
    for name in names:
        try:
            if not stat.S_ISDIR((root / name).lstat().st_mode):
                continue
        except OSError:  # pragma: no cover - removed between the listing and the check
            continue
        rel = f"{agents}/{name}/{di.LEDGER}"
        if da._plain(vault, rel):
            rels.append(rel)
    return rels


def _uncommitted(vault: Path, rels: list[str]) -> list[str]:
    """Which of ``rels`` differ from HEAD (new or changed)."""
    if not rels:
        return []
    out = git(vault, "status", "--porcelain", "--untracked-files=all", "--", *rels, check=False).stdout
    changed = {line[3:].strip() for line in out.splitlines() if len(line) > 3}
    return [r for r in rels if r in changed]


def commit_ledgers(vault: Path, commit: da.Committer) -> str | None:
    """Commit the ledgers and the copies' ``.gitignore`` that changed; the new HEAD, or None. On a failed commit
    they are unstaged (the next run must not commit them as its own) and left as they are: the ledger only grows,
    and the next sync commits it."""
    rels = _uncommitted(vault, ledger_paths(vault))
    if not rels:
        return None
    before = da._head(vault)
    try:
        return commit(vault, LEDGER_MESSAGE, rels)
    except BaseException as e:
        if da._head(vault) == before:
            git(vault, "reset", "-q", "--", *rels, check=False)
        raise RepoSyncError("commit_failed", f"refused: the ledger commit didn't land ({e})") from e


Lease = Callable[[Path], AbstractContextManager[list[str]]]


@contextmanager
def agnes_lease(vault: Path) -> Iterator[list[str]]:
    """Hold the Agnes Repository Writer Lease for the commit-and-apply phase, when the vault is linked; its notes.
    Raises ``repository_lease_held`` or another :class:`RepoSyncError` (``lease_unavailable``) when it can't."""
    from scout import repo_lease
    from scout.repo_sync import AgnesHost, load_state, load_token

    state = load_state(vault)
    if state is None:
        yield []
        return
    host = AgnesHost(state.agnes_url, load_token(vault))
    run_id = f"inbox-sync-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{os.getpid()}"
    _lease, notes = repo_lease.acquire_for_run(vault, host, run_id=run_id, what="inbox-sync", pid=os.getpid(), wait_s=0)
    try:
        yield notes
    finally:
        try:
            repo_lease.release(vault, host, run_id)
        except RepoSyncError:  # Agnes lets it expire after its TTL; the next sync here clears the file
            pass


def sync(
    vault: Path,
    *,
    env: Mapping[str, str] | None = None,
    gh: di.GhRunner = di._gh,
    commit: da.Committer | None = None,
    now: dt.datetime | None = None,
    budget: float = APPLY_BUDGET_S,
    lease: Lease | None = None,
) -> SyncResult:
    """Pull every inbox, commit the ledgers, and apply what was taken, within ``budget`` seconds of applying."""
    env = os.environ if env is None else env
    headless_gate(env, "Syncing the delegates' inboxes (the schedule tick starts it, outside any run)")
    vault = vault.resolve()
    result = SyncResult()
    if not di.state_path(vault).exists():
        result.skipped = "no_inbox"
        return result
    result.pulled = [_row(r) for r in di.pull_all(vault, gh=gh, env=env)]
    from scout import repo_lease

    held = (lease or agnes_lease)(vault)
    try:
        result.notes += held.__enter__()
    except RepoSyncError as e:  # only the lease's own refusal: never a commit without it
        result.skipped = "lease_held" if e.code == repo_lease.HELD_CODE else "lease_unavailable"
        result.notes.append(f"the Agnes repo lease wasn't granted ({e.code}): nothing was committed or applied")
        return result
    try:
        deadline = time.monotonic() + budget
        try:
            with da._session(vault), di.locked(vault):
                result.ledger_commit = commit_ledgers(vault, commit or da._default_commit)
        except RepoSyncError as e:
            if e.code != "busy":
                raise
            result.skipped = "busy"
            result.notes.append("a run is live: the ledger commit and apply wait for the next tick")
            return result
        result.applied = [_row(r) for r in da.apply_all(vault, now=now, env=env, commit=commit, deadline=deadline)]
    finally:
        held.__exit__(None, None, None)
    return result


# ----- the tick's side ------------------------------------------------------------------------


def _started_path(vault: Path) -> Path:
    return vault.joinpath(*di.STATE_DIR, STARTED_NAME)


def due(vault: Path, *, now: dt.datetime | None = None, slot_times: list[dt.datetime] | None = None) -> bool:
    """Whether the tick should start a sync: an inbox is linked, none started in :data:`SYNC_INTERVAL`, and no
    slot's time is within :data:`SLOT_GUARD` ahead (``slot_times``: today's targets, in the tick's zone)."""
    if not di.state_path(vault).exists():
        return False
    now = now or dt.datetime.now(dt.UTC)
    for target in slot_times or []:
        if now <= target <= now + SLOT_GUARD:
            return False
    try:
        started = dt.datetime.fromtimestamp(_started_path(vault).stat().st_mtime, dt.UTC)
    except OSError:
        return True
    return now - started >= SYNC_INTERVAL


def mark_started(vault: Path) -> None:
    """Stamp the tick's start of a sync, in the inbox's own verified folder (never through a symlink)."""
    dir_fd = di._state_dir(vault, create=True)
    assert dir_fd is not None
    try:
        di._write_new(dir_fd, STARTED_NAME, b"", mode=0o600)
    finally:
        os.close(dir_fd)


# ----- what health and the doctor show --------------------------------------------------------

WAITING = (None, "held", "limited", "dirty", "no_mirror", "not_taken")  # taken, not settled: for the owner or a retry


def summary(vault: Path, *, now: dt.datetime | None = None) -> dict[str, dict[str, Any]]:
    """Each linked inbox at a glance, from the signed state: ids, counts and times only, never agent text.
    Raises :class:`RepoSyncError` when the state doesn't verify (health and the doctor say so)."""
    state = di.load_state(vault)
    out: dict[str, dict[str, Any]] = {}
    for delegate, link in sorted(state.inboxes.items()):
        taken = state.taken.get(delegate, {})
        applied = state.applied.get(delegate, {})
        outcomes: dict[str, int] = {}
        for entry in applied.values():
            key = str(entry.get("outcome"))
            outcomes[key] = outcomes.get(key, 0) + 1
        waiting = [u for u in taken if applied.get(u, {}).get("outcome") in WAITING]
        oldest = min((str(taken[u].get("at", "")) for u in waiting), default=None)
        out[delegate] = {
            "linked_at": link.linked_at,
            "last_pull": link.last_pull,
            "last_error": (link.last_error or {}).get("code"),
            "outcomes": dict(sorted(outcomes.items())),
            "waiting": len(waiting),
            "oldest_waiting": oldest,
            "untaken": _untaken(vault, delegate, taken),
        }
    return out


def _untaken(vault: Path, delegate: str, taken: Mapping[str, Any]) -> int:
    """Copy-named files in the delegate's folder that no pull took: something else put them there."""
    from scout import delegate_proposals as dp

    folder = vault.joinpath(*di.INBOX_DIR, delegate)
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    return sum(1 for n in names if n.endswith(".json") and dp.ULID_RE.match(n[:-5]) and n[:-5] not in taken)

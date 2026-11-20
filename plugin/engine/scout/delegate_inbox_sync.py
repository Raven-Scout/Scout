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
    settled: list[str] = field(default_factory=list)  # interrupted changes this sync committed for their delegate
    skipped: str | None = (
        None  # why commits/applies waited: busy | lease_held | lease_unavailable | lease_lost | no_inbox
    )
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


@dataclass
class HeldLease:
    """What :func:`agnes_lease` yields: the lease's notes, and a renew for between the phase's steps."""

    notes: list[str] = field(default_factory=list)
    renew: Callable[[], None] = lambda: None


Lease = Callable[[Path], AbstractContextManager[HeldLease]]


@contextmanager
def agnes_lease(vault: Path) -> Iterator[HeldLease]:
    """Hold the Agnes Repository Writer Lease for the commit-and-apply phase, when the vault is linked.

    Raises ``repository_lease_held`` or another :class:`RepoSyncError` (``lease_unavailable``) when it can't. The
    lease is renewed once, between the ledger commit and apply. The phase starts no new work after
    :data:`APPLY_BUDGET_S`, so it ends at most one commit's timeout (``retitle.COMMIT_TIMEOUT_S``, 5 minutes)
    past that; a TTL shorter than that could expire under a hung commit, which writes nothing to Agnes (this
    phase never pushes). On the way out the lease file here is forgotten first, then Agnes is asked to release
    it: a release that hangs or fails leaves nothing here naming the sync, and Agnes ends the lease at its TTL.
    """
    from scout import repo_lease
    from scout.repo_sync import AgnesHost, load_state, load_token

    state = load_state(vault)
    if state is None:
        yield HeldLease()
        return
    host = AgnesHost(state.agnes_url, load_token(vault))
    run_id = f"inbox-sync-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{os.getpid()}"
    lease, notes = repo_lease.acquire_for_run(vault, host, run_id=run_id, what="inbox-sync", pid=os.getpid(), wait_s=0)

    def renew() -> None:
        repo_lease.renew(vault, host, run_id)

    try:
        yield HeldLease(notes=notes, renew=renew)
    finally:
        repo_lease.forget(vault, lease.token)
        try:
            host.lease_release(lease.token)
        except RepoSyncError:  # Agnes ends it at its TTL
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

    holding = (lease or agnes_lease)(vault)
    try:
        held = holding.__enter__()
        result.notes += held.notes
    except RepoSyncError as e:  # only the lease's own refusal: never a commit without it
        result.skipped = "lease_held" if e.code == repo_lease.HELD_CODE else "lease_unavailable"
        result.notes.append(f"the Agnes repo lease wasn't granted ({e.code}): nothing was committed or applied")
        return result
    try:
        deadline = time.monotonic() + budget
        try:
            with da._session(vault), di.locked(vault):
                result.ledger_commit = commit_ledgers(vault, commit or da._default_commit)
                result.settled, unsettled = settle_interrupted(vault, commit or da._default_commit)
                result.notes += unsettled
        except RepoSyncError as e:
            if e.code != "busy":
                raise
            result.skipped = "busy"
            result.notes.append("a run is live: the ledger commit and apply wait for the next tick")
            return result
        try:
            held.renew()
        except RepoSyncError as e:
            result.skipped = "lease_lost"
            result.notes.append(f"the Agnes repo lease couldn't be renewed ({e.code}): nothing was applied")
            return result
        result.applied = [_row(r) for r in da.apply_all(vault, now=now, env=env, commit=commit, deadline=deadline)]
    finally:
        holding.__exit__(None, None, None)
    return result


def _op_result(vault: Path, entry: Mapping[str, Any], head_text: str) -> list[str] | None:
    """The task's lines after re-running ``entry``'s op on HEAD's daily file, in a scratch folder (with the vault's
    config and id map, which the op reads); None when the op can't be re-run."""
    import shutil
    import tempfile

    rel = da._daily_rel(entry["date"])
    with tempfile.TemporaryDirectory(prefix="scout-inbox-settle-") as tmp:
        scratch = Path(tmp)
        (scratch / "action-items").mkdir()
        (scratch / rel).write_text(head_text, encoding="utf-8")
        for extra in ("scout-config.yaml", da.ID_MAP_REL):
            if da._plain(vault, extra):
                (scratch / extra).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(vault / extra, scratch / extra)
        body = {"op": entry["op"], "tag": entry["tag"], "date": entry["date"], "args": entry["args"]}
        try:
            da._run_op(scratch, body)
        except Exception:  # noqa: BLE001 - an op that can't be re-run here settles nothing
            return None
        return da._task_lines(scratch / rel, entry["tag"])


def settle_interrupted(vault: Path, commit: da.Committer) -> tuple[list[str], list[str]]:
    """Commit, under its delegate's name, a change an apply made but a crash kept from committing; (ULIDs settled,
    notes for the rest). Only when today's daily file differs from HEAD in that task's block alone: the next run's
    commit would otherwise take it as the run's own. Anything else is left for the owner, with a note."""
    state = di.load_state(vault)
    settled: list[str] = []
    notes: list[str] = []
    today = da._today(vault)
    for delegate, applied in sorted(state.applied.items()):
        for ulid, entry in sorted(applied.items()):
            if entry.get("outcome") != "interrupted" or entry.get("date") != today or "before" not in entry:
                continue
            rel = da._daily_rel(today)
            if not da._plain(vault, rel) or da._clean(vault, rel):
                continue  # the op never wrote, or its change is committed
            shown = git(vault, "show", f"HEAD:{rel}", check=False)
            work = (vault / rel).read_text(encoding="utf-8")
            after = da._task_lines(vault / rel, entry["tag"])
            head_item = da._task(shown.stdout, entry["tag"]) if shown.returncode == 0 else None
            only_this = False
            if after is not None and head_item is not None and "args" in entry:
                lines = shown.stdout.splitlines()
                start, end = da._block(lines, head_item.line_number)
                # Exactly the op's own change: HEAD still holds the task as the op found it, the working file holds
                # what the op makes of it (re-run on a scratch copy), and nothing else in the file differs. An edit
                # the owner or a run made since is never committed in a delegate's name.
                if lines[start:end] == entry["before"] and after == _op_result(vault, entry, shown.stdout):
                    lines[start:end] = after
                    only_this = "\n".join(lines) == "\n".join(work.splitlines())
            if not only_this:
                notes.append(
                    f"an interrupted change by {delegate} to {rel} is uncommitted, and the file isn't just that "
                    "change; check it"
                )
                continue
            message = f"inbox: {entry['op']} [#{entry['tag']}] from delegate-{delegate} ({ulid}), after an interruption"
            before = da._head(vault)
            try:
                sha = commit(vault, message, [rel])
            except BaseException as e:
                if da._head(vault) == before:
                    git(vault, "reset", "-q", "--", rel, check=False)
                notes.append(f"couldn't commit the interrupted change by {delegate} ({type(e).__name__})")
                continue
            applied[ulid] = {**entry, "outcome": "applied", "after": after, "commit": sha, "settled": True}
            settled.append(ulid)
    if settled:
        di.save_state(vault, state)
    return settled, notes


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

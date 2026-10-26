"""The Repository Writer Lease on this computer (Agnes slice R2).

Agnes spec: "Scout in Agnes" (2026-10-20), §5 lease, §8.8 laptop and Agnes
together, §15 R2. Agnes lets one place at a time write a person's Personal
Knowledge Repository: this computer or a run in Agnes. A linked vault's
scheduled run takes the lease before it writes anything, renews it while it
runs and gives it back on every exit; when Agnes holds it (an Agnes-hosted run)
or can't be reached, the run is skipped and recorded instead.

**One lease file per vault**, ``~/.scout-secrets/agnes-knowledge-repo/<vault-id>.lease.json``,
next to the PAT: mode 0600 in a 0700 folder, outside the vault, so it is never
committed or synced. It holds the lease token, which never goes on a command
line: the runner names its run (``--run-id``) and the engine reads and writes
the token itself.

**The token rule.** Agnes rotates the token when the same holder acquires
again, which ends the old token. So nothing on this computer acquires a lease
another process here already holds:

- a ``scoutctl repo sync`` during a scheduled run pushes with the run's token,
  read from the lease file, and leaves the lease to the run;
- otherwise sync and link take a lease of their own (holder ``…:sync``) and
  give it back when they are done;
- a run that finds a manual sync's lease waits for it, briefly.

**A run killed before it releases.** The keepalive renews the lease every five
minutes and watches the runner's PID: when the runner is gone it releases the
lease. If the keepalive dies with it, the next run or sync on this computer
releases the dead run's lease from the lease file, and Agnes lets it expire
after its TTL in any case.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import repo_sync
from scout.repo_sync import Host, RepoSyncError
from scout.sessions.cli_home import pid_alive

SCHEMA_VERSION = 1
LEASE_SUFFIX = ".lease.json"
MACHINE_ID_FILE = "machine.id"
LOST_CODES = frozenset({"repository_lease_lost", "repository_lease_not_held"})
HELD_CODE = "repository_lease_held"
RUN_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,120}")

RENEW_SECS_ENV = "SCOUT_LEASE_RENEW_SECS"
POLL_SECS_ENV = "SCOUT_LEASE_POLL_SECS"
SYNC_WAIT_SECS_ENV = "SCOUT_LEASE_SYNC_WAIT_SECS"
DEFAULT_RENEW_SECS = 300
DEFAULT_POLL_SECS = 5
DEFAULT_SYNC_WAIT_SECS = 120
RENEW_RETRY_SECS = 60


@dataclass
class LocalLease:
    owner: str
    run_id: str
    pid: int
    holder_ref: str
    holder_label: str
    token: str
    generation: int
    acquired_at: str | None = None
    expires_at: str | None = None
    took_over: bool = False
    lost: bool = False
    schema_version: int = SCHEMA_VERSION

    def public(self) -> dict[str, Any]:
        """Everything but the token, for ``--json`` and logs."""
        out = dataclasses.asdict(self)
        out.pop("token")
        return out


def env_secs(name: str, default: int, env: dict[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get(name, "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def lease_path(vault: Path) -> Path:
    token = repo_sync.token_path(vault)
    return token.with_name(token.name.removesuffix(".token") + LEASE_SUFFIX)


def load(vault: Path) -> LocalLease | None:
    p = lease_path(vault)
    try:
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        return None
    if mode & 0o077:
        raise RepoSyncError("lease_file_insecure", f"{p} has permissions {oct(mode)}; run chmod 600 {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("schema")
        return LocalLease(**data)
    except (OSError, ValueError, TypeError) as e:
        raise RepoSyncError("lease_file_unreadable", f"{p} can't be read ({e}); delete it and try again") from e


def save(vault: Path, lease: LocalLease) -> None:
    p = lease_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dataclasses.asdict(lease), indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def forget(vault: Path, token: str) -> None:
    """Remove the lease file, but only while it still holds *token*."""
    try:
        current = load(vault)
    except RepoSyncError:
        return
    if current is not None and current.token == token:
        lease_path(vault).unlink(missing_ok=True)


def machine_ref() -> str:
    """A random id for this computer, kept next to the leases.

    The host name is no id: macOS renames it with the network it joins, and
    a run's holder ref must stay the same for the whole run.
    """
    p = Path.home() / ".scout-secrets" / "agnes-knowledge-repo" / MACHINE_ID_FILE
    try:
        found = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        found = ""
    if re.fullmatch(r"[0-9a-f]{12}", found):
        return found
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    new = secrets.token_hex(6)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new + "\n")
    try:
        os.link(tmp, p)
    except FileExistsError:
        return machine_ref()
    finally:
        tmp.unlink(missing_ok=True)
    return new


def machine_label() -> str:
    return socket.gethostname().split(".")[0] or "this computer"


def run_holder_ref(run_id: str) -> str:
    return f"laptop:{machine_ref()}:{run_id}"


def sync_holder_ref() -> str:
    return f"laptop:{machine_ref()}:sync"


def _from_grant(grant: dict[str, Any], *, owner: str, run_id: str, pid: int, ref: str, label: str) -> LocalLease:
    token = grant.get("token")
    if not isinstance(token, str) or not token:
        raise RepoSyncError("host_error", "Agnes granted a lease without a token")
    return LocalLease(
        owner=owner,
        run_id=run_id,
        pid=pid,
        holder_ref=ref,
        holder_label=label,
        token=token,
        generation=int(grant.get("generation") or 0),
        acquired_at=_text(grant.get("acquired_at")),
        expires_at=_text(grant.get("expires_at")),
        took_over=bool(grant.get("took_over")),
    )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def holder_text(e: RepoSyncError) -> str:
    holder = e.detail.get("holder") or {}
    kind = holder.get("holder_kind") or "someone"
    label = holder.get("holder_label")
    until = holder.get("expires_at")
    return f"{kind}" + (f" ({label})" if label else "") + (f" until {until}" if until else "")


def clear_stale(vault: Path, host: Host) -> str | None:
    """Release the lease a dead process on this computer left behind, from the lease file.

    A process killed before it released (SIGKILL, a crash, a reaped run) leaves
    its lease file. Its token is still the current one unless the lease expired
    or was taken over, so releasing with it frees the lease now rather than at
    the end of the TTL. When Agnes can't be reached the file stays, marked dead,
    and the next caller tries again.
    """
    local = load(vault)
    if local is None or pid_alive(local.pid):
        return None
    try:
        host.lease_release(local.token)
    except RepoSyncError as e:
        if e.code not in LOST_CODES:
            raise
    forget(vault, local.token)
    return f"released the lease a stopped {local.owner} left behind ({local.run_id}, pid {local.pid})"


def acquire_for_run(
    vault: Path,
    host: Host,
    *,
    run_id: str,
    what: str,
    pid: int,
    wait_s: int = DEFAULT_SYNC_WAIT_SECS,
    poll_s: int = DEFAULT_POLL_SECS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[LocalLease, list[str]]:
    """Take the lease for one scheduled run and record it in the lease file.

    Raises ``repository_lease_held`` when Agnes (or a manual sync here that
    outlasts *wait_s*) holds it; any other :class:`RepoSyncError` means Agnes
    can't grant it now.
    """
    notes: list[str] = []
    deadline = clock() + wait_s
    while True:
        note = clear_stale(vault, host)
        if note:
            notes.append(note)
        local = load(vault)
        if local is None:
            break
        if local.owner == "run" and local.run_id == run_id:
            return local, notes
        if clock() >= deadline:
            raise RepoSyncError(
                HELD_CODE,
                f"refused: a {local.owner} on this computer (pid {local.pid}) still holds the lease",
                {"holder": {"holder_kind": "laptop", "holder_label": local.holder_label}},
            )
        sleep(poll_s)
    ref = run_holder_ref(run_id)
    label = f"{machine_label()} ({what})"
    lease = _from_grant(host.lease_acquire(ref, label), owner="run", run_id=run_id, pid=pid, ref=ref, label=label)
    save(vault, lease)
    return lease, notes


def renew(vault: Path, host: Host, run_id: str) -> LocalLease:
    local = load(vault)
    if local is None or local.run_id != run_id:
        raise RepoSyncError("lease_not_ours", f"no lease for run {run_id} on this computer")
    try:
        state = host.lease_renew(local.token)
    except RepoSyncError as e:
        if e.code in LOST_CODES:
            local.lost = True
            save(vault, local)
        raise
    local.expires_at = _text(state.get("expires_at")) or local.expires_at
    save(vault, local)
    return local


def release(vault: Path, host: Host, run_id: str) -> bool:
    """Give back run *run_id*'s lease. False when there was none to give back.

    A lease Agnes already ended (expired, taken over, force-released) counts as
    given back. When Agnes can't be reached the file stays, marked dead, so the
    next run or sync here releases it; the error is raised.
    """
    local = load(vault)
    if local is None or local.run_id != run_id:
        return False
    try:
        host.lease_release(local.token)
    except RepoSyncError as e:
        if e.code not in LOST_CODES:
            local.pid = 0
            save(vault, local)
            raise
    forget(vault, local.token)
    return True


def keepalive(
    vault: Path,
    host: Host,
    *,
    run_id: str,
    parent_pid: int,
    renew_s: int = DEFAULT_RENEW_SECS,
    poll_s: int = DEFAULT_POLL_SECS,
    log: Callable[[str], None] = lambda _m: None,
    stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Renew run *run_id*'s lease every *renew_s* while *parent_pid* (the runner) lives.

    Returns why it stopped: ``released`` (the runner died without releasing, so
    this did), ``release_failed`` (that release didn't reach Agnes), ``gone``
    (the runner released it), ``lost`` (Agnes ended it) or ``stopped`` (asked
    to stop while the runner lives).
    """
    due = clock() + renew_s
    while True:
        if not pid_alive(parent_pid):
            try:
                if release(vault, host, run_id):
                    log(f"lease keepalive: runner {parent_pid} is gone; released run {run_id}'s lease")
                    return "released"
            except RepoSyncError as e:
                log(f"lease keepalive: runner {parent_pid} is gone; release failed ({e.code}), it expires on Agnes")
                return "release_failed"
            return "gone"
        if stop():
            return "stopped"
        local = load(vault)
        if local is None or local.run_id != run_id:
            return "gone"
        if clock() >= due:
            try:
                renewed = renew(vault, host, run_id)
                log(f"lease keepalive: renewed run {run_id}'s lease until {renewed.expires_at}")
                due = clock() + renew_s
            except RepoSyncError as e:
                if e.code in LOST_CODES:
                    log(f"lease keepalive: Agnes ended run {run_id}'s lease ({e.code}); its pushes are refused now")
                    return "lost"
                log(f"lease keepalive: renew failed ({e.code}: {e.message}); retrying")
                due = clock() + min(RENEW_RETRY_SECS, renew_s)
        sleep(poll_s)


@contextmanager
def for_push(vault: Path, host: Host) -> Iterator[tuple[str, int]]:
    """The ``(token, generation)`` a sync or link pushes with.

    During a live scheduled run that is the run's own lease, which this leaves
    alone: acquiring again would rotate the token and end the run's lease.
    Otherwise this takes a lease of its own and releases it afterwards.
    """
    local = load(vault)
    if local is not None and pid_alive(local.pid):
        if local.owner == "run":
            if local.lost:
                raise RepoSyncError(
                    "repository_lease_lost",
                    f"refused: the scheduled run on this computer ({local.run_id}) lost its lease; "
                    "sync again after it ends",
                )
            yield local.token, local.generation
            return
        raise RepoSyncError(
            HELD_CODE,
            f"refused: another scoutctl repo sync on this computer (pid {local.pid}) is pushing; try again "
            "when it ends",
        )
    clear_stale(vault, host)
    ref = sync_holder_ref()
    label = f"{machine_label()} (scoutctl repo sync)"
    pid = os.getpid()
    lease = _from_grant(
        host.lease_acquire(ref, label), owner="sync", run_id=f"sync-{pid}", pid=pid, ref=ref, label=label
    )
    save(vault, lease)
    try:
        yield lease.token, lease.generation
    finally:
        try:
            host.lease_release(lease.token)
        except RepoSyncError:
            pass
        forget(vault, lease.token)

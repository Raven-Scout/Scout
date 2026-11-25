"""The owner-side state record of a detached experiment run (spec §6.4).

A detached run's supervisor (:mod:`scout.lab.supervise`) writes one JSON record per experiment in the owner's
run-broker folder, ``lab-runs/<vault hash>-<slug>-<EID>.json`` (folder 0700, file 0600), outside every path a
scheduled run or an experiment's script may write:

- ``started``, ``deadline`` (ISO times) and ``pid`` (the supervisor's) as soon as it holds the experiment's run lock;
- ``probe`` (the sweep's probe file) and ``marker`` (the folder the script's sandbox may not write: the sweep's
  ``outside``) once the gate's kernel checks pass, before the script starts;
- ``outcome`` last, before the supervisor lets the run lock go.

**Alive** means the supervisor still holds the experiment's run lock (:func:`lock_held`, a non-blocking ``flock``
from outside) and its ``pid`` still exists. The lock goes with the process however it ends, so a pid the system
reused for something else never counts: no lock, no supervisor. A record with no outcome whose supervisor isn't
alive was left by a supervisor that stopped (a restart, a kill), and the next collect sweeps it.

The record never holds a secret: the meter has no token, and the supervisor's inputs go in a file of their own
(:func:`inputs_path`), removed once read.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scout.errors import LabError
from scout.lab.model import EID_RE, SLUG_RE
from scout.sandbox import broker

RUNS_DIR = "lab-runs"
LEDGER_DIR = "lab-meter"  # the runner's folder for the ledger and the run lock (scout.lab.runner.LEDGER_DIR)
# The supervisor's SIGTERM is the owner's stop only when this file (written by `lab experiment stop`) exists.
STOP_SUFFIX = ".stop"
INPUTS_SUFFIX = ".in.json"
LOG_SUFFIX = ".log"
# Signals sent to a supervisor go through here, so a test can stand in for it.
_kill = os.kill


@dataclass(frozen=True)
class RunRecord:
    started: str
    deadline: str
    pid: int
    probe: str | None = None
    marker: str | None = None
    outcome: dict[str, Any] | None = None


def vault_key(vault: Path) -> str:
    """The vault's hash, as the runner names its ledger and lock files."""
    return hashlib.sha256(os.path.realpath(vault).encode("utf-8")).hexdigest()[:16]


def _stem(vault: Path, slug: str, eid: str) -> str:
    if not re.match(SLUG_RE, slug) or not re.fullmatch(EID_RE, eid):
        raise LabError(f"not an experiment: {slug!r} {eid!r}")
    return f"{vault_key(vault)}-{slug}-{eid}"


def runs_dir(env: Mapping[str, str]) -> Path:
    return broker.owner_dir(env) / RUNS_DIR


def record_path(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Path:
    """``owner_dir(env)/lab-runs/<vault hash>-<slug>-<EID>.json``. Nothing is created here: :func:`write` makes the
    folder (0700)."""
    return runs_dir(env) / f"{_stem(vault, slug, eid)}.json"


def _beside(record: Path, suffix: str) -> Path:
    return record.with_name(record.name.removesuffix(".json") + suffix)


def inputs_path(record: Path) -> Path:
    """The supervisor's inputs, beside its record (0600; removed by the supervisor once read)."""
    return _beside(record, INPUTS_SUFFIX)


def log_path(record: Path) -> Path:
    """The supervisor's own stdout and stderr, beside its record."""
    return _beside(record, LOG_SUFFIX)


def stop_path(record: Path) -> Path:
    """The owner's stop request, beside the record (:func:`request_stop`)."""
    return _beside(record, STOP_SUFFIX)


def lock_path(vault: Path, slug: str, eid: str, env: Mapping[str, str]) -> Path:
    """The experiment's run lock (:func:`scout.lab.runner.run_lock_path`), named without creating anything."""
    return broker.owner_dir(env) / LEDGER_DIR / f"{_stem(vault, slug, eid)}.lock"


def ensure_dir(env: Mapping[str, str]) -> Path:
    folder = runs_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def read(path: Path) -> RunRecord | None:
    """The record at ``path``; None when there is none, or it isn't one (unreadable, malformed)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid, outcome = data.get("pid"), data.get("outcome")
    texts = [data.get(k) for k in ("started", "deadline")]
    optional = [data.get(k) for k in ("probe", "marker")]
    if (
        not all(isinstance(t, str) for t in texts)
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 1
        or not all(v is None or isinstance(v, str) for v in optional)
        or not (outcome is None or isinstance(outcome, dict))
    ):
        return None
    return RunRecord(str(texts[0]), str(texts[1]), pid, optional[0], optional[1], outcome)


def write(path: Path, record: RunRecord) -> None:
    """Write ``record`` to ``path`` atomically (a temp file beside it, then a rename), 0600; the folder 0700."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".rec-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(record), fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def lock_held(lock: Path) -> bool:
    """Whether another open file holds ``lock`` (a non-blocking exclusive ``flock`` fails). Nothing is created: a
    missing lock file is not held. The test takes the lock for an instant when it is free."""
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _pid_exists(pid: int) -> bool:
    try:
        _kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def alive(vault: Path, slug: str, eid: str, record: RunRecord, env: Mapping[str, str]) -> bool:
    """The record's supervisor is still running: it holds the experiment's run lock and its pid exists. The lock is
    looked at first, so no pid is signalled (even with 0) unless a process holds the lock."""
    return lock_held(lock_path(vault, slug, eid, env)) and _pid_exists(record.pid)


def _experiments(vault: Path, env: Mapping[str, str]) -> list[tuple[str, str, Path]]:
    """(slug, EID, record path) for every record of this vault's."""
    prefix = vault_key(vault) + "-"
    try:
        names = sorted(os.listdir(runs_dir(env)))
    except OSError:
        return []
    out: list[tuple[str, str, Path]] = []
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".json") or name.endswith(INPUTS_SUFFIX):
            continue
        slug, _, eid = name[len(prefix) : -len(".json")].rpartition("-")
        if re.match(SLUG_RE, slug) and re.fullmatch(EID_RE, eid):
            out.append((slug, eid, runs_dir(env) / name))
    return out


def live(vault: Path, env: Mapping[str, str]) -> list[RunRecord]:
    """This vault's records with no outcome whose supervisor is alive (:func:`alive`)."""
    out: list[RunRecord] = []
    for slug, eid, path in _experiments(vault, env):
        rec = read(path)
        if rec is not None and rec.outcome is None and alive(vault, slug, eid, rec, env):
            out.append(rec)
    return out


def request_stop(vault: Path, slug: str, eid: str, env: Mapping[str, str], author: str) -> int:
    """The owner's stop (``lab experiment stop``): write the stop request beside the record, then SIGTERM the
    supervisor, which kills the script and records ``stopped by the owner``. Returns the supervisor's pid. Raises
    :class:`LabError` when no detached run of the experiment is alive."""
    path = record_path(vault, slug, eid, env)
    rec = read(path)
    if rec is None:
        raise LabError(f"no detached run of {slug} {eid} was started")
    if rec.outcome is not None:
        raise LabError(f"the detached run of {slug} {eid} has already ended; `lab experiment collect` reads it")
    if not alive(vault, slug, eid, rec, env):
        raise LabError(f"the supervisor of {slug} {eid} isn't running; `lab experiment collect` reports it")
    stop = stop_path(path)
    fd = os.open(stop, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"by": author, "at": datetime.now().astimezone().isoformat()}, fh)
    _kill(rec.pid, signal.SIGTERM)
    return rec.pid


def hhmm(seconds: float) -> str:
    """``HhMm`` (``1h05m``): whole minutes, never negative."""
    minutes = max(int(seconds // 60), 0)
    return f"{minutes // 60}h{minutes % 60:02d}m"


def parse_time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None

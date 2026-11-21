"""The scheduled runs' commit broker and send-only Telegram broker (Phase 1d, spec 2026-11-16).

Since Phase 1c a run executes inside a sandbox-exec profile, but two carve-outs were left for work the run did
in-session: git could write the vault's git dir (so a run could commit a protected file through git plumbing),
and the Telegram token was readable (so a run could send anything to anyone the bot reaches). Here the run asks
and a trusted process outside the sandbox does, after checking the request; then the profile denies every write
in the vault's git dir and every read in the secrets dirs (:func:`scout.sandbox.profile.broker_rules`).

- ``claude-with-retry.sh`` starts :func:`serve` outside the sandbox, one per run, on a unix socket in a fresh
  0700 folder, and asks :func:`build_profile` for the tightened profile. It exports :data:`ENV_SOCKET`.
- In-session, ``scripts/git-safe-commit.sh`` hands over to :func:`client_commit` and ``scoutctl notify telegram``
  to :func:`client_telegram`. They print what the broker's reply says and exit with its code, so a run sees what
  it saw before: ``committed <sha>`` back during the session, the same exit codes.
- The broker validates (:func:`validate_commit`, :func:`validate_telegram`) and then runs the vault's own
  ``git-safe-commit.sh`` (its mutex, the pre-commit hook) with a fixed argv and a clean environment, or calls
  :func:`scout.scripts.notify_telegram.send`, which reads the recipient from the owner's files.
- When the broker doesn't answer, the client writes the request to a queue in the vault (:func:`enqueue`) and
  :func:`drain` applies it after the session, validated exactly like a live request.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import io
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scout import safe_write
from scout.kb import hook_paths

PROTOCOL = 1
ENV_SOCKET = "SCOUT_RUN_BROKER"
SOCKET_NAME = "b.sock"
QUEUE_DIR = Path(".scout-state") / "broker-queue"
FAILED_DIR = "failed"
VERBS = ("commit", "telegram")
# Where the wrapper and `broker profile` say whether this run's brokers are on, next to .claude-sign-in.json.
STATUS_FILE = ".run-broker.json"
# What `broker profile` looks for in the vault's helper: a helper without its client mode would commit directly,
# which the tightened profile refuses (spec §5.1). Such a vault runs as Phase 1c until /scout-update.
CLIENT_MARKER = ENV_SOCKET

MAX_REQUEST_BYTES = 1 << 20
MAX_REPLY_BYTES = 128 << 10  # what a client reads at most: two outputs of MAX_OUTPUT_CHARS, 4 bytes a character
MAX_OUTPUT_CHARS = 12_000  # each of stderr and stdout in a reply, the tail kept
CONNECT_TIMEOUT_S = 5.0
COMMIT_TIMEOUT_S = 300.0
TELEGRAM_TIMEOUT_S = 120.0
REQUEST_READ_TIMEOUT_S = 30.0
CLIENT_SLACK_S = 30.0
SOCKET_WAIT_S = 10.0
PARENT_POLL_S = 2.0

EXIT_REFUSED = 2  # git-safe-commit's usage code: the request breaks the rules
EXIT_LIMIT = 5  # notify telegram: refused by the broker's limits
EXIT_QUEUED = 6  # git-safe-commit: queued, it lands after the session (no SHA yet)

MAX_MESSAGE_CHARS = 10_000
MAX_PATHS = 500
MAX_PATH_CHARS = 4096
MAX_OVERRIDE_CHARS = 500
MAX_COVERS = 100  # inbound message ids one Telegram reply may answer
BLANKET = frozenset({"-A", "--all", ".", "./", ":/", ":"})

QUEUE_MAX_AGE = timedelta(hours=24)
QUEUE_MAX_ATTEMPTS = 10
QUEUE_MAX_ENTRY_BYTES = MAX_REQUEST_BYTES
# A drain stops starting entries after this; the rest wait for the next drain (the wrapper's exit holds the run lock).
DRAIN_DEADLINE_S = 120.0
# What a queue entry's file name may be (the client writes ULIDs); anything else is moved to failed/ unread.
_ENTRY_NAME = re.compile(r"^[0-9A-Za-z_-]{1,64}\.json$")
# A run's id, its broker folder's name (``scout-broker.XXXXXX``), and a request's id (a ULID).
_RUN_ID = re.compile(r"^scout-broker\.[0-9A-Za-z_]{1,32}$")
_REQUEST_ID = re.compile(r"^[0-9A-Za-z]{1,64}$")
# Exit codes after which a queued entry stays queued for the next drain: the commit mutex was busy, git or a hook
# failed (a lock not provably stale), the network or Telegram failed, the secrets are missing.
_TRANSIENT = {"commit": {1, 3, 5}, "telegram": {1, 2, 10}}
# Exit codes that mean the entry is done: committed or nothing left to commit; sent or already sent.
_DONE = {"commit": {0, 4}, "telegram": {0, 3}}
# Reply kinds (never sent over the socket) that decide a queued entry's fate before its exit code does.
REFUSED = "refused"  # the request breaks the rules: moved to failed/
RUN_LIMIT = "run_limit"  # over its run's allowance: moved to failed/ (a client never queues past it)
HOUR_LIMIT = "hour_limit"  # the hour is full: stays queued, not counted as an attempt

# The variables a commit runs with (spec §5.3); nothing else reaches git, the helper or the hook.
_COMMIT_ENV_KEYS = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "SCOUT_MODE", "SCOUT_READER")
_DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class BrokerError(Exception):
    """The brokers can't run for this session (no socket, an old helper, a profile that fails its self-test)."""


class Refused(Exception):
    """A request the broker won't carry out: its exit code and the reason the run reads."""

    def __init__(self, exit_code: int, reason: str) -> None:
        super().__init__(reason)
        self.exit_code = exit_code
        self.reason = reason


class Unreachable(Exception):
    """No answer from the broker: nothing listens, or it went away before replying."""


@dataclass
class Reply:
    exit: int
    stderr: str = ""
    stdout: str = ""
    kind: str = ""  # REFUSED | RUN_LIMIT | HOUR_LIMIT; the drain reads it, the socket never carries it

    def to_dict(self) -> dict[str, Any]:
        return {"v": PROTOCOL, "exit": self.exit, "stderr": _tail(self.stderr), "stdout": _tail(self.stdout)}

    @classmethod
    def from_dict(cls, data: Any) -> Reply:
        if not isinstance(data, dict) or not isinstance(data.get("exit"), int) or isinstance(data.get("exit"), bool):
            raise Unreachable("the broker's reply wasn't one")
        return cls(int(data["exit"]), str(data.get("stderr") or ""), str(data.get("stdout") or ""))


def _tail(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else "…" + text[-MAX_OUTPUT_CHARS:]


# -- the owner's files ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """Telegram limits (spec §5.5, Q4): the owner's, never the run's."""

    max_per_run: int = 4
    max_per_hour: int = 12
    max_chars: int = 8000


def owner_dir(env: Mapping[str, str]) -> Path:
    """``~/.local/state/scout/run-brokers/``: the limits, the hourly count and the drain lock. A protected engine
    root (:func:`scout.kb.hook_paths.engine_roots`), so a run can't write it, from either layer."""
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "scout" / hook_paths.RUN_BROKERS_NAME


CONFIG_NAME = "config.json"
# One row per real send: {"at", "run", "rid"}, kept 48 h. The hourly count, each run's count (live and drained
# alike), and which requests already went out (a send whose reply was lost is never sent again from the queue).
TELEGRAM_LOG_NAME = "telegram-sent.jsonl"
TELEGRAM_LOG_KEEP = timedelta(hours=48)
# Locks are write-only files (mode 0200): a run, the same user, can't open them to read, and its sandbox refuses the
# write open, so it can't hold a lock and stall the brokers.
TELEGRAM_LOCK_NAME = "telegram.lock"
DRAIN_LOCK_NAME = "drain.lock"


def load_limits(env: Mapping[str, str]) -> Limits:
    """The owner's limits from ``config.json`` (``{"telegram": {"max_per_run": …}}``); a missing or malformed file,
    or a value that isn't a positive integer, means the default."""
    defaults = Limits()
    try:
        data = json.loads((owner_dir(env) / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return defaults
    block = data.get("telegram") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return defaults

    def pick(name: str) -> int:
        value = block.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 1_000_000:
            return value
        return int(getattr(defaults, name))

    return Limits(pick("max_per_run"), pick("max_per_hour"), pick("max_chars"))


# -- validation ----------------------------------------------------------------------------------------------------


def _control(text: str) -> bool:
    return any(ord(c) < 0x20 or c == "\x7f" for c in text)


def validate_commit(req: Mapping[str, Any], vault: Path, env: Mapping[str, str]) -> tuple[str, list[str], str | None]:
    """(message, paths relative to the vault, override) for a commit request, or :class:`Refused` (exit 2).

    A path must stay inside the vault as spelled and resolved, have no ``.git`` component, and neither be nor hold a
    path :mod:`scout.kb.hook_paths` protects; options, pathspec magic and blanket forms are refused.
    """
    msg = req.get("message")
    if not isinstance(msg, str) or not msg.strip():
        raise Refused(EXIT_REFUSED, "empty commit message")
    if len(msg) > MAX_MESSAGE_CHARS or "\x00" in msg:
        raise Refused(EXIT_REFUSED, f"the commit message must be under {MAX_MESSAGE_CHARS} characters, without NUL")
    override = req.get("override")
    if override is not None and (
        not isinstance(override, str) or len(override) > MAX_OVERRIDE_CHARS or _control(override)
    ):
        raise Refused(EXIT_REFUSED, f"SCOUT_LINT_OVERRIDE must be one line under {MAX_OVERRIDE_CHARS} characters")
    raw = req.get("paths")
    if not isinstance(raw, list) or not raw:
        raise Refused(EXIT_REFUSED, 'usage: git-safe-commit.sh "commit message" <path> [<path> ...]')
    if len(raw) > MAX_PATHS:
        raise Refused(EXIT_REFUSED, f"at most {MAX_PATHS} paths per commit; split it")
    vault_spelled = os.path.normpath(os.path.abspath(str(vault)))
    vault_real = os.path.realpath(str(vault))
    out: list[str] = []
    for p in raw:
        if not isinstance(p, str) or not p:
            raise Refused(EXIT_REFUSED, "an empty path")
        if p in BLANKET:
            raise Refused(EXIT_REFUSED, f"refusing blanket stage ('{p}') — pass explicit file paths")
        if len(p) > MAX_PATH_CHARS or _control(p):
            raise Refused(EXIT_REFUSED, "a path with a control character, or too long")
        if p.startswith(("-", ":")):
            raise Refused(EXIT_REFUSED, f"'{p}': a path can't start with '-' or ':'")
        absolute = p if os.path.isabs(p) else os.path.join(vault_spelled, p)
        spelled = os.path.normpath(absolute)
        real = os.path.realpath(absolute)
        inside = hook_paths._under(hook_paths._norm(spelled), hook_paths._norm(vault_spelled)) and hook_paths._under(
            hook_paths._norm(real), hook_paths._norm(vault_real)
        )
        if not inside:
            raise Refused(EXIT_REFUSED, f"'{p}' is outside the vault")
        rel = spelled[len(vault_spelled) :].lstrip("/")
        if not rel:
            raise Refused(EXIT_REFUSED, f"refusing blanket stage ('{p}') — pass explicit file paths")
        if any(part.casefold() == ".git" for part in (*rel.split("/"), *real[len(vault_real) :].split("/"))):
            raise Refused(EXIT_REFUSED, f"'{p}' is in git's own folder")
        # removes_protected: the path itself, or a folder that holds a protected path (`scripts`): committing a
        # folder would take the owner's uncommitted edits to its protected files with it.
        form = hook_paths.removes_protected(spelled, vault, env) or hook_paths.removes_protected(real, vault, env)
        if form is not None:
            raise Refused(EXIT_REFUSED, f"'{p}' is protected ({form}); a scheduled run doesn't commit it")
        out.append(rel)
    return msg, out, override


def validate_telegram(req: Mapping[str, Any], limits: Limits) -> dict[str, Any]:
    """The send's fields, or :class:`Refused`. The request names no recipient: :func:`send` reads the owner's."""
    from scout.scripts.notify_telegram import VALID_PARSE_MODES, VALID_TIERS, _is_message_id

    tier, parse_mode, body = req.get("tier", "info"), req.get("parse_mode", "none"), req.get("body")
    if tier not in VALID_TIERS:
        raise Refused(1, f"unknown tier {tier!r}; valid tiers are: {', '.join(VALID_TIERS)}")
    if parse_mode not in VALID_PARSE_MODES:
        raise Refused(1, f"unknown parse mode {parse_mode!r}; valid modes are: {', '.join(VALID_PARSE_MODES)}")
    if not isinstance(body, str) or not body:
        raise Refused(1, "body cannot be empty")
    if len(body) > limits.max_chars:
        raise Refused(EXIT_LIMIT, f"the message is {len(body)} characters; the broker sends at most {limits.max_chars}")
    flags = {k: req.get(k, False) for k in ("allow_duplicate", "dry_run")}
    if not all(isinstance(v, bool) for v in flags.values()):
        raise Refused(1, "allow_duplicate and dry_run are true or false")
    # Which inbound messages the reply answers: only their shape here; Broker.telegram checks them against the ledger.
    reply_to, covers = req.get("reply_to"), req.get("covers")
    covers = [] if covers is None else covers
    if reply_to is not None and not _is_message_id(reply_to):
        raise Refused(EXIT_REFUSED, "reply_to is a positive message id")
    if not isinstance(covers, list) or len(covers) > MAX_COVERS or not all(_is_message_id(i) for i in covers):
        raise Refused(EXIT_REFUSED, f"covers is a list of at most {MAX_COVERS} positive message ids")
    if covers and reply_to is None:
        raise Refused(EXIT_REFUSED, "covers needs reply_to (a reply threads to a message)")
    return {"tier": tier, "parse_mode": parse_mode, "body": body, **flags, "reply_to": reply_to, "covers": covers}


# -- carrying it out -----------------------------------------------------------------------------------------------


def commit_env(vault: Path, base: Mapping[str, str], override: str | None) -> dict[str, str]:
    """The commit's whole environment: the broker's own (the wrapper's, which the run can't change), filtered."""
    env = {k: base[k] for k in _COMMIT_ENV_KEYS if base.get(k)}
    env.update({k: v for k, v in base.items() if k.startswith("LC_")})
    env.setdefault("PATH", _DEFAULT_PATH)
    # A broker only ever commits for a scheduled run, so its commits get the scheduled-run checks, fail closed.
    env.setdefault("SCOUT_MODE", "scheduled")
    env.setdefault("SCOUT_READER", "autonomous")
    env["SCOUT_DATA_DIR"] = str(vault)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    if override:
        env["SCOUT_LINT_OVERRIDE"] = override
    return env


def run_commit(
    vault: Path, message: str, paths: Sequence[str], override: str | None, base: Mapping[str, str], *,
    timeout: float = COMMIT_TIMEOUT_S,
) -> Reply:  # fmt: skip
    """``bash <vault>/scripts/git-safe-commit.sh MESSAGE PATHS…`` in the vault: its checks, its mutex, the hook."""
    helper = vault / "scripts" / "git-safe-commit.sh"
    if not helper.is_file():
        return Reply(5, "git-safe-commit: the vault has no scripts/git-safe-commit.sh\n")
    try:
        proc = subprocess.Popen(
            ["/bin/bash", str(helper), message, *paths],
            cwd=str(vault),
            env=commit_env(vault, base, override),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        return Reply(5, f"git-safe-commit: couldn't start the helper ({e})\n")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_group(proc)
        return Reply(5, f"git-safe-commit: the commit took longer than {int(timeout)}s and was stopped\n")
    except BaseException:
        # Stopped (the drain's SIGTERM from the wrapper's watchdog): the helper and its hook are in their own process
        # group, so they would outlive us and the run's lock. Take the group down with us.
        _stop_group(proc)
        raise
    return Reply(proc.returncode, err, out)


def _stop_group(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def send_telegram(fields: Mapping[str, Any], vault: Path | None = None) -> Reply:
    """:func:`scout.scripts.notify_telegram.cli_send` in this process, its output captured. A reply is recorded
    in ``vault``'s inbound ledger (the broker passes its own, so the cwd never decides which)."""
    from scout.scripts import notify_telegram

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = notify_telegram.cli_send(
            tier=fields["tier"],
            text=fields["body"],
            parse_mode=fields["parse_mode"],
            dry_run=fields["dry_run"],
            allow_duplicate=fields["allow_duplicate"],
            echo_out=lambda s: print(s, file=sys.stdout),
            echo_err=lambda s: print(s, file=sys.stderr),
            reply_to=fields.get("reply_to"),
            covers=list(fields.get("covers") or []),
            vault=vault,
        )
    return Reply(code, err.getvalue(), out.getvalue())


@contextlib.contextmanager
def _locked(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """An exclusive flock on ``path``, a write-only (0200) file in the owner folder. Yields False when
    ``blocking`` is off and another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o200)
    try:
        with contextlib.suppress(OSError):
            os.fchmod(fd, 0o200)  # a lock file made by an older build
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_sends(env: Mapping[str, str], now: datetime) -> list[dict[str, str]]:
    """The send log's rows from the last 48 h. Readable to a run (it counts its own sends), never writable."""
    try:
        lines = (owner_dir(env) / TELEGRAM_LOG_NAME).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rows: list[dict[str, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
            at = datetime.fromisoformat(row["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(row, dict) and timedelta(0) <= now - at < TELEGRAM_LOG_KEEP:
            rows.append({k: str(row.get(k) or "") for k in ("at", "run", "rid")})
    return rows


def _write_sends(env: Mapping[str, str], rows: list[dict[str, str]]) -> None:
    path = owner_dir(env) / TELEGRAM_LOG_NAME
    safe_write.atomic_write_text(path, "".join(json.dumps(r) + "\n" for r in rows))


def _hour(rows: list[dict[str, str]], now: datetime) -> int:
    return sum(1 for r in rows if now - datetime.fromisoformat(r["at"]) < timedelta(hours=1))


# Runs the owner side knows: one file per run in the protected owner folder, written by the run's broker as it starts
# and closed by the wrapper's drain as the run ends ({"at", "open", "rids"}). A drain credits a queued Telegram entry
# to its run only through this record; anything else shares UNREGISTERED's single slot an hour.
RUNS_DIR_NAME = "runs"
UNREGISTERED = "unregistered"
UNREGISTERED_PER_HOUR = 1
RUN_RECORD_KEEP = timedelta(hours=24)  # a queued entry's own lifetime


def _run_record(env: Mapping[str, str], run: str) -> Path:
    return owner_dir(env) / RUNS_DIR_NAME / f"{run}.json"


def register_run(
    env: Mapping[str, str],
    run: str,
    *,
    pid: int | None = None,
    folder: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Record ``run`` as open (the live broker, as it starts, with its pid and folder). Prunes records past their
    lifetime."""
    now = now or datetime.now(UTC)
    d = owner_dir(env) / RUNS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in d.glob("scout-broker.*.json"):
        with contextlib.suppress(OSError, ValueError):
            if now - datetime.fromtimestamp(old.lstat().st_mtime, UTC) > 2 * RUN_RECORD_KEEP:
                old.unlink()
    record: dict[str, Any] = {"at": now.isoformat(), "open": True, "rids": []}
    if pid is not None:
        record["pid"] = pid
    if folder is not None:
        record["dir"] = str(folder)
    _write_record(env, run, record)


def close_stale_runs(env: Mapping[str, str], vault: Path) -> list[str]:
    """Close every open run whose broker is gone (its process, or its folder): a wrapper killed with SIGKILL never
    ran its own drain. Like :func:`close_run`, what the run still has queued keeps its allowance. Returns the runs
    closed."""
    closed: list[str] = []
    for path in sorted((owner_dir(env) / RUNS_DIR_NAME).glob("scout-broker.*.json")):
        run = path.name.removesuffix(".json")
        record = _read_record(env, run)
        if record is None or record.get("open") is not True:
            continue
        pid, folder = record.get("pid"), record.get("dir")
        gone = (isinstance(pid, int) and not _alive(pid)) or (isinstance(folder, str) and not Path(folder).is_dir())
        if not gone:
            continue
        try:
            left = [e for e in _queued(vault, "telegram") if e.get("run") == run]
        except (QueueUnsafe, OSError):
            left = []
        close_run(env, run, [e["rid"] for e in left if isinstance(e.get("rid"), str)])
        closed.append(run)
    return closed


def close_run(env: Mapping[str, str], run: str, rids: Sequence[str]) -> None:
    """Close ``run`` (the wrapper's drain, as the run ends): only the Telegram requests still queued now (``rids``)
    keep its allowance; an entry written after this gets none."""
    record = _read_record(env, run)
    if record is None:
        return
    _write_record(env, run, {"at": record["at"], "open": False, "rids": sorted(set(rids))})


def _write_record(env: Mapping[str, str], run: str, data: Mapping[str, Any]) -> None:
    safe_write.atomic_write_text(_run_record(env, run), json.dumps(data))


def _read_record(env: Mapping[str, str], run: str) -> dict[str, Any] | None:
    if not _RUN_ID.match(run):
        return None
    try:
        data = json.loads(_run_record(env, run).read_text(encoding="utf-8"))
        datetime.fromisoformat(data["at"])
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _credited(env: Mapping[str, str], run: str, rid: str, now: datetime) -> bool:
    record = _read_record(env, run)
    if record is None or now - datetime.fromisoformat(record["at"]) > RUN_RECORD_KEEP:
        return False
    rids = record.get("rids")
    return record.get("open") is True or (bool(rid) and isinstance(rids, list) and rid in rids)


def run_id_of(sock_path: str) -> str:
    """The run's id: its broker folder's name. "unknown" when there is none to name."""
    name = Path(sock_path).parent.name if sock_path else ""
    return name if _RUN_ID.match(name) else "unknown"


@dataclass
class Broker:
    """One run's brokers (or one drain's): the commit verb and the Telegram verb, sharing nothing but the process.

    ``run_id`` is the live broker's own run; a drain has none and takes each entry's (a client tags what it
    queues). Each run's Telegram allowance is counted in the owner's send log, so live sends and drained ones
    share it.
    """

    vault: Path
    env: Mapping[str, str]
    limits: Limits = field(default_factory=Limits)
    sender: Callable[[Mapping[str, Any]], Reply] | None = None  # None: :func:`send_telegram` into this vault
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    run_id: str | None = None

    def _send(self, fields: Mapping[str, Any]) -> Reply:
        return self.sender(fields) if self.sender is not None else send_telegram(fields, vault=self.vault)

    @classmethod
    def for_vault(cls, vault: Path, env: Mapping[str, str] | None = None, **kw: Any) -> Broker:
        env = dict(os.environ if env is None else env)
        return cls(vault=vault, env=env, limits=load_limits(env), **kw)

    def handle(self, req: Any) -> Reply:
        if not isinstance(req, dict) or req.get("v") != PROTOCOL:
            return Reply(EXIT_REFUSED, "broker: not a request this broker speaks\n", kind=REFUSED)
        verb = req.get("verb")
        if verb == "ping":
            return Reply(0, "", "pong")
        if verb == "commit":
            return self.commit(req)
        if verb == "telegram":
            return self.telegram(req)
        return Reply(EXIT_REFUSED, "broker: unknown verb\n", kind=REFUSED)

    def commit(self, req: Mapping[str, Any]) -> Reply:
        try:
            message, paths, override = validate_commit(req, self.vault, self.env)
        except Refused as r:
            return Reply(r.exit_code, f"git-safe-commit: {r.reason}\n", kind=REFUSED)
        return run_commit(self.vault, message, paths, override, self.env)

    def telegram(self, req: Mapping[str, Any]) -> Reply:
        try:
            fields = validate_telegram(req, self.limits)
        except Refused as r:
            return Reply(r.exit_code, f"scoutctl notify telegram: {r.reason}\n", kind=REFUSED)
        if fields["reply_to"] is not None:
            # Before any limit: a reply to a message that isn't waiting (unknown, or already answered) is refused
            # for good, live or drained, so a queued reply can't wait out a full hour for a predicted id to arrive.
            # The same check cli_send makes again as it sends.
            from scout.scripts.notify_telegram import EXIT_UNANSWERABLE, unanswerable

            problem = unanswerable(self.vault, [fields["reply_to"], *fields["covers"]])
            if problem is not None:
                code, reason = problem
                return Reply(
                    code, f"scoutctl notify telegram: {reason}\n", kind=REFUSED if code == EXIT_UNANSWERABLE else ""
                )
        if fields["dry_run"]:
            return self._send(fields)
        rid = req["rid"] if isinstance(req.get("rid"), str) and _REQUEST_ID.match(req["rid"]) else ""
        now = self.clock()
        claimed = req["run"] if isinstance(req.get("run"), str) and _RUN_ID.match(req["run"]) else ""
        # The live broker is its run. A drained entry gets a run's allowance only when the owner side registered
        # that run and it is still open, or it closed with this request still queued (close_run): a queue file is
        # the run's to write, so its own `run` field proves nothing.
        if self.run_id:
            run = self.run_id
        elif claimed and _credited(self.env, claimed, rid, now):
            run = claimed
        else:
            run = UNREGISTERED
        cap = UNREGISTERED_PER_HOUR if run == UNREGISTERED else self.limits.max_per_run
        # Held across the send, so two brokers can't both take the hour's last slot or send one request twice.
        with _locked(owner_dir(self.env) / TELEGRAM_LOCK_NAME):
            rows = _read_sends(self.env, now)
            if rid and any(r["rid"] == rid for r in rows):
                return Reply(0, f"scoutctl notify telegram: request {rid} was already sent\n")
            mine = [r for r in rows if r["run"] == run]
            if run == UNREGISTERED:
                mine = [r for r in mine if now - datetime.fromisoformat(r["at"]) < timedelta(hours=1)]
            full = _hour(rows, now) >= self.limits.max_per_hour
            if len(mine) >= cap or (run == UNREGISTERED and full):
                # No run vouches for it: one shared slot an hour, and a full hour fails it rather than keeping it.
                why = (
                    "no open run vouches for this queued message, and its shared slot is used"
                    if run == UNREGISTERED
                    else f"this run has sent its {self.limits.max_per_run} messages; put the rest in the wrap"
                )
                return Reply(EXIT_LIMIT, f"scoutctl notify telegram: {why}\n", kind=RUN_LIMIT)
            if full:
                return Reply(
                    EXIT_LIMIT,
                    f"scoutctl notify telegram: {_hour(rows, now)} messages went out in the last hour, the most "
                    "the owner allows; nothing was sent\n",
                    kind=HOUR_LIMIT,
                )
            reply = self._send(fields)
            if reply.exit == 0:
                _write_sends(self.env, [*rows, {"at": now.isoformat(), "run": run, "rid": rid}])
        return reply


# -- the server ----------------------------------------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_line(conn: socket.socket, cap: int) -> bytes:
    buf = bytearray()
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            raise ValueError("too long")
    return bytes(buf.split(b"\n", 1)[0])


def _check_dir(broker_dir: Path) -> None:
    st = broker_dir.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise BrokerError(f"{broker_dir} must be a folder of this user's with mode 0700")


STALE_BROKER_DIR = timedelta(hours=24)


def sweep_stale(parent: Path, *, keep: str = "", now: float | None = None) -> int:
    """Remove ``scout-broker.*`` folders a killed wrapper left in ``parent`` over a day ago. Only this user's real
    folders: a link of that name is left alone, and nothing is followed below them."""
    removed = 0
    cutoff = (now if now is not None else time.time()) - STALE_BROKER_DIR.total_seconds()
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return 0
    for entry in entries:
        if not _RUN_ID.match(entry.name) or entry.name == keep:
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and st.st_mtime < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
    return removed


def serve(vault: Path, broker_dir: Path, *, parent: int | None = None, broker: Broker | None = None) -> int:
    """Answer requests on ``<broker_dir>/b.sock`` one at a time until SIGTERM or ``parent`` is gone."""
    _check_dir(broker_dir)
    os.environ["SCOUT_DATA_DIR"] = str(vault)  # notify_telegram's duplicate guard finds the vault by it
    broker = broker or Broker.for_vault(vault, run_id=broker_dir.name)
    if broker.run_id and _RUN_ID.match(broker.run_id):
        register_run(broker.env, broker.run_id, pid=os.getpid(), folder=broker_dir)
    sweep_stale(broker_dir.parent, keep=broker_dir.name)
    with contextlib.suppress(Exception):  # warm the protected-path list, so the first commit isn't the slow one
        hook_paths.protected_roots(vault, broker.env)
    path = broker_dir / SOCKET_NAME
    stop = False

    def on_term(_sig: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        srv.bind(str(path))
    finally:
        os.umask(old)
    srv.listen(8)
    srv.settimeout(PARENT_POLL_S)
    print(f"broker: listening for vault commits and Telegram sends (pid {os.getpid()})", flush=True)
    try:
        while not stop:
            if parent is not None and not _alive(parent):
                break
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except InterruptedError:
                continue
            except OSError:
                if stop:
                    break
                raise
            with conn:
                _answer(conn, broker)
    finally:
        srv.close()
        with contextlib.suppress(OSError):
            path.unlink()
    return 0


def _answer(conn: socket.socket, broker: Broker) -> None:
    conn.settimeout(REQUEST_READ_TIMEOUT_S)
    req: Any = None
    try:
        req = json.loads(_read_line(conn, MAX_REQUEST_BYTES))
    except Exception:  # noqa: BLE001 - not JSON, too long, too deeply nested (RecursionError): a refusal, never a crash
        reply = Reply(EXIT_REFUSED, "broker: a request must be one JSON line under 1 MiB\n")
        req = None
    else:
        try:
            reply = broker.handle(req)
        except Exception as e:  # noqa: BLE001 - a bug answers as a failure, it doesn't stop the broker
            reply = Reply(1, f"broker: internal error ({type(e).__name__})\n")
    verb = req.get("verb") if isinstance(req, dict) and req.get("verb") in ("ping", *VERBS) else "request"
    print(f"broker: {verb} -> exit {reply.exit}", flush=True)
    # Each output is cut to MAX_OUTPUT_CHARS, so a reply stays under the client's cap.
    with contextlib.suppress(OSError):
        conn.sendall((json.dumps(reply.to_dict(), ensure_ascii=False) + "\n").encode())


# -- the client ----------------------------------------------------------------------------------------------------


def call(sock_path: str, req: Mapping[str, Any], *, timeout: float) -> Reply:
    """Send ``req``, return the broker's reply. :class:`Unreachable` when there is no answer."""
    if not sock_path:
        raise Unreachable("no broker")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(CONNECT_TIMEOUT_S)
        try:
            s.connect(sock_path)
        except OSError as e:
            raise Unreachable(str(e)) from None
        s.settimeout(timeout)
        try:
            s.sendall((json.dumps(req) + "\n").encode())
            line = _read_line(s, MAX_REPLY_BYTES)
        except (OSError, ValueError) as e:
            raise Unreachable(str(e)) from None
    finally:
        s.close()
    if not line:
        raise Unreachable("the broker closed the connection without a reply")
    try:
        return Reply.from_dict(json.loads(line))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise Unreachable(str(e)) from None


def _emit(reply: Reply) -> int:
    if reply.stdout:
        sys.stdout.write(reply.stdout if reply.stdout.endswith("\n") else reply.stdout + "\n")
        sys.stdout.flush()
    if reply.stderr:
        sys.stderr.write(reply.stderr if reply.stderr.endswith("\n") else reply.stderr + "\n")
        sys.stderr.flush()
    return reply.exit


def _vault(env: Mapping[str, str]) -> Path:
    from scout import paths

    return paths.data_dir(env.get("SCOUT_DATA_DIR") or None)


def client_commit(message: str, paths: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """In-session ``git-safe-commit.sh``: the broker commits, or the request is queued (exit 6)."""
    env = os.environ if env is None else env
    req = {
        "v": PROTOCOL,
        "verb": "commit",
        "message": message,
        "paths": list(paths),
        "override": env.get("SCOUT_LINT_OVERRIDE") or None,
    }
    try:
        return _emit(call(env.get(ENV_SOCKET, ""), req, timeout=COMMIT_TIMEOUT_S + CLIENT_SLACK_S))
    except Unreachable:
        pass
    try:
        qid = enqueue(_vault(env), "commit", req)
    except (OSError, QueueUnsafe) as e:
        print(
            f"git-safe-commit: the commit broker isn't answering and the queue can't be written ({e})", file=sys.stderr
        )
        return 5
    print(
        f"git-safe-commit: the commit broker isn't answering; queued {qid}, it lands after this session (no SHA yet)",
        file=sys.stderr,
    )
    return EXIT_QUEUED


def client_telegram(fields: Mapping[str, Any], env: Mapping[str, str] | None = None) -> int:
    """In-session ``scoutctl notify telegram``: the broker sends it, or it is queued and sent after (exit 0).

    A queued message is one the drain will send: the client queues only within its run's allowance (what the log
    says the run sent, plus what it already queued), and answers exit 5 past it, as the broker would.
    """
    from scout.ids import new_ulid

    env = os.environ if env is None else env
    sock = env.get(ENV_SOCKET, "")
    run = run_id_of(sock)
    req = {"v": PROTOCOL, "verb": "telegram", **fields, "rid": new_ulid(), "run": run}
    try:
        return _emit(call(sock, req, timeout=TELEGRAM_TIMEOUT_S + CLIENT_SLACK_S))
    except Unreachable:
        pass
    if fields.get("dry_run"):
        print("scoutctl notify telegram: the Telegram broker isn't answering; a dry run isn't queued", file=sys.stderr)
        return 1
    limits = load_limits(env)
    body = fields.get("body")
    if isinstance(body, str) and len(body) > limits.max_chars:
        print(
            f"scoutctl notify telegram: the message is {len(body)} characters; the broker sends at most "
            f"{limits.max_chars}",
            file=sys.stderr,
        )
        return EXIT_LIMIT
    vault = _vault(env)
    try:
        used = sum(1 for r in _read_sends(env, datetime.now(UTC)) if r["run"] == run)
        used += sum(1 for entry in _queued(vault, "telegram") if entry.get("run") == run)
        if used >= limits.max_per_run:
            print(
                f"scoutctl notify telegram: this run has sent or queued its {limits.max_per_run} messages; "
                "put the rest in the wrap",
                file=sys.stderr,
            )
            return EXIT_LIMIT
        qid = enqueue(vault, "telegram", req)
    except (OSError, QueueUnsafe) as e:
        print(
            f"scoutctl notify telegram: the broker isn't answering and the queue can't be written ({e})",
            file=sys.stderr,
        )
        return 1
    print(f"id={qid}", file=sys.stderr)
    print(f"scoutctl notify telegram: queued {qid}: Telegram sends it after this session", file=sys.stderr)
    return 0


# -- the queue -----------------------------------------------------------------------------------------------------
#
# The queue is in the vault, which a run can write, and the drain runs outside the sandbox. So the drain never
# follows a link: it opens .scout-state, broker-queue and each folder below one at a time with O_NOFOLLOW, from the
# vault's resolved path, and checks each is a folder of this user's; then it reads, rewrites, moves and removes
# entries only through those folders' descriptors, opening each entry with O_NOFOLLOW | O_NONBLOCK and taking only
# a regular file. A link, a FIFO or a folder that isn't this user's can't take the drain outside the queue or stall
# it.


class QueueUnsafe(Exception):
    """A queue folder isn't a real folder of this user's (a link, a file, another owner's)."""


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_dir(parent_fd: int, name: str, *, create: bool) -> int | None:
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        return _open_dir(parent_fd, name, create=False)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise QueueUnsafe(f"{name} isn't a real folder") from None
        raise
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise QueueUnsafe(f"{name} isn't a folder of this user's")
    return fd


@contextlib.contextmanager
def _queue_dirs(vault: Path, *names: str, create: bool = False) -> Iterator[dict[str, int | None]]:
    """Descriptors of the queue's folders ``names`` (verbs, failed), opened link by link from the vault. A missing
    folder is None (made, when ``create``). :class:`QueueUnsafe` when any folder on the way isn't real."""
    opened: list[int] = []
    try:
        fd: int | None = os.open(os.path.realpath(vault), os.O_RDONLY | os.O_DIRECTORY)
        assert fd is not None
        opened.append(fd)
        for part in QUEUE_DIR.parts:
            fd = _open_dir(opened[-1], part, create=create) if fd is not None else None
            if fd is None:
                break
            opened.append(fd)
        queue_fd = fd  # broker-queue/, or None when it (or .scout-state) is missing
        out: dict[str, int | None] = {}
        for name in names:
            sub = _open_dir(queue_fd, name, create=create) if queue_fd is not None else None
            out[name] = sub
            if sub is not None:
                opened.append(sub)
        yield out
    finally:
        for f in reversed(opened):
            with contextlib.suppress(OSError):
                os.close(f)


def queue_problem(vault: Path) -> str | None:
    """Why the queue can't be drained safely, or None (also when there is no queue)."""
    try:
        with _queue_dirs(vault, *VERBS, FAILED_DIR):
            return None
    except QueueUnsafe as e:
        return str(e)
    except OSError:
        return None


def _names(dir_fd: int | None) -> list[str]:
    if dir_fd is None:
        return []
    try:
        return sorted(n for n in os.listdir(dir_fd) if _ENTRY_NAME.match(n))
    except OSError:
        return []


def _read_entry(dir_fd: int, name: str) -> dict[str, Any]:
    """The entry ``name`` in ``dir_fd``; ValueError for anything but a regular file holding one JSON object."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError as e:
        raise ValueError("unreadable") from e
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > QUEUE_MAX_ENTRY_BYTES:
            raise ValueError("not a queue entry")
        data = fh.read(QUEUE_MAX_ENTRY_BYTES + 1)
    try:
        entry = json.loads(data.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - malformed or too deeply nested
        raise ValueError("malformed") from e
    if not isinstance(entry, dict):
        raise ValueError("not a queue entry")
    return entry


def _write_entry(dir_fd: int, name: str, data: Mapping[str, Any]) -> None:
    tmp = f".{name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise


def queue_dir(vault: Path, verb: str) -> Path:
    return vault / QUEUE_DIR / verb


def enqueue(vault: Path, verb: str, req: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """Write ``req`` to the queue atomically (a temp file, then a rename), mode 0600. Returns its id (a ULID)."""
    from scout.ids import new_ulid

    if verb not in VERBS:
        raise ValueError(verb)
    qid = new_ulid()
    entry = {k: v for k, v in req.items() if k not in ("v", "verb")}
    entry.update({"v": PROTOCOL, "queued_at": (now or datetime.now(UTC)).isoformat(), "attempts": 0})
    with _queue_dirs(vault, verb, create=True) as dirs:
        fd = dirs[verb]
        assert fd is not None
        _write_entry(fd, f"{qid}.json", entry)
    return qid


def _queued(vault: Path, verb: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with _queue_dirs(vault, verb) as dirs:
        fd = dirs[verb]
        for name in _names(fd):
            with contextlib.suppress(ValueError):
                out.append(_read_entry(fd, name))  # type: ignore[arg-type]
    return out


def pending(vault: Path) -> int:
    """How many entries wait in the queue (failed ones not counted); 0 when the queue isn't safe to read."""
    try:
        with _queue_dirs(vault, *VERBS, FAILED_DIR) as dirs:  # failed/ too: an unsafe queue waits for the owner
            return sum(len(_names(dirs[v])) for v in VERBS)
    except (QueueUnsafe, OSError):
        return 0


def failed_entries(vault: Path) -> list[str]:
    """The names in ``failed/`` ([] when there are none or the queue isn't safe to read)."""
    try:
        with _queue_dirs(vault, FAILED_DIR) as dirs:
            return _names(dirs[FAILED_DIR])
    except (QueueUnsafe, OSError):
        return []


@dataclass
class DrainResult:
    done: int = 0
    kept: int = 0
    failed: int = 0
    busy: bool = False
    unsafe: str = ""
    reasons: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.busy:
            return "broker drain: another drain is running"
        if self.unsafe:
            return f"broker drain: refused, the queue isn't safe to read ({self.unsafe}); nothing was applied"
        return f"broker drain: {self.done} done, {self.kept} kept for the next drain, {self.failed} moved to failed/"


def drain(
    vault: Path,
    *,
    env: Mapping[str, str] | None = None,
    broker: Broker | None = None,
    deadline_s: float = DRAIN_DEADLINE_S,
    run: str | None = None,
) -> DrainResult:
    """Apply the queue outside the sandbox: each entry validated exactly like a live request (spec §5.6).

    ``run``: the wrapper's drain, as its run ends. Afterwards the run is closed (:func:`close_run`): the Telegram
    requests of its still queued keep its allowance for later drains, and nothing queued after gets any.
    """
    env = dict(os.environ if env is None else env)
    with contextlib.suppress(OSError):
        close_stale_runs(env, vault)  # before any entry is credited
    try:
        return _drain(vault, env=env, broker=broker, deadline_s=deadline_s)
    finally:
        if run and _RUN_ID.match(run):
            try:
                left = [e for e in _queued(vault, "telegram") if e.get("run") == run]
            except (QueueUnsafe, OSError):
                left = []
            close_run(env, run, [e["rid"] for e in left if isinstance(e.get("rid"), str)])


def _drain(vault: Path, *, env: dict[str, str], broker: Broker | None, deadline_s: float) -> DrainResult:
    result = DrainResult()
    problem = queue_problem(vault)
    if problem is not None:
        result.unsafe = problem
        _event(vault, "broker.queue_unsafe", {"reason": problem})
        return result
    if pending(vault) == 0:
        return result
    stop_at = time.monotonic() + deadline_s
    with _locked(owner_dir(env) / DRAIN_LOCK_NAME, blocking=False) as held:
        if not held:
            result.busy = True
            return result
        broker = broker or Broker.for_vault(vault, env)
        now = broker.clock()
        try:
            with _queue_dirs(vault, *VERBS, FAILED_DIR, create=True) as dirs:
                for verb in VERBS:
                    for name in _names(dirs[verb]):
                        if time.monotonic() >= stop_at:
                            result.kept += 1
                            continue
                        _drain_one(vault, verb, name, dirs, broker, now, result)
        except QueueUnsafe as e:  # a folder swapped for a link between the check and here
            result.unsafe = str(e)
            _event(vault, "broker.queue_unsafe", {"reason": str(e)})
    return result


def _drain_one(
    vault: Path,
    verb: str,
    name: str,
    dirs: Mapping[str, int | None],
    broker: Broker,
    now: datetime,
    result: DrainResult,
) -> None:
    vfd = dirs[verb]
    assert vfd is not None
    try:
        entry = _read_entry(vfd, name)
    except ValueError as e:
        _fail(vault, verb, name, dirs, str(e), result)
        return
    raw_attempts = entry.get("attempts")
    attempts = raw_attempts if isinstance(raw_attempts, int) and raw_attempts >= 0 else 0
    try:
        queued = datetime.fromisoformat(str(entry.get("queued_at")))
        queued = queued if queued.tzinfo else queued.replace(tzinfo=UTC)
    except ValueError:
        queued = datetime.fromtimestamp(0, UTC)
    if now - queued > QUEUE_MAX_AGE:
        _fail(vault, verb, name, dirs, "older than 24 h", result)
        return
    if attempts >= QUEUE_MAX_ATTEMPTS:
        _fail(vault, verb, name, dirs, f"{attempts} attempts", result)
        return
    req = {k: v for k, v in entry.items() if k not in ("queued_at", "attempts", "last_exit")}
    req.update({"v": PROTOCOL, "verb": verb})  # the folder says the verb, not the file
    reply = broker.handle(req)
    if reply.kind in (REFUSED, RUN_LIMIT):
        _fail(vault, verb, name, dirs, f"refused (exit {reply.exit})", result)
    elif reply.exit in _DONE[verb]:
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=vfd)
        result.done += 1
    elif reply.kind == HOUR_LIMIT or reply.exit in _TRANSIENT[verb]:
        # The hour being full isn't the entry's fault: it waits for a later drain without using up its tries.
        if reply.kind != HOUR_LIMIT:
            entry.update({"attempts": attempts + 1, "last_exit": reply.exit})
            with contextlib.suppress(OSError):
                _write_entry(vfd, name, entry)
        result.kept += 1
    else:
        _fail(vault, verb, name, dirs, f"refused (exit {reply.exit})", result)


def _fail(vault: Path, verb: str, name: str, dirs: Mapping[str, int | None], reason: str, result: DrainResult) -> None:
    vfd, ffd = dirs[verb], dirs[FAILED_DIR]
    assert vfd is not None
    try:
        if ffd is None:
            raise OSError("no failed/ folder")
        os.replace(name, f"{verb}-{name}", src_dir_fd=vfd, dst_dir_fd=ffd)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=vfd)
    result.failed += 1
    result.reasons.append(f"{verb}: {reason}")
    _event(vault, "broker.queue_failed", {"verb": verb, "reason": reason})


def _event(vault: Path, kind: str, payload: dict[str, Any]) -> None:
    try:
        from scout import paths
        from scout.events import append_event

        append_event(paths.logs_dir(vault), kind=kind, source="cli:broker_drain", payload=payload)
    except Exception:  # noqa: BLE001, S110 - the drain's outcome stands without its event; health reads the queue
        pass


# -- the profile ---------------------------------------------------------------------------------------------------


def helper_has_client(vault: Path) -> bool:
    """Whether the vault's git-safe-commit.sh hands over to the broker, and its scoutctl launcher is there."""
    try:
        text = (vault / "scripts" / "git-safe-commit.sh").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return CLIENT_MARKER in text and os.access(vault / "scripts" / "scoutctl", os.X_OK)


def wait_for(sock_path: Path, *, timeout: float = SOCKET_WAIT_S) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if call(str(sock_path), {"v": PROTOCOL, "verb": "ping"}, timeout=CONNECT_TIMEOUT_S).exit == 0:
                return True
        except Unreachable:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def build_profile(vault: Path, broker_dir: Path, base: str, env: Mapping[str, str] | None = None) -> str:
    """``base`` (the run's 1c profile) tightened by :func:`scout.sandbox.profile.broker_rules`, once the broker
    answers and the vault's helper has its client mode. Self-tested. :class:`BrokerError` says why not."""
    from scout.sandbox import profile as sbx

    env = os.environ if env is None else env
    if not base.strip():
        raise BrokerError("no base profile was passed")
    if not helper_has_client(vault):
        raise BrokerError("the vault's scripts/git-safe-commit.sh predates the broker (run /scout-update)")
    if not wait_for(broker_dir / SOCKET_NAME, timeout=SOCKET_WAIT_S):
        raise BrokerError(f"the broker didn't answer within {int(SOCKET_WAIT_S)}s")
    try:
        text = sbx.with_broker_rules(base, sbx.broker_rules(vault, broker_dir, env))
    except sbx.ProfileError as e:
        raise BrokerError(str(e)) from e
    err = sbx.self_test(text) or python_starts(text, vault, env)
    if err is not None:
        raise BrokerError(f"the tightened profile failed its self-test: {err}")
    return text


PYTHON_START_TIMEOUT_S = 30.0


def python_starts(
    profile: str, vault: Path, env: Mapping[str, str], *, timeout_s: float = PYTHON_START_TIMEOUT_S
) -> str | None:
    """None when the vault's ``scripts/scoutctl`` starts the engine under ``profile``, with the run's environment
    and in the vault, as a session would; else why not.

    The session's first process inside the sandbox can be scoutctl (``agent-runtime exec`` launches claude), so a
    profile that stopped it from starting would stop every run.
    """
    from scout.sandbox import profile as sbx

    run_env = {k: v for k, v in env.items() if k != ENV_SOCKET}
    try:
        proc = subprocess.run(
            [str(sbx.SANDBOX_EXEC), "-p", profile, str(vault / "scripts" / "scoutctl"), "version"],
            capture_output=True, text=True, timeout=timeout_s, check=False, stdin=subprocess.DEVNULL,
            cwd=str(vault), env=run_env,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as e:
        return f"the engine's Python didn't start under it: {e}"
    if proc.returncode != 0:
        tail = proc.stderr.strip()[-300:]
        return f"the engine's Python didn't start under it (exit {proc.returncode}){': ' + tail if tail else ''}"
    return None


def write_status(vault: Path, status: str, reason: str, *, now: datetime | None = None) -> None:
    """``.scout-logs/.run-broker.json``: on | unavailable | off, for health and the doctor. Best effort."""
    from scout import paths

    data = {"at": int((now or datetime.now(UTC)).timestamp()), "status": status, "reason": reason}
    with contextlib.suppress(OSError):
        logs = paths.logs_dir(vault)
        logs.mkdir(parents=True, exist_ok=True)
        safe_write.atomic_write_text(logs / STATUS_FILE, json.dumps(data) + "\n")


def read_status(vault: Path) -> dict[str, Any] | None:
    from scout import paths

    try:
        data = json.loads((paths.logs_dir(vault) / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("status"), str) else None

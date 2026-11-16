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
import fcntl
import io
import json
import os
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
BLANKET = frozenset({"-A", "--all", ".", "./", ":/", ":"})

QUEUE_MAX_AGE = timedelta(hours=24)
QUEUE_MAX_ATTEMPTS = 10
QUEUE_MAX_ENTRY_BYTES = MAX_REQUEST_BYTES
# Exit codes after which a queued entry stays queued for the next drain: the commit mutex was busy, git or a hook
# failed (a lock not provably stale), the network or Telegram failed, the secrets are missing.
_TRANSIENT = {"commit": {1, 3, 5}, "telegram": {1, 2, 10}}
# Exit codes that mean the entry is done: committed or nothing left to commit; sent or already sent.
_DONE = {"commit": {0, 4}, "telegram": {0, 3}}

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
TELEGRAM_LOG_NAME = "telegram-sent.jsonl"
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

    A path must stay inside the vault as spelled and resolved, have no ``.git`` component, and not be a path
    :func:`scout.kb.hook_paths.protected_write` protects; options, pathspec magic and blanket forms are refused.
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
        form = hook_paths.protected_write(spelled, vault, env) or hook_paths.protected_write(real, vault, env)
        if form is not None:
            raise Refused(EXIT_REFUSED, f"'{p}' is protected ({form}); a scheduled run doesn't commit it")
        out.append(rel)
    return msg, out, override


def validate_telegram(req: Mapping[str, Any], limits: Limits) -> dict[str, Any]:
    """The send's fields, or :class:`Refused`. The request names no recipient: :func:`send` reads the owner's."""
    from scout.scripts.notify_telegram import VALID_PARSE_MODES, VALID_TIERS

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
    return {"tier": tier, "parse_mode": parse_mode, "body": body, **flags}


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
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return Reply(5, f"git-safe-commit: the commit took longer than {int(timeout)}s and was stopped\n")
    return Reply(proc.returncode, err, out)


def send_telegram(fields: Mapping[str, Any]) -> Reply:
    """:func:`scout.scripts.notify_telegram.cli_send` in this process, its output captured."""
    from scout.scripts.notify_telegram import cli_send

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_send(
            tier=fields["tier"],
            text=fields["body"],
            parse_mode=fields["parse_mode"],
            dry_run=fields["dry_run"],
            allow_duplicate=fields["allow_duplicate"],
            echo_out=lambda s: print(s, file=sys.stdout),
            echo_err=lambda s: print(s, file=sys.stderr),
        )
    return Reply(code, err.getvalue(), out.getvalue())


@contextlib.contextmanager
def _locked(path: Path, *, blocking: bool = True) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield fh
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _recent_sends(fh: Any, now: datetime) -> list[str]:
    fh.seek(0)
    keep: list[str] = []
    for line in fh.read().splitlines():
        try:
            at = datetime.fromisoformat(json.loads(line)["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if timedelta(0) <= now - at < timedelta(hours=1):
            keep.append(line)
    return keep


@dataclass
class Broker:
    """One run's brokers: the commit verb and the Telegram verb, sharing nothing but the process."""

    vault: Path
    env: Mapping[str, str]
    limits: Limits = field(default_factory=Limits)
    sender: Callable[[Mapping[str, Any]], Reply] = send_telegram
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sent: int = 0

    @classmethod
    def for_vault(cls, vault: Path, env: Mapping[str, str] | None = None, **kw: Any) -> Broker:
        env = dict(os.environ if env is None else env)
        return cls(vault=vault, env=env, limits=load_limits(env), **kw)

    def handle(self, req: Any) -> Reply:
        if not isinstance(req, dict) or req.get("v") != PROTOCOL:
            return Reply(EXIT_REFUSED, "broker: not a request this broker speaks\n")
        verb = req.get("verb")
        if verb == "ping":
            return Reply(0, "", "pong")
        if verb == "commit":
            return self.commit(req)
        if verb == "telegram":
            return self.telegram(req)
        return Reply(EXIT_REFUSED, "broker: unknown verb\n")

    def commit(self, req: Mapping[str, Any]) -> Reply:
        try:
            message, paths, override = validate_commit(req, self.vault, self.env)
        except Refused as r:
            return Reply(r.exit_code, f"git-safe-commit: {r.reason}\n")
        return run_commit(self.vault, message, paths, override, self.env)

    def telegram(self, req: Mapping[str, Any]) -> Reply:
        try:
            fields = validate_telegram(req, self.limits)
        except Refused as r:
            return Reply(r.exit_code, f"scoutctl notify telegram: {r.reason}\n")
        if fields["dry_run"]:
            return self.sender(fields)
        if self.sent >= self.limits.max_per_run:
            return Reply(
                EXIT_LIMIT,
                f"scoutctl notify telegram: this run has sent its {self.limits.max_per_run} messages; "
                "put the rest in the wrap\n",
            )
        now = self.clock()
        # Held across the send, so two runs' brokers can't both take the hour's last slot.
        with _locked(owner_dir(self.env) / TELEGRAM_LOG_NAME) as fh:
            recent = _recent_sends(fh, now)
            if len(recent) >= self.limits.max_per_hour:
                return Reply(
                    EXIT_LIMIT,
                    f"scoutctl notify telegram: {len(recent)} messages went out in the last hour, the most the "
                    "owner allows; nothing was sent\n",
                )
            reply = self.sender(fields)
            if reply.exit == 0:
                self.sent += 1
                fh.seek(0)
                fh.truncate()
                fh.write("".join(line + "\n" for line in [*recent, json.dumps({"at": now.isoformat()})]))
                fh.flush()
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


def serve(vault: Path, broker_dir: Path, *, parent: int | None = None, broker: Broker | None = None) -> int:
    """Answer requests on ``<broker_dir>/b.sock`` one at a time until SIGTERM or ``parent`` is gone."""
    _check_dir(broker_dir)
    os.environ["SCOUT_DATA_DIR"] = str(vault)  # notify_telegram's duplicate guard finds the vault by it
    broker = broker or Broker.for_vault(vault)
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
    except (ValueError, OSError, UnicodeDecodeError):
        reply = Reply(EXIT_REFUSED, "broker: a request must be one JSON line under 1 MiB\n")
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
    except (ValueError, UnicodeDecodeError) as e:
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
    except OSError as e:
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
    """In-session ``scoutctl notify telegram``: the broker sends it, or it is queued and sent after (exit 0)."""
    env = os.environ if env is None else env
    req = {"v": PROTOCOL, "verb": "telegram", **fields}
    try:
        return _emit(call(env.get(ENV_SOCKET, ""), req, timeout=TELEGRAM_TIMEOUT_S + CLIENT_SLACK_S))
    except Unreachable:
        pass
    if fields.get("dry_run"):
        print("scoutctl notify telegram: the Telegram broker isn't answering; a dry run isn't queued", file=sys.stderr)
        return 1
    try:
        qid = enqueue(_vault(env), "telegram", req)
    except OSError as e:
        print(
            f"scoutctl notify telegram: the broker isn't answering and the queue can't be written ({e})",
            file=sys.stderr,
        )
        return 1
    print(f"id={qid}", file=sys.stderr)
    print(f"scoutctl notify telegram: queued {qid}: Telegram sends it after this session", file=sys.stderr)
    return 0


# -- the queue -----------------------------------------------------------------------------------------------------


def queue_dir(vault: Path, verb: str) -> Path:
    return vault / QUEUE_DIR / verb


def enqueue(vault: Path, verb: str, req: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """Write ``req`` to the queue atomically (a temp file, then a rename), mode 0600. Returns its id (a ULID)."""
    from scout.ids import new_ulid

    if verb not in VERBS:
        raise ValueError(verb)
    qid = new_ulid()
    d = queue_dir(vault, verb)
    d.mkdir(parents=True, exist_ok=True)
    entry = {k: v for k, v in req.items() if k not in ("v", "verb")}
    entry.update({"v": PROTOCOL, "queued_at": (now or datetime.now(UTC)).isoformat(), "attempts": 0})
    _write_atomic(d / f"{qid}.json", entry)
    return qid


def _write_atomic(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def pending(vault: Path) -> int:
    """How many entries wait in the queue (failed ones not counted)."""
    return sum(len(list(queue_dir(vault, verb).glob("*.json"))) for verb in VERBS)


def failed_entries(vault: Path) -> list[Path]:
    return sorted((vault / QUEUE_DIR / FAILED_DIR).glob("*.json"))


@dataclass
class DrainResult:
    done: int = 0
    kept: int = 0
    failed: int = 0
    busy: bool = False
    reasons: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.busy:
            return "broker drain: another drain is running"
        return f"broker drain: {self.done} done, {self.kept} kept for the next drain, {self.failed} moved to failed/"


def drain(vault: Path, *, env: Mapping[str, str] | None = None, broker: Broker | None = None) -> DrainResult:
    """Apply the queue outside the sandbox: each entry validated exactly like a live request (spec §5.6)."""
    env = dict(os.environ if env is None else env)
    result = DrainResult()
    if pending(vault) == 0:
        return result
    with _locked(owner_dir(env) / DRAIN_LOCK_NAME, blocking=False) as fh:
        if fh is None:
            result.busy = True
            return result
        # One drain has the per-run Telegram allowance of one run.
        broker = broker or Broker.for_vault(vault, env)
        now = broker.clock()
        for verb in VERBS:
            for path in sorted(queue_dir(vault, verb).glob("*.json")):
                _drain_one(vault, verb, path, broker, now, result)
    return result


def _drain_one(vault: Path, verb: str, path: Path, broker: Broker, now: datetime, result: DrainResult) -> None:
    try:
        if path.is_symlink() or path.stat().st_size > QUEUE_MAX_ENTRY_BYTES:
            raise ValueError("not a queue entry")
        entry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entry, dict):
            raise ValueError("not a queue entry")
    except (OSError, ValueError, UnicodeDecodeError):
        _fail(vault, verb, path, "malformed", result)
        return
    raw_attempts = entry.get("attempts")
    attempts = raw_attempts if isinstance(raw_attempts, int) and raw_attempts >= 0 else 0
    try:
        queued = datetime.fromisoformat(str(entry.get("queued_at")))
        queued = queued if queued.tzinfo else queued.replace(tzinfo=UTC)
    except ValueError:
        queued = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    if now - queued > QUEUE_MAX_AGE:
        _fail(vault, verb, path, "older than 24 h", result)
        return
    if attempts >= QUEUE_MAX_ATTEMPTS:
        _fail(vault, verb, path, f"{attempts} attempts", result)
        return
    req = {k: v for k, v in entry.items() if k not in ("queued_at", "attempts", "last_exit")}
    req.update({"v": PROTOCOL, "verb": verb})  # the folder says the verb, not the file
    reply = broker.handle(req)
    if reply.exit in _DONE[verb]:
        with contextlib.suppress(OSError):
            path.unlink()
        result.done += 1
    elif reply.exit in _TRANSIENT[verb]:
        entry.update({"attempts": attempts + 1, "last_exit": reply.exit})
        try:
            _write_atomic(path, entry)
        except OSError:
            pass
        result.kept += 1
    else:
        _fail(vault, verb, path, f"refused (exit {reply.exit})", result)


def _fail(vault: Path, verb: str, path: Path, reason: str, result: DrainResult) -> None:
    dest = vault / QUEUE_DIR / FAILED_DIR
    try:
        dest.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest / f"{verb}-{path.name}")
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink()
    result.failed += 1
    result.reasons.append(f"{verb}: {reason}")
    try:
        from scout import paths
        from scout.events import append_event

        append_event(
            paths.logs_dir(vault),
            kind="broker.queue_failed",
            source="cli:broker_drain",
            payload={"verb": verb, "reason": reason},
        )
    except Exception:  # noqa: BLE001, S110 - the entry is in failed/ either way, and health counts it
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
    if not wait_for(broker_dir / SOCKET_NAME):
        raise BrokerError(f"the broker didn't answer within {int(SOCKET_WAIT_S)}s")
    try:
        text = sbx.with_broker_rules(base, sbx.broker_rules(vault, broker_dir, env))
    except sbx.ProfileError as e:
        raise BrokerError(str(e)) from e
    err = sbx.self_test(text)
    if err is not None:
        raise BrokerError(f"the tightened profile failed its self-test: {err}")
    return text


def write_status(vault: Path, status: str, reason: str, *, now: datetime | None = None) -> None:
    """``.scout-logs/.run-broker.json``: on | unavailable | off, for health and the doctor. Best effort."""
    from scout import paths

    data = {"at": int((now or datetime.now(UTC)).timestamp()), "status": status, "reason": reason}
    with contextlib.suppress(OSError):
        logs = paths.logs_dir(vault)
        logs.mkdir(parents=True, exist_ok=True)
        tmp = logs / f".{STATUS_FILE}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
        os.replace(tmp, logs / STATUS_FILE)


def read_status(vault: Path) -> dict[str, Any] | None:
    from scout import paths

    try:
        data = json.loads((paths.logs_dir(vault) / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("status"), str) else None

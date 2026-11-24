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
- ``scoutctl lab experiment run`` hands over to :func:`client_experiment`: the broker checks the experiment's
  committed note (:func:`validate_experiment`: notice, veto, holds, started, the owner's cap) and runs its script
  with :func:`scout.lab.runner.run_experiment`, from the commit it checked, since ``sandbox-exec`` can't nest
  inside the run's own sandbox. An experiment request is never queued; a refused one doesn't use the run's
  allowance. The verb is off until the owner sets ``"experiments_enabled": true`` in ``config.json``.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import io
import json
import math
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

from scout import plural, safe_write
from scout.kb import hook_paths

PROTOCOL = 1
ENV_SOCKET = "SCOUT_RUN_BROKER"
SOCKET_NAME = "b.sock"
QUEUE_DIR = Path(".scout-state") / "broker-queue"
FAILED_DIR = "failed"
VERBS = ("commit", "telegram", "experiment")
# What a client may queue for the drain. An experiment is never queued: it runs live or not at all.
QUEUE_VERBS = ("commit", "telegram")
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
EXIT_NOT_RUN = 4  # scoutctl lab experiment run: no broker to run it, or experiments are off
EXPERIMENTS_OFF = "experiments are off in the owner's config"  # LabLimits.experiments_enabled

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
    ran: bool = False  # an experiment's script ran (its exit is the script's); the socket carries it when true

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "v": PROTOCOL,
            "exit": self.exit,
            "stderr": _tail(self.stderr),
            "stdout": _tail(self.stdout),
        }
        if self.ran:
            d["ran"] = True
        return d

    @classmethod
    def from_dict(cls, data: Any) -> Reply:
        if not isinstance(data, dict) or not isinstance(data.get("exit"), int) or isinstance(data.get("exit"), bool):
            raise Unreachable("the broker's reply wasn't one")
        return cls(
            int(data["exit"]), str(data.get("stderr") or ""), str(data.get("stdout") or ""), ran=data.get("ran") is True
        )


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


@dataclass(frozen=True)
class LabLimits:
    """Experiment limits (lab spec §6.3): the owner's, never the run's."""

    experiments_per_run: int = 1
    max_cost_cap_usd: float = 1.0
    notice_hours: float = 6
    timeout_s: float = 1800
    # The broker's `experiment` verb runs nothing until the owner turns it on (only JSON `true` does). The owner's
    # own interactive session, which has no broker, isn't gated by it.
    experiments_enabled: bool = False
    # Totals for one run (W4): processes the script may have at once, its own included, and bytes its folder and
    # scratch dir may hold (scout.lab.runner enforces both).
    max_processes: int = 64
    max_total_bytes: int = 512 * 1024 * 1024
    # Descriptors the script may hold open (the gate's `ulimit -n`): dataset loaders and the like need hundreds.
    max_open_files: int = 1024


def _finite(value: float) -> bool:
    """``math.isfinite``, false (not OverflowError) for an integer too large for a float."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def load_lab_limits(env: Mapping[str, str]) -> LabLimits:
    """The owner's experiment limits from ``config.json``'s ``"lab"`` block, read like :func:`load_limits`: a
    missing or malformed file, or a value out of bounds, means the default. ``experiments_per_run`` is a positive
    integer; the others are finite positive numbers, except ``notice_hours``, which may be 0. ``max_processes``,
    ``max_total_bytes`` and ``max_open_files`` are positive integers too, the bytes up to 1 TiB and the files up to
    65536.
    ``experiments_enabled`` is on only when it is exactly ``true``."""
    defaults = LabLimits()
    try:
        data = json.loads((owner_dir(env) / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return defaults
    block = data.get("lab") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return defaults

    def number(name: str, *, integer: bool = False, zero: bool = False, most: int = 1_000_000) -> Any:
        value = block.get(name)
        if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
            return getattr(defaults, name)
        if _finite(value) and (0 <= value if zero else 0 < value) and value <= most:
            return value
        return getattr(defaults, name)

    return LabLimits(
        number("experiments_per_run", integer=True),
        float(number("max_cost_cap_usd")),
        float(number("notice_hours", zero=True)),
        float(number("timeout_s")),
        block.get("experiments_enabled") is True,
        max_processes=number("max_processes", integer=True),
        max_total_bytes=number("max_total_bytes", integer=True, most=1 << 40),
        max_open_files=number("max_open_files", integer=True, most=65536),
    )


# The owner's vetoes, one JSON row each ({"slug", "eid", "at", "vault"}), in the owner folder, which a run can't
# write. A veto in the vault's files can be undone by a run before it is committed; a row here can't, so the broker
# refuses a listed experiment whatever HEAD, the history or the worktree say. Rows match on slug and EID alone, so
# a moved or renamed vault keeps its vetoes (`vault` only says where it was made).
LAB_VETOES_NAME = "lab-vetoes.jsonl"


def record_veto(env: Mapping[str, str], vault: Path, slug: str, eid: str, at: datetime) -> None:
    """Append ``slug`` ``eid`` to the owner's veto store, synced to disk (``lab veto``, the owner's verb). Raises
    :class:`OSError` when it can't be written, as it can't from inside a run's sandbox.

    Under an exclusive ``flock`` on the store, so two vetoes never interleave. A store whose last line has no
    newline (a write cut short, or a hand edit that dropped it) gets one first, so the new row starts a line of
    its own instead of joining the torn tail."""
    folder = owner_dir(env)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / LAB_VETOES_NAME
    row = {"slug": slug, "eid": eid, "at": at.isoformat(), "vault": os.path.realpath(vault)}
    with safe_write.open_append(path, mode=0o600) as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(("\n" if _torn_tail(path) else "") + json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _torn_tail(path: Path) -> bool:
    """Whether the file at ``path`` is non-empty and its last byte isn't a newline."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        return size > 0 and os.pread(fd, 1, size - 1) != b"\n"
    finally:
        os.close(fd)


def _json_objects(line: str) -> Iterator[dict[str, Any]]:
    """Every complete JSON object in ``line``, including several written onto one line (a torn row followed by a
    whole one) and objects after garbage: a decode that fails resumes at the next ``{``."""
    decoder = json.JSONDecoder()
    at = 0
    while True:
        at = line.find("{", at)
        if at < 0:
            return
        try:
            obj, end = decoder.raw_decode(line, at)
        except ValueError:
            at += 1
            continue
        if isinstance(obj, dict):
            yield obj
        at = end


def owner_veto(env: Mapping[str, str], slug: str, eid: str) -> str | None:
    """When the owner's store says ``slug`` ``eid`` was vetoed (the row's ``at``), or None. A missing store has no
    vetoes; one that exists but can't be read is :class:`Refused`, since a veto can't be ruled out. Damage doesn't
    hide a row: every complete JSON object in the file is read (:func:`_json_objects`), wherever it sits."""
    try:
        data = (owner_dir(env) / LAB_VETOES_NAME).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused(EXIT_REFUSED, "the owner's veto store can't be read; a veto can't be ruled out") from None
    for line in data.decode("utf-8", errors="replace").splitlines():
        for row in _json_objects(line):
            if row.get("slug") == slug and row.get("eid") == eid:
                return str(row.get("at") or "an unrecorded time")
    return None


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


MAX_EXPERIMENT_ARGS = 64
MAX_EXPERIMENT_ARG_CHARS = 4096
EXPERIMENT_GIT_TIMEOUT_S = 30.0


def _vault_git(vault: Path, *args: str) -> str | None:
    """``git`` in the vault with the broker's PATH and HOME only (no GIT_* from anywhere); None when it fails."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if os.environ.get(k)}
    env.setdefault("PATH", _DEFAULT_PATH)
    env.update({"GIT_LITERAL_PATHSPECS": "1", "GIT_PAGER": "cat"})
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args], cwd=str(vault), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=EXPERIMENT_GIT_TIMEOUT_S, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    return proc.stdout if proc.returncode == 0 else None


# Every commit that touched the paths, side branches too, with names relative to the vault.
_GIT_LOG = ("log", "--full-history", "--relative")


_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _touched(vault: Path, rels: Sequence[str]) -> list[tuple[int, str, list[str]]] | None:
    """(commit time, sha, the vault-relative paths it changed under ``rels``) for every commit touching ``rels``,
    newest first. None when git fails or answers something unexpected (callers fail closed).

    The log names commits only (a time and a hash, nothing a file name can forge); each commit's paths come from
    ``git diff-tree -z``, NUL-separated and never quoted, so no name with a quote, backslash or newline can hide.
    """
    log = _vault_git(vault, *_GIT_LOG, "--format=%ct %H", "--", *rels)
    if log is None:
        return None
    out: list[tuple[int, str, list[str]]] = []
    for line in log.splitlines():
        stamp, _, sha = line.partition(" ")
        if not stamp.isdigit() or not _SHA_RE.match(sha):
            return None
        names = _vault_git(
            vault, "diff-tree", "-z", "-r", "-m", "--root", "--no-commit-id", "--name-only", "--relative", sha,
            "--", *rels,
        )  # fmt: skip
        if names is None:
            return None
        out.append((int(stamp), sha, [n for n in names.split("\0") if n]))
    return out


def _vetoed_in_history(vault: Path, slug: str, prefix: str) -> str | None:
    """The first commit (newest first) whose copy of a note named ``<prefix>*.md`` says ``status: vetoed`` in its
    frontmatter; any name, so a rename doesn't shed the veto. :class:`Refused` when the history can't be read."""
    from scout.lab.parser import parse_experiment

    commits = _touched(vault, [prefix.rsplit("/", 1)[0]])
    if commits is None:
        raise Refused(EXIT_REFUSED, "git couldn't read the experiment notes' history; a veto can't be ruled out")
    for _, sha, names in commits:
        for path in names:
            if not (path.startswith(prefix) and path.endswith(".md") and "/" not in path[len(prefix) :]):
                continue
            text = _vault_git(vault, "show", f"{sha}:./{path}")  # None where the commit deleted it
            if text is not None and parse_experiment(slug, text).status == "vetoed":
                return sha
    return None


# What `lab experiment start` writes into a note (R25): a change to these alone isn't a change to the plan.
_LIFECYCLE_KEYS = ("status", "started")
_LIFECYCLE_LINE = re.compile(rf"^(?:{'|'.join(_LIFECYCLE_KEYS)})\s*:")


def _same_plan(a: str, b: str) -> bool:
    """Whether two copies of an experiment note differ at most in their top-level ``status``/``started``
    frontmatter fields. Text without a frontmatter block must match exactly."""

    def plan(text: str) -> list[str] | None:
        lines = text.split("\n")
        if not lines or lines[0].rstrip("\r") != "---":
            return None
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---"), None)
        if end is None:
            return None
        head = [line for line in lines[1:end] if not _LIFECYCLE_LINE.match(line)]
        return ["---", *head, *lines[end:]]

    pa, pb = plan(a), plan(b)
    return a == b if pa is None or pb is None else pa == pb


def _uncommitted(vault: Path, rels: Sequence[str]) -> list[str] | None:
    """Vault-relative paths under ``rels`` that differ from HEAD: modified, staged, untracked or ignored files.
    None when git can't say."""
    prefix = _vault_git(vault, "rev-parse", "--show-prefix")
    out = _vault_git(
        vault, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching", "--", *rels
    )
    if prefix is None or out is None:
        return None
    prefix = prefix.strip()
    entries = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry, i = entries[i], i + 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":  # a rename or copy: the next field is where it came from
            paths.append(entries[i] if i < len(entries) else "")
            i += 1
        paths.append(entry[3:])
    return [p[len(prefix) :].rstrip("/") if p.startswith(prefix) else p.rstrip("/") for p in paths if p]


# R26: what may start an experiment's script. Anything else as argv[0] must be the script itself.
EXPERIMENT_INTERPRETERS = frozenset({"python3", "python", "sh", "bash", "node"})


def _committed_command(
    vault: Path, exp_rel: str, argv: list[str], runner_owned: Callable[[str], bool], commit: str = "HEAD"
) -> list[str]:  # fmt: skip
    """``argv`` when it runs a committed file of the experiment's own folder, else :class:`Refused` (``argv``).

    ``argv[0]`` is an executable file committed in the folder, or one of :data:`EXPERIMENT_INTERPRETERS` with such
    a file (any mode) as ``argv[1]``: no interpreter options first, so ``-c``, ``-e`` and ``-m`` are out. The rest is
    data. A file path is relative, stays in the folder (no ``..``, no link out of it) and is never one of the
    runner's own (its scratch dir, its run logs). A script run directly comes back as ``./<path>``, so the shell
    finds it in the folder and not on PATH.
    """
    direct = argv[0] not in EXPERIMENT_INTERPRETERS
    if not direct and len(argv) < 2:
        raise Refused(EXIT_REFUSED, f"argv: {argv[0]} needs the experiment's script as its first argument")
    given = argv[0] if direct else argv[1]
    parts = [p for p in given.split("/") if p not in ("", ".")]
    if not parts or given.startswith(("/", "-")) or ".." in parts:
        raise Refused(
            EXIT_REFUSED,
            f"argv: {given!r} isn't a file of the experiment's; run a committed script in its folder "
            f"(itself, or with one of {', '.join(sorted(EXPERIMENT_INTERPRETERS))})",
        )
    inner = "/".join(parts)
    if runner_owned(inner):
        raise Refused(EXIT_REFUSED, f"argv: {inner} is the runner's own file, not the experiment's script")
    listing = _vault_git(vault, "ls-tree", "-z", commit, "--", f"./{exp_rel}/{inner}") or ""
    mode = listing.split(" ", 1)[0] if listing.endswith(f"\t{exp_rel}/{inner}\0") else ""
    if mode not in ("100644", "100755") or (direct and mode != "100755"):
        why = "isn't a committed executable file" if direct else "isn't a committed file"
        raise Refused(EXIT_REFUSED, f"argv: {inner} {why} in the experiment's folder")
    folder = os.path.realpath(vault / exp_rel)
    real = os.path.realpath(vault / exp_rel / inner)
    if not real.startswith(folder + os.sep) or not os.path.isfile(real):
        raise Refused(EXIT_REFUSED, f"argv: {inner} leads out of the experiment's folder")
    return [f"./{inner}", *argv[1:]] if direct else [argv[0], inner, *argv[2:]]


def _shown(vault: Path, moment: datetime) -> str:
    from scout import config

    return moment.astimezone(config.resolve_timezone(vault)).strftime("%Y-%m-%d %H:%M %Z")


def _private_at(slug: str, front: Path, note: Path, front_head: str | None, note_head: str) -> str | None:
    """Why an experiment counts as private, or None: the thread's front page or the note says so, as committed or
    in the worktree. Fails closed like the privacy layer, whose parser reads the value (``Private`` or ``secret`` is
    private): a page that is absent, unreadable or whose frontmatter doesn't parse counts as private."""
    from scout.lab.parser import parse_experiment, parse_front_page

    def read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    for label, text in (("the thread's front page at HEAD", front_head), ("the thread's front page", read(front))):
        if text is None:
            return f"{label} couldn't be read, so the thread counts as private; it"
        page = parse_front_page(slug, text)
        if not page.frontmatter or page.visibility == "private":
            return f"{label} is private or unparseable; it"
    for label, text in (("the experiment note at HEAD", note_head), ("the experiment note", read(note))):
        if text is None:
            continue  # an unreadable note is refused by the checks that follow
        exp = parse_experiment(slug, text)
        if not exp.frontmatter or exp.visibility == "private":
            return f"{label} is private or unparseable; it"
    return None


def _held(slug: str, eid: str, pages: Sequence[tuple[str, str | None]], notes: Sequence[Any]) -> str | None:
    """``held: …`` when an unanswered owner comment sits on the experiment's front-page line or on a question its
    note lists, on any of ``pages`` ((where, text) of the front page) for any of ``notes`` (the parsed note as
    committed and in the worktree); else None. The definition is ``summary.held``'s:
    :meth:`scout.lab.model.Thread.unanswered_owner_comments`, targeted at the EID or one of the note's questions."""
    from scout.lab.parser import parse_front_page

    wanted = {eid, *(q for note in notes for q in note.questions)}
    found: list[str] = []
    for where, text in pages:
        if text is None:
            continue
        targets = sorted({t for t, _ in parse_front_page(slug, text).unanswered_owner_comments() if t in wanted})
        if targets:
            found.append(f"{', '.join(targets)} {where}")
    if not found:
        return None
    return (
        f"held: an owner comment waits for Scout's answer on {'; '.join(found)}; "
        f"answer it before {eid} runs (`scoutctl lab reply`)"
    )


def validate_experiment(
    req: Mapping[str, Any],
    vault: Path,
    limits: LabLimits,
    now: datetime,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str, list[str], float, str]:
    """(slug, EID, argv, cap, commit) for an experiment request, or :class:`Refused` (exit 2).

    The experiment's note is read as committed at HEAD, never from the worktree (a run can rewrite that), and
    HEAD is resolved once, to the ``commit`` every check reads and the runner runs the scripts from (ruling R30:
    the worktree can change after this). ``env`` finds the owner's folder (default: this process's). It doesn't
    run when:

    - the note isn't at HEAD, or is ``vetoed`` there or in any earlier commit (a veto is final: ruling R22)
    - the owner's veto store (:func:`owner_veto`) lists it, whatever the vault says
    - an unanswered owner comment holds it, at HEAD or in the worktree (:func:`_held`)
    - the worktree note isn't ``running`` (``lab experiment start`` comes first: ruling R31)
    - the note or its experiment folder has uncommitted changes, other than the runner's own files and the
      lifecycle fields ``start`` writes (R23, R25)
    - the newest commit touching the note or the folder's files is more recent than the owner's notice (R23)
    - ``argv`` doesn't run a committed file of the experiment's folder (:func:`_committed_command`, R26)

    The argv returned is the one to run (a script run directly as ``./<path>``). The cap is the committed note's,
    clamped to the owner's maximum.
    """
    from scout.lab import paths as lab_paths
    from scout.lab import runner as lab_runner
    from scout.lab.model import EID_RE, SLUG_RE
    from scout.lab.parser import parse_experiment

    slug, eid, argv = req.get("slug"), req.get("eid"), req.get("argv")
    if not isinstance(slug, str) or not re.match(SLUG_RE, slug):
        raise Refused(EXIT_REFUSED, f"bad thread slug {slug!r}")
    if not isinstance(eid, str) or not re.fullmatch(EID_RE, eid):
        raise Refused(EXIT_REFUSED, f"{eid!r} is not an experiment ID (E<n>)")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= MAX_EXPERIMENT_ARGS
        or not all(isinstance(a, str) and len(a) <= MAX_EXPERIMENT_ARG_CHARS and not _control(a) for a in argv)
    ):
        raise Refused(
            EXIT_REFUSED,
            f"the command must be 1 to {MAX_EXPERIMENT_ARGS} arguments of at most {MAX_EXPERIMENT_ARG_CHARS} "
            "characters each, without control characters",
        )
    note = lab_paths.experiment_note(vault, slug, eid)
    if note is None:
        raise Refused(EXIT_REFUSED, f"{slug}: no experiment note for {eid}")
    rel = note.relative_to(vault).as_posix()
    commit = (_vault_git(vault, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or "").strip()
    if not _SHA_RE.match(commit):
        raise Refused(EXIT_REFUSED, "the vault has no commit to run an experiment from")
    committed = _vault_git(vault, "show", f"{commit}:./{rel}")
    if committed is None:
        raise Refused(EXIT_REFUSED, f"{note.name} isn't at HEAD; an experiment runs only once its plan is committed")
    head = parse_experiment(slug, committed)
    front = lab_paths.front_page(vault, slug)
    front_committed = _vault_git(vault, "show", f"{commit}:./{front.relative_to(vault).as_posix()}")
    where = _private_at(slug, front, note, front_committed, committed)
    if where:
        raise Refused(
            EXIT_REFUSED,
            f"{where} counts as private, and experiments in a private thread aren't supported yet "
            "(their scripts and outputs couldn't be labelled private)",
        )
    if head.status == "vetoed":
        raise Refused(EXIT_REFUSED, f"{eid} is vetoed at HEAD; it doesn't run")
    folder = (lab_paths.thread_dir(vault, slug) / "experiments").relative_to(vault).as_posix()
    vetoed_in = _vetoed_in_history(vault, slug, f"{folder}/{slug}-{eid}-")
    if vetoed_in:
        raise Refused(EXIT_REFUSED, f"{eid} was vetoed in commit {vetoed_in[:12]}; a veto is final, it doesn't run")
    vetoed_at = owner_veto(os.environ if env is None else env, slug, eid)
    if vetoed_at:
        raise Refused(EXIT_REFUSED, f"vetoed: the owner vetoed {slug} {eid} at {vetoed_at}; a veto is final")
    exp_rel = lab_paths.experiment_dir(note).relative_to(vault).as_posix()

    def counts(path: str) -> bool:  # the note, or a file of the experiment's that the runner doesn't write
        inner = path[len(exp_rel) + 1 :] if path.startswith(exp_rel + "/") else None
        return path == rel or (inner is not None and not lab_runner.runner_owned(inner))

    try:
        worktree_text = note.read_text(encoding="utf-8")
        front_text = front.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused(EXIT_REFUSED, f"cannot read {note.name} or the thread's front page") from None
    worktree_exp = parse_experiment(slug, worktree_text)
    held = _held(slug, eid, [("at HEAD", front_committed), ("in the worktree", front_text)], [head, worktree_exp])
    if held:
        raise Refused(EXIT_REFUSED, held)
    worktree_status = worktree_exp.status
    if worktree_status != "running":
        raise Refused(
            EXIT_REFUSED,
            f"{eid} is {worktree_status or 'without a status'}, not running; only a running experiment runs "
            f"(`scoutctl lab experiment start {slug} {eid}` first)",
        )
    dirty = _uncommitted(vault, [rel, exp_rel])
    if dirty is None:
        raise Refused(EXIT_REFUSED, f"git status failed in the vault; can't tell whether {eid} is committed")
    # R25: `lab experiment start` writes only the lifecycle fields; that edit isn't a change to the plan.
    dirty = [p for p in dirty if counts(p) and not (p == rel and _same_plan(worktree_text, committed))]
    if dirty:
        shown = ", ".join(dirty[:5]) + (f" and {len(dirty) - 5} more" if len(dirty) > 5 else "")
        raise Refused(EXIT_REFUSED, f"{eid} has uncommitted changes ({shown}); commit them and wait out the notice")
    argv = _committed_command(vault, exp_rel, list(argv), lab_runner.runner_owned, commit)
    commits = _touched(vault, [rel, exp_rel])
    if commits is None:
        raise Refused(EXIT_REFUSED, f"git couldn't read {eid}'s history; the notice can't be counted")
    newest = 0
    for stamp, sha, names in commits:
        if stamp <= newest:
            continue
        for path in names:
            if not counts(path):
                continue
            if path == rel:
                before = _vault_git(vault, "show", f"{sha}^:./{rel}")
                after = _vault_git(vault, "show", f"{sha}:./{rel}")
                if before is not None and after is not None and _same_plan(before, after):
                    continue  # only status/started changed (R25)
            newest = stamp
            break
    if not newest:
        raise Refused(EXIT_REFUSED, f"no commit holds {note.name}; the notice can't be counted")
    last = datetime.fromtimestamp(newest, UTC)
    runs_from = last + timedelta(hours=limits.notice_hours)
    if runs_from > now:
        raise Refused(
            EXIT_REFUSED,
            f"{eid} is within the owner's {limits.notice_hours:g} h notice: its plan or files last changed in a "
            f"commit at {_shown(vault, last)}, so it may run from {_shown(vault, runs_from)}",
        )
    raw = head.frontmatter.get("cost_cap_usd")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not _finite(raw) or raw <= 0:
        raise Refused(EXIT_REFUSED, f"{note.name} at HEAD has cost_cap_usd {raw!r}; it must be a number above 0")
    return slug, eid, argv, min(float(raw), limits.max_cost_cap_usd), commit


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
    lab_limits: LabLimits = field(default_factory=LabLimits)
    experiments_run: int = 0

    def _send(self, fields: Mapping[str, Any]) -> Reply:
        return self.sender(fields) if self.sender is not None else send_telegram(fields, vault=self.vault)

    @classmethod
    def for_vault(cls, vault: Path, env: Mapping[str, str] | None = None, **kw: Any) -> Broker:
        env = dict(os.environ if env is None else env)
        return cls(vault=vault, env=env, limits=load_limits(env), lab_limits=load_lab_limits(env), **kw)

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
        if verb == "experiment":
            return self.experiment(req)
        return Reply(EXIT_REFUSED, "broker: unknown verb\n", kind=REFUSED)

    def experiment(self, req: Mapping[str, Any]) -> Reply:
        """Run one experiment for this run, outside its sandbox, under the experiment sandbox (lab Task 10)."""
        from scout.errors import LabError
        from scout.lab import runner

        if not self.lab_limits.experiments_enabled:  # the owner's switch, before anything else is looked at
            return Reply(EXIT_NOT_RUN, f"experiment not run: {EXPERIMENTS_OFF}\n", kind=REFUSED)
        allowed = self.lab_limits.experiments_per_run
        if self.experiments_run >= allowed:
            ran = plural.count(allowed, "experiment")
            return Reply(EXIT_LIMIT, f"experiment refused: this run has run its {ran}\n", kind=RUN_LIMIT)
        try:
            requested = _requested_timeout(req)
            slug, eid, argv, cap, commit = validate_experiment(req, self.vault, self.lab_limits, self.clock(), self.env)
        except Refused as r:
            # A refused request ran nothing, so it keeps the run's allowance; the notice bounds its retries.
            return Reply(r.exit_code, f"experiment refused: {_one_line(r.reason)}\n", kind=REFUSED)
        timeout_s = min(requested, self.lab_limits.timeout_s) if requested is not None else self.lab_limits.timeout_s
        # Counted once the runner is called, whatever it answers: a script that ran and then failed a check
        # (a survivor of the kill sweep) still ran.
        self.experiments_run += 1
        try:
            outcome = runner.run_experiment(
                self.vault,
                slug,
                eid,
                argv,
                cap_usd=cap,
                timeout_s=timeout_s,
                env=self.env,
                commit=commit,
                max_processes=self.lab_limits.max_processes,
                max_total_bytes=self.lab_limits.max_total_bytes,
                max_open_files=self.lab_limits.max_open_files,
            )
        except LabError as e:
            return Reply(e.exit_code, f"experiment refused: {_one_line(e)}\n", kind=REFUSED)
        return _outcome_reply(outcome)

    def commit(self, req: Mapping[str, Any]) -> Reply:
        try:
            message, paths, override = validate_commit(req, self.vault, self.env)
        except Refused as r:
            return Reply(r.exit_code, f"git-safe-commit: {r.reason}\n", kind=REFUSED)
        return run_commit(self.vault, message, paths, override, self.env)

    def _unanswerable(self, fields: Mapping[str, Any]) -> Reply | None:
        """A reply to a message that isn't waiting (unknown, or already answered): refused for good, live or
        drained, and before any limit, so a queued reply can't wait out a full hour for a predicted id to arrive.
        The same check cli_send makes again as it sends; an unreadable ledger is exit 1, which the drain retries."""
        if fields["reply_to"] is None:
            return None
        from scout.scripts.notify_telegram import EXIT_UNANSWERABLE, unanswerable

        problem = unanswerable(self.vault, [fields["reply_to"], *fields["covers"]])
        if problem is None:
            return None
        code, reason = problem
        return Reply(code, f"scoutctl notify telegram: {reason}\n", kind=REFUSED if code == EXIT_UNANSWERABLE else "")

    def telegram(self, req: Mapping[str, Any]) -> Reply:
        try:
            fields = validate_telegram(req, self.limits)
        except Refused as r:
            return Reply(r.exit_code, f"scoutctl notify telegram: {r.reason}\n", kind=REFUSED)
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
            if (unsendable := self._unanswerable(fields)) is not None:
                return unsendable
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


EXPERIMENT_CLIENT_SLACK_S = 60.0


def _outcome_reply(outcome: Any) -> Reply:
    """A :class:`scout.lab.runner.RunOutcome` as the run reads it: the script's exit and its output's tails."""
    return Reply(outcome.exit, outcome.stderr_tail, outcome.stdout_tail, ran=True)


def _requested_timeout(req: Mapping[str, Any]) -> int | None:
    """The run's own ``timeout_s`` for an experiment: absent, or an int of at least 1 (it only ever lowers)."""
    value = req.get("timeout_s")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise Refused(EXIT_REFUSED, "timeout_s must be a whole number of seconds, at least 1")
    return value


def _one_line(reason: object) -> str:
    """A reason on one line (a status line is read as the last line of stderr)."""
    return " ".join(str(reason).split())


def _emit_experiment(reply: Reply) -> int:
    """Print an experiment reply, then the one status line a run reads last (``experiment ran|refused|not run``)."""
    code = _emit(reply)
    if reply.ran:
        print(f"experiment ran: exit {reply.exit}", file=sys.stderr)
        return code
    last = reply.stderr.rstrip("\n").rsplit("\n", 1)[-1] if reply.stderr.strip() else ""
    if not last.startswith(("experiment refused: ", "experiment not run: ")):
        print(f"experiment not run: {_one_line(last) or 'the broker answered without running it'}", file=sys.stderr)
    return code


def client_experiment(
    slug: str, eid: str, argv: Sequence[str], env: Mapping[str, str] | None = None, *, timeout_s: int | None = None
) -> int:
    """``scoutctl lab experiment run``: the run's broker runs it, or, in the owner's own session, this process.

    Never queued: a scheduled run without an answering broker gets exit 4 and nothing runs. Without a broker or a
    scheduled run (an interactive owner session, unsandboxed) the request is checked as the broker would check it
    and run here, under the owner's limits; the owner's ``experiments_enabled`` switch gates only the broker, not
    this. ``timeout_s`` can only lower the owner's timeout. The last line on
    stderr is always one of ``experiment ran: exit <n>``, ``experiment refused: <reason>`` or
    ``experiment not run: <reason>``.
    """
    env = os.environ if env is None else env
    limits = load_lab_limits(env)
    sock = env.get(ENV_SOCKET, "")
    if timeout_s is not None and timeout_s < 1:
        print("experiment refused: the timeout must be at least 1 second", file=sys.stderr)
        return EXIT_REFUSED
    effective = min(timeout_s, limits.timeout_s) if timeout_s is not None else limits.timeout_s
    if sock:
        req: dict[str, Any] = {"v": PROTOCOL, "verb": "experiment", "slug": slug, "eid": eid, "argv": list(argv)}
        if timeout_s is not None:
            req["timeout_s"] = timeout_s
        try:
            return _emit_experiment(call(sock, req, timeout=effective + EXPERIMENT_CLIENT_SLACK_S))
        except Unreachable:
            print("experiment not run: this run's broker didn't answer", file=sys.stderr)
            return EXIT_NOT_RUN
    if env.get("SCOUT_MODE"):
        print("experiment not run: experiments need the run broker", file=sys.stderr)
        return EXIT_NOT_RUN
    from scout.errors import LabError
    from scout.lab import runner

    vault = _vault(env)
    try:
        slug, eid, args, cap, commit = validate_experiment(
            {"slug": slug, "eid": eid, "argv": list(argv)}, vault, limits, datetime.now(UTC), env
        )
    except Refused as r:
        print(f"experiment refused: {_one_line(r.reason)}", file=sys.stderr)
        return r.exit_code
    try:
        outcome = runner.run_experiment(
            vault,
            slug,
            eid,
            args,
            cap_usd=cap,
            timeout_s=effective,
            env=env,
            commit=commit,
            max_processes=limits.max_processes,
            max_total_bytes=limits.max_total_bytes,
            max_open_files=limits.max_open_files,
        )
    except LabError as e:
        print(f"experiment refused: {_one_line(e)}", file=sys.stderr)
        return e.exit_code
    return _emit_experiment(_outcome_reply(outcome))


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
        with _queue_dirs(vault, *QUEUE_VERBS, FAILED_DIR):
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

    if verb not in QUEUE_VERBS:
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
        with _queue_dirs(vault, *QUEUE_VERBS, FAILED_DIR) as dirs:  # failed/ too: an unsafe queue waits for the owner
            return sum(len(_names(dirs[v])) for v in QUEUE_VERBS)
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
            with _queue_dirs(vault, *QUEUE_VERBS, FAILED_DIR, create=True) as dirs:
                for verb in QUEUE_VERBS:
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

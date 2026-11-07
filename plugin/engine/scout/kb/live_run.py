"""Whose commit is it: a scheduled run's or the owner's, from the process table, not the environment (#595).

The commit hook used to decide "scheduled run" from the commit's environment
alone (``SCOUT_MODE``, ``SCOUT_READER``: :func:`scout.kb.reader.is_scheduled`),
so a run could commit with ``env -u SCOUT_MODE`` or ``SCOUT_MODE=`` and get the
owner's rules. A run can change its environment and delete files, but not the
processes it runs in, so this module reads one ``ps`` snapshot:

- **The run's processes** (:func:`run_processes`): the session lock's holder
  when its argv is a ``run-(scout|dreaming|research).sh``; any
  ``<vault>/scripts/claude-with-retry.sh``; and the run's own ``claude``
  session (``--name``, ``-p`` and ``--max-budget-usd`` with the vault's path in
  its prompt). Zombies don't count. ``scoutctl bootstrap`` takes the same lock,
  but it isn't a runner, so the owner's ``/scout-update`` is never a run; a
  holder PID reused after a crash isn't one either. Killing the runner leaves
  its wrapper and its claude, which still mark the run.
- **Whose commit** (:func:`commit_source`), when a run of this vault is live:
  the run's when the commit's ancestry passes through one of those processes
  (``by: ancestry``), or reaches no interactive host (``by: detached``: a
  double-forked or orphaned ``git`` re-parented to init/launchd). It is the
  owner's when the ancestry reaches an interactive host first: a macOS app
  bundle (a terminal, an IDE, the Claude or Scout app), ``sshd``/``login``, a
  ``tmux``/``screen`` server or a known terminal emulator. A run that hands its
  commit to a host has to detach first, which the PreToolUse gate refuses
  (:func:`run_escape`).

The run marker the lock script writes beside the lock
(``.scout-logs/.scout-session.lock.run``: ``<pid> <mode>``) only names the
mode; deleting it changes nothing here, and the lock script logs it to
``failures.log`` at release. When ``ps`` can't run, a lock naming a live PID
with a marker naming the same PID counts the commit as the run's (fail closed).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LOCK_NAME = ".scout-session.lock"
RUN_MARKER = LOCK_NAME + ".run"
UNKNOWN_MODE = "scheduled"
_MODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")
_PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s?(.*)$")
_RUNNER_RE = re.compile(r"(?:^|[\s/])run-(?:scout|dreaming|research)\.sh(?:\s|$)")
# Interactive hosts by the basename of argv[0] (a trailing ":" dropped, for "tmux: server").
_HOSTS = {
    "sshd", "login", "tmux", "screen", "mosh-server", "zellij",
    "gnome-terminal-server", "konsole", "xterm", "alacritty", "kitty", "wezterm-gui", "foot", "tilix",
    "xfce4-terminal", "terminator", "ghostty", "code", "cursor", "zed",
}  # fmt: skip


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    stat: str
    command: str


@dataclass(frozen=True)
class CommitSource:
    runs: bool  # the commit is the run's
    mode: str  # the live run's mode (from the marker), else UNKNOWN_MODE
    by: str  # "ancestry", "detached" or "host"


def process_table() -> dict[int, Proc]:
    """Every process: one ``ps`` call. Raises when ``ps`` can't run."""
    out = subprocess.run(
        ["ps", "-A", "-w", "-w", "-o", "pid=,ppid=,stat=,command="],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=10,
    ).stdout
    table: dict[int, Proc] = {}
    last: Proc | None = None
    for line in out.splitlines():
        m = _PS_LINE.match(line)
        if m is None:  # an argv with a newline in it (a run's prompt) continues the last row
            if last is not None:
                last = table[last.pid] = Proc(last.pid, last.ppid, last.stat, f"{last.command}\n{line}")
            continue
        last = table[int(m[1])] = Proc(int(m[1]), int(m[2]), m[3], m[4])
    if not table:
        raise OSError("ps listed no processes")
    return table


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if _DIGITS_RE.match(text) else None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def lock_holder(vault: Path) -> int | None:
    text = _read(vault / ".scout-logs" / LOCK_NAME)
    pid = _number(text) if text is not None else None
    return pid if pid is not None and pid > 0 else None


def _marker(vault: Path) -> tuple[int | None, str | None]:
    text = _read(vault / ".scout-logs" / RUN_MARKER)
    fields = text.split() if text else []
    pid = _number(fields[0]) if fields else None
    mode = fields[1] if len(fields) > 1 and _MODE_RE.match(fields[1]) else None
    return pid, mode


def _vault_paths(vault: Path) -> set[str]:
    paths = {str(vault)}
    try:
        paths.add(str(vault.resolve()))
    except OSError:
        pass
    return paths


def run_processes(vault: Path, table: Mapping[int, Proc], *, holder: int | None) -> set[int]:
    """The live processes of a scheduled run of ``vault`` (see the module doc)."""
    roots = _vault_paths(vault)
    wrappers = {f"{r}/scripts/claude-with-retry.sh" for r in roots}
    out: set[int] = set()
    for p in table.values():
        if p.stat.startswith("Z"):
            continue
        cmd = p.command
        if p.pid == holder and _RUNNER_RE.search(cmd):
            out.add(p.pid)
        elif any(w in cmd for w in wrappers):
            out.add(p.pid)
        elif "--name" in cmd and "-p" in cmd and "--max-budget-usd" in cmd and any(r in cmd for r in roots):
            out.add(p.pid)
    return out


def _is_host(p: Proc) -> bool:
    if ".app/Contents/" in p.command:
        return True
    argv0 = p.command.split(None, 1)[0] if p.command.strip() else ""
    return argv0.rsplit("/", 1)[-1].rstrip(":").lower() in _HOSTS


def _ancestry(table: Mapping[int, Proc], start: int) -> list[int]:
    chain: list[int] = []
    pid: int | None = start
    while pid is not None and pid > 1 and pid not in chain and len(chain) < 256:
        chain.append(pid)
        p = table.get(pid)
        pid = p.ppid if p is not None else None
    return chain


def commit_source(
    vault: Path, table: Mapping[int, Proc], *, start: int, holder: int | None | Literal["lock"] = "lock"
) -> CommitSource | None:
    """Whether the commit made by process ``start`` is a live run's, or None when no run of ``vault`` is live.

    ``holder`` is the session lock's PID; by default it is read from the lock.
    """
    held = lock_holder(vault) if holder == "lock" else holder
    run = run_processes(vault, table, holder=held)
    if not run:
        return None
    marker_pid, marker_mode = _marker(vault)
    mode = marker_mode if marker_mode is not None and marker_pid is not None and marker_pid == held else UNKNOWN_MODE
    chain = _ancestry(table, start)
    for pid in chain:
        if pid in run:
            return CommitSource(True, mode, "ancestry")
        p = table.get(pid)
        if p is not None and _is_host(p):
            return CommitSource(False, mode, "host")
    return CommitSource(True, mode, "detached")


def commit_source_now(vault: Path) -> CommitSource | None:
    """:func:`commit_source` for this process, from a fresh ``ps``; fails closed when ``ps`` can't run."""
    try:
        table = process_table()
    except (OSError, subprocess.SubprocessError, ValueError):
        held = lock_holder(vault)
        marker_pid, marker_mode = _marker(vault)
        if held is None or marker_pid != held or not _alive(held):
            return None
        return CommitSource(True, marker_mode or UNKNOWN_MODE, "no-ps")
    return commit_source(vault, table, start=os.getpid())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ----- the PreToolUse gate's check: a run's command that hides it's a run ------------

RUN_VARS = ("SCOUT_MODE", "SCOUT_READER")
_SEGMENTS = re.compile(r"&&|\|\||[;|\n`]|\$\(|\(|\)")
# A lone "&" (background), not "&&", "&>", ">&", "|&" or "2>&1".
_BACKGROUND = re.compile(r"(?<![&>|])&(?![&>])")
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_DETACH = {
    "nohup", "setsid", "disown", "launchctl", "osascript", "open", "tmux", "screen", "script", "at", "batch",
    "daemonize", "start-stop-daemon", "systemd-run", "ssh", "mosh",
}  # fmt: skip
_PREFIX = {"sudo", "command", "exec", "nice", "time", "xargs", "builtin"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def _segments(command: str) -> list[str]:
    """``command`` split at ``;``, ``&&``, ``||``, ``|``, newlines and subshell or substitution
    boundaries, outside quotes."""
    masked = _QUOTED.sub(lambda m: "\0" * len(m[0]), command)
    out, at = [], 0
    for m in _SEGMENTS.finditer(masked):
        out.append(command[at : m.start()])
        at = m.end()
    out.append(command[at:])
    return out


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _sets_run_var(token: str) -> bool:
    m = _ASSIGN.match(token)
    return m is not None and m[1] in RUN_VARS


def _env_escape(args: list[str]) -> tuple[str | None, list[str]]:
    """``env``'s own options and assignments: ("env", _) if they hide the run, else the command after them."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-", "-i", "--ignore-environment") or (a.startswith("-") and not a.startswith("--") and "i" in a[1:]):
            return "env", []
        if a in ("-u", "--unset"):
            if i + 1 < len(args) and args[i + 1] in RUN_VARS:
                return "env", []
            i += 2
            continue
        if a.startswith("--unset=") and a.split("=", 1)[1] in RUN_VARS:
            return "env", []
        if a.startswith("-u") and a[2:] in RUN_VARS:
            return "env", []
        if a in ("-C", "--chdir", "-P", "-S", "--split-string"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        if _ASSIGN.match(a):
            if _sets_run_var(a):
                return "env", []
            i += 1
            continue
        return None, args[i:]
    return None, []


def _words_escape(words: list[str], depth: int) -> str | None:
    while words and _ASSIGN.match(words[0]):
        if _sets_run_var(words[0]):
            return "env"
        words = words[1:]
    while words and words[0] in _PREFIX:
        words = words[1:]
    if not words:
        return None
    name, args = words[0].rsplit("/", 1)[-1], words[1:]
    if name == "env":
        verdict, rest = _env_escape(args)
        return verdict or (_words_escape(rest, depth) if rest else None)
    if name == "unset":
        return "env" if any(a in RUN_VARS for a in args) else None
    if name in ("export", "declare", "typeset", "local", "readonly"):
        unexport = any(a in ("-n", "+x") or (a.startswith("+") and "x" in a) for a in args)
        if any(_sets_run_var(a) or (unexport and a in RUN_VARS) for a in args):
            return "env"
        return None
    if name in _DETACH:
        return "detach"
    if depth < 3 and name in _SHELLS and "-c" in args[:-1]:
        return run_escape(args[args.index("-c") + 1], depth=depth + 1)
    if depth < 3 and name == "eval":
        return run_escape(" ".join(args), depth=depth + 1)
    return None


def run_escape(command: str, *, depth: int = 0) -> str | None:
    """Whether a run's shell command hides that it's a run: "env" (changes SCOUT_MODE/SCOUT_READER or clears
    the environment), "detach" (backgrounds or hands work to another process), else None.

    Only the forms that write count: reading ``$SCOUT_MODE``, ``printenv``, a mention in a commit message or a
    search, and ``2>&1`` all pass.
    """
    if _BACKGROUND.search(_QUOTED.sub("''", command)):
        return "detach"
    verdict: str | None = None
    for segment in _segments(command):
        found = _words_escape(_tokens(segment), depth)
        if found == "env":
            return "env"
        verdict = verdict or found
    return verdict

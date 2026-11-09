"""Whose commit is it: a scheduled run's or the owner's, from the process table, not the environment (#595).

The commit hook used to decide "scheduled run" from the commit's environment
alone (``SCOUT_MODE``, ``SCOUT_READER``: :func:`scout.kb.reader.is_scheduled`),
so a run could commit with ``env -u SCOUT_MODE`` or ``SCOUT_MODE=`` and get the
owner's rules. A run can change its environment and delete files, but not the
processes it runs in, so this module reads one ``ps`` snapshot:

- **A live run** (:func:`run_processes`): the session lock's holder when its
  argv is a ``run-(scout|dreaming|research).sh``; a running
  ``<vault>/scripts/claude-with-retry.sh`` (argv[0], or argv[1] of a shell; a
  command that only names it isn't one); and the run's own ``claude``
  session (``--name``, ``-p`` and ``--max-budget-usd`` with the vault's path in
  its prompt, which ``ps`` shows because the runners export
  ``CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1``). Zombies don't count.
  ``scoutctl bootstrap`` takes the same lock, but it isn't a runner, so the
  owner's ``/scout-update`` is never a run; a holder PID reused after a crash
  isn't one either. A run also stays live while its holder is dead and the lock
  and run marker it left both name it, until the next lock holder clears them
  (not across a reboot): only a crash or a kill leaves that behind.
- **Whose commit** (:func:`commit_source`), when a run of this vault is live:
  the run's when any process in the commit's ancestry is one of those
  (``by: ancestry``), wherever it sits: nothing the owner runs passes through a
  run. Otherwise it is the owner's only when the ancestry reaches an interactive
  host that started before the run did; every other chain (a double-forked or
  orphaned ``git``) is the run's (``by: detached``, or ``stale-lock``). A host
  is judged by its real executable (:func:`exe_path`, from the kernel, never
  ``ps`` or argv, which the caller controls) against a fixed list
  (:func:`is_host_exe`): an app under ``/Applications``, ``sshd``, ``login``,
  ``tmux``, ``screen`` or a known terminal in a system directory. Start times
  defeat a host the run starts itself, and rule out the hook's own Python and
  ``git`` even when they live in an app bundle. An owner who opens a brand-new
  terminal app during a run gets the run's rules until it ends.

The run marker the lock script writes beside the lock
(``.scout-logs/.scout-session.lock.run``: ``<pid> <mode>``) names the mode and
tells a dead holder's run from a crashed ``scoutctl``; the lock script logs a
deleted one to ``failures.log`` at release. When ``ps`` can't run, a lock naming
a live PID with a marker naming the same PID counts as the run's (fail closed).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LOCK_NAME = ".scout-session.lock"
RUN_MARKER = LOCK_NAME + ".run"
UNKNOWN_MODE = "scheduled"
_MODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")
_PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+((?:\d+-)?(?:\d+:)?\d+:\d+)\s?(.*)$")
_SHELL_RE = re.compile(r"(?:^|/)-?(?:ba|z|da|k)?sh$")
_RUNNER_RE = re.compile(r"(?:^|[\s/])run-(?:scout|dreaming|research)\.sh(?:\s|$)")
# Interactive hosts, by the real executable: an app bundle in an Applications folder (Xcode's developer
# tools live in one too, and aren't hosts), or one of these names in a system directory.
_APP_HOST_RE = re.compile(r"^(?:/System|/Users/[^/]+)?/Applications/(?:[^/]+/)*?[^/]+\.app/Contents/")
_HOST_NAMES = {
    "sshd", "sshd-session", "login", "tmux", "screen", "mosh-server", "zellij",
    "gnome-terminal-server", "konsole", "xterm", "alacritty", "kitty", "wezterm-gui", "foot", "tilix",
    "xfce4-terminal", "terminator", "ghostty", "ptyxis", "kgx", "mate-terminal", "lxterminal", "qterminal",
    "urxvt", "rxvt", "code", "cursor", "zed",
}  # fmt: skip
_SYSTEM_DIR_RE = re.compile(
    r"^(?:/s?bin|/usr/(?:local/)?(?:s?bin|libexec)|/usr/lib(?:exec)?/[^/]+|/usr/share/[^/]+|/opt/[^/]+"
    r"|/opt/homebrew/s?bin|/(?:opt/homebrew|usr/local)/Cellar/[^/]+/[^/]+/(?:s?bin|libexec)|/nix/store/[^/]+/s?bin)$"
)


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    stat: str
    command: str
    elapsed: int  # seconds since it started


@dataclass(frozen=True)
class CommitSource:
    runs: bool  # the commit is the run's
    mode: str  # the live run's mode (from the marker), else UNKNOWN_MODE
    by: str  # "ancestry", "detached", "stale-lock", "host" or "no-ps"
    dead_holder: bool = False  # no run process is left: its holder died and left lock and marker


def parse_etime(text: str) -> int:
    """``ps`` etime (``[[dd-]hh:]mm:ss``) in seconds; 0 when it can't be read."""
    m = _ETIME_RE.match(text.strip())
    if m is None:
        return 0
    return int(m[1] or 0) * 86400 + int(m[2] or 0) * 3600 + int(m[3]) * 60 + int(m[4])


def process_table() -> dict[int, Proc]:
    """Every process: one ``ps`` call. Raises when ``ps`` can't run."""
    out = subprocess.run(
        ["ps", "-A", "-w", "-w", "-o", "pid=,ppid=,stat=,etime=,command="],
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
        if m is None or int(m[1]) in table:  # an argv with a newline in it (a run's prompt) continues the last row
            if last is not None:
                last = table[last.pid] = Proc(last.pid, last.ppid, last.stat, f"{last.command}\n{line}", last.elapsed)
            continue
        last = table[int(m[1])] = Proc(int(m[1]), int(m[2]), m[3], m[5], parse_etime(m[4]))
    if not table:
        raise OSError("ps listed no processes")
    return table


def exe_path(pid: int) -> str | None:
    """The process's real executable, from the kernel: ``exec -a`` and symlinks don't change it. None if unknown."""
    if sys.platform == "darwin":
        import ctypes

        try:
            buf = ctypes.create_string_buffer(4096)
            n = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(pid, buf, 4096)
        except OSError:
            return None
        return buf.value.decode(errors="replace") if n > 0 else None
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def is_host_exe(exe: str | None) -> bool:
    """Whether a real executable is an interactive host (see the module doc)."""
    if not exe:
        return False
    if _APP_HOST_RE.match(exe):
        return ".app/Contents/Developer/" not in exe
    folder, _, name = exe.rpartition("/")
    return name in _HOST_NAMES and _SYSTEM_DIR_RE.match(folder) is not None


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


def _starts_word(text: str, word: str) -> bool:
    return text == word or text.startswith(word + " ")


def _runs_script(command: str, script: str) -> bool:
    """Whether a command line is ``script`` running: its argv[0], or argv[1] of a shell (a shebang's form)."""
    if _starts_word(command, script):
        return True
    first, _, rest = command.partition(" ")
    return _SHELL_RE.search(first) is not None and _starts_word(rest.lstrip(), script)


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
        elif any(_runs_script(cmd, w) for w in wrappers):
            out.add(p.pid)
        elif "--name" in cmd and "-p" in cmd and "--max-budget-usd" in cmd and any(r in cmd for r in roots):
            out.add(p.pid)
    return out


def _ancestry(table: Mapping[int, Proc], start: int) -> list[int]:
    chain: list[int] = []
    pid: int | None = start
    while pid is not None and pid > 1 and pid not in chain and len(chain) < 256:
        chain.append(pid)
        p = table.get(pid)
        pid = p.ppid if p is not None else None
    return chain


def _stale_run_age(vault: Path, table: Mapping[int, Proc], held: int | None, now: float) -> int | None:
    """Seconds since a run whose holder died without releasing: lock and marker still name it, and no reboot since."""
    if held is None or _marker(vault)[0] != held:
        return None
    holder = table.get(held)
    if holder is not None and not holder.stat.startswith("Z"):
        return None
    try:
        age = int(now - (vault / ".scout-logs" / LOCK_NAME).stat().st_mtime)
    except OSError:
        return None
    init = table.get(1)
    return None if init is not None and age > init.elapsed else max(age, 0)


def commit_source(
    vault: Path,
    table: Mapping[int, Proc],
    *,
    start: int,
    holder: int | None | Literal["lock"] = "lock",
    exe: Callable[[int], str | None] | None = None,
    now: float | None = None,
) -> CommitSource | None:
    """Whether the commit made by process ``start`` is a live run's, or None when no run of ``vault`` is live.

    ``holder`` is the session lock's PID; by default it is read from the lock. ``exe`` reads a process's real
    executable (default :func:`exe_path`).
    """
    held = lock_holder(vault) if holder == "lock" else holder
    run = run_processes(vault, table, holder=held)
    if run:
        run_age = max(table[pid].elapsed for pid in run)
    else:
        stale = _stale_run_age(vault, table, held, time.time() if now is None else now)
        if stale is None:
            return None
        run_age = stale
    marker_pid, marker_mode = _marker(vault)
    mode = marker_mode if marker_mode is not None and marker_pid is not None and marker_pid == held else UNKNOWN_MODE
    chain = _ancestry(table, start)
    if any(pid in run for pid in chain):
        return CommitSource(True, mode, "ancestry")
    read_exe = exe_path if exe is None else exe
    for pid in chain:
        p = table.get(pid)
        if p is not None and p.elapsed > run_age and is_host_exe(read_exe(pid)):
            return CommitSource(False, mode, "host", dead_holder=not run)
    return CommitSource(True, mode, "detached" if run else "stale-lock", dead_holder=not run)


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
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][\w.-]*))")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_DETACH = {
    "nohup", "setsid", "disown", "launchctl", "osascript", "open", "tmux", "screen", "script", "at", "batch",
    "daemonize", "start-stop-daemon", "systemd-run", "ssh", "mosh",
}  # fmt: skip
_PREFIX = {"sudo", "command", "exec", "nice", "time", "xargs", "builtin"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def _drop_heredoc_bodies(command: str) -> str:
    """``command`` without its here-documents' bodies, which are text and not commands."""
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        quoted = [m.span() for m in _QUOTED.finditer(line)]
        for m in _HEREDOC.finditer(line):
            if any(a <= m.start() < b for a, b in quoted):
                continue  # "<<" inside a quoted argument
            word = m[2] or m[3] or m[4]
            for j in range(i, len(lines)):
                if (lines[j].lstrip("\t") if m[1] else lines[j]) == word:
                    i = j + 1
                    break  # an unterminated one keeps the rest: that text is still scanned
    return "\n".join(out)


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
    search, ``2>&1``, and anything in a quoted argument or a here-document's body (a Telegram wrap's ``&amp;``)
    all pass.
    """
    command = _drop_heredoc_bodies(command)
    if _BACKGROUND.search(_QUOTED.sub("''", command)):
        return "detach"
    verdict: str | None = None
    for segment in _segments(command):
        found = _words_escape(_tokens(segment), depth)
        if found == "env":
            return "env"
        verdict = verdict or found
    return verdict

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
from typing import Any, Literal

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


# Claude Code's run argv words (#595), kept here too so liveness never depends on the registry importing.
_CLAUDE_MARKERS: tuple[str, ...] = ("--name", "-p", "--max-budget-usd")


def marker_sets() -> tuple[tuple[str, ...], ...]:
    """Each registered agent runtime's run argv words (spec 2026-11-13 §8.5, Liveness); Claude Code's always."""
    sets: list[tuple[str, ...]] = [_CLAUDE_MARKERS]
    try:
        from scout.agent_runtimes import registry

        for rid in registry.ids():
            words = tuple(registry.get(rid).process_markers().argv_words)
            if words and words not in sets:
                sets.append(words)
    except Exception:  # noqa: BLE001 - a broken adapter must not blind liveness to Claude runs
        pass
    return tuple(sets)


def run_processes(vault: Path, table: Mapping[int, Proc], *, holder: int | None) -> set[int]:
    """The live processes of a scheduled run of ``vault`` (see the module doc)."""
    roots = _vault_paths(vault)
    wrappers = {f"{r}/scripts/claude-with-retry.sh" for r in roots}
    markers = marker_sets()
    out: set[int] = set()
    for p in table.values():
        if p.stat.startswith("Z"):
            continue
        cmd = p.command
        if p.pid == holder and _RUNNER_RE.search(cmd):
            out.add(p.pid)
        elif any(_runs_script(cmd, w) for w in wrappers):
            out.add(p.pid)
        elif any(all(w in cmd for w in words) for words in markers) and any(r in cmd for r in roots):
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
        held = run_without_ps(vault)
        if held is None:
            return None
        return CommitSource(True, held[1] or UNKNOWN_MODE, "no-ps")
    return commit_source(vault, table, start=os.getpid())


def run_without_ps(vault: Path) -> tuple[int, str | None] | None:
    """The live run, judged without ``ps`` (a sandboxed run can't start the setuid ``/bin/ps``).

    ``(holder PID, mode)`` when the lock names a holder, the run marker names
    the same PID, and that PID is alive; None otherwise. The one rule for every
    caller that must decide "is a run live" with no process table.
    """
    held = lock_holder(vault)
    marker_pid, marker_mode = _marker(vault)
    if held is None or marker_pid != held or not _alive(held):
        return None
    return held, marker_mode


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
_SEGMENTS = re.compile(r"&&|\|\||[;\n`]|(?<!>)\||\$\(|\(|\)")  # ">|" is a redirect, not a pipe
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
_KEYWORDS = {"{", "!", "if", "then", "elif", "else", "do", "while", "until"}
# An unquoted redirect and its target. Found in the masked text, so a quoted ">" is never one.
_REDIRECT = re.compile(r"(?:(?<=\s)\d+|&)?(>>|>\||>&|>|<<<|<<-?|<&|<>|<)\s*([^\s<>|]*)")

# What skips or redirects the commit hooks (#607). Git takes a unique prefix of a long option.
_NO_VERIFY = "--no-verify"
_NO_V = "--no-v"
_RUNS_HOOKS = {"commit", "merge", "rebase", "am", "cherry-pick", "revert", "push", "pull"}
# Commands that run git for you: their own options come before it, so look for git among their words.
_GIT_WRAPPERS = _PREFIX | {"timeout", "gtimeout", "caffeinate", "stdbuf", "ionice", "flock", "chronic", "find", "watch"}
_GIT_VALUE_OPTIONS = {"-C", "--git-dir", "--work-tree", "--namespace", "--attr-source"}
# Commits and refs written without `git commit`, so with no hook.
_WRITES_COMMITS = {"commit-tree", "fast-import", "filter-branch", "filter-repo"}
_CONFIG_READS = {
    "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l",
    "get", "list",
}  # fmt: skip
GIT_CONFIG_FORM = "GIT_CONFIG_*"
# What a run never does to the owner's agents' mirrors: link, unlink, deploy keys and re-trusting the link state
# are the owner's, at the keyboard (#550; each command also refuses a scheduled run itself).
DELEGATE_FORM = "scoutctl delegate"
_DELEGATE_WRITES = {"link", "unlink", "key", "trust-state"}


def _python_module(args: list[str]) -> tuple[str, list[str]] | None:
    """The module ``python [options] -m MODULE ARGS`` runs, and its arguments; None when it runs no module.

    Short options are read as Python reads them: ``-Pm scout``, ``-IPm scout``, ``-mscout``, ``-W ignore -m scout``.
    A letter that takes a value (``c``, ``m``, ``W``, ``X``) takes the rest of its cluster, else the next word.
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--" or not a.startswith("-") or a.startswith("--") or a == "-":
            return None  # a script, stdin, or the end of python's own options
        for at, letter in enumerate(a[1:], 1):
            if letter in "cmWX":
                value, used = (a[at + 1 :], 1) if at + 1 < len(a) else (args[i + 1] if i + 1 < len(args) else "", 2)
                if letter == "m":
                    return value, args[i + used :]
                if letter == "c":
                    return None  # an inline program, not a module
                i += used
                break
        else:
            i += 1
    return None


def _delegate_write(name: str, args: list[str]) -> bool:
    """``scoutctl delegate link|unlink|key|trust-state``, by any path to scoutctl or as ``python -m scout``."""
    if name == "scoutctl":
        rest = args
    elif name.startswith("python") and (module := _python_module(args)) is not None and module[0] == "scout":
        rest = module[1]
    else:
        return False
    words = [a for a in rest if not a.startswith("-")]
    return words[:1] == ["delegate"] and len(words) > 1 and words[1] in _DELEGATE_WRITES


_GIT_CONFIG_VAR = re.compile(r"^GIT_CONFIG(?!_NOSYSTEM=)[A-Z0-9_]*=")
# A short -n means no-verify only on these: (letters that take a value, letters that take an attached one).
_N_IS_NO_VERIFY = {"commit": ("mFCct", "uS"), "am": ("", "SCp")}
# Long options whose value is free text, and a merge's short ones: "git commit --author --no-verify" names no flag.
_VALUE_OPTIONS = {
    "commit": {
        "--message", "--file", "--reuse-message", "--reedit-message", "--fixup", "--squash", "--template", "--author",
        "--date", "--cleanup", "--trailer", "--pathspec-from-file",
    },
    "merge": {"-m", "-F", "-s", "-X", "--message", "--file", "--strategy", "--strategy-option", "--into-name"},
}  # fmt: skip
# Writing these changes the hooks. Only a command that writes counts: reading them is fine.
_HOOK_FILE = re.compile(r"(?:^|/)\.git/(hooks(?:/|$)|config$)")
# And these decide which of the agent's hooks run, or redirect git's from the user's config
# (scout.kb.hook_paths names them for the write tools; here by spelling, as a shell path can't be resolved).
_AGENT_SETTINGS_FILE = re.compile(
    r"(?:^|/)\.claude/(?:settings(?:\.local)?\.json$|plugins(?:/|$))|(?:^|/)\.(?:codex|gemini|agents|grok|cursor)(?:/|$)",
    re.IGNORECASE,
)
_USER_GIT_CONFIG_FILE = re.compile(r"(?:^|/)\.gitconfig$|(?:^|/)\.config/git(?:/|$)", re.IGNORECASE)

_WRITERS = {"tee", "rm", "unlink", "mv", "chmod", "chown", "chflags", "touch", "truncate", "rmdir", "shred"}
_COPIERS = {"cp", "install", "ln", "rsync"}  # the last argument is the one written
_SED_IN_PLACE = re.compile(r"^(?:--in-place|-[A-Za-z]*i)")


# A here-document's operator becomes `<<__scout_hd_N__` and its body is kept aside: the segment that names the
# marker owns it (an interpreter's program can be one). An unquoted delimiter means the shell expands the body.
_HD_MARK = re.compile(r"__scout_hd_(\d+)__")
# What a command or process substitution leaves in the text: a variable no command can set (_substitute never
# fills it in), so a path built from one is unresolved; the substitution's own command is checked on its own.
SUBST = "$__scout_subst__"
_SUBST_NAME = SUBST[1:]
_HEREDOC_AT = re.compile(r"<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][\w.-]*))")


def _split_heredocs(command: str, start: int = 0) -> tuple[str, list[tuple[str, bool]]]:
    """``command`` with each here-document's body cut out and its operator marked, and the bodies in order as
    ``(text, expands)``: ``expands`` when the delimiter is unquoted (the shell runs its substitutions)."""
    lines = command.split("\n")
    out: list[str] = []
    bodies: list[tuple[str, bool]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        quoted = [m.span() for m in _QUOTED.finditer(line)]
        pieces: list[str] = []
        found: list[tuple[str, bool, bool]] = []
        at = 0
        for m in _HEREDOC.finditer(line):
            if any(a <= m.start() < b for a, b in quoted):
                continue  # "<<" inside a quoted argument
            pieces += [line[at : m.start()], f"<<__scout_hd_{start + len(bodies) + len(found)}__"]
            at = m.end()
            found.append((m[2] or m[3] or m[4], bool(m[1]), m[4] is not None and "\\" + m[4] not in m[0]))
        out.append("".join([*pieces, line[at:]]))
        for word, strip_tabs, expands in found:
            body: list[str] = []
            for j in range(i, len(lines)):
                if (lines[j].lstrip("\t") if strip_tabs else lines[j]) == word:
                    i = j + 1
                    break
                body.append(lines[j])
            else:
                body = []  # unterminated: the rest stays in the text, still scanned as commands
            bodies.append(("\n".join(body), expands))
    return "\n".join(out), bodies


def _close(s: str, i: int, kind: str) -> int:
    """The index of what closes a ``kind`` (``)``, a backtick or ``"``) opened just before ``i``, else ``len(s)``.

    Quotes, nested substitutions, comments and here-documents inside it are skipped the way the shell does.
    """
    n = len(s)
    pending: list[tuple[str, bool]] = []
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if kind == '"':
            if c == '"':
                return i
            if s.startswith("$(", i):
                i = _close(s, i + 2, ")") + 1
            elif c == "`":
                i = _close(s, i + 1, "`") + 1
            else:
                i += 1
            continue
        if c == kind:
            return i
        if c == "'":
            j = s.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            i = _close(s, i + 1, '"') + 1
        elif c == "`":
            i = _close(s, i + 1, "`") + 1
        elif c == "(":
            i = _close(s, i + 1, ")") + 1
        elif c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("<<", i) and not s.startswith("<<<", i) and (m := _HEREDOC_AT.match(s, i)):
            pending.append((m[2] or m[3] or m[4], bool(m[1])))
            i = m.end()
        elif c == "\n" and pending:
            i = _skip_bodies(s, i + 1, pending)
            pending = []
        else:
            i += 1
    return n


def _skip_bodies(s: str, i: int, pending: list[tuple[str, bool]]) -> int:
    for word, strip_tabs in pending:
        while i < len(s):
            end = s.find("\n", i)
            line = s[i : end if end >= 0 else len(s)]
            i = len(s) if end < 0 else end + 1
            if (line.lstrip("\t") if strip_tabs else line) == word:
                break
    return i


def _substitutions(s: str, *, quotes: bool = True) -> list[tuple[int, int, int, int]]:
    """The outermost ``$( … )``, backtick and ``<( … )`` / ``>( … )`` in ``s``: (start, end, body start, body end).

    Found outside single quotes, and inside double quotes too: ``"$(…)"`` runs its command all the same.
    ``quotes=False`` for an unquoted here-document's body, where quotes and ``#`` are only text.
    """
    spans: list[tuple[int, int, int, int]] = []
    n, i, in_dq = len(s), 0, False
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if not quotes:
            pass
        elif in_dq:
            if c == '"':
                in_dq = False
                i += 1
                continue
        elif c == "'":
            j = s.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        elif c == '"':
            in_dq = True
            i += 1
            continue
        elif c == "#" and (i == 0 or s[i - 1] in " \t\n;&|()"):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if s.startswith("$(", i):
            end = _close(s, i + 2, ")")
            spans.append((i, min(end + 1, n), i + 2, end))
            i = end + 1
        elif c == "`":
            end = _close(s, i + 1, "`")
            spans.append((i, min(end + 1, n), i + 1, end))
            i = end + 1
        elif quotes and not in_dq and c in "<>" and s.startswith("(", i + 1) and (i == 0 or s[i - 1] not in "<>"):
            end = _close(s, i + 2, ")")
            spans.append((i, min(end + 1, n), i + 2, end))
            i = end + 1
        else:
            i += 1
    return spans


def _segments_with_ops(command: str) -> list[tuple[str, str]]:
    """Each segment of ``command`` (see :func:`_segments`) with the operator before it ("" for the first)."""
    masked = _QUOTED.sub(lambda m: "\0" * len(m[0]), command)
    out: list[tuple[str, str]] = []
    at, op = 0, ""
    for m in _SEGMENTS.finditer(masked):
        out.append((command[at : m.start()], op))
        at, op = m.end(), m[0]
    out.append((command[at:], op))
    return out


def _redirects(segment: str) -> tuple[str, list[str], list[str], list[str]]:
    """``segment`` without its unquoted redirects; the files they write, the files they read, and here-strings."""
    masked = _QUOTED.sub(lambda m: "\0" * len(m[0]), segment)
    kept: list[str] = []
    writes: list[str] = []
    reads: list[str] = []
    strings: list[str] = []
    at = 0
    for m in _REDIRECT.finditer(masked):
        kept.append(segment[at : m.start()])
        words = _tokens(segment[m.start(2) : m.end(2)])
        if ">" in m[1]:
            writes.extend(words)
        elif m[1] == "<<<":
            strings.extend(words)
        elif m[1] in ("<", "<>", "<&"):
            reads.extend(words)
        at = m.end()
    kept.append(segment[at:])
    return "".join(kept), writes, reads, strings


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
        if a == "-S" or a == "--split-string":
            return _env_escape(_tokens(args[i + 1] if i + 1 < len(args) else "") + args[i + 2 :])
        if a.startswith("--split-string="):
            return _env_escape(_tokens(a.split("=", 1)[1]) + args[i + 1 :])
        if a in ("-C", "--chdir", "-P"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        if _ASSIGN.match(a):
            if _sets_run_var(a):
                return "env", []
            if _GIT_CONFIG_VAR.match(a):
                return GIT_CONFIG_FORM, []
            i += 1
            continue
        return None, args[i:]
    return None, []


def _dynamic(word: str) -> bool:
    """Whether the shell fills a word in, so what it says can't be read here."""
    return "$" in word or "`" in word


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _git_words(words: list[str]) -> list[str] | None:
    """The arguments of the git call ``words`` makes, directly or through a wrapper, else None."""
    name = _basename(words[0])
    if name == "git":
        return words[1:]
    if name.startswith("git-"):  # the dashed form of a subcommand; a script named git-*.sh matches no subcommand
        return [name[4:], *words[1:]]
    if name in _GIT_WRAPPERS:
        for i, word in enumerate(words[1:], 1):
            if _basename(word) == "git" or _basename(word).startswith("git-"):
                return _git_words(words[i:])
    return None


# Config keys that run a command of their choosing (keys don't depend on case): setting one is as good as a hook.
_COMMAND_KEYS = {
    "core.fsmonitor", "core.sshcommand", "core.pager", "core.editor", "core.attributesfile", "core.askpass",
    "credential.helper", "init.templatedir", "sequence.editor", "diff.external", "gpg.program",
    "uploadpack.packobjectshook", "receive.procreceiverefs",
}  # fmt: skip
_COMMAND_KEY_SUFFIXES = (".helper", ".textconv", ".driver", ".process", ".clean", ".smudge", ".command", ".cmd")
_COMMAND_KEY_PREFIXES = ("pager.", "filter.", "credential.", "diff.", "merge.", "gpg.")


def _config_form(key: str) -> str | None:
    """The name of a config key that changes which hooks or commands run (keys don't depend on case)."""
    key = key.lower()
    if key == "core.hookspath":
        return "core.hooksPath"
    if key.startswith("alias."):
        return "alias.*"
    if key == "include.path" or (key.startswith("includeif.") and key.endswith(".path")):
        return "include.path"
    if key in _COMMAND_KEYS or (key.startswith(_COMMAND_KEY_PREFIXES) and key.endswith(_COMMAND_KEY_SUFFIXES)):
        return key
    if key.startswith("pager."):
        return key
    return None


def _config_flag_form(flag: str, setting: str) -> str | None:
    key = setting.split("=", 1)[0]
    form = _config_form(key)
    if form is not None:
        return f"{flag} {form}"
    return f"{flag} <unknown>" if _dynamic(key) else None


def _short_no_verify(subcommand: str, flag: str) -> tuple[bool, bool]:
    """Whether a short-option cluster has -n in it, and whether its last letter takes the next word as its value."""
    takes_value, takes_attached = _N_IS_NO_VERIFY[subcommand]
    for at, letter in enumerate(flag[1:], 1):
        if letter == "n":
            return True, False
        if letter in takes_value:
            return False, at == len(flag) - 1
        if letter in takes_attached:
            break
    return False, False


def _no_verify_form(subcommand: str, args: list[str]) -> str | None:
    values = _VALUE_OPTIONS.get(subcommand, set())
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == "--":
            return None
        elif a.startswith(_NO_V) and _NO_VERIFY.startswith(a):
            return _NO_VERIFY
        elif a in values:
            skip = True
        elif subcommand in _N_IS_NO_VERIFY and a.startswith("-") and not a.startswith("--"):
            has_n, skip = _short_no_verify(subcommand, a)
            if has_n:
                return "-n"
    return None


def _moves_a_branch(args: list[str]) -> bool:
    """Whether ``git update-ref args`` writes a branch: named, or a ref that can't be read here."""
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == "--stdin":
            return True
        elif a == "-m":
            skip = True
        elif not a.startswith("-"):
            return a in ("HEAD", "@") or a.startswith("refs/heads/") or _dynamic(a)
    return True


def _writes_a_commit_object(args: list[str]) -> bool:
    return any(a in ("-tcommit", "--type=commit") for a in args) or any(
        a in ("-t", "--type") and b == "commit" for a, b in zip(args, args[1:], strict=False)
    )


_CONFIG_SCOPES = {"--global", "--system", "--file", "-f", "--worktree", "--blob"}


_CONFIG_VALUE_FLAGS = {"--file", "-f", "--blob", "--type", "--default", "--comment"}
_CONFIG_WRITE_FLAGS = {
    "--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section", "-e", "--edit",
}  # fmt: skip
_CONFIG_WRITE_SUBCOMMANDS = {"set", "unset", "rename-section", "remove-section", "edit"}


def _config_writes(args: list[str]) -> bool:
    """Whether a `git config` call writes: a write flag or subcommand, or a key with a value (`key` alone reads)."""
    positional: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in _CONFIG_VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            positional.append(a)
    if any(a in _CONFIG_WRITE_FLAGS for a in args):
        return True
    return bool(positional) and (positional[0] in _CONFIG_WRITE_SUBCOMMANDS or len(positional) >= 2)


def _git_config_form(args: list[str]) -> str | None:
    if any(a in _CONFIG_READS for a in args) or not _config_writes(args):
        return None
    for a in args:
        form = _config_form(a)
        if form is not None:
            return f"git config {form}"
    scope = next((a for a in args if a in _CONFIG_SCOPES or a.startswith("--file=")), None)
    if scope is not None and scope != "--blob" and any(not a.startswith("-") for a in args):
        return f"git config {scope.split('=', 1)[0]}"  # any key: the user's or another config file
    return None


def _git_escape(args: list[str]) -> str | None:
    """The form of a git call that skips or redirects the commit hooks, else None."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-c", "--config-env"):
            form = _config_flag_form(a, args[i + 1] if i + 1 < len(args) else "$")  # a substitution left no setting
            i += 2
        elif a.startswith("--config-env="):
            form = _config_flag_form("--config-env", a.split("=", 1)[1])
            i += 1
        else:
            form = None
            i += 2 if a in _GIT_VALUE_OPTIONS else 1
        if form is not None:
            return form
    if i >= len(args):
        return None
    subcommand, rest = args[i], args[i + 1 :]
    if _dynamic(subcommand):
        return "git <unknown>"
    if subcommand in _WRITES_COMMITS:
        return subcommand
    if subcommand == "update-ref":
        return subcommand if _moves_a_branch(rest) else None
    if subcommand == "hash-object":
        return "hash-object -t commit" if _writes_a_commit_object(rest) else None
    if subcommand == "config":
        return _git_config_form(rest)
    return _no_verify_form(subcommand, rest) if subcommand in _RUNS_HOOKS else None


# Set by run_escape for the call in progress: decides a written, removed or linked path against
# scout.kb.hook_paths (resolving symlinks, so `ln -s` then `echo >` is caught). None: spelling only.
_PROTECT: Callable[[str, bool], str | None] | None = None
# And whether reading a path reads one of Scout's secrets (best effort: only the paths a command spells out).
_SECRET: Callable[[str], str | None] | None = None
_REMOVERS = {"rm", "rmdir", "unlink", "shred", "mv"}
# Per call: how many paths were checked (above the cap the command is denied as too complex to check, so a run
# can't make the gate slow enough to time out: review of #637), the shell variables set so far
# (`d=.claude; echo > $d/x`, each with every value it can take: a `for` loop's words), and the here-documents.
MAX_CHECKED_PATHS = 256
# How deep `sh -c`, `eval` and substitutions nest before a run's command counts as too complex to check.
MAX_DEPTH = 6
_STATE: dict[str, Any] = {"checked": 0, "secrets": 0, "vars": {}, "bodies": []}
_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
# An interpreter given its program inline or on stdin.
_INTERPRETER = re.compile(r"^(python[0-9.]*|pypy[0-9.]*|perl[0-9.]*|node|nodejs|ruby|php|deno|bun)$")
_MODULE_FLAGS = {"-m"}  # python -m module: a program from a file, not inline
# Each quote style scanned on its own, so a single-quoted path inside a double-quoted program is still seen.
_PATH_LITERALS = (
    re.compile(r"'([^'\n]{1,512})'"),
    re.compile(r'"([^"\n]{1,512})"'),
    re.compile(r"""(?<![\w.$-])((?:~|\.{1,2}|[\w.@-]+)?/[^\s'"();|&<>,]+)"""),
)
# What an inline program does that writes, removes or links files, or starts another program (which can do any
# of that). A program with none of these is only read: its paths aren't checked (review round 3, must-fix 2).
_PROGRAM_WRITES = re.compile(
    r"""\.write_(?:text|bytes)\s*\(
    |\bos\.(?:replace|renames?|remove|unlink|symlink|link|rmdir|removedirs|truncate|mkfifo|mknod|chmod|chown|lchown
        |utime|system|popen|exec\w*|spawn\w*|posix_spawnp?)\s*\(
    |\bos\.open\s*\([^)]*O_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)
    |\bshutil\.(?:copy\w*|move|rmtree|chown|make_archive|unpack_archive)\s*\(
    |\.(?:unlink|rename|symlink_to|hardlink_to|link_to|touch|chmod|rmdir)\s*\(
    |\bsubprocess\b|\bPopen\s*\(|\bpty\.spawn\b
    |\b(?:writeFile|appendFile|copyFile|createWriteStream|rename|unlink|rm|rmdir|symlink|link|truncate|cp|chmod|exec
        |spawn|execFile|write|open|mkdtemp)Sync\b|\b(?:writeFile|appendFile|copyFile|createWriteStream)\b|\bchild_process\b
    |\bFile\.(?:write|delete|unlink|rename|symlink|link|chmod|truncate|new)\b|\bIO\.(?:write|popen)\b
    |\bFileUtils\b|\bDir\.(?:mkdir|rmdir|delete|unlink)\b|%x[({\[]
    |\bDeno\.(?:write\w*|remove|rename|symlink|link|truncate\w*|chmod\w*|copyFile\w*|create\w*|open\w*|run|Command)\b
    |\b(?:system|exec|qx|shell_exec|passthru|proc_open|popen|file_put_contents|fopen|unlink|rename|symlink|link
        |copy|tempnam|chmod|truncate)\s*[({]
    |\b(?:unlink|rename|symlink|link|truncate|chmod)\s+[$"'@]
    """,
    re.VERBOSE,
)
# open() with a mode that writes (Python, Ruby: 'w', 'a', 'x', '+'; Perl: '>', '>>', '+<', '|').
_OPEN_CALL = re.compile(r"\bopen\s*\(?")
_WRITE_MODE = re.compile(
    r"""(['"])\s*(?:[rbtU]*[wax+][rbt+]*|>>?|\+[<>]|\|-?|-\|)\s*\1|(['"])\s*>>?[^'"\n]+\2|\bmode\s*="""
)
# Calls whose arguments are what a program writes: a runner script named here is written, not run (must-fix 2d).
_WRITE_TARGET_CALL = re.compile(
    r"""\b(?:open|fopen|writeFileSync|writeFile|appendFileSync|appendFile|createWriteStream|file_put_contents
        |copyFileSync|copyFile|renameSync|unlinkSync|symlinkSync|rmSync|cpSync|write_text|write_bytes|symlink_to
        |hardlink_to|touch|unlink|rename|symlink|link|truncate|chmod)\s*\(
    |\b(?:File|IO)\.(?:write|open|delete|unlink|rename|symlink|link)\s*\(
    |\bos\.(?:replace|renames?|remove|unlink|symlink|link|truncate|chmod)\s*\(
    |\bshutil\.(?:copy\w*|move|rmtree)\s*\(
    |\bPath\s*\(""",
    re.VERBOSE,
)
# Runs call these all the time, from the shell and from their programs: naming one isn't writing it.
_INVOKED = {"scoutctl", "git-safe-commit.sh"}
# 'a' + 'b', 'a' 'b' -> 'ab': a protected name split across literals is still that name.
_ADJACENT_LITERALS = re.compile(r"""(['"])\s*\+?\s*(['"])""")
_SECRET_WORDS = re.compile(r"\.scout-secrets|SCOUT_SECRETS_DIR", re.IGNORECASE)
_TELEGRAM_AFTER = re.compile(r"""^[\s'"/,)+]*(?:telegram-bot-token|telegram-chat-id)\b""")
# Commands that only list or test a path, never read what is in it.
_LISTS_ONLY = {"ls", "stat", "test", "[", "[["}
# Parameters that only ever hold digits: a path built from one can't name a protected file.
_DIGIT_PARAMS = re.compile(r"\$\$|\$\{?(?:RANDOM|PPID|SECONDS|EPOCHSECONDS|BASHPID)\b\}?")


def _substitute(path: str) -> list[str]:
    """``path`` with the variables this command set put in, once for each value they can take (a ``for`` loop's
    words); unknown ones stay as written, and so does a command substitution's placeholder."""
    known: dict[str, list[str]] = _STATE["vars"]
    names = [n for n in dict.fromkeys(m[1] or m[2] for m in _VAR.finditer(path)) if n in known and known[n]]
    out = [path]
    for name in names:
        out = [_put(one, name, value) for one in out for value in known[name]][:64]
    return out


def _put(text: str, name: str, value: str) -> str:
    """``text`` with ``$name`` / ``${name}`` replaced by ``value``."""

    def one(m: re.Match[str]) -> str:
        return value if (m[1] or m[2]) == name else m[0]

    return _VAR.sub(one, text)


def _set_var(name: str, values: list[str]) -> None:
    if name != _SUBST_NAME:
        _STATE["vars"][name] = values


def _record_vars(words: list[str]) -> None:
    """Remember what this segment sets: ``VAR=…`` alone, ``export``/``local``/… ``VAR=…``, a ``for`` loop's words;
    ``read``, ``mapfile`` and ``printf -v`` set a value nobody can know."""
    while words and words[0] in _KEYWORDS:
        words = words[1:]
    if not words:
        return
    if all(_ASSIGN.match(w) for w in words):
        assignments = words
    elif words[0] in ("export", "declare", "typeset", "local", "readonly"):
        assignments = [w for w in words[1:] if _ASSIGN.match(w)]
    elif words[0] == "for" and len(words) >= 2:
        values = [v for w in words[3:] for v in _substitute(w)] if words[2:3] == ["in"] else [SUBST]
        _set_var(words[1], values or [SUBST])
        return
    elif words[0] in ("read", "mapfile", "readarray") or (words[0] == "printf" and "-v" in words[1:-1]):
        names = words[words.index("-v") + 1 : words.index("-v") + 2] if words[0] == "printf" else words[1:]
        for name in names:
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                _set_var(name, [SUBST])
        return
    else:
        return
    for word in assignments:
        name, _, value = word.partition("=")
        _set_var(name, _substitute(value))


def _program_flags(interpreter: str, args: list[str]) -> tuple[list[str], bool]:
    """An interpreter's inline programs (``-c``/``-e`` values) and whether it reads its program from stdin."""
    python = interpreter.startswith(("python", "pypy"))
    letters = "c" if python else "r" if interpreter == "php" else "eE"
    if interpreter == "deno" and args[:1] == ["eval"]:
        return args[1:2], False
    programs: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        node_print = interpreter in ("node", "nodejs", "bun") and a == "-p"
        if a in ("--eval", "--print") or node_print or re.fullmatch(rf"-[A-Za-z]*[{letters}]", a):
            if i + 1 < len(args):
                programs.append(args[i + 1])
            i += 2
            if python:
                break  # python -c: the rest is the program's argv
            continue
        if re.fullmatch(rf"-[{letters}].+", a):
            programs.append(a[2:])
            if python:
                break
            i += 1
            continue
        if a == "-":
            return programs, True
        if a in _MODULE_FLAGS or not a.startswith("-"):
            return programs, False  # a module or a script file: its program isn't in the command
        i += 1
    return programs, not programs


def _stdin_programs(segment: str, herestrings: list[str], fed: str | None) -> list[str]:
    """What a program reading its program from stdin gets: this segment's here-documents and here-strings, else
    the segment piped into it (``fed``: its text, and its own here-documents)."""
    bodies = _segment_bodies(segment)
    if bodies or herestrings:
        return [*bodies, *herestrings]
    if fed is not None:
        return [fed, *_segment_bodies(fed)]
    return []


def _segment_bodies(segment: str) -> list[str]:
    bodies: list[tuple[str, bool]] = _STATE["bodies"]
    return [bodies[int(m[1])][0] for m in _HD_MARK.finditer(segment) if int(m[1]) < len(bodies)]


def _program_writes(program: str) -> bool:
    if _PROGRAM_WRITES.search(program):
        return True
    return _OPEN_CALL.search(program) is not None and _WRITE_MODE.search(program) is not None


def _write_targets(program: str) -> set[str]:
    """The string literals inside a writing call's arguments (``open('x', 'w')``, ``Path('x').write_text``)."""
    out: set[str] = set()
    for m in _WRITE_TARGET_CALL.finditer(program):
        end = min(_close(program, m.end(), ")"), m.end() + 2000)
        for pattern in _PATH_LITERALS[:2]:
            out.update(lit[1] for lit in pattern.finditer(program[m.end() : end]))
    return out


def _inline_paths(raw: str) -> list[str]:
    """The string literals in a program's text that could be a path: quoted, no whitespace, with a ``/`` or a
    leading ``~`` (a Perl mode prefix, ``">.git/x"``, dropped). Prose that mentions a path isn't one."""
    out: list[str] = []
    for pattern in _PATH_LITERALS[:2]:
        for m in pattern.finditer(raw):
            text = re.sub(r"^(?:\+?[<>]{1,2}|\|)\s*", "", m[1].strip())
            if text and not any(ch.isspace() for ch in text) and ("/" in text or text.startswith("~")):
                if text not in out:
                    out.append(text)
    return out[:MAX_CHECKED_PATHS]


# A program that hands a string to a shell: each of its string literals may be a shell command.
_RUNS_SHELL = re.compile(
    r"shell\s*=\s*True|\bos\.(?:system|popen)\b|\bgetoutput\b|\bgetstatusoutput\b|\b(?:execSync|exec|system"
    r"|shell_exec|passthru|popen|qx)\s*[({]|%x[({\[]"
)


def _program_escape(program: str, cwd: str, depth: int = 0) -> str | None:
    """Whether an inline program writes what decides which checks run, or reads a secret (a run's call only)."""
    from scout.kb import hook_paths

    joined = _ADJACENT_LITERALS.sub("", program)
    if _SECRET is not None:
        for m in _SECRET_WORDS.finditer(joined):
            if not _TELEGRAM_AFTER.match(joined[m.end() : m.end() + 64]):
                return hook_paths.SECRETS
    if not _program_writes(joined):
        return None
    if _RUNS_SHELL.search(joined):
        for pattern in _PATH_LITERALS[:2]:
            for lit in pattern.finditer(joined):
                found = _run_escape(lit[1], depth + 1) if any(ch.isspace() for ch in lit[1]) else None
                if found is not None and found != "detach":
                    return found
    targets = _write_targets(joined)
    for path in _inline_paths(joined):
        if _dynamic(path):
            continue  # "$" in a program's string isn't a shell variable (must-fix 2c)
        if _basename(path.rstrip("/")) in _INVOKED and path not in targets:
            continue  # scripts/scoutctl run, not written (must-fix 2d)
        found = _check(path, False, cwd)
        if found is not None:
            return found
    # A program that writes and names a protected file however it builds the path: os.path.join, Path / "…",
    # os.sep, concatenation (should-fix 3). chr() and base64 still get past this; the OS sandbox (1c) doesn't.
    # Only a literal that could be a path counts (no whitespace): a note's prose may name a runner script.
    for pattern in _PATH_LITERALS[:2]:
        for lit in pattern.finditer(joined):
            if not any(ch.isspace() for ch in lit[1]) and hook_paths.protected_token(lit[1]):
                return hook_paths.INLINE_WRITE
    return None


def _check(path: str, removes: bool, cwd: str) -> str | None:
    """Ask _PROTECT about one path (each value its variables can take), counting it against the per-call cap."""
    from scout.kb.hook_paths import TOO_MANY

    assert _PROTECT is not None
    for one in _substitute(path):
        _STATE["checked"] += 1
        if _STATE["checked"] > MAX_CHECKED_PATHS:
            return TOO_MANY
        found = _PROTECT(one if one.startswith(("/", "~", "$")) else cwd + one, removes)
        if found is not None:
            return found
    return None


def _secret_read(words: list[str], reads: list[str], cwd: str) -> str | None:
    """Whether a command names one of Scout's secrets (best effort: a path it spells out, or one after a ``cd``)."""
    if _SECRET is None or (words and _basename(words[0]) in _LISTS_ONLY):
        return None
    for word in [*words[1:], *reads]:
        word = word.split("=", 1)[1] if word.startswith("--") and "=" in word else word.lstrip("@<")  # @file
        if not (cwd or "/" in word or word.startswith(("~", "$"))) or word.startswith("-"):
            continue
        for one in _substitute(word):
            _STATE["secrets"] += 1
            if _STATE["secrets"] > MAX_CHECKED_PATHS:
                return None
            found = _SECRET(one if one.startswith(("/", "~", "$")) else cwd + one)
            if found is not None:
                return found
    return None


def _destinations(name: str, args: list[str]) -> list[str]:
    """What a copy or move writes: its target, and each source's name inside it (a directory target, or -t DIR)."""
    plain = [a for a in args if not a.startswith("-")]
    target = next((a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")), None)
    for flag, value in zip(args, args[1:], strict=False):
        if flag in ("-t", "--target-directory"):
            target = value
    if target is not None:
        inside = target.rstrip("/")
        return [target, *(f"{inside}/{os.path.basename(src.rstrip('/'))}" for src in plain if src != target)]
    if not plain:
        return []
    last, sources = plain[-1], plain[:-1]
    return [last, *(f"{last.rstrip('/')}/{os.path.basename(src.rstrip('/'))}" for src in sources)]


def _shell_stdin_program(words: list[str]) -> bool:
    """``sh``, ``bash -s``, ``zsh`` with no ``-c`` and no script: it runs its stdin as commands."""
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS or words[0] in _PREFIX):
        words = words[1:]
    if not words or _basename(words[0]) not in _SHELLS:
        return False
    args = words[1:]
    if "-c" in args:
        return False
    return "-s" in args or not any(not a.startswith("-") for a in args)


def _interpreter_programs(words: list[str], segment: str, herestrings: list[str], fed: str | None) -> list[str] | None:
    """The programs an interpreter call runs from the command itself, else None (not an interpreter, or a file)."""
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS or words[0] in _PREFIX):
        words = words[1:]
    if not words or not _INTERPRETER.match(_basename(words[0])):
        return None
    programs, stdin = _program_flags(_basename(words[0]), words[1:])
    if stdin:
        programs += _stdin_programs(segment, herestrings, fed)
    return programs


# A sed script: `s/a/b/`, `s|a|b|g`, `y/ab/cd/`, `/pat/d`, `1d`, `$a\ text`, `/^-$/,/^-$/!s/a/b/`: an address or
# a range of two, maybe `!`, then a command.
_SED_ADDRESS = r"(?:[0-9$]+|/(?:[^/\\]|\\.)*/)"
_SED_SCRIPT = re.compile(
    rf"^\s*(?:{_SED_ADDRESS}(?:\s*,\s*{_SED_ADDRESS})?)?\s*!?\s*"
    r"(?:[{]|s(.).*\1.*\1|y(.).*\2.*\2|[dpqx=gGhHnNDP]\s*(?:[;}]|$)|[aic]\\?)",
    re.S,
)


def _edited_files(args: list[str], script_flags: str) -> list[str]:
    """The files ``sed -i`` / ``perl -i`` edit in place: their operands, not the script (``-e`` values, or the first
    operand when no -e is given), nor ``-i``'s backup suffix (BSD sed's ``-i ''``)."""
    files: list[str] = []
    scripted = False
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
        elif a.startswith("--expression=") or a.startswith("--file="):
            scripted = True
        elif a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            if a[-1] in script_flags:
                scripted, skip = True, True  # -e SCRIPT / -f SCRIPTFILE: the next word isn't edited
            elif a in ("-i", "-I") and i + 1 < len(args) and (args[i + 1] == "" or args[i + 1].startswith(".")):
                skip = True  # a backup suffix
        elif a.startswith("-"):
            continue
        elif not scripted and not files and _SED_SCRIPT.match(a):
            scripted = True  # the script, given as the first operand
        else:
            files.append(a)
    return files


def _hook_write_form(words: list[str], targets: list[str], cwd: str) -> str | None:
    """Whether a command writes what decides which checks run: a redirect, or a command that writes files."""
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS or words[0] in _PREFIX):
        words = words[1:]
    written = list(targets)
    removed: list[str] = []
    linked: list[str] = []
    if words:
        name, args = _basename(words[0]), words[1:]
        plain = [a for a in args if not a.startswith("-")]
        if name in _REMOVERS:
            removed += plain[:-1] if name == "mv" else plain
        if name in ("cp", "mv", "install", "rsync"):
            written += _destinations(name, args)
        elif name == "ln":
            linked += plain  # the link and what it points at: either may be a protected file
            if len(plain) >= 2:  # a symlink's relative target is read from the link's own folder
                link_dir = os.path.dirname(plain[-1].rstrip("/")) or "."
                linked += [f"{link_dir}/{src}" for src in plain[:-1] if not src.startswith(("/", "~"))]
        if name == "sed" and any(_SED_IN_PLACE.match(a) for a in args):
            written += _edited_files(args, "ef")
        elif name == "perl" and any(a.startswith("-") and "i" in a for a in args):
            written += _edited_files(args, "eE")
        elif name in _WRITERS:
            written += args
        elif name in _COPIERS:
            written += args[-1:]
        elif name == "dd":
            written += [a[3:] for a in args if a.startswith("of=")]
        elif name in ("curl", "wget"):
            for flag, value in zip(args, args[1:], strict=False):
                if flag in ("-o", "--output", "-O", "--output-document"):
                    written.append(value)
    if _PROTECT is not None:
        gone = set(removed)  # a removed path is checked once, the stricter way
        written_only = list(dict.fromkeys(p for p in written if p not in gone))
        for paths, removes in ((written_only, False), (linked, False), (list(dict.fromkeys(removed)), True)):
            for path in paths:
                found = _check(path, removes, cwd)
                if found is not None:
                    return found
    for path in written:
        m = _HOOK_FILE.search(path if path.startswith("/") else cwd + path)
        if m is not None:
            return ".git/config" if m[1] == "config" else ".git/hooks"
        if _AGENT_SETTINGS_FILE.search(path):
            return "agent settings"  # scout.kb.hook_paths.AGENT_SETTINGS
        if _USER_GIT_CONFIG_FILE.search(path):
            return "git config"  # scout.kb.hook_paths.USER_GIT_CONFIG
    return None


def _words_escape(words: list[str], depth: int) -> str | None:
    form = None
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS):
        if _sets_run_var(words[0]):
            return "env"
        if _GIT_CONFIG_VAR.match(words[0]):
            form = GIT_CONFIG_FORM
        words = words[1:]
    found = _command_escape(words, depth) if words else None
    return found if found == "env" else form or found


def _command_escape(words: list[str], depth: int) -> str | None:
    command = words
    while command and command[0] in _PREFIX:
        command = command[1:]
    found = _plain_escape(command, depth) if command else None
    if found is not None:
        return found
    git = _git_words(words)
    return _git_escape(git) if git is not None else None


def _plain_escape(words: list[str], depth: int) -> str | None:
    name, args = _basename(words[0]), words[1:]
    if name == "env":
        verdict, rest = _env_escape(args)
        return verdict or (_words_escape(rest, depth) if rest else None)
    if name == "unset":
        return "env" if any(a in RUN_VARS for a in args) else None
    if name in ("export", "declare", "typeset", "local", "readonly"):
        unexport = any(a in ("-n", "+x") or (a.startswith("+") and "x" in a) for a in args)
        if any(_sets_run_var(a) or (unexport and a in RUN_VARS) for a in args):
            return "env"
        return GIT_CONFIG_FORM if any(_GIT_CONFIG_VAR.match(a) for a in args) else None
    if name in _DETACH:
        return "detach"
    if _delegate_write(name, args):
        return DELEGATE_FORM
    if name in _SHELLS and "-c" in args[:-1]:
        return _run_escape(args[args.index("-c") + 1], depth + 1)
    if name == "eval":
        return _run_escape(" ".join(args), depth + 1)
    return None


def run_escape(
    command: str,
    *,
    depth: int = 0,
    protect: Callable[[str, bool], str | None] | None = None,
    secret: Callable[[str], str | None] | None = None,
) -> str | None:
    """Whether a run's shell command gets around the commit hook's checks: "env" (changes SCOUT_MODE/SCOUT_READER
    or clears the environment), "detach" (backgrounds or hands work to another process), else the name of the form
    that skips or redirects the hooks (``--no-verify``, ``-c core.hooksPath``, ``commit-tree``, ``.git/hooks``, #607),
    else None. "env" wins, then a named form, then "detach".

    Only the forms that write count: reading ``$SCOUT_MODE``, ``printenv``, a mention in a commit message or a
    search, ``git log -n 5``, ``git config --get``, ``cat .git/hooks/pre-commit``, ``2>&1``, and anything in a quoted
    argument or a here-document's body (a Telegram wrap's ``&amp;``) all pass. Only the top-level command is read,
    so what ``scripts/git-safe-commit.sh`` or ``scoutctl`` run inside themselves is never in question; a command
    substitution's command (``$( … )``, backticks, ``<( … )``) is read as a command of its own, quoted or not.

    ``protect`` (a run's gate) judges each path the command writes, removes or links; with it, a written path the
    gate can't work out (a substitution, an unknown variable) is judged unresolved, and an inline program
    (``python3 -c``, a here-document fed to ``python3 -``) that writes files is judged by the paths and protected
    names it spells. ``secret`` judges each path it names for a read of Scout's secrets.
    """
    global _PROTECT, _SECRET
    outer = depth == 0
    if outer:
        _PROTECT, _SECRET = protect, secret
        _STATE.update(checked=0, secrets=0, vars={}, bodies=[])
    try:
        return _run_escape(command, depth)
    finally:
        if outer:
            _PROTECT = _SECRET = None


def _known_output(body: str) -> str | None:
    """What a substitution prints, where the gate can know it: ``$(mktemp …)`` is a new file in the template's
    folder (``-p DIR``, else the temp folder). Anything else is unknown (None)."""
    if _dynamic(body):
        return None
    words = _tokens(body)
    if not words or _basename(words[0]) != "mktemp":
        return None
    folder, template, i = "/tmp", None, 1
    while i < len(words):
        a = words[i]
        if a in ("-p", "--tmpdir") and i + 1 < len(words):
            folder, i = words[i + 1], i + 2
            continue
        if a.startswith("--tmpdir="):
            folder = a.split("=", 1)[1]
        elif a == "-t" and i + 1 < len(words):
            i += 1  # BSD: a prefix in the temp folder
        elif not a.startswith("-") and template is None:
            template = a
        i += 1
    if template is not None and "/" in template:
        folder = os.path.dirname(template)
    return f"{folder.rstrip('/') or '/'}/scout-mktemp-XXXXXX" if folder and " " not in folder else None


def _merge(results: list[str | None], detach: bool = False) -> str | None:
    if "env" in results:
        return "env"
    form = next((r for r in results if r is not None and r != "detach"), None)
    return form or ("detach" if detach or "detach" in results else None)


def _run_escape(command: str, depth: int, *, split: bool = True) -> str | None:
    if depth > MAX_DEPTH:
        from scout.kb.hook_paths import TOO_MANY

        return TOO_MANY if _PROTECT is not None else None
    bodies: list[tuple[str, bool]] = []
    if split:
        command, bodies = _split_heredocs(command, start=len(_STATE["bodies"]))
        _STATE["bodies"].extend(bodies)
    command = command.replace("\\\n", " ")
    # Every verdict with where it sits in the command, so the first form in the text names the deny
    # (`c=$(git commit-tree …) && git update-ref …` is commit-tree; `git update-ref HEAD "$(git commit-tree …)"`
    # is update-ref).
    results: list[tuple[int, str | None]] = []
    # Each substitution's command on its own, and a placeholder in its place (see SUBST): `> $(printf .claude)/x`.
    spans = _substitutions(command)
    inner = [_run_escape(command[a:b], depth + 1, split=False) for _s, _e, a, b in spans]
    rebuilt, at, shift = [], 0, 0
    for (start, end, a, b), found in zip(spans, inner, strict=True):
        # A process substitution becomes the pipe the shell hands over (its own command is judged above).
        known = _known_output(command[a:b]) if command[start] not in "<>" else "/dev/fd/63"
        rebuilt += [command[at:start], known or SUBST]
        results.append((start - shift, found))
        shift += (end - start) - len(known or SUBST)
        at = end
    command = "".join([*rebuilt, command[at:]])
    for text, expands in bodies:  # an unquoted here-document runs its substitutions; quotes there are text
        if expands:
            for _s, _e, a, b in _substitutions(text, quotes=False):
                results.append((len(command), _run_escape(text[a:b], depth + 1)))
    detach = _BACKGROUND.search(_QUOTED.sub("''", command)) is not None
    cwd = ""  # follows each `cd`, so `cd .git/hooks && rm pre-commit` reads as the path it writes
    previous: str | None = None
    position = 0
    for segment, op in _segments_with_ops(command):
        position += len(op)
        here, position = position, position + len(segment)
        fed = previous if op in ("|", "|&") else None  # the segment piped into this one
        clean, targets, reads, strings = _redirects(segment)
        words = _tokens(clean)
        _record_vars(words)
        found = _words_escape(words, depth) or _hook_write_form(words, targets, cwd)
        if found is None and _PROTECT is not None:
            for program in _interpreter_programs(words, segment, strings, fed) or []:
                found = _program_escape(program, cwd, depth)
                if found is not None:
                    break
        if found is None and _shell_stdin_program(words):
            found = _shell_stdin_escape(segment, strings, fed, depth)
        if found is None:
            found = _secret_read(words, reads, cwd)
        if found == "env":
            return "env"
        results.append((here, found))
        previous = segment
        if words[:1] == ["cd"]:
            dest = _substitute(words[1])[0] if len(words) > 1 else "~"
            if dest == "-":
                cwd = ""
            elif dest.startswith(("/", "~")):
                cwd = dest.rstrip("/") + "/"
            else:
                cwd = cwd + dest.rstrip("/") + "/"
    return _merge([found for _, found in sorted(results, key=lambda r: r[0])], detach)


def _shell_stdin_escape(segment: str, strings: list[str], fed: str | None, depth: int) -> str | None:
    """A shell that runs its stdin (``sh <<EOF``, ``… | bash``): judge those commands. From a pipe, only what
    ``echo``/``printf``/``cat <<EOF`` feed it can be read; in a run anything else is unresolved."""
    programs = [*_segment_bodies(segment), *strings]
    if not programs and fed is not None:
        feeder = _tokens(_redirects(fed)[0])
        if feeder[:1] in (["echo"], ["printf"]):
            programs.append(" ".join(w for w in feeder[1:] if not w.startswith("-")))
        programs += _segment_bodies(fed)
        if not programs and _PROTECT is not None:
            from scout.kb.hook_paths import UNRESOLVED

            return UNRESOLVED
    return _merge([_run_escape(program, depth + 1) for program in programs])

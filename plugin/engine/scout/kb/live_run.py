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
_KEYWORDS = {"{", "!", "if", "then", "elif", "else", "do", "while", "until"}
# An unquoted redirect and its target. Found in the masked text, so a quoted ">" is never one.
_REDIRECT = re.compile(r"(?:(?<=\s)\d+|&)?(?:>>|>&|>|<<<|<<-?|<&|<>|<)\s*([^\s<>]*)")

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
_GIT_DIR = re.compile(r"(?:^|/)(\.git(?:/hooks)?)/?$")
_WRITERS = {"tee", "rm", "unlink", "mv", "chmod", "chown", "chflags", "touch", "truncate", "rmdir", "shred"}
_COPIERS = {"cp", "install", "ln", "rsync"}  # the last argument is the one written
_SED_IN_PLACE = re.compile(r"^(?:--in-place|-[A-Za-z]*i)")


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


def _split_redirects(segment: str) -> tuple[str, list[str]]:
    """``segment`` without its unquoted redirects, and the files they name."""
    masked = _QUOTED.sub(lambda m: "\0" * len(m[0]), segment)
    kept, targets, at = [], [], 0
    for m in _REDIRECT.finditer(masked):
        kept.append(segment[at : m.start()])
        targets.extend(_tokens(segment[m.start(1) : m.end(1)]))
        at = m.end()
    kept.append(segment[at:])
    return "".join(kept), targets


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


def _config_form(key: str) -> str | None:
    """The name of a config key that changes which hooks or commands run (keys don't depend on case)."""
    key = key.lower()
    if key == "core.hookspath":
        return "core.hooksPath"
    if key.startswith("alias."):
        return "alias.*"
    if key == "include.path" or (key.startswith("includeif.") and key.endswith(".path")):
        return "include.path"
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


def _git_config_form(args: list[str]) -> str | None:
    if any(a in _CONFIG_READS for a in args):
        return None
    for a in args:
        form = _config_form(a)
        if form is not None:
            return f"git config {form}"
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


def _hook_write_form(words: list[str], targets: list[str], cwd: str) -> str | None:
    """Whether a command writes ``.git/hooks`` or ``.git/config``: by a redirect, or a command that writes files."""
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS or words[0] in _PREFIX):
        words = words[1:]
    written = list(targets)
    if words:
        name, args = _basename(words[0]), words[1:]
        if name in _WRITERS or (name == "sed" and any(_SED_IN_PLACE.match(a) for a in args)):
            written += args
        elif name in _COPIERS:
            written += args[-1:]
        elif name == "dd":
            written += [a[3:] for a in args if a.startswith("of=")]
    for path in written:
        m = _HOOK_FILE.search(path if path.startswith("/") else cwd + path)
        if m is not None:
            return ".git/config" if m[1] == "config" else ".git/hooks"
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
    if depth < 3 and name in _SHELLS and "-c" in args[:-1]:
        return run_escape(args[args.index("-c") + 1], depth=depth + 1)
    if depth < 3 and name == "eval":
        return run_escape(" ".join(args), depth=depth + 1)
    return None


def run_escape(command: str, *, depth: int = 0) -> str | None:
    """Whether a run's shell command gets around the commit hook's checks: "env" (changes SCOUT_MODE/SCOUT_READER
    or clears the environment), "detach" (backgrounds or hands work to another process), else the name of the form
    that skips or redirects the hooks (``--no-verify``, ``-c core.hooksPath``, ``commit-tree``, ``.git/hooks``, #607),
    else None. "env" wins, then a named form, then "detach".

    Only the forms that write count: reading ``$SCOUT_MODE``, ``printenv``, a mention in a commit message or a
    search, ``git log -n 5``, ``git config --get``, ``cat .git/hooks/pre-commit``, ``2>&1``, and anything in a quoted
    argument or a here-document's body (a Telegram wrap's ``&amp;``) all pass. Only the top-level command is read,
    so what ``scripts/git-safe-commit.sh`` or ``scoutctl`` run inside themselves is never in question.
    """
    command = _drop_heredoc_bodies(command).replace("\\\n", " ")
    detach = _BACKGROUND.search(_QUOTED.sub("''", command)) is not None
    form: str | None = None
    cwd = ""  # set by a `cd` into the git dir, so `cd .git/hooks && rm pre-commit` reads as the path it writes
    for segment in _segments(command):
        clean, targets = _split_redirects(segment)
        words = _tokens(clean)
        found = _words_escape(words, depth) or _hook_write_form(words, targets, cwd)
        if found == "env":
            return "env"
        if found == "detach":
            detach = True
        else:
            form = form or found
        if words[:1] == ["cd"]:
            m = _GIT_DIR.search(words[-1])
            cwd = f"{m[1]}/" if m is not None else ""
    return form or ("detach" if detach else None)

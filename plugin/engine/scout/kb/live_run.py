"""Whose commit is it: a scheduled run's or the owner's, from the process table, not the environment (#595).

The commit hook used to decide "scheduled run" from the commit's environment
alone (``SCOUT_MODE``, ``SCOUT_READER``: :func:`scout.kb.reader.is_scheduled`),
so a run could commit with ``env -u SCOUT_MODE`` or ``SCOUT_MODE=`` and get the
owner's rules. A run can change its environment and delete files, but not the
processes it runs in, so this module reads one ``ps`` snapshot:

- **A live run** (:func:`run_processes`): the session lock's holder when its
  argv is a ``run-(scout|dreaming|research|respond).sh``; a running
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
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from scout.kb.git_read import GitRead

LOCK_NAME = ".scout-session.lock"
RUN_MARKER = LOCK_NAME + ".run"
UNKNOWN_MODE = "scheduled"
_MODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")
_PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+((?:\d+-)?(?:\d+:)?\d+:\d+)\s?(.*)$")
_SHELL_RE = re.compile(r"(?:^|/)-?(?:ba|z|da|k)?sh$")
_RUNNER_RE = re.compile(r"(?:^|[\s/])run-(?:scout|dreaming|research|respond)\.sh(?:\s|$)")
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


def _backgrounds(text: str) -> bool:
    """Whether ``text`` (quotes masked) holds a lone "&": a background.

    Not the second half of "&&", ">&" (`2>&1`), "<&" (`cat <&3`, #618) or "|&".
    A backslash before that first character makes it a letter of a word, so
    the "&" after `x\\<` or `x\\>` does background.
    """
    for match in _AMPERSAND.finditer(text):
        if _backslashes(text, match.start()) % 2:
            continue  # an escaped "&" is a letter
        before = match.start() - 1
        if before < 0 or text[before] not in "&><|":
            return True
        if _backslashes(text, before) % 2:
            return True
    return False


def _backslashes(text: str, at: int) -> int:
    """How many backslashes come directly before ``text[at]``."""
    return at - len(text[:at].rstrip("\\"))


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
# What `~` and `$HOME` name, which the gate expands with the run's own HOME: a command that points HOME elsewhere
# (`HOME=… cmd`, `export HOME=…`, `env HOME=…`, `unset HOME`, `for HOME in …`, `read HOME`) writes where the gate
# didn't look. Runs don't need it: one `HOME=$HOME` in the last 280 scheduled sessions, which keeps HOME as it is and
# stays allowed.
HOME_FORM = "HOME"
# HOME set to itself: what `~` and `$HOME` name doesn't change.
_SAME_HOME = ("$HOME", "${HOME}", "~", "$HOME/", "${HOME}/", "~/")
# An assignment as bash and zsh read it: a name, an optional subscript (`HOME[1,-1]=`), then `=` or `+=`.
_ASSIGN_ANY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?(\+?=)")
# A name, as a builtin's target or an assignment's left side, that the parser can read for certain: anything else
# (`$v`, `H$v`, `${v}`, a backtick, a glob such as `HOM?` or `HOM[E]`, which the shell may expand to a file's name)
# may be HOME once the shell has expanded it.
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A parameter expansion that assigns a name that matters, or one built at run time: `${HOME:=…}`, `${HOME=…}` (with a
# value: an empty one changes nothing), zsh's `${HOME::=…}` (always assigns), `${(P)v::=…}` (zsh's (P) flag: the
# name is v's value), and bash's `${!v:=…}`.
_PROTECTED_NAMES = "HOME|XDG_CONFIG_HOME|SCOUT_MODE|SCOUT_READER"
_PARAM_ASSIGN = re.compile(rf"\$\{{(?:({_PROTECTED_NAMES})(?:::=|:?=(?!\}}))|\([^)]*P[^)]*\).*?=|!.*?=)")
# A named file descriptor (`exec {HOME}>/dev/null`): the shell sets the name to the new descriptor's number.
_NAMED_FD = re.compile(rf"\{{({_PROTECTED_NAMES})\}}\s*(?:[<>]|&>)")
# An even run of `$` (not escaped) before a quote that holds a backslash: shells read it two ways (expansion_escape).
_EVEN_DOLLAR_QUOTE = re.compile(r"(?<![$\\])(?:\$\$)+'[^']*\\")
# Builtins that set or unset the variables they name, and the options whose value is the next word (not a name).
_DECLARERS = ("export", "declare", "typeset", "local", "readonly", "integer", "float")
_NAMER_VALUE_OPTIONS = {
    "read": "dinNptu",  # bash and zsh; `-a`/`-A` (an array) take the target itself, so they are flags here
    "mapfile": "dnOsuCc",
    "readarray": "dnOsuCc",
    "vared": "Mmprif",
}
# zsh builtins that set a variable they're given (zsh/zutil, zsh/datetime): `print -v`, `zstyle -s/-a/-b/-g`,
# `zformat -f/-a`, `strftime -s`, `getln`.
_ZSH_SETTERS = {"print", "zstyle", "zformat", "strftime", "getln"}
# Module builtins that set what they're given in ways not worth modelling (`zparseopts -A name`, `h=name` specs,
# `zstat -A`, `sysread`, `sysopen -u`, `zselect -a`, `zpty -r`): any argument that spells a name that matters, or
# could build one, is denied.
_STRICT_SETTERS = {"zparseopts", "zstat", "sysread", "sysopen", "zselect", "zpty", "zregexparse"}
_NAMERS = {
    *_DECLARERS, "unset", "for", "select", "getopts", "printf", "let", *_NAMER_VALUE_OPTIONS, *_ZSH_SETTERS,
    *_STRICT_SETTERS,
}  # fmt: skip
# Declarer options whose value is a number: zsh's `typeset -i 10 x`, `-Z 3`, `-L 5`, `-R 5`, `-E 2`, `-F 2`.
_NUMBER_OPTIONS = "iZLREF"
_PROTECTED_WORD = re.compile(rf"(?<![A-Za-z0-9_])(?:{_PROTECTED_NAMES})(?![A-Za-z0-9_])")
_BUILDS_A_NAME = re.compile(r"[$`?*\[{]")
# An arithmetic assignment (`let`, `(( … ))`): a left side, then `=`, `+=`, `<<=` and the like (not `==`).
_ARITH_ASSIGN = re.compile(r"([^\s=+\-*/%&|^<>!(),]+)\s*(?:\*\*|<<|>>|[-+*/%&|^])?=(?!=)")
# A command word whose left side is a name built at run time from name characters, `$…`, `${…}` or a backtick
# (`$v=x`, `H${v}=x`): `eval` makes it an assignment. A glob there (`HOM?=x`) never assigns.
_BUILT_HEAD = re.compile(r"[A-Za-z0-9_]*[$`][A-Za-z0-9_{}$`]*\+?=")
_ARITH_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^=", "**="}
# ">|" is a redirect, not a pipe; an escaped `\;`, `\(`, `\)` or `\|` (`find … -exec x {} \;`) is a word.
_SEGMENTS = re.compile(r"&&|\|\||(?<!\\)[;\n`]|(?<![>\\])\||\$\(|(?<!\\)\(|(?<!\\)\)")
# An "&" that isn't the first half of "&&" or "&>". Whether it backgrounds is _backgrounds' call.
_AMPERSAND = re.compile(r"&(?![&>])")
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][\w.-]*))")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_DETACH = {
    "nohup", "setsid", "disown", "launchctl", "osascript", "open", "tmux", "screen", "script", "at", "batch",
    "daemonize", "start-stop-daemon", "systemd-run", "ssh", "mosh",
}  # fmt: skip
# `noglob`, `nocorrect` and `-` are zsh's precommand modifiers: the command after them runs as written.
_PREFIX = {"sudo", "command", "exec", "nice", "time", "xargs", "builtin", "noglob", "nocorrect", "-"}
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
# And the agents' inboxes (#550 V4b): pulled, applied and reverted by the owner, or by the tick's own step.
_INBOX_WRITES = {"pull", "apply", "revert", "sync", "review"}  # review: it prints agents' text (V4b2)


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
    """``scoutctl delegate link|unlink|key|trust-state`` or ``scoutctl inbox pull|apply|revert|sync|review``, by any
    path to scoutctl or as ``python -m scout``."""
    if name == "scoutctl":
        rest = args
    elif name.startswith("python") and (module := _python_module(args)) is not None and module[0] == "scout":
        rest = module[1]
    else:
        return False
    words = [a for a in rest if not a.startswith("-")]
    if len(words) < 2:
        return False
    return (words[0] == "delegate" and words[1] in _DELEGATE_WRITES) or (
        words[0] == "inbox" and words[1] in _INBOX_WRITES
    )


_GIT_CONFIG_VAR = re.compile(r"^GIT_CONFIG(?!_NOSYSTEM=)[A-Z0-9_]*=")
# git reads `$XDG_CONFIG_HOME/git/config` as its global config, which can set core.hooksPath.
XDG_CONFIG_FORM = "XDG_CONFIG_HOME"
_XDG_CONFIG_VAR = re.compile(r"^XDG_CONFIG_HOME=")
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
# A substitution that prints one name with no `/` in it (`$(date +%F)`): unresolved for a write as before, but a
# read judges it as `*`, which matches every name it can print.
ANY_NAME = "$__scout_name__"
# What xargs reads from a command the gate can't follow: unresolved, as SUBST is, but told apart in the audit.
UNSEEN_INPUT = "$__scout_input__"
# A `date` format whose output has no `/` and no space: digits, the literal characters given, `%z`'s sign.
_DATE_FORMAT = re.compile(r"\+(?:%[YmdHMSjuwUVGgyCsNIFTRz%]|[A-Za-z0-9_.:,+-])*")
_DATE_VALUE_FLAGS = {"-d", "-v", "-r", "-f", "--date", "--reference"}


# A time zone set for the one call (`TZ="$(scripts/scout-tz.sh)" date +%F`): it moves the clock, not the format.
# A substitution in it is judged as a command of its own.
_TZ_PREFIX = re.compile(r"""^\s*TZ=(?:"\$\([^()"'`$;&|<>\n]*\)"|"[^"$`]*"|'[^']*'|[^\s"'$`;&|<>()]*)\s+""")


def _one_name_output(body: str) -> str | None:
    """:data:`ANY_NAME` for a substitution that is one ``date`` call with a ``+FORMAT`` (or ``-I``) printing no
    ``/`` or whitespace, else None."""
    body = _TZ_PREFIX.sub("", body, count=1)
    if re.search(r"[;&|<>`\n]|\$\(", body):
        return None
    words = _tokens(body)
    if not words or _basename(words[0]) not in ("date", "gdate"):
        return None
    formats: list[str] = []
    iso = False
    skip = False
    for word in words[1:]:
        if skip:
            skip = False
        elif word in _DATE_VALUE_FLAGS:
            skip = True
        elif word.startswith("+"):
            formats.append(word)
        elif word in ("-I", "--iso-8601", "--iso-8601=date"):
            iso = True
        elif word.startswith("-I") or word.startswith("--iso-8601="):
            return None  # a time part: `-Iseconds` prints a `,` or `+`, harmless, but keep the rule narrow
    if len(formats) == 1 and _DATE_FORMAT.fullmatch(formats[0]):
        return ANY_NAME
    return ANY_NAME if iso and not formats else None


_HEREDOC_AT = re.compile(r"<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][\w.-]*))")


def _quoted_spans(text: str, *, even_dollars_escape: bool = False) -> list[tuple[int, int]]:
    """Where the quoted strings of ``text`` sit, as the shell reads them (each span includes its quotes).

    A backslash outside quotes escapes the next character, so ``\\'`` and ``\\"`` open nothing. Single
    quotes take no escapes; double quotes and ``$'…'`` do. A quote in a comment opens nothing (the
    comment's text is left as it is). A quote that is never closed is no span.

    Shells disagree on ``$$'``: bash reads the process id and then a plain single quote, zsh and sh read
    a ``$`` and then ``$'…'``. ``even_dollars_escape`` picks the second reading for an even run of ``$``.
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    dollars = 0  # how many unescaped "$" come directly before text[i]
    while i < n:
        c = text[i]
        if c == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            end = text.find("\n", i)
            i, dollars = (n if end < 0 else end), 0
            continue
        if c not in "'\"":
            dollars = dollars + 1 if c == "$" else 0
            i += 2 if c == "\\" else 1
            continue
        escapes = c == '"' or dollars % 2 == 1 or (dollars > 0 and even_dollars_escape)
        dollars = 0
        j = i + 1
        while j < n and text[j] != c:
            j += 2 if escapes and text[j] == "\\" else 1
        if j < n:
            spans.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return spans


def _mask_quoted(text: str, *, even_dollars_escape: bool = False) -> str:
    """``text`` with every quoted string blanked to NULs, so no operator inside one is read as one."""
    out, at = [], 0
    for a, b in _quoted_spans(text, even_dollars_escape=even_dollars_escape):
        out += [text[at:a], "\0" * (b - a)]
        at = b
    out.append(text[at:])
    return "".join(out)


_Pending = list[tuple[int, str, bool]]  # here-document operators waiting for their line to end
# A here-document: its operator's start and end, whether its body expands, and the body's start, its end and
# the index after its delimiter line (all -1 when it has no body).
_HereDoc = tuple[int, int, bool, int, int, int]


def _split_heredocs(command: str, start: int = 0) -> tuple[str, list[tuple[str, bool]]]:
    """``command`` with each here-document's body cut out and its operator marked, and the bodies in order as
    ``(text, expands)``: ``expands`` when the delimiter is unquoted (the shell runs its substitutions).

    The command is read once from the start, the way the shell reads it (see :func:`_heredocs`). Shells
    disagree on ``$$'`` (see :func:`_quoted_spans`), so it is read both ways and a body is cut only where
    both readings find the same one: otherwise its lines stay in the text, still scanned as commands.
    """
    docs = _heredocs(command, even_dollars_escape=False)
    if "$" in command and "'" in command:
        agreed = set(_heredocs(command, even_dollars_escape=True))
        docs = [doc if doc in agreed else (doc[0], doc[1], doc[2], -1, -1, -1) for doc in docs]
    edits: list[tuple[int, int, str]] = []
    bodies: list[tuple[str, bool]] = []
    for number, (at, end, expands, body, body_end, after) in enumerate(docs):
        edits.append((at, end, f"<<__scout_hd_{start + number}__"))
        bodies.append((command[body:body_end] if body >= 0 else "", expands))
        if body >= 0:
            edits.append((body, after, ""))
    text, kept = "", 0
    for at, end, put in sorted(edits):
        text += command[kept:at] + put
        if not put and command[end - 1] != "\n" and text.endswith("\n"):
            text = text[:-1]  # the last line was the delimiter: its operator's line ends the text
        kept = end
    return text + command[kept:], bodies


def _heredocs(command: str, *, even_dollars_escape: bool) -> list[_HereDoc]:
    """The here-documents of ``command``, in the order of their operators.

    A ``<<`` in a comment or inside a quoted string (one that runs over several lines too) opens nothing. A
    body starts after the end of its operator's line, which a string spanning lines pushes down. A
    here-document inside ``$(…)`` or backticks is one all the same, but its body has to start inside them:
    when they close on the operator's own line the lines below are commands. Read a line at a time,
    `true # <<EOF` hid the commands below it as a body.
    """
    ops: list[tuple[int, int, bool]] = []
    cuts: dict[int, tuple[int, int, int]] = {}

    def take_bodies(i: int, end: int, pending: _Pending) -> int:
        """At the line end ``command[i]``: find the pending bodies below it; where to read on."""
        j = i + 1
        for number, word, strip_tabs in pending:
            body = _heredoc_body(command, j, end, word, strip_tabs)
            if body is not None:  # else unterminated: the rest stays in the text, still scanned as commands
                cuts[number] = (j, *body)
                j = body[1]
        pending.clear()
        return j

    def scan(i: int, end: int, kind: str, pending: _Pending) -> int:
        """Read ``command[i:end]`` as commands (``kind`` "" or ")") or as a double-quoted string (``kind`` '"');
        the index after what closes it, else ``end``. A ``$(…)`` or backtick body gets a ``pending`` of its
        own: a here-document still open when it ends has no body."""
        dollars = 0  # how many unescaped "$" come directly before command[i]
        last = ""  # the plain character directly before command[i], if there is one
        while i < end:
            c = command[i]
            before, dollars = dollars, 0
            previous, last = last, ""
            if c == "\\":
                i += 2
            elif c == "`":
                close = min(_close(command, i + 1, "`"), end)
                scan(i + 1, close, "", [])
                i = close + 1
            elif kind == '"':
                if c == '"':
                    return i + 1
                i = scan(i + 2, end, ")", []) if command.startswith("$(", i) else i + 1
            elif c == kind:
                return i + 1
            elif c == "\n" and pending:
                i = take_bodies(i, end, pending)
            elif c == "'":
                escapes = before % 2 == 1 or (before > 0 and even_dollars_escape)  # $'…' takes escapes
                j = i + 1
                while j < end and command[j] != "'":
                    j += 2 if escapes and command[j] == "\\" else 1
                i = j + 1 if j < end else i + 1  # never closed: no string
            elif c == '"':
                close = _close(command, i + 1, '"')
                if close < end:
                    scan(i + 1, close, '"', pending)
                    i = close + 1
                else:
                    i += 1  # never closed: no string
            elif c == "(":
                # A subshell's here-documents take their bodies below its line, like any command's.
                i = scan(i + 1, end, ")", [] if previous and previous in "$<>" else pending)
            elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
                j = command.find("\n", i, end)
                i = end if j < 0 else j
            elif c == "<" and (m := _HEREDOC.match(command, i)) and m.end() <= end:
                pending.append((len(ops), m[2] or m[3] or m[4], bool(m[1])))
                ops.append((i, m.end(), m[4] is not None and "\\" + m[4] not in m[0]))
                i = m.end()
            else:
                dollars = before + 1 if c == "$" else 0
                last = c
                i += 1
        return end

    scan(0, len(command), "", [])
    return [(*op, *cuts.get(number, (-1, -1, -1))) for number, op in enumerate(ops)]


def _heredoc_body(s: str, i: int, end: int, word: str, strip_tabs: bool) -> tuple[int, int] | None:
    """Where the here-document body that starts at ``i`` ends, and the index after its delimiter line; None
    when no line before ``end`` is the delimiter."""
    at = i
    while at < end:
        nl = s.find("\n", at, end)
        stop = end if nl < 0 else nl
        line = s[at:stop]
        if (line.lstrip("\t") if strip_tabs else line) == word:
            return max(i, at - 1), stop if nl < 0 else nl + 1
        at = stop + 1
    return None


def _close(s: str, i: int, kind: str) -> int:
    """The index of what closes a ``kind`` (``)``, a backtick or ``"``) opened just before ``i``, else ``len(s)``.

    Quotes, nested substitutions, comments and here-documents inside it are skipped the way the shell does.
    A backtick substitution is the exception: nothing inside it hides the backtick that ends it.
    """
    n = len(s)
    if kind == "`":  # the shell ends it at the next backtick with no backslash before it, inside quotes or not
        while i < n and s[i] != "`":
            i += 2 if s[i] == "\\" else 1
        return min(i, n)
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
    masked = _mask_quoted(command)
    out: list[tuple[str, str]] = []
    at, op = 0, ""
    for m in _SEGMENTS.finditer(masked):
        out.append((command[at : m.start()], op))
        at, op = m.end(), m[0]
    out.append((command[at:], op))
    return out


def _redirects(segment: str) -> tuple[str, list[str], list[str], list[str]]:
    """``segment`` without its unquoted redirects; the files they write, the files they read, and here-strings."""
    masked = _mask_quoted(segment)
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
    except ValueError:  # an unclosed quote: the words are a guess, so a run's read of them can't be judged
        if _STATE["parsing"]:  # the walk's own segment, not a helper's look at a substitution or a feeder
            _STATE["unparsed"] = True
        return segment.split()


def _reads_something(words: list[str], reads: list[str]) -> bool:
    from scout.kb import raw_read

    stripped = raw_read._strip(words).words
    return bool(reads) or bool(stripped and raw_read._command_name(stripped[0]) in raw_read._READ_COMMANDS)


# Characters a quote keeps literal (no glob, brace or tilde expansion, and no `$` inside single quotes), marked in
# private-use code points before shlex drops the quotes, for the KB read check only: the gate judges a marked
# character as itself (`cat "k*/a.md"` reads a file named `k*`, not a glob).
_SHIELD = {"*": "\ue000", "?": "\ue001", "[": "\ue002", "{": "\ue003", "~": "\ue004"}
_SHIELD_DOLLAR = "\ue005"
UNSHIELD = {**{v: k for k, v in _SHIELD.items()}, _SHIELD_DOLLAR: "$"}


def _shield_quoted(text: str) -> str:
    spans = _quoted_spans(text)
    if not spans:
        return text
    out: list[str] = []
    at = 0
    for a, b in spans:
        inner = text[a + 1 : b - 1]
        out += [text[at : a + 1], _shield_single(inner) if text[a] == "'" else _shield_double(inner), text[b - 1]]
        at = b
    return "".join([*out, text[at:]])


def _shield_single(inner: str) -> str:
    """Inside single quotes everything is literal, `$` too."""
    return "".join(_SHIELD.get(c, _SHIELD_DOLLAR if c == "$" else c) for c in inner)


def _shield_double(inner: str) -> str:
    """Inside double quotes `$NAME`, `${…}` (a pattern in it too: `${X%*/}`), `$(…)` and backticks stay live; glob
    characters, a brace that doesn't follow `$`, and `~` are literal."""
    out: list[str] = []
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n:
            out.append(inner[i : i + 2])
            i += 2
        elif c == "$" and inner.startswith("${", i):
            end = inner.find("}", i)
            end = n if end < 0 else end + 1
            out.append(inner[i:end])
            i = end
        else:
            out.append(_SHIELD.get(c, c))
            i += 1
    return "".join(out)


def _strip_comments(command: str) -> str:
    """``command`` without its comments: a ``#`` that starts a word (the rule :func:`_quoted_spans` and
    :func:`_close` use) outside quotes, to the end of its line. ``cat x # it's`` is ``cat x``; ``a#b``, ``${#x}``,
    ``$#`` and ``\\#`` keep theirs. Run after :func:`_ansi_c`."""
    if "#" not in command:
        return command
    spans = _quoted_spans(command)
    out: list[str] = []
    at = i = 0
    while (i := command.find("#", i)) >= 0:
        if (i == 0 or command[i - 1] in " \t\n;&|(") and not any(x <= i < y for x, y in spans):
            end = command.find("\n", i)
            out.append(command[at:i])
            at = i = len(command) if end < 0 else end
        else:
            i += 1
    return "".join([*out, command[at:]])


# zsh's alternation (`(knowledge|x)-base/a.md`, core zsh even with NO_EXTENDED_GLOB) in a word's place: a group glued
# to a word, or one after a command's first word. A subshell, `$(`, `<(`, `a=(`, `((` and `f()` are not.
_RESERVED = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time", "function", "coproc", "in"}


def _zsh_groups(command: str) -> str:
    """``command`` with each zsh glob group in argument position written as a brace list (``(a|b)`` is ``{a,b}``,
    ``(a)`` is ``a``), so :func:`scout.kb.raw_read.brace_expand` gives every name it can match. A group it can't
    rewrite (nested, quoted or with a substitution inside) marks the command unparsed."""
    if "(" not in command:
        return command
    masked = _mask_quoted(command)
    out: list[str] = []
    at = 0
    for m in re.finditer(r"\(", masked):
        i = m.start()
        if i < at:
            continue
        before = masked[:i]
        if before.endswith("\\"):
            continue  # `\(`: a literal paren (`find . \( -name a -o -name b \)`)
        # Glued to a word (`kn(o|)w`, `${x}(…)`, `{,}(…)`, `(a|b)(c|d)`); only `$(`, `<(`, `>(`, `=(` and `((` aren't.
        glued = (bool(out) and i == at) or (bool(before) and before[-1] not in " \t\n;&|(<>=$`")
        if not glued:
            word = before.rstrip(" \t")
            if not word or len(word) == len(before) or word[-1] in "\n;&|({`)":
                continue  # command position: a subshell (`x; (cd y)`), or `((`
            last = re.split(r"[\s;&|(){}]", word)[-1]
            if last in _RESERVED or word.split()[-1] == "}":
                continue  # `then (cd x; ls)`, `if ((n > 1))`, `{ …; } (…)`: a subshell or arithmetic
            # anything else is an argument, a redirect's target too (`cat < (kn*|x)-base/a.md`)
        close = masked.find(")", i + 1)
        inner = command[i + 1 : close] if close > 0 else ""
        if close < 0 or "(" in masked[i + 1 : close] or "\0" in masked[i + 1 : close] or re.search(r"[$`\\]", inner):
            _STATE["unparsed"] = True
            return command
        if not inner:
            continue  # `f()`: a function, not a glob
        alts = inner.split("|")
        out += [command[at:i], ("{" + ",".join(alts) + "}") if len(alts) > 1 else inner]
        at = close + 1
    return "".join([*out, command[at:]])


def _sets_run_var(token: str) -> bool:
    m = _ASSIGN.match(token)
    return m is not None and m[1] in RUN_VARS


def _sets_home(token: str) -> bool:
    """``HOME=…`` with any value but HOME's own (``HOME=$HOME``, ``HOME=~``), ``HOME+=…`` or ``HOME[…]=…``."""
    m = _ASSIGN_ANY.match(token)
    if m is None or m[1] != "HOME":
        return False
    value = token[m.end() :]
    return bool(m[2]) or (value != "" if m[3] == "+=" else value not in _SAME_HOME)


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
            if i + 1 < len(args) and args[i + 1] == "HOME":
                return HOME_FORM, []
            i += 2
            continue
        if a.startswith("--unset=") and a.split("=", 1)[1] in RUN_VARS:
            return "env", []
        if a.startswith("-u") and a[2:] in RUN_VARS:
            return "env", []
        if (a.startswith("--unset=") and a.split("=", 1)[1] == "HOME") or a == "-uHOME":
            return HOME_FORM, []
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
            if _sets_home(a):
                return HOME_FORM, []
            if _GIT_CONFIG_VAR.match(a):
                return GIT_CONFIG_FORM, []
            if _XDG_CONFIG_VAR.match(a):
                return XDG_CONFIG_FORM, []
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
# And whether a reading command's file (scout.kb.raw_read's readers) is a KB note, by the path the shell hands it.
_KB_READ: Callable[[str, str], str | None] | None = None
# And what a git call reads (scout.kb.git_read): its tree paths and pathspecs, from the folder it runs in.
_GIT_READ: Callable[[GitRead, str], str | None] | None = None
# The forms that callback returns: a read of a KB note, and a read whose path the gate can't work out.
KB_READ = "a knowledge-base read"
UNRESOLVED_READ = "a read the gate can't resolve"
_REMOVERS = {"rm", "rmdir", "unlink", "shred", "mv"}
# Per call: how many paths were checked (above the cap the command is denied as too complex to check, so a run
# can't make the gate slow enough to time out: review of #637), the shell variables set so far
# (`d=.claude; echo > $d/x`, each with every value it can take: a `for` loop's words), and the here-documents.
MAX_CHECKED_PATHS = 256
# Paths a read spells out in full (no glob, variable or `~`): a stat each, so a longer list may be checked.
MAX_LITERAL_READS = 4096
# How deep `sh -c`, `eval` and substitutions nest before a run's command counts as too complex to check.
MAX_DEPTH = 6
_STATE: dict[str, Any] = {
    "checked": 0, "secrets": 0, "reads": 0, "literal": 0, "vars": {}, "bodies": [], "unparsed": False, "capped": False,
    "parsing": False,
}  # fmt: skip
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
    found = [m[1] or m[2] for m in _VAR.finditer(path)] + [m[1] for m in _PARAM.finditer(path)]
    names = [n for n in dict.fromkeys(found) if n in known and known[n]]
    out = [path]
    for name in names:
        out = [_put(one, name, value) for one in out for value in known[name]]
        if len(out) > MAX_CHECKED_PATHS:  # more values than are checked: the call is refused, never cut short
            _STATE["capped"] = True
            return out[:MAX_CHECKED_PATHS]
    return out


def _put(text: str, name: str, value: str) -> str:
    """``text`` with ``$name`` / ``${name}`` replaced by ``value``, and ``${name:-word}``, ``${name%pattern}`` and the
    rest of :data:`_PARAM` worked out from it."""

    def op(m: re.Match[str]) -> str:
        if m[1] != name:
            return m[0]
        found = param_value(value, m[2] or m[4], m[3] if m[2] else m[5])
        return m[0] if found is None else found

    def one(m: re.Match[str]) -> str:
        return value if (m[1] or m[2]) == name else m[0]

    return _VAR.sub(one, _PARAM.sub(op, text))


# `${NAME:-word}` and kin (m[2], m[3]); `${NAME%pattern}`, `%%`, `#`, `##` (m[4], m[5]). A nested `${` is left alone.
_PARAM = re.compile(r"\$\{([A-Za-z_]\w*)(?:(:?[-=+?])([^{}]*)|(%%?|##?)([^{}]*))\}")


def param_value(value: str | None, op: str, word: str) -> str | None:
    """What ``${NAME<op>word}`` expands to when NAME holds ``value`` (None: unset); None when the gate can't know
    (``${X:?}``, or a value it only has a placeholder for)."""
    if op in (":-", ":="):
        return word if not value else value
    if op in ("-", "="):
        return word if value is None else value
    if op == ":+":
        return word if value else ""
    if op == "+":
        return word if value is not None else ""
    if op in (":?", "?"):
        return value or None
    if value is None:
        return ""
    if "$__scout_" in value:
        return None
    import fnmatch

    cuts = range(len(value) + 1)
    if op == "%":  # the shortest suffix that matches
        return next((value[:i] for i in reversed(cuts) if fnmatch.fnmatchcase(value[i:], word)), value)
    if op == "%%":
        return next((value[:i] for i in cuts if fnmatch.fnmatchcase(value[i:], word)), value)
    if op == "#":
        return next((value[i:] for i in cuts if fnmatch.fnmatchcase(value[:i], word)), value)
    return next((value[i:] for i in reversed(cuts) if fnmatch.fnmatchcase(value[:i], word)), value)  # ##


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
    for one in _substitute(_tilde_dirs(path)):
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
        for one in _substitute(_tilde_dirs(word)):
            _STATE["secrets"] += 1
            if _STATE["secrets"] > MAX_CHECKED_PATHS:
                return None
            found = _SECRET(one if one.startswith(("/", "~", "$")) else cwd + one)
            if found is not None:
                return found
    return None


def _kb_read(words: list[str], reads: list[str], cwd: str, fed: str | None, oldpwd: str | None = None) -> str | None:
    """Ask _KB_READ about each file a reading command reads (scout.kb.raw_read's readers, past their prefixes),
    and each input redirect, as the shell hands them over: brace lists expanded, the command's own variables put
    in, from the folder it runs in (``cd``, ``env -C``). An ``xargs`` reader also reads what it is fed: the words
    ``echo``/``printf`` print, files under the folders a lister names (``git ls-files``, ``find``, ``grep -l``,
    ``rg -l|--files``), and from any other command files the gate can't see. Counted against the per-call caps."""
    from scout.kb import raw_read

    assert _KB_READ is not None
    lead = next((w for w in words if w not in raw_read._KEYWORDS), None)
    # (word, folder it runs in, how it reads: "file", "folder" or "covers", see KB_READ_HOW)
    targets = [] if lead == "[[" else [(word, cwd, "file") for word in reads]  # `[[ a < b ]]` compares
    roots: list[tuple[str, str, str]] = []
    stripped = raw_read._strip(words)
    if stripped.words:
        name = raw_read._command_name(stripped.words[0])
        operands = raw_read.read_operands(name, stripped.words[1:]) if name in raw_read._READ_COMMANDS else None
        if operands is not None:
            where = cwd if stripped.chdir is None else _into(cwd, _substitute(stripped.chdir)[0])
            how = "folder" if raw_read.reads_folders(name, stripped.words[1:]) else "file"
            if stripped.fed:
                names, listed = _fed_sources(fed)
                roots = [(root, cwd, "covers") for root in listed]
                marker = _replace_string([_unshield(w) for w in words])
                if marker is not None and not any(marker in o for o in operands):
                    marker = "".join(_SHIELD.get(c, c) for c in marker)  # `xargs -I{} cat "{}"`
                if marker is None:
                    operands = [*operands, *names]
                else:  # `xargs -I{} cat {}`: each input line goes where the marker is
                    given = names or [UNSEEN_INPUT]
                    operands = [o.replace(marker, n) for o in operands for n in (given if marker in o else [""])]
                    if listed and any(marker in o and o != marker for o in stripped.words[1:]):
                        operands.append(UNSEEN_INPUT)  # a listed path inside a longer word: not one the gate can judge
            targets += [(word, where, how) for word in operands]
    for word, where, how in [*targets, *roots]:
        found = _judge_read(word, where, how, oldpwd)
        if found is not None:
            return found
    return None


# How a reader takes a path it's handed: "file" opens files (a folder only prints an error), "folder" also reads a
# folder (``ls``, ``grep -r``), "covers" reads every file below it (the root an xargs lister lists).
KB_READ_HOW = ("file", "folder", "covers")


def _judge_read(word: str, where: str, how: str, oldpwd: str | None = None) -> str | None:
    from scout.kb import raw_read
    from scout.kb.hook_paths import TOO_MANY

    assert _KB_READ is not None
    expanded = raw_read.brace_expand(word)
    if expanded is None:
        return TOO_MANY
    for alt in expanded:
        for one in _substitute(_tilde_dirs(alt)):
            one = one.replace(ANY_NAME, "*")  # one name, any name: a glob for it is a superset
            key = "reads" if re.search(r"[*?\[$~]", one) else "literal"
            _STATE[key] += 1
            if _STATE[key] > (MAX_CHECKED_PATHS if key == "reads" else MAX_LITERAL_READS):
                return TOO_MANY
            if not one.startswith(("/", "~", "$")):
                one = where + one
            found = _KB_READ(_put_pwd(one, where, oldpwd), how)
            if found is not None:
                return found
    return None


# The variables that choose a git call's repository and its top, when its options don't.
_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE")


def _git_read(words: list[str], cwd: str, oldpwd: str | None = None) -> str | None:
    """Ask _GIT_READ about a git call that prints file contents (scout.kb.git_read.parse), once for each folder
    it can run in: the shell's (``cd``, ``env -C``), then each ``-C`` in turn, with the command's own variables,
    brace lists and ``$PWD`` put in. ``--git-dir``/``--work-tree`` come from its options, else from a
    ``GIT_DIR=``/``GIT_WORK_TREE=`` prefix or an earlier assignment, else (None) from the run's environment.
    The files it reads from disk (``grep -f``, ``blame --contents``, ``diff --no-index``) go to _KB_READ as a
    reader's do. A ``-C`` folder with a glob in it is one the gate can't know (unresolved)."""
    from scout.kb import git_read, raw_read
    from scout.kb.hook_paths import TOO_MANY

    assert _GIT_READ is not None
    stripped = raw_read._strip(words)
    if not stripped.words:
        return None
    git = _git_words(stripped.words)
    if git is None:
        return None
    read = git_read.parse([_unshield(w) for w in git])
    if read is None:
        return None
    if stripped.fed:  # `… | xargs git cat-file -p`: its objects or paths come from stdin, which the gate can't see
        read.trees, read.pathspecs, read.maybe_paths, read.files, read.no_index = [SUBST], [], [], [], False
    start = cwd if stripped.chdir is None else _into(cwd, _substitute(stripped.chdir)[0])
    given = {}
    for word in words[: len(words) - len(stripped.words)]:
        m = _ASSIGN.match(word)
        if m and m[1] in _GIT_ENV:
            given[m[1]] = _unshield(word.split("=", 1)[1])
    known = _STATE["vars"]

    def chosen(option: str | None, name: str) -> str | None:
        if option is not None:
            return option
        return given.get(name) or (f"${name}" if known.get(name) else None)

    def alternatives(word: str) -> list[str] | None:
        """``word`` as the shell hands it over, once per value (``$PWD`` is the shell's folder, ``start``)."""
        expanded = raw_read.brace_expand(word)
        if expanded is None:
            return None
        return [_put_pwd(one, start, oldpwd) for alt in expanded for one in _substitute(_tilde_dirs(alt))]

    def placed(word: str, where: str) -> list[str]:
        """A folder word as the gate resolves it, from the session's folder: one built from ``$PWD`` already is;
        any other relative one is from ``where``, the folder git is in by then (-C applies first)."""
        out = []
        for one in _substitute(word):
            pwd = bool(re.match(r"^\$(?:PWD\b|\{PWD[}:%#/])", one))
            for value in alternatives(one) or [SUBST]:
                if re.search(r"[*?\[]", value):
                    value = SUBST  # a glob the shell expands: a folder the gate can't know
                out.append(value if pwd or value.startswith(("/", "~", "$")) else _into(where, value).rstrip("/"))
        return out

    wheres = [start]
    for folder in read.chdirs:
        if not folder:
            continue  # `-C ""` leaves git where it is
        wheres = [_into("", one) if one else where for where in wheres for one in placed(folder, where)]
        if len(wheres) > MAX_CHECKED_PATHS:
            return TOO_MANY
    git_dir, work_tree = chosen(read.git_dir, "GIT_DIR"), chosen(read.work_tree, "GIT_WORK_TREE")
    for where in wheres:
        for word in read.files:
            found = _judge_read(word, where, "file", oldpwd)
            if found is not None:
                return found
        if read.no_index:  # files on disk, as `diff -r` and `grep -r` read them
            roots = [*read.maybe_paths, *read.pathspecs] or (["."] if read.unbounded == "cwd" else [])
            for word in roots:
                found = _judge_read(word, where, "covers", oldpwd)
                if found is not None:
                    return found
            continue
        lists = []
        for words_ in (read.trees, read.pathspecs, read.maybe_paths):
            flat: list[str] = []
            for word in words_:
                flat += alternatives(word) or [SUBST]
            lists.append(flat)
        if sum(len(x) for x in lists) > MAX_CHECKED_PATHS:
            return TOO_MANY
        git_dirs: list[str | None] = [None] if git_dir is None else list(placed(git_dir, where))
        work_trees: list[str | None] = [None] if work_tree is None else list(placed(work_tree, where))
        for gd in git_dirs:
            for wt in work_trees:
                one = git_read.GitRead(
                    sub=read.sub, git_dir=gd, work_tree=wt, trees=lists[0], pathspecs=lists[1],
                    maybe_paths=lists[2], unbounded=read.unbounded,
                )  # fmt: skip
                found = _GIT_READ(one, where)
                if found is not None:
                    return found
    return None


def _unshield(word: str) -> str:
    return "".join(UNSHIELD.get(c, c) for c in word)


def _replace_string(words: list[str]) -> str | None:
    """The word ``xargs -I`` (``-J``, ``-i``, ``--replace``) or ``parallel`` swaps each input line in for."""
    for i, word in enumerate(words):
        base = _basename(word)
        if base == "parallel":
            return "{}"
        if base != "xargs":
            continue
        for j, flag in enumerate(words[i + 1 :], start=i + 1):
            if flag in ("-I", "-J") and j + 1 < len(words):
                return words[j + 1]
            if re.fullmatch(r"-[IJ].+", flag):
                return flag[2:]
            if flag in ("-i", "--replace"):
                return "{}"
            if flag.startswith(("--replace=", "-i")):
                return flag.split("=", 1)[1] if "=" in flag else flag[2:]
            if not flag.startswith("-"):
                break
        return None
    return None


# `$OLDPWD` before the command's own first `cd`: a variable it didn't set (unresolved, recorded as unset_variable).
UNKNOWN_OLDPWD = "$__scout_oldpwd__"


def _put_pwd(word: str, where: str, oldpwd: str | None = None) -> str:
    """``$PWD`` is the folder the command runs in, not the hook's: ``where`` from the session's cwd (``.`` and
    ``./docs`` stay relative, so the gate joins them to it). ``$OLDPWD`` is the folder before the command's last
    ``cd`` (``oldpwd``), unknown before its first."""
    if "PWD" not in word:
        return word

    def form(folder: str) -> str:
        return folder.rstrip("/") if folder.startswith(("/", "~")) else ("./" + folder).rstrip("/")

    values = {"PWD": form(where), "OLDPWD": None if oldpwd is None else form(oldpwd)}

    def op(m: re.Match[str]) -> str:
        if m[1] not in values:
            return m[0]
        value = values[m[1]]
        if value is None:
            return UNKNOWN_OLDPWD
        found = param_value(value, m[2] or m[4], m[3] if m[2] else m[5])
        return SUBST if found is None else found

    def one(m: re.Match[str]) -> str:
        name = m[1] or m[2]
        if name not in values:
            return m[0]
        value = values[name]
        return UNKNOWN_OLDPWD if value is None else value

    return _VAR.sub(one, _PARAM.sub(op, word))


def _tilde_dirs(word: str) -> str:
    """``~+`` is ``$PWD`` and ``~-`` is ``$OLDPWD`` (bash and zsh), not a user named ``+`` or ``-``."""
    return re.sub(r"^~([+-])(?=/|$)", lambda m: "$PWD" if m[1] == "+" else "$OLDPWD", word)


def _into(cwd: str, dest: str) -> str:
    """The folder ``cd dest`` from ``cwd`` (both as run_escape keeps them: "" is the session's) leaves."""
    if dest == "-":
        return ""
    if dest.startswith(("/", "~")):
        return dest.rstrip("/") + "/"
    return cwd + dest.rstrip("/") + "/"


def _fed_sources(fed: str | None) -> tuple[list[str], list[str]]:
    """What ``xargs`` gets from the segment piped into it: the words ``echo`` or ``printf`` prints, split as
    ``xargs`` splits it; or the folders a lister prints file paths under (``git ls-files docs``, ``find docs``,
    ``grep -l x docs/*.md``, ``rg --files``), each a root it reads below; from anything else, one name the gate
    can't know (:data:`UNSEEN_INPUT`)."""
    if fed is None:
        return [UNSEEN_INPUT], []
    clean, _writes, _reads, strings = _redirects(fed)
    words = _tokens(clean)
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS):
        words = words[1:]
    if strings or _HD_MARK.search(fed) or not words:
        return [UNSEEN_INPUT], []
    name, args = _basename(words[0]), words[1:]
    if name == "echo":
        escapes = False
        while args and re.fullmatch(r"-[neE]+", args[0]):
            escapes = escapes or "e" in args[0]
            args = args[1:]
        text: str | None = " ".join(args)
        if escapes and text is not None:
            text = _unescape(text)
    elif name == "printf":
        args = args[1:] if args[:1] == ["--"] else args
        text = _printf(args[0], args[1:]) if args and args[0] != "-v" else None
    else:
        roots = _listed_roots(name, args)
        return ([UNSEEN_INPUT], []) if roots is None else ([], roots)
    return ([UNSEEN_INPUT], []) if text is None else (text.split(), [])


# A lister's flags that make it print something other than the paths, as found, of files under its operands.
_FIND_NOT_PATHS = {"-printf", "-fprintf", "-fprint", "-fprint0", "-exec", "-execdir", "-ok", "-okdir", "-ls", "-fls"}


def _listed_roots(name: str, args: list[str]) -> list[str] | None:
    """The folders (or files) whose files a lister prints the paths of, as they resolve from where it runs;
    None when it isn't one or may print anything else."""
    from scout.kb import raw_read

    if name == "git":
        if args[:1] != ["ls-files"] or any(a == "--full-name" or a.startswith("--format") for a in args):
            return None
        return [a for a in args[1:] if not a.startswith("-")] or ["."]
    if name == "find":
        if any(a in _FIND_NOT_PATHS for a in args):
            return None
        return raw_read.read_operands("find", args) or ["."]
    if name in ("grep", "egrep", "fgrep", "rg"):
        flags, _plain = raw_read._split_args(raw_read._operands(name, args))
        short = "l" if name == "rg" else "lL"
        lists = any(
            f in ("--files-with-matches", "--files-without-match") or (name == "rg" and f == "--files")
            or re.fullmatch(rf"-[A-Za-z]*[{short}][A-Za-z]*", f)
            for f in flags
        )  # fmt: skip
        if not lists:
            return None
        return raw_read.read_operands(name, args) or ["."]
    return raw_read.listed_roots(name, args)  # fd, ag -g, ack -f, ugrep -l (#791)


_PRINTF_SPEC = re.compile(r"%(?:%|[-+ #0-9.]*[sbdiu])")


def _printf(fmt: str, args: list[str]) -> str | None:
    """What ``printf fmt args…`` prints, for ``%s``/``%b``/``%d``-style formats; None for any other."""
    fmt = _unescape(fmt)
    if "%" in _PRINTF_SPEC.sub("", fmt):
        return None
    out: list[str] = []
    at = 0
    while True:
        used = 0

        def fill(m: re.Match[str]) -> str:
            nonlocal at, used
            if m[0] == "%%":
                return "%"
            value = args[at] if at < len(args) else ""
            at, used = at + 1, used + 1
            return _unescape(value) if m[0].endswith("b") else value

        out.append(_PRINTF_SPEC.sub(fill, fmt))
        if not used or at >= len(args):
            return "".join(out)


_ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", "'": "'", '"': '"', "?": "?",
}  # fmt: skip
_ESCAPE_DIGITS = {"x": (16, 2), "u": (16, 4), "U": (16, 8)}


def _unescape(text: str) -> str:
    """``text`` with the backslash escapes ``$'…'``, ``printf`` and ``echo -e`` read: ``\\x2d``, ``\\055``, ``\\n``…
    A NUL ends the text, as it does for the shell."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        n = text[i + 1]
        if n in _ESCAPES:
            out.append(_ESCAPES[n])
            i += 2
        elif n in _ESCAPE_DIGITS:
            base, width = _ESCAPE_DIGITS[n]
            m = re.match(rf"[0-9a-fA-F]{{1,{width}}}", text[i + 2 :])
            if m is None:
                out.append("\\" + n)
                i += 2
            else:
                out.append(chr(min(int(m[0], base), 0x10FFFF)))
                i += 2 + len(m[0])
        elif n in "01234567":
            m = re.match(r"0?[0-7]{1,3}", text[i + 1 :])
            assert m is not None
            out.append(chr(int(m[0], 8) & 0xFF))
            i += 1 + len(m[0])
        elif n == "c" and i + 2 < len(text):
            out.append(chr(ord(text[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + n)
            i += 2
    return "".join(out).split("\0", 1)[0]


def _ansi_c(command: str) -> str:
    """``command`` with each ``$'…'`` (outside other quotes) decoded into a plain single-quoted word, so
    ``$'knowledge\\x2dbase/x.md'`` is read as ``'knowledge-base/x.md'``. The spans are :func:`_quoted_spans`'s, read
    the zsh way (the shell runs use). An even run of ``$`` before the quote (``$$'…'``) is bash's ``$$`` and a plain
    quote: it is left as written (so :func:`_backgrounds` reads it both ways, #793), and when the quote holds a
    backslash (the two readings end it apart) a run's read fails closed."""
    if "$'" not in command:
        return command
    out: list[str] = []
    at = 0
    for a, b in _quoted_spans(command, even_dollars_escape=True):
        if command[a] != "'":
            continue
        head = command[:a]
        run = len(head) - len(head.rstrip("$"))
        if run and head[: a - run].endswith("\\"):
            run -= 1  # `\$'…'`: the first `$` is a character
        if not run:
            continue
        body = command[a + 1 : b - 1]
        if run % 2 == 0:
            # Left as written, so _backgrounds still reads it both ways (#793); with a backslash inside, the two
            # readings end the quote apart, so a run's read can't be judged.
            if "\\" in body:
                _STATE["unparsed"] = True
            continue
        decoded = _unescape(body)
        out += [command[at : a - 1], "'" + decoded.replace("'", "'\\''") + "'"]
        at = b
    return "".join([*out, command[at:]])


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


def expansion_escape(command: str) -> str | None:
    """The form of a parameter expansion in ``command`` that assigns a name that matters (HOME, XDG_CONFIG_HOME, the
    run's own variables) or one built at run time: ``${HOME:=…}``, ``${HOME::=…}``, ``${(P)v::=…}``. Read on the
    whole text, since the parser splits segments at ``(``, and a nested ``sh -c`` or ``eval`` is a substring of it."""
    fd = _NAMED_FD.search(command)
    if fd is not None:
        return _name_form(fd[1])
    m = _PARAM_ASSIGN.search(command)
    if m is None:
        return None
    return _name_form(m[1]) if m[1] else HOME_FORM


def even_dollar_escape(command: str) -> str | None:
    """HOME_FORM when ``command`` has an even run of ``$`` before a quote that holds a backslash (``eval $$'; \\x72ead
    HOME'``): bash reads ``$$`` and a plain quote, zsh a ``$`` and then ``$'…'``, and :func:`_ansi_c` keeps bash's
    reading, so the command zsh runs may set HOME unread. The gate asks it last, after :func:`run_escape`, whose own
    reasons (a KB read, a background, either reading) say more. No scheduled run in the replay has one."""
    return HOME_FORM if _EVEN_DOLLAR_QUOTE.search(command) else None


def _name_form(name: str) -> str | None:
    """The form a run's change to the variable ``name`` takes, or None when it may change it."""
    if name in RUN_VARS:
        return "env"
    if name == "HOME":
        return HOME_FORM
    return XDG_CONFIG_FORM if name == "XDG_CONFIG_HOME" else None


def _target_form(word: str, *, assigns: bool, value_matters: bool = True) -> str | None:
    """The form of setting the target ``word`` (``NAME``, ``NAME=value``, ``NAME+=value``, ``NAME[sub]=value``):
    HOME_FORM for a name the parser can't read for certain, the name's own form for one that matters, else None.
    ``assigns``: a bare ``NAME`` sets it too (``read NAME``, ``local NAME``), not only ``NAME=…``."""
    name, op, _ = re.match(r"([^=+]*(?:\+(?!=)[^=+]*)*)(\+?=)?(.*)", word, re.S).groups()  # type: ignore[union-attr]
    if not _PLAIN_NAME.fullmatch(name):
        return HOME_FORM
    form = _name_form(name)
    if form is None or (op is None and not assigns):
        return None
    if form == HOME_FORM and value_matters and op is not None and not _sets_home(word):
        return None  # `HOME=$HOME`, `HOME=~`, `HOME+=`: HOME stays as it is
    return form


def _name_escape(words: list[str]) -> str | None:
    """The form of this command's change to a variable that matters, or to one it names at run time (which may be
    HOME): HOME_FORM, XDG_CONFIG_FORM or "env". It reads the command's own assignments (``HOME=x cmd``, ``HOME+=…``,
    ``HOME[1]=…``, and ``$v=…`` or ``H$v=…`` as a command word, which ``eval`` makes an assignment), an earlier
    segment's the parser recorded (``HOME=x; …``), an arithmetic assignment (``(( HOME = 0 ))``, ``let``), and the
    targets of the builtins that set or unset what they name: ``export``/``typeset``/… (``export HOME`` alone
    changes nothing), ``unset``, ``read``, ``mapfile``, ``vared``, ``getopts``, ``printf -v``, ``for``/``select``.
    A target is denied when it isn't a plain name (``$v``, ``H$v``, ``HOM?``): the parser can't rule out HOME.
    An expansion that assigns is :func:`expansion_escape`'s."""
    recorded = _STATE["vars"].get("HOME")
    if recorded is not None and recorded not in ([v] for v in _SAME_HOME):
        return HOME_FORM
    i = 0
    while i < len(words) and (
        _ASSIGN_ANY.match(words[i]) or words[i] in _KEYWORDS or words[i] in _PREFIX or words[i] == "repeat"
    ):
        if words[i] == "repeat":
            i += 1  # zsh's `repeat N cmd`: skip the count too
        elif _ASSIGN_ANY.match(words[i]):
            form = _target_form(re.sub(r"\[[^\]]*\]", "", words[i], count=1), assigns=False)
            if form is not None:
                return form
        i += 1
    if i == len(words):
        return None
    head, args = words[i], words[i + 1 :]
    if _BUILT_HEAD.match(head):
        return HOME_FORM  # `$v=…`, `H${v}=…`: `eval` makes it an assignment to a name built at run time
    if args and args[0] in _ARITH_OPS:
        return _target_form(f"{head}=x", assigns=False, value_matters=False)  # `(( HOME = 0 ))`
    name = _basename(head)
    if name not in _NAMERS:
        return None
    if name in _STRICT_SETTERS:
        for arg in args:
            if _PROTECTED_WORD.search(arg) or (not arg.startswith("-") and _BUILDS_A_NAME.search(arg)):
                return HOME_FORM
        return None
    if name == "let":
        for arg in args:
            for m in _ARITH_ASSIGN.finditer(arg):
                form = _target_form(f"{m[1]}=x", assigns=False, value_matters=False)
                if form is not None:
                    return form
        return None
    if name in ("print", "printf"):
        for arg in args:
            if arg == "--" or not arg.startswith("-") or len(arg) == 1:
                break
            if "$" in arg or "`" in arg:
                return HOME_FORM  # `print -$x NAME`: the option may be -v, and `-vHOME` names its target itself
    targets = _targets(name, args)
    options = [a for a in args if a.startswith(("-", "+")) and a not in ("-", "--")]
    for target in targets:
        if name == "export" and not options and _PLAIN_NAME.fullmatch(target):
            continue  # `export HOME`, `export SCOUT_MODE`: the value stays as it is
        form = _target_form(target, assigns=True)
        if form is not None:
            return form
        if name in _DECLARERS and "-n" in options and "=" in target:
            form = _name_form(target.split("=", 1)[1])  # `declare -n ref=HOME`: every later `ref=…` sets HOME
            if form is not None:
                return form
    return None


def _targets(name: str, args: list[str]) -> list[str]:
    """The words ``name``'s builtin sets or unsets, among ``args``: not its options, and not their values."""
    if name in ("for", "select"):
        return args[:1]
    if name == "getopts":
        return (args[1:] if args[:1] == ["--"] else args)[1:2]
    if name in ("printf", "print"):
        for i, a in enumerate(args):
            if a == "--" or not a.startswith("-") or len(a) == 1:
                break
            if "v" in a[1:]:  # `-v NAME`, zsh's `-rv NAME`, `-vNAME`
                rest = a[a.index("v", 1) + 1 :]
                return [rest] if rest else args[i + 1 : i + 2]
        return []
    if name == "zstyle":
        if args[:1] in (["-s"], ["-a"], ["-b"]):
            return args[3:4]
        return args[1:2] if args[:1] == ["-g"] else []
    if name == "zformat":
        return args[1:2] if args[:1] in (["-f"], ["-a"]) else []
    if name == "strftime":
        return args[1:2] if args[:1] and args[0].startswith("-") and "s" in args[0] else []
    takes = _NAMER_VALUE_OPTIONS.get(name, "")
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith(("-", "+")) and len(a) > 1 and a != "--" and not out:
            letters = a[1:]
            first = next((k for k, c in enumerate(letters) if c in takes), None)
            if first is not None and first == len(letters) - 1:
                i += 1  # its value is the next word: `-t $T`, `-p "$prompt"`, `-d ''`
            elif (
                name in _DECLARERS and letters[-1] in _NUMBER_OPTIONS and args[i + 1 : i + 2] and args[i + 1].isdigit()
            ):  # noqa: E501
                i += 1  # `typeset -i 10 x`: a base or width
            i += 1
            continue
        if a != "--":
            out.append(a)
        i += 1
    return out


def _words_escape(words: list[str], depth: int) -> str | None:
    form = _name_escape(words)
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS):
        if _sets_run_var(words[0]):
            return "env"
        if _GIT_CONFIG_VAR.match(words[0]):
            form = GIT_CONFIG_FORM
        if _XDG_CONFIG_VAR.match(words[0]):
            form = form or XDG_CONFIG_FORM
        words = words[1:]
    found = _command_escape(words, depth) if words else None
    return found if found == "env" else form or found


def _command_escape(words: list[str], depth: int) -> str | None:
    command = words
    while command and command[0] in _PREFIX:
        command = command[1:]
    if command is not words and command and _ASSIGN.match(command[0]):
        return _words_escape(command, depth)  # `sudo HOME=/a ls`: sudo sets the variables it is given
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
        if any(_GIT_CONFIG_VAR.match(a) for a in args):
            return GIT_CONFIG_FORM
        return XDG_CONFIG_FORM if any(_XDG_CONFIG_VAR.match(a) for a in args) else None
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
    kb_read: Callable[[str, str], str | None] | None = None,
    git_read: Callable[[GitRead, str], str | None] | None = None,
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
    names it spells. ``secret`` judges each path it names for a read of Scout's secrets. ``kb_read`` judges each
    file a reading command reads (``cat``, ``ls``, ``grep``…, input redirects) by the path the shell hands it,
    returning :data:`KB_READ` or :data:`UNRESOLVED_READ` (see :func:`_kb_read`). ``git_read`` (with ``kb_read``)
    judges what each git call reads, given as a :class:`scout.kb.git_read.GitRead` and the folder it runs in
    (see :func:`_git_read`), returning the same.
    """
    global _PROTECT, _SECRET, _KB_READ, _GIT_READ
    outer = depth == 0
    if outer:
        _PROTECT, _SECRET, _KB_READ, _GIT_READ = protect, secret, kb_read, git_read
        _STATE.update(
            checked=0, secrets=0, reads=0, literal=0, vars={}, bodies=[], unparsed=False, capped=False, parsing=False
        )
    try:
        found = _run_escape(command, depth)
        # Fail closed where the walk had to guess: an unclosed quote or a zsh group it can't rewrite (a run's
        # read), or a variable with more values than are checked (a read or a write).
        guessed = (_STATE["unparsed"] and kb_read is not None) or (
            _STATE["capped"] and (kb_read is not None or protect is not None)
        )
        if outer and found is None and guessed:
            from scout.kb.hook_paths import TOO_MANY

            return TOO_MANY
        return found
    finally:
        if outer:
            _PROTECT = _SECRET = _KB_READ = _GIT_READ = None


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
    command = _strip_comments(_ansi_c(command.replace("\\\n", " ")))
    if _KB_READ is not None:
        command = _zsh_groups(command)
    # Every verdict with where it sits in the command, so the first form in the text names the deny
    # (`c=$(git commit-tree …) && git update-ref …` is commit-tree; `git update-ref HEAD "$(git commit-tree …)"`
    # is update-ref).
    results: list[tuple[int, str | None]] = []
    # Each substitution's command on its own, and a placeholder in its place (see SUBST): `> $(printf .claude)/x`.
    # Each body is judged when the walk below gets to it, so it sees the variables set before it
    # (`f=docs/a.md && echo "$(wc -c < $f)"`, `for f in a b; do n=$(grep -c x "$f"); done`).
    spans = _substitutions(command)
    pending: list[tuple[int, str]] = []
    rebuilt, at, shift = [], 0, 0
    for start, end, a, b in spans:
        # A process substitution becomes the pipe the shell hands over (its own command is judged too).
        body = command[a:b]
        known = (_known_output(body) or _one_name_output(body)) if command[start] not in "<>" else "/dev/fd/63"
        rebuilt += [command[at:start], known or SUBST]
        pending.append((start - shift, body))
        shift += (end - start) - len(known or SUBST)
        at = end
    command = "".join([*rebuilt, command[at:]])

    def judge_bodies(before: int) -> None:
        while pending and pending[0][0] < before:
            at_, body_ = pending.pop(0)
            results.append((at_, _run_escape(body_, depth + 1, split=False)))

    for text, expands in bodies:  # an unquoted here-document runs its substitutions; quotes there are text
        if expands:
            for _s, _e, a, b in _substitutions(text, quotes=False):
                results.append((len(command), _run_escape(text[a:b], depth + 1)))
    # Both readings of `$$'` (see _quoted_spans): a background under either one is a background.
    detach = any(_backgrounds(_mask_quoted(command, even_dollars_escape=way)) for way in (False, True))
    cwd = ""  # follows each `cd`, so `cd .git/hooks && rm pre-commit` reads as the path it writes
    oldpwd: str | None = None  # the folder before the last `cd` (`$OLDPWD`, `~-`): unknown until one
    previous: str | None = None
    position = 0
    for segment, op in _segments_with_ops(command):
        position += len(op)
        here, position = position, position + len(segment)
        fed = previous if op in ("|", "|&") else None  # the segment piped into this one
        judge_bodies(position)
        unparsed, _STATE["unparsed"], _STATE["parsing"] = _STATE["unparsed"], False, True
        clean, targets, reads, strings = _redirects(segment)
        words = _tokens(clean)
        _STATE["parsing"] = False
        # A segment that doesn't parse is the gate's misreading (a real unclosed quote is a syntax error, and nothing
        # runs): it fails closed only where it may hide a read (a reading command or an input redirect).
        _STATE["unparsed"] = unparsed or (_STATE["unparsed"] and _reads_something(words, reads))
        _record_vars(words)
        found = _words_escape(words, depth) or _hook_write_form(words, targets, cwd)
        if found is None and _PROTECT is not None:
            for program in _interpreter_programs(words, segment, strings, fed) or []:
                found = _program_escape(program, cwd, depth)
                if found is not None:
                    break
        if found is None and _shell_stdin_program(words):
            found = _shell_stdin_escape(segment, strings, fed, depth)
        if found is None and (_SECRET is not None or _KB_READ is not None):
            # The same words, with what their quotes keep literal marked so (see _SHIELD): a quoted regex
            # (`grep -o "a/(b|c)[^/]*/…"`) is no glob for either check.
            literal_words, literal_reads = words, reads
            shielded = _shield_quoted(segment)
            if shielded != segment:
                kb_clean, _writes, literal_reads, _strings = _redirects(shielded)
                literal_words = _tokens(kb_clean)
            found = _secret_read(literal_words, literal_reads, cwd)
            if found is None and _KB_READ is not None:
                found = _kb_read(literal_words, literal_reads, cwd, fed, oldpwd)
            if found is None and _GIT_READ is not None and _KB_READ is not None:
                found = _git_read(literal_words, cwd, oldpwd)
        if found == "env":
            return "env"
        results.append((here, found))
        previous = segment
        if words[:1] in (["cd"], ["pushd"]):
            dests = [w for w in words[1:] if w not in ("-L", "-P", "-e", "-@", "--")]
            dest = _substitute(dests[0])[0] if dests else "~"
            cwd, oldpwd = ((oldpwd or "") if dest == "-" else _into(cwd, dest)), cwd
        elif words[:1] == ["popd"]:
            cwd = ""  # back where the pushd started, as far as a run's `pushd x && …; popd` goes
    judge_bodies(len(command) + 1)
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

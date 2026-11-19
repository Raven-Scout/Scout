"""The files that decide which hooks run, which a scheduled run may not write (spec 2026-11-13 §8.5, Phase 1b).

Scout's checks live in git's hooks (the pre-commit hook with the privacy and size
blocks, #453/#607), in the agent's hooks (Claude Code's PreToolUse hooks:
kb-read-gate, outbound-gate, mcp-tool-gate), in the engine those hooks run, and in
the runner scripts that start each run with ``SCOUT_MODE`` set. A run that could
write any of them could turn the checks off for itself or for every later run: a
``.git/hooks/pre-commit`` emptied, a ``core.hooksPath`` in a git config,
``"disableAllHooks": true`` in a ``.claude/settings.local.json``, ``/usr/bin/true`` in
the launcher's interpreter cache, or ``unset SCOUT_MODE`` in
``scripts/claude-with-retry.sh``. This module is the one list the file tools
(Write, Edit, MultiEdit, NotebookEdit) and the shell check (``live_run.run_escape``)
are both judged against.

Protected (each with the form name a deny and the audit row use):

- ``.git`` (forms ``.git/hooks``, ``.git/config``, ``.git``): the vault's git dir, all of
  it, a worktree's real and common git dir and its ``gitdir:`` file, and git's own hooks
  dir (``git rev-parse --git-path hooks``, which honours ``core.hooksPath``); the vault's
  ``.gitattributes`` and ``.gitmodules``;
- ``git config``: ``~/.gitconfig``, ``$GIT_CONFIG_GLOBAL``, ``$XDG_CONFIG_HOME/git/``;
- ``agent settings``: ``settings.json`` / ``settings.local.json``, ``.claude.json`` and the
  plugin store under ``~/.claude`` and ``$CLAUDE_CONFIG_DIR``; the vault's
  ``.claude/settings*.json``, ``.claude/hooks|commands|agents/`` and ``.mcp.json``; the
  hook config dirs other agent CLIs read (``.codex``, ``.gemini``, ``.agents``, ``.grok``,
  ``.cursor``) in the vault and the home folder;
- ``Scout's engine``: the plugin the running engine ships in (its launcher's interpreter
  cache, its venv, its hooks.json), ``$CLAUDE_PLUGIN_ROOT``, the venv the engine runs in
  (``sys.prefix``), the engine pointer (``~/.local/state/scout/engine.json``), and the
  plugin the vault's pre-commit hook calls;
- ``Scout's runner scripts``: the vault files bootstrap installs and runs (``run-*.sh``,
  ``scripts/claude-with-retry.sh``, ``scripts/scoutctl`` …; :data:`RUNNER_FILES`);
- ``startup files``: the shell's login and rc files and ``~/Library/LaunchAgents``;
- ``ssh config``: ``~/.ssh`` (its config and keys decide what ``git push`` over ssh runs after a run; the OS
  sandbox lets ssh itself add to ``known_hosts``);
- ``Scout's secrets``: ``~/.scout-secrets`` and ``$SCOUT_SECRETS_DIR`` (also read-gated in runs: Read, Grep, Glob,
  and a Bash command that spells a secret's path, except :data:`TELEGRAM_SECRETS` when the run has no send-only
  broker);
- ``Scout's delegate state``: the vault's ``.scout-state/delegate-mirrors.json`` and ``.scout-state/delegate-scan/``.

:func:`protected_specs` is the list as built (before resolving and case-folding), for the OS write sandbox
(Phase 1c); :func:`protected_roots` is it normalised for matching.

A path is compared after ``~``, ``..`` and symlinks are resolved *and* as spelled (so a
``.git/hooks`` that is a symlink out of ``.git`` still counts), NFC-normalised and
case-folded: a case-insensitive disk takes ``.GIT/HOOKS`` for ``.git/hooks``, and on a
case-sensitive one denying that spelling costs nothing. :func:`removes_protected` says
whether removing or moving a path (``rm -rf .claude``) takes a protected file with it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

GIT_HOOKS = ".git/hooks"
GIT_CONFIG_FILE = ".git/config"
GIT_DIR = ".git"
USER_GIT_CONFIG = "git config"
AGENT_SETTINGS = "agent settings"
ENGINE = "Scout's engine"
RUNNER_SCRIPTS = "Scout's runner scripts"
STARTUP = "startup files"
HARD_LINK = "a hard-linked file"
# The owner's switch for the scheduled runs' OS write sandbox, in ~/.local/state/scout/ (scout.sandbox.switch).
SANDBOX_SWITCH_NAME = "run-sandbox.json"
# The owner's model-provider consent (scout.agent_runtimes.consent, spec 2026-11-13 §8.4), also in
# ~/.local/state/scout/: a run that could write it could consent to a provider for the next run.
PROVIDER_CONSENT_NAME = "provider-consent.json"
# The owner's run limits (scout.agent_runtimes.limits): raising a run's timeout or token ceiling, or turning
# one off, is the owner's alone, so a run can't loosen its own or the next run's.
RUN_LIMITS_NAME = "run-limits.json"
# The run brokers' owner folder, in ~/.local/state/scout/ (scout.sandbox.broker): the Telegram limits, the hourly
# send count and the drain lock.
RUN_BROKERS_NAME = "run-brokers"
# The owner's reviewed doctor warnings (scout.doctor_triage): a run that could write them could mark its own
# privacy warnings reviewed and hide them from the owner.
REVIEWED_WARNINGS_NAME = "reviewed-warnings.json"
SECRETS = "Scout's secrets"
SCHEDULE = "Scout's schedule"
PROGRAMS = "programs the runners run"
SSH_CONFIG = "ssh config"
DELEGATE_STATE = "Scout's delegate state"
TOO_MANY = "too many paths to check"
UNRESOLVED = "a path the gate can't resolve"
INLINE_WRITE = "an inline program that writes next to a protected name"

AGENT_DIRS = (".codex", ".gemini", ".agents", ".grok", ".cursor")
CLAUDE_SETTINGS_FILES = ("settings.json", "settings.local.json")
VAULT_CLAUDE_DIRS = ("hooks", "commands", "agents")
SHELL_STARTUP = (".zshenv", ".zshrc", ".zprofile", ".zlogin", ".bash_profile", ".bashrc", ".bash_login", ".profile")

# The vault files bootstrap installs and the runners execute (scout.scripts.bootstrap: _CAT1_TEMPLATES but the
# .gitignore, _CAT1B_RUNNERS, and the scripts in _CAT1_FILES_FROM_PLUGIN). Copied so the hook needn't import
# bootstrap; tests/unit/test_hooks_write_gate.py keeps the two in step.
RUNNER_FILES: tuple[str, ...] = (
    "run-scout.sh",
    "run-dreaming.sh",
    "run-research.sh",
    "scripts/scout-tz.sh",
    "scripts/scoutctl",
    "scripts/budget-check.sh",
    "scripts/heartbeat.sh",
    "scripts/pre-session-data.sh",
    "scripts/cc-session-cache.sh",
    "scripts/write-session-cost.sh",
    "scripts/rate-limit-detect.sh",
    "scripts/claude-with-retry.sh",
    "scripts/post-session-backfill.sh",
    "scripts/materialize-daily-file.sh",
    "scripts/run-outcome.sh",
    "scripts/scout-lock.sh",
    "scripts/git-safe-commit.sh",
    "hooks/kb-pre-filter.sh",
    "scripts/recurring-task-status.py",
    "scripts/vault-freshness.py",
    "scripts/session-lane-liveness.py",
    "scripts/kb-shard-log.py",
)


@dataclass(frozen=True)
class Spec:
    path: str  # as built: absolute, ``~`` expanded by the caller's HOME, symlinks not resolved, case kept
    form: str
    tree: bool  # a directory and everything under it, else this one file


@dataclass(frozen=True)
class Root:
    key: str  # canonical (see canon)
    form: str
    tree: bool  # a directory and everything under it, else this one file


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _expand(path: str | Path, env: Mapping[str, str]) -> str:
    text = str(path)
    if text == "~" or text.startswith("~/"):
        return str(env.get("HOME") or Path.home()) + text[1:]
    return os.path.expanduser(text)  # ~user: that user's home


def canon(path: str | Path, env: Mapping[str, str] | None = None) -> str:
    """``path`` with ``~``, ``..`` and symlinks resolved, NFC-normalised and case-folded."""
    return _norm(os.path.realpath(_expand(path, os.environ if env is None else env)))


def spelled(path: str | Path, env: Mapping[str, str] | None = None) -> str:
    """``path`` as written (``~`` and ``..`` resolved, symlinks not), NFC-normalised and case-folded."""
    return _norm(os.path.normpath(os.path.abspath(_expand(path, os.environ if env is None else env))))


def _under(key: str, root: str) -> bool:
    return key == root or key.startswith(root.rstrip("/") + "/")


def git_dirs(vault: Path) -> list[Path]:
    """The vault's git dir, or for a worktree its ``gitdir:`` and common dir. Missing or unreadable: none."""
    dotgit = vault / ".git"
    if dotgit.is_dir():
        return [dotgit]
    try:
        first = dotgit.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return []
    if not first.startswith("gitdir:"):
        return []
    gitdir = Path(first.removeprefix("gitdir:").strip())
    gitdir = gitdir if gitdir.is_absolute() else vault / gitdir
    dirs = [gitdir]
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        common = ""
    if common:
        dirs.append(Path(common) if Path(common).is_absolute() else gitdir / common)
    return dirs


def _remaining(deadline: float | None, cap: float) -> float:
    """Seconds a git call may take: ``cap``, or what is left before ``deadline`` if that is less."""
    return cap if deadline is None else min(cap, deadline - time.monotonic())


def git_hooks_dir(vault: Path, *, deadline: float | None = None) -> Path | None:
    """Where git runs this vault's hooks from: ``core.hooksPath`` if set, else the git dir's ``hooks``."""
    timeout = _remaining(deadline, _GIT_TIMEOUT_S)
    if timeout <= 0:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    return Path(line) if out.returncode == 0 and line else None


def _precommit_engine(vault: Path, hooks: Path | None) -> Path | None:
    """The plugin dir of the scoutctl the vault's pre-commit hook calls (its ``SCOUTCTL=`` line)."""
    for hooks_dir in [*([hooks] if hooks is not None else []), *(g / "hooks" for g in git_dirs(vault))]:
        try:
            text = (hooks_dir / "pre-commit").read_text(encoding="utf-8", errors="replace")[:65536]
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("SCOUTCTL="):
                value = line.removeprefix("SCOUTCTL=").strip().strip("'\"")
                path = Path(value)
                # <plugin>/engine/bin/scoutctl
                return path.parents[2] if path.is_absolute() and len(path.parents) > 2 else None
    return None


def _pointer_venv(pointer: Path) -> Path | None:
    """The venv the engine pointer names (its ``"python"``, ``<venv>/bin/python``): the launcher may run it."""
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    python = data.get("python") if isinstance(data, dict) else None
    if not isinstance(python, str) or not python.startswith("/"):
        return None
    venv = Path(python).parent.parent
    return venv if (venv / "pyvenv.cfg").is_file() else None


def engine_roots(env: Mapping[str, str]) -> list[Path]:
    """The engine the hooks run and everything that picks it (see the module doc)."""
    import scout

    package = Path(scout.__file__).resolve().parent
    roots = [package, package.parents[1], Path(sys.prefix)]  # scout/, the plugin (<plugin>/engine/scout), the venv
    # The interpreter the venv was made from: its stdlib is what every hook imports (review of #642).
    for base in {sys.base_prefix, sys.base_exec_prefix} - {sys.prefix}:
        roots.append(Path(base))
    if env.get("CLAUDE_PLUGIN_ROOT"):
        roots.append(Path(env["CLAUDE_PLUGIN_ROOT"]))
    home = Path(env.get("HOME") or Path.home())
    state = home / ".local" / "state" / "scout"
    pointer = state / "engine.json"
    roots.append(pointer)
    venv = _pointer_venv(pointer)
    if venv is not None:
        roots.append(venv)
    # The owner's switch for the scheduled runs' write sandbox (scout.sandbox, Phase 1c): a run that could write
    # it could turn the sandbox off for the next run.
    roots.append(state / SANDBOX_SWITCH_NAME)
    roots.append(state / PROVIDER_CONSENT_NAME)
    roots.append(state / RUN_LIMITS_NAME)
    # The run brokers' limits and counts (Phase 1d): a run that could write them could lift its own send limit.
    roots.append(state / RUN_BROKERS_NAME)
    roots.append(state / REVIEWED_WARNINGS_NAME)
    return roots


# The whole roots build inside a hook (protected_roots): kb-read-gate's watchdog blocks the call at 8 s, so a few slow
# gits must not use it all up. Past this, the hook skips the git calls it has left; the OS sandbox's profile is
# built once per run with no deadline and keeps the full list (review of #642).
HOOK_ROOTS_BUDGET_S = 4.0

_ENV_KEYS = (
    "HOME", "GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "SCOUT_SECRETS_DIR",
    "GIT_CONFIG_SYSTEM", "PATH", "SCOUT_CLAUDE_BIN",
)  # fmt: skip
_ROOTS_CACHE: dict[tuple[str, ...], list[Root]] = {}


def protected_roots(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Root]:
    """The protected roots for this vault and environment, built once per process (a hook is one process).

    Building them runs ``git rev-parse`` and resolves every root; a command naming hundreds of paths checks
    each against this one list, so the gate stays fast however many paths it is handed (review of #637).
    """
    env = os.environ if env is None else env
    key = (str(vault), *(env.get(k, "") for k in _ENV_KEYS))
    if key not in _ROOTS_CACHE:
        _ROOTS_CACHE[key] = _build_roots(vault, env, deadline=time.monotonic() + HOOK_ROOTS_BUDGET_S)
    return _ROOTS_CACHE[key]


def _build_roots(vault: Path | None, env: Mapping[str, str], *, deadline: float | None = None) -> list[Root]:
    roots: list[Root] = []
    for spec in protected_specs(vault, env, deadline=deadline):
        for key in {canon(spec.path, env), spelled(spec.path, env)}:
            if key == "/dev" or key.startswith("/dev/"):
                # GIT_CONFIG_GLOBAL=/dev/null (the eval harness sets it) names no file a run could change, and
                # protecting it would refuse every `2>/dev/null`. The sandbox profile skips devices the same way.
                continue
            roots.append(Root(key, spec.form, spec.tree))
    return roots


def secrets_dirs(env: Mapping[str, str]) -> list[Path]:
    """Where Scout keeps its API keys and tokens: ``~/.scout-secrets``, and ``$SCOUT_SECRETS_DIR`` when it is set
    (scout.linear_activity.secrets_dir reads the same variable)."""
    home = Path(env.get("HOME") or Path.home())
    dirs = [home / ".scout-secrets"]
    if env.get("SCOUT_SECRETS_DIR"):
        dirs.append(Path(env["SCOUT_SECRETS_DIR"]))
    return dirs


def protected_specs(
    vault: Path | None, env: Mapping[str, str] | None = None, *, deadline: float | None = None
) -> list[Spec]:
    """Every protected path as written here (``~`` unexpanded, not resolved, not case-folded), with its form.

    :func:`protected_roots` is these, normalised for matching. The scheduled runs' OS write sandbox
    (:mod:`scout.sandbox.profile`, Phase 1c) is generated from the same list, so the two layers never drift.
    ``deadline`` (a :func:`time.monotonic` value) bounds the git calls: one past it is skipped (the hooks pass
    :data:`HOOK_ROOTS_BUDGET_S`; the profile passes none).
    """
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    roots: list[Spec] = []

    def add(path: Path | str, form: str, *, tree: bool) -> None:
        roots.append(Spec(str(path), form, tree))

    if vault is not None:
        add(vault / ".git", GIT_DIR, tree=False)
        for gitdir in git_dirs(vault):
            add(gitdir, GIT_DIR, tree=True)
        hooks = git_hooks_dir(vault, deadline=deadline)
        if hooks is not None:
            add(hooks, GIT_HOOKS, tree=True)
        for name in (".gitattributes", ".gitmodules", ".mcp.json"):
            add(vault / name, GIT_DIR if name.startswith(".git") else AGENT_SETTINGS, tree=False)
        for name in CLAUDE_SETTINGS_FILES:
            add(vault / ".claude" / name, AGENT_SETTINGS, tree=False)
        for name in VAULT_CLAUDE_DIRS:
            add(vault / ".claude" / name, AGENT_SETTINGS, tree=True)
        for name in AGENT_DIRS:
            add(vault / name, AGENT_SETTINGS, tree=True)
        for rel in RUNNER_FILES:
            add(vault / rel, RUNNER_SCRIPTS, tree=False)
        # Which delegates exist and what their mirrors may hold (#550): a run that wrote these could widen a
        # delegate's view of the vault.
        add(vault / ".scout-state" / "delegate-mirrors.json", DELEGATE_STATE, tree=False)
        add(vault / ".scout-state" / "delegate-scan", DELEGATE_STATE, tree=True)
        engine = _precommit_engine(vault, hooks)
        if engine is not None:
            add(engine, ENGINE, tree=True)
    add(home / ".gitconfig", USER_GIT_CONFIG, tree=False)
    if env.get("GIT_CONFIG_GLOBAL"):
        add(env["GIT_CONFIG_GLOBAL"], USER_GIT_CONFIG, tree=False)
    add(Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "git", USER_GIT_CONFIG, tree=True)
    claude_dirs = [home / ".claude"]
    if env.get("CLAUDE_CONFIG_DIR"):
        claude_dirs.append(Path(env["CLAUDE_CONFIG_DIR"]))
    for base in claude_dirs:
        for name in (*CLAUDE_SETTINGS_FILES, ".claude.json"):
            add(base / name, AGENT_SETTINGS, tree=False)
        add(base / "plugins", AGENT_SETTINGS, tree=True)
    add(home / ".claude.json", AGENT_SETTINGS, tree=False)
    for name in AGENT_DIRS:
        add(home / name, AGENT_SETTINGS, tree=True)
    for root in engine_roots(env):
        add(root, ENGINE, tree=root.is_dir() or root.suffix == "")
    for name in SHELL_STARTUP:
        add(home / name, STARTUP, tree=False)
    add(home / "Library" / "LaunchAgents", STARTUP, tree=True)
    for secrets in secrets_dirs(env):
        add(secrets, SECRETS, tree=True)
    add(ssh_dir(env), SSH_CONFIG, tree=True)
    if vault is not None:
        # The tick starts each slot's runner, and the trigger engine each trigger's, outside any run (#642 review).
        for name in SCHEDULE_FILES:
            add(vault / ".scout-state" / name, SCHEDULE, tree=False)
    for path, tree in git_config_files(env, deadline=deadline, vault=vault):
        add(path, USER_GIT_CONFIG, tree=tree)
    for path in runner_programs(env, home):
        add(path, PROGRAMS, tree=True)
    return roots


# The vault files that choose what the tick and the trigger engine start (scout.schedule, scout.triggers.config).
SCHEDULE_FILES = ("schedule.yaml", "triggers.yaml")
_GIT_TIMEOUT_S = 3


def _git_binaries(env: Mapping[str, str]) -> list[str]:
    """Every distinct ``git`` on PATH (Homebrew's and Apple's read different system configs), plus /usr/bin/git."""
    seen: dict[str, str] = {}
    for d in [*str(env.get("PATH", "")).split(os.pathsep), "/usr/bin"]:
        cand = os.path.join(d, "git") if d else ""
        if cand and os.path.isabs(cand) and os.access(cand, os.X_OK):
            seen.setdefault(os.path.realpath(cand), cand)
    return list(seen.values())


_ORIGIN = re.compile(r"^file:(.+?)\t")
_MISSING = re.compile(r"unable to read config file '([^']+)'")


def ssh_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home()) / ".ssh"


def git_config_files(
    env: Mapping[str, str], *, deadline: float | None = None, vault: Path | None = None
) -> list[tuple[str, bool]]:
    """(path, tree) for git config a run's git reads besides the vault's and ~/.gitconfig: each git's system
    config (``git config --system --show-origin``, named even when the file doesn't exist yet), ``$GIT_CONFIG_SYSTEM``,
    and every ``include.path``/``includeIf.*.path`` the global and system configs name. Review of #642.

    With ``vault``, each git is asked again from inside it, so an ``includeIf "gitdir:…"`` that matches only the
    vault names its file too."""
    out: list[tuple[str, bool]] = []
    if env.get("GIT_CONFIG_SYSTEM"):
        out.append((env["GIT_CONFIG_SYSTEM"], False))
    run_env = {k: v for k, v in env.items() if k not in ("GIT_CONFIG_NOSYSTEM", "GIT_DIR", "GIT_CONFIG_COUNT")}
    places: list[Path | None] = [None, *([vault] if vault is not None and vault.is_dir() else [])]
    for git in _git_binaries(env):
        for cwd in places:
            for scope in ("--system", "--global"):
                timeout = _remaining(deadline, _GIT_TIMEOUT_S)
                if timeout <= 0:
                    return out  # out of time (a hook): the OS sandbox's profile still lists every git's config
                try:
                    proc = subprocess.run(
                        [git, "config", scope, "--show-origin", "--includes", "--list"],
                        capture_output=True, text=True, timeout=timeout, check=False,
                        stdin=subprocess.DEVNULL, env=run_env, cwd=cwd,
                    )  # fmt: skip
                except (OSError, subprocess.SubprocessError):
                    continue
                files = {m[1] for line in proc.stdout.splitlines() if (m := _ORIGIN.match(line))}
                files |= set(_MISSING.findall(proc.stderr))
                for f in sorted(files):
                    if f and f != os.devnull and (f, False) not in out:
                        out.append((f, False))
    return out


def runner_programs(env: Mapping[str, str], home: Path) -> list[str]:
    """Trees the runners and the gate probe run programs from, outside the sandbox, that the user can write: the
    claude install the runner's claude resolves to (with its side-by-side versions), ``~/.local/bin``, and a
    user-writable Homebrew prefix's bin/sbin/etc/lib (its python site-packages included)/opt/Cellar. Review of #642.
    The PATH folders themselves are :func:`path_dirs`."""
    out: list[str] = []

    def keep(path: str) -> None:
        real = os.path.realpath(path)
        if real not in out and real not in ("/", os.path.realpath(home)):
            out.append(real)

    # ~/.local/bin, where the native installer links `claude` (and pipx/uv put tools), whether or not it is on
    # this PATH: the scheduler's PATH may differ from this one.
    local_bin = home / ".local" / "bin"
    if local_bin.is_dir():
        keep(str(local_bin))
    claude = env.get("SCOUT_CLAUDE_BIN") or shutil.which("claude", path=env.get("PATH")) or ""
    if claude and os.path.isabs(claude):
        real = os.path.realpath(claude)
        holder = os.path.dirname(real)
        # ~/.local/share/claude/versions/<v>: the native installer keeps every version side by side.
        keep(os.path.dirname(holder) if os.path.basename(holder) == "versions" else holder)
    for prefix in ("/opt/homebrew", "/usr/local", "/home/linuxbrew/.linuxbrew"):
        if os.path.isdir(prefix) and os.access(prefix, os.W_OK):
            for sub in ("bin", "sbin", "etc", "lib", "opt", "Cellar", "Caskroom", "Library"):
                if os.path.isdir(os.path.join(prefix, sub)):
                    keep(os.path.join(prefix, sub))
    return out


def path_dirs(env: Mapping[str, str]) -> list[str]:
    """Every user-writable folder on PATH (``~/.local/bin``, Homebrew's bin …): a new ``date`` or ``git`` there is
    what the next runner starts. Only the folder's own entries count, not what's below them (a tool's store under
    ``~/Library/pnpm`` stays writable), which the OS sandbox can say and the hook's tree list can't, so this is
    the sandbox's alone (scout.sandbox.profile)."""
    out: list[str] = []
    for d in str(env.get("PATH", "")).split(os.pathsep):
        if d and os.path.isabs(d) and os.path.isdir(d) and os.access(d, os.W_OK) and not _transient(d):
            real = os.path.realpath(d)
            if real not in out:
                out.append(real)
    return out


def _transient(d: str) -> bool:
    """A PATH entry under a temp dir (a test's or a shim's): not something a later run would start."""
    real = os.path.realpath(d)
    return any(real.startswith(os.path.realpath(t) + "/") for t in {tempfile.gettempdir(), "/private/tmp", "/tmp"})


def _git_form(rest: str) -> str:
    if rest == "hooks" or rest.startswith("hooks/"):
        return GIT_HOOKS
    if rest in ("config", "config.worktree"):
        return GIT_CONFIG_FILE
    return GIT_DIR


def _match(keys: set[str], roots: list[Root]) -> str | None:
    best: Root | None = None
    for root in roots:
        if any(k == root.key or (root.tree and _under(k, root.key)) for k in keys):
            if best is None or len(root.key) > len(best.key):  # the most specific root names the form
                best = root
    if best is None:
        return None
    if best.form == GIT_DIR and best.tree:
        rest = next(k for k in sorted(keys) if _under(k, best.key))[len(best.key) :].lstrip("/")
        return _git_form(rest)
    return best.form


def protected_write(
    target: str | Path, vault: Path | None, env: Mapping[str, str] | None = None, *, file_tool: bool = False
) -> str | None:
    """The form name when writing ``target`` could change which checks run, else None.

    ``file_tool``: also refuse a regular file with more than one hard link (another name may be a protected file).
    """
    env = os.environ if env is None else env
    found = _match({canon(target, env), spelled(target, env)}, protected_roots(vault, env))
    if found is None and file_tool:
        try:
            st = os.stat(_expand(target, env))
        except OSError:
            return None
        if st.st_nlink > 1 and not os.path.isdir(_expand(target, env)):
            return HARD_LINK
    return found


def removes_protected(target: str | Path, vault: Path | None, env: Mapping[str, str] | None = None) -> str | None:
    """The form name when removing or moving ``target`` (a folder) takes a protected file or folder with it."""
    env = os.environ if env is None else env
    direct = protected_write(target, vault, env)
    if direct is not None:
        return direct
    keys = {canon(target, env), spelled(target, env)}
    for root in protected_roots(vault, env):
        if any(_under(root.key, k) and root.key != k for k in keys):
            return root.form
    return None


# The two secrets a run reads itself (`scoutctl notify telegram`, and the run prompts' Telegram steps), unless its
# send-only broker runs (scout.sandbox.broker sets SCOUT_RUN_BROKER): then the broker reads them, not the run.
TELEGRAM_SECRETS = ("telegram-bot-token", "telegram-chat-id")
BROKER_ENV = "SCOUT_RUN_BROKER"


def _secret_roots(env: Mapping[str, str]) -> set[str]:
    return {key for d in secrets_dirs(env) for key in (canon(d, env), spelled(d, env))}


def secret_read(target: str | Path, env: Mapping[str, str] | None = None) -> str | None:
    """:data:`SECRETS` when reading ``target`` reads one of Scout's secrets, else None.

    The Telegram token and chat id are a run's to read, unless the run's send-only broker runs (``SCOUT_RUN_BROKER``
    in the environment Claude Code hands its hooks, which a run can't change). Both spellings are judged, so a link
    named like one of them that points at another secret is still a secret.
    """
    env = os.environ if env is None else env
    roots = _secret_roots(env)
    readable = () if env.get(BROKER_ENV) else TELEGRAM_SECRETS
    for key in (canon(target, env), spelled(target, env)):
        for root in roots:
            if _under(key, root) and key[len(root) :].lstrip("/") not in readable:
                return SECRETS
    return None


def holds_secrets(target: str | Path, env: Mapping[str, str] | None = None) -> bool:
    """Whether a folder searched from ``target`` takes in one of Scout's secrets folders (``~`` does)."""
    env = os.environ if env is None else env
    keys = {canon(target, env), spelled(target, env)}
    return any(_under(root, key) for root in _secret_roots(env) for key in keys)


# Names only the protected files carry (no generic word like ``config`` or ``hooks``). A program that writes files
# and names one of these is denied even when the gate can't follow how it builds the path (os.path.join,
# Path / "…", string concatenation). scoutctl and git-safe-commit.sh are left out: runs call them all the time.
PROTECTED_TOKENS: tuple[str, ...] = (
    ".claude",
    "settings.local.json",
    ".claude.json",
    ".mcp.json",
    ".git",
    ".gitconfig",
    ".gitattributes",
    ".gitmodules",
    ".scoutctl-py-cache",
    "engine.json",
    "provider-consent.json",
    "run-limits.json",
    RUN_BROKERS_NAME,
    REVIEWED_WARNINGS_NAME,
    "hooks.json",
    "pyvenv.cfg",
    ".scout-secrets",
    "delegate-mirrors.json",
    "delegate-scan",
    "LaunchAgents",
    *AGENT_DIRS,
    *SHELL_STARTUP,
    *sorted(
        {r.rsplit("/", 1)[-1] for r in RUNNER_FILES} - {"scoutctl", "git-safe-commit.sh"},
    ),
)
_TOKEN_RE = re.compile(
    r"(?<![\w.-])(" + "|".join(re.escape(t) for t in sorted(PROTECTED_TOKENS, key=len, reverse=True)) + r")(?![\w.-])",
    re.IGNORECASE,
)


def protected_token(text: str) -> str | None:
    """The first :data:`PROTECTED_TOKENS` name in ``text`` (case ignored), ending a path component: ``.gitignore``
    is not ``.git``, nor a template ``claude-with-retry.sh.tmpl`` a runner script. Else None."""
    m = _TOKEN_RE.search(unicodedata.normalize("NFC", text))
    return m[1] if m else None

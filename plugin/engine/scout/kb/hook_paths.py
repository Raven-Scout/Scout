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
- ``startup files``: the shell's login and rc files and ``~/Library/LaunchAgents``.

A path is compared after ``~``, ``..`` and symlinks are resolved *and* as spelled (so a
``.git/hooks`` that is a symlink out of ``.git`` still counts), NFC-normalised and
case-folded: a case-insensitive disk takes ``.GIT/HOOKS`` for ``.git/hooks``, and on a
case-sensitive one denying that spelling costs nothing. :func:`removes_protected` says
whether removing or moving a path (``rm -rf .claude``) takes a protected file with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
TOO_MANY = "too many paths to check"
UNRESOLVED = "a path the gate can't resolve"

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


def git_hooks_dir(vault: Path) -> Path | None:
    """Where git runs this vault's hooks from: ``core.hooksPath`` if set, else the git dir's ``hooks``."""
    try:
        out = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            timeout=3,
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
    return roots


_ENV_KEYS = ("HOME", "GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT")
_ROOTS_CACHE: dict[tuple[str, ...], list[Root]] = {}


def protected_roots(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Root]:
    """The protected roots for this vault and environment, built once per process (a hook is one process).

    Building them runs ``git rev-parse`` and resolves every root; a command naming hundreds of paths checks
    each against this one list, so the gate stays fast however many paths it is handed (review of #637).
    """
    env = os.environ if env is None else env
    key = (str(vault), *(env.get(k, "") for k in _ENV_KEYS))
    if key not in _ROOTS_CACHE:
        _ROOTS_CACHE[key] = _build_roots(vault, env)
    return _ROOTS_CACHE[key]


def _build_roots(vault: Path | None, env: Mapping[str, str]) -> list[Root]:
    roots: list[Root] = []
    for spec in protected_specs(vault, env):
        for key in {canon(spec.path, env), spelled(spec.path, env)}:
            roots.append(Root(key, spec.form, spec.tree))
    return roots


def protected_specs(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Spec]:
    """Every protected path as written here (``~`` unexpanded, not resolved, not case-folded), with its form.

    :func:`protected_roots` is these, normalised for matching. The scheduled runs' OS write sandbox
    (:mod:`scout.sandbox.profile`, Phase 1c) is generated from the same list, so the two layers never drift.
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
        hooks = git_hooks_dir(vault)
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
    return roots


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


def unresolved_could_be_protected(path: str, vault: Path | None, env: Mapping[str, str] | None = None) -> str | None:
    """A written path with a ``$variable`` the gate couldn't resolve: deny when its literal parts could still
    name a protected file (``$d/settings.local.json``, ``$HOME/.gitconfig``), else None."""
    literal = [part for part in path.split("/") if part and "$" not in part and "`" not in part]
    if not literal:
        return None
    names: set[str] = set()
    for root in protected_roots(vault, env):
        names.add(root.key.rsplit("/", 1)[-1])
    return UNRESOLVED if any(_norm(part) in names for part in literal) else None

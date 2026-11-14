"""The files that decide which hooks run, which a scheduled run may not write (spec 2026-11-13 §8.5, Phase 1b).

Scout's checks live in two kinds of hook: git's (the pre-commit hook with the
privacy and size blocks, #453/#607) and the agent's (Claude Code's PreToolUse
hooks: kb-read-gate, outbound-gate, mcp-tool-gate). A run that could write the
files those read could turn them off for itself or for every later run: a
``.git/hooks/pre-commit`` emptied, a ``core.hooksPath`` in a git config, or
``"disableAllHooks": true`` in a ``.claude/settings.local.json``. #607 kept a
run's *shell* out of ``.git/hooks`` and ``.git/config``; this is the list the
write tools (Write, Edit, MultiEdit, NotebookEdit) are checked against, and the
shell check uses the same names for the agent's settings.

Protected:

- the vault's git dir, all of it (``.git/info/attributes`` and refs too; a run
  never writes it with a file tool), the real git dir and common dir when
  ``.git`` is a worktree's ``gitdir:`` file, and that file itself;
- the user's git config: ``~/.gitconfig``, ``$GIT_CONFIG_GLOBAL``,
  ``$XDG_CONFIG_HOME/git/`` (``~/.config/git/``);
- the agent's settings: ``settings.json`` / ``settings.local.json`` in the vault's
  ``.claude/``, ``~/.claude/`` and ``$CLAUDE_CONFIG_DIR``, the plugin store under
  them (where a plugin is turned off), and the config dirs other agent CLIs read
  hooks from (``.codex``, ``.gemini``, ``.agents``, ``.grok``, ``.cursor``) in the
  vault and the home folder.

A path is compared after ``~``, ``..`` and every symlink are resolved, and
case-folded: a case-insensitive disk takes ``.GIT/HOOKS`` for ``.git/hooks``,
and on a case-sensitive one denying that spelling costs nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

GIT_HOOKS = ".git/hooks"
GIT_CONFIG_FILE = ".git/config"
GIT_DIR = ".git"
USER_GIT_CONFIG = "git config"
AGENT_SETTINGS = "agent settings"

AGENT_DIRS = (".codex", ".gemini", ".agents", ".grok", ".cursor")
CLAUDE_SETTINGS_FILES = ("settings.json", "settings.local.json")


def canon(path: str | Path) -> str:
    """``path`` with ``~``, ``..`` and symlinks resolved, case-folded, as a posix string."""
    return os.path.realpath(os.path.expanduser(str(path))).casefold()


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


def _git_form(rest: str) -> str:
    if rest == "hooks" or rest.startswith("hooks/"):
        return GIT_HOOKS
    if rest in ("config", "config.worktree"):
        return GIT_CONFIG_FILE
    return GIT_DIR


def protected_write(target: str | Path, vault: Path | None, env: Mapping[str, str] | None = None) -> str | None:
    """The form name when writing ``target`` would change which hooks run, else None."""
    env = os.environ if env is None else env
    key = canon(target)
    home = Path(env.get("HOME") or Path.home())
    if vault is not None:
        if key == canon(vault / ".git"):
            return GIT_DIR  # a worktree's gitdir: file, or the git dir itself
        roots = sorted((canon(d) for d in git_dirs(vault)), key=len, reverse=True)
        for root in roots:  # longest first: a worktree's own dir sits inside the common dir
            if _under(key, root):
                return _git_form(key[len(root) :].lstrip("/"))
        if key in {canon(vault / ".claude" / name) for name in CLAUDE_SETTINGS_FILES}:
            return AGENT_SETTINGS
        if any(_under(key, canon(vault / d)) for d in AGENT_DIRS):
            return AGENT_SETTINGS
    git_files = [home / ".gitconfig"]
    if env.get("GIT_CONFIG_GLOBAL"):
        git_files.append(Path(env["GIT_CONFIG_GLOBAL"]))
    xdg = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "git"
    if key in {canon(f) for f in git_files} or _under(key, canon(xdg)):
        return USER_GIT_CONFIG
    claude_dirs = [home / ".claude"]
    if env.get("CLAUDE_CONFIG_DIR"):
        claude_dirs.append(Path(env["CLAUDE_CONFIG_DIR"]))
    for base in claude_dirs:
        if key in {canon(base / name) for name in CLAUDE_SETTINGS_FILES} or _under(key, canon(base / "plugins")):
            return AGENT_SETTINGS
    if any(_under(key, canon(home / d)) for d in AGENT_DIRS):
        return AGENT_SETTINGS
    return None

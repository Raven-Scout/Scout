"""Which vault the changes map counts for, and which folders are that vault.

Owner rows are keyed to one vault, so it is found from configuration, never from the working directory (spec §6.3):
``SCOUT_DATA_DIR``, else the vault in the engine pointer (``~/.local/state/scout/engine.json``), else ``~/Scout``.
Every git worktree of a vault carries its own tracked ``scout-config.yaml``, so the cwd would name the worktree.

The vault's folders are the vault plus the same folder in each linked worktree of its repository: a session in one
of them is in the vault, and its ``knowledge-base/`` and ``action-items/`` are the vault's. Never raises."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from scout import paths
from scout.scripts.engine_pointer import read_pointer

# The Stop hook has 10 s in all; one git call must never use much of it.
_GIT_TIMEOUT_S = 2.0
# A hook's environment must not point git at some other repository.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")


def _resolved(p: str | Path) -> Path:
    try:
        return paths.resolve_path(p)
    except (OSError, RuntimeError):
        return Path(p).expanduser().absolute()


def _git_env() -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}, "GIT_OPTIONAL_LOCKS": "0"}


def main_worktree(path: Path) -> Path:
    """``path``, or the same folder in its repository's main worktree when ``path`` is inside a linked worktree.

    A vault's own ``scout/scripts/scoutctl`` names its folder in ``SCOUT_DATA_DIR``, so a command run from a vault
    worktree would otherwise key the owner's decisions to the worktree and read its (absent, gitignored)
    ``.scout-logs/``. Never raises: anything git can't answer leaves ``path`` as given."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return path
    if proc.returncode != 0:
        return path
    lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) != 2:
        return path
    top, common = _resolved(lines[0]), _resolved(lines[1])
    if common.name != ".git":
        return path  # a bare repository's worktree: no main working tree to point at
    main = common.parent
    if main == top:
        return path  # the main worktree itself
    try:
        rel = _resolved(path).relative_to(top)
    except ValueError:
        return path
    return main / rel


def installed_vault(env: Mapping[str, str]) -> Path:
    """The installed vault: ``SCOUT_DATA_DIR``, else the engine pointer's ``vault``, else ``~/Scout``. Not the cwd.
    A folder in a linked git worktree stands for the same folder in the main worktree (:func:`main_worktree`).

    Raises ``ValueError("no home directory")`` when none of those can be found."""
    explicit = env.get("SCOUT_DATA_DIR")
    if explicit:
        return main_worktree(_resolved(explicit))
    try:
        home = Path(env.get("HOME") or Path.home())
    except RuntimeError:  # Path.home() when no home directory can be found
        raise ValueError("no home directory") from None
    pointer = read_pointer(home=home)
    if pointer is not None and pointer.vault:
        return main_worktree(_resolved(pointer.vault))
    return main_worktree(_resolved(home / paths.DEFAULT_DATA_DIR_NAME))


def _worktrees(root: Path) -> list[Path]:
    """Every working tree of the repository ``root`` is in (main first), or [] when git can't say."""
    env = _git_env()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if proc.returncode != 0:
        return []
    fields = proc.stdout.decode("utf-8", errors="replace").split("\0")
    return [_resolved(f[len("worktree ") :]) for f in fields if f.startswith("worktree ")]


def vault_roots(vault: Path) -> tuple[Path, ...]:
    """The vault, then the same folder in each other worktree of its repository. Just the vault when it isn't one."""
    root = _resolved(vault)
    trees = _worktrees(root)
    # The worktree holding the vault is the deepest one above it; the vault may be a subfolder of the repository.
    holders = [t for t in trees if t == root or t in root.parents]
    if not holders:
        return (root,)
    rel = root.relative_to(max(holders, key=lambda t: len(t.parts)))
    out = [root]
    for tree in trees:
        candidate = tree / rel
        if candidate not in out:
            out.append(candidate)
    return tuple(out)

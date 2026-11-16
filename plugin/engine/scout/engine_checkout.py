"""The git checkout the engine runs from, and the rule that scheduled runs never edit it.

A dev-managed engine (``managed_by: dev`` in ``~/.local/state/scout/engine.json``) is an
editable install: its working tree is the code launchd runs, so an uncommitted edit there
runs in the next scheduled run unreviewed, and a checkout that is off ``main`` or a release
tag can't be upgraded. Engine changes belong in a separate clone under
``~/.scout-worktrees/<name>`` (phases/core/git-setup.md). ``checkout_state`` feeds run
health's ``engine.*`` findings; ``refuse_live_engine_write`` guards commands that write into
an engine tree. Read-only: every git call runs with ``GIT_OPTIONAL_LOCKS=0`` so it never
takes the checkout's index lock.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_S = 10
RELEASE_TAG_PREFIX = "v"
WORKTREES_HINT = "~/.scout-worktrees/<name>"


@dataclass(frozen=True)
class CheckoutState:
    root: Path
    branch: str | None  # None on a detached HEAD
    release_tag: str | None  # the release tag HEAD sits on, when detached at one
    changes: tuple[str, ...]  # `git status --porcelain` paths: tracked edits and untracked files

    @property
    def off_release(self) -> bool:
        """Neither on ``main`` nor detached at a release tag: an upgrade can't move it."""
        return self.branch != "main" and self.release_tag is None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, env=env
    )


def _toplevel(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    top = _git(path, "rev-parse", "--show-toplevel")
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 else None


def checkout_state(path: Path) -> CheckoutState | None:
    """The state of the git checkout holding ``path``; None when it is in none or git can't run."""
    try:
        root = _toplevel(path)
        if root is None:
            return None
        status = _git(root, "status", "--porcelain", "--untracked-files=normal")
        branch = _git(root, "symbolic-ref", "-q", "--short", "HEAD")
        tag = None
        if branch.returncode != 0:
            exact = _git(root, "describe", "--tags", "--exact-match", "HEAD")
            name = exact.stdout.strip()
            tag = name if exact.returncode == 0 and name.startswith(RELEASE_TAG_PREFIX) else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    changes = tuple(line[3:] for line in status.stdout.splitlines() if line.strip())
    return CheckoutState(
        root=root,
        branch=branch.stdout.strip() if branch.returncode == 0 else None,
        release_tag=tag,
        changes=changes,
    )


def live_engine_root(*, home: Path) -> Path | None:
    """The engine root launchd runs, from the engine pointer (never a hard-coded path)."""
    from scout.scripts.engine_pointer import read_pointer

    pointer = read_pointer(home=home)
    return Path(pointer.engine_root) if pointer is not None else None


def _within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def refuse_live_engine_write(target: Path, *, home: Path) -> str | None:
    """Why a write into ``target`` must not happen, or None when it may.

    Refuses anything inside the live engine root or the git checkout that holds it.
    """
    live = live_engine_root(home=home)
    if live is None:
        return None
    try:
        checkout = _toplevel(live)
    except (OSError, subprocess.TimeoutExpired):
        checkout = None
    if not any(_within(target, root) for root in (live, checkout) if root is not None):
        return None
    return (
        f"refusing to write into the engine scheduled runs use ({live}): an edit there runs in the next "
        f"scheduled run, unreviewed. Clone origin/main into {WORKTREES_HINT}, re-run with "
        f"--plugin-root {WORKTREES_HINT}/plugin, then push a branch and open a PR."
    )

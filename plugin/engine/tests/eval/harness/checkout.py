"""The two arms of a comparison (spec 2026-10-12 §8).

A ``Checkout`` is the plugin tree an arm runs. Head is this repo. A base is a
detached ``git worktree`` of a ref, kept under the cache by commit and reused.

A base doesn't get a venv of its own. It runs on head's interpreter and
dependencies with ``PYTHONPATH`` set to its own ``plugin/engine``, so its
``scout`` package (and the plugin root derived from it) shadows head's. That
keeps a base cheap to make and needs no network. A base whose dependencies
differ from head's fails at import, visibly.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]


class CheckoutError(Exception):
    pass


@dataclass(frozen=True)
class Checkout:
    label: str
    root: Path  # the repo root of this arm (holds plugin/)
    commit: str | None
    pythonpath: Path | None  # None: head's own engine, already installed in this venv

    @property
    def plugin_root(self) -> Path:
        return self.root / "plugin"


def _git(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def head_checkout() -> Checkout:
    head = _git("rev-parse", "HEAD")
    return Checkout(label="head", root=REPO_ROOT, commit=head.stdout.strip() or None, pythonpath=None)


def base_checkout(ref: str, cache_root: Path) -> Checkout:
    resolved = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = resolved.stdout.strip()
    if resolved.returncode != 0 or not sha:
        raise CheckoutError(f"base ref {ref!r} does not name a commit in {REPO_ROOT}")
    root = cache_root / "checkouts" / sha
    if not (root / "plugin").is_dir():
        root.parent.mkdir(parents=True, exist_ok=True)
        added = _git("worktree", "add", "--detach", str(root), sha)
        if added.returncode != 0:
            raise CheckoutError(f"could not check out {ref} ({sha[:12]}) at {root}: {added.stderr.strip()}")
    status = _git("status", "--porcelain", cwd=root)
    if status.returncode != 0 or status.stdout.strip():
        raise CheckoutError(
            f"the cached base checkout at {root} is not clean; remove it "
            f"(git worktree remove --force {root}) and run again"
        )
    return Checkout(label="base", root=root, commit=sha, pythonpath=root / "plugin" / "engine")

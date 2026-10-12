"""The base-arm cache (spec 2026-10-12 §8).

Model-backed samples spend someone's subscription, so main's arm is reused
until something that shapes the run changes. The key is a content hash of
exactly that:

- the arm's ``plugin/phases``, ``plugin/templates`` and ``plugin/engine/scout``;
- the scenario directory;
- this checkout's harness, stubs and base overlay (the scorer and the fixture);
- ``HARNESS_VERSION``, the model alias and ``claude --version``.

A main commit that touches none of those reuses the cache. Nothing is shared
between machines: a cache is one maintainer's record.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable
from pathlib import Path

from .checkout import Checkout
from .runner import SampleResult, load_sample
from .scenario import Scenario

HARNESS_VERSION = "1"
EVAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "scout-eval"
_SKIP = {"__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
ARM_DIRS = ("phases", "templates", "engine/scout")
HARNESS_DIRS = ("harness", "stubs", "vault")


def content_hash(roots: Iterable[Path]) -> str:
    """sha256 over every file's path (relative to its root) and bytes, in a stable order."""
    digest = hashlib.sha256()
    for root in roots:
        files = sorted(p for p in root.rglob("*") if p.is_file() and not _SKIP & set(p.relative_to(root).parts))
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
        digest.update(b"\1")
    return digest.hexdigest()


def cache_key(checkout: Checkout, scenario: Scenario, *, model: str, cli_version: str) -> str:
    parts = [
        content_hash([checkout.plugin_root / d for d in ARM_DIRS]),
        content_hash([scenario.path]),
        content_hash([EVAL_ROOT / d for d in HARNESS_DIRS]),
        HARNESS_VERSION,
        model,
        cli_version,
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:20]


class BaselineCache:
    def __init__(self, root: Path = DEFAULT_CACHE_ROOT) -> None:
        self.root = root

    def _dir(self, key: str) -> Path:
        return self.root / "baselines" / key

    def samples(self, key: str) -> list[SampleResult]:
        d = self._dir(key)
        if not d.is_dir():
            return []
        return [load_sample(p) for p in sorted(d.iterdir()) if (p / "meta.json").is_file()]

    def add(self, key: str, result_dir: Path) -> Path:
        target = self._dir(key) / result_dir.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(result_dir, target, symlinks=True)
        return target

"""The self-update e2e harness's version stamp (scripts/e2e/self-update/e2e.py).

`--to release/vX.Y.Z --as-version X.Y.Z` names a target that already carries X.Y.Z: the stamp
must be a no-op there, not a failing empty `git commit`. Runs the real versioning.py against
a throwaway repo holding copies of the real manifests.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MANIFESTS = (
    ".claude-plugin/marketplace.json",
    "plugin/.claude-plugin/marketplace.json",
    "plugin/.claude-plugin/plugin.json",
    "plugin/engine/pyproject.toml",
    "plugin/engine/scout/__init__.py",
    "plugin/engine/scout/scripts/versioning.py",
    "apps/macos/Scout.xcodeproj/project.pbxproj",
)


def _harness():
    spec = importlib.util.spec_from_file_location("e2e_self_update", REPO / "scripts/e2e/self-update/e2e.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "checkout"
    for rel in MANIFESTS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, root / rel)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "e2e")
    _git(root, "config", "user.email", "e2e@example.com")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "manifests")
    version = json.loads((root / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    return root, version


def test_stamping_the_version_the_tree_already_carries_commits_nothing(tmp_path):
    root, current = _checkout(tmp_path)
    head = _git(root, "rev-parse", "HEAD")
    assert _harness().stamp_version(root, current) is False
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "status", "--porcelain") == ""


def test_stamping_a_new_version_commits_every_manifest(tmp_path):
    root, current = _checkout(tmp_path)
    major, minor, patch = (int(p) for p in current.split("-")[0].split(".")[:3])
    new = f"{major}.{minor}.{patch + 1}"
    assert _harness().stamp_version(root, new) is True
    assert _git(root, "log", "-1", "--format=%s") == f"e2e: stamp {new}"
    assert _git(root, "status", "--porcelain") == ""
    assert json.loads((root / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"] == new
    for marketplace in (".claude-plugin/marketplace.json", "plugin/.claude-plugin/marketplace.json"):
        assert json.loads((root / marketplace).read_text(encoding="utf-8"))["plugins"][0]["version"] == new

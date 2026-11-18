"""`.github/scripts/version-bump-only.sh`: a release PR (version bumps, CHANGELOG edits, deleted changelog
fragments) skips the heavy suites; anything else runs them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / ".github" / "scripts" / "version-bump-only.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _write(r, "plugin/.claude-plugin/plugin.json", '{\n  "name": "scout",\n  "version": "1.0.0"\n}\n')
    _write(r, "plugin/CHANGELOG.md", "# C\n\n## [Unreleased]\n\n### Added\n- a\n")
    _write(r, "plugin/changelog.d/README.md", "how to\n")
    _write(r, "plugin/changelog.d/fix-x.fixed.md", "- x\n")
    _write(r, "apps/macos/changelog.d/app-y.added.md", "- y\n")
    _write(r, "plugin/engine/scout/thing.py", "X = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _release_pr(repo: Path) -> None:
    """What release.sh prepare commits: the bump, the CHANGELOG section, the fragments deleted."""
    pj = repo / "plugin/.claude-plugin/plugin.json"
    pj.write_text(pj.read_text().replace("1.0.0", "1.1.0"), encoding="utf-8")
    _write(repo, "plugin/CHANGELOG.md", "# C\n\n## [Unreleased]\n\n## [1.1.0] - 2026-11-18\n\n### Added\n- a\n")
    _git(repo, "rm", "-q", "plugin/changelog.d/fix-x.fixed.md", "apps/macos/changelog.d/app-y.added.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release: v1.1.0")


def _decide(repo: Path, base: str, tmp_path: Path) -> str:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "GITHUB_EVENT_NAME": "pull_request",
        "BASE_SHA": base,
        "HEAD_SHA": _git(repo, "rev-parse", "HEAD"),
        "GITHUB_OUTPUT": str(out),
    }
    done = subprocess.run(["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out.read_text(encoding="utf-8").strip()


def test_a_release_pr_that_deletes_fragments_is_version_only(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    _release_pr(repo)
    assert _decide(repo, base, tmp_path) == "version_only=true"


def test_a_release_pr_that_also_edits_code_is_not(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    _release_pr(repo)
    _write(repo, "plugin/engine/scout/thing.py", "X = 2\n")
    _git(repo, "commit", "-q", "-am", "sneak")
    assert _decide(repo, base, tmp_path) == "version_only=false"


def test_a_file_beside_the_fragment_dirs_is_not_a_fragment(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    _release_pr(repo)
    _write(repo, "plugin/changelog.dx/evil.sh", "echo\n")  # a prefix match without the slash would let this by
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    assert _decide(repo, base, tmp_path) == "version_only=false"

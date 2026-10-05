"""Unit tests for the single-source-of-truth versioning module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout.scripts import versioning


def _fake_plugin(tmp_path: Path, version: str = "1.2.3") -> Path:
    """Build the monorepo layout: plugin subtree + repo-root marketplace.json.

    Mirrors the real tree — marketplace.json is the repo's entry point and
    lives ABOVE the plugin subtree, because `claude plugin marketplace add`
    reads it from the repo root.
    """
    repo = tmp_path
    plugin = repo / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        f'{{\n  "name": "scout",\n  "version": "{version}"\n}}\n', encoding="utf-8"
    )
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        f'{{\n  "name": "scout-plugin",\n  "plugins": [\n'
        f'    {{\n      "name": "scout",\n      "source": "./plugin",\n'
        f'      "version": "{version}"\n    }}\n  ]\n}}\n',
        encoding="utf-8",
    )
    (plugin / "engine").mkdir()
    (plugin / "engine" / "pyproject.toml").write_text(
        f'[project]\nname = "scout-engine"\nversion = "{version}"\n', encoding="utf-8"
    )
    (plugin / "engine" / "scout").mkdir()
    (plugin / "engine" / "scout" / "__init__.py").write_text(
        f'"""scout."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    pbx = repo / "apps" / "macos" / "Scout.xcodeproj"
    pbx.mkdir(parents=True)
    (pbx / "project.pbxproj").write_text(
        "\n".join(
            f"\t\t\t\tMARKETING_VERSION = {version};" if i % 2 == 0 else "\t\t\t\tPRODUCT_NAME = Scout;"
            for i in range(8)
        )
        + "\n",
        encoding="utf-8",
    )
    for rel in ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md"):
        (repo / rel).write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n- a thing\n", encoding="utf-8")
    return plugin


def test_read_versions_reads_marketplace_from_the_repo_root(tmp_path):
    """marketplace.json sits above the plugin subtree; the other three inside it."""
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(plugin_root)
    assert versions == {
        "plugin.json": "1.2.3",
        "marketplace.json": "1.2.3",
        "pyproject.toml": "1.2.3",
        "__init__.py": "1.2.3",
        "MARKETING_VERSION": "1.2.3",
    }


def test_set_version_writes_the_repo_root_marketplace(tmp_path):
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(plugin_root, version="1.3.0")
    marketplace = (tmp_path / ".claude-plugin" / "marketplace.json").read_text()
    assert '"version": "1.3.0"' in marketplace
    assert versioning.assert_in_sync(plugin_root) == "1.3.0"


def test_read_versions_accepts_an_explicit_repo_root(tmp_path):
    """repo_root can be pointed elsewhere than root.parent — e.g. a worktree
    whose plugin subtree and repo root are checked out separately. Relocating
    the repo-root files (marketplace.json, the app project file) outside
    root.parent proves repo_root is actually consulted (default resolution
    now fails) rather than silently ignored."""
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    other_repo = tmp_path.parent / f"{tmp_path.name}-other-repo"
    (other_repo / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "marketplace.json").rename(other_repo / ".claude-plugin" / "marketplace.json")
    (other_repo / "apps" / "macos").mkdir(parents=True)
    (tmp_path / "apps" / "macos" / "Scout.xcodeproj").rename(other_repo / "apps" / "macos" / "Scout.xcodeproj")

    with pytest.raises(FileNotFoundError):
        versioning.read_versions(plugin_root)  # default repo_root=None -> root.parent, now missing

    versions = versioning.read_versions(plugin_root, repo_root=other_repo)
    assert versions["marketplace.json"] == "1.2.3"
    assert versions["MARKETING_VERSION"] == "1.2.3"


def test_set_version_accepts_an_explicit_repo_root(tmp_path):
    """Mirrors test_read_versions_accepts_an_explicit_repo_root. set_version
    has its own, independent `repo = root.parent if repo_root is None else
    repo_root` ternary — a copy/paste slip there (e.g. `repo_root.parent`)
    would still pass every other test, since they all pass repo_root=None.
    Relocating marketplace.json outside root.parent means the write only
    lands if repo_root is genuinely consulted."""
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    other_repo = tmp_path.parent / f"{tmp_path.name}-other-repo"
    (other_repo / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "marketplace.json").rename(other_repo / ".claude-plugin" / "marketplace.json")
    (other_repo / "apps" / "macos").mkdir(parents=True)
    (tmp_path / "apps" / "macos" / "Scout.xcodeproj").rename(other_repo / "apps" / "macos" / "Scout.xcodeproj")

    versioning.set_version(plugin_root, version="1.3.0", repo_root=other_repo)

    marketplace = (other_repo / ".claude-plugin" / "marketplace.json").read_text()
    assert '"version": "1.3.0"' in marketplace
    assert versioning.read_versions(plugin_root, repo_root=other_repo) == {
        "plugin.json": "1.3.0",
        "marketplace.json": "1.3.0",
        "pyproject.toml": "1.3.0",
        "__init__.py": "1.3.0",
        "MARKETING_VERSION": "1.3.0",
    }


def test_read_versions_returns_all_five(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(root)
    assert set(versions.values()) == {"1.2.3"}
    assert len(versions) == 5


def test_assert_in_sync_passes_when_equal(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.assert_in_sync(root)  # must not raise


def test_assert_in_sync_raises_on_drift(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    mk = root.parent / ".claude-plugin" / "marketplace.json"
    mk.write_text(mk.read_text().replace("1.2.3", "1.2.2"))
    with pytest.raises(ValueError, match="version drift"):
        versioning.assert_in_sync(root)


def test_bump_levels():
    assert versioning.bump("1.2.3", "patch") == "1.2.4"
    assert versioning.bump("1.2.3", "minor") == "1.3.0"
    assert versioning.bump("1.2.3", "major") == "2.0.0"
    assert versioning.bump("1.2.3", "9.9.9") == "9.9.9"  # explicit passthrough


def test_bump_invalid_level_raises():
    with pytest.raises(ValueError):
        versioning.bump("1.2.3", "beta")


def test_set_version_writes_all_four_and_preserves_format(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(root, "1.3.0")
    assert set(versioning.read_versions(root).values()) == {"1.3.0"}
    plugin_text = (root / ".claude-plugin" / "plugin.json").read_text()
    json.loads(plugin_text)
    # indentation preserved (2-space indent json.dumps), not reserialized
    assert '  "version": "1.3.0"' in plugin_text


def test_promote_changelog_happy_path(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [Unreleased]\n\n- Added a thing\n- Fixed a bug\n")
    versioning.promote_changelog(tmp_path, version="1.3.0", date="2026-06-02")
    text = path.read_text()
    assert "## [Unreleased]" in text
    assert "## [1.3.0] - 2026-06-02" in text
    # the entries are preserved under the new dated section
    assert "- Added a thing" in text
    assert "- Fixed a bug" in text
    # fresh Unreleased sits above the dated section
    assert text.index("## [Unreleased]") < text.index("## [1.3.0] - 2026-06-02")


def test_promote_changelog_missing_marker_raises(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nno unreleased section here\n")
    with pytest.raises(ValueError):
        versioning.promote_changelog(tmp_path, version="1.3.0", date="2026-06-02")


def test_marketing_version_is_read_and_must_agree(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    assert versioning.read_versions(tmp_path / "plugin", tmp_path)["MARKETING_VERSION"] == "1.2.3"
    pbx = tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj"
    pbx.write_text(pbx.read_text().replace("1.2.3;", "9.9.9;", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="MARKETING_VERSION differs"):
        versioning.read_versions(tmp_path / "plugin", tmp_path)


def test_set_version_rewrites_every_marketing_version(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(tmp_path / "plugin", "1.3.0", tmp_path)
    text = (tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj").read_text()
    assert text.count("MARKETING_VERSION = 1.3.0;") == 4 and "1.2.3" not in text
    assert versioning.assert_in_sync(tmp_path / "plugin", tmp_path) == "1.3.0"


def test_drift_between_app_and_plugin_fails_check(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    pbx = tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj"
    pbx.write_text(pbx.read_text().replace("1.2.3", "1.2.4"), encoding="utf-8")
    with pytest.raises(ValueError, match="version drift"):
        versioning.assert_in_sync(tmp_path / "plugin", tmp_path)


def test_promote_changelogs_promotes_both(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    versioning.promote_changelogs(tmp_path, version="1.3.0", date="2026-10-05")
    for rel in ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md"):
        text = (tmp_path / rel).read_text()
        assert text.index("## [Unreleased]") < text.index("## [1.3.0] - 2026-10-05") < text.index("- a thing")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo_with_tags(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for msg, tag in [
        ("chore: one", "app/v0.14.0"),
        ("fix: two", "plugin/v0.14.0"),
        ("chore: three", "v0.15.1-rc.1"),
        ("feat: four", None),
    ]:
        _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
        if tag:
            _git(repo, "tag", tag)
    return repo


def test_previous_release_picks_highest_then_newest_and_ignores_rc(tmp_path):
    repo = _repo_with_tags(tmp_path)
    tag, sha = versioning.previous_release(repo)
    assert tag == "plugin/v0.14.0"  # same version as app/v0.14.0, but the later commit
    assert sha == _git(repo, "rev-list", "-n", "1", "plugin/v0.14.0")


def test_previous_release_ignores_rc_tags(tmp_path):
    repo = _repo_with_tags(tmp_path)
    assert versioning.previous_release(repo)[0] != "v0.15.1-rc.1"


def test_previous_release_excludes_and_respects_ref(tmp_path):
    repo = _repo_with_tags(tmp_path)
    assert versioning.previous_release(repo, exclude="plugin/v0.14.0")[0] == "app/v0.14.0"
    assert versioning.previous_release(repo, ref="app/v0.14.0", exclude="app/v0.14.0") is None


def test_recommend_level(tmp_path):
    repo = _repo_with_tags(tmp_path)
    since = _git(repo, "rev-list", "-n", "1", "plugin/v0.14.0")
    assert versioning.recommend_level(repo, since) == "minor"  # "feat: four" landed after it
    assert versioning.recommend_level(repo, since, ref="v0.15.1-rc.1") == "patch"

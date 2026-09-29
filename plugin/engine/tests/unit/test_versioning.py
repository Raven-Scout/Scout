"""Unit tests for the single-source-of-truth versioning module."""

from __future__ import annotations

import json
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
    marketplace.json outside root.parent proves repo_root is actually
    consulted (default resolution now fails) rather than silently ignored."""
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    other_repo = tmp_path.parent / f"{tmp_path.name}-other-repo"
    (other_repo / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "marketplace.json").rename(other_repo / ".claude-plugin" / "marketplace.json")

    with pytest.raises(FileNotFoundError):
        versioning.read_versions(plugin_root)  # default repo_root=None -> root.parent, now missing

    versions = versioning.read_versions(plugin_root, repo_root=other_repo)
    assert versions["marketplace.json"] == "1.2.3"


def test_read_versions_returns_all_four(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(root)
    assert set(versions.values()) == {"1.2.3"}
    assert len(versions) == 4


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

"""Unit tests for engine/scout/scripts/bootstrap.py — migrate-legacy + upgrade pre-flight."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import (
    _INSTALL_ONLY_TEMPLATES,
    BootstrapConfig,
    _is_legacy_vault,
    migrate_legacy,
    upgrade,
)


def _config(vault: Path, *, plugin_root: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
    )


def _populate_legacy_vault(vault: Path) -> None:
    """Plan-5-era vault: .scout-state/ exists; no scout-config.yaml; runners + cat-4 present."""
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state").mkdir()
    (vault / "knowledge-base").mkdir()
    (vault / "action-items").mkdir()
    (vault / "scripts").mkdir()
    (vault / "hooks").mkdir()
    (vault / ".scout-logs").mkdir()
    # Heavy vault edits — months of dreaming proposals.
    (vault / "SKILL.md").write_text("# SKILL\n\nVault-customized content. " * 200)
    (vault / "DREAMING.md").write_text("# DREAMING\n\nVault-customized content. " * 100)
    (vault / "RESEARCH.md").write_text("# RESEARCH\n\nVault-customized content. " * 50)
    # Legacy runners with hand-edited content.
    (vault / "run-scout.sh").write_text('#!/bin/bash\n# legacy hand-edited runner\nSCOUT_DIR="..."\n')
    (vault / "run-dreaming.sh").write_text("#!/bin/bash\n# legacy\n")
    (vault / "run-research.sh").write_text("#!/bin/bash\n# legacy\n")


def test_is_legacy_vault_detects_correctly(tmp_path):
    """`.scout-state/` without `scout-config.yaml` → legacy."""
    _populate_legacy_vault(tmp_path)
    assert _is_legacy_vault(tmp_path) is True


def test_is_legacy_vault_false_when_config_exists(tmp_path):
    _populate_legacy_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("# present\n")
    assert _is_legacy_vault(tmp_path) is False


def test_is_legacy_vault_false_when_no_state_dir(tmp_path):
    assert _is_legacy_vault(tmp_path) is False


def test_upgrade_refuses_on_legacy_vault(tmp_path):
    """upgrade() refuses with actionable error on Plan-5-era vault."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(RuntimeError, match="legacy vault.*migrate-legacy"):
        upgrade(_config(tmp_path, plugin_root=plugin))


def test_migrate_legacy_refuses_a_legacy_vault_in_the_old_layout_and_changes_nothing(tmp_path):
    """Layout v2 (spec 2026-11-26 §7.1): this engine renders v2 only, and a Plan-5-era vault is v1 everywhere, so
    migrate-legacy refuses before writing anything. The way through is an 0.18.x release's migrate-legacy, then
    an upgrade with this one, which moves the vault. (The conversion itself was tested against those releases;
    the M3 guard it relied on is tested in test_upgrade_brain_sidecars.py and test_brain_merge.py.)"""
    _populate_legacy_vault(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(RuntimeError, match="legacy vault in the old layout"):
        migrate_legacy(_config(tmp_path, plugin_root=plugin))
    after = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert not (tmp_path / "scout-config.yaml").exists()


def test_the_cat2_seed_table_is_layout_v2(tmp_path):
    """What migrate-legacy and upgrade seed, at layout v2 paths."""
    seeded = {rel for rel, _ in _INSTALL_ONLY_TEMPLATES}
    assert "inbox/inbox.md" in seeded and "knowledge-base/meetings.md" in seeded
    assert "inbox.md" not in seeded and "meetings/meetings.md" not in seeded


def test_migrate_legacy_refuses_when_config_already_exists(tmp_path):
    """migrate_legacy on a non-legacy vault → FileExistsError."""
    _populate_legacy_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("# already migrated\n")
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(FileExistsError, match="not a legacy vault"):
        migrate_legacy(_config(tmp_path, plugin_root=plugin))


def test_migrate_legacy_refuses_when_no_state_dir(tmp_path):
    """migrate_legacy on empty directory → FileNotFoundError, suggests /scout-setup."""
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(FileNotFoundError, match="run /scout-setup"):
        migrate_legacy(_config(tmp_path, plugin_root=plugin))

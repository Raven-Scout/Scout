"""apply_custom_change: add/remove reaches live brain files through brain_merge, never a second merge path."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc
from scout.scripts import brain_merge
from scout.scripts.bootstrap import (
    BootstrapConfig,
    _assemble,
    apply_custom_change,
    config_from_vault,
    install,
    write_connector_config,
)

PLUGIN = Path(__file__).parent.parent.parent.parent
SUITE = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def _installed(tmp_path: Path) -> BootstrapConfig:
    vault = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=vault,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors={"slack"},
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    return config_from_vault(vault, plugin_root=PLUGIN, plugin_version="0.0.0", skip_jobs=True, skip_claude=True)


def _add_suite(before: BootstrapConfig):
    custom_after = cc.parse_file(
        {"schema_version": 1, "connectors": {"suite_mail": SUITE}},
        reserved=set(),
        presets=cc.load_presets(PLUGIN),
    ).connectors
    after = dataclasses.replace(before, enabled_connectors=before.enabled_connectors | {"suite_mail"})
    return apply_custom_change(before, after, custom_before={}, custom_after=custom_after), after, custom_after


def _snap(cfg: BootstrapConfig) -> Path:
    return cfg.vault / ".scout-state" / "last-assembled"


def test_config_from_vault_reads_what_install_wrote(tmp_path: Path):
    cfg = _installed(tmp_path)
    assert cfg.user_name == "Alex"
    assert cfg.enabled_connectors == {"slack"}
    assert cfg.skip_jobs is True


def test_config_from_vault_rejects_a_non_mapping_top_level(tmp_path: Path):
    """A scout-config.yaml that parses as valid YAML but isn't a mapping (e.g. a
    bare list) must raise ValueError, not AttributeError from .get() on a list."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        config_from_vault(vault, plugin_root=PLUGIN, plugin_version="0.0.0")


def test_add_on_clean_vault_fast_forwards_live_snapshot_and_provenance(tmp_path: Path):
    before = _installed(tmp_path)
    result, after, custom_after = _add_suite(before)
    assert result.status == "applied"
    assert result.updated == ["SKILL.md"]
    new = _assemble(after, "SKILL", custom=custom_after)
    assert (before.vault / "SKILL.md").read_text() == new
    assert (_snap(before) / "SKILL.md").read_text() == new
    assert brain_merge.load_provenance(_snap(before))["SKILL.md"].sha256 == brain_merge.sha256_text(new)
    assert not (before.vault / "SKILL.md.proposed-merge").exists()


def test_add_keeps_the_users_own_edits(tmp_path: Path):
    before = _installed(tmp_path)
    live_path = before.vault / "SKILL.md"
    lines = live_path.read_text().splitlines()
    lines.insert(2, "Local rule: keep the briefing under one screen.")
    live_path.write_text("\n".join(lines) + "\n")
    result, _, _ = _add_suite(before)
    assert result.status == "applied"
    text = live_path.read_text()
    assert "Local rule: keep the briefing under one screen." in text
    assert "## Mail suite Inbound Scan" in text


def test_existing_vault_trailing_newline_does_not_spuriously_conflict(tmp_path: Path):
    """A real installed vault's SKILL.md/snapshot end without a trailing newline
    (``_assemble`` doesn't add one). A normal editor save of a hand edit adds one
    on the live side only — that alone must not stop the custom-connector change
    from merging in cleanly (regression for the git merge-file final-newline
    conflict; see three_way_merge._pad_trailing_newline)."""
    before = _installed(tmp_path)
    snapshot = (_snap(before) / "SKILL.md").read_text()
    live_path = before.vault / "SKILL.md"
    assert not snapshot.endswith("\n")
    assert live_path.read_text() == snapshot
    lines = snapshot.splitlines()
    lines.insert(2, "Local rule: keep the briefing under one screen.")
    live_path.write_text("\n".join(lines) + "\n")  # editor-style save: adds a trailing "\n"
    result, _, _ = _add_suite(before)
    assert result.status == "applied"
    text = live_path.read_text()
    assert "Local rule: keep the briefing under one screen." in text
    assert "## Mail suite Inbound Scan" in text


def test_plugin_drift_defers_and_writes_nothing(tmp_path: Path):
    before = _installed(tmp_path)
    snap = _snap(before) / "SKILL.md"
    snap.write_text(snap.read_text() + "\nplugin changed underneath\n")
    live_before = (before.vault / "SKILL.md").read_text()
    result, _, _ = _add_suite(before)
    assert result.status == "deferred"
    assert (before.vault / "SKILL.md").read_text() == live_before
    assert not (before.vault / "SKILL.md.proposed-merge").exists()


def test_overlapping_user_edit_goes_to_sidecar_with_recorded_proposal(tmp_path: Path):
    before = _installed(tmp_path)
    live_path = before.vault / "SKILL.md"
    live_path.write_text(live_path.read_text() + "\nMy own footer.\n")
    result, after, custom_after = _add_suite(before)
    assert result.status == "conflict"
    assert result.sidecars == ["SKILL.md.proposed-merge"]
    assert "My own footer." in live_path.read_text()
    assert "Mail suite" not in live_path.read_text()
    proposed = _snap(before) / brain_merge.PROPOSED_DIR / "SKILL.md"
    assert proposed.read_text() == _assemble(after, "SKILL", custom=custom_after)


def test_pending_sidecar_defers_and_touches_nothing(tmp_path: Path):
    before = _installed(tmp_path)
    sidecar = before.vault / "SKILL.md.proposed-merge"
    sidecar.write_text("pending review\n")
    live_before = (before.vault / "SKILL.md").read_text()
    result, _, _ = _add_suite(before)
    assert (result.status, result.waiting) == ("deferred", ["SKILL.md"])
    assert sidecar.read_text() == "pending review\n"
    assert (before.vault / "SKILL.md").read_text() == live_before


def test_live_file_with_conflict_markers_is_held(tmp_path: Path):
    before = _installed(tmp_path)
    live_path = before.vault / "SKILL.md"
    live_path.write_text(live_path.read_text() + "\n<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n")
    result, _, _ = _add_suite(before)
    assert (result.status, result.waiting) == ("deferred", ["SKILL.md"])
    assert "Mail suite" not in live_path.read_text()


def test_only_the_changed_brain_files_are_reconciled(tmp_path: Path):
    """A seeded DREAMING.md with local edits would get a PROPOSE sidecar from a full
    reconciliation; an inbound-only connector never changes DREAMING, so it must not."""
    before = _installed(tmp_path)
    dreaming = before.vault / "DREAMING.md"
    dreaming.write_text(dreaming.read_text() + "\nLocal dreaming note.\n")
    records = brain_merge.load_provenance(_snap(before))
    records["DREAMING.md"] = brain_merge.Provenance.seeded((_snap(before) / "DREAMING.md").read_text())
    (_snap(before) / brain_merge.PROVENANCE_FILE).write_text(brain_merge.dumps_provenance(records))
    result, _, _ = _add_suite(before)
    assert result.status == "applied"
    assert not (before.vault / "DREAMING.md.proposed-merge").exists()
    assert "Local dreaming note." in dreaming.read_text()


def test_no_change_is_unchanged(tmp_path: Path):
    before = _installed(tmp_path)
    assert apply_custom_change(before, before, custom_before={}, custom_after={}).status == "unchanged"


def test_write_connector_config_keeps_comments(tmp_path: Path):
    before = _installed(tmp_path)
    path = before.vault / "scout-config.yaml"
    path.write_text("# my budget note\n" + path.read_text())
    write_connector_config(before.vault, enabled={"slack", "suite_mail"}, inputs={"suite_mail__box": "team"})
    text = path.read_text()
    assert text.startswith("# my budget note\n")
    data = yaml.safe_load(text)
    assert data["connectors"]["enabled"] == ["slack", "suite_mail"]
    assert data["connectors"]["inputs"] == {"suite_mail__box": "team"}

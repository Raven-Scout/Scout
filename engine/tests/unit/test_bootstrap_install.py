"""Unit tests for engine/scout/scripts/bootstrap.py — install pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import (
    BootstrapConfig,
    InstallResult,
    install,
    resolve_claude_bin,
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
        skip_jobs=True,  # don't touch ~/Library/LaunchAgents in tests
        skip_claude=True,  # don't run a real Claude session
    )


def test_install_creates_directory_tree(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent  # repo root: ~/scout-plugin-plan-8
    vault = tmp_path / "Scout"
    result = install(_config(vault, plugin_root=plugin))
    assert isinstance(result, InstallResult)
    assert vault.exists()
    assert (vault / "knowledge-base").is_dir()
    assert (vault / "action-items").is_dir()
    assert (vault / ".scout-state").is_dir()
    assert (vault / "scripts").is_dir()
    assert (vault / "hooks").is_dir()


def test_install_creates_layered_kb_dirs_and_seeds(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    for rel in ("knowledge-base/topics", "knowledge-base/sources", "knowledge-base/session-log"):
        assert (vault / rel).is_dir(), rel
    assert "[[" in (vault / "knowledge-base/topics/topics.md").read_text()
    assert "[[" in (vault / "knowledge-base/sources/sources.md").read_text()
    log_index = (vault / "knowledge-base/session-log.md").read_text()
    assert "## Shards" in log_index
    assert "{{" not in log_index


def test_install_never_overwrites_layered_kb_seeds(tmp_path, monkeypatch):
    from scout.scripts import bootstrap

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base/session-log.md").write_text("# my log\n")
    monkeypatch.setattr(bootstrap, "_vault_exists", lambda _v: False)
    install(_config(vault, plugin_root=plugin))
    assert (vault / "knowledge-base/session-log.md").read_text() == "# my log\n"


def test_kb_index_template_points_at_session_log_not_a_table():
    plugin = Path(__file__).parent.parent.parent.parent
    text = (plugin / "templates/knowledge-base/knowledge-base.md.tmpl").read_text()
    assert "## Recent Sessions" not in text
    assert "[[session-log]]" in text
    assert "## Key Decisions Log" in text and "## Navigation" in text


def test_install_writes_scout_config(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config = (vault / "scout-config.yaml").read_text()
    assert "TestScout" in config
    assert "version_at_last_setup" in config
    assert "0.4.0" in config


def test_install_seeds_schedule_yaml(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    schedule = vault / ".scout-state" / "schedule.yaml"
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


def test_install_writes_assembled_files_and_snapshots(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        assert (vault / f"{name}.md").exists()
        assert (vault / ".scout-state" / "last-assembled" / f"{name}.md").exists()


def test_install_refuses_existing_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("# already here\n")
    with pytest.raises(FileExistsError, match="vault detected"):
        install(_config(vault, plugin_root=plugin))


def test_install_records_plugin_version(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config_text = (vault / "scout-config.yaml").read_text()
    import yaml

    cfg = yaml.safe_load(config_text)
    assert cfg["plugin"]["version_at_last_setup"] == "0.4.0"
    assert cfg["plugin"]["version_at_last_update"] == "0.4.0"


def test_install_persists_connector_inputs(tmp_path):
    """Install must persist connector inputs so the next upgrade's
    template renders use the user's real values instead of defaults.

    Regression: cli_bootstrap_install previously hardcoded
    connector_inputs={}, which meant fresh installs left scout-config.yaml
    without `connectors.inputs`. The next /scout-update would then regen
    cat-1b runners with placeholder CLAUDE_BIN / empty USER_SLACK_ID."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.enabled_connectors = {"slack", "github"}
    cfg.connector_inputs = {
        "user_slack_id": "U123ABC",
        "github_username": "alice",
        "github_repos": "org/repo-a,org/repo-b",
        "claude_bin": "/opt/homebrew/bin/claude",
        "max_budget": "12.50",
    }
    install(cfg)

    import yaml

    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert persisted["connectors"]["enabled"] == ["github", "slack"]  # sorted
    inputs = persisted["connectors"]["inputs"]
    assert inputs["user_slack_id"] == "U123ABC"
    assert inputs["claude_bin"] == "/opt/homebrew/bin/claude"
    assert inputs["max_budget"] == "12.50"

    # And the rendered runner picked them up rather than falling back to
    # the template defaults — this is the failure mode the friend's vault hit.
    runner_text = (vault / "run-scout.sh").read_text()
    assert "/opt/homebrew/bin/claude" in runner_text


def test_install_without_jobs_writes_no_engine_pointer(tmp_path):
    """The pointer is gated by skip_jobs like the plists and the shim
    (spec §4.2): a --no-jobs install leaves plists, shim
    AND pointer alone, so a scratch run can never repoint Scout.app. HOME is
    the hermetic per-test home from conftest, so Path.home() is safe to read."""
    from scout.scripts.engine_pointer import read_pointer

    plugin = Path(__file__).parent.parent.parent.parent
    cfg = _config(tmp_path / "Scout", plugin_root=plugin)
    cfg.managed_by = "scout-app"
    result = install(cfg)
    assert result.pointer is None
    assert read_pointer(home=Path.home()) is None


def test_pointer_stage_writes_this_vault_and_defaults_to_unknown_manager(tmp_path):
    """With jobs enabled the stage records this vault; BootstrapConfig's own
    managed_by default is the concrete "unknown"."""
    from scout.scripts.bootstrap import _stage_write_engine_pointer
    from scout.scripts.engine_pointer import read_pointer

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.skip_jobs = False
    written = _stage_write_engine_pointer(cfg)
    assert written == Path.home() / ".local" / "state" / "scout" / "engine.json"
    pointer = read_pointer(home=Path.home())
    assert pointer is not None
    assert pointer.vault == str(vault)
    assert pointer.managed_by == "unknown"


def test_stage_jobs_install_passes_vault_through(tmp_path, monkeypatch):
    """A vault anywhere other than ~/Scout must still get scheduled runs
    pointed at it — _stage_jobs_install forwards cfg.vault to both plist
    installers (E6)."""
    from scout.scripts.bootstrap import _stage_jobs_install

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Vaults" / "Work"
    cfg = _config(vault, plugin_root=plugin)
    cfg.platform = "macos"
    cfg.skip_jobs = False

    calls: dict[str, dict] = {}

    def fake_install_st(**kwargs):
        calls["st"] = kwargs
        return tmp_path / "com.scout.schedule-tick.plist"

    def fake_install_hb(**kwargs):
        calls["hb"] = kwargs
        return tmp_path / "com.scout.heartbeat.plist"

    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", fake_install_st)
    monkeypatch.setattr("scout.scripts.install_heartbeat_plist.install_plist", fake_install_hb)

    _stage_jobs_install(cfg)

    assert calls["st"]["vault"] == cfg.vault
    assert calls["hb"]["vault"] == cfg.vault


# ---------- #254: CLAUDE_BIN detection ----------


def _fake_claude(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_resolve_claude_bin_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert resolve_claude_bin("/opt/custom/claude", home=tmp_path) == "/opt/custom/claude"


def test_resolve_claude_bin_prefers_path_lookup(tmp_path, monkeypatch):
    on_path = _fake_claude(tmp_path / "bin" / "claude")
    _fake_claude(tmp_path / ".local" / "bin" / "claude")
    monkeypatch.setattr("shutil.which", lambda _name: str(on_path))
    assert resolve_claude_bin("", home=tmp_path) == str(on_path)


def test_resolve_claude_bin_finds_native_installer_location(tmp_path, monkeypatch):
    """The native installer's ~/.local/bin/claude — not the old /usr/local/bin
    default — when claude is not on the setup shell's PATH."""
    native = _fake_claude(tmp_path / ".local" / "bin" / "claude")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("os.access", lambda p, _mode: str(p) == str(native))
    assert resolve_claude_bin("", home=tmp_path) == str(native)


def test_resolve_claude_bin_nothing_found_returns_native_path(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("os.access", lambda _p, _mode: False)
    assert resolve_claude_bin("", home=tmp_path) == str(tmp_path / ".local" / "bin" / "claude")


def test_install_without_claude_bin_never_renders_usr_local_default(tmp_path, monkeypatch):
    import shutil

    native = _fake_claude(tmp_path / "home" / ".local" / "bin" / "claude")
    real_which = shutil.which

    def fake_which(name, *args, **kwargs):
        return str(native) if name == "claude" else real_which(name, *args, **kwargs)

    monkeypatch.setattr("shutil.which", fake_which)
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    assert f'CLAUDE_BIN="{native}"' in (vault / "run-scout.sh").read_text()


# ---------- #255: auto-update preference recorded by install ----------


@pytest.mark.parametrize("choice", [True, False])
def test_install_records_auto_update_choice(tmp_path, choice):
    import yaml

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.auto_update = choice
    install(cfg)
    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert persisted["auto_update"] == {"enabled": choice, "channel": "stable"}


def test_install_without_auto_update_choice_leaves_block_absent(tmp_path):
    import yaml

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert "auto_update" not in persisted

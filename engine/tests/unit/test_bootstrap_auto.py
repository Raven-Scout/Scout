"""Unit tests for engine/scout/scripts/bootstrap_auto.py (scout-plugin#26, spec E3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig
from scout.scripts.bootstrap_auto import AutoAction, detect, result_dict, run

PLUGIN = Path(__file__).resolve().parents[3]


def _cfg(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.10.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
        managed_by="scout-app",
    )


def test_detect_missing_dir_is_install(tmp_path):
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_empty_dir_is_install(tmp_path):
    (tmp_path / "Scout").mkdir()
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_legacy_vault_is_migrate(tmp_path):
    (tmp_path / "Scout" / ".scout-state").mkdir(parents=True)
    assert detect(tmp_path / "Scout").action is AutoAction.MIGRATE_LEGACY


def test_detect_configured_vault_is_upgrade(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    assert detect(tmp_path / "Scout").action is AutoAction.UPGRADE


def test_detect_pending_sidecar_is_refused(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    (tmp_path / "Scout" / "SKILL.md.proposed-merge").write_text("<<<<<<<\n")
    plan = detect(tmp_path / "Scout")
    assert plan.action is AutoAction.REFUSED and "sidecar" in plan.reason


def test_detect_nonempty_non_vault_is_refused(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "notes.txt").write_text("hi")
    plan = detect(tmp_path / "Scout")
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_run_installs_then_upgrades(tmp_path):
    vault = tmp_path / "Scout"
    first, code = run(_cfg(vault))
    assert first["action"] == "install" and code in (0, 1)
    assert first["doctor"]["severity"] in ("green", "yellow")
    assert first["pointer"] is None  # skip_jobs: no plists/shim, so no pointer either
    assert (vault / "scout-config.yaml").exists()
    second, _ = run(_cfg(vault))
    assert second["action"] == "upgrade"
    assert second["conflicts"] == []


def test_run_dry_run_mutates_nothing(tmp_path):
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault), dry_run=True)
    assert code == 0 and d["dry_run"] is True and d["action"] == "install" and d["doctor"] is None
    assert not vault.exists()


def test_run_refused_returns_exit_2(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    d, code = run(_cfg(vault))
    assert code == 2 and d["action"] == "refused" and d["error"]


def test_result_dict_has_the_contract_keys(tmp_path):
    d = result_dict(action=AutoAction.INSTALL, vault=tmp_path, plugin_version="0.10.0", result=None)
    assert set(d) == {
        "schema_version",
        "action",
        "reason",
        "dry_run",
        "vault",
        "plugin_version",
        "error",
        "doctor",
        "conflicts",
        "backups",
        "snapshots_recorded",
        "pointer",
    }


# --- Additional branch coverage (not in the brief; closes gaps the coverage
# gate's branch-coverage requirement flagged in `detect`/`run`) --------------


def test_detect_file_at_vault_path_is_refused(tmp_path):
    """`detect()`'s `not vault.is_dir()` branch: a plain file sitting where the
    vault directory should be."""
    vault_path = tmp_path / "Scout"
    vault_path.write_text("not a directory")
    plan = detect(vault_path)
    assert plan.action is AutoAction.REFUSED and "not a directory" in plan.reason


def _populate_legacy_vault(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state").mkdir()
    (vault / "knowledge-base").mkdir()
    (vault / "action-items").mkdir()
    (vault / "scripts").mkdir()
    (vault / "hooks").mkdir()
    (vault / ".scout-logs").mkdir()
    (vault / "SKILL.md").write_text("# SKILL\n")


def test_run_dispatches_migrate_legacy(tmp_path):
    """`run()`'s MIGRATE_LEGACY dispatch branch — exercised via `detect()`
    identifying a legacy (.scout-state/ without scout-config.yaml) vault."""
    vault = tmp_path / "Scout"
    _populate_legacy_vault(vault)
    d, code = run(_cfg(vault))
    assert d["action"] == "migrate-legacy"
    assert code in (0, 1, 2)


def test_run_dispatch_exception_becomes_refused(tmp_path, monkeypatch):
    """Defensive: if install()/upgrade()/migrate_legacy() raises despite
    detect() predicting a runnable state (e.g. a TOCTOU race between the two),
    run() must surface a refused/exit-2 payload rather than propagate."""
    import scout.scripts.bootstrap_auto as bootstrap_auto

    def _raise(cfg: BootstrapConfig) -> None:
        raise FileExistsError("raced with another process")

    monkeypatch.setattr(bootstrap_auto, "install", _raise)
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault))
    assert code == 2 and d["action"] == "refused" and "raced" in d["error"]


# --- Fix round 1 (reviewer findings 1 & 2) -----------------------------------


def test_detect_ds_store_only_dir_is_install(tmp_path):
    """Finder leaves .DS_Store behind just from opening/viewing ~/Scout —
    that alone must not count as vault content (finding 1)."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".DS_Store").write_bytes(b"\x00\x00")
    assert detect(vault).action is AutoAction.INSTALL


def test_detect_ds_store_plus_content_is_refused(tmp_path):
    """.DS_Store alongside real content is still a non-empty, non-vault
    directory — the Finder-metadata carve-out must not swallow real files."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".DS_Store").write_bytes(b"\x00\x00")
    (vault / "notes.txt").write_text("hi")
    plan = detect(vault)
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_detect_dotgit_only_dir_is_refused(tmp_path):
    """A dotfile that isn't Finder metadata (e.g. .git/) still counts as
    content — only the exact names .DS_Store/.localized are ignored."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".git").mkdir()
    plan = detect(vault)
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_run_lock_busy_becomes_refused(tmp_path, monkeypatch):
    """LockBusyError (lock contention on .scout-logs/.scout-session.lock,
    raised by acquire_lock_with_wait after its poll timeout) must produce a
    refused/exit-2 payload like the other caught exceptions, not an uncaught
    traceback (finding 2)."""
    import scout.scripts.bootstrap_auto as bootstrap_auto
    from scout.scripts.bootstrap_lock import LockBusyError

    lock_path = tmp_path / "Scout" / ".scout-logs" / ".scout-session.lock"

    def _raise(cfg: BootstrapConfig) -> None:
        raise LockBusyError(lock_path, 4242)

    monkeypatch.setattr(bootstrap_auto, "install", _raise)
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault))
    assert code == 2
    assert d["action"] == "refused"
    assert "4242" in d["error"]


# --- Final review: interrupted installs resume (Ruling 16) ------------------


def test_interrupted_install_resumes_as_install(tmp_path, monkeypatch):
    """install() creates .scout-state/ in its first stage but writes
    scout-config.yaml at the end. A failure in between used to make the
    retry look like a legacy vault → migrate-legacy, which never writes
    SKILL/DREAMING/RESEARCH or parser.py → doctor red forever."""
    import scout.scripts.bootstrap as bootstrap

    vault = tmp_path / "Scout"
    marker = vault / ".scout-state" / "install-incomplete"

    def disk_full(cfg: BootstrapConfig) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(bootstrap, "_stage_cat4_install", disk_full)
        with pytest.raises(OSError, match="No space"):
            bootstrap.install(_cfg(vault))
    assert marker.exists()
    assert (vault / ".scout-state").is_dir() and not (vault / "scout-config.yaml").exists()
    plan = detect(vault)
    assert plan.action is AutoAction.INSTALL
    assert plan.reason == "resuming an interrupted install"

    d, code = run(_cfg(vault))
    assert d["action"] == "install", d
    assert d["doctor"]["severity"] != "red", d["doctor"]
    assert code in (0, 1)
    assert not marker.exists()
    assert (vault / "SKILL.md").exists()
    assert (vault / "knowledge-base" / "ontology" / "parser.py").exists()


def test_upgrade_and_migrate_legacy_refuse_an_interrupted_install(tmp_path):
    """A marker-bearing vault is not a vault: only install may touch it."""
    from scout.scripts.bootstrap import migrate_legacy, upgrade

    vault = tmp_path / "Scout"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "install-incomplete").touch()
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        upgrade(_cfg(vault))
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        migrate_legacy(_cfg(vault))
    # Even if the crash landed after the version stamp, the marker still wins.
    (vault / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    assert detect(vault).action is AutoAction.INSTALL
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        upgrade(_cfg(vault))

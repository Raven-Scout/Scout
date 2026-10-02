"""An upgrade never silently loses a vault's edit to a plugin-owned file.

Each test installs a vault from a temporary copy of the plugin's templates, so
it can change the plugin's version of a file between install and upgrade, then
edits the vault the way a user (or a dreaming session) would and upgrades.

Design: docs/superpowers/specs/2026-09-30-upgrade-keeps-vault-edits-design.md
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scout.scripts import vault_drift as vd
from scout.scripts.bootstrap import BootstrapConfig, install, upgrade
from scout.scripts.bootstrap_doctor import Severity, run_doctor

REAL_PLUGIN = Path(__file__).resolve().parents[3]

HEARTBEAT = "scripts/heartbeat.sh"
HEARTBEAT_TMPL = "templates/scripts/heartbeat.sh.tmpl"
PARSER = "knowledge-base/ontology/parser.py"

VAULT_FIX = 'echo "vault-local fix" >> "$SCOUT_DIR/.scout-logs/heartbeat.log"\n'


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    shutil.copytree(REAL_PLUGIN / "templates", root / "templates")
    return root


def _config(vault: Path, plugin: Path, *, version: str = "0.12.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture
def vault(tmp_path: Path, plugin: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v, plugin, version="0.11.0"))
    return v


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _plugin_changes_heartbeat_top(plugin: Path) -> None:
    """A plugin change at the top of the file, far from the vault's edit at the end."""
    tmpl = plugin / HEARTBEAT_TMPL
    lines = tmpl.read_text(encoding="utf-8").splitlines(keepends=True)
    lines.insert(1, "# plugin change: new header line\n")
    tmpl.write_text("".join(lines), encoding="utf-8")


def _drift_files(vault: Path) -> list[str]:
    root = vault / ".scout-state" / "drift"
    return sorted(p.relative_to(vault).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _managed_bytes(vault: Path) -> dict[str, bytes]:
    """Every file the upgrade owns or records, for idempotency checks."""
    paths = [p for p in vault.rglob("*") if p.is_file() and ".scout-logs" not in p.parts]
    return {p.relative_to(vault).as_posix(): p.read_bytes() for p in paths if p.name != "scout-config.yaml"}


# ------------------------------------------------------------- edits kept ----


def test_an_unedited_vault_reports_nothing(vault: Path, plugin: Path) -> None:
    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == []
    assert result.backups == []
    assert _drift_files(vault) == []
    assert result.doctor.severity is Severity.GREEN, result.doctor
    assert result.doctor.notes == []


def test_a_hand_edit_the_plugin_did_not_touch_survives_and_is_reported(vault: Path, plugin: Path) -> None:
    _append(vault / HEARTBEAT, VAULT_FIX)

    result = upgrade(_config(vault, plugin))

    assert VAULT_FIX in (vault / HEARTBEAT).read_text(encoding="utf-8")
    assert result.vault_edits == [vd.VaultEdit(HEARTBEAT, "kept")]
    assert _drift_files(vault) == []
    # Carrying an edit is not a health problem: the doctor notes it, stays green.
    assert result.doctor.severity is Severity.GREEN, result.doctor
    assert any(HEARTBEAT in n for n in result.doctor.notes)


def test_a_hand_edit_merges_with_a_plugin_change_elsewhere(vault: Path, plugin: Path) -> None:
    _append(vault / HEARTBEAT, VAULT_FIX)
    _plugin_changes_heartbeat_top(plugin)

    result = upgrade(_config(vault, plugin))

    live = (vault / HEARTBEAT).read_text(encoding="utf-8")
    assert VAULT_FIX in live
    assert "# plugin change: new header line" in live
    assert result.vault_edits == [vd.VaultEdit(HEARTBEAT, "merged")]
    # The base advanced to the plugin's render: what remains is the vault's own edit.
    assert live == vd.snapshot_path(vault, HEARTBEAT).read_text(encoding="utf-8") + VAULT_FIX


def test_an_overlapping_change_keeps_the_vault_version_running(vault: Path, plugin: Path) -> None:
    before = (vault / HEARTBEAT).read_text(encoding="utf-8")
    _append(vault / HEARTBEAT, VAULT_FIX)
    _append(plugin / HEARTBEAT_TMPL, "# plugin change at the same spot\n")

    result = upgrade(_config(vault, plugin))

    assert (vault / HEARTBEAT).read_text(encoding="utf-8") == before + VAULT_FIX
    [edit] = result.vault_edits
    assert edit.outcome == "conflict" and edit.path == HEARTBEAT
    parked = vault / ".scout-state" / "drift" / f"{HEARTBEAT}.plugin"
    assert parked.read_text(encoding="utf-8").endswith("# plugin change at the same spot\n")
    # Loud: the vault runs an older version of a plugin-owned file.
    assert result.doctor.severity is Severity.YELLOW
    assert any(HEARTBEAT in w and "drift --resolve" in w for w in result.doctor.warnings)
    # ...but never a blocking sidecar.
    assert list(vault.rglob("*.proposed-merge")) == []


def test_a_conflict_never_blocks_the_next_upgrade(vault: Path, plugin: Path) -> None:
    _append(vault / HEARTBEAT, VAULT_FIX)
    _append(plugin / HEARTBEAT_TMPL, "# plugin change at the same spot\n")
    first = upgrade(_config(vault, plugin))

    second = upgrade(_config(vault, plugin, version="0.12.1"))  # must not raise

    assert second.vault_edits == first.vault_edits
    assert VAULT_FIX in (vault / HEARTBEAT).read_text(encoding="utf-8")


def test_resolving_a_conflict_by_hand_sticks(vault: Path, plugin: Path) -> None:
    _append(vault / HEARTBEAT, VAULT_FIX)
    _append(plugin / HEARTBEAT_TMPL, "# plugin change at the same spot\n")
    upgrade(_config(vault, plugin))
    parked = (vault / ".scout-state" / "drift" / f"{HEARTBEAT}.plugin").read_text(encoding="utf-8")
    (vault / HEARTBEAT).write_text(parked + VAULT_FIX, encoding="utf-8")  # merged by hand

    vd.resolve(vault, HEARTBEAT)
    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == [vd.VaultEdit(HEARTBEAT, "kept")]
    assert (vault / HEARTBEAT).read_text(encoding="utf-8") == parked + VAULT_FIX
    assert result.doctor.severity is Severity.GREEN


def test_repeated_upgrades_are_idempotent(vault: Path, plugin: Path) -> None:
    _append(vault / HEARTBEAT, VAULT_FIX)
    _append(vault / "run-scout.sh", "# vault-local runner tweak\n")
    _plugin_changes_heartbeat_top(plugin)
    upgrade(_config(vault, plugin))
    after_first = _managed_bytes(vault)

    second = upgrade(_config(vault, plugin))
    third = upgrade(_config(vault, plugin))

    assert _managed_bytes(vault) == after_first
    assert second.vault_edits == third.vault_edits
    assert {e.outcome for e in second.vault_edits} == {"kept"}


def test_a_runner_hand_edit_is_kept_in_place_not_backed_up(vault: Path, plugin: Path) -> None:
    _append(vault / "run-scout.sh", "# vault-local runner tweak\n")

    result = upgrade(_config(vault, plugin))

    assert "# vault-local runner tweak" in (vault / "run-scout.sh").read_text(encoding="utf-8")
    assert result.vault_edits == [vd.VaultEdit("run-scout.sh", "kept")]
    assert list(vault.glob("run-*.sh.bak.*")) == []


def test_a_vault_edit_to_parser_survives_a_plugin_change(vault: Path, plugin: Path) -> None:
    """parser.py moved from the blocking-sidecar policy to the managed-file one."""
    _append(vault / PARSER, "\n# VAULT_CUSTOM_MARKER = 1\n")
    tmpl = plugin / "templates" / PARSER
    tmpl.write_text("# plugin header change\n" + tmpl.read_text(encoding="utf-8"), encoding="utf-8")

    result = upgrade(_config(vault, plugin))

    live = (vault / PARSER).read_text(encoding="utf-8")
    assert "# VAULT_CUSTOM_MARKER = 1" in live and live.startswith("# plugin header change\n")
    assert result.vault_edits == [vd.VaultEdit(PARSER, "merged")]
    assert result.conflicts == []


# --------------------------------------------------------- first baseline ----


def _forget_snapshots(vault: Path) -> None:
    """Make the vault look like one last upgraded before last-rendered/ existed."""
    shutil.rmtree(vault / ".scout-state" / "last-rendered")


def test_first_baseline_of_an_unedited_vault_is_silent(vault: Path, plugin: Path) -> None:
    _forget_snapshots(vault)

    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == []
    assert _drift_files(vault) == []
    assert vd.snapshot_path(vault, HEARTBEAT).is_file()


def test_first_baseline_updates_an_older_release_render_silently(
    tmp_path: Path, plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vault last rendered by an older release: its file differs from the new
    render only because the plugin changed it. The shipped signature of the old
    template recognises it, so it updates without a report."""
    old_template = (plugin / HEARTBEAT_TMPL).read_text(encoding="utf-8")
    history = tmp_path / "render-history.json"
    history.write_text(
        json.dumps({"files": {HEARTBEAT: [vd.signature(old_template, rendered=True).to_json()]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vd, "RENDER_HISTORY", history)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin, version="0.10.0"))
    _forget_snapshots(vault)
    _plugin_changes_heartbeat_top(plugin)

    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == []
    assert "# plugin change: new header line" in (vault / HEARTBEAT).read_text(encoding="utf-8")
    assert _drift_files(vault) == []


def test_first_baseline_after_the_last_release_without_bases_is_silent(tmp_path: Path, plugin: Path) -> None:
    """The real case: a vault last upgraded by v0.11.0 (heartbeat.sh as that
    release shipped it, no last-rendered/) upgrades to this version."""
    current = (plugin / HEARTBEAT_TMPL).read_text(encoding="utf-8")
    old = Path(__file__).resolve().parents[1] / "fixtures" / "render-history" / "heartbeat.sh.v0.11.0.tmpl"
    (plugin / HEARTBEAT_TMPL).write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    vault = tmp_path / "Scout"
    install(_config(vault, plugin, version="0.11.0"))
    _forget_snapshots(vault)
    (plugin / HEARTBEAT_TMPL).write_text(current, encoding="utf-8")

    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == []
    assert (vault / HEARTBEAT).read_text(encoding="utf-8") == vd.snapshot_path(vault, HEARTBEAT).read_text(
        encoding="utf-8"
    )
    assert "session-lane-liveness.py" in (vault / HEARTBEAT).read_text(encoding="utf-8")


def test_first_baseline_parks_a_file_no_release_shipped(vault: Path, plugin: Path) -> None:
    _forget_snapshots(vault)
    _append(vault / HEARTBEAT, VAULT_FIX)
    edited = (vault / HEARTBEAT).read_text(encoding="utf-8")

    result = upgrade(_config(vault, plugin))

    parked = f".scout-state/drift/{HEARTBEAT}.vault"
    assert result.vault_edits == [vd.VaultEdit(HEARTBEAT, "replaced", (parked,))]
    assert result.backups == [parked]
    assert (vault / parked).read_text(encoding="utf-8") == edited
    assert VAULT_FIX not in (vault / HEARTBEAT).read_text(encoding="utf-8")
    assert result.doctor.severity is Severity.GREEN
    assert any(parked in n for n in result.doctor.notes)


def test_first_baseline_carries_over_parsers_legacy_snapshot(vault: Path, plugin: Path) -> None:
    rendered = vd.snapshot_path(vault, PARSER)
    legacy = vault / ".scout-state" / "last-assembled" / PARSER
    legacy.parent.mkdir(parents=True, exist_ok=True)
    rendered.replace(legacy)
    _append(vault / PARSER, "\n# VAULT_CUSTOM_MARKER = 1\n")

    result = upgrade(_config(vault, plugin))

    assert result.vault_edits == [vd.VaultEdit(PARSER, "kept")]
    assert rendered.is_file() and not legacy.exists()


def test_first_baseline_keeps_an_unknown_parser_running(vault: Path, plugin: Path) -> None:
    """parser.py is extended in the vault; with nothing to merge against, the
    vault's version stays live and the plugin's is parked — what the old
    sidecar did, minus blocking the next upgrade."""
    _forget_snapshots(vault)
    _append(vault / PARSER, "\n# LEGACY_VAULT_EDIT = 1\n")

    result = upgrade(_config(vault, plugin))

    assert "# LEGACY_VAULT_EDIT = 1" in (vault / PARSER).read_text(encoding="utf-8")
    [edit] = result.vault_edits
    assert (edit.path, edit.outcome) == (PARSER, "conflict")
    assert (vault / ".scout-state" / "drift" / f"{PARSER}.plugin").is_file()
    assert not (vault / f"{PARSER}.proposed-merge").exists()
    upgrade(_config(vault, plugin))  # not blocked


def test_snapshots_are_gitignored(vault: Path) -> None:
    assert ".scout-state/last-rendered/" in (vault / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_parked_copies_are_one_doctor_note_not_one_per_file(vault: Path, plugin: Path) -> None:
    """A vault carrying several hand fixes gets several parked copies on its
    first baseline; the doctor names them all in one note."""
    _forget_snapshots(vault)
    _append(vault / HEARTBEAT, VAULT_FIX)
    _append(vault / "run-scout.sh", "# vault-local runner tweak\n")

    result = upgrade(_config(vault, plugin))

    parked_notes = [n for n in result.doctor.notes if ".vault" in n]
    assert len(parked_notes) == 1
    assert f".scout-state/drift/{HEARTBEAT}.vault" in parked_notes[0]
    assert ".scout-state/drift/run-scout.sh.vault" in parked_notes[0]


# ---------------------------------------------------------- robustness ----


def test_a_managed_file_that_is_not_utf8_never_blocks_an_upgrade(vault: Path, plugin: Path) -> None:
    """A byte pasted from a Latin-1 editor used to raise UnicodeDecodeError on
    every upgrade, forever. The edit is kept byte-for-byte and the upgrade finishes."""
    raw = (vault / HEARTBEAT).read_bytes() + b"# caf\xe9 (vault-local)\n"
    (vault / HEARTBEAT).write_bytes(raw)

    result = upgrade(_config(vault, plugin, version="0.12.0"))

    assert (vault / HEARTBEAT).read_bytes() == raw
    assert result.vault_edits == [vd.VaultEdit(HEARTBEAT, "kept")]
    assert "0.12.0" in (vault / "scout-config.yaml").read_text(encoding="utf-8")


def test_a_file_the_upgrade_cannot_read_is_reported_and_the_rest_still_upgrades(
    vault: Path, plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_reconcile = vd.reconcile

    def flaky(vault_: Path, rel: str, new: str, **kw: object) -> vd.VaultEdit | None:
        if rel == HEARTBEAT:
            raise PermissionError(13, "Permission denied", str(vault_ / rel))
        return real_reconcile(vault_, rel, new, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(vd, "reconcile", flaky)
    _plugin_changes_heartbeat_top(plugin)
    (plugin / "templates/run-scout.sh.tmpl").write_text(
        (plugin / "templates/run-scout.sh.tmpl").read_text(encoding="utf-8") + "# plugin runner change\n",
        encoding="utf-8",
    )

    result = upgrade(_config(vault, plugin, version="0.12.0"))

    [edit] = result.vault_edits
    assert (edit.path, edit.outcome) == (HEARTBEAT, "error")
    assert "Permission denied" in edit.describe()
    assert "# plugin runner change" in (vault / "run-scout.sh").read_text(encoding="utf-8")
    assert "0.12.0" in (vault / "scout-config.yaml").read_text(encoding="utf-8")


def test_a_drift_check_that_fails_is_a_doctor_warning_not_a_crash(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doctor runs after every upgrade stage; a crash there would turn a
    finished upgrade into a failed one."""

    def broken_scan(_vault: Path) -> list[vd.DriftEntry]:
        raise PermissionError(13, "Permission denied", ".scout-state/drift")

    monkeypatch.setattr(vd, "scan", broken_scan)

    report = run_doctor(vault=vault, check_jobs=False)

    assert any("could not check vault drift" in w for w in report.warnings)

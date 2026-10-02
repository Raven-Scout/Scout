"""`scoutctl bootstrap drift`, and the vault-edit lines `bootstrap upgrade` and
`bootstrap doctor` print.

The vault is installed from the real plugin checkout (the CLI renders from it),
under a tmp dir; nothing here touches a real vault.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.scripts.bootstrap import BootstrapConfig, install

runner = CliRunner()
PLUGIN = Path(cli.__file__).resolve().parent.parent.parent
HEARTBEAT = "scripts/heartbeat.sh"
FIX = 'mkdir -p "$SCOUT_DATA_DIR/.scout-cache"  # vault-local fix\n'


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.11.0",
            enabled_connectors=set(),
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def test_drift_on_an_unedited_vault_says_so(vault: Path) -> None:
    result = runner.invoke(cli.app, ["bootstrap", "drift"])
    assert result.exit_code == 0, result.output
    assert "no vault drift" in result.stdout


def test_drift_lists_an_edited_file(vault: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "drift"])

    assert result.exit_code == 0, result.output
    assert "edited" in result.stdout and HEARTBEAT in result.stdout
    assert "+1 −0" in result.stdout


def test_drift_json_is_machine_readable(vault: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "drift", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1
    [entry] = data["files"]
    assert entry == {"path": HEARTBEAT, "status": "edited", "parked": [], "stale": False, "added": 1, "removed": 0}


def test_drift_diff_shows_the_edit_against_the_plugins_version(vault: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "drift", "--diff"])

    assert result.exit_code == 0, result.output
    assert f"+++ vault/{HEARTBEAT}" in result.stdout
    assert "+" + FIX.rstrip("\n") in result.stdout.splitlines()


def test_drift_patch_applies_to_a_plugin_checkout(vault: Path, tmp_path: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "drift", "--patch"])

    assert result.exit_code == 0, result.output
    assert "--- a/templates/scripts/heartbeat.sh.tmpl" in result.stdout
    checkout = tmp_path / "checkout"
    shutil.copytree(PLUGIN / "templates", checkout / "templates")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (tmp_path / "fix.patch").write_text(result.stdout, encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "apply", str(tmp_path / "fix.patch")], check=True)
    assert FIX in (checkout / "templates/scripts/heartbeat.sh.tmpl").read_text(encoding="utf-8")


def test_drift_resolve_dismisses_a_parked_copy(vault: Path) -> None:
    shutil.rmtree(vault / ".scout-state" / "last-rendered")
    _append(vault / HEARTBEAT, FIX)
    runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])
    assert (vault / ".scout-state/drift/scripts/heartbeat.sh.vault").exists()

    result = runner.invoke(cli.app, ["bootstrap", "drift", "--resolve", HEARTBEAT])

    assert result.exit_code == 0, result.output
    assert not (vault / ".scout-state/drift/scripts/heartbeat.sh.vault").exists()


def test_drift_resolve_with_nothing_parked_exits_two(vault: Path) -> None:
    result = runner.invoke(cli.app, ["bootstrap", "drift", "--resolve", HEARTBEAT])
    assert result.exit_code == 2
    assert "nothing to resolve" in result.output


def test_upgrade_prints_each_vault_edit(vault: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])

    assert f"vault edit kept: {HEARTBEAT}" in result.output


def test_doctor_prints_notes(vault: Path) -> None:
    _append(vault / HEARTBEAT, FIX)

    result = runner.invoke(cli.app, ["bootstrap", "doctor", "--no-jobs"])

    assert result.exit_code == 0, result.output
    assert any(line.startswith("note: ") and HEARTBEAT in line for line in result.stdout.splitlines())


def test_drift_resolve_takes_several_files(vault: Path) -> None:
    shutil.rmtree(vault / ".scout-state" / "last-rendered")
    _append(vault / HEARTBEAT, FIX)
    _append(vault / "run-scout.sh", "# vault-local runner tweak\n")
    runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])

    result = runner.invoke(cli.app, ["bootstrap", "drift", "--resolve", HEARTBEAT, "--resolve", "run-scout.sh"])

    assert result.exit_code == 0, result.output
    assert not (vault / ".scout-state" / "drift").exists()


def test_drift_resolve_drop_update_keeps_the_vault_version(vault: Path) -> None:
    # Make the plugin's current render look like it added its final line (the
    # exec) since the last upgrade, while the vault edited the same spot.
    lines = (vault / HEARTBEAT).read_text(encoding="utf-8").splitlines(keepends=True)
    base = "".join(lines[:-1])
    (vault / ".scout-state" / "last-rendered" / HEARTBEAT).write_text(base, encoding="utf-8")
    (vault / HEARTBEAT).write_text(base + FIX, encoding="utf-8")
    runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])
    assert (vault / ".scout-state/drift/scripts/heartbeat.sh.plugin").exists()

    refused = runner.invoke(cli.app, ["bootstrap", "drift", "--resolve", HEARTBEAT])
    dropped = runner.invoke(cli.app, ["bootstrap", "drift", "--resolve", HEARTBEAT, "--drop-update"])

    assert refused.exit_code == 2 and "--drop-update" in refused.output
    assert dropped.exit_code == 0, dropped.output
    assert not (vault / ".scout-state/drift/scripts/heartbeat.sh.plugin").exists()

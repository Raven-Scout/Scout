"""CLI tests for `scoutctl bootstrap auto` and the --json flags (spec E3)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()
IDENTITY = ["--user-name", "Alex", "--user-email", "alex@example.com"]
HEADLESS = [
    "--no-jobs",
    "--skip-claude",
    "--no-interactive",
    "--yes",
    "--json",
    "--platform",
    "macos",
    "--claude-bin",
    "/usr/local/bin/claude",
    "--managed-by",
    "scout-app",
]


def _vault(tmp_path: Path, monkeypatch) -> Path:
    v = tmp_path / "Scout"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_auto_installs_fresh_vault_and_emits_json(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "install"
    assert payload["vault"] == str(vault)
    assert payload["doctor"]["severity"] in ("green", "yellow")
    assert (vault / "scout-config.yaml").exists()


def test_auto_second_run_upgrades(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    assert json.loads(result.stdout)["action"] == "upgrade", result.stdout + result.stderr


def test_auto_dry_run_reports_plan_without_touching_disk(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY, "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True and payload["action"] == "install"
    assert not vault.exists()


def test_auto_non_interactive_without_identity_exits_2(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused" and "--user-name" in payload["error"]


def test_auto_refuses_non_vault_directory(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["action"] == "refused"


def test_doctor_json_shape(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(app, ["bootstrap", "doctor", "--no-jobs", "--json"])
    payload = json.loads(result.stdout)
    assert set(payload) == {"severity", "errors", "warnings"}


def test_install_json_matches_auto_contract(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "install", "--no-jobs", "--skip-claude", "--json", *IDENTITY])
    payload = json.loads(result.stdout)
    assert payload["action"] == "install" and "doctor" in payload and "pointer" in payload


# --- Additional branch coverage (controller decisions) ----------------------


def test_auto_interactive_prompts_for_identity_and_installs(tmp_path, monkeypatch):
    """--interactive prompts for the missing identity fields and, on
    confirmation, proceeds to install."""
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "auto",
            "--no-jobs",
            "--skip-claude",
            "--platform",
            "macos",
            "--claude-bin",
            "/usr/local/bin/claude",
            "--interactive",
        ],
        input="Alex\nalex@example.com\ny\n",
    )
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    assert (vault / "scout-config.yaml").exists()


def test_auto_interactive_confirmation_declined_creates_nothing(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "auto",
            "--no-jobs",
            "--skip-claude",
            "--platform",
            "macos",
            "--claude-bin",
            "/usr/local/bin/claude",
            "--interactive",
        ],
        input="Alex\nalex@example.com\nn\n",
    )
    assert result.exit_code == 1
    assert not vault.exists()


def test_auto_platform_auto_refuses_unsupported_os(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    result = runner.invoke(app, ["bootstrap", "auto", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused"
    assert "--platform" in payload["error"]


def test_auto_platform_auto_resolves_on_supported_os(tmp_path, monkeypatch):
    """`--platform auto` (the default) on a real, supported OS falls through
    to claude-bin resolution instead of refusing — the counterpart branch to
    test_auto_platform_auto_refuses_unsupported_os above."""
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", "--json", "--dry-run", *IDENTITY])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "install"


def test_auto_dry_run_text_mode_has_no_doctor_line(tmp_path, monkeypatch):
    """Text-mode (non-JSON) `_emit` on a dry-run payload: doctor is None, so
    the doctor block must be skipped rather than crashing on a None subscript."""
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "auto",
            "--no-jobs",
            "--skip-claude",
            "--no-interactive",
            "--yes",
            "--platform",
            "macos",
            "--claude-bin",
            "/usr/local/bin/claude",
            *IDENTITY,
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"installed: {vault}" in result.stdout
    assert "doctor:" not in result.stdout
    assert not vault.exists()


def test_auto_claude_bin_auto_resolves_via_which(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: "/opt/homebrew/bin/claude")
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "auto",
            "--no-jobs",
            "--skip-claude",
            "--no-interactive",
            "--yes",
            "--platform",
            "macos",
            *IDENTITY,
        ],
    )
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    config = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert config["connectors"]["inputs"]["claude_bin"] == "/opt/homebrew/bin/claude"


def test_auto_upgrade_ignores_identity_flags_note_on_stderr(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "auto",
            "--no-jobs",
            "--skip-claude",
            "--no-interactive",
            "--yes",
            "--platform",
            "macos",
            "--claude-bin",
            "/usr/local/bin/claude",
            *IDENTITY,
        ],
    )
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    assert "note: identity flags are ignored on upgrade" in result.stderr


def _populate_legacy_vault(vault: Path) -> None:
    """Plan-5-era vault: .scout-state/ exists; no scout-config.yaml (mirrors
    the fixture in tests/unit/test_bootstrap_migrate_legacy.py)."""
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state").mkdir()
    (vault / "knowledge-base").mkdir()
    (vault / "action-items").mkdir()
    (vault / "scripts").mkdir()
    (vault / "hooks").mkdir()
    (vault / ".scout-logs").mkdir()
    (vault / "SKILL.md").write_text("# SKILL\n\nVault-customized content.\n")
    (vault / "DREAMING.md").write_text("# DREAMING\n\nVault-customized content.\n")
    (vault / "RESEARCH.md").write_text("# RESEARCH\n\nVault-customized content.\n")
    (vault / "run-scout.sh").write_text('#!/bin/bash\n# legacy hand-edited runner\nSCOUT_DIR="..."\n')
    (vault / "run-dreaming.sh").write_text("#!/bin/bash\n# legacy\n")
    (vault / "run-research.sh").write_text("#!/bin/bash\n# legacy\n")


def test_migrate_legacy_json_on_legacy_vault(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    _populate_legacy_vault(vault)
    result = runner.invoke(app, ["bootstrap", "migrate-legacy", "--json", *IDENTITY])
    # Exit code is the doctor's 0/1/2 (this minimal fixture has no parser.py
    # cat-1 file, so red/2 is expected) — the JSON contract is what's under test.
    assert result.exit_code in (0, 1, 2), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "migrate-legacy"
    assert "SKILL.md" in payload["snapshots_recorded"]
    assert payload["pointer"] is not None


def test_upgrade_json_refuses_without_vault(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "upgrade", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused"
    assert "run /scout-setup" in payload["error"]

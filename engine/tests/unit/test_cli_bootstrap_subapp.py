"""CLI tests for `scoutctl bootstrap auto` and the --json flags (spec E3)."""

from __future__ import annotations

import json
import os
import plistlib
import re
from pathlib import Path

import pytest
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
    assert payload["action"] == "install" and "doctor" in payload
    assert payload["pointer"] is None  # --no-jobs


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
    assert result.stdout.splitlines() == [f"would install: {vault} (no vault: directory missing or empty)"]
    assert "installed:" not in result.stdout
    assert "doctor:" not in result.stdout
    assert not vault.exists()


TEXT_HEADLESS = [a for a in HEADLESS if a != "--json"]


def test_auto_dry_run_text_mode_names_a_refusal_it_would_make(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    result = runner.invoke(app, ["bootstrap", "auto", *TEXT_HEADLESS, "--dry-run"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.startswith(f"would refuse: {vault} ({vault} is non-empty but is not a Scout vault")


def test_auto_refusal_text_mode_names_vault_error_and_reason(tmp_path, monkeypatch):
    """A refusal prints `refused: <vault> — <error>` and, when it adds
    something, the detected reason on the next line (both on stderr)."""
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *TEXT_HEADLESS])
    assert result.exit_code == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"refused: {vault} — install needs --user-name and --user-email (or run interactively)",
        "  reason: no vault: directory missing or empty",
    ]


def test_refusal_text_mode_omits_a_reason_that_repeats_the_error(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    result = runner.invoke(app, ["bootstrap", "auto", *TEXT_HEADLESS, *IDENTITY])
    assert result.exit_code == 2, result.stdout + result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith(f"refused: {vault} — {vault} is non-empty but is not a Scout vault")


def test_auto_claude_bin_auto_resolves_via_which(tmp_path, monkeypatch):
    """`--claude-bin auto` uses install's resolver (#254): the PATH hit wins
    when it is executable."""
    vault = _vault(tmp_path, monkeypatch)
    claude = tmp_path / "homebrew" / "bin" / "claude"
    claude.parent.mkdir(parents=True)
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(claude))
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
    assert config["connectors"]["inputs"]["claude_bin"] == str(claude)


def test_auto_upgrade_ignores_identity_flags_note_on_stderr(tmp_path, monkeypatch):
    """The note names every flag that was passed but is read from
    scout-config.yaml on upgrade — and none that was honored."""
    vault = _vault(tmp_path, monkeypatch)
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
            "--claude-bin",
            "/usr/local/bin/claude",
            "--connectors",
            "github",
            "--max-budget",
            "9.00",
            *IDENTITY,
        ],
    )
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    note = next(line for line in result.stderr.splitlines() if line.startswith("note:"))
    assert note == (
        "note: ignored on upgrade (read from scout-config.yaml): --user-name, --user-email, --connectors, --max-budget"
    )
    inputs = yaml.safe_load((vault / "scout-config.yaml").read_text())["connectors"]
    assert inputs["enabled"] == [] and inputs["inputs"]["max_budget"] == "5.00"


def test_auto_upgrade_applies_an_explicit_claude_bin(tmp_path, monkeypatch):
    """Scout.app passes a fresh --claude-bin on every upgrade; a moved
    `claude` must reach the runners and scout-config.yaml."""
    vault = _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, "--claude-bin", "/tmp/x/claude"])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    assert json.loads(result.stdout)["action"] == "upgrade"
    config = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert config["connectors"]["inputs"]["claude_bin"] == "/tmp/x/claude"
    assert "/tmp/x/claude" in (vault / "run-scout.sh").read_text()
    assert "--claude-bin" not in result.stderr


def test_auto_upgrade_keeps_the_vaults_claude_bin_by_default(tmp_path, monkeypatch):
    """`--claude-bin auto` (the default) on upgrade keeps the recorded path."""
    vault = _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    monkeypatch.setattr("shutil.which", lambda name: "/elsewhere/claude")
    result = runner.invoke(app, ["bootstrap", "auto", "--no-jobs", "--no-interactive", "--yes", "--json"])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    config = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert config["connectors"]["inputs"]["claude_bin"] == "/usr/local/bin/claude"
    assert "note:" not in result.stderr


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
    # migrate-legacy defaults to --no-jobs, which leaves plists, shim and pointer alone.
    assert payload["pointer"] is None


def test_upgrade_json_refuses_without_vault(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "upgrade", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused"
    assert "run /scout-setup" in payload["error"]


# --- Fix round 1 (reviewer findings 1 & 3) -----------------------------------


def test_auto_installs_when_vault_has_only_ds_store(tmp_path, monkeypatch):
    """A vault directory containing only Finder's .DS_Store must still be
    treated as empty and install cleanly (reviewer finding 1)."""
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / ".DS_Store").write_bytes(b"\x00\x00")
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "install"
    assert (vault / "scout-config.yaml").exists()


def test_auto_upgrade_malformed_config_refuses_with_exit_2(tmp_path, monkeypatch):
    """A malformed scout-config.yaml hit on a repeat `auto` run (the UPGRADE
    branch, which calls _config_from_existing_vault directly) must refuse
    cleanly with exit 2 and a JSON payload — not crash (reviewer finding 3)."""
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance: [unclosed\n")
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    assert result.exit_code == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused"
    assert "malformed" in payload["error"]


# --- Final review: OS errors keep the --json / exit-code contract (Ruling 17)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_auto_unreadable_vault_is_refused_json_exit_2(tmp_path, monkeypatch):
    """detect() lists the vault; an unreadable one (e.g. TCC-denied
    ~/Documents/Scout) must still yield the refused payload, not exit 70
    with empty stdout."""
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    vault.chmod(0)
    try:
        result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    finally:
        vault.chmod(0o755)
    assert result.exit_code == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused"
    assert payload["mutated"] is False
    assert "Permission denied" in payload["error"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_auto_unreadable_config_is_refused_json_exit_2(tmp_path, monkeypatch):
    """Same contract on the UPGRADE path, where scout-config.yaml is read
    back before dispatch."""
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    config = vault / "scout-config.yaml"
    config.write_text("instance: {name: Scout}\n")
    config.chmod(0)
    try:
        result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    finally:
        config.chmod(0o644)
    assert result.exit_code == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused" and payload["mutated"] is False
    assert "Permission denied" in payload["error"]


# --- Final review: jobs-enabled runs (fake launchctl) -----------------------


@pytest.fixture
def fake_launchctl(tmp_path, monkeypatch) -> Path:
    """Put a fake `launchctl` first on PATH so a jobs-enabled bootstrap never
    touches the real launchd. install_plist(bootstrap=True) and the doctor
    both call `launchctl` by name. `list` reports both Scout jobs so the
    doctor is green; every call is logged; everything exits 0. Returns the log."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "launchctl.log"
    fake = fakebin / "launchctl"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'if [ "$1" = "list" ]; then\n'
        "  printf -- '-\\t0\\tcom.scout.schedule-tick\\n-\\t0\\tcom.scout.heartbeat\\n'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ['PATH']}")
    return log


JOBS_ENABLED = [a for a in HEADLESS if a != "--no-jobs"]


def test_auto_with_jobs_writes_one_consistent_engine_pointer(tmp_path, monkeypatch, fake_launchctl):
    """With jobs installed, the pointer, the schedule-tick plist and the
    ~/.local/bin/scoutctl shim all name the same scoutctl, and the pointer's
    vault is the plist's SCOUT_DATA_DIR — the consistency the doctor checks."""
    vault = _vault(tmp_path, monkeypatch)
    home = Path.home()  # the hermetic per-test HOME from conftest
    # The doctor reports red when the runner's CLAUDE_BIN is not executable
    # (#254), so point it at a stub instead of the HEADLESS /usr/local/bin path.
    claude = tmp_path / "fakebin" / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    argv = [*JOBS_ENABLED]
    argv[argv.index("--claude-bin") + 1] = str(claude)
    result = runner.invoke(app, ["bootstrap", "auto", *argv, *IDENTITY])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "install"
    assert payload["doctor"]["severity"] in ("green", "yellow"), payload["doctor"]
    assert "bootstrap gui/" in fake_launchctl.read_text()  # the fake, not launchd, was driven

    pointer_path = home / ".local" / "state" / "scout" / "engine.json"
    assert payload["pointer"] == str(pointer_path)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    with (home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist").open("rb") as f:
        plist = plistlib.load(f)
    shim = (home / ".local" / "bin" / "scoutctl").read_text(encoding="utf-8")
    shim_target = re.search(r'exec "([^"]+)"', shim)
    assert shim_target is not None, shim

    assert pointer["scoutctl"] == plist["ProgramArguments"][0] == shim_target.group(1)
    assert pointer["vault"] == plist["EnvironmentVariables"]["SCOUT_DATA_DIR"] == str(vault)
    assert pointer["managed_by"] == "scout-app"


# --- Final review: --managed-by defaults to `preserve` (Ruling 15) ----------


@pytest.mark.parametrize(
    "argv",
    [
        ["bootstrap", "install", "--no-jobs", "--skip-claude", *IDENTITY],
        ["bootstrap", "upgrade", "--no-jobs"],
        ["bootstrap", "migrate-legacy", *IDENTITY],
        ["bootstrap", "auto", *HEADLESS, *IDENTITY],
    ],
    ids=["install", "upgrade", "migrate-legacy", "auto"],
)
def test_invalid_managed_by_is_a_usage_error(tmp_path, monkeypatch, argv):
    """An explicit --managed-by outside MANAGED_BY_VALUES exits 2 before
    anything is written (the last --managed-by on the command line wins)."""
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, [*argv, "--managed-by", "bogus"])
    assert result.exit_code == 2, result.stdout + result.stderr
    assert "bogus" in result.output
    assert not vault.exists()


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["bootstrap", "upgrade", "--no-jobs", "--json"], "scout.scripts.bootstrap.upgrade"),
        (
            ["bootstrap", "auto", "--no-jobs", "--no-interactive", "--yes", "--json", "--platform", "macos"],
            "scout.scripts.bootstrap_auto.upgrade",
        ),
    ],
    ids=["upgrade", "auto"],
)
def test_plain_upgrade_keeps_an_app_managed_engine_app_managed(tmp_path, monkeypatch, argv, target):
    """No --managed-by (the doctor's own fix hint is a plain `scoutctl
    bootstrap upgrade`): the pointer's `scout-app` survives when the pointer
    describes the interpreter doing the upgrade."""
    from scout.scripts.bootstrap import UpgradeResult
    from scout.scripts.bootstrap_doctor import DoctorReport, Severity
    from scout.scripts.engine_pointer import current_pointer, write_pointer

    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance:\n  name: Scout\n", encoding="utf-8")
    write_pointer(current_pointer(vault=vault, managed_by="scout-app"), home=Path.home())
    seen: dict[str, str] = {}

    def fake_upgrade(cfg):
        seen["managed_by"] = cfg.managed_by
        return UpgradeResult(vault=cfg.vault, doctor=DoctorReport(severity=Severity.GREEN))

    monkeypatch.setattr(target, fake_upgrade)
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["action"] == "upgrade"
    assert seen["managed_by"] == "scout-app"

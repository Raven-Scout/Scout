"""An upgrade must not wipe fixes a vault carries in its plugin-owned files.

``scoutctl bootstrap upgrade`` (and auto-update, which calls it unattended)
re-renders the cat-1 files and the cat-1b runners from ``templates/``. Any fix
that existed only in a vault was therefore deleted on every upgrade, and had to
be restored by hand each time. Each case below is a fix a real vault carried
across two upgrades; the test gives a fresh vault the fix, upgrades it, and
checks the fix is still there.

Most of these files are plugin-owned, so the fix is kept by living in the
template. ``.gitignore`` is also edited by the vault, so the upgrade merges it
instead (see test_upgrade_merges_gitignore.py); the version stamp's handling of
scout-config.yaml comments is in test_upgrade_stamp_keeps_config_comments.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _config(vault: Path, *, version: str = "0.4.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN_ROOT,
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
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v))
    return v


# (vault file, the lines that carry the fix). Only functional lines are listed,
# not comments, so the template is free to word its comments generically.
# Lines compare stripped of indentation.
_RUNNER_FIX = [
    # Without the export the telemetry hooks treat every scheduled run as
    # interactive and write nothing, so connector-health logging goes dark.
    'export SCOUT_MODE="$MODE"',
    # Record how the run ended, from the wrapper, out-of-band.
    'OUTCOME="$SCOUT_DIR/scripts/run-outcome.sh"',
    '"$OUTCOME" record "$MODE" "$EXIT_CODE" "$START_TIME" "$LOG_FILE" >> "$LOG_FILE" 2>&1 || true',
    # Connector-health roll-up, so the surface refreshes even when a run dies early.
    'HEALTH_ROLLUP="$SCOUT_DIR/scripts/connector-health-rollup.sh"',
    '"$HEALTH_ROLLUP" >> "$LOG_FILE" 2>&1 || true',
]

VAULT_FIXES = [
    pytest.param(".gitignore", [".mcp.json", ".venv/"], id="gitignore"),
    pytest.param("run-scout.sh", _RUNNER_FIX, id="run-scout"),
    pytest.param("run-dreaming.sh", _RUNNER_FIX, id="run-dreaming"),
    pytest.param("run-research.sh", _RUNNER_FIX, id="run-research"),
    pytest.param(
        "hooks/kb-pre-filter.sh",
        ['( cd "$SCOUT_DATA_DIR" && python3 scripts/vault-freshness.py --limit 15 \\'],
        id="kb-pre-filter",
    ),
    pytest.param(
        "scripts/heartbeat.sh",
        ['python3 "$SCOUT_DATA_DIR/scripts/session-lane-liveness.py" --quiet --notify \\'],
        id="heartbeat",
    ),
    pytest.param(
        "scripts/recurring-task-status.py",
        [
            'if cadence.startswith("yearly:"):',
            "def count_missed_windows(cadence: str, last_completed: Optional[date], today: date) -> int:",
        ],
        id="recurring-task-status",
    ),
]


def _lines(path: Path) -> list[str]:
    """The file's lines, stripped, so a fix matches at any indentation."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(("rel", "fix_lines"), VAULT_FIXES)
def test_upgrade_over_a_vault_carrying_the_fix_keeps_it(vault: Path, rel: str, fix_lines: list[str]) -> None:
    target = vault / rel
    # Give the vault the fix the way it was restored by hand: append whatever the
    # installed file lacks.
    missing = [line for line in fix_lines if line not in _lines(target)]
    if missing:
        with target.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(missing) + "\n")

    upgrade(_config(vault, version="0.4.1"))

    after = _lines(target)
    dropped = [line for line in fix_lines if line not in after]
    assert not dropped, f"upgrade dropped the vault's fix from {rel}: {dropped}"


@pytest.mark.parametrize(("rel", "fix_lines"), VAULT_FIXES)
def test_fresh_install_ships_the_fix(vault: Path, rel: str, fix_lines: list[str]) -> None:
    """The fix is in the template, so a vault that never had it gets it too."""
    installed = _lines(vault / rel)
    absent = [line for line in fix_lines if line not in installed]
    assert not absent, f"{rel} from a fresh install lacks: {absent}"


def test_upgrade_over_a_current_vault_backs_up_no_runner(vault: Path) -> None:
    """A vault already at the template produces no runner .bak files.

    A runner backup means "your hand edit was overwritten", and the doctor
    reports it. With the fixes in the template, a vault that only carried them
    has nothing to back up, so the doctor can stay green across upgrades.
    """
    result = upgrade(_config(vault, version="0.4.1"))
    assert result.backups == []
    assert list(vault.glob("run-*.sh.bak.*")) == []

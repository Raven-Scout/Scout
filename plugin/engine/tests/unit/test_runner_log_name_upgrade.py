"""Upgrading a vault to unique run-log names keeps the vault's own runner edits.

The runners' log-name lines changed (claim the minute-named log, else a
seconds+PID name). A vault upgrades through vault_drift's 3-way merge, with the
vault's last render as the base. An edit far from those lines merges, and the
runner takes the new naming; an edit right next to them conflicts, so the
upgrade parks the plugin's version and leaves the vault's runner untouched,
still working with its old minute-named logs, which every reader still parses.

The vault is installed under a tmp dir with ``skip_jobs`` (``--no-jobs``): no
scheduler job is written or replaced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.scripts import vault_drift
from scout.scripts.bootstrap import BootstrapConfig, install

runner = CliRunner()
PLUGIN = Path(cli.__file__).resolve().parent.parent.parent
RUNNERS = {"run-scout.sh": "scout", "run-dreaming.sh": "dreaming", "run-research.sh": "research"}

# The block a runner's log-name lines are now, and the two lines it replaced.
_NEW_BLOCK = re.compile(
    r"# One log per run\..*?\nfi\n",
    re.DOTALL,
)


def _old_render(new: str, stem: str) -> str:
    """The same runner as the release before unique names rendered it."""
    old, n = _NEW_BLOCK.subn(
        f'TIMESTAMP=$(date +%Y-%m-%d_%H-%M)\nLOG_FILE="$LOG_DIR/{stem}-$TIMESTAMP.log"\n', new, count=1
    )
    assert n == 1, "the runner's log-name block moved; update this test"
    return old


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
        )
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _as_old_release(vault: Path, rel: str, edit) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Put ``rel`` back to the old release's render (base), with ``edit`` applied live."""
    new = (vault / rel).read_text(encoding="utf-8")
    old = _old_render(new, RUNNERS[rel])
    vault_drift.snapshot_path(vault, rel).write_text(old, encoding="utf-8")
    live = edit(old.splitlines(keepends=True))
    (vault / rel).write_text(live, encoding="utf-8")
    return new, live


def _tag(lines: list[str], index: int) -> str:
    lines[index] = lines[index].rstrip("\n") + "  # vault-local\n"
    return "".join(lines)


def _upgrade():  # type: ignore[no-untyped-def]
    return runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])


@pytest.mark.parametrize("rel", RUNNERS)
def test_a_far_vault_edit_merges_and_the_runner_gets_unique_log_names(vault: Path, rel: str) -> None:
    new, _ = _as_old_release(vault, rel, lambda lines: _tag(lines, len(lines) - 5))

    result = _upgrade()

    assert result.exit_code == 0, result.output

    upgraded = (vault / rel).read_text(encoding="utf-8")
    assert "# vault-local" in upgraded
    assert _NEW_BLOCK.search(upgraded), "the merged runner must claim its log the new way"
    assert "TIMESTAMP=" not in upgraded
    assert upgraded.replace("  # vault-local", "") == new
    assert not (vault / vault_drift.DRIFT_DIR / f"{rel}.plugin").exists()


@pytest.mark.parametrize("rel", RUNNERS)
def test_an_edit_next_to_the_log_name_lines_parks_the_update(vault: Path, rel: str) -> None:
    def edit_lock_line(lines: list[str]) -> str:
        (i,) = [n for n, line in enumerate(lines) if line.startswith("LOCK_FILE=")]
        return _tag(lines, i)

    new, live = _as_old_release(vault, rel, edit_lock_line)

    result = _upgrade()

    # A parked update is a doctor warning: the upgrade says so and exits non-zero.
    assert f"vault edit conflict: {rel}" in result.output

    # The vault's runner is left exactly as it was: old minute-named logs, its edit kept.
    assert (vault / rel).read_text(encoding="utf-8") == live
    assert (vault / vault_drift.DRIFT_DIR / f"{rel}.plugin").read_text(encoding="utf-8") == new
    drift = runner.invoke(cli.app, ["bootstrap", "drift"])
    assert rel in drift.stdout

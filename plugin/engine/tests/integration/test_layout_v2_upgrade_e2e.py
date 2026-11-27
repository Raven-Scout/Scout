"""End to end: a 0.18-shaped (layout v1) vault upgraded by this engine lands on layout v2 and its runners run.

``bootstrap.upgrade`` runs the layout move as its migrations stage, then renders every managed file at its v2
path against the moved merge bases, assembles the brain skills into ``scout/brain/``, and records the move. Each
runner then runs dry (another session holds the session lock, so it skips at once) and records its outcome
through ``scout/scripts/run-outcome.sh``: the helpers it sources resolve at their new paths.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scout import layout
from scout.scripts import bootstrap
from tests.unit.layout_v1_vault import build_v1_vault

PLUGIN_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
SPACES = (
    "spaces:\n  - key: work\n    default: true\n"
    "  - key: personal\n    visibility: private\n  - key: svj\n    visibility: private\n"
)
USER = (
    "connectors:\n  enabled: []\n  inputs: {claude_bin: /usr/bin/true, max_budget: '5.00'}\n"
    "timezone: America/New_York\n"
)


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def upgraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bootstrap.UpgradeResult]:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = build_v1_vault(tmp_path / "Scout", spaces=SPACES + USER, dirty=False)
    cfg = bootstrap.config_from_vault(vault, plugin_root=PLUGIN_ROOT, plugin_version="0.19.0", skip_jobs=True)
    return vault, bootstrap.upgrade(cfg)


def test_the_upgrade_moves_the_vault_and_renders_at_v2_paths(upgraded: tuple[Path, bootstrap.UpgradeResult]) -> None:
    vault, result = upgraded
    assert result.migration is not None and result.migration.state == "applied"
    assert layout.layout_version(vault) == 2
    for kind in layout.BRAIN_KINDS:
        assert (vault / layout.brain_path(kind)).is_file()
    for name in layout.RUNNER_NAMES:
        assert (vault / layout.runner_path(name)).is_file(), name
    for v2_rel, _tmpl in bootstrap._CAT1_TEMPLATES:
        if v2_rel != ".gitignore":
            assert (vault / v2_rel).is_file(), v2_rel
    # nothing rendered at a v1 path: the old entry points are only the compatibility links
    for v1 in ("scripts", "hooks", "SKILL.md", "run-scout.sh"):
        assert (vault / v1).is_symlink(), v1
    assert not (vault / "meetings").exists() and not (vault / "inbox.md").exists()
    config = (vault / "scout-config.yaml").read_text()
    assert "layout-v2" in config
    assert (vault / "README.md").read_text().startswith(layout.README_MARKER)
    # the vault's own runner moved and its literal paths were rewritten
    assert "$SCOUT_DIR/scout/scripts/scout-lock.sh" in (vault / "scout/runners/run-mine.sh").read_text()


def test_no_merge_base_is_pruned_or_orphaned(upgraded: tuple[Path, bootstrap.UpgradeResult]) -> None:
    vault, _ = upgraded
    rendered = vault / ".scout-state" / "last-rendered"
    bases = {p.relative_to(rendered).as_posix() for p in rendered.rglob("*") if p.is_file()}
    assert "scout/scripts/scoutctl" in bases
    assert not any(b.startswith(("scripts/", "hooks/", "run-")) for b in bases)


def test_a_second_upgrade_is_quiet_and_removes_no_link_while_the_jobs_are_old(
    upgraded: tuple[Path, bootstrap.UpgradeResult],
) -> None:
    vault, _ = upgraded
    cfg = bootstrap.config_from_vault(vault, plugin_root=PLUGIN_ROOT, plugin_version="0.19.0", skip_jobs=True)
    second = bootstrap.upgrade(cfg)
    assert second.migration is not None and second.migration.state == "skipped"
    # No jobs or pointer were installed (skip_jobs), and no plugin is installed: nothing names a v1 path, so the
    # links go on this upgrade.
    assert sorted(second.removed_compat_links) == sorted(
        [
            "scripts",
            "hooks",
            "SKILL.md",
            "DREAMING.md",
            "RESEARCH.md",
            "RESPOND.md",
            *(n for n in ("run-scout.sh", "run-dreaming.sh", "run-research.sh", "run-respond.sh", "run-mine.sh")),
        ]
    )
    assert not (vault / "scripts").exists()


@pytest.mark.parametrize("runner", ["run-scout.sh", "run-dreaming.sh", "run-research.sh"])
def test_each_runner_runs_dry_at_its_v2_path(upgraded: tuple[Path, bootstrap.UpgradeResult], runner: str) -> None:
    vault, _ = upgraded
    holder = subprocess.Popen(["sleep", "60"])
    try:
        (vault / ".scout-logs").mkdir(exist_ok=True)
        (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{holder.pid}\n", encoding="utf-8")
        proc = subprocess.run(
            [str(vault / "scout" / "runners" / runner)],
            env={**os.environ, "SCOUT_FORCE_MODE": "morning-briefing", "SCOUT_LOCK_WAIT_SECS": "0"},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        holder.kill()
        holder.wait()
    assert proc.returncode == 0, proc.stderr
    ledger = vault / ".scout-logs" / "run-outcomes.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[-1]["outcome"] == "skipped_lock"


def test_removing_the_links_also_unignores_their_paths(upgraded: tuple[Path, bootstrap.UpgradeResult]) -> None:
    vault, _ = upgraded
    cfg = bootstrap.config_from_vault(vault, plugin_root=PLUGIN_ROOT, plugin_version="0.19.0", skip_jobs=True)
    bootstrap.upgrade(cfg)
    gi = (vault / ".gitignore").read_text()
    assert "/scripts\n" not in gi and "/SKILL.md\n" not in gi and "compatibility links" not in gi
    assert "inbox/drop/*" in gi

"""Recovery from an interrupted layout move (spec 2026-11-26 §4.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout, layout_migrate
from tests.unit.layout_v1_vault import build_v1_vault


@pytest.fixture(autouse=True)
def _no_installed_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


class Killed(BaseException):
    """Stands in for the process dying: nothing in the move catches it."""


def _kill_at(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def boom(*a: object, **k: object) -> None:
        raise Killed

    monkeypatch.setattr(layout_migrate, name, boom)


def _snapshot(vault: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(vault.rglob("*")):
        rel = p.relative_to(vault).as_posix()
        if rel.startswith((".git/", ".scout-state/migrations")) or p.is_dir():
            continue
        out[rel] = p.read_bytes() if not p.is_symlink() else b"->" + str(p.readlink()).encode()
    return out


def test_kill_after_the_moves_keeps_the_heartbeat_reachable_and_recovery_reverses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    before = _snapshot(vault)
    head = _git(vault, "rev-parse", "HEAD")
    _kill_at(monkeypatch, "_index_renames")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # The journal says a move is in progress; runs skip, but the heartbeat (which starts self-update) resolves.
    assert layout.move_in_progress(vault)
    assert (vault / "scripts/heartbeat.sh").is_file()
    assert (vault / "scripts").is_symlink()
    assert layout_migrate.recover(vault) == "reversed"
    assert not layout.move_in_progress(vault)
    assert _snapshot(vault) == before
    assert _git(vault, "rev-parse", "HEAD") == head
    assert _git(vault, "status", "--porcelain") == ""
    # and the next upgrade's stage then moves it
    assert layout_migrate.migrate_layout(vault).state == "applied"


def test_kill_mid_move_is_reversed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    before = _snapshot(vault)
    real = layout_migrate._rename
    calls = {"n": 0}

    def flaky(v: Path, src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 20:
            raise Killed
        real(v, src, dst)

    monkeypatch.setattr(layout_migrate, "_rename", flaky)
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.setattr(layout_migrate, "_rename", real)
    assert layout_migrate.recover(vault) == "reversed"
    assert _snapshot(vault) == before


def test_a_failure_before_commit_one_defers_and_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    before = _snapshot(vault)

    def fail(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(layout_migrate, "_index_renames", fail)
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "deferred" and "disk full" in out.reason and "reversed" in out.reason
    assert _snapshot(vault) == before
    assert not layout.move_in_progress(vault)


def test_kill_between_the_commits_finishes_forward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    _kill_at(monkeypatch, "render_report")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    assert layout.move_in_progress(vault)
    assert _git(vault, "log", "-1", "--format=%s").startswith("scout-update: move the vault to layout v2 (")
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "recovered"
    assert not layout.move_in_progress(vault)
    assert "Scout-Migration: layout-v2 content" in _git(vault, "log", "-1", "--format=%b")
    assert (vault / "knowledge-base/people/alex-example.md").read_text().count("spaces: [work]") == 1

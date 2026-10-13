"""Run-health spec §11: with a Scout.app engine present, the updater refuses before any read or subprocess.

The eligibility tests drive fake ``Deps``. This one wires the REAL ``default_deps`` against a
temporary HOME that holds the pointer Scout.app writes, and makes every side effect the updater
could reach fail the test: a subprocess, a network read, the vault config, the doctor, the lock.
It is the macOS "app-managed engine present" case of §11 without a Mac account; the container
e2e (scripts/e2e/self-update) covers the same refusal through the CLI on Linux.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request

import pytest

from scout import update_outcomes as uo
from scout.scripts import self_update
from scout.scripts import self_update_run as sur


def _app_pointer(home, vault):
    # What Scout.app's EngineInstaller records: its own engine and venv, outside any plugin cache.
    root = home / ".local" / "share" / "scout" / "engine" / "0.18.0"
    path = home / ".local" / "state" / "scout" / "engine.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.18.0",
                "engine_root": str(root),
                "python": str(home / ".local" / "share" / "scout" / "venv" / "0.18.0" / "bin" / "python"),
                "scoutctl": str(home / ".local" / "share" / "scout" / "venv" / "0.18.0" / "bin" / "scoutctl"),
                "vault": str(vault),
                "managed_by": "scout-app",
                "written_at": "2026-10-13T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def tripwires(monkeypatch):
    """Every side effect past the pointer read raises; returns the names that were reached."""
    reached: list[str] = []

    def trip(name):
        def _raise(*args, **kwargs):
            reached.append(name)
            raise AssertionError(f"the updater reached {name} for an app-managed engine")

        return _raise

    monkeypatch.setattr(subprocess, "Popen", trip("subprocess"))
    monkeypatch.setattr(urllib.request, "urlopen", trip("network"))
    monkeypatch.setattr(self_update, "check", trip("check"))
    monkeypatch.setattr(self_update, "fetch_release", trip("fetch_release"))
    monkeypatch.setattr("scout.config.load_config", trip("load_config"))
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.run_doctor", trip("doctor"))
    monkeypatch.setattr("scout.scripts.bootstrap_lock.is_lock_held_by_live_pid", trip("lock"))
    monkeypatch.setattr("scout.scripts.bootstrap.blocking_sidecars", trip("sidecars"))
    return reached


@pytest.mark.parametrize(("scheduled", "force"), [(True, False), (True, True), (False, True), (False, False)])
def test_real_wiring_refuses_an_app_managed_engine_before_any_side_effect(tmp_path, tripwires, scheduled, force):
    home, vault = tmp_path / "home", tmp_path / "vault"
    vault.mkdir()
    # Auto-update on, in the vault's own config: the refusal must not depend on reading it.
    (vault / "scout-config.yaml").write_text("auto_update:\n  enabled: true\n  channel: stable\n", encoding="utf-8")
    _app_pointer(home, vault)

    deps = sur.default_deps(vault, home=home, quiet=True)
    notified = []
    deps.notify = lambda row, prior: notified.append(row)  # the real notifier may run osascript
    attempt = sur.run_update(deps, scheduled=scheduled, force=force)

    assert tripwires == []
    assert attempt.row is not None
    assert (attempt.row.result, attempt.row.detail) == ("not_eligible", "managed_by=scout-app")
    assert attempt.row.target is None and attempt.row.from_version == "0.18.0"
    assert [r.result for r in uo.read(vault)] == ["not_eligible"]
    assert len(notified) == 1

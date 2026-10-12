"""self-update run's process handling: a new session for the heartbeat, one attempt at a time."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from typer.testing import CliRunner

from scout import update_outcomes as uo
from scout.cli import app
from scout.scripts import self_update_run as sur
from tests.unit.self_update_fakes import NOW, make_deps

runner = CliRunner()


def test_spawn_detached_uses_a_new_session_and_appends_to_the_log(tmp_path):
    seen = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, **kw):
        seen.update(argv=argv, **kw)
        kw["stdout"].write("child output\n")
        return FakeProc()

    log = tmp_path / "logs" / "self-update.log"
    log.parent.mkdir()
    log.write_text("earlier\n")
    assert sur.spawn_detached(["scoutctl", "x"], log_path=log, popen=fake_popen) == 4242
    assert seen["start_new_session"] is True
    assert seen["stdin"] is subprocess.DEVNULL and seen["stdout"] is seen["stderr"]
    assert log.read_text() == "earlier\nchild output\n"


def test_spawn_detached_child_outlives_and_leaves_the_parent_group(tmp_path):
    log = tmp_path / "out.log"
    pid = sur.spawn_detached([sys.executable, "-c", "import os; print(os.getsid(0), os.getpgid(0))"], log_path=log)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not log.read_text().strip():
        time.sleep(0.05)
    sid, pgid = (int(x) for x in log.read_text().split())
    assert sid == pid and pgid == pid  # its own session and process group
    assert pgid != os.getpgid(0)


def test_run_lock_admits_one_holder(tmp_path):
    with sur.run_lock(tmp_path) as first:
        assert first is True
        with sur.run_lock(tmp_path) as second:
            assert second is False
    with sur.run_lock(tmp_path) as again:
        assert again is True
    assert (tmp_path / ".scout-cache" / "self-update.lock").exists()


def test_cli_detach_spawns_the_same_flags_and_returns(tmp_path, monkeypatch):
    seen = {}

    def fake_spawn(argv, *, log_path):
        seen.update(argv=argv, log_path=log_path)
        return 77

    vault = tmp_path / "vault"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.setattr(sur, "spawn_detached", fake_spawn)
    monkeypatch.setattr(sur, "default_deps", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran inline")))
    result = runner.invoke(app, ["self-update", "run", "--scheduled", "--force", "--detach"])
    assert result.exit_code == 0, result.output
    assert seen["argv"] == [sys.executable, "-m", "scout", "self-update", "run", "--scheduled", "--force"]
    assert seen["log_path"] == vault / ".scout-logs" / f"self-update-{datetime.now():%Y-%m-%d}.log"
    assert "pid 77" in result.output


def test_cli_skips_when_another_attempt_holds_the_lock(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.setattr(sur, "default_deps", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    with sur.run_lock(vault):
        result = runner.invoke(app, ["self-update", "run", "--scheduled"])
    assert result.exit_code == 0 and "another attempt is still running" in result.output
    assert not list((vault / ".scout-cache").glob("self-update-*.done"))


def test_network_failures_never_count_toward_a_targets_backoff(tmp_path):
    deps, _, _ = make_deps(tmp_path)
    for days in (5, 4, 3):
        uo.append(
            deps.vault,
            uo.UpdateOutcome(
                at=NOW - timedelta(days=days),
                from_version="0.16.0",
                target="0.17.0",
                managed_by="install.sh",
                result="failed",
                step="check",
                detail="network: could not reach GitHub",
            ),
        )
    assert sur.run_update(deps, scheduled=True).row.result == "applied"

"""#550 V2c: delegate mirrors sync after each run and from the schedule tick; failures reach run health and doctor.

Reuses the fixture vault and local bare remotes of ``test_delegate_mirror``. No
test starts a real background process or touches a real scheduler.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_mirror as dm
from scout import health
from scout import repo_sync as rs
from scout.cli import app
from scout.scripts import bootstrap_doctor, schedule_tick

from .privacy_vault_helpers import git
from .test_delegate_mirror import _bare, _commit, _link, vault  # noqa: F401 - the fixture

runner = CliRunner()
TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


def _events(v: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted((v / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def _break_remote(v: Path, delegate: str) -> None:
    links = dm.load_links(v)
    links[delegate].remote = "/nonexistent/mirror.git"
    dm.save_links(v, links)


def _ignore_state(v: Path) -> None:
    git(v, "add", "-A")  # link's .gitignore lines
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "ignore state")


# ----- outcomes on the link and as events ---------------------------------------------------


def test_a_failed_sync_is_recorded_until_one_succeeds(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    _break_remote(vault, "grok-personal")
    with pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    link = dm.load_links(vault)["grok-personal"]
    assert link.last_error is not None and link.last_error["code"] and link.last_attempt
    failed = [e for e in _events(vault) if e["kind"] == "delegate_mirror.sync_failed"]
    assert failed and failed[-1]["payload"] == {"delegate": "grok-personal", "code": link.last_error["code"]}
    links = dm.load_links(vault)
    links["grok-personal"].remote = str(bare)
    dm.save_links(vault, links)
    dm.sync(vault, "grok-personal")
    assert dm.load_links(vault)["grok-personal"].last_error is None
    assert _events(vault)[-1]["kind"] == "delegate_mirror.synced"
    assert _events(vault)[-1]["payload"] == {"delegate": "grok-personal", "pushed": True}


def test_busy_records_nothing(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    with dm.locked(vault), pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    assert dm.load_links(vault)["grok-personal"].last_error is None
    assert not [e for e in _events(vault) if e["kind"] == "delegate_mirror.sync_failed"]


# ----- which mirrors may be behind, and when the tick starts a sync ------------------------------


def test_due_needs_a_link_a_moved_head_and_fifteen_quiet_minutes(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    assert not dm.due(vault)
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _ignore_state(vault)
    assert dm.needs_sync(vault, dm.load_links(vault)) == ["grok-personal"]
    assert dm.due(vault)
    dm.mark_started(vault)
    assert not dm.due(vault)
    old = time.time() - 16 * 60
    os.utime(vault / dm.TICK_REL, (old, old))
    assert dm.due(vault)
    dm.sync(vault, "grok-personal")
    assert dm.needs_sync(vault, dm.load_links(vault)) == [] and not dm.due(vault)
    links = dm.load_links(vault)
    links["grok-personal"].last_error = {"code": "push_failed", "at": "2026-11-13T10:00:00Z"}
    dm.save_links(vault, links)
    assert dm.due(vault)
    dm.state_path(vault).write_text("not json", encoding="utf-8")
    assert dm.due(vault)  # the sync reports why it can't run
    assert git(vault, "check-ignore", dm.TICK_REL).strip() == dm.TICK_REL


def test_sync_all_if_moved_skips_mirrors_that_are_current(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _link(vault, "duet", _bare(tmp_path, "duet"))
    _ignore_state(vault)
    dm.sync(vault, "grok-personal")
    done, failed = dm.sync_all(vault, only_moved=True)
    assert [r.delegate for r in done] == ["duet"] and not failed
    r = runner.invoke(app, ["delegate", "sync", "--all", "--if-moved", "--json"])
    assert r.exit_code == 0 and json.loads(r.stdout)["synced"] == []
    assert runner.invoke(app, ["delegate", "sync", "duet", "--if-moved"]).exit_code == 2


def test_the_tick_starts_a_sync_only_when_due(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    log_dir = vault / ".scout-logs"
    started: list[tuple[Path, Path]] = []

    def spawn(v: Path, logs: Path) -> int:
        started.append((v, logs))
        return 4242

    schedule_tick._sync_delegates(vault=vault, log_dir=log_dir, spawn=spawn)
    assert started == []  # nothing linked: one stat, no import
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _ignore_state(vault)
    schedule_tick._sync_delegates(vault=vault, log_dir=log_dir, spawn=spawn)
    assert started == [(vault, log_dir)]
    assert any(e["kind"] == "delegate_mirror.sync_started" and e["payload"] == {"pid": 4242} for e in _events(vault))
    schedule_tick._sync_delegates(vault=vault, log_dir=log_dir, spawn=spawn)
    assert len(started) == 1  # fifteen minutes haven't passed


def test_a_tick_whose_spawn_fails_records_it_and_carries_on(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _ignore_state(vault)

    def spawn(_v: Path, _l: Path) -> int:
        raise OSError("no python")

    schedule_tick._sync_delegates(vault=vault, log_dir=vault / ".scout-logs", spawn=spawn)
    failed = [e for e in _events(vault) if e["kind"] == "delegate_mirror.spawn_failed"]
    assert failed and "no python" in failed[-1]["payload"]["error"]


def test_the_tick_spawns_the_sync_detached_with_its_output_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv: list[str], **kw: object) -> None:
            seen["argv"], seen["kw"] = argv, kw
            self.pid = 99

    monkeypatch.setattr(schedule_tick.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    vault_dir, logs = tmp_path / "v", tmp_path / "v" / ".scout-logs"
    vault_dir.mkdir()
    assert schedule_tick._spawn_delegate_sync(vault_dir, logs) == 99
    assert seen["argv"][1:] == ["-m", "scout", "delegate", "sync", "--all", "--if-moved"]
    kw = seen["kw"]
    assert kw["start_new_session"] is True and kw["cwd"] == str(vault_dir)
    assert kw["env"]["SCOUT_DATA_DIR"] == str(vault_dir) and "SCOUT_MODE" not in kw["env"]
    assert kw["stderr"] is subprocess.STDOUT and (logs / schedule_tick._DELEGATE_LOG).exists()


# ----- run health and doctor --------------------------------------------------------------------


def test_run_health_shows_a_failed_mirror_and_nothing_without_links(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    assert health.delegate_findings(vault) == []
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    assert health.delegate_findings(vault) == []
    links = dm.load_links(vault)
    links["grok-personal"].last_error = {"code": "remote_public", "at": "2026-11-13T10:00:00Z"}
    dm.save_links(vault, links)
    [finding] = health.delegate_findings(vault)
    assert finding.id == "delegate.sync_failed" and finding.severity == "yellow"
    assert "remote_public" in finding.summary and finding.since == datetime(2026, 11, 13, 10, tzinfo=UTC)
    assert "scoutctl delegate sync grok-personal" in finding.fix
    dm.state_path(vault).write_text("[]", encoding="utf-8")
    [finding] = health.delegate_findings(vault)
    assert finding.id == "delegate.state_unreadable"
    assert any(f.id == "delegate.state_unreadable" for f in health.evaluate(vault).findings)


def test_doctor_shows_mirror_ages_and_warns_when_one_is_stuck(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    assert bootstrap_doctor._check_delegate_mirrors(vault=vault, now=None) == ([], [])
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _link(vault, "duet", _bare(tmp_path, "duet"))
    now = datetime.now(UTC) + timedelta(minutes=30)
    warnings, notes = bootstrap_doctor._check_delegate_mirrors(vault=vault, now=now)
    assert warnings == [] and notes == [
        "delegate mirror duet: last synced 30 min ago",
        "delegate mirror grok-personal: last synced 30 min ago",
    ]
    links = dm.load_links(vault)
    links["duet"].last_error = {"code": "push_failed", "at": "2026-11-13T10:00:00Z"}
    links["grok-personal"].pending_mirror = "0" * 40
    dm.save_links(vault, links)
    warnings, _ = bootstrap_doctor._check_delegate_mirrors(vault=vault, now=now)
    assert "duet: the last sync failed (push_failed)" in warnings[0]
    assert "grok-personal: a push didn't finish" in warnings[1]
    links["duet"].last_error = None
    links["grok-personal"].pending_mirror = None
    links["grok-personal"].last_sync = None
    dm.save_links(vault, links)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    later = datetime.now(UTC) + timedelta(hours=3)
    warnings, notes = bootstrap_doctor._check_delegate_mirrors(vault=vault, now=later)
    assert len(warnings) == 2 and all("behind HEAD" in w for w in warnings)
    assert "delegate mirror grok-personal: never synced" in notes
    links["duet"].last_sync = "garbled"
    dm.save_links(vault, links)
    _, notes = bootstrap_doctor._check_delegate_mirrors(vault=vault, now=later)
    assert "delegate mirror duet: never synced" in notes
    dm.state_path(vault).write_text("[]", encoding="utf-8")
    warnings, notes = bootstrap_doctor._check_delegate_mirrors(vault=vault, now=later)
    assert notes == [] and warnings[0].startswith("delegate mirrors: refused:")


def test_ages_read_in_minutes_hours_then_days() -> None:
    assert bootstrap_doctor._age(timedelta(minutes=5)) == "5 min"
    assert bootstrap_doctor._age(timedelta(hours=5)) == "5 h"
    assert bootstrap_doctor._age(timedelta(days=5)) == "5 days"
    assert bootstrap_doctor._age(timedelta(minutes=-3)) == "0 min"


# ----- the runners --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
def test_every_runner_syncs_the_mirrors_when_it_finishes_and_never_fails_for_it(name: str) -> None:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    guard = 'if [ -x "$SCOUTCTL_BIN" ] && [ -f "$SCOUT_DIR/.scout-state/delegate-mirrors.json" ]; then'
    call = '    "$SCOUTCTL_BIN" delegate sync --all >> "$LOG_FILE" 2>&1 || true'
    assert f"{guard}\n{call}\nfi\n" in text
    assert text.index("connector-health-report") < text.index("delegate sync --all") < text.index("record-run")

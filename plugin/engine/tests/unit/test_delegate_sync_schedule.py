"""#550 V2c: delegate mirrors sync after each run and from the schedule tick; failures reach run health and doctor.

Reuses the fixture vault and local bare remotes of ``test_delegate_mirror``. No
test starts a real background process or touches a real scheduler.
"""

from __future__ import annotations

import dataclasses
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
from .test_delegate_mirror import (  # noqa: F401 - the fixtures
    INBOX_CANARY,
    PRIYA,
    _all_blobs,
    _bare,
    _commit,
    _commit_files,
    _link,
    _show,
    github_link,
    home,
    vault,
)

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

    def never(*_a: object, **_k: object) -> bool:
        raise AssertionError("a vault with no links must not reach the mirror code")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dm, "due", never)
        schedule_tick._sync_delegates(vault=vault, log_dir=log_dir, spawn=spawn)
    assert started == []  # nothing linked: one stat
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
    assert seen["argv"][1:] == ["-P", "-m", "scout", "delegate", "sync", "--all", "--if-moved"]
    kw = seen["kw"]
    assert kw["start_new_session"] is True and kw["cwd"] != str(vault_dir) and not kw["cwd"].startswith(str(vault_dir))
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
    call = '    "$SCOUTCTL_BIN" delegate sync --all --if-moved >> "$LOG_FILE" 2>&1 || true'
    assert f"{guard}\n{call}\nfi\n" in text
    # last: after the cost record, so the run's own bookkeeping never waits on a push; never in a trap
    assert text.index("connector-health-report") < text.index("record-run") < text.index("delegate sync --all")
    assert text.rstrip().endswith("fi")
    assert not any(ln.lstrip().startswith("trap ") and "delegate" in ln for ln in text.splitlines())


# ----- review of #636 (coordinator, 2026-11-14) ----------------------------------------------------


def _scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the path of every text scan (the expensive part of a sync)."""
    seen: list[str] = []
    real = dm.Scanner.lines

    def counting(self: dm.Scanner, text: str, rel: str = "") -> list[int]:
        seen.append(rel)
        return real(self, text, rel)

    monkeypatch.setattr(dm.Scanner, "lines", counting)
    return seen


def _synced(vault: Path, tmp_path: Path, delegate: str = "grok-personal") -> Path:  # noqa: F811
    bare = _bare(tmp_path, delegate)
    _link(vault, delegate, bare)
    _ignore_state(vault)
    dm.sync(vault, delegate)
    return bare


def test_an_unchanged_vault_does_no_scan_on_the_runner_path(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _synced(vault, tmp_path)

    def never(*_a: object, **_k: object) -> object:
        raise AssertionError("an unchanged vault must not be planned")

    monkeypatch.setattr(dm, "plan", never)
    assert dm.sync_all(vault, only_moved=True) == ([], {})  # what the runners and the tick run


def test_a_failing_mirror_is_retried_on_a_backoff_not_every_tick(
    vault: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    bare = _synced(vault, tmp_path)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    _break_remote(vault, "grok-personal")  # fails at the push, after the whole build and check
    with pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    links = dm.load_links(vault)
    now = datetime.now(UTC)
    assert dm.needs_sync(vault, links, now=now) == []  # same HEAD, same config: wait
    assert dm.needs_sync(vault, links, now=now + timedelta(minutes=59)) == []
    assert dm.needs_sync(vault, links, now=now + timedelta(minutes=61)) == ["grok-personal"]
    error = links["grok-personal"].last_error
    assert error is not None
    links["grok-personal"].last_error = {**error, "first_at": (now - timedelta(days=2)).isoformat()}
    links["grok-personal"].last_attempt = (now - timedelta(hours=2)).isoformat()
    assert dm.needs_sync(vault, links, now=now) == []  # failing for over a day: daily
    links["grok-personal"].last_attempt = (now - timedelta(hours=25)).isoformat()
    assert dm.needs_sync(vault, links, now=now) == ["grok-personal"]
    links["grok-personal"].last_attempt = (now + timedelta(hours=1)).isoformat()
    assert dm.needs_sync(vault, links, now=now) == ["grok-personal"]  # a clock that went back: try
    links = dm.load_links(vault)
    _commit(vault, "knowledge-base/topics/misc/other.md", "---\ntitle: Other\n---\n\nother\n", "other")
    assert dm.needs_sync(vault, links, now=now) == ["grok-personal"]  # a new commit: at the next tick
    del bare


def test_a_config_edit_alone_makes_a_mirror_due(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _synced(vault, tmp_path)
    assert dm.needs_sync(vault, dm.load_links(vault)) == []
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "# narrowed by hand\n", encoding="utf-8")
    assert dm.needs_sync(vault, dm.load_links(vault)) == ["grok-personal"]  # uncommitted, HEAD unchanged


def test_a_sync_rescans_only_what_changed(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _synced(vault, tmp_path)
    scanned = _scans(monkeypatch)
    new = "knowledge-base/topics/misc/new.md"
    _commit(vault, new, "---\ntitle: New\n---\n\nnew\n", "one more note")
    assert dm.sync(vault, "grok-personal").pushed
    assert set(scanned) <= {new, "", "scout-config.yaml"} and new in scanned, scanned
    scanned.clear()
    assert dm.sync(vault, "grok-personal").unchanged
    assert set(scanned) <= {"", "scout-config.yaml"}, scanned


def test_a_new_hidden_note_rescans_everything_and_redacts_what_now_names_it(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mention = "knowledge-base/topics/misc/mention.md"
    _commit(vault, mention, "---\ntitle: Mention\n---\n\nPlanning with Zephyr Orchard.\nKept.\n", "mention")
    bare = _synced(vault, tmp_path)
    assert "Zephyr Orchard" in _show(bare, mention)
    scanned = _scans(monkeypatch)
    _commit(
        vault,
        "knowledge-base/people/zephyr-orchard.md",
        "---\nname: Zephyr Orchard\ntype: person\nvisibility: private\n---\n\nquiet\n",
        "a private note",
    )
    dm.sync(vault, "grok-personal")
    assert mention in scanned and len(set(scanned)) > 5  # the fingerprint changed: nothing reused
    assert _show(bare, mention) == "---\ntitle: Mention\n---\n\nKept.\n"


def test_check_tree_skips_only_blobs_it_checked_under_the_same_fingerprint(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _synced(vault, tmp_path)
    link = dm.load_links(vault)["grok-personal"]
    assert link.last_mirror and link.checked_fingerprint
    head = rs._head(vault)
    scanned = _scans(monkeypatch)
    dm.check_tree(vault, link.last_mirror, "grok-personal", head, trusted=(link.last_mirror, "another"))
    full = len(scanned)
    scanned.clear()
    fp = dm.check_tree(
        vault, link.last_mirror, "grok-personal", head, trusted=(link.last_mirror, link.checked_fingerprint)
    )
    assert fp == link.checked_fingerprint and len(scanned) == 0 < full
    current = dataclasses.replace(link, pending_mirror="abc")
    monkeypatch.setattr(dm, "remote_head", lambda *_a: "abc")
    adopted = dm._adopt_pending(vault, current)
    assert adopted.last_mirror == "abc" and adopted.checked_fingerprint is None  # checked under an unknown one


def test_a_memo_that_cant_be_used_falls_back_to_a_scan(vault: Path) -> None:  # noqa: F811
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        plain = dm.plan(vault, head, "grok-personal", blobs)[0]
        rel = PRIYA
        sha = rs._blob_at(vault, head, rel)
        assert sha is not None
        memo = dm.ScanMemo(vault / dm.SCAN_DIR / "x.json", plain.fingerprint, {rel: [sha.sha, "redact", "0" * 40, 1]})
        reused = dm.plan(vault, head, "grok-personal", blobs, memo=memo)[0]
    finally:
        blobs.close()
    assert [(f.path, f.data) for f in reused.files] == [(f.path, f.data) for f in plain.files]
    assert memo.get("other", rel, sha.sha) is None and memo.get(plain.fingerprint, rel, "nope") is None
    bad = vault / dm.SCAN_DIR / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    assert dm.ScanMemo.load(vault, "bad").files == {}


def test_gh_and_git_give_up_instead_of_hanging(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    def slow(_args: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("gh", dm.GH_TIMEOUT)

    with pytest.raises(rs.RepoSyncError) as e:
        dm._ask_gh(slow, ["api", "user"], "o/r")
    assert e.value.code == "remote_unverified" and "didn't answer" in e.value.message
    seen: dict[str, object] = {}

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        if args[0] == "git":
            raise subprocess.TimeoutExpired(args, kw["timeout"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(dm.subprocess, "run", fake_run)
    dm._gh(["api", "user"])
    assert seen["timeout"] == dm.GH_TIMEOUT
    with pytest.raises(rs.RepoSyncError) as e:
        dm.remote_head(vault, "/some/mirror.git")
    assert e.value.code == "remote_timeout" and seen["timeout"] == dm.GIT_TIMEOUT


def test_repeated_failures_keep_since_when(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _synced(vault, tmp_path)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")

    def unverified(*_a: object, **_k: object) -> None:
        raise rs.RepoSyncError("remote_unverified", "refused: gh isn't signed in")

    monkeypatch.setattr(dm, "check_remote", unverified)
    for _ in range(2):
        with pytest.raises(rs.RepoSyncError):
            dm.sync(vault, "grok-personal")
        time.sleep(1.1)
    error = dm.load_links(vault)["grok-personal"].last_error
    assert error is not None and error["first_at"] != error["at"]
    [finding] = health.delegate_findings(vault)
    assert finding.since == datetime.fromisoformat(error["first_at"].replace("Z", "+00:00"))
    links = dm.load_links(vault)
    links["grok-personal"].last_error = {**error, "code": "something_else"}
    dm.save_links(vault, links)
    with pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    again = dm.load_links(vault)["grok-personal"].last_error
    assert again is not None and again["first_at"] == error["first_at"]  # failing since: the whole streak
    links = dm.load_links(vault)
    links["grok-personal"].last_error = None
    dm.save_links(vault, links)
    with pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    fresh = dm.load_links(vault)["grok-personal"].last_error
    assert fresh is not None and fresh["first_at"] == fresh["at"]  # a new streak starts a new since


def test_an_unexpected_error_is_recorded_and_the_others_still_sync(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _link(vault, "duet", _bare(tmp_path, "duet"))
    real = dm._build_and_push

    def flaky(v: Path, link: dm.DelegateLink, **kw: object) -> object:
        if link.id == "grok-personal":
            raise ValueError("a bug")
        return real(v, link, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(dm, "_build_and_push", flaky)
    done, failed = dm.sync_all(vault)
    assert [r.delegate for r in done] == ["duet"]
    assert failed["grok-personal"].code == "internal" and "ValueError: a bug" in failed["grok-personal"].message
    error = dm.load_links(vault)["grok-personal"].last_error
    assert error is not None and error["code"] == "internal"


def test_run_health_flags_a_mirror_no_sync_has_tried(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _synced(vault, tmp_path)
    now = datetime.now(UTC)
    assert health.delegate_findings(vault, now=now + timedelta(hours=3)) == []  # behind? no: nothing changed
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    assert health.delegate_findings(vault, now=now + timedelta(minutes=30)) == []
    [finding] = health.delegate_findings(vault, now=now + timedelta(hours=2))
    assert finding.id == "delegate.sync_stalled" and finding.severity == "yellow" and finding.source == "state"


def test_a_sync_ignores_the_newer_state_files_for_an_older_link(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _synced(vault, tmp_path)
    gi = vault / ".gitignore"
    gi.write_text(
        "".join(
            ln + "\n"
            for ln in gi.read_text(encoding="utf-8").splitlines()
            if dm.TICK_REL not in ln and dm.SCAN_DIR not in ln
        ),
        encoding="utf-8",
    )
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    result = dm.sync(vault, "grok-personal")
    assert any("to .gitignore" in n for n in result.notes)
    assert dm.TICK_REL in gi.read_text(encoding="utf-8") and dm.SCAN_DIR in gi.read_text(encoding="utf-8")


def test_no_links_costs_no_git_and_a_future_tick_time_is_due(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_git(*_a: object) -> str:
        raise AssertionError("no links: no git")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dm, "_rev", no_git)
        assert dm.needs_sync(vault, {}) == []
        dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
        dm.save_links(vault, {})
        assert dm.due(vault) is False
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    tick = vault / dm.TICK_REL
    tick.touch()
    future = time.time() + 3600
    os.utime(tick, (future, future))
    assert dm.due(vault) is True  # a tick time ahead of the clock doesn't stall the throttle


def test_gh_missing_on_the_automatic_path_fails_closed(
    github_link: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pushed: list[object] = []
    monkeypatch.setattr(dm, "push", lambda *a, **_k: pushed.append(a))
    _commit(github_link, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")

    def missing(_args: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    done, failed = dm.sync_all(github_link, only_moved=True, gh=missing)
    assert done == [] and failed["grok-personal"].code == "remote_unverified" and pushed == []
    error = dm.load_links(github_link)["grok-personal"].last_error
    assert error is not None and error["code"] == "remote_unverified"


def test_the_engine_child_never_imports_from_the_vault(tmp_path: Path) -> None:
    from scout.engine_spawn import engine_argv, neutral_cwd

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    marker = tmp_path / "PWNED"
    (vault_dir / "typer.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    env = {**os.environ, "SCOUT_DATA_DIR": str(vault_dir)}
    ok = subprocess.run(engine_argv("version"), cwd=vault_dir, env=env, capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0 and not marker.exists()
    unsafe = [a for a in engine_argv("version") if a != "-P"]  # the old command, to prove the test can fail
    subprocess.run(unsafe, cwd=vault_dir, env=env, capture_output=True, text=True, timeout=60)
    assert marker.exists()
    assert not neutral_cwd().startswith(str(vault_dir))


def test_the_memo_reuses_left_out_and_redacted_files_as_they_were(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named = "notes/Sammy plans.md"  # its path names a hidden note: left out
    _commit(vault, named, "# Plans\n\nnothing private here\n", "named after")
    bare = _synced(vault, tmp_path)
    redacted = _show(bare, PRIYA)  # links [[sam]]: a redacted copy
    memo = dm.ScanMemo.load(vault, "grok-personal")
    assert memo.files[named][1] == "drop" and memo.files[PRIYA][1] == "redact"
    scanned = _scans(monkeypatch)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    result = dm.sync(vault, "grok-personal")
    assert PRIYA not in scanned and named not in scanned
    assert named not in set(_bare_files(bare)) and _show(bare, PRIYA) == redacted and result.left_out >= 1


def _bare_files(bare: Path) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"], check=True, capture_output=True, text=True
    ).stdout.splitlines()


def test_the_shared_reads_ledger_feeds_both_fingerprints(vault: Path) -> None:  # noqa: F811
    from scout.kb.privacy import load_policy
    from scout.kb.shared_reads import LEDGER_DIR

    policy = load_policy(vault)
    before = dm.sync_inputs(vault), dm.scan_fingerprint(vault, policy, [], [])
    ledger = vault / LEDGER_DIR
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "2026-11.jsonl").write_text("{}\n", encoding="utf-8")
    after = dm.sync_inputs(vault), dm.scan_fingerprint(vault, policy, [], [])
    assert before[0] != after[0] and before[1] != after[1]
    assert dm._stat_key(vault / "no-such-note.md") == "-"


def test_an_unreadable_failure_time_retries(vault: Path) -> None:  # noqa: F811
    link = dm.DelegateLink(
        id="d", remote="/x.git", linked_at="2026-11-14T00:00:00Z", account_unshared=True, last_attempt="garbled"
    )
    head, inputs = dm._rev(vault, "HEAD"), dm.sync_inputs(vault)
    link.last_error = {"code": "push_failed", "at": "garbled", "head": head or "", "inputs": inputs}
    assert dm._retry_due(link, head, inputs, datetime.now(UTC)) is True


# ----- re-review of #636 (coordinator, 2026-11-15): a planted memo or forged state never ships hidden content ---

INBOX = ".scout-inbox/phone-notifications-2026-10-01.jsonl"
NEW = "knowledge-base/topics/misc/new.md"


def _blob(vault: Path, rel: str) -> str:  # noqa: F811
    entry = rs._blob_at(vault, rs._head(vault), rel)
    assert entry is not None
    return entry.sha


def _plant_memo(vault: Path, files: dict[str, list[object]]) -> None:  # noqa: F811
    """Write the memo by hand, as anything that can write .scout-state could: unsigned."""
    fingerprint = dm.ScanMemo.load(vault, "grok-personal").fingerprint
    memo = vault / dm.SCAN_DIR / "grok-personal.json"
    memo.write_text(json.dumps({"fingerprint": fingerprint, "files": files}), encoding="utf-8")


def test_a_planted_memo_cant_redirect_a_file_to_the_inbox(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    bare = _synced(vault, tmp_path)
    _commit(vault, NEW, "---\ntitle: New\n---\n\nnew\n", "new")
    redirect = [_blob(vault, NEW), "redact", _blob(vault, INBOX), 1]
    _plant_memo(vault, {NEW: redirect})
    dm.sync(vault, "grok-personal")
    assert _show(bare, NEW) == "---\ntitle: New\n---\n\nnew\n"
    assert not any(INBOX_CANARY in text for text in _all_blobs(bare).values())


def test_a_signed_memo_that_redirects_is_still_not_trusted(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    """Even a memo signed with this computer's key (a bug, or a key holder) can't put another blob at a path."""
    bare = _synced(vault, tmp_path)
    _commit(vault, NEW, "---\ntitle: New\n---\n\nnew\n", "new")
    memo = dm.ScanMemo.load(vault, "grok-personal")
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        fingerprint = dm.plan(vault, head, "grok-personal", blobs)[0].fingerprint
    finally:
        blobs.close()
    forged = dm.ScanMemo(memo.path, delegate="grok-personal")
    forged.seen = {
        NEW: [_blob(vault, NEW), "redact", _blob(vault, INBOX), 1],
        PRIYA: [_blob(vault, PRIYA), "keep", _blob(vault, INBOX), 0],
    }
    forged.save(dm.Plan(delegate="grok-personal", fingerprint=fingerprint))
    assert dm.ScanMemo.load(vault, "grok-personal").files  # it verifies
    dm.sync(vault, "grok-personal")
    assert not any(INBOX_CANARY in text for text in _all_blobs(bare).values())
    assert "Sam" not in _show(bare, PRIYA) or "[[sam" not in _show(bare, PRIYA)


def _swapped(vault: Path, swap: dict[str, str]) -> tuple[str, str]:  # noqa: F811
    """A mirror commit of HEAD with some paths pointing at other blobs (by sha)."""
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    the_plan.files = [
        dm.MirrorFile(f.path, f.mode, None, swap[f.path]) if f.path in swap else f for f in the_plan.files
    ]
    return git(vault, "commit-tree", dm.write_tree(vault, the_plan), "-m", "x").strip(), head


def test_check_tree_refuses_a_blob_that_isnt_heads_at_that_path(vault: Path) -> None:  # noqa: F811
    _commit_files(
        vault,
        {
            NEW: b"---\ntitle: New\n---\n\nnew\nmore\n",
            "docs/report.pdf": b"%PDF-1.4\n\xe2\xe3\xcf\xd3\n\x00visible\n",
            "trading/secret.pdf": b"%PDF-1.4\n\xe2\xe3\xcf\xd3\n\x00TRADING-SECRET\n",
        },
        "files",
    )

    def blob(text: str) -> str:
        return rs.git(vault, "hash-object", "-w", "--stdin", input=text).stdout.strip()

    trimmed = blob("---\ntitle: New\n---\n\nmore\n")
    reordered = blob("more\n---\ntitle: New\n---\n\nnew\n")
    config = blob("privacy:\n  delegates:\n    grok-personal: {}\nx: y\n")
    for swap, ok in (
        ({NEW: trimmed}, True),  # lines removed, in order: what redaction makes
        ({NEW: reordered}, False),
        ({NEW: _blob(vault, INBOX)}, False),  # the reviewer's redirect
        ({"docs/report.pdf": _blob(vault, "trading/secret.pdf")}, False),  # a binary to another binary
        ({"scout-config.yaml": config}, False),  # a config holding what HEAD's doesn't
    ):
        commit, head = _swapped(vault, swap)
        if ok:
            dm.check_tree(vault, commit, "grok-personal", head)
            continue
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", head)
        assert e.value.code == "delegate_mirror_unsafe", swap


def test_a_forged_state_is_refused_so_a_newly_hidden_name_cant_stay(
    vault: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    mention = "knowledge-base/topics/misc/mention.md"
    _commit(vault, mention, "---\ntitle: Mention\n---\n\nPlanning with Zephyr Orchard.\nKept.\n", "mention")
    bare = _synced(vault, tmp_path)
    _commit(
        vault,
        "knowledge-base/people/zephyr-orchard.md",
        "---\nname: Zephyr Orchard\ntype: person\nvisibility: private\n---\n\nquiet\n",
        "a private note",
    )
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        new_fp = dm.plan(vault, head, "grok-personal", blobs)[0].fingerprint
    finally:
        blobs.close()
    state = json.loads(dm.state_path(vault).read_text(encoding="utf-8"))
    state["links"]["grok-personal"]["checked_fingerprint"] = new_fp  # the reviewer's forgery
    dm.state_path(vault).write_text(json.dumps(state), encoding="utf-8")
    _plant_memo(vault, {mention: [_blob(vault, mention), "keep", _blob(vault, mention), 0]})
    with pytest.raises(rs.RepoSyncError) as e:
        dm.sync(vault, "grok-personal")
    assert e.value.code == "state_unverified"
    assert "Zephyr Orchard" in _show(bare, mention)  # nothing was pushed from the forged state
    assert health.delegate_findings(vault)[0].id == "delegate.state_unreadable"


def test_derivation_and_matcher_code_rules() -> None:
    assert dm._derives(b"a\nc\n", b"a\nb\nc\n") and dm._derives(b"", b"x\n")
    assert not dm._derives(b"c\na\n", b"a\nb\nc\n") and not dm._derives(b"a\nz\n", b"a\nb\n")
    assert not dm._derives(b"\xff", b"\xff") and not dm._config_derives(b"\xff", b"a: 1\n", "d")
    assert not dm._config_derives(b"[1, 2]\n", b"a: 1\n", "d")
    assert dm._config_derives(b"privacy:\n  delegates:\n    d: {include_private: true}\n", b"a: 1\n", "d")
    assert len(dm._matcher_source()) == 64


def test_the_signing_key_missing_garbled_or_made_concurrently(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    key = dm.secrets_dir() / dm.STATE_KEY
    assert key.stat().st_mode & 0o777 == 0o600 and not str(key).startswith(str(vault))
    saved = key.read_text(encoding="utf-8")
    key.unlink()
    with pytest.raises(rs.RepoSyncError) as e:  # signed by a key this computer no longer has
        dm.load_links(vault)
    assert e.value.code == "state_unverified"
    key.write_text("not hex\n", encoding="utf-8")
    assert dm._state_key(create=False) is None
    key.unlink()
    real_open = dm.os.open

    def racing(path: object, flags: int, mode: int = 0o777) -> int:
        key.write_text(saved, encoding="utf-8")  # another process got there first
        raise FileExistsError(path)

    monkeypatch.setattr(dm.os, "open", racing)
    assert dm._state_key(create=True) == bytes.fromhex(saved.strip())
    monkeypatch.setattr(dm.os, "open", real_open)
    assert dm.load_links(vault)  # the original key again: the state verifies


def test_a_signed_state_of_the_wrong_shape_is_unreadable(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    key = dm._state_key(create=False)
    assert key is not None
    doc: dict[str, object] = {"schema_version": 1, "links": {"grok-personal": "not a link"}}
    doc["mac"] = dm._sign(key, "links", doc)
    dm.state_path(vault).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.load_links(vault)
    assert e.value.code == "state_unreadable"


def test_check_tree_refuses_a_changed_mode_or_symlink(vault: Path) -> None:  # noqa: F811
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    link = next(f for f in the_plan.files if f.mode == "120000")
    other = rs.git(vault, "hash-object", "-w", "--stdin", input="knowledge-base/people").stdout.strip()
    for swap in (
        {PRIYA: dm.MirrorFile(PRIYA, "100755", None, _blob(vault, PRIYA))},
        {link.path: dm.MirrorFile(link.path, link.mode, None, other)},
    ):
        p, _ = (the_plan, head)
        files = [swap.get(f.path, f) for f in p.files]
        planned = dataclasses.replace(p, files=files)
        commit = git(vault, "commit-tree", dm.write_tree(vault, planned), "-m", "x").strip()
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", head)
        assert e.value.code == "delegate_mirror_unsafe", list(swap)

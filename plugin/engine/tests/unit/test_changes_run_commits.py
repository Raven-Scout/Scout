"""The run-commit ledger (scout.changes.run_commits): a commit a run made is judged in the run's lane whatever its
subject says, the owner's own commits during a run stay interactive, and history from before the ledger still
classifies by subject prefix.

A scratch vault with real git and the per-test HOME (the owner store follows it, tests/conftest.py)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from scout.changes import run_commits
from scout.changes.actions import due, rollback
from scout.changes.models import Signal
from scout.changes.scan import classify, derive, lane_of, scan
from scout.changes.store import append_signal
from scout.changes.verdicts import is_tested
from scout.cli import app
from scout.sandbox import integrity

TZ = ZoneInfo("UTC")
NOW = datetime.now(UTC)
WORKTREE_BRANCH = "claude/session-a"


def _git(repo: Path, *args: str, date: datetime | None = None) -> str:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date.isoformat()
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str], *, date: datetime | None = None) -> str:
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, date=date or NOW - timedelta(days=1))
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.name", "Scout Test")
    _git(r, "config", "user.email", "scout@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    _commit(r, "seed", {"SKILL.md": "skill v1\n", "README.md": "readme\n"}, date=NOW - timedelta(days=60))
    return r


@pytest.fixture
def owner_env() -> dict[str, str]:
    return {"HOME": os.environ["HOME"]}


def _run(repo: Path, label: str, make: Callable[[], list[str]]) -> list[str]:
    """Snapshot, let ``make`` commit as a run would, then record the window as ``sandbox verify`` does."""
    snap = integrity.snapshot(repo, os.environ)
    shas = make()
    assert run_commits.record(repo, snap, label=label) is not None
    return shas


def _undo(repo: Path, env: dict[str, str], cid: str) -> None:
    append_signal(Signal(ts=NOW, kind="approval", surface="scoutctl", ref=f"undo:{cid}"), vault=repo, env=env)


# --- a run's commit is in the run's lane, whatever its subject -------------------------------------------------------


def test_a_run_commit_titled_fix_is_in_the_runs_lane_tested_and_undoable(repo: Path, owner_env: dict[str, str]) -> None:
    [sha] = _run(repo, "dreaming-nightly", lambda: [_commit(repo, "fix: tighten the wrap", {"SKILL.md": "skill v2\n"})])

    row = run_commits.read().commits[sha]
    assert (row.sha, row.mode, row.by) == (sha, "dreaming-nightly", "run") and row.ts is not None
    [change] = [c for c in scan(repo, since=None) if c.commit == sha]  # the seed commit is a change too
    assert (change.kind, change.author_lane) == ("brain-edit", "dreaming")
    assert is_tested(change)

    cid = change.id
    _undo(repo, owner_env, cid)
    assert [(r["id"], r["reason"]) for r in due(repo, owner_env, now=NOW, tz=TZ)["rollbacks"]] == [(cid, "owner-undo")]
    assert rollback(repo, owner_env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    assert (repo / "SKILL.md").read_text() == "skill v1\n"


def test_without_the_ledger_the_same_commit_is_the_owners(repo: Path, owner_env: dict[str, str]) -> None:
    """The bug: a `fix:` subject made a run's brain edit interactive, untested and never offered for undo."""
    sha = _commit(repo, "fix: tighten the wrap", {"SKILL.md": "skill v2\n"})
    [change] = [c for c in scan(repo, since=None) if c.commit == sha]
    assert change.author_lane == "interactive" and not is_tested(change)
    _undo(repo, owner_env, change.id)
    doc = due(repo, owner_env, now=NOW, tz=TZ)
    assert doc["rollbacks"] == []
    assert [(b["id"], b["why_blocked"]) for b in doc["blocked"]] == [(change.id, "not-a-run-change")]


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ('Revert "dreaming [22:12]: an older edit"', "brain-edit"),
        ("scout-update: refresh SKILL.md", "brain-edit"),  # not an upgrade: only the update lane makes one
        ("trading [15:31]: SKILL.md", "brain-edit"),  # not dropped as a trading session's commit
        ("dreaming [22:12]: SKILL.md self-edit", "brain-edit"),
    ],
)
def test_no_subject_takes_a_run_commit_out_of_its_lane(repo: Path, subject: str, kind: str) -> None:
    [sha] = _run(repo, "morning-briefing", lambda: [_commit(repo, subject, {"SKILL.md": "skill v2\n"})])
    change = derive(repo, [sha])[sha]  # type: ignore[index]
    assert change is not None and (change.kind, change.author_lane) == (kind, "briefing") and is_tested(change)


@pytest.mark.parametrize(
    ("mode", "subject", "lane"),
    [
        ("morning-briefing", "fix: x", "briefing"),
        ("weekend-consolidation", "fix: x", "consolidation"),
        ("dreaming-manual", "research [09:00]: x", "dreaming"),  # the runner's mode beats the run's own prefix
        ("research-manual", "fix: x", "research"),
        ("manual", "work [10:00]: x", "work"),  # a mode that names no lane takes a run lane from the prefix
        ("respond", "fix: x", "run"),
        ("respond", "scout-update: x", "run"),
    ],
)
def test_the_lane_comes_from_the_mode_then_the_prefix(mode: str, subject: str, lane: str) -> None:
    assert lane_of(subject, run_commits.RunCommit("0" * 40, mode, "run")) == lane


def test_classify_takes_the_lane_it_is_given() -> None:
    assert classify([("M", "SKILL.md")], "scout-update: x", lambda p: "") == "upgrade"
    assert classify([("M", "SKILL.md")], "scout-update: x", lambda p: "", lane="run") == "brain-edit"


def test_every_commit_of_the_run_is_recorded_on_every_branch_it_moved(repo: Path) -> None:
    def make() -> list[str]:
        a = _commit(repo, "notes: one", {"README.md": "r2\n"})
        _git(repo, "checkout", "-q", "-b", "side")
        b = _commit(repo, "fix: side", {"SKILL.md": "side\n"})
        _git(repo, "checkout", "-q", "main")
        return [a, b]

    a, b = _run(repo, "research", make)
    seed = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    assert set(run_commits.read().commits) == {a, b} and seed not in run_commits.read().commits


def test_a_detached_head_commit_is_recorded(repo: Path) -> None:
    def make() -> list[str]:
        _git(repo, "checkout", "-q", "--detach")
        return [_commit(repo, "fix: detached", {"SKILL.md": "d\n"})]

    [sha] = _run(repo, "research", make)
    assert run_commits.read().commits[sha].by == "run"


def test_a_run_that_committed_nothing_records_nothing(repo: Path) -> None:
    assert _run(repo, "research", lambda: []) == []
    assert run_commits.read() == run_commits.Ledger()
    assert not run_commits.path().exists()


def test_sandbox_verify_records_before_it_restores(repo: Path) -> None:
    """End to end through the CLI: the run's commit is recorded, the integrity check's restore commit is not."""
    protected = "scripts/claude-with-retry.sh"
    _commit(repo, "seed runner", {protected: "#!/bin/sh\necho start\n"}, date=NOW - timedelta(days=59))
    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(repo)])
    assert snap.exit_code == 0
    run_sha = _commit(repo, "fix: faster retry", {protected: "#!/bin/sh\necho changed\n"})

    result = runner.invoke(
        app, ["sandbox", "verify", "--vault", str(repo), "--label", "morning-briefing"], input=snap.stdout
    )

    assert result.exit_code == 10
    restore = _git(repo, "rev-parse", "HEAD")
    assert restore != run_sha and set(run_commits.read().commits) == {run_sha}
    got = derive(repo, [run_sha, restore])
    assert got is not None
    assert (got[run_sha].kind, got[run_sha].author_lane) == ("driver", "briefing")  # type: ignore[union-attr]
    assert got[restore].author_lane == "interactive"  # type: ignore[union-attr]


# --- the owner's own commits during a run stay interactive ----------------------------------------------------------


def _worktree(repo: Path) -> Path:
    wt = repo.parent / "session-a"
    _git(repo, "worktree", "add", "-q", "-b", WORKTREE_BRANCH, str(wt))
    return wt


def test_an_owner_worktree_commit_during_an_enforced_run_stays_interactive(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")
    wt = _worktree(repo)

    def make() -> list[str]:
        owner = _commit(wt, "fix: owner's own edit", {"SKILL.md": "owner\n"})
        run = _commit(repo, "fix: run's edit", {"README.md": "run\n", "SKILL.md": "run\n"})
        return [owner, run]

    owner, run = _run(repo, "dreaming-nightly", make)

    ledger = run_commits.read().commits
    assert (ledger[owner].by, ledger[run].by) == ("worktree", "run")
    got = derive(repo, [owner, run])
    assert got is not None
    assert got[owner].author_lane == "interactive" and not is_tested(got[owner])  # type: ignore[arg-type,union-attr]
    assert got[run].author_lane == "dreaming" and is_tested(got[run])  # type: ignore[arg-type,union-attr]


@pytest.mark.parametrize("status", [None, "off", "unavailable"])
def test_without_an_enforced_sandbox_no_worktree_vouches(
    repo: Path, monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    """As in the integrity check: without the sandbox the run could have written the worktree's index itself."""
    if status is None:
        monkeypatch.delenv("SCOUT_SANDBOX_STATUS", raising=False)
    else:
        monkeypatch.setenv("SCOUT_SANDBOX_STATUS", status)
    wt = _worktree(repo)
    [sha] = _run(repo, "dreaming-nightly", lambda: [_commit(wt, "fix: x", {"SKILL.md": "x\n"})])
    assert run_commits.read().commits[sha].by == "run"


def test_a_run_moving_a_worktrees_branch_is_still_the_run(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run can move another worktree's branch, but not that worktree's index: no vouch."""
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")
    _worktree(repo)

    def make() -> list[str]:
        idx = {**os.environ, "GIT_INDEX_FILE": str(repo.parent / "planted-index")}
        tip = _git(repo, "rev-parse", f"refs/heads/{WORKTREE_BRANCH}")
        blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="planted\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()  # fmt: skip
        for args in (["read-tree", tip], ["update-index", "--cacheinfo", f"100644,{blob},SKILL.md"]):
            subprocess.run(["git", "-C", str(repo), *args], env=idx, check=True, capture_output=True)
        tree = subprocess.run(
            ["git", "-C", str(repo), "write-tree"], env=idx, check=True, capture_output=True, text=True
        ).stdout.strip()
        planted = _git(repo, "commit-tree", tree, "-p", tip, "-m", "fix: planted")
        _git(repo, "update-ref", f"refs/heads/{WORKTREE_BRANCH}", planted, tip)
        return [planted]

    [sha] = _run(repo, "dreaming-nightly", make)
    assert run_commits.read().commits[sha].by == "run"


def test_a_worktree_on_the_vaults_own_branch_never_vouches(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")
    wt = repo.parent / "same-branch"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    _git(wt, "checkout", "-q", "--ignore-other-worktrees", "main")
    [sha] = _run(repo, "dreaming-nightly", lambda: [_commit(wt, "fix: x", {"SKILL.md": "x\n"})])
    assert run_commits.read().commits[sha].by == "run"


# --- history from before the ledger keeps its prefix ---------------------------------------------------------------


def test_history_from_before_the_ledger_classifies_by_prefix(repo: Path) -> None:
    old_owner = _commit(repo, "fix: owner edit", {"SKILL.md": "v2\n"}, date=NOW - timedelta(days=30))
    old_run = _commit(repo, "dreaming [22:12]: SKILL.md self-edit", {"SKILL.md": "v3\n"}, date=NOW - timedelta(days=29))
    old_update = _commit(repo, "scout-update: v0.18.0", {"SKILL.md": "v4\n"}, date=NOW - timedelta(days=28))
    old_trading = _commit(repo, "trading [15:31]: nav", {"SKILL.md": "v5\n"}, date=NOW - timedelta(days=27))
    # The ledger starts later: this run's commit is in it, the older ones are not.
    [new] = _run(repo, "research", lambda: [_commit(repo, "fix: research edit", {"SKILL.md": "v6\n"})])

    got = derive(repo, [old_owner, old_run, old_update, old_trading, new])
    assert got is not None
    lanes = {sha: (c.kind, c.author_lane) if c else None for sha, c in got.items()}
    assert lanes == {
        old_owner: ("brain-edit", "interactive"),
        old_run: ("brain-edit", "dreaming"),
        old_update: ("upgrade", "update"),
        old_trading: None,
        new: ("brain-edit", "research"),
    }


# --- the ledger file -----------------------------------------------------------------------------------------------


def _write_rows(*rows: object) -> None:
    p = run_commits.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


def test_a_run_row_wins_over_a_worktree_row_in_either_order() -> None:
    a, b = "a" * 40, "b" * 40
    _write_rows(
        {"schema": 1, "sha": a, "mode": "research", "by": "worktree"},
        {"schema": 1, "sha": a, "mode": "research", "by": "run"},
        {"schema": 1, "sha": b, "mode": "research", "by": "run"},
        {"schema": 1, "sha": b, "mode": "research", "by": "worktree"},
    )
    assert {s: c.by for s, c in run_commits.read().commits.items()} == {a: "run", b: "run"}


def test_bad_rows_are_skipped_and_a_bad_mode_is_unknown() -> None:
    good = "c" * 40
    _write_rows(
        "not json",
        ["a list"],
        {"schema": 2, "sha": "d" * 40, "mode": "research", "by": "run"},
        {"schema": 1, "sha": "HEAD", "mode": "research", "by": "run"},
        {"schema": 1, "sha": "f" * 40, "mode": "research", "by": "owner"},
        {"schema": 1, "window": "x", "mode": "research"},
        {"schema": 1, "window": {"tips": [], "known": []}, "mode": "research"},
        {"schema": 1, "window": {"tips": ["HEAD"], "known": []}, "mode": "research"},
        {"schema": 1, "window": {"tips": ["a" * 40], "known": ["--all"]}, "mode": "research"},
        {"schema": 1, "sha": good, "mode": "--evil Mode", "by": "run", "ts": "yesterday"},
        {"schema": 1, "sha": "e" * 40, "mode": 7, "by": "run", "ts": "2026-11-17T10:00:00"},
    )
    assert run_commits.read() == run_commits.Ledger(
        {
            good: run_commits.RunCommit(good, "unknown", "run"),
            "e" * 40: run_commits.RunCommit("e" * 40, "unknown", "run"),
        }
    )
    assert run_commits.mode_lane("unknown") == "run"


def test_an_unreadable_ledger_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Path:
        raise OSError("no")

    monkeypatch.setattr(run_commits, "path", boom)
    assert run_commits.read() == run_commits.Ledger()


def test_record_never_raises(repo: Path, tmp_path: Path) -> None:
    assert run_commits.record(repo, {"repo": True}, label="research") is None  # a snapshot missing its keys
    assert run_commits.record(tmp_path / "missing", integrity.snapshot(repo, os.environ), label="x") is None
    assert run_commits.window(tmp_path, {"repo": False}, os.environ, label="x") == ([], [])


def test_a_branch_with_no_start_commit_is_compared_with_its_whole_tree(repo: Path) -> None:
    """An unborn vault at the snapshot has no base to diff against: every path in the tip counts, and with no
    worktree recorded in the snapshot nothing vouches."""
    tip = _git(repo, "rev-parse", "HEAD")
    assert not run_commits._vouched(repo, {"worktrees": []}, "refs/heads/main", tip, None, os.environ)


# --- the review's bypasses (#768): each one used to leave a run's `fix:` brain commit interactive ------------------


def _lane(repo: Path, sha: str) -> str | None:
    got = derive(repo, [sha])
    assert got is not None and got[sha] is not None
    return got[sha].author_lane  # type: ignore[union-attr]


def test_a_branch_ref_at_a_blob_doesnt_wipe_the_runs_ledger(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")

    def make() -> list[str]:
        sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
        blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="hi\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()  # fmt: skip
        (repo / ".git" / "refs" / "heads" / "zz").write_text(blob + "\n")
        return [sha]

    [sha] = _run(repo, "dreaming-nightly", make)
    assert run_commits.read().commits[sha].by == "run"
    assert _lane(repo, sha) == "dreaming"


def test_a_vouch_that_cant_be_read_is_no_vouch(repo: Path) -> None:
    tip = _git(repo, "rev-parse", "HEAD")
    assert not run_commits._vouched(repo, {"worktrees": []}, "refs/heads/main", tip, "f" * 40, os.environ)
    assert not run_commits._vouched(repo, {}, "refs/heads/main", "not-a-commit", None, os.environ)


def test_a_commit_staged_on_a_tag_in_one_run_is_still_that_runs(repo: Path) -> None:
    def stage() -> list[str]:
        _git(repo, "checkout", "-q", "--detach")
        sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
        _git(repo, "tag", "t", sha)
        _git(repo, "checkout", "-q", "main")
        return [sha]

    [sha] = _run(repo, "dreaming-nightly", stage)
    assert run_commits.read().commits[sha].by == "run"  # the tag is a tip, so run 1 records it itself

    def land() -> list[str]:
        _git(repo, "merge", "-q", "--ff-only", "t")
        return []

    _run(repo, "dreaming-nightly", land)
    assert _git(repo, "rev-parse", "HEAD") == sha and _lane(repo, sha) == "dreaming"


def test_an_annotated_tag_is_peeled_to_its_commit(repo: Path) -> None:
    def make() -> list[str]:
        _git(repo, "checkout", "-q", "--detach")
        sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
        _git(repo, "tag", "-a", "-m", "x", "t2", sha)
        _git(repo, "checkout", "-q", "main")
        return [sha]

    [sha] = _run(repo, "research", make)
    assert run_commits.read().commits[sha].by == "run"


def test_past_the_cap_the_whole_window_is_the_runs(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_commits, "MAX_PER_RUN", 3)

    def make() -> list[str]:
        sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
        for i in range(3):
            _git(repo, "commit", "-q", "--allow-empty", "-m", f"chore {i}")
        return [sha]

    snap = integrity.snapshot(repo, os.environ)
    [sha] = make()
    recorded = run_commits.record(repo, snap, label="dreaming-nightly")
    assert recorded == run_commits.Recorded(commits=0, windows=1)
    ledger = run_commits.read()
    assert ledger.commits == {} and ledger.windows[0].tips == (_git(repo, "rev-parse", "HEAD"),)
    assert _lane(repo, sha) == "dreaming"
    seed = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    assert _lane(repo, seed) == "interactive"  # what the snapshot held is not in the window


def test_a_window_git_cant_read_holds_nothing(repo: Path) -> None:
    _write_rows({"schema": 1, "window": {"tips": ["a" * 40], "known": []}, "mode": "research", "ts": NOW.isoformat()})
    sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
    assert _lane(repo, sha) == "interactive"


# --- run-writable bookkeeping can't hide the run's commits -----------------------------------------------------------


def test_a_watermark_the_run_moved_past_its_commit_still_has_it_judged(repo: Path) -> None:
    from scout.changes.scan import scan_new
    from scout.changes.store import write_scan_mark

    scan_new(repo, now=NOW)
    [sha] = _run(repo, "dreaming-nightly", lambda: [_commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})])
    write_scan_mark(repo, sha, NOW)  # the run's own write: the runner's scan would read an empty range

    [change] = scan_new(repo, now=NOW)
    assert (change.commit, change.author_lane) == (sha, "dreaming")
    assert scan_new(repo, now=NOW) == []  # once each


def test_a_truncated_changes_ledger_gets_the_runs_commits_back(repo: Path) -> None:
    from scout.changes.scan import scan_new
    from scout.changes.store import CHANGES_NAME

    [sha] = _run(repo, "dreaming-nightly", lambda: [_commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})])
    scan_new(repo, now=NOW)
    (repo / ".scout-logs" / CHANGES_NAME).write_text("")
    assert [c.commit for c in scan_new(repo, now=NOW)] == [sha]


def test_only_recent_run_commits_in_heads_history_are_read_back(repo: Path) -> None:
    from scout.changes.scan import scan_new

    scan_new(repo, now=NOW)

    def side() -> list[str]:
        _git(repo, "checkout", "-q", "-b", "side")
        sha = _commit(repo, "fix: side", {"SKILL.md": "side\n"})
        _git(repo, "checkout", "-q", "main")
        return [sha]

    _run(repo, "research", side)
    old = _commit(repo, "fix: old", {"SKILL.md": "old\n"})
    from scout.changes.store import _append

    row = {"schema": 1, "sha": old, "mode": "research", "by": "run", "ts": (NOW - timedelta(days=30)).isoformat()}
    _append(run_commits.path(), row)  # appended, so the side branch's row stays
    from scout.changes.store import write_scan_mark

    write_scan_mark(repo, old, NOW)
    assert scan_new(repo, now=NOW) == []  # the side branch isn't in effect; the old row is past the window


# --- sandbox verify reports a ledger it couldn't write ---------------------------------------------------------------


def test_verify_exits_12_when_a_clean_runs_commits_couldnt_be_recorded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(repo)])
    _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
    monkeypatch.setattr(run_commits, "record", lambda *a, **k: None)
    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)
    assert result.exit_code == 12
    assert "couldn't be recorded for the changes map" in result.stdout


def test_verify_keeps_a_restore_and_says_the_ledger_failed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protected = "scripts/claude-with-retry.sh"
    _commit(repo, "seed runner", {protected: "#!/bin/sh\n"}, date=NOW - timedelta(days=59))
    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(repo)])
    _commit(repo, "fix: faster retry", {protected: "#!/bin/sh\necho x\n"})
    monkeypatch.setattr(run_commits, "record", lambda *a, **k: None)
    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)
    assert result.exit_code == 10
    assert "integrity check: this run's commits couldn't be recorded for the changes map" in result.stdout


def test_verify_says_what_it_recorded(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(repo)])
    _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)
    assert result.exit_code == 0 and "changes map: recorded 1 commit this run made" in result.stdout
    monkeypatch.setattr(run_commits, "record", lambda *a, **k: run_commits.Recorded(0, 1))
    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)
    assert "recorded 0 commits this run made, and 1 window too big to list" in result.stdout


def test_a_tip_git_cant_list_is_recorded_as_a_window(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = integrity._git

    def flaky(vault: Path, args: list[str], env: object, **kw: object) -> str:
        if args[0] == "rev-list":
            raise integrity.IntegrityError("rev-list failed")
        return real(vault, args, env, **kw)  # type: ignore[arg-type]

    snap = integrity.snapshot(repo, os.environ)
    sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
    monkeypatch.setattr(integrity, "_git", flaky)
    assert run_commits.record(repo, snap, label="research") == run_commits.Recorded(commits=0, windows=1)
    monkeypatch.setattr(integrity, "_git", real)
    assert _lane(repo, sha) == "research"

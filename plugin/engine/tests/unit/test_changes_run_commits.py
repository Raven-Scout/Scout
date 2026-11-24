"""The run-commit ledger (scout.changes.run_commits): a commit a run made is judged in the run's lane whatever its
subject says, the owner's own commits during a run stay interactive, and history from before the ledger still
classifies by subject prefix.

A scratch vault with real git and the per-test HOME (the owner store follows it, tests/conftest.py)."""

from __future__ import annotations

import json
import os
import shutil
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


def _snap(repo: Path) -> dict:
    """The start snapshot `scoutctl sandbox snapshot` takes: the integrity check's, plus the ledger's extra."""
    snap = integrity.snapshot(repo, os.environ)
    snap["changes"] = run_commits.snapshot_extra(repo)
    return snap


def _run(repo: Path, label: str, make: Callable[[], list[str]]) -> list[str]:
    """Snapshot, let ``make`` commit as a broker-less run would, then record the git window as ``sandbox verify``
    does."""
    snap = _snap(repo)
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
        },
        named=frozenset({good, "e" * 40, "f" * 40}),  # an unknown `by` is still a sha a row names
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

    snap = _snap(repo)
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
    assert result.exit_code == 0
    assert "no commit broker, so 1 commit this run made was worked out from git (best effort)" in result.stdout
    monkeypatch.setattr(run_commits, "record", lambda *a, **k: run_commits.Recorded(0, 1))
    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)
    assert "0 commits this run made were worked out from git (best effort), and 1 window too big" in result.stdout


def test_a_tip_git_cant_list_is_recorded_as_a_window(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = integrity._git

    def flaky(vault: Path, args: list[str], env: object, **kw: object) -> str:
        if args[0] == "rev-list":
            raise integrity.IntegrityError("rev-list failed")
        return real(vault, args, env, **kw)  # type: ignore[arg-type]

    snap = _snap(repo)
    sha = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
    monkeypatch.setattr(integrity, "_git", flaky)
    assert run_commits.record(repo, snap, label="research") == run_commits.Recorded(commits=0, windows=1)
    monkeypatch.setattr(integrity, "_git", real)
    assert _lane(repo, sha) == "research"


# --- the broker's record (exact, Phase 1d) ---------------------------------------------------------------------------

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


@pytest.fixture
def helper_vault(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The scratch vault with the real git-safe-commit.sh rendered into scripts/, as /scout-update installs it."""
    from scout.scripts.phase_assembly import render_template

    helper = repo / "scripts" / "git-safe-commit.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    template = (TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl").read_text()
    helper.write_text(render_template(template, {"SCOUT_DIR": str(repo)}))
    helper.chmod(0o755)
    (repo / ".gitignore").write_text(".scout-cache/\n.scout-logs/\n.scout-state/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "helper", "--no-verify")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(repo))
    monkeypatch.setenv("SCOUT_MODE", "dreaming-nightly")
    return repo


BROKER_RUN = "scout-broker.abc123"


def _live_broker(vault: Path):  # noqa: ANN202
    """A run's live broker whose `broker profile` handed over the tightened profile."""
    from scout.sandbox import broker

    assert run_commits.mark_broker_profile(vault, BROKER_RUN, tightened=True)
    return broker.Broker.for_vault(vault, run_id=BROKER_RUN)


def _broker_commit(vault: Path, message: str, rel: str, text: str) -> str:
    (vault / rel).write_text(text)
    reply = _live_broker(vault).handle({"v": 1, "verb": "commit", "message": message, "paths": [rel]})
    assert reply.exit == 0, reply.stderr
    return _git(vault, "rev-parse", "HEAD")


def _broker_row(vault: Path, sha: str, mode: str = "research", run: str = BROKER_RUN) -> None:
    """What a tightened run's `broker profile` and `Broker.commit` write around a commit that landed: the profile row,
    a begin row, then an end row with its sha."""
    assert run_commits.mark_broker_profile(vault, run, tightened=True)
    rid = run_commits.broker_begin(vault, run, mode)
    assert rid is not None
    assert run_commits.broker_end(vault, rid, run, mode, exit_code=0, sha=sha)


def test_the_broker_records_the_exact_commit_it_made(helper_vault: Path) -> None:
    sha = _broker_commit(helper_vault, "fix: tighten the wrap", "SKILL.md", "brokered\n")
    ledger = run_commits.read()
    row = ledger.commits[sha]
    assert (row.by, row.mode) == ("broker", "dreaming-nightly") and ledger.gaps == ()
    assert run_commits.is_broker_recorded(sha)
    assert _lane(helper_vault, sha) == "dreaming"


def test_an_owner_commit_racing_the_runs_never_takes_its_place(helper_vault: Path) -> None:
    """The coordinator's requirement: the sha is the commit git just made, never HEAD read afterwards. An owner
    commit lands between the run's commit and the sha read (a post-commit hook commits as the owner, from its own
    process: no nonce); the broker still records the run's commit, and only it."""
    hook = helper_vault / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/bash\nunset GIT_REFLOG_ACTION\n"
        'cd "$(git rev-parse --show-toplevel)" && echo owner >> README.md && '
        "git -c core.hooksPath=/dev/null commit -q -m 'owner: racing edit' -- README.md\n"
    )
    hook.chmod(0o755)

    (helper_vault / "SKILL.md").write_text("run\n")
    reply = _live_broker(helper_vault).handle(
        {"v": 1, "verb": "commit", "message": "fix: run's edit", "paths": ["SKILL.md"]}
    )
    assert reply.exit == 0, reply.stderr
    owner = _git(helper_vault, "rev-parse", "HEAD")
    run = _git(helper_vault, "rev-parse", "HEAD~1")
    assert _git(helper_vault, "log", "-1", "--format=%s", owner) == "owner: racing edit"
    assert set(run_commits.read().commits) == {run}


def test_a_hook_that_commits_with_the_nonce_still_leaves_the_runs_commit_named(helper_vault: Path) -> None:
    hook = helper_vault / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/bash\n"  # inherits GIT_REFLOG_ACTION: its own commit's reflog entry carries the nonce too
        'cd "$(git rev-parse --show-toplevel)" && echo hook >> README.md && '
        "git -c core.hooksPath=/dev/null commit -q -m 'hook: after' -- README.md\n"
    )
    hook.chmod(0o755)
    _broker_commit(helper_vault, "fix: run's edit", "SKILL.md", "run\n")
    assert set(run_commits.read().commits) == {_git(helper_vault, "rev-parse", "HEAD~1")}


def test_the_helper_names_its_sha_last_on_stdout(helper_vault: Path) -> None:
    (helper_vault / "SKILL.md").write_text("direct\n")
    out = subprocess.run(
        ["bash", str(helper_vault / "scripts" / "git-safe-commit.sh"), "notes: direct", "SKILL.md"],
        capture_output=True, text=True, check=True, env={**os.environ, "SCOUT_RUN_BROKER": ""},
    )  # fmt: skip
    assert run_commits.sha_from_helper(out.stdout) == ("sha", _git(helper_vault, "rev-parse", "HEAD"))
    assert run_commits.sha_from_helper("scout-commit unresolved\n") == ("unresolved", None)
    assert run_commits.sha_from_helper("scout-commit none\n") == ("none", None)
    assert run_commits.sha_from_helper("") == ("silent", None)
    assert run_commits.sha_from_helper("scout-commit HEAD\n") == ("silent", None)


def test_a_helper_without_a_reflog_says_unresolved_and_the_commit_is_a_gap(helper_vault: Path) -> None:
    _git(helper_vault, "config", "core.logAllRefUpdates", "false")
    for log in (helper_vault / ".git" / "logs").rglob("*"):
        if log.is_file():
            log.unlink()
    sha = _broker_commit(helper_vault, "fix: x", "SKILL.md", "x\n")
    ledger = run_commits.read()
    assert sha not in ledger.commits
    [gap] = ledger.gaps
    assert gap.mode == "dreaming-nightly"


def test_a_helper_from_before_the_sha_line_leaves_a_gap_not_a_guess(helper_vault: Path) -> None:
    old = helper_vault / "scripts" / "git-safe-commit.sh"
    old.write_text(old.read_text().replace('echo "scout-commit ${SHA:-unresolved}"', ""))
    sha = _broker_commit(helper_vault, "fix: old helper", "SKILL.md", "old\n")
    ledger = run_commits.read()
    assert sha not in ledger.commits and len(ledger.gaps) == 1


def test_a_failed_commit_is_no_gap(helper_vault: Path) -> None:

    reply = _live_broker(helper_vault).handle(
        {"v": 1, "verb": "commit", "message": "fix: nothing", "paths": ["SKILL.md"]}
    )
    assert reply.exit == 4  # nothing to commit
    assert run_commits.read().gaps == ()


def test_a_begin_with_no_end_is_a_gap(repo: Path) -> None:
    assert run_commits.broker_begin(repo, "scout-broker.abc123", "research") is not None
    [gap] = run_commits.broker_gaps(repo, NOW - timedelta(days=1))
    assert (gap.run, gap.mode) == ("scout-broker.abc123", "research")
    assert run_commits.broker_gaps(repo.parent, NOW - timedelta(days=1)) == []


def test_a_broker_that_cant_record_says_so_and_the_commit_stands(
    helper_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run_commits, "broker_end", lambda *a, **k: False)
    sha = _broker_commit(helper_vault, "fix: x", "SKILL.md", "x\n")
    assert "couldn't record the commit for the changes map" in capsys.readouterr().err
    ledger = run_commits.read()
    assert sha not in ledger.commits and len(ledger.gaps) == 1  # the begin row says a commit went missing


def test_recording_never_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Path:
        raise OSError("no")

    monkeypatch.setattr(run_commits, "path", boom)
    assert run_commits.broker_begin(repo, "r", "research") is None
    assert not run_commits.broker_end(repo, "x", "r", "research", exit_code=0, sha="a" * 40)
    assert not run_commits.mark_broker_profile(repo, "r", tightened=True)


def test_a_brokered_run_records_no_window_so_an_owner_commit_in_the_vault_stays_the_owners(repo: Path) -> None:
    """The known limit the broker removes: under the git window an owner commit made in the vault's own checkout
    during a run was the run's. With the broker on, verify records nothing from git."""
    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(repo)])
    assert run_commits.mark_broker_profile(repo, "scout-broker.abc123", tightened=True)
    owner = _commit(repo, "fix: the owner's own edit", {"SKILL.md": "owner\n"})

    result = runner.invoke(app, ["sandbox", "verify", "--vault", str(repo), "--label", "research"], input=snap.stdout)

    assert result.exit_code == 0 and "recorded by its commit broker" in result.stdout
    assert owner not in run_commits.read().commits
    assert _lane(repo, owner) == "interactive"


def test_a_brokered_run_with_a_gap_also_records_the_git_window(repo: Path) -> None:
    snap = _snap(repo)
    assert run_commits.mark_broker_profile(repo, "scout-broker.abc123", tightened=True)
    assert run_commits.broker_begin(repo, "scout-broker.abc123", "research")  # the end row was lost
    assert not run_commits.broker_was_on(repo, snap)


def test_broker_was_on_needs_every_broker_since_the_snapshot_tightened(repo: Path) -> None:
    assert run_commits.mark_broker_profile(repo, "earlier", tightened=True)
    snap = _snap(repo)
    assert not run_commits.broker_was_on(repo, snap)  # an earlier run's mark doesn't count
    assert run_commits.mark_broker_profile(repo, "scout-broker.abc123", tightened=True)
    assert run_commits.broker_was_on(repo, snap)
    assert not run_commits.broker_was_on(repo.parent, snap)  # another vault's
    assert run_commits.mark_broker_profile(repo, "scout-broker.def456", tightened=False)  # one fell back to 1c
    assert not run_commits.broker_was_on(repo, snap)
    runs = run_commits.broker_runs(repo, NOW - timedelta(days=1))
    assert [(r.run, r.tightened) for r in runs] == [
        ("earlier", True), ("scout-broker.abc123", True), ("scout-broker.def456", False),
    ]  # fmt: skip
    assert not run_commits.broker_was_on(repo, {"changes": {}})  # a snapshot from before the extra
    assert not run_commits.broker_was_on(repo, {})


@pytest.mark.parametrize("ok", [True, False])
def test_broker_profile_marks_the_run_either_way(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ok: bool
) -> None:
    from scout.sandbox import broker

    def build(*_a: object, **_k: object) -> str:
        if not ok:
            raise broker.BrokerError("no socket")
        return "(version 1)\n"

    monkeypatch.setattr(broker, "build_profile", build)
    snap = _snap(repo)
    bdir = tmp_path / "scout-broker.abc123"
    result = CliRunner().invoke(app, ["broker", "profile", "--vault", str(repo), "--dir", str(bdir)], input="")
    assert result.exit_code == (0 if ok else 1), result.output
    [r] = run_commits.broker_runs(repo, NOW - timedelta(days=1))
    assert (r.run, r.tightened) == ("scout-broker.abc123", ok)
    assert run_commits.broker_was_on(repo, snap) is ok


def test_bare_broker_rows_are_never_trusted() -> None:
    _write_rows({"schema": 1, "sha": "a" * 40, "mode": "research", "by": "broker", "ts": NOW.isoformat()})
    assert run_commits.read().commits == {}


# --- round 2 of the review: the git window's hardening (best effort, not sound) --------------------------------------


def test_head_pointed_at_a_ref_outside_refs_heads_is_still_a_tip(repo: Path) -> None:
    def make() -> list[str]:
        _git(repo, "update-ref", "refs/x/main", "HEAD")
        _git(repo, "symbolic-ref", "HEAD", "refs/x/main")
        return [_commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})]

    [sha] = _run(repo, "dreaming-nightly", make)
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    assert run_commits.read().commits[sha].by == "run"


def test_a_planted_shallow_file_hides_nothing(repo: Path) -> None:
    def make() -> list[str]:
        hidden = _commit(repo, "fix: brain edit", {"SKILL.md": "evil\n"})
        harmless = _commit(repo, "notes: t", {"README.md": "t\n"})
        (repo / ".git" / "shallow").write_text(harmless + "\n")
        return [hidden, harmless]

    hidden, harmless = _run(repo, "dreaming-nightly", make)
    assert {hidden, harmless} <= set(run_commits.read().commits)
    (repo / ".git" / "shallow").unlink()
    assert _lane(repo, hidden) == "dreaming"


def test_a_worktree_vouches_for_one_new_commit_on_its_snapshot_tip_only(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2: a run moved the worktree's branch to B (the old tip's tree) with parent A, a brain edit; the net
    diff was empty, so A and B were both vouched. Two new commits are the run's."""
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")
    _worktree(repo)

    def make() -> list[str]:
        tip = _git(repo, "rev-parse", f"refs/heads/{WORKTREE_BRANCH}")
        tree = _git(repo, "rev-parse", f"{tip}^{{tree}}")
        blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="evil\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()  # fmt: skip
        idx = {**os.environ, "GIT_INDEX_FILE": str(repo.parent / "planted-index")}
        for args in (["read-tree", tip], ["update-index", "--cacheinfo", f"100644,{blob},SKILL.md"]):
            subprocess.run(["git", "-C", str(repo), *args], env=idx, check=True, capture_output=True)
        a_tree = subprocess.run(
            ["git", "-C", str(repo), "write-tree"], env=idx, check=True, capture_output=True, text=True
        ).stdout.strip()
        a = _git(repo, "commit-tree", a_tree, "-p", tip, "-m", "fix: a")
        b = _git(repo, "commit-tree", tree, "-p", a, "-m", "fix: b")
        _git(repo, "update-ref", f"refs/heads/{WORKTREE_BRANCH}", b, tip)
        return [a, b]

    a, b = _run(repo, "dreaming-nightly", make)
    ledger = run_commits.read().commits
    assert (ledger[a].by, ledger[b].by) == ("run", "run")


def test_a_replace_ref_hides_nothing_at_record_or_read_time(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_commits, "MAX_PER_RUN", 1)
    seed = _git(repo, "rev-list", "--max-parents=0", "HEAD")

    def make() -> list[str]:
        x = _commit(repo, "fix: tweak", {"SKILL.md": "evil\n"})
        _commit(repo, "chore", {"README.md": "c\n"})
        _git(repo, "replace", "--graft", "HEAD", seed)  # HEAD's parent is now the seed, to anything that honours it
        return [x]

    [x] = _run(repo, "dreaming-nightly", make)
    assert len(run_commits.read().windows) == 1
    assert _lane(repo, x) == "dreaming"


def test_a_known_commit_gone_by_the_end_of_the_run_is_refused_not_ignored(repo: Path) -> None:
    """Round 2: pruning a commit the run started with made rev-list fail, and the window that replaced the list
    dropped the exclusion: all history became the run's, the owner's own edits included."""
    owner = _commit(repo, "fix: owner edit", {"SKILL.md": "owner\n"})
    snap = _snap(repo)
    _git(repo, "reset", "-q", "--soft", "HEAD~1")
    _commit(repo, "fix: run edit", {"SKILL.md": "run\n"})
    _git(repo, "reflog", "expire", "--expire=now", "--all")
    _git(repo, "gc", "-q", "--prune=now")
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", owner], check=False).returncode != 0
    assert run_commits.record(repo, snap, label="research") is None
    assert run_commits.read() == run_commits.Ledger()


def test_a_window_with_a_known_commit_gone_since_is_bounded_by_its_snapshot_time(repo: Path) -> None:
    old = _commit(repo, "fix: owner edit long ago", {"SKILL.md": "old\n"}, date=NOW - timedelta(days=30))
    new = _commit(repo, "fix: run edit", {"SKILL.md": "new\n"}, date=NOW)
    head = _git(repo, "rev-parse", "HEAD")
    window = {"tips": [head], "known": ["f" * 40], "taken_at": (NOW - timedelta(hours=1)).isoformat()}
    _write_rows({"schema": 1, "window": window, "mode": "research", "ts": NOW.isoformat()})
    assert (_lane(repo, new), _lane(repo, old)) == ("research", "interactive")
    _write_rows({"schema": 1, "window": {**window, "taken_at": None}, "mode": "research", "ts": NOW.isoformat()})
    assert _lane(repo, new) == "interactive"  # no time to bound it by: the window holds nothing


def test_an_owner_fetch_during_a_broker_less_run_is_counted_as_the_runs(repo: Path) -> None:
    """A known limit of the git window, failing closed: a remote-tracking ref that moved is a tip like any other."""

    def make() -> list[str]:
        _git(repo, "checkout", "-q", "--detach")
        fetched = _commit(repo, "owner: elsewhere", {"README.md": "elsewhere\n"})
        _git(repo, "update-ref", "refs/remotes/origin/main", fetched)
        _git(repo, "checkout", "-q", "main")
        return [fetched]

    [fetched] = _run(repo, "research", make)
    assert run_commits.read().commits[fetched].by == "run"


# --- automatic rollback acts only on what the broker recorded --------------------------------------------------------


def test_auto_rollback_acts_only_on_broker_recorded_changes(
    helper_vault: Path, owner_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.changes import actions
    from scout.changes.settings import Settings, write_settings

    monkeypatch.setattr(actions, "AUTO_ROLLBACK_LIVE", True)
    when = NOW - timedelta(days=20)
    brokered = _commit(helper_vault, "fix: brokered edit", {"SKILL.md": "b\n"}, date=when)
    _broker_row(helper_vault, brokered, "dreaming-nightly")
    windowed = _commit(helper_vault, "fix: windowed edit", {"DREAMING.md": "w\n"}, date=when)
    store_row = {"schema": 1, "sha": windowed, "mode": "dreaming-nightly", "by": "run", "ts": NOW.isoformat()}
    from scout.changes.store import _append

    _append(run_commits.path(), store_row)
    scan(helper_vault, since=None)
    for sha in (brokered, windowed):
        ts = when + timedelta(days=2)
        append_signal(
            Signal(
                ts=ts, kind="dm-reply", surface="slack-dm", ref=f"{ts.timestamp()}/{sha[:4]}", sentiment="negative",
                change=f"chg-{sha[:12]}",
            ),
            vault=helper_vault,
            env={},
        )  # fmt: skip
    write_settings(Settings(auto_rollback="targeted", calibrated_on=NOW.date()), owner_env)

    doc = due(helper_vault, owner_env, now=NOW, tz=TZ)

    assert [(r["id"], r["reason"]) for r in doc["rollbacks"]] == [(f"chg-{brokered[:12]}", "auto-targeted")]
    assert actions.auto_eligible(brokered) and not actions.auto_eligible(windowed)
    assert not actions.auto_eligible("0" * 40)


# --- run health says when a run was attributed without its broker ----------------------------------------------------


def test_health_flags_runs_attributed_without_the_broker(repo: Path) -> None:
    from scout.health import unbrokered_attribution_findings

    assert unbrokered_attribution_findings(vault=repo, now=NOW) == []
    sha = _broker_commit_row(repo)
    assert unbrokered_attribution_findings(vault=repo, now=NOW) == []  # brokered: exact, nothing to say
    _run(repo, "research", lambda: [_commit(repo, "fix: x", {"SKILL.md": "x\n"})])
    [finding] = unbrokered_attribution_findings(vault=repo, now=NOW + timedelta(seconds=1))
    assert (finding.id, finding.severity) == ("changes.unbrokered_attribution", "yellow")
    assert "1 run in the last 7 days committed without the commit broker" in finding.summary
    assert sha
    assert unbrokered_attribution_findings(vault=repo.parent, now=NOW) == []  # another vault's runs


def _broker_commit_row(repo: Path) -> str:
    sha = _git(repo, "rev-parse", "HEAD")
    _broker_row(repo, sha)
    return sha


def test_malformed_broker_rows_are_skipped() -> None:
    ts = NOW.isoformat()
    _write_rows(
        {"schema": 1, "broker": "profile", "run": "r", "tightened": True, "ts": ts},  # no vault key
        {"schema": 1, "broker": "end", "id": "x", "run": "r", "exit": 0, "vault_key": "k"},  # no time
        {"schema": 1, "broker": "begin", "run": "r", "vault_key": "k", "ts": ts},  # no id: can't be matched
        {"schema": 1, "broker": "end", "id": 7, "run": 7, "exit": 1, "vault_key": "k", "ts": ts},  # failed, no id
    )
    ledger = run_commits.read()
    assert (ledger.commits, ledger.profiles) == ({}, ())
    # The failed end names no commit, but one may have landed: it fails closed as a gap.
    assert [(g.vault_key, g.run) for g in ledger.gaps] == [("k", "")]


def test_an_unborn_or_missing_vault_snapshots_empty(tmp_path: Path) -> None:
    extra = run_commits.snapshot_extra(tmp_path)
    assert (extra["refs"], extra["known"]) == ({}, [])
    empty = tmp_path / "unborn"
    empty.mkdir()
    _git(empty, "init", "-q")
    extra = run_commits.snapshot_extra(empty)
    assert (extra["refs"], extra["known"]) == ({}, [])
    assert run_commits._missing(empty, [], os.environ) == []


def test_a_ref_line_git_didnt_give_in_full_is_skipped(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(integrity, "_git", lambda *a, **k: f"garbage\n{head} commit   refs/heads/main\n")
    assert run_commits._all_refs(repo, os.environ) == {"refs/heads/main": (head, head)}


# --- review of the redesign: a broker's sha is exact only for a tightened run ----------------------------------------


@pytest.mark.parametrize(("profile", "by"), [(True, "broker"), (False, "run"), (None, "run")])
def test_an_end_row_is_exact_only_when_its_run_was_tightened(repo: Path, profile: bool | None, by: str) -> None:
    sha = _git(repo, "rev-parse", "HEAD")
    if profile is not None:
        assert run_commits.mark_broker_profile(repo, BROKER_RUN, tightened=profile)
    rid = run_commits.broker_begin(repo, BROKER_RUN, "research")
    assert run_commits.broker_end(repo, rid, BROKER_RUN, "research", exit_code=0, sha=sha)
    assert run_commits.read().commits[sha].by == by
    assert run_commits.is_broker_recorded(sha) is (by == "broker")


def test_a_drain_records_its_own_run_never_the_entrys_claim(helper_vault: Path) -> None:
    from scout.sandbox import broker

    assert run_commits.mark_broker_profile(helper_vault, BROKER_RUN, tightened=True)
    (helper_vault / "SKILL.md").write_text("claimed\n")
    claim = {"v": 1, "verb": "commit", "message": "fix: x", "paths": ["SKILL.md"], "run": BROKER_RUN}
    tick = broker.Broker.for_vault(helper_vault)  # the periodic tick's drain: no run of its own
    assert tick.handle(claim).exit == 0
    sha = _git(helper_vault, "rev-parse", "HEAD")
    assert run_commits.read().commits[sha].by == "run"  # the claimed (tightened) run doesn't make it exact
    assert _lane(helper_vault, sha) == "dreaming"  # SCOUT_MODE in this env; a real tick has none: `run` lane

    (helper_vault / "SKILL.md").write_text("own\n")
    own = broker.Broker.for_vault(helper_vault, commit_run=BROKER_RUN)  # the run-end drain, `--run` its own
    assert own.handle({**claim, "run": "scout-broker.zzz999"}).exit == 0
    assert run_commits.read().commits[_git(helper_vault, "rev-parse", "HEAD")].by == "broker"


def test_drain_with_a_run_commits_under_that_run(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.sandbox import broker

    seen: list[object] = []
    monkeypatch.setattr(broker, "_drain", lambda vault, *, env, broker, deadline_s: seen.append(broker))
    broker.drain(repo, run=BROKER_RUN)
    broker.drain(repo)
    assert getattr(seen[0], "commit_run", None) == BROKER_RUN and seen[1] is None


def test_a_planted_older_reflog_line_with_the_nonce_never_wins(helper_vault: Path) -> None:
    """Review: a run that could write .git/logs read the helper's nonce and planted an older reflog line carrying it
    for an owner commit. The helper looks only at entries added since its commit started."""
    seed = _git(helper_vault, "rev-list", "--max-parents=0", "HEAD")
    hook = helper_vault / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/bash\n"
        'log="$(git rev-parse --git-dir)/logs/HEAD"\n'
        f"printf '%s %s Owner <o@example.invalid> 1700000000 +0000\\t%s: planted\\n' {'0' * 40} {seed} "
        '"$GIT_REFLOG_ACTION" | cat - "$log" > "$log.new" && mv "$log.new" "$log"\n'
    )
    hook.chmod(0o755)
    sha = _broker_commit(helper_vault, "fix: run's edit", "SKILL.md", "run\n")
    assert set(run_commits.read().commits) == {sha} and seed not in run_commits.read().commits


def test_a_helper_stopped_mid_commit_is_a_gap(helper_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.sandbox import broker

    monkeypatch.setattr(
        broker, "run_commit", lambda *a, **k: broker.Reply(5, "git-safe-commit: took longer\n", kind=broker.STOPPED)
    )
    (helper_vault / "SKILL.md").write_text("x\n")
    assert (
        _live_broker(helper_vault).handle({"v": 1, "verb": "commit", "message": "m", "paths": ["SKILL.md"]}).exit == 5
    )
    [gap] = run_commits.read().gaps
    assert gap.run == BROKER_RUN


def test_run_commit_marks_a_timeout_as_stopped(helper_vault: Path) -> None:
    from scout.sandbox import broker

    (helper_vault / "scripts" / "git-safe-commit.sh").write_text("#!/bin/bash\nsleep 30\n")
    reply = broker.run_commit(helper_vault, "m", ["SKILL.md"], None, dict(os.environ), timeout=0.5)
    assert (reply.exit, reply.kind) == (5, broker.STOPPED)


def test_an_old_helper_is_named_as_the_cause_in_verify_and_health(helper_vault: Path) -> None:
    from scout.health import unbrokered_attribution_findings

    runner = CliRunner()
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(helper_vault)])
    old = helper_vault / "scripts" / "git-safe-commit.sh"
    old.write_text(old.read_text().replace('echo "scout-commit ${SHA:-unresolved}"', ""))
    sha = _broker_commit(helper_vault, "fix: old helper", "SKILL.md", "old\n")
    snap_doc = json.loads(snap.stdout)
    assert run_commits.broker_state(helper_vault, snap_doc) == "gap"

    result = runner.invoke(
        app, ["sandbox", "verify", "--vault", str(helper_vault), "--label", "research"], input=snap.stdout
    )

    assert "the commit broker was on but couldn't name every commit" in result.stdout
    assert "run /scout-update" in result.stdout
    assert run_commits.read().commits[sha].by == "run"  # the git window caught it
    [finding] = unbrokered_attribution_findings(vault=helper_vault, now=NOW + timedelta(seconds=1))
    assert "had the commit broker on" in finding.summary and "/scout-update" in finding.fix


@pytest.mark.parametrize(
    ("exit_code", "extra", "gap"),
    [
        (5, {}, True),  # git failed with nothing to show HEAD didn't move: a commit may exist
        (1, {}, True),  # the helper died after `git commit` (set -e, a signal)
        (143, {}, True),
        (5, {"none": True}, False),  # the helper said its commit failed with HEAD unmoved
        (2, {}, False),  # usage, the mutex and nothing to commit come before `git commit`
        (3, {}, False),
        (4, {}, False),
        (0, {}, True),
        (5, {"uncertain": True}, True),
    ],
)
def test_an_end_with_no_sha_is_a_gap_unless_no_commit_was_made(
    repo: Path, exit_code: int, extra: dict[str, bool], gap: bool
) -> None:
    """Review of #786: a helper failing or stopped after `git commit` ran leaves a commit nobody named, and
    broker_was_on must not stay true over it."""
    start = _snap(repo)
    assert run_commits.mark_broker_profile(repo, BROKER_RUN, tightened=True)
    rid = run_commits.broker_begin(repo, BROKER_RUN, "research")
    assert run_commits.broker_end(repo, rid, BROKER_RUN, "research", exit_code=exit_code, sha=None, **extra)
    assert (len(run_commits.read().gaps) == 1) is gap
    assert run_commits.broker_was_on(repo, start) is not gap


def test_none_is_never_written_beside_a_sha(repo: Path) -> None:
    sha = "a" * 40
    assert run_commits.broker_end(repo, None, BROKER_RUN, "research", exit_code=5, sha=sha, none=True)
    assert run_commits.recorded_shas() == {sha} and run_commits.read().gaps == ()


def _git_failing_after_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `git` on PATH whose `commit` makes the commit, then fails (as when git can't write the new index)."""
    real = shutil.which("git")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "git").write_text(
        f'#!/bin/bash\n"{real}" "$@"; rc=$?\nfor a in "$@"; do [ "$a" = commit ] && exit 1; done\nexit $rc\n'
    )
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")


def test_a_commit_git_reports_failed_after_head_moved_is_still_named(
    helper_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _git(helper_vault, "rev-parse", "HEAD")
    _git_failing_after_commit(tmp_path, monkeypatch)
    (helper_vault / "SKILL.md").write_text("half\n")
    reply = _live_broker(helper_vault).handle(
        {"v": 1, "verb": "commit", "message": "fix: half done", "paths": ["SKILL.md"]}
    )
    sha = _git(helper_vault, "rev-parse", "HEAD")
    assert reply.exit == 5 and sha != head
    ledger = run_commits.read()
    assert ledger.commits[sha].by == "broker" and ledger.gaps == ()


def test_a_hook_refusal_says_none_and_is_no_gap(helper_vault: Path) -> None:
    hook = helper_vault / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/bash\nexit 1\n")
    hook.chmod(0o755)
    (helper_vault / "SKILL.md").write_text("refused\n")
    out = subprocess.run(
        ["bash", str(helper_vault / "scripts" / "git-safe-commit.sh"), "fix: refused", "SKILL.md"],
        capture_output=True, text=True, env={**os.environ, "SCOUT_RUN_BROKER": ""},
    )  # fmt: skip
    assert out.returncode == 5 and run_commits.sha_from_helper(out.stdout) == ("none", None)
    reply = _live_broker(helper_vault).handle({"v": 1, "verb": "commit", "message": "fix: r", "paths": ["SKILL.md"]})
    assert reply.exit == 5
    assert run_commits.read().gaps == () and run_commits.recorded_shas() == frozenset()


def test_recorded_shas_names_every_row_whatever_its_grade(repo: Path) -> None:
    """The integrity check's blame set (#786): every sha any row names, with no grading."""
    exact, loose, bare, worktree, run = "a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40
    _write_rows(
        {"schema": 1, "sha": bare, "mode": "research", "by": "broker"},  # never trusted, still named
        {"schema": 1, "sha": worktree, "mode": "research", "by": "worktree"},
        {"schema": 1, "sha": run, "mode": "research", "by": "run"},
    )
    _broker_row(repo, exact)
    assert run_commits.broker_end(repo, None, "scout-broker.untight", "research", exit_code=0, sha=loose)
    ledger = run_commits.read()
    assert run_commits.recorded_shas(ledger) == {exact, loose, bare, worktree, run}
    assert run_commits.recorded_shas() == run_commits.recorded_shas(ledger)
    assert bare not in ledger.commits and ledger.commits[loose].by == "run"

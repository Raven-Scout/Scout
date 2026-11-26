"""The post-run integrity check (spec 2026-11-15 §13, option 1): protected tracked files leave a run as they came.

A scratch vault with real git and a scratch HOME. Each test takes the start snapshot, makes the end state a run
could leave (with plain git, outside any sandbox), and checks what :func:`scout.sandbox.integrity.verify` reports
and puts back.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.changes import run_commits, store
from scout.cli import app
from scout.kb import hook_paths
from scout.sandbox import integrity
from scout.sandbox import profile as sbx

RUNNER_SCRIPT = "scripts/claude-with-retry.sh"
SCHEDULE = ".scout-state/schedule.yaml"
IDENTITY = {
    "GIT_AUTHOR_NAME": "Alex",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Alex",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
}


class Vault:
    def __init__(self, root: Path) -> None:
        self.path = root / "vault"
        self.home = root / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SCOUT_", "CLAUDE_"))}
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", **IDENTITY)
        self.path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write(RUNNER_SCRIPT, "#!/bin/sh\necho start\n")
        self.write(SCHEDULE, "schema_version: 1\n")
        self.write("notes/seed.md", "seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        hook_paths._ROOTS_CACHE.clear()

    def git(self, *args: str, input_text: str | None = None) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        ).stdout

    def write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def blob(self, rev: str, rel: str) -> str:
        return self.git("rev-parse", f"{rev}:{rel}").strip()

    def staged(self, rel: str) -> str:
        return self.git("rev-parse", f":{rel}").strip()

    def count(self, rev: str = "HEAD") -> int:
        return int(self.git("rev-list", "--count", rev))

    def snapshot(self) -> dict:
        return integrity.snapshot(self.path, self.env)

    def verify(self, snap: dict) -> integrity.Result:
        return integrity.verify(self.path, snap, self.env, label="morning-briefing")


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path.resolve())


def test_the_scope_is_the_protected_files_in_the_work_tree(vault: Vault) -> None:
    rels = {s.rel for s in integrity.scopes(vault.path, vault.env)}
    assert {RUNNER_SCRIPT, SCHEDULE, ".scout-state/triggers.yaml", ".claude/settings.json", ".mcp.json"} <= rels
    assert "run-scout.sh" in rels and ".gitattributes" in rels
    assert not any(r == ".git" or r.startswith(".git/") for r in rels)
    assert "notes/seed.md" not in rels


def test_an_ordinary_run_is_clean_and_adds_no_commit(vault: Vault) -> None:
    snap = vault.snapshot()
    vault.write("notes/today.md", "found things\n")
    vault.git("add", "notes/today.md")
    vault.git("commit", "-qm", "notes: today")
    before = vault.count()

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("clean", 0)
    assert vault.count() == before
    assert result.lines() == ["integrity check: protected tracked files unchanged"]


def test_a_committed_change_to_a_runner_script_is_put_back_in_a_new_commit(vault: Vault) -> None:
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    vault.write("notes/today.md", "found things\n")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho changed\n")
    vault.git("add", "-A")
    vault.git("commit", "-qm", "run commit")
    run_commit = vault.git("rev-parse", "HEAD").strip()

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("restored", 10)
    assert result.changed == {"refs/heads/main": [RUNNER_SCRIPT], "index": [RUNNER_SCRIPT]}
    assert vault.git("rev-parse", "HEAD^").strip() == run_commit  # the run's work stays, one commit on top
    assert vault.blob("HEAD", RUNNER_SCRIPT) == start_blob
    assert vault.staged(RUNNER_SCRIPT) == start_blob
    assert vault.blob("HEAD", "notes/today.md") == vault.blob(run_commit, "notes/today.md")
    message = vault.git("log", "-1", "--format=%B")
    assert "morning-briefing run" in message and RUNNER_SCRIPT in message
    assert any("restored them on refs/heads/main" in line for line in result.lines())
    # Run again from the restored state: nothing left to do.
    assert vault.verify(vault.snapshot()).status == "clean"


def test_the_schedule_is_put_back_too(vault: Vault) -> None:
    start_blob = vault.blob("HEAD", SCHEDULE)
    snap = vault.snapshot()
    vault.write(SCHEDULE, "schema_version: 1\nslots: {}\n")
    vault.git("commit", "-qam", "schedule")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert vault.blob("HEAD", SCHEDULE) == start_blob


def test_a_staged_change_is_put_back_in_the_index_without_a_commit(vault: Vault) -> None:
    start_blob = vault.staged(RUNNER_SCRIPT)
    snap = vault.snapshot()
    before = vault.count()
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho staged\n")
    vault.git("add", RUNNER_SCRIPT)

    result = vault.verify(snap)

    assert result.status == "restored"
    assert result.changed == {"index": [RUNNER_SCRIPT]}
    assert vault.staged(RUNNER_SCRIPT) == start_blob
    assert vault.count() == before


def test_a_file_the_run_added_under_a_protected_folder_is_removed_again(vault: Vault) -> None:
    snap = vault.snapshot()
    vault.write(".claude/commands/new.md", "x\n")
    vault.git("add", "-f", ".claude/commands/new.md")
    vault.git("commit", "-qm", "add")

    result = vault.verify(snap)

    assert result.status == "restored"
    tracked = vault.git("ls-tree", "-r", "--name-only", "HEAD").split()
    assert ".claude/commands/new.md" not in tracked
    assert ".claude/commands/new.md" not in vault.git("ls-files").split()


def test_a_new_branch_carrying_a_change_is_put_back_on_that_branch(vault: Vault) -> None:
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    vault.git("checkout", "-q", "-b", "side")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho side\n")
    vault.git("commit", "-qam", "side")
    vault.git("checkout", "-q", "main")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert set(result.restored) == {"refs/heads/side"}
    assert vault.blob("side", RUNNER_SCRIPT) == start_blob
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob


def test_a_detached_head_is_put_back_too(vault: Vault) -> None:
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    vault.git("checkout", "-q", "--detach")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho detached\n")
    vault.git("commit", "-qam", "detached")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert "HEAD" in result.restored
    assert vault.blob("HEAD", RUNNER_SCRIPT) == start_blob


def test_a_tag_holding_a_change_is_reported_and_left_alone(vault: Vault) -> None:
    snap = vault.snapshot()
    vault.git("checkout", "-q", "-b", "side")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho tagged\n")
    vault.git("commit", "-qam", "side")
    vault.git("tag", "t1")
    vault.git("checkout", "-q", "main")
    vault.git("branch", "-q", "-D", "side")
    tagged = vault.git("rev-parse", "t1").strip()

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("unrestored", 11)
    assert result.unrestored == ["refs/tags/t1"]
    assert vault.git("rev-parse", "t1").strip() == tagged
    assert any("refs/tags/t1 still holds the change" in line for line in result.lines())


AGENT_SETTINGS = ".claude/settings.local.json"
SESSION_BRANCH = "claude/session-a"


def _enforced(vault: Vault) -> None:
    """The runner exports the sandbox status it started claude with; only `enforced` lets a worktree vouch."""
    vault.env["SCOUT_SANDBOX_STATUS"] = "enforced"


def _session_worktree(vault: Vault) -> Path:
    """An interactive session's linked worktree under the vault's ``.claude/worktrees/``, on its own branch."""
    wt = vault.path / ".claude" / "worktrees" / "session-a"
    vault.git("worktree", "add", "-q", "-b", SESSION_BRANCH, str(wt))
    return wt


def _wt_git(vault: Vault, wt: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(wt), *args], env=vault.env, check=True, capture_output=True, text=True
    ).stdout


def test_a_worktree_sessions_own_commit_during_the_run_is_left_alone(vault: Vault) -> None:
    _enforced(vault)
    wt = _session_worktree(vault)
    snap = vault.snapshot()
    # While the run goes, the session commits a protected file on its own branch, from its own work tree.
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text('{"permissions": {}}\n', encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "session: settings")
    session_tip = _wt_git(vault, wt, "rev-parse", "HEAD").strip()

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("clean", 0)
    assert vault.git("rev-parse", f"refs/heads/{SESSION_BRANCH}").strip() == session_tip  # not rewritten
    assert _wt_git(vault, wt, "status", "--porcelain") == ""
    assert result.left_alone == {f"refs/heads/{SESSION_BRANCH}": [AGENT_SETTINGS]}
    assert any(
        f"refs/heads/{SESSION_BRANCH} changed" in line and "worktree" in line and AGENT_SETTINGS in line
        for line in result.lines()
    )


@pytest.mark.parametrize("status", [None, "off", "unavailable", "conflict", "ENFORCED"])
def test_without_an_enforced_sandbox_no_worktree_vouches(vault: Vault, status: str | None) -> None:
    # With the sandbox off the run could have written the worktree's HEAD and index itself, so the session's
    # commit still counts as the run's (a false positive the owner can read in the log). The branch is checked out
    # in the session's worktree, though, so it is reported and never rewritten under that session.
    if status is not None:
        vault.env["SCOUT_SANDBOX_STATUS"] = status
    wt = _session_worktree(vault)
    snap = vault.snapshot()
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "session: settings")
    session_tip = _wt_git(vault, wt, "rev-parse", "HEAD").strip()

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("unrestored", 11)
    assert result.restored == {} and result.left_alone == {}
    assert result.held_by == {f"refs/heads/{SESSION_BRANCH}": "session-a"}
    assert vault.git("rev-parse", f"refs/heads/{SESSION_BRANCH}").strip() == session_tip
    assert _wt_git(vault, wt, "status", "--porcelain") == ""


def _plant_on_branch(vault: Vault, branch: str, rel: str, text: str) -> str:
    """What a sandboxed run can do to any branch: write objects and move the ref, touching no worktree's index."""
    blob = vault.git("hash-object", "-w", "--stdin", input_text=text).strip()
    tip = vault.git("rev-parse", f"refs/heads/{branch}").strip()
    env = {**vault.env, "GIT_INDEX_FILE": str(vault.path.parent / "planted-index")}
    for args in (["read-tree", tip], ["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"]):
        subprocess.run(["git", "-C", str(vault.path), *args], env=env, check=True, capture_output=True)
    tree = subprocess.run(
        ["git", "-C", str(vault.path), "write-tree"], env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    planted = vault.git("commit-tree", tree, "-p", tip, "-m", "planted").strip()
    vault.git("update-ref", f"refs/heads/{branch}", planted, tip)
    return planted


def test_a_run_moving_a_worktree_sessions_branch_is_reported_not_rewritten(vault: Vault) -> None:
    # The planted change is the run's (the session's index doesn't vouch for it), but the branch is checked out in
    # the session's worktree: health goes red and the log names the worktree; no commit lands under the session.
    _enforced(vault)
    wt = _session_worktree(vault)
    snap = vault.snapshot()
    planted = _plant_on_branch(vault, SESSION_BRANCH, RUNNER_SCRIPT, "#!/bin/sh\necho planted\n")

    result = vault.verify(snap)

    assert (result.status, result.exit_code) == ("unrestored", 11)
    assert result.changed == {f"refs/heads/{SESSION_BRANCH}": [RUNNER_SCRIPT]}
    assert result.restored == {} and result.left_alone == {}
    assert vault.git("rev-parse", SESSION_BRANCH).strip() == planted
    held = [line for line in result.lines() if "still holds the change" in line]
    assert held and "'session-a'" in held[0] and RUNNER_SCRIPT in held[0]
    assert wt.is_dir()


def test_a_run_moving_a_branch_no_worktree_has_checked_out_is_still_put_back(vault: Vault) -> None:
    _enforced(vault)
    vault.git("branch", "-q", "parked")
    start_blob = vault.blob("parked", RUNNER_SCRIPT)
    snap = vault.snapshot()
    planted = _plant_on_branch(vault, "parked", RUNNER_SCRIPT, "#!/bin/sh\necho planted\n")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert set(result.restored) == {"refs/heads/parked"}
    assert vault.git("rev-parse", "parked^").strip() == planted
    assert vault.blob("parked", RUNNER_SCRIPT) == start_blob


def test_a_worktree_session_doesnt_cover_the_runs_change_on_main(vault: Vault) -> None:
    _enforced(vault)
    wt = _session_worktree(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "session: settings")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho changed\n")
    vault.git("commit", "-qam", "run commit")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert set(result.restored) == {"refs/heads/main"}
    assert set(result.left_alone) == {f"refs/heads/{SESSION_BRANCH}"}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob


def test_a_session_whose_index_differs_from_its_branch_tip_isnt_trusted(vault: Vault) -> None:
    # The session committed the file, then staged another version of it before the run ended: the index no
    # longer vouches for the tip, so the branch is held to its start content like any other.
    _enforced(vault)
    wt = _session_worktree(vault)
    snap = vault.snapshot()
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "session: settings")
    (wt / AGENT_SETTINGS).write_text('{"x": 1}\n', encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)

    result = vault.verify(snap)

    assert result.status == "unrestored"
    assert result.changed == {f"refs/heads/{SESSION_BRANCH}": [AGENT_SETTINGS]}
    assert result.held_by == {f"refs/heads/{SESSION_BRANCH}": "session-a"}


def test_a_worktree_made_after_the_snapshot_isnt_trusted(vault: Vault) -> None:
    # Jordan, 2026-11-19: only worktrees that existed when the run started vouch for a branch, as read outside
    # the sandbox. Under an enforced sandbox a run can't create one anyway (nothing carves out a new admin dir),
    # and without one no worktree vouches at all; the rule keeps the trusted set to what the snapshot saw.
    snap = vault.snapshot()
    _enforced(vault)
    wt = _session_worktree(vault)
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "session: settings")

    result = vault.verify(snap)

    # Not vouched for, so the change is the run's as far as this check can tell; but the branch is that new
    # worktree's, so it is reported, not rewritten.
    assert result.status == "unrestored"
    assert result.changed == {f"refs/heads/{SESSION_BRANCH}": [AGENT_SETTINGS]}
    assert result.held_by == {f"refs/heads/{SESSION_BRANCH}": "session-a"}


def test_a_worktree_that_moved_to_another_branch_doesnt_vouch_for_the_old_one(vault: Vault) -> None:
    _enforced(vault)
    wt = _session_worktree(vault)
    vault.git("branch", "-q", "other")
    snap = vault.snapshot()
    planted = _plant_on_branch(vault, "other", AGENT_SETTINGS, "{}\n")
    # The worktree now has `other` checked out, its index and HEAD from the session, not from the planting.
    _wt_git(vault, wt, "checkout", "-q", "other")

    result = vault.verify(snap)

    assert result.status == "unrestored"
    assert result.changed == {"refs/heads/other": [AGENT_SETTINGS]}
    assert result.held_by == {"refs/heads/other": "session-a"}
    assert vault.git("rev-parse", "other").strip() == planted


def test_a_worktree_with_the_vaults_own_branch_checked_out_doesnt_vouch_for_it(vault: Vault) -> None:
    # The vault's branch is the run's. A linked worktree can have it checked out too (`--ignore-other-worktrees`),
    # and what that worktree's index holds is no evidence the run didn't commit it.
    _enforced(vault)
    wt = _session_worktree(vault)
    _wt_git(vault, wt, "checkout", "-q", "--ignore-other-worktrees", "main")
    snap = vault.snapshot()
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    _wt_git(vault, wt, "commit", "-qm", "on main from the worktree")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert set(result.restored) == {"refs/heads/main"}
    assert result.left_alone == {}


def test_a_prunable_worktree_doesnt_vouch(vault: Vault) -> None:
    # Its folder is gone but its admin dir, HEAD and index are still there: no session works in it, so what its
    # index holds (here, a staged change) says nothing about who moved its branch.
    _enforced(vault)
    wt = _session_worktree(vault)
    (wt / ".claude").mkdir(exist_ok=True)
    (wt / AGENT_SETTINGS).write_text("{}\n", encoding="utf-8")
    _wt_git(vault, wt, "add", "-f", AGENT_SETTINGS)
    shutil.rmtree(wt)
    assert "prunable" in vault.git("worktree", "list", "--porcelain")
    snap = vault.snapshot()
    _plant_on_branch(vault, SESSION_BRANCH, AGENT_SETTINGS, "{}\n")

    result = vault.verify(snap)

    assert result.status == "restored"
    assert set(result.restored) == {f"refs/heads/{SESSION_BRANCH}"}
    assert result.left_alone == {}


def test_the_proof_rests_on_the_sandbox_denying_other_worktrees_head_and_index(vault: Vault) -> None:
    # The check can't tell a session's index from one a run forged: a run that could write the session's index
    # to match what it planted would be left alone. It can't, because the enforced profile carves out only the
    # vault's own git dirs. That deny (run on the kernel in tests/integration/test_run_sandbox_kernel.py) is
    # what the exemption rests on, so pin it here next to the check.
    _enforced(vault)
    wt = _session_worktree(vault)
    admin = vault.path / ".git" / "worktrees" / wt.name
    rules = sbx.build_rules(vault.path, vault.env)
    for rel in ("HEAD", "index", "index.lock", "gitdir", "commondir", "config.worktree", "logs/HEAD"):
        assert sbx.decide(rules, str(admin / rel)) == "deny", rel
    assert sbx.decide(rules, str(vault.path / ".git" / "worktrees" / "new-one" / "HEAD")) == "deny"
    assert sbx.decide(rules, str(vault.path / ".git" / "index")) == "allow"  # the vault's own index stays writable

    snap = vault.snapshot()
    _plant_on_branch(vault, SESSION_BRANCH, AGENT_SETTINGS, "{}\n")
    blob = vault.git("rev-parse", f"{SESSION_BRANCH}:{AGENT_SETTINGS}").strip()
    # What the kernel deny stops: writing the session's index to match the planted tip.
    vault.git(f"--git-dir={admin}", "update-index", "--add", "--cacheinfo", f"100644,{blob},{AGENT_SETTINGS}")

    assert vault.verify(snap).left_alone == {f"refs/heads/{SESSION_BRANCH}": [AGENT_SETTINGS]}


# -- who made it: the broker's record (Phase 1d) -------------------------------------------------------------------
#
# The 2026-11-22 case, synthetic: during a dreaming run another (owner) session fast-forwarded the vault's main with
# a commit changing .claude/settings.json, and a dozen more sessions then made new worktrees and branches from it.
# The check blamed the run for all of it and wrote restore commits onto every one of those branches.

SETTINGS = ".claude/settings.json"
OWNER = {"GIT_AUTHOR_NAME": "Sam", "GIT_AUTHOR_EMAIL": "sam@example.com"}


def _commit(vault: Vault, cwd: Path, rel: str, text: str, message: str, **ident: str) -> str:
    """A plain commit in ``cwd`` (the vault or one of its worktrees); returns its sha."""
    env = {**vault.env, **ident}
    (cwd / rel).parent.mkdir(parents=True, exist_ok=True)
    (cwd / rel).write_text(text, encoding="utf-8")
    for args in (["add", "-f", rel], ["commit", "-qm", message]):
        subprocess.run(["git", "-C", str(cwd), *args], env=env, check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"], env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def _worktree(vault: Vault, name: str, branch: str, start: str = "HEAD") -> Path:
    wt = vault.path / ".claude" / "worktrees" / name
    vault.git("worktree", "add", "-q", "-b", branch, str(wt), start)
    return wt


def _broker(*shas: str) -> integrity.BrokerRecord:
    """What the run's broker recorded: the commits it made for the run, and nothing else."""
    return integrity.BrokerRecord(frozenset(shas))


def _brokered(vault: Vault, snap: dict, *shas: str) -> integrity.Result:
    return integrity.verify(vault.path, snap, vault.env, label="dreaming", record=_broker(*shas))


def _tips(vault: Vault) -> dict[str, str]:
    return dict(
        line.split(" ", 1)[::-1]
        for line in vault.git("for-each-ref", "--format=%(objectname) %(refname)", "refs/heads").splitlines()
    )


def _the_night(vault: Vault) -> tuple[dict, list[str], dict[str, Path]]:
    """The run's snapshot, its two broker commits, and the owner sessions' worktrees by branch."""
    _enforced(vault)
    vault.write(SETTINGS, '{"permissions": {}}\n')
    vault.git("add", "-f", SETTINGS)
    vault.git("commit", "-qm", "settings")
    trees = {"claude/older": _worktree(vault, "older", "claude/older")}  # there before the run
    snap = vault.snapshot()
    # What `scoutctl sandbox snapshot` adds for the shared ledger: when the run started.
    snap["changes"] = run_commits.snapshot_extra(vault.path, vault.env)
    run1 = _commit(vault, vault.path, "notes/dream-1.md", "one\n", "dreaming: notes")
    # 22:32: an owner session commits the settings change in its worktree, then fast-forwards main in the vault.
    trees["claude/owner"] = _worktree(vault, "owner", "claude/owner")
    _commit(vault, trees["claude/owner"], SETTINGS, '{"permissions": {"allow": ["x"]}}\n', "settings: allow x", **OWNER)
    vault.git("merge", "-q", "--ff-only", "claude/owner")
    run2 = _commit(vault, vault.path, "notes/dream-2.md", "two\n", "dreaming: more notes")
    # More owner sessions start from the new main.
    for i in range(3):
        trees[f"claude/new-{i}"] = _worktree(vault, f"new-{i}", f"claude/new-{i}")
    # The older session commits the same change on its own branch.
    _commit(vault, trees["claude/older"], SETTINGS, '{"permissions": {"allow": ["x"]}}\n', "settings", **OWNER)
    return snap, [run1, run2], trees


def test_the_night_with_the_brokers_record_blames_nobody_and_writes_nothing(vault: Vault) -> None:
    snap, run_commits, trees = _the_night(vault)
    before = _tips(vault)

    result = _brokered(vault, snap, *run_commits)

    assert (result.status, result.exit_code) == ("clean", 0)
    assert _tips(vault) == before  # no restore commit anywhere
    assert result.changed == {} and result.restored == {} and result.unrestored == []
    branches = {"refs/heads/main", "refs/heads/claude/older", "refs/heads/claude/owner"}
    branches |= {f"refs/heads/claude/new-{i}" for i in range(3)}
    assert result.foreign == {**dict.fromkeys(branches, [SETTINGS]), "index": [SETTINGS]}
    # The owner's fast-forward updated this checkout's index and tree together; the index is left as it is.
    assert vault.staged(SETTINGS) == vault.blob("main", SETTINGS)
    assert vault.git("status", "--porcelain", "--", SETTINGS) == ""
    for wt in trees.values():
        assert _wt_git(vault, wt, "status", "--porcelain") == ""
    lines = result.lines()
    assert lines[-1] == "integrity check: this run changed no protected file"
    assert any(line.startswith("integrity check: refs/heads/main changed protected file during") for line in lines)
    assert not any("restored them" in line or "still holds" in line for line in lines)


def test_the_night_without_a_record_still_blames_main_but_writes_on_no_sessions_branch(vault: Vault) -> None:
    # No broker record (Phase 1c, or the record isn't trustworthy): the conservative check as before. main, the
    # vault's own branch, is put back and the run is blamed; the older session is vouched for by its worktree; the
    # branches checked out in the newer sessions' worktrees are reported, never rewritten.
    snap, _, trees = _the_night(vault)
    before = _tips(vault)

    result = vault.verify(snap)

    assert result.status == "unrestored"
    assert set(result.restored) == {"refs/heads/main"}
    assert set(result.left_alone) == {"refs/heads/claude/older"}
    held = {"refs/heads/claude/owner", *(f"refs/heads/claude/new-{i}" for i in range(3))}
    assert set(result.held_by) == held == set(result.unrestored)
    after = _tips(vault)
    assert {r: after[r] for r in held} == {r: before[r] for r in held}
    for branch, wt in trees.items():
        assert _wt_git(vault, wt, "status", "--porcelain") == "", branch


def test_a_runs_own_commit_editing_a_protected_file_is_put_back_with_health_red(vault: Vault) -> None:
    _enforced(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    run = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho changed\n", "dreaming: tidy")

    result = _brokered(vault, snap, run)

    assert (result.status, result.exit_code) == ("restored", 10)
    # Its commit staged the file too, so the index entry goes back with the branch.
    assert result.changed == {"refs/heads/main": [RUNNER_SCRIPT], "index": [RUNNER_SCRIPT]}
    assert result.foreign == {}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob == vault.staged(RUNNER_SCRIPT)
    assert vault.git("rev-parse", "main^").strip() == run


def test_a_forged_owner_author_and_message_dont_make_the_runs_commit_the_owners(vault: Vault) -> None:
    _enforced(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    forged = {**OWNER, "GIT_COMMITTER_NAME": "Sam", "GIT_COMMITTER_EMAIL": "sam@example.com"}
    run = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho x\n", "settings: approved by Sam", **forged)

    assert _brokered(vault, snap, run).status == "restored"
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob
    # Without a record, the same commit is the run's too (everything is).
    vault.git("reset", "-q", "--hard", run)
    assert vault.verify(snap).status == "restored"


def test_an_owner_commit_on_top_doesnt_launder_the_runs_change(vault: Vault) -> None:
    _enforced(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    run = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho run\n", "dreaming: tidy")
    _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho owner\n", "owner edit", **OWNER)

    result = _brokered(vault, snap, run)

    assert result.status == "restored"
    # Fail closed: back to its start content, in the branch and the index.
    assert result.changed == {"refs/heads/main": [RUNNER_SCRIPT], "index": [RUNNER_SCRIPT]}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob == vault.staged(RUNNER_SCRIPT)


def test_only_the_paths_the_runs_commits_touched_are_put_back(vault: Vault) -> None:
    _enforced(vault)
    vault.write(SETTINGS, "{}\n")
    vault.git("add", "-f", SETTINGS)
    vault.git("commit", "-qm", "settings")
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    owner = _commit(vault, vault.path, SETTINGS, '{"a": 1}\n', "owner settings", **OWNER)
    run = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho run\n", "dreaming: tidy")

    result = _brokered(vault, snap, run)

    assert result.changed == {"refs/heads/main": [RUNNER_SCRIPT], "index": [RUNNER_SCRIPT]}
    assert result.foreign == {"refs/heads/main": [SETTINGS], "index": [SETTINGS]}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob == vault.staged(RUNNER_SCRIPT)
    # The owner's change stays, committed and staged.
    assert vault.blob("main", SETTINGS) == vault.blob(owner, SETTINGS) == vault.staged(SETTINGS)


def test_a_ref_moved_onto_a_commit_the_run_made_elsewhere_is_still_the_runs(vault: Vault) -> None:
    # The run's broker commit sits off main (as if made elsewhere); main and a new session-looking branch are then
    # pointed at it. Whoever moved them, the commit is the run's: main is put back; the branch a worktree has
    # checked out is reported.
    _enforced(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    vault.git("checkout", "-q", "--detach")
    run = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho elsewhere\n", "notes")
    vault.git("checkout", "-q", "main")
    vault.git("update-ref", "refs/heads/main", run)
    vault.git("reset", "-q", "--hard")
    wt = _worktree(vault, "decoy", "claude/decoy", run)

    result = _brokered(vault, snap, run)

    assert result.status == "unrestored"
    assert set(result.restored) == {"refs/heads/main"}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob
    assert result.held_by == {"refs/heads/claude/decoy": "decoy"}
    assert vault.git("rev-parse", "claude/decoy").strip() == run
    assert _wt_git(vault, wt, "status", "--porcelain") == ""


def test_a_commit_in_a_worktree_the_run_made_itself_isnt_vouched_for(vault: Vault) -> None:
    # Without a broker record (the run could write git): a worktree made during the run vouches for nothing, so its
    # branch's change counts as the run's and health goes red, without a write under that worktree; moving main
    # onto it gets main put back.
    _enforced(vault)
    start_blob = vault.blob("HEAD", RUNNER_SCRIPT)
    snap = vault.snapshot()
    wt = _worktree(vault, "mine", "claude/mine")
    tampered = _commit(vault, wt, RUNNER_SCRIPT, "#!/bin/sh\necho mine\n", "settings: approved", **OWNER)
    vault.git("merge", "-q", "--ff-only", "claude/mine")

    result = vault.verify(snap)

    assert result.status == "unrestored"
    assert set(result.restored) == {"refs/heads/main"} and result.left_alone == {}
    assert vault.blob("main", RUNNER_SCRIPT) == start_blob
    assert result.held_by == {"refs/heads/claude/mine": "mine"}
    assert vault.git("rev-parse", "claude/mine").strip() == tampered
    # Under Phase 1d that worktree can't be the run's: the tightened profile denies every write in the git dir,
    # a new worktree's admin dir included (pinned on the kernel in tests/integration/test_run_sandbox_kernel.py).
    rules = sbx.build_rules(vault.path, vault.env) + sbx.broker_rules(vault.path, vault.path.parent / "b", vault.env)
    for rel in ("worktrees/mine2/HEAD", "refs/heads/main", "index", "objects/ab/cd", "HEAD"):
        assert sbx.decide(rules, str(vault.path / ".git" / rel)) == "deny", rel


def test_a_detached_head_with_someone_elses_change_is_left_alone(vault: Vault) -> None:
    _enforced(vault)
    snap = vault.snapshot()
    vault.git("checkout", "-q", "--detach")
    owner = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho owner\n", "owner", **OWNER)

    result = _brokered(vault, snap)

    assert result.status == "clean"
    assert result.foreign == {"HEAD": [RUNNER_SCRIPT], "index": [RUNNER_SCRIPT]}
    assert vault.git("rev-parse", "HEAD").strip() == owner


def test_too_many_new_commits_to_sort_are_all_the_runs(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    _enforced(vault)
    snap = vault.snapshot()
    _commit(vault, vault.path, "notes/a.md", "a\n", "a")
    _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho owner\n", "owner", **OWNER)
    monkeypatch.setattr(integrity, "MAX_NEW_COMMITS", 1)

    assert _brokered(vault, snap).status == "restored"


def test_a_start_commit_git_cant_read_makes_every_change_the_runs(vault: Vault) -> None:
    _enforced(vault)
    snap = vault.snapshot()
    _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho owner\n", "owner", **OWNER)
    snap["trees"]["0" * 40] = {}

    assert _brokered(vault, snap).status == "restored"


def test_a_root_commit_from_the_broker_counts_everything_it_holds(vault: Vault) -> None:
    # An orphan branch the run's broker started: every path in it is the run's.
    _enforced(vault)
    snap = vault.snapshot()
    vault.git("checkout", "-q", "--orphan", "fresh")
    vault.git("rm", "-rq", "--cached", ".")
    root = _commit(vault, vault.path, RUNNER_SCRIPT, "#!/bin/sh\necho root\n", "root")
    vault.git("checkout", "-q", "-f", "main")

    result = _brokered(vault, snap, root)

    assert "refs/heads/fresh" in result.restored


def test_a_branch_the_main_work_tree_has_checked_out_isnt_rewritten(vault: Vault) -> None:
    # The run's vault is itself a linked worktree: the main work tree is another session's.
    run_wt = _worktree(vault, "run", "run-branch")
    snap = integrity.snapshot(run_wt, vault.env)
    _plant_on_branch(vault, "main", RUNNER_SCRIPT, "#!/bin/sh\necho planted\n")

    result = integrity.verify(run_wt, snap, vault.env, record=None)

    assert result.status == "unrestored"
    assert result.held_by == {"refs/heads/main": "main work tree"}
    # A main work tree on a detached HEAD holds no branch.
    vault.git("checkout", "-q", "--detach")
    assert integrity._checked_out_elsewhere(run_wt, integrity._git_env(vault.env)) == {}


def test_a_branch_mid_rebase_in_another_worktree_isnt_rewritten(vault: Vault) -> None:
    # Mid-rebase the worktree's HEAD is detached, but git still counts the branch as checked out there: a restore
    # commit on it would break that session's `git rebase --continue` ("cannot lock ref").
    wt = _worktree(vault, "rb", "claude/rb")
    _commit(vault, wt, "notes.md", "theirs\n", "owner: notes")
    _commit(vault, vault.path, "notes.md", "ours\n", "owner: other notes")
    rebase = subprocess.run(["git", "-C", str(wt), "rebase", "main"], env=vault.env, capture_output=True)
    git_dir = Path(_wt_git(vault, wt, "rev-parse", "--absolute-git-dir").strip())
    assert rebase.returncode != 0 and (git_dir / "rebase-merge").is_dir()
    snap = vault.snapshot()
    _plant_on_branch(vault, "claude/rb", RUNNER_SCRIPT, "#!/bin/sh\necho planted\n")

    result = integrity.verify(vault.path, snap, vault.env, record=None)

    assert result.status == "unrestored"
    assert result.held_by == {"refs/heads/claude/rb": "rb"}


def test_a_branch_the_main_work_tree_is_bisecting_isnt_rewritten(vault: Vault) -> None:
    run_wt = _worktree(vault, "run", "run-branch")
    vault.git("bisect", "start")  # BISECT_START names the branch bisect started from
    vault.git("checkout", "-q", "--detach")
    snap = integrity.snapshot(run_wt, vault.env)
    _plant_on_branch(vault, "main", RUNNER_SCRIPT, "#!/bin/sh\necho planted\n")

    result = integrity.verify(run_wt, snap, vault.env, record=None)

    assert result.held_by == {"refs/heads/main": "main work tree"}


def test_what_a_detached_work_tree_holds(vault: Vault, tmp_path: Path) -> None:
    gd = tmp_path / "admin"
    (gd / "rebase-apply").mkdir(parents=True)
    held = lambda: integrity._held_branch(vault.path, integrity._git_env(vault.env), str(gd))  # noqa: E731
    (gd / "HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    assert held() is None
    (gd / "rebase-apply" / "head-name").write_text("detached HEAD\n", encoding="utf-8")
    assert held() is None  # a rebase started detached holds no branch
    (gd / "BISECT_START").write_text("a" * 40 + "\n", encoding="utf-8")
    assert held() is None  # nor does a bisect started detached
    (gd / "BISECT_START").write_text("refs/heads/topic\n", encoding="utf-8")
    assert held() == "refs/heads/topic"
    (gd / "rebase-apply" / "head-name").write_text("refs/heads/other\n", encoding="utf-8")
    assert held() == "refs/heads/other"


# -- the broker record, read from the shared ledger (scout.changes.run_commits) ---------------------------------------


@pytest.fixture
def ledger(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> Vault:
    """The owner store (and so the ledger) under the vault's scratch HOME, where its git and sandbox rules look too."""
    monkeypatch.setenv("HOME", str(vault.home))
    return vault


def _broker_run(vault: Vault, shas: list[str], *, tightened: bool = True, gap: bool = False) -> None:
    """What a run's broker writes to the ledger, outside the sandbox: its profile, then a begin/end per commit."""
    run = "scout-broker.abc123"
    run_commits.mark_broker_profile(vault.path, run, tightened=tightened)
    for sha in shas:
        rid = run_commits.broker_begin(vault.path, run, "dreaming")
        run_commits.broker_end(vault.path, rid, run, "dreaming", exit_code=0, sha=sha)
    if gap:
        run_commits.broker_begin(vault.path, run, "dreaming")  # stopped before it could write the end row


def test_the_night_through_the_ledger_blames_nobody(ledger: Vault) -> None:
    snap, runs, _ = _the_night(ledger)
    _broker_run(ledger, runs)
    before = _tips(ledger)

    result = ledger.verify(snap)  # the record comes from the ledger, as `scoutctl sandbox verify` reads it

    assert (result.status, result.exit_code) == ("clean", 0)
    assert _tips(ledger) == before
    assert "refs/heads/main" in result.foreign and result.changed == {}


@pytest.mark.parametrize(
    "case",
    ["profile not tightened", "a gap", "no broker run", "sandbox not enforced", "no start time"],
)
def test_a_ledger_that_doesnt_cover_the_run_leaves_the_check_conservative(ledger: Vault, case: str) -> None:
    snap, runs, _ = _the_night(ledger)
    if case != "no broker run":
        _broker_run(ledger, runs, tightened=case != "profile not tightened", gap=case == "a gap")
    if case == "sandbox not enforced":
        ledger.env["SCOUT_SANDBOX_STATUS"] = "unavailable"
    if case == "no start time":
        del snap["changes"]

    assert integrity.broker_record(ledger.path, snap, ledger.env) is None
    result = ledger.verify(snap)
    assert set(result.restored) == {"refs/heads/main"}  # blamed, as before
    assert result.foreign == {}


def test_a_broker_run_from_before_the_snapshot_doesnt_count(ledger: Vault) -> None:
    _enforced(ledger)
    _broker_run(ledger, [])  # an earlier run's broker
    snap = ledger.snapshot()
    snap["changes"] = run_commits.snapshot_extra(ledger.path, ledger.env)
    assert integrity.broker_record(ledger.path, snap, ledger.env) is None


def test_the_ledger_record_names_only_broker_commits(ledger: Vault) -> None:
    _enforced(ledger)
    snap = ledger.snapshot()
    snap["changes"] = run_commits.snapshot_extra(ledger.path, ledger.env)
    run = _commit(ledger, ledger.path, RUNNER_SCRIPT, "#!/bin/sh\necho run\n", "dreaming: tidy")
    _broker_run(ledger, [run])

    record = integrity.broker_record(ledger.path, snap, ledger.env)

    assert record == integrity.BrokerRecord(frozenset({run}))
    assert ledger.verify(snap).status == "restored"


def test_a_run_grade_row_still_blames_never_clears(ledger: Vault) -> None:
    # A commit the ledger names below broker grade (a tick drain's, an untightened broker's) is still the run's: a
    # lower grade can't clear a protected-file change, only make it the run's.
    _enforced(ledger)
    _commit(ledger, ledger.path, SETTINGS, '{"approved": true}\n', "owner: settings", **OWNER)
    start_blob, base = ledger.blob("main", SETTINGS), ledger.git("rev-parse", "main").strip()
    snap = ledger.snapshot()
    snap["changes"] = run_commits.snapshot_extra(ledger.path, ledger.env)
    drained = _commit(ledger, ledger.path, SETTINGS, '{"drained": true}\n', "dreaming: drained")
    _broker_run(ledger, [])  # this run's broker: tightened, no commits of its own
    run_commits._append({"sha": drained, "mode": "dreaming", "by": "run", "vault_key": store.vault_key(ledger.path)})
    owner_wt = _worktree(ledger, "owner2", "claude/owner2", start=base)
    owner = _commit(ledger, owner_wt, RUNNER_SCRIPT, "#!/bin/sh\necho owner\n", "owner: tweak", **OWNER)

    record = integrity.broker_record(ledger.path, snap, ledger.env)
    result = ledger.verify(snap)

    assert record is not None and drained in record.shas and owner not in record.shas
    assert result.status == "restored" and result.changed["refs/heads/main"] == [SETTINGS]
    assert "refs/heads/claude/owner2" in result.foreign  # the owner's commit is still left alone
    assert ledger.blob("main", SETTINGS) == start_blob


def test_a_sandboxed_run_cant_write_the_ledger(ledger: Vault) -> None:
    # The broker record is trusted because a run can't write it: the ledger sits in a protected engine root, denied
    # by the run's 1c profile and so by the tightened one built on it.
    rules = sbx.build_rules(ledger.path, ledger.env)
    tightened = rules + sbx.broker_rules(ledger.path, ledger.path.parent / "b", ledger.env)
    target = run_commits.path()
    assert target.is_relative_to(ledger.home)
    for r in (rules, tightened):
        assert sbx.decide(r, str(target)) == "deny"
        assert sbx.decide(r, str(target.parent / "other.jsonl")) == "deny"


def test_the_broker_record_is_trusted_only_under_an_enforced_sandbox(vault: Vault) -> None:
    snap = vault.snapshot()
    assert integrity.broker_record(vault.path, snap, vault.env) is None


def test_the_git_env_drops_what_would_point_git_elsewhere() -> None:
    env = {k: "x" for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_NAMESPACE")}
    out = integrity._git_env({**env, "HOME": "/h"})
    assert not set(env) & set(out)
    assert out["HOME"] == "/h"


def test_an_index_left_mid_merge_is_put_back(vault: Vault) -> None:
    vault.git("checkout", "-q", "-b", "other")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho other\n")
    vault.git("commit", "-qam", "other")
    vault.git("checkout", "-q", "main")
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho main\n")
    vault.git("commit", "-qam", "main side")
    start_blob = vault.staged(RUNNER_SCRIPT)
    snap = vault.snapshot()
    subprocess.run(["git", "-C", str(vault.path), "merge", "-q", "other"], env=vault.env, capture_output=True)
    assert "U" in vault.git("status", "--porcelain")  # conflicted

    result = vault.verify(snap)

    assert result.status == "restored"
    assert result.changed["index"] == [RUNNER_SCRIPT]
    assert vault.staged(RUNNER_SCRIPT) == start_blob


def test_a_restore_without_a_git_identity_signs_as_scout(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    snap = vault.snapshot()
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho changed\n")
    vault.git("commit", "-qam", "run commit")
    real = integrity._git

    def no_identity(v: Path, args: list[str], env, **kw):  # noqa: ANN001
        if args[:1] == ["var"]:
            raise integrity.IntegrityError("no identity")
        return real(v, args, env, **kw)

    monkeypatch.setattr(integrity, "_git", no_identity)
    assert vault.verify(snap).status == "restored"
    assert vault.git("log", "-1", "--format=%an <%ae>").strip() == "Scout <scout@localhost>"


def test_a_vault_that_isnt_a_repo_is_clean(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    env = {**os.environ, "HOME": str(tmp_path)}
    snap = integrity.snapshot(plain, env)
    assert snap["repo"] is False
    assert integrity.verify(plain, snap, env).status == "clean"
    subprocess.run(["git", "init", "-q", str(plain)], check=True)
    assert integrity.verify(plain, snap, env).status == "error"


@pytest.mark.parametrize("snap", [{}, {"version": 99}, {"version": 1, "scopes": []}, "x"])
def test_a_snapshot_that_isnt_one_is_an_error(vault: Vault, snap) -> None:  # noqa: ANN001
    result = vault.verify(snap)
    assert (result.status, result.exit_code) == ("error", 12)
    assert result.lines()[0].startswith("integrity check: couldn't run:")


def test_snapshot_text_round_trips_and_rejects_junk() -> None:
    assert integrity.loads(integrity.dumps({"version": 1, "a": "x\0y"})) == {"version": 1, "a": "x\0y"}
    with pytest.raises(integrity.IntegrityError):
        integrity.loads("not json")
    with pytest.raises(integrity.IntegrityError):
        integrity.loads("[1]")


def test_a_git_that_wont_run_is_an_error(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    snap = vault.snapshot()

    def broken(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("no git")

    monkeypatch.setattr(integrity.subprocess, "run", broken)
    assert vault.verify(snap).status == "error"


def test_nothing_a_run_executes_syncs_the_repo_in_session() -> None:
    """`scoutctl repo sync` is what writes .scout-state/schedule.yaml into the vault (Agnes R1, repo_sync's
    read-tree -u). The sandbox now refuses that write, so no runner, phase, skill or command may start it inside a
    run; the owner and the app run it outside (review of #642)."""
    plugin = Path(__file__).resolve().parents[3]
    roots = [plugin / d for d in ("templates", "phases", "skills", "commands", "hooks")]
    hits = [
        str(p.relative_to(plugin))
        for root in roots
        for p in root.rglob("*")
        if p.is_file() and "repo sync" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits == []


# -- scoutctl sandbox snapshot | verify ----------------------------------------------------------------------------

runner = CliRunner()


def test_the_cli_round_trip_restores_and_exits_with_the_status(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in vault.env.items():
        monkeypatch.setenv(k, v)
    snap = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(vault.path)])
    assert snap.exit_code == 0
    assert json.loads(snap.stdout)["repo"] is True
    clean = runner.invoke(app, ["sandbox", "verify", "--vault", str(vault.path)], input=snap.stdout)
    assert clean.exit_code == 0, clean.output
    vault.write(RUNNER_SCRIPT, "#!/bin/sh\necho changed\n")
    vault.git("commit", "-qam", "run commit")
    restored = runner.invoke(
        app, ["sandbox", "verify", "--vault", str(vault.path), "--label", "dreaming"], input=snap.stdout
    )
    assert restored.exit_code == 10
    assert RUNNER_SCRIPT in restored.stdout
    assert "dreaming run" in vault.git("log", "-1", "--format=%s")


def test_the_cli_says_when_it_cant_check(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["sandbox", "verify", "--vault", str(vault.path)], input="junk").exit_code == 12

    def fail(*_a, **_k):  # noqa: ANN002, ANN003
        raise integrity.IntegrityError("nope")

    monkeypatch.setattr(integrity, "snapshot", fail)
    result = runner.invoke(app, ["sandbox", "snapshot", "--vault", str(vault.path)])
    assert result.exit_code == 12
    assert result.stdout == ""

"""The post-run integrity check (spec 2026-11-15 §13, option 1): protected tracked files leave a run as they came.

A scratch vault with real git and a scratch HOME. Each test takes the start snapshot, makes the end state a run
could leave (with plain git, outside any sandbox), and checks what :func:`scout.sandbox.integrity.verify` reports
and puts back.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import hook_paths
from scout.sandbox import integrity

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

"""Edge cases of the layout move (spec 2026-11-26 §4): unreadable inputs, refusals and recovery corners."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout, layout_migrate
from scout.layout_migrate import LiveFacts, Move
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"
CLEAN = LiveFacts(0, None, (), (), ())


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


class Killed(BaseException):
    pass


def _kill(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def boom(*a: object, **k: object) -> None:
        raise Killed

    monkeypatch.setattr(layout_migrate, name, boom)


def test_a_failing_git_command_raises_with_its_message(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    with pytest.raises(RuntimeError, match="git rev-parse"):
        layout_migrate._git(vault, "rev-parse", "--verify", "no-such-ref")


def test_unreadable_heads_and_frontmatter_corners(tmp_path: Path) -> None:
    (tmp_path / "bin.md").write_bytes(b"---\nname: \xff\n---\n")
    (tmp_path / "bad.md").write_text("---\nname: [unclosed\n---\n")
    (tmp_path / "list.md").write_text("---\n- a\n---\n")
    assert layout_migrate._head(tmp_path / "bin.md") is None
    assert layout_migrate._head(tmp_path / "bad.md") is None
    assert layout_migrate._head(tmp_path / "list.md") is None
    assert layout_migrate._head(tmp_path / "missing.md") is None
    assert layout_migrate.split_frontmatter("---\nname: x\nno end\n") == (None, "---\nname: x\nno end\n")
    assert layout_migrate._blocks("  stray\nname: x") == [(None, ["  stray"]), ("name", ["name: x"])]


def test_registry_keys() -> None:
    key = layout_migrate._registry_key
    assert key("|  | x |") is None
    assert key("| [[knowledge-base/meetings/sync/sync\\|Sync]] |") == "sync"
    assert key("| [[meetings/sync/2026-01-01]] |") == "sync"
    assert key("| Board Sync! |") == "board-sync"
    assert layout_migrate._calendar_matches(Path("/nonexistent")) == {}


def test_live_facts_fail_closed_without_a_process_table_and_find_conflict_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.kb import live_run

    vault = build_v1_vault(tmp_path / "v", dirty=False)
    (vault / "SKILL.md").write_text("<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
    _git(vault, "commit", "-qam", "conflict")

    def no_table() -> object:
        raise OSError("no ps")

    monkeypatch.setattr(live_run, "process_table", no_table)
    plan = layout_migrate.plan(vault)
    facts = layout_migrate.live_facts(vault, plan, holder=None)
    assert facts.queued_runners == -1 and facts.conflict_files == ("SKILL.md",)
    assert "process table" in (layout_migrate.check_preconditions(vault, plan, facts) or "")
    facts2 = LiveFacts(0, None, (), (), ("SKILL.md",))
    assert "conflict markers" in (layout_migrate.check_preconditions(vault, plan, facts2) or "")


def test_unmerged_paths_stop_the_move(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    base = _git(vault, "rev-parse", "HEAD").strip()
    (vault / "a.txt").write_text("one\n")
    _git(vault, "add", "a.txt")
    _git(vault, "commit", "-qm", "one")
    _git(vault, "checkout", "-q", "-b", "other", base)
    (vault / "a.txt").write_text("two\n")
    _git(vault, "add", "a.txt")
    _git(vault, "commit", "-qm", "two")
    subprocess.run(["git", "-C", str(vault), "merge", "-q", "-"], capture_output=True)
    (vault / ".git" / "MERGE_HEAD").unlink(missing_ok=True)  # leave only the unmerged index entries
    assert layout_migrate._git_problem(vault) == "the vault has unmerged paths"


def test_two_moves_to_one_path_are_refused(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    plan = layout_migrate.plan(vault)
    plan.moves = [Move("a.md", "scout/x.md", False, 1), Move("b.md", "scout/X.md", False, 1)]
    assert layout_migrate.check_preconditions(vault, plan, CLEAN) == "two files would move to scout/X.md"


def test_parity_is_unchecked_for_a_done_plan_or_an_unreadable_config(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    plan = layout_migrate.plan(vault)
    (vault / "scout-config.yaml").write_bytes(b"\xff\xfe")
    assert layout_migrate.parity_problem(vault, plan) is None
    done = layout_migrate.plan(vault)
    done.moves, done.removes, done.splits = [], [], []
    v2 = tmp_path / "v2"
    (v2 / "scout" / "brain").mkdir(parents=True)
    (v2 / "scout-config.yaml").write_text("{}\n")
    assert layout_migrate.parity_problem(v2, layout_migrate.plan(v2)) is None


def test_reversing_restores_the_brain_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    snap = vault / ".scout-state/last-assembled"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "SKILL.md").write_text("# SKILL\n\nscripts/heartbeat.sh\n")
    _kill(monkeypatch, "_index_renames")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    (snap / "SKILL.md").write_text("changed by the interrupted move\n")
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    assert layout_migrate.recover(vault) == "reversed"
    assert (snap / "SKILL.md").read_text() == "# SKILL\n\nscripts/heartbeat.sh\n"


def test_a_kill_before_the_gitignore_step_restores_it_from_the_pre_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    before = (vault / ".gitignore").read_text()
    _kill(monkeypatch, "_gitignore_rewrite")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    (vault / layout.MIGRATION_STATE_DIR / "pre" / "gitignore.json").unlink(missing_ok=True)
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    assert layout_migrate.recover(vault) == "reversed"
    assert (vault / ".gitignore").read_text() == before


def test_rehoming_is_a_no_op_on_v1_and_stages_tracked_records(tmp_path: Path) -> None:
    v1 = build_v1_vault(tmp_path / "v1", dirty=False)
    assert layout_migrate.rehome_records(v1) == []
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    (vault / "dreaming-proposals").mkdir(exist_ok=True)
    (vault / "dreaming-proposals/p.md").write_text("# p\n")
    _git(vault, "add", "dreaming-proposals/p.md")
    _git(vault, "commit", "-qm", "old client")
    moved = layout_migrate.rehome_records(vault)
    assert moved and moved[0].startswith("scout/records/")
    assert f"R  dreaming-proposals/p.md -> {moved[0]}" in _git(vault, "status", "--porcelain")


def test_jobs_check_reads_the_crontab_and_survives_without_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real = subprocess.run

    def no_crontab(args, *a, **k):  # type: ignore[no-untyped-def]
        if args[:1] == ["crontab"]:
            raise OSError("no crontab")
        return real(args, *a, **k)

    monkeypatch.setattr(layout_migrate.subprocess, "run", no_crontab)
    assert layout_migrate._jobs_on_v2(vault) is True


def test_dropping_link_ignores_without_a_gitignore_or_with_other_lines(tmp_path: Path) -> None:
    layout_migrate._drop_link_ignores(tmp_path, ["scripts"])  # no .gitignore: nothing to do
    assert not (tmp_path / ".gitignore").exists()
    header = "# Layout v2 compatibility links (removed by the next upgrade once nothing uses them)\n"
    (tmp_path / ".gitignore").write_text(f"a\n{header}/scripts\n/hooks\n")
    layout_migrate._drop_link_ignores(tmp_path, ["scripts"])
    assert (tmp_path / ".gitignore").read_text() == f"a\n{header}/hooks\n"  # header kept: a link is left


def test_a_failure_recovery_cant_reverse_is_an_interrupted_deferral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)

    def fail(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(layout_migrate, "_index_renames", fail)
    monkeypatch.setattr(layout_migrate, "recover", fail)
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "deferred" and out.reason.startswith("the layout move interrupted at index")


def test_small_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    assert any(v.startswith("~/") for v in layout_migrate._vault_abs(home / "Scout"))
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    assert layout_migrate._index_entries(vault, []) == {}
    layout_migrate._index_renames(vault, [("not-tracked.md", "x.md")], [])  # nothing to rename: no git call
    layout_migrate._update_index(vault, [])
    assert layout_migrate._commit1_sha(vault) == ""
    assert layout_migrate._is_text_file(vault / "knowledge-base") is None
    (vault / "scout-config.yaml").write_text("plugin:\n  applied_migrations: odd\n")
    layout_migrate._record_applied(vault)
    assert layout.MIGRATION_ID in (vault / "scout-config.yaml").read_text()


def test_the_plan_skips_a_nameless_person_and_leaves_meeting_prep_files(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, git=False)
    (vault / "knowledge-base/people/nameless.md").write_text("---\ntype: person\nname: 42\n---\n")
    (vault / "action-items/meeting-prep/prep.md").write_text("# prep\n")
    plan = layout_migrate.plan(vault)
    assert not any(s.origin.endswith("nameless.md") for s in plan.splits)
    assert "action-items/meeting-prep" in plan.left_dirs


def test_a_crash_between_a_rename_and_its_log_entry_is_still_reversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    before = {p.relative_to(vault).as_posix() for p in vault.rglob("*") if ".git" not in p.parts}
    real = layout_migrate._log_progress

    def log(v: Path, entry: dict) -> None:  # type: ignore[type-arg]
        if entry.get("i") == 5:
            raise Killed
        real(v, entry)

    monkeypatch.setattr(layout_migrate, "_log_progress", log)
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.setattr(layout_migrate, "_log_progress", real)
    assert layout_migrate.recover(vault) == "reversed"
    after = {p.relative_to(vault).as_posix() for p in vault.rglob("*") if ".git" not in p.parts}
    assert {p for p in after if not p.startswith(".scout-state/migrations")} == {
        p for p in before if not p.startswith(".scout-state/migrations")
    }


def test_a_reversed_move_is_retried_by_the_same_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    _kill(monkeypatch, "_index_renames")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    assert layout_migrate.migrate_layout(vault).state == "applied"


def test_finishing_forward_without_the_saved_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    _kill(monkeypatch, "_record_applied")
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    (vault / layout.MIGRATION_STATE_DIR / "pre" / "labels.json").unlink()
    assert layout_migrate.recover(vault) == "finished"
    assert not layout.move_in_progress(vault)


def test_jobs_check_skips_an_unreadable_job_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / "Library/LaunchAgents/com.scout.heartbeat.plist").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    vault = tmp_path / "v"
    vault.mkdir()
    monkeypatch.setattr(layout_migrate.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    assert layout_migrate._jobs_on_v2(vault) is True


def test_dropping_ignores_with_no_header_and_with_only_the_header_left(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("a\n/scripts\n")
    layout_migrate._drop_link_ignores(tmp_path, ["scripts"])
    assert (tmp_path / ".gitignore").read_text() == "a\n"
    layout_migrate._drop_link_ignores(tmp_path, ["hooks"])  # nothing to drop: untouched
    header = "# Layout v2 compatibility links (removed by the next upgrade once nothing uses them)\n"
    (tmp_path / ".gitignore").write_text(f"a\n{header}/scripts\n")
    layout_migrate._drop_link_ignores(tmp_path, ["scripts"])
    assert header not in (tmp_path / ".gitignore").read_text()

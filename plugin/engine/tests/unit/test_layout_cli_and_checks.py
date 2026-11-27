"""`scoutctl vault` commands, the layout move's precondition branches and helpers, and the layout findings
in the doctor and run health (layout v2, spec 2026-11-26 §4.3, §4.12, §6)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import health, layout, layout_migrate
from scout.cli import app
from scout.config import Space
from scout.scripts.bootstrap_doctor import _check_layout
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


@pytest.fixture
def v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    return vault


def _run(*args: str) -> tuple[int, str]:
    res = CliRunner().invoke(app, list(args))
    return res.exit_code, res.output


# ---- scoutctl vault ---------------------------------------------------------------------------------------


def test_migrate_plan_text_and_json(v1: Path) -> None:
    code, out = _run("vault", "migrate", "--plan")
    assert code == 0 and "This version reorganises your vault" in out and "ready to move" in out
    assert "notes-of-mine.md" in out
    code, out = _run("vault", "migrate", "--plan", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["would_defer"] is None and doc["summary"]["moved"] > 40


def test_migrate_needs_exactly_one_mode(v1: Path) -> None:
    assert _run("vault", "migrate")[0] == 2
    assert _run("vault", "migrate", "--plan", "--apply")[0] == 2


def test_migrate_apply_then_plan_on_v2(v1: Path) -> None:
    code, out = _run("vault", "migrate", "--apply", "--json")
    assert code == 0 and json.loads(out)["state"] == "applied"
    code, out = _run("vault", "migrate", "--plan")
    assert "nothing to move" in out
    code, out = _run("vault", "layout")
    assert "layout v2 (move: applied)" in out and "space work (default): knowledge-base/work (normal)" in out


def test_migrate_apply_reports_a_deferral(v1: Path) -> None:
    (v1 / "SKILL.md.proposed-merge").write_text("x")
    code, out = _run("vault", "migrate", "--apply")
    assert code == 3 and out.startswith("deferred:")


def test_space_candidates_and_done(v1: Path) -> None:
    _run("vault", "migrate", "--apply")
    (v1 / "knowledge-base/work/topics/g.md").write_text("---\ndomain: personal\n---\n# G\n")
    code, out = _run("vault", "space-candidates", "--json")
    assert code == 0 and json.loads(out)["candidates"][0]["suggested"] == "personal"
    code, out = _run("vault", "space-candidates")
    assert "domain:personal -> personal?" in out
    assert (v1 / layout.REVIEW_PENDING_REL).exists()
    assert _run("vault", "space-candidates", "--done")[1].startswith("space review recorded")
    assert not (v1 / layout.REVIEW_PENDING_REL).exists()


def test_vault_move_from_the_cli(v1: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run("vault", "migrate", "--apply")
    code, out = _run("vault", "move", "knowledge-base/work/topics/cooking.md", "--space", "personal", "--json")
    assert code == 0 and json.loads(out)["to"] == "knowledge-base/personal/topics/cooking.md"
    code, out = _run("vault", "move", "knowledge-base/work/topics/nope.md", "--space", "personal")
    assert code == 2 and "refused" in out
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    code, out = _run("vault", "move", "knowledge-base/work/topics/topics.md", "--space", "personal")
    assert code == 2 and "scheduled run" in out


# ---- preconditions ----------------------------------------------------------------------------------------


def _facts(**kw: object) -> layout_migrate.LiveFacts:
    base: dict[str, object] = {
        "queued_runners": 0,
        "git_problem": None,
        "brain_sidecars": (),
        "old_plugins": (),
        "conflict_files": (),
    }
    base.update(kw)
    return layout_migrate.LiveFacts(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("facts", "words"),
    [
        ({"queued_runners": 2}, "2 Scout runs are waiting"),
        ({"queued_runners": -1}, "process table can't be read"),
        ({"git_problem": "a merge is in progress in the vault"}, "a merge"),
        ({"brain_sidecars": ("SKILL.md.proposed-merge",)}, "sidecars are pending"),
        ({"conflict_files": ("scripts/x",)}, "conflict markers"),
        ({"old_plugins": ("scout@m 0.18.11",)}, "older than layout v2"),
    ],
)
def test_each_precondition_defers(v1: Path, facts: dict[str, object], words: str) -> None:
    plan = layout_migrate.plan(v1)
    reason = layout_migrate.check_preconditions(v1, plan, _facts(**facts))
    assert reason is not None and words in reason


def test_target_and_identity_collisions(v1: Path) -> None:
    plan = layout_migrate.plan(v1)
    assert layout_migrate.check_preconditions(v1, plan, _facts()) is None
    (v1 / "knowledge-base/organizations").mkdir(parents=True)
    (v1 / "knowledge-base/organizations/acme.md").write_text("x")
    assert "identity note already exists" in (layout_migrate.check_preconditions(v1, plan, _facts()) or "")


def test_a_path_through_a_symlink_out_of_the_vault_defers(v1: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (v1 / "scout").symlink_to(outside)
    plan = layout_migrate.plan(v1)
    reason = layout_migrate.check_preconditions(v1, plan, _facts()) or ""
    assert "leaves the vault through a symlink" in reason or "already exists" in reason


def test_git_problems(v1: Path) -> None:
    git_dir = v1 / ".git"
    (git_dir / "MERGE_HEAD").write_text("x")
    assert layout_migrate._git_problem(v1) == "a merge is in progress in the vault"
    (git_dir / "MERGE_HEAD").unlink()
    (git_dir / "index.lock").write_text("")
    assert "index is locked" in (layout_migrate._git_problem(v1) or "")
    (git_dir / "index.lock").unlink()
    assert layout_migrate._git_problem(v1) is None


def test_installed_plugins_are_read_from_the_registry(tmp_path: Path) -> None:
    cfg = tmp_path / "cc"
    (cfg / "plugins").mkdir(parents=True)
    assert layout_migrate.installed_old_plugins({"CLAUDE_CONFIG_DIR": str(cfg)}) == ()
    new, old = tmp_path / "new", tmp_path / "old"
    (new / "engine" / "scout").mkdir(parents=True)
    (new / "engine" / "scout" / "layout.py").write_text("")
    old.mkdir()
    reg = {"plugins": {"scout@m": [{"installPath": str(new)}, {"installPath": str(old), "version": "0.18.1"}],
                       "other@m": [{"installPath": str(old)}], "scout@x": {"bad": 1}}}  # fmt: skip
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(reg))
    assert layout_migrate.installed_old_plugins({"CLAUDE_CONFIG_DIR": str(cfg)}) == ("scout@m 0.18.1",)
    (cfg / "plugins" / "installed_plugins.json").write_text("{not json")
    assert layout_migrate.installed_old_plugins({"CLAUDE_CONFIG_DIR": str(cfg)}) == ()


# ---- helpers ----------------------------------------------------------------------------------------------


def test_split_note_needs_frontmatter_and_identity_key_check() -> None:
    with pytest.raises(ValueError):
        layout_migrate.split_note(
            "# no frontmatter\n", kind="person", space="w", identity_link="a", role_link="b", private=False
        )
    assert layout_migrate.identity_keys_only("# none\n") == []
    assert layout_migrate.identity_keys_only("---\nname: A\nrole: x\nlast_updated: 2026-01-01\n---\n") == ["role"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("# plain\n", "---\nvisibility: private\n---\n# plain\n"),
        ("---\nvisibility: normal\n---\n# x\n", "---\nvisibility: private\n---\n# x\n"),
        ("---\nname: A\n---\n# x\n", "---\nname: A\nvisibility: private\n---\n# x\n"),
    ],
)
def test_force_private(tmp_path: Path, before: str, after: str) -> None:
    p = tmp_path / "n.md"
    p.write_text(before)
    layout_migrate._force_private(p)
    assert p.read_text() == after


def test_write_spaces_into_a_flow_style_config(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("{user: {name: Alex}}\n")
    layout_migrate._write_spaces(tmp_path, (Space("work", True, "normal"),))
    import yaml

    data = yaml.safe_load((tmp_path / "scout-config.yaml").read_text())
    assert data["user"] == {"name": "Alex"} and data["spaces"][0]["key"] == "work"


def test_rehoming_reports_a_taken_path(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    (v1 / "docs/wishlist").mkdir(parents=True)
    (v1 / "docs/wishlist/w-1.md").write_text("old\n")  # w-1.md already lives in scout/records/wishlist/
    out = layout_migrate.migrate_layout(v1)
    assert out.state == "rehomed" and "left 1 file" in out.reason
    assert (v1 / "docs/wishlist/w-1.md").exists()


def test_links_stay_while_a_job_names_a_v1_path(v1: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.scout.heartbeat.plist").write_text(f"<string>{v1}/scripts/heartbeat.sh</string>")
    monkeypatch.setenv("HOME", str(home))
    assert layout_migrate.remove_compat_links_if_unused(v1) == []
    (agents / "com.scout.heartbeat.plist").write_text(f"<string>{v1}/scout/scripts/heartbeat.sh</string>")
    (v1 / "hooks").unlink()
    (v1 / "hooks").symlink_to("/elsewhere")  # not a link the move made: left alone
    removed = layout_migrate.remove_compat_links_if_unused(v1)
    assert "scripts" in removed and "hooks" not in removed and (v1 / "hooks").is_symlink()


def test_deferral_reads_back(tmp_path: Path) -> None:
    assert layout_migrate.deferral(tmp_path) is None
    layout_migrate._defer(tmp_path, "because")
    assert layout_migrate.deferral(tmp_path)["reason"] == "because"  # type: ignore[index]


def test_a_crash_while_planning_is_a_deferral(v1: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(vault: Path) -> None:
        raise ValueError("planner broke")

    monkeypatch.setattr(layout_migrate, "plan", boom)
    out = layout_migrate.migrate_layout(v1)
    assert (
        out.state == "deferred" and "failed before it changed anything" in out.reason and "planner broke" in out.reason
    )


def test_an_invalid_registry_defers(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces="spaces: [{key: work}]\n")
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "deferred" and "spaces: is invalid" in out.reason


# ---- doctor and run health --------------------------------------------------------------------------------


def test_doctor_and_health_on_each_layout_state(v1: Path, tmp_path: Path) -> None:
    now = datetime(2026, 11, 27, tzinfo=UTC)
    home = tmp_path / "home"
    assert _check_layout(vault=v1, home=home) == ([], [])
    assert health.layout_findings(v1, now=now) == []
    layout_migrate._defer(v1, "waiting on a sidecar")
    assert _check_layout(vault=v1, home=home)[1] == ["layout move deferred: waiting on a sidecar"]
    assert [f.id for f in health.layout_findings(v1, now=now)] == ["layout.deferred"]
    (v1 / layout.JOURNAL_REL).write_text("{}")
    errors, _ = _check_layout(vault=v1, home=home)
    assert errors and "interrupted" in errors[0]
    assert [f.severity for f in health.layout_findings(v1, now=now)] == ["red"]
    (v1 / layout.JOURNAL_REL).unlink()
    assert layout_migrate.migrate_layout(v1).state == "applied"
    errors, warnings = _check_layout(vault=v1, home=home)
    assert any("unknown files in the vault root" in w and "notes-of-mine.md" in w for w in warnings)
    assert any("compatibility links still in the vault root" in w for w in warnings)
    ids = {f.id for f in health.layout_findings(v1, now=now)}
    assert ids == {"layout.space_review", "layout.unknown_root"}
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    import plistlib

    gone = f"{v1}/elsewhere/heartbeat.sh"
    (agents / "com.scout.heartbeat.plist").write_bytes(plistlib.dumps({"ProgramArguments": ["/bin/bash", gone]}))
    errors, _ = _check_layout(vault=v1, home=home)
    assert any("doesn't exist" in e for e in errors)


def test_recovering_a_journal_that_never_existed_is_a_no_op(tmp_path: Path) -> None:
    assert layout_migrate.recover(tmp_path) == "none"


def test_untracked_duplicates_are_reported_not_moved(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", git=False)
    subprocess.run(["git", "-C", str(vault), "init", "-q"], check=True)
    inv = layout_migrate.gather(vault)
    plan = layout_migrate.build_plan(inv)
    # nothing is tracked: the losing registry and the identical meeting copy are reported, not removed
    assert "knowledge-base/meetings/meetings.md" in plan.untracked_left or "meetings/meetings.md" in plan.untracked_left
    assert plan.removes == []

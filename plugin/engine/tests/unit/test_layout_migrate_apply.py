"""The layout move applied to a 0.18-shaped vault (spec 2026-11-26 §4.5): apply equals plan, renames stay renames."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout, layout_migrate
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = (
    "spaces:\n  - key: work\n    default: true\n"
    "  - key: personal\n    visibility: private\n  - key: svj\n    visibility: private\n"
)


@pytest.fixture(autouse=True)
def _no_installed_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Precondition 6 reads the user's Claude Code plugin registry: keep the real one out.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def _tracked(vault: Path) -> set[str]:
    return set(_git(vault, "ls-files").splitlines())


@pytest.fixture
def v1(tmp_path: Path) -> Path:
    return build_v1_vault(tmp_path / "vault", spaces=SPACES)


def test_apply_equals_plan(v1: Path) -> None:
    before = _tracked(v1)
    the_plan = layout_migrate.plan(v1)
    outcome = layout_migrate.migrate_layout(v1)
    assert outcome.state == "applied", outcome.reason
    after = _tracked(v1)
    moved_tracked = {m.src: m.dst for m in the_plan.moves if m.tracked}
    expected = (before - set(moved_tracked) - set(the_plan.removes)) | set(moved_tracked.values())
    # merge bases and parked copies move with their file (row 23)
    for base in (".scout-state/last-rendered/", ".scout-state/drift/"):
        for rel in [r for r in expected if r.startswith(base)]:
            tail = rel[len(base) :]
            for m in the_plan.moves:
                if tail == m.src or tail.startswith(m.src + "."):
                    expected = (expected - {rel}) | {base + m.dst + tail[len(m.src) :]}
    expected |= {s.identity for s in the_plan.splits} | {outcome.report, "README.md"}
    assert after == expected
    for m in the_plan.moves:
        assert (v1 / m.dst).exists(), m.dst
    for link, target in the_plan.compat_links:
        assert (v1 / link).is_symlink() and (v1 / link).resolve() == (v1 / target).resolve()
    assert layout.layout_version(v1) == 2
    assert not (v1 / layout.JOURNAL_REL).exists()


def test_commit_one_is_renames_only(v1: Path) -> None:
    the_plan = layout_migrate.plan(v1)
    assert layout_migrate.migrate_layout(v1).state == "applied"
    log = _git(v1, "log", "--format=%s%n%b", "-2")
    assert "Scout-Migration: layout-v2 content" in log and "Scout-Migration: layout-v2\n" in log
    status = _git(v1, "show", "-M100%", "--name-status", "--format=", "HEAD~1").splitlines()
    renames = {tuple(line.split("\t")[1:]) for line in status if line.startswith("R100")}
    expected = {(m.src, m.dst) for m in the_plan.moves if m.tracked}
    # Every tracked move is an exact rename. For two identical meeting copies git may pick either as the source.
    assert {d for _, d in renames} == {d for _, d in expected}
    assert {s for s, _ in renames} <= {s for s, _ in expected} | set(the_plan.removes)
    others = [line for line in status if not line.startswith("R100")]
    assert {line.split("\t")[0] for line in others} <= {"D", "M"}
    assert _git(v1, "rev-parse", layout.PRE_REF).strip() == _git(v1, "rev-parse", "HEAD~2").strip()
    assert _git(v1, "rev-parse", layout.POST_REF).strip() == _git(v1, "rev-parse", "HEAD").strip()


def test_dirty_tree_is_checkpointed(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    assert _git(v1, "log", "--format=%s", "-3").splitlines()[2] == layout_migrate.CHECKPOINT
    assert "an uncommitted note" in (v1 / "inbox/inbox.md").read_text()


def test_ignored_drop_file_moves_and_stays_ignored(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    assert (v1 / "inbox/drop/big.pdf").exists()
    assert "inbox/drop/big.pdf" not in _git(v1, "status", "--porcelain", "--ignored=no")
    assert "inbox/drop/README.md" in _tracked(v1)


def test_split_identity_and_role(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    identity = (v1 / "knowledge-base/people/alex-example.md").read_text()
    role = (v1 / "knowledge-base/work/people/alex-example-work.md").read_text()
    assert "name: Alex Example" in identity and "spaces: [work]" in identity and "role: Engineer" not in identity
    assert "type: role" in role and 'role_of: "[[people/alex-example]]"' in role and "name:" not in role
    assert "[[organizations/acme]]" in role and "[[work/projects/apollo/apollo]]" in role
    private = (v1 / "knowledge-base/people/sam-private.md").read_text()
    assert "visibility: private" in private
    assert (v1 / "knowledge-base/organizations/acme.md").read_text().count("domain: example.com") == 1
    # not a person or organization: untouched
    assert (v1 / "knowledge-base/people/team-notes.md").exists()
    assert (v1 / "knowledge-base/ontology/entities/rust.md").exists()


def test_meetings_merge(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    series = v1 / "knowledge-base/work/meetings/standup"
    assert "kb copy, newer" in (series / "2026-11-02.md").read_text()
    assert "root copy" in (series / "2026-11-02.from-root-meetings.md").read_text()
    assert not (series / "2026-11-03.from-kb-meetings.md").exists()  # identical: one copy kept
    registry = (v1 / "knowledge-base/meetings.md").read_text()
    for slug in ("standup", "review", "planning"):
        assert f"[[work/meetings/{slug}/{slug}\\|" in registry
    assert "Priya" not in registry  # attendees stay in the home file
    home = (series / "standup.md").read_text()
    assert "From the old meetings registry" in home
    # the kb side holds standup's newest dated file, so its row (09:30) won the clash, labelled by column
    assert "Day/Time: 09:30" in home and "Day/Time: 09:00" not in home
    assert "| [[work/meetings/standup/standup\\|Standup]] | work | standup |" in registry  # its calendar_match
    assert not (v1 / "meetings").exists() and not (v1 / "knowledge-base/meetings").exists()


def test_rewrite_scope(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    project = (v1 / "knowledge-base/work/projects/apollo/apollo.md").read_text()
    assert "scout/scripts/scoutctl" in project and "knowledge-base/work/projects/apollo/apollo.md" in project
    assert "[[projects/apollo" in (v1 / "action-items/archive/action-items-2026-01-01.md").read_text()
    assert "[[projects/apollo" in (v1 / "knowledge-base/work/sources/2026-11-01-recipe.md").read_text()
    assert "[[svj/svj-board]]" in (v1 / "knowledge-base/personal/journal.md").read_text()
    runner = (v1 / "scout/runners/run-scout.sh").read_text()
    assert "$SCOUT_DIR/scout/scripts/scout-lock.sh" in runner and "$SCOUT_DIR/scout/brain/SKILL.md" in runner
    config = (v1 / "scout-config.yaml").read_text()
    assert (
        "knowledge-base/svj/projects/**/archive/**" in config and "projects_dir: knowledge-base/work/projects" in config
    )
    assert "applied_migrations:\n  - layout-v2" in config or "- layout-v2" in config
    gi = (v1 / ".gitignore").read_text()
    assert "inbox/drop/*" in gi and "!inbox/drop/README.md" in gi and "\ndrop/*" not in gi


def test_svj_notes_stay_private(v1: Path) -> None:
    from scout.kb import privacy

    assert layout_migrate.migrate_layout(v1).state == "applied"
    policy = privacy.load_policy(v1)
    for rel in ("knowledge-base/svj/svj-board.md", "knowledge-base/svj/svj-budget/2026.md"):
        assert privacy.label_file(v1, rel, policy).visibility == "private", rel


def test_state_is_rekeyed(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    assert (v1 / ".scout-state/last-rendered/scout/scripts/scoutctl").is_file()
    assert not (v1 / ".scout-state/last-rendered/scripts/scoutctl").exists()
    assert (v1 / ".scout-state/drift/scout/hooks/kb-pre-filter.sh.vault").is_file()


def test_report_names_no_note_content(v1: Path) -> None:
    out = layout_migrate.migrate_layout(v1)
    report = (v1 / out.report).read_text()
    assert "notes-of-mine.md" in report  # unknown root entry
    assert "kb copy" not in report and "Engineer" not in report and "Priya" not in report
    assert "scout/scripts/heartbeat.sh" in report  # self-locating script to check by hand


def test_second_run_is_a_no_op(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    head = _git(v1, "rev-parse", "HEAD")
    assert layout_migrate.migrate_layout(v1).state == "skipped"
    assert _git(v1, "rev-parse", "HEAD") == head


def test_record_folder_an_old_client_recreates_is_rehomed(v1: Path) -> None:
    assert layout_migrate.migrate_layout(v1).state == "applied"
    (v1 / "docs/wishlist").mkdir(parents=True)
    (v1 / "docs/wishlist/w-2.md").write_text("---\ntitle: two\n---\n")
    out = layout_migrate.migrate_layout(v1)
    assert out.state == "rehomed" and out.rehomed == ["scout/records/wishlist/w-2.md"]
    assert (v1 / "scout/records/wishlist/w-2.md").exists()


def test_defers_on_pending_brain_sidecar(v1: Path) -> None:
    (v1 / "SKILL.md.proposed-merge").write_text("x")
    out = layout_migrate.migrate_layout(v1)
    assert out.state == "deferred" and "sidecar" in out.reason
    assert layout.layout_version(v1) == 1
    assert layout_migrate.deferral(v1)["reason"] == out.reason


def test_defers_on_an_old_installed_plugin(v1: Path, tmp_path: Path) -> None:
    import json

    cfg = tmp_path / "claude-config" / "plugins"
    old = tmp_path / "cache" / "scout" / "0.18.11"
    (old / "engine" / "scout").mkdir(parents=True)
    cfg.mkdir(parents=True)
    entry = {"installPath": str(old), "version": "0.18.11"}
    (cfg / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"scout@m": [entry]}}))
    out = layout_migrate.migrate_layout(v1)
    assert out.state == "deferred" and "0.18.11" in out.reason
    (old / "engine" / "scout" / "layout.py").write_text("")
    assert layout_migrate.migrate_layout(v1).state == "applied"


def test_defers_on_an_existing_target(v1: Path) -> None:
    (v1 / "scout/records").mkdir(parents=True)
    (v1 / "scout/records/connector-health.md").write_text("x")
    out = layout_migrate.migrate_layout(v1)
    assert out.state == "deferred" and "already exists" in out.reason


def test_defers_without_git(tmp_path: Path) -> None:
    v = build_v1_vault(tmp_path / "v", git=False)
    assert layout_migrate.migrate_layout(v).state == "deferred"


def test_writes_default_spaces_when_missing(tmp_path: Path) -> None:
    v = build_v1_vault(tmp_path / "v")
    assert layout_migrate.migrate_layout(v).state == "applied"
    from scout import config

    assert config.load_spaces(v) == config.DEFAULT_SPACES
    # no svj space: the svj-* notes stay in personal, and the plan hints at a space
    assert (v / "knowledge-base/personal/svj-board.md").exists()

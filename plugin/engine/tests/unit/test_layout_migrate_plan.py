"""The layout move's plan (spec 2026-11-26 §4.2, §4.4): every row of the path map on a 0.18-shaped vault."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout import layout_migrate
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = (
    "spaces:\n  - key: work\n    default: true\n"
    "  - key: personal\n    visibility: private\n  - key: svj\n    visibility: private\n"
)


@pytest.fixture
def plan(tmp_path: Path) -> layout_migrate.Plan:
    return layout_migrate.plan(build_v1_vault(tmp_path / "v", spaces=SPACES))


def _dst(plan: layout_migrate.Plan, src: str) -> str:
    return next(m.dst for m in plan.moves if m.src == src)


def test_every_row_of_the_path_map(plan: layout_migrate.Plan) -> None:
    expect = {
        "SKILL.md": "scout/brain/SKILL.md",  # 1
        "run-mine.sh": "scout/runners/run-mine.sh",  # 2 (the vault's own runner too)
        "scripts/my-own.py": "scout/scripts/my-own.py",  # 3
        "hooks/kb-pre-filter.sh": "scout/hooks/kb-pre-filter.sh",  # 4
        "docs/kb-page-standard.md": "scout/kb-page-standard.md",  # 5
        "knowledge-base/session-log/2026-11.md": "scout/records/session-log/2026-11.md",  # 6
        "knowledge-base/scout-mistake-audit.md": "scout/records/scout-mistake-audit.md",  # 7
        "knowledge-base/connector-health.md": "scout/records/connector-health.md",  # 8
        "dreaming-proposals.md": "scout/records/dreaming-proposals.md",  # 9
        "docs/wishlist/w-1.md": "scout/records/wishlist/w-1.md",  # 10
        "inbox.md": "inbox/inbox.md",  # 11
        "drop/big.pdf": "inbox/drop/big.pdf",  # 12
        "meetings/review/review.md": "knowledge-base/work/meetings/review/review.md",  # 14
        "knowledge-base/projects/apollo/apollo.md": "knowledge-base/work/projects/apollo/apollo.md",  # 15
        "knowledge-base/topics/topics.md": "knowledge-base/work/topics/topics.md",  # 16
        "knowledge-base/sources/sources.md": "knowledge-base/work/sources/sources.md",  # 17
        "knowledge-base/people/alex-example.md": "knowledge-base/work/people/alex-example-work.md",  # 18
        "knowledge-base/ontology/entities/acme.md": "knowledge-base/work/organizations/acme-work.md",  # 19
        "knowledge-base/personal/svj-board.md": "knowledge-base/svj/svj-board.md",  # 20
    }
    for src, dst in expect.items():
        assert _dst(plan, src) == dst, src
    assert plan.registry_dst == "knowledge-base/meetings.md"  # 13
    assert "knowledge-base/inbox/README.md" in plan.removes  # 21
    assert plan.empty_dirs == ["action-items/meeting-prep"]  # 22
    moved = {m.src for m in plan.moves}
    for stays in ("docs/other.md", "notes-of-mine.md", "knowledge-base/personal/journal.md",
                  "knowledge-base/people/team-notes.md", "knowledge-base/ontology/entities/rust.md"):  # fmt: skip
        assert stays not in moved, stays  # 24
    assert plan.unknown_root == ["notes-of-mine.md"]


def test_drop_is_untracked_and_brain_names_are_rewritten_only_bare_in_brain_text(plan: layout_migrate.Plan) -> None:
    assert next(m for m in plan.moves if m.src == "drop/big.pdf").tracked is False
    skill = next(r for r in plan.refs if r.old == "SKILL.md")
    assert skill.bare_ok is False


def test_a_repeated_prefix_without_a_space_is_hinted(tmp_path: Path) -> None:
    plan = layout_migrate.plan(build_v1_vault(tmp_path / "v"))
    assert plan.prefix_hints == ["3 files are named `svj-*`; add a space `svj` to file them there"]
    assert plan.write_spaces is True
    assert not any(m.dst.startswith("knowledge-base/svj/") for m in plan.moves)


def test_the_plan_round_trips_through_its_journal_form(plan: layout_migrate.Plan) -> None:
    again = layout_migrate.Plan.from_json(plan.to_json())
    assert again.to_json() == plan.to_json()


def test_a_v2_vault_plans_nothing(tmp_path: Path) -> None:
    v = tmp_path / "v"
    (v / "scout" / "brain").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("{}\n")
    assert layout_migrate.plan(v).done


def test_a_registry_with_no_table_heading_keeps_its_rows() -> None:
    """A registry that is one table under its title: its rows are recurring meetings, never dropped (§4.7)."""
    text = (
        "# Meetings\n\n| Meeting | Cadence | Owner |\n|---|---|---|\n"
        "| [[meetings/sync\\|Sync]] | Weekly | [[people/alex\\|Alex]] |\n"
    )
    out, clashes, extras = layout_migrate.merge_registries(
        [("root-meetings", text)], space="work", newest_side={}, header="# Meetings"
    )
    assert "[[work/meetings/sync/sync\\|Sync]] | work |" in out
    # labelled by their column, the table's pipe escape gone in the home file
    assert extras == {"sync": ["Cadence: Weekly", "Owner: [[people/alex|Alex]]"]}
    assert clashes == []


def test_a_row_with_no_series_folder_gets_a_home_file_for_its_cells(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    vault = build_v1_vault(tmp_path / "v")
    reg = vault / "meetings" / "meetings.md"
    reg.write_text(reg.read_text() + "| [[meetings/retro/retro\\|Retro]] | monthly | Fri | Sam | |\n")
    subprocess_git = __import__("subprocess").run
    subprocess_git(["git", "-C", str(vault), "commit", "-qam", "retro"], check=True, capture_output=True)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    home = vault / "knowledge-base/work/meetings/retro/retro.md"
    assert "monthly" in home.read_text() and "Sam" in home.read_text()

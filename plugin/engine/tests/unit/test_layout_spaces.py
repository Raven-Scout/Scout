"""The space review (layout v2, spec 2026-11-26 §5): candidates and `vault move`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout, layout_migrate, layout_spaces
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = (
    "spaces:\n  - key: work\n    default: true\n"
    "  - key: personal\n    visibility: private\n  - key: svj\n    visibility: private\n"
)


@pytest.fixture
def v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    return vault


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def _w(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_candidates_carry_signals_and_never_note_text(v2: Path) -> None:
    _w(v2, "knowledge-base/work/topics/garden.md", "---\ndomain: personal\n---\n# Garden\n\nsecret words\n")
    _w(
        v2,
        "knowledge-base/work/topics/family.md",
        "---\nrelationships:\n  - type: family_of\n    target: '[[people/sam-private]]'\n---\n# Family\n",
    )
    _w(v2, "knowledge-base/work/topics/board.md", "# Board\n\n[[svj/svj-board]] [[svj/svj-repairs]] [[work/x]]\n")
    _w(v2, "knowledge-base/work/topics/plain.md", "# Plain\n\n[[work/projects/apollo/apollo]]\n")
    cands = {c.path: c for c in layout_spaces.space_candidates(v2)}
    assert cands["knowledge-base/work/topics/garden.md"].signals == ["domain:personal"]
    assert cands["knowledge-base/work/topics/garden.md"].suggested == "personal"
    assert cands["knowledge-base/work/topics/family.md"].suggested == "personal"
    assert cands["knowledge-base/work/topics/board.md"].signals == ["links-mostly:svj"]
    assert "knowledge-base/work/topics/plain.md" not in cands
    assert "secret words" not in str([c.to_json() for c in cands.values()])


def test_no_candidates_on_a_v1_vault(tmp_path: Path) -> None:
    assert layout_spaces.space_candidates(build_v1_vault(tmp_path / "v1", git=False)) == []


def test_moving_a_note_rewrites_links_and_commits(v2: Path) -> None:
    out = layout_spaces.move_to_space(v2, "knowledge-base/work/topics/cooking.md", "personal")
    assert out["to"] == "knowledge-base/personal/topics/cooking.md"
    assert (v2 / out["to"]).exists() and not (v2 / out["from"]).exists()
    assert _git(v2, "log", "-1", "--format=%s").startswith("scout: move knowledge-base/work/topics/cooking.md")
    assert not (v2 / layout.REVIEW_PENDING_REL).exists()  # a review has run


def test_a_move_commits_only_what_it_touched(v2: Path) -> None:
    """An unrelated edit, staged or not, stays out of the move's commit (review of #832)."""
    (v2 / "action-items/action-items-2026-11-20.md").write_text("edited by the app\n")
    _w(v2, "knowledge-base/work/topics/other.md", "# staged elsewhere\n")
    _git(v2, "add", "knowledge-base/work/topics/other.md")
    layout_spaces.move_to_space(v2, "knowledge-base/work/topics/cooking.md", "personal")
    committed = _git(v2, "show", "--name-only", "--format=", "HEAD").split()
    assert "knowledge-base/personal/topics/cooking.md" in committed
    assert (
        "action-items/action-items-2026-11-20.md" not in committed
        and "knowledge-base/work/topics/other.md" not in committed
    )
    status = _git(v2, "status", "--porcelain")
    assert " M action-items/action-items-2026-11-20.md" in status and "A  knowledge-base/work/topics/other.md" in status


def test_moving_a_role_note_renames_it_and_updates_both_spaces_lists(v2: Path) -> None:
    out = layout_spaces.move_to_space(v2, "knowledge-base/work/people/alex-example-work.md", "personal")
    assert out["to"] == "knowledge-base/personal/people/alex-example-personal.md"
    role = (v2 / out["to"]).read_text()
    assert "space: personal" in role and "space: work" not in role
    assert "spaces: [personal]" in (v2 / "knowledge-base/people/alex-example.md").read_text()


def test_moving_a_meeting_series_updates_its_registry_row(v2: Path) -> None:
    layout_spaces.move_to_space(v2, "knowledge-base/work/meetings/standup", "svj")
    registry = (v2 / "knowledge-base/meetings.md").read_text()
    assert "[[svj/meetings/standup/standup\\|Standup]] | svj |" in registry
    assert (v2 / "knowledge-base/svj/meetings/standup/standup.md").exists()


@pytest.mark.parametrize(
    ("rel", "space", "why"),
    [
        ("knowledge-base/work/topics/cooking.md", "nowhere", "not a registered space"),
        ("knowledge-base/people/alex-example.md", "personal", "not inside a space folder"),
        ("knowledge-base/work/topics/cooking.md", "work", "already in work"),
        ("knowledge-base/work/topics/missing.md", "personal", "doesn't exist"),
    ],
)
def test_refusals(v2: Path, rel: str, space: str, why: str) -> None:
    with pytest.raises(layout_spaces.SpaceMoveError, match=why):
        layout_spaces.move_to_space(v2, rel, space)


def test_a_taken_target_is_refused(v2: Path) -> None:
    _w(v2, "knowledge-base/personal/topics/cooking.md", "# mine\n")
    with pytest.raises(layout_spaces.SpaceMoveError, match="already exists"):
        layout_spaces.move_to_space(v2, "knowledge-base/work/topics/cooking.md", "personal")


def test_a_v1_vault_is_refused(tmp_path: Path) -> None:
    with pytest.raises(layout_spaces.SpaceMoveError, match="layout v2"):
        layout_spaces.move_to_space(build_v1_vault(tmp_path / "v1", git=False), "knowledge-base/x.md", "work")


def test_candidate_reading_survives_bad_notes(v2: Path) -> None:
    _w(v2, "knowledge-base/work/topics/broken.md", "---\nname: [unclosed\n---\n[[personal/x]]\n")
    (v2 / "knowledge-base/work/topics/binary.md").write_bytes(b"---\ndomain: personal\n---\n\xff")
    cands = {c.path: c for c in layout_spaces.space_candidates(v2)}
    assert cands["knowledge-base/work/topics/broken.md"].signals == ["links-mostly:personal"]
    assert "knowledge-base/work/topics/binary.md" not in cands
    assert layout_spaces._front(v2 / "nope.md") == {}


def test_no_candidates_without_a_default_space_folder(v2: Path) -> None:
    import shutil

    shutil.rmtree(v2 / "knowledge-base/work")
    assert layout_spaces.space_candidates(v2) == []


def test_a_git_failure_and_a_path_outside_the_space_are_move_errors(v2: Path) -> None:
    with pytest.raises(layout_spaces.SpaceMoveError, match="git rev-parse"):
        layout_spaces._git(v2, "rev-parse", "--verify", "no-such-ref")
    with pytest.raises(layout_spaces.SpaceMoveError, match="not in the work space"):
        layout_spaces._target("knowledge-base/personal/x.md", "work", "personal")


def test_a_move_without_commit_leaves_the_tree_staged(v2: Path) -> None:
    head = _git(v2, "rev-parse", "HEAD")
    out = layout_spaces.move_to_space(v2, "knowledge-base/work/topics/cooking.md", "personal", commit=False)
    assert out["commit"] == "" and _git(v2, "rev-parse", "HEAD") == head

"""Label parity (spec 2026-11-26 §4.5 step 10): the move never makes a note less private."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout_migrate
from scout.kb import privacy
from tests.unit.layout_v1_vault import build_v1_vault


@pytest.fixture(autouse=True)
def _no_installed_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def test_the_svj_notes_would_be_normal_without_a_private_space_and_the_move_says_so(tmp_path: Path) -> None:
    # svj registered as a normal space: notes under personal/ (private by path) would become normal. Abort.
    spaces = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n  - key: svj\n"
    vault = build_v1_vault(tmp_path / "v", spaces=spaces)
    head = subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    out = layout_migrate.migrate_layout(vault)
    # deferred before anything is written, naming the folder and a count, never a note
    assert out.state == "deferred" and "knowledge-base/svj/" in out.reason and "svj-board" not in out.reason
    assert subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert (vault / "knowledge-base/personal/svj-board.md").exists()
    assert not (vault / ".scout-state/migrations/layout-v2/journal.json").exists()


def test_a_path_private_person_keeps_its_label_in_the_role_note(tmp_path: Path) -> None:
    """An owner default makes people/** private; the role note lands in work/people/ and must stay private."""
    spaces = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"
    overlay = "privacy:\n  visibility_defaults:\n    - glob: knowledge-base/people/**\n      visibility: private\n"
    vault = build_v1_vault(tmp_path / "v", spaces=spaces + overlay)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    policy = privacy.load_policy(vault)
    for rel in ("knowledge-base/people/alex-example.md", "knowledge-base/work/people/alex-example-work.md"):
        assert privacy.label_file(vault, rel, policy).visibility == "private", rel


def test_a_private_person_stays_private_in_both_files(tmp_path: Path) -> None:
    spaces = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"
    vault = build_v1_vault(tmp_path / "v", spaces=spaces)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    policy = privacy.load_policy(vault)
    for rel in ("knowledge-base/people/sam-private.md", "knowledge-base/work/people/sam-private-work.md"):
        assert privacy.label_file(vault, rel, policy).visibility == "private", rel

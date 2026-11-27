"""Files git doesn't track, and the owner's own symlinks, through the layout move and `vault move` (review of
#832): never edited, never committed, never deleted."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout import layout_migrate, layout_spaces
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = (
    "spaces:\n  - key: work\n    default: true\n"
    "  - key: personal\n    visibility: private\n  - key: svj\n    visibility: private\n"
)


@pytest.fixture(autouse=True)
def _no_installed_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def _commit_all(vault: Path, msg: str) -> None:
    _git(vault, "add", "-A")
    _git(vault, "-c", "commit.gpgsign=false", "commit", "-qm", msg)


def test_the_move_never_edits_or_commits_an_untracked_or_ignored_file(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    drop = "See knowledge-base/projects/apollo/apollo.md and [[projects/apollo/apollo]].\n"
    (vault / "drop/notes.md").write_text(drop)  # ignored (drop/*)
    draft = "# Draft\n\n[[projects/apollo/apollo]]\n"
    (vault / "knowledge-base/topics/secret-draft.md").write_text(draft)  # untracked
    res = layout_migrate.migrate_layout(vault)
    assert res.state == "applied", res
    assert (vault / "inbox/drop/notes.md").read_text() == drop
    moved_draft = vault / "knowledge-base/work/topics/secret-draft.md"
    assert moved_draft.read_text() == draft
    tracked = set(_git(vault, "ls-files").split("\n"))
    assert "knowledge-base/work/topics/secret-draft.md" not in tracked
    assert "inbox/drop/notes.md" not in tracked
    assert not layout_migrate._phase_done(vault, "commit1")  # the journal is gone: the move finished


def test_commit_2_leaves_what_the_owner_staged_meanwhile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    real = layout_migrate._record_applied
    calls = {"n": 0}

    def staged_meanwhile(v: Path) -> None:
        # Obsidian (or the app) stages an edit of its own while the content step runs.
        if calls["n"] == 0:
            (v / "notes-of-mine.md").write_text("# edited meanwhile\n")
            _git(v, "add", "notes-of-mine.md")
        calls["n"] += 1
        real(v)

    monkeypatch.setattr(layout_migrate, "_record_applied", staged_meanwhile)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    assert "notes-of-mine.md" not in _git(vault, "show", "--name-only", "--format=", "HEAD").split("\n")
    assert _git(vault, "diff", "--cached", "--name-only").strip() == "notes-of-mine.md"  # still staged, not lost


def test_recovery_keeps_the_owners_own_symlink_at_a_link_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", dirty=False)
    tools = tmp_path / "dev-tools"
    tools.mkdir()
    (tools / "run.sh").write_text("#!/bin/bash\n")
    os.symlink(tools / "run.sh", vault / "run-dev.sh")
    _commit_all(vault, "a symlink of mine")
    assert any(a == "run-dev.sh" for a, _ in layout_migrate.plan(vault).compat_links)
    before = _git(vault, "status", "--porcelain")

    def fail(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(layout_migrate, "_rename", fail)
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "deferred" and "was reversed" in out.reason
    assert (vault / "run-dev.sh").is_symlink() and os.readlink(vault / "run-dev.sh") == str(tools / "run.sh")
    status = [ln for ln in _git(vault, "status", "--porcelain").splitlines() if "scout-state/migrations" not in ln]
    assert status == before.splitlines()  # the deferral's own record aside, nothing changed


@pytest.fixture
def v2(tmp_path: Path) -> Path:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    return vault


def test_vault_move_commits_only_tracked_files_of_its_own(v2: Path) -> None:
    series = "knowledge-base/work/meetings/review"
    (v2 / series / "draft.md").write_text("# untracked draft\n")
    (v2 / "inbox/drop/ref.md").write_text("[[work/meetings/review/review]]\n")  # ignored
    (v2 / "notes-of-mine.md").write_text("# staged meanwhile\n")
    _git(v2, "add", "notes-of-mine.md")
    out = layout_spaces.move_to_space(v2, series, "svj")
    committed = set(_git(v2, "show", "--name-only", "--format=", out["commit"]).split("\n")) - {""}
    assert "knowledge-base/svj/meetings/review/draft.md" not in committed
    assert "knowledge-base/svj/meetings/review/review.md" in committed
    assert "notes-of-mine.md" not in committed
    assert (v2 / "knowledge-base/svj/meetings/review/draft.md").is_file()  # went along, still untracked
    assert (v2 / "inbox/drop/ref.md").read_text() == "[[work/meetings/review/review]]\n"
    assert _git(v2, "diff", "--cached", "--name-only").strip() == "notes-of-mine.md"


def test_a_failed_vault_move_puts_everything_back(v2: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    series = "knowledge-base/work/meetings/review"
    before = {p: p.read_bytes() for p in v2.rglob("*.md") if ".git/" not in p.as_posix()}
    status = _git(v2, "status", "--porcelain")

    def fail(*a: object, **k: object) -> str:
        raise layout_spaces.SpaceMoveError("git add: paths are ignored")

    monkeypatch.setattr(layout_spaces, "_git_paths", fail)
    with pytest.raises(layout_spaces.SpaceMoveError):
        layout_spaces.move_to_space(v2, series, "svj")
    assert (v2 / series / "review.md").is_file() and not (v2 / "knowledge-base/svj/meetings/review").exists()
    after = {p: p.read_bytes() for p in v2.rglob("*.md") if ".git/" not in p.as_posix()}
    assert after == before
    assert _git(v2, "status", "--porcelain") == status

"""The layout move, its recovery and `vault move` on a vault that isn't all committed (review of #832).

The clean fixture vault passed every test while the move edited and committed untracked files and recovery
deleted the owner's symlinks: the fixture matched the code's assumptions. This one doesn't. The invariants, on
every path through the move: a file git doesn't track ends up byte for byte as it was (moved with its folder at
most), stays untracked, and is in no commit; the owner's symlinks still point where they pointed; a commit holds
only what the move wrote.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout import layout, layout_migrate, layout_spaces
from tests.unit.layout_v1_vault import (
    HOSTILE_IGNORED,
    HOSTILE_STAGED,
    HOSTILE_TRACKED,
    HOSTILE_TRACKED_LINK,
    HOSTILE_UNTRACKED,
    HOSTILE_UNTRACKED_LINK,
    build_hostile_v1_vault,
)

SPACES = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"


@pytest.fixture(autouse=True)
def _no_installed_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def _tracked(vault: Path) -> set[str]:
    return set(_git(vault, "-c", "core.quotepath=false", "ls-files", "-z").split("\0")) - {""}


def _status(vault: Path) -> list[str]:
    out = _git(vault, "-c", "core.quotepath=false", "status", "--porcelain", "-z").split("\0")
    return [ln for ln in out if ln and "scout-state/migrations" not in ln]


def _snapshot(vault: Path) -> dict[str, bytes]:
    """Every file and symlink outside .git, ignored and untracked ones included."""
    out = {}
    for p in sorted(vault.rglob("*")):
        rel = p.relative_to(vault).as_posix()
        if rel.startswith((".git/", ".scout-state/migrations")) or (p.is_dir() and not p.is_symlink()):
            continue
        out[rel] = b"->" + os.readlink(p).encode() if p.is_symlink() else p.read_bytes()
    return out


def _where(plan: layout_migrate.Plan, rel: str) -> str:
    """Where ``rel`` is after the move: its own row, or the folder that moved in one rename."""
    for m in plan.moves:
        if m.src == rel:
            return m.dst
    for top in layout.COMPAT_LINK_DIRS:
        if rel.startswith(top + "/"):
            return f"scout/{rel}"
    return rel


@pytest.fixture
def hostile(tmp_path: Path) -> tuple[Path, Path, layout_migrate.Plan]:
    vault = build_hostile_v1_vault(tmp_path / "v", tmp_path / "outside", spaces=SPACES)
    return vault, tmp_path / "outside", layout_migrate.plan(vault)


def _assert_moved_and_untouched(vault: Path, outside: Path, plan: layout_migrate.Plan) -> None:
    tracked = _tracked(vault)
    committed = {
        f
        for sha in _git(vault, "rev-list", f"{layout.PRE_REF}..HEAD").split()
        for f in _git(vault, "-c", "core.quotepath=false", "show", "--name-only", "--format=", sha).splitlines()
    }
    for rel, text in {**HOSTILE_UNTRACKED, **HOSTILE_IGNORED}.items():
        now = _where(plan, rel)
        assert (vault / now).read_text() == text, rel  # never edited
        assert now not in tracked and now not in committed, rel  # never committed
    for rel in HOSTILE_TRACKED:
        now = _where(plan, rel)
        assert "[[work/projects/apollo/apollo]]" in (vault / now).read_text(), rel  # rewritten like any note
        assert now in tracked, rel
    staged = _where(plan, next(iter(HOSTILE_STAGED)))
    assert staged in tracked and "[[work/projects/apollo/apollo]]" in (vault / staged).read_text()
    for link, target in ((HOSTILE_TRACKED_LINK, "run.sh"), (HOSTILE_UNTRACKED_LINK, "elsewhere.md")):
        now = vault / _where(plan, link)
        assert now.is_symlink() and Path(os.path.realpath(now)) == (outside / target).resolve(), link
    # Nothing tracked is left changed: whatever git still lists is a file it doesn't track.
    assert all(ln.startswith("?? ") for ln in _status(vault)), _status(vault)


def test_the_move_leaves_what_git_doesnt_track(hostile: tuple[Path, Path, layout_migrate.Plan]) -> None:
    vault, outside, plan = hostile
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "applied", out
    _assert_moved_and_untouched(vault, outside, plan)


class Killed(BaseException):
    """Stands in for the process dying: nothing in the move catches it."""


@pytest.mark.parametrize(
    ("name", "nth", "outcome"),
    [
        ("_index_renames", 1, "reversed"),  # files moved, links made, index not yet renamed
        ("_commit", 2, "reversed"),  # commit 1 (the first commit is the checkpoint of the staged file)
        ("_record_applied", 1, "finished"),  # after commit 1, in the content step
        ("_commit", 3, "finished"),  # commit 2
    ],
)
def test_a_kill_anywhere_then_recovery_keeps_the_invariants(
    hostile: tuple[Path, Path, layout_migrate.Plan],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    nth: int,
    outcome: str,
) -> None:
    vault, outside, plan = hostile
    before = _snapshot(vault)
    real = getattr(layout_migrate, name)
    calls = {"n": 0}

    def maybe_die(*a: object, **k: object) -> object:
        calls["n"] += 1
        if calls["n"] == nth:
            raise Killed
        return real(*a, **k)

    monkeypatch.setattr(layout_migrate, name, maybe_die)
    with pytest.raises(Killed):
        layout_migrate.migrate_layout(vault)
    monkeypatch.setattr(layout_migrate, name, real)
    assert layout_migrate.recover(vault) == outcome
    assert not layout.move_in_progress(vault)
    if outcome == "reversed":
        assert _snapshot(vault) == before  # every file, untracked, ignored and symlinks included, as it was
        assert all(ln.startswith("?? ") for ln in _status(vault)), _status(vault)
    else:
        _assert_moved_and_untouched(vault, outside, plan)


def test_vault_move_leaves_what_git_doesnt_track(hostile: tuple[Path, Path, layout_migrate.Plan]) -> None:
    vault, _outside, _plan = hostile
    assert layout_migrate.migrate_layout(vault).state == "applied"
    local = "knowledge-base/work/projects/apollo/notes.local.md"  # ignored, inside the folder that moves
    text = (vault / local).read_text()
    before = _status(vault)
    out = layout_spaces.move_to_space(vault, "knowledge-base/work/projects/apollo", "personal")
    moved = "knowledge-base/personal/projects/apollo/notes.local.md"
    assert (vault / moved).read_text() == text
    committed = set(
        _git(vault, "-c", "core.quotepath=false", "show", "--name-only", "--format=", out["commit"]).split("\n")
    )
    assert moved not in committed and moved not in _tracked(vault)
    assert all(ln.startswith("?? ") for ln in _status(vault))
    assert len(_status(vault)) == len(before)  # the untracked files are the same ones, none added

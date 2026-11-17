from __future__ import annotations

import itertools
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.changes.scan import classify, infer_why, lane_of, parse_trailers, scan
from scout.changes.store import read_changes

_clock = itertools.count(1)


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip()


def _next_date() -> str:
    return f"2026-11-17T10:{next(_clock):02d}:00Z"


def commit(repo: Path, path: str, message: str, *, date: str | None = None, content: str | None = None) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    target.write_text(content if content is not None else existing + f"{message}\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-m", message, date=date or _next_date())
    return _git(repo, "rev-parse", "HEAD")


def rename(repo: Path, old: str, new: str, message: str) -> str:
    if not (repo / old).exists():
        commit(repo, old, "seed", content="x\n")
    (repo / new).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", old, new)
    _git(repo, "commit", "-m", message, date=_next_date())
    return _git(repo, "rev-parse", "HEAD")


def edit(repo: Path, path: str, old: str, new: str, message: str) -> str:
    if not (repo / path).exists():
        commit(repo, path, "seed", content=f"---\n{old}\n---\n")
    (repo / path).write_text((repo / path).read_text().replace(old, new))
    _git(repo, "add", path)
    _git(repo, "commit", "-m", message, date=_next_date())
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.name", "Scout Test")
    _git(r, "config", "user.email", "scout@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    return r


def test_brain_edit_with_trailers(repo):
    commit(
        repo,
        "SKILL.md",
        "dreaming [22:12]: SKILL.md self-edit — x\n\nScout-Change-Why: pattern-288\n"
        "Scout-Change-Summary: Flags leave labels past their end date",
    )
    [c] = scan(repo, since=None)
    assert (c.kind, c.why.type, c.why.ref, c.why.inferred, c.author_lane) == (
        "brain-edit",
        "pattern",
        "pattern-288",
        False,
        "dreaming",
    )
    assert c.summary == "Flags leave labels past their end date"
    assert c.id == "chg-" + c.commit[:12]


def test_driver_without_trailer_infers_why(repo):
    commit(repo, "scripts/pr-state-check.py", "dreaming [22:16]: wishlist/driver — closes Pattern #271")
    [c] = scan(repo, since=None)
    assert (c.kind, c.why.ref, c.why.inferred, c.targets) == ("driver", "pattern-271", True, ("pattern-271",))


def test_scan_is_idempotent(repo):
    commit(repo, "DREAMING.md", "dreaming [18:00]: DREAMING.md self-edit — y")
    assert len(scan(repo, since=None)) == 1
    assert scan(repo, since=None) == []
    assert len(read_changes(repo)[0]) == 1


def test_non_changes_produce_no_rows(repo):
    commit(repo, "knowledge-base/projects/x/x.md", "consolidation [11:05]: closed EHYG")
    commit(repo, "action-items/action-items-2026-11-17.md", "briefing [2026-11-17]: items")
    commit(repo, "trading/log.md", "trading [15:31]: NAV $45.82")
    assert scan(repo, since=None) == []


def test_kb_rename_and_wishlist_done(repo):
    rename(repo, "knowledge-base/a.md", "knowledge-base/topics/a.md", "scout: move a")
    edit(repo, "docs/wishlist/2026-11-11-x.md", "status: open", "status: done", "dreaming [18:41]: wishlist — x done")
    got = scan(repo, since=None)
    assert sorted(c.kind for c in got) == ["kb-structure", "wishlist-done"]
    done = next(c for c in got if c.kind == "wishlist-done")
    assert (done.why.type, done.why.ref, done.why.inferred) == ("wishlist", "2026-11-11-x", True)


def test_backfilled_flag_and_since(repo):
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — old", date="2026-06-02T01:00:00Z")
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — new", date="2026-11-16T01:00:00Z")
    got = scan(repo, since=datetime(2026, 11, 1, tzinfo=UTC), backfilled=True)
    assert [c.summary for c in got] == ["SKILL.md self-edit — new"] and got[0].backfilled


def test_overlapping_windows_do_not_duplicate(repo):
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — a", date="2026-06-02T01:00:00Z")
    commit(repo, "SKILL.md", "dreaming [02:00]: SKILL.md self-edit — b", date="2026-11-16T01:00:00Z")
    assert len(scan(repo, since=datetime(2026, 11, 1, tzinfo=UTC))) == 1
    assert len(scan(repo, since=None)) == 1
    assert len(read_changes(repo)[0]) == 2


def test_non_repo_is_no_changes(tmp_path):
    assert scan(tmp_path / "missing", since=None) == []
    assert scan(tmp_path, since=None) == []


def test_revert_update_and_summary_cap(repo):
    commit(repo, "x.md", 'Revert "dreaming [01:00]: bad"')
    commit(repo, "y.md", "scout-update: upgrade to v0.19")
    commit(repo, "SKILL.md", "dreaming [03:00]: " + "w" * 300)
    got = {c.kind: c for c in scan(repo, since=None)}
    assert set(got) == {"rollback", "upgrade", "brain-edit"}
    assert got["upgrade"].author_lane == "update"
    assert len(got["brain-edit"].summary) == 120


def test_helpers():
    assert parse_trailers("s\n\nScout-Change-Targets: a, b\nScout-Change-Governance: yes") == {
        "targets": "a, b",
        "governance": "yes",
    }
    assert lane_of("trading [15:31]: x") is None
    assert lane_of("scout: x") == "interactive"
    assert lane_of("chore(scout-update): x") == "update"
    assert infer_why("nothing", "").type == "unknown"
    assert classify([("M", "README.md")], "scout: x", lambda p: "") is None

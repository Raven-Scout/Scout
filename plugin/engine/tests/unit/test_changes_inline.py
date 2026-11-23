"""``inline-comment`` rows: derived from git on every read, never stored."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.changes import inline
from scout.changes.inline import inline_comments, merge_inline
from scout.changes.models import Signal
from scout.changes.report import changes_report, engagement_report

DAY = "2026-11-17"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=3)
UTC_TZ = ZoneInfo("UTC")
_tick = iter(range(1, 1000))


def git(repo: Path, *args: str, stamp: str | None = None) -> str:
    when = stamp or f"{DAY}T10:{next(_tick):02d}:00Z"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.name", "Scout Test")
    git(r, "config", "user.email", "scout@example.invalid")
    git(r, "config", "commit.gpgsign", "false")
    return r


def commit(repo: Path, path: str, text: str, message: str, stamp: str | None = None) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message, stamp=stamp)
    return git(repo, "rev-parse", "HEAD")


def test_inline_comment_counts_once_per_interactive_commit(repo):
    text = "intro\n//==<< tighten this\nmore\nscout: user: and this too\n"
    sha = commit(repo, "action-items/a.md", text, "scout: review notes", stamp=f"{DAY}T10:01:00Z")
    commit(repo, "action-items/b.md", text, "dreaming [22:00]: tidy")
    rows = inline_comments(repo, since=SINCE, now=NOW)
    assert [(s.kind, s.surface, s.ref) for s in rows] == [("inline-comment", "kb", sha)]
    assert rows[0].ts == datetime(2026, 11, 17, 10, 1, tzinfo=UTC)


def test_inline_comment_needs_an_added_marker(repo):
    commit(repo, "a.md", "keep\n//==<< old note\n", "scout: add note", stamp="2026-11-10T10:00:00Z")
    commit(repo, "a.md", "keep\n", "scout: remove the note")
    assert inline_comments(repo, since=SINCE, now=NOW) == []


def test_a_marker_in_a_file_name_is_not_an_add(repo):
    commit(repo, "a.md", "x\n", "scout: seed", stamp="2026-11-10T10:00:00Z")
    commit(repo, "scout: user: a.md", "plain\n", "scout: odd file name")  # the marker is only in the diff header
    assert inline_comments(repo, since=SINCE, now=NOW) == []


def test_inline_comment_respects_the_window(repo):
    commit(repo, "a.md", "//==<< early\n", "scout: early", stamp="2026-11-01T10:00:00Z")
    commit(repo, "b.md", "//==<< late\n", "scout: late", stamp="2026-11-19T10:00:00Z")
    assert inline_comments(repo, since=SINCE, now=NOW) == []


def test_inline_comment_is_empty_without_git(tmp_path):
    (tmp_path / "v").mkdir()
    assert inline_comments(tmp_path / "v", since=SINCE, now=NOW) == []


def test_inline_comment_skips_unparseable_records(repo, monkeypatch):
    monkeypatch.setattr(inline, "_git", lambda *a, **k: "\x1ejunk\n\x1eabc\x1fnot-a-date\x1fscout: x\n+nope\n")
    assert inline_comments(repo, since=SINCE, now=NOW) == []


def test_merge_does_not_double_count_a_stored_row():
    stored = Signal(ts=NOW, kind="inline-comment", surface="kb", ref="abc")
    derived = [stored, Signal(ts=NOW, kind="inline-comment", surface="kb", ref="def")]
    assert [s.ref for s in merge_inline([stored], derived)] == ["abc", "def"]


def test_engagement_report_counts_inline_comments_as_feedback(repo, tmp_path):
    commit(repo, "a.md", "//==<< please change\n", "scout: owner comment")
    doc = engagement_report(repo, {"HOME": str(tmp_path / "home")}, now=NOW, since=SINCE, tz=UTC_TZ)
    day = next(d for d in doc["days"] if d["date"] == DAY)
    assert day["feedback"] == 1 and day["by_surface"]["feedback"] == {"kb": 1}
    assert doc["totals"]["feedback"] == 1


def test_changes_report_runs_with_inline_comments_present(repo, tmp_path):
    commit(repo, "a.md", "//==<< please change\n", "scout: owner comment")
    doc = changes_report(repo, now=NOW, since=SINCE, env={"HOME": str(tmp_path / "home")}, tz=UTC_TZ)
    assert doc["changes"] == [] and doc["schema_version"] == 1

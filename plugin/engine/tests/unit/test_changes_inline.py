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
    listing = "\x1ejunk\n\x1eabc\x1fnot-a-date\x1fscout: x\n"
    monkeypatch.setattr(inline, "_git_timed", lambda *a, **k: (listing, False))
    assert inline_comments(repo, since=SINCE, now=NOW) == []


def test_inline_comment_skips_an_unparseable_patch_record(repo, monkeypatch):
    sha = commit(repo, "a.md", "//==<< note\n", "scout: owner comment")
    real = inline._git_timed

    def odd_patch(vault, *args):
        if "-p" in args:
            return "\x1e\n+//==<< a record with no sha\n", False
        return real(vault, *args)

    monkeypatch.setattr(inline, "_git_timed", odd_patch)
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    monkeypatch.setattr(inline, "_git_timed", real)
    assert [s.ref for s in inline_comments(repo, since=SINCE, now=NOW)] == [sha]


def test_inline_comment_reads_markdown_only_and_diffs_interactive_commits_only(repo, monkeypatch):
    """I5: the listing reads the window over `*.md` without diffs; only interactive commits are diffed."""
    owner = commit(repo, "notes/a.md", "//==<< tighten this\n", "scout: review notes")
    commit(repo, "notes/b.md", "//==<< a run's own line\n", "dreaming [22:00]: tidy")
    commit(repo, "scripts/c.py", "# //==<< not a vault note\n", "scout: a script")
    calls: list[tuple[str, ...]] = []
    real = inline._git_timed

    def spy(vault, *args):
        calls.append(args)
        return real(vault, *args)

    monkeypatch.setattr(inline, "_git_timed", spy)
    rows = inline_comments(repo, since=SINCE, now=NOW)
    assert [s.ref for s in rows] == [owner]
    listing, diffs = calls
    assert listing[-2:] == ("--", "*.md") and diffs[-2:] == ("--", "*.md")
    assert f"--since={(SINCE - timedelta(days=1)).isoformat()}" in listing
    assert f"--until={(NOW + timedelta(days=1)).isoformat()}" in listing
    assert "-p" not in listing and "-p" in diffs
    assert owner in diffs and not any(a for a in diffs if len(a) == 40 and a != owner)


def test_no_interactive_commit_means_no_diff_call(repo, monkeypatch):
    commit(repo, "a.md", "//==<< a run's own line\n", "dreaming [22:00]: tidy")
    calls: list[tuple[str, ...]] = []
    real = inline._git_timed
    monkeypatch.setattr(inline, "_git_timed", lambda vault, *args: calls.append(args) or real(vault, *args))
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    assert len(calls) == 1


def test_a_git_timeout_warns_once_and_counts_nothing(repo, monkeypatch, capsys):
    """I5 (d): a timeout is said on stderr once per process, never silently read as zero comments."""
    commit(repo, "a.md", "//==<< note\n", "scout: owner comment")
    real = inline._git_timed
    monkeypatch.setattr(inline, "_warned", False)
    monkeypatch.setattr(inline, "_git_timed", lambda *a, **k: (None, True))
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    assert capsys.readouterr().err == "inline comments not counted: git timed out\n"
    # A timeout on the diff step warns too.
    monkeypatch.setattr(inline, "_warned", False)
    monkeypatch.setattr(inline, "_git_timed", lambda vault, *args: (None, True) if "-p" in args else real(vault, *args))
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    assert capsys.readouterr().err == "inline comments not counted: git timed out\n"


def test_the_git_runner_tells_a_timeout_from_a_failure(repo, monkeypatch):
    from scout.changes import scan

    assert scan._git_timed(repo, "rev-parse", "--git-dir") == (".git\n", False)
    assert scan._git_timed(repo, "no-such-command") == (None, False)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=scan.GIT_TIMEOUT_S)

    monkeypatch.setattr(scan.subprocess, "run", slow)
    assert scan._git_timed(repo, "log") == (None, True)
    assert scan._git(repo, "log") is None


def test_a_git_failure_that_is_not_a_timeout_is_silent(repo, monkeypatch, capsys):
    monkeypatch.setattr(inline, "_warned", False)
    monkeypatch.setattr(inline, "_git_timed", lambda *a, **k: (None, False))
    assert inline_comments(repo, since=SINCE, now=NOW) == []
    assert capsys.readouterr().err == ""


def test_many_interactive_commits_are_diffed_in_chunks(repo, monkeypatch):
    monkeypatch.setattr(inline, "DIFF_CHUNK", 2)
    shas = [commit(repo, f"n{i}.md", f"//==<< note {i}\n", f"scout: note {i}") for i in range(5)]
    assert sorted(s.ref for s in inline_comments(repo, since=SINCE, now=NOW)) == sorted(shas)


def test_changes_report_bounds_the_inline_window_to_what_verdicts_read(tmp_path, monkeypatch):
    """I5 (a): from the oldest tested change's baseline (14 local days before it, plus a day of slack), not the
    report's whole `since`; no tested change, no git call at all."""
    from dataclasses import replace

    from scout.changes import report
    from scout.changes.models import Change, Why
    from scout.changes.store import append_change

    vault = tmp_path / "plain"  # not a repository: the ledger rows are shown as they are
    vault.mkdir()
    env = {"HOME": str(tmp_path / "home")}
    seen: list[datetime] = []
    monkeypatch.setattr(report, "inline_comments", lambda vault, *, since, now: seen.append(since) or [])
    changes_report(vault, now=NOW, since=NOW - timedelta(days=3650), env=env, tz=UTC_TZ)
    assert seen == []
    tested = Change(
        id="chg-0123456789ab",
        ts=NOW - timedelta(days=5),
        kind="brain-edit",
        summary="a tested change",
        why=Why("unknown", "", inferred=True),
        commit="0123456789ab" + "0" * 28,
        files=("SKILL.md",),
        targets=(),
        governance=False,
        author_lane="dreaming",
    )
    append_change(vault, tested)
    # An older change that isn't tested (the owner's own) needs no inline comments: it doesn't widen the window.
    old = replace(tested, id="chg-0123456789ac", commit="0123456789ac" + "0" * 28, ts=NOW - timedelta(days=400))
    append_change(vault, replace(old, author_lane="interactive"))
    changes_report(vault, now=NOW, since=NOW - timedelta(days=3650), env=env, tz=UTC_TZ)
    assert seen == [NOW - timedelta(days=5) - timedelta(days=15)]


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

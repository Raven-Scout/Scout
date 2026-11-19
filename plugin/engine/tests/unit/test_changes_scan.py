from __future__ import annotations

import itertools
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.changes.models import Change, Why
from scout.changes.report import changes_report
from scout.changes.scan import classify, derive, infer_why, lane_of, parse_trailers, scan, scan_new
from scout.changes.store import SCAN_MARK_NAME, append_change, read_changes, read_scan_mark, write_scan_mark

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


def test_revert_touching_brain_file_is_rollback(repo):
    commit(repo, "SKILL.md", 'Revert "dreaming [22:12]: SKILL.md self-edit"')
    [c] = scan(repo, since=None)
    assert c.kind == "rollback"


def test_non_ascii_path_is_recorded_unquoted(repo):
    commit(repo, "scripts/café.py", "dreaming [22:16]: driver — café")
    [c] = scan(repo, since=None)
    assert c.files == ("scripts/café.py",)


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


def test_wishlist_why_needs_a_wishlist_path_somewhere():
    assert infer_why("scout: wishlist — done", "", ("README.md",)).type == "unknown"
    # The first file names no wishlist item; a later one does.
    why = infer_why("scout: wishlist — done", "", ("README.md", "docs/wishlist/red-carry.md"))
    assert (why.type, why.ref, why.inferred) == ("wishlist", "red-carry", True)


def test_unrecognised_why_trailer_is_kept_as_unknown_not_inferred(repo):
    commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — x\n\nScout-Change-Why: vibes-only")
    [c] = scan(repo, since=None)
    assert (c.why.type, c.why.ref, c.why.inferred) == ("unknown", "vibes-only", False)


def test_targets_trailer_overrides_the_inferred_target(repo):
    commit(
        repo,
        "SKILL.md",
        "dreaming [22:12]: SKILL.md self-edit — closes Pattern #12\n\nScout-Change-Targets: briefing, , dreaming",
    )
    [c] = scan(repo, since=None)
    assert c.targets == ("briefing", "dreaming")


def test_scan_returns_nothing_when_git_cannot_run(repo, monkeypatch):
    from scout.changes import scan as scan_mod

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(scan_mod.subprocess, "run", boom)
    assert scan(repo, since=None) == []


def test_parse_log_skips_blank_short_and_undated_records():
    from scout.changes.scan import _RS, _US, _parse_log

    def rec(*parts: str) -> str:
        return _RS + _US.join(parts)

    raw = "".join(
        [
            _RS + "   ",
            rec("a" * 40, "2026-11-17T10:00:00Z", "subject"),  # too few fields
            rec("b" * 40, "not-a-date", "subject", "", "M\tSKILL.md"),
            rec("c" * 40, "2026-11-17T10:00:00Z", "subject", "body", "M\tSKILL.md\n\nR090\told.md\tnew.md\nstray"),
        ]
    )
    [(sha, ts, subject, body, paths)] = _parse_log(raw)
    assert (sha, subject, body) == ("c" * 40, "subject", "body")
    assert ts.year == 2026
    assert paths == [("M", "SKILL.md"), ("R090", "new.md")]


# --- the watermark: a plain `changes scan` reads from where the last one stopped (coordinator review item 2) ---

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def test_first_watermark_scan_reads_the_last_14_days_and_sets_the_mark(repo):
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — old", date="2026-10-01T01:00:00Z")
    head = commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — recent", date="2026-11-10T01:00:00Z")
    assert [c.summary for c in scan_new(repo, now=NOW)] == ["SKILL.md self-edit — recent"]
    assert read_scan_mark(repo) == head
    assert scan_new(repo, now=NOW) == []


def test_a_gap_longer_than_any_window_loses_nothing(repo):
    """Runs skipped for three weeks (gates, budget, a lock): the next scan still reads every commit since the mark,
    including one dated long before a fixed window would reach."""
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — seen", date="2026-10-20T01:00:00Z")
    assert len(scan_new(repo, now=datetime(2026, 10, 20, 2, 0, tzinfo=UTC))) == 1
    commit(repo, "scripts/gap.py", "scout: interactive driver during the gap", date="2026-10-21T09:00:00Z")
    head = commit(repo, "DREAMING.md", "dreaming [18:30]: DREAMING.md self-edit", date="2026-11-16T18:30:00Z")
    got = scan_new(repo, now=NOW)
    assert sorted((c.kind, c.author_lane) for c in got) == [("brain-edit", "dreaming"), ("driver", "interactive")]
    assert read_scan_mark(repo) == head
    assert len(read_changes(repo)[0]) == 3


def test_explicit_since_does_not_move_the_mark(repo):
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — a", date="2026-11-10T01:00:00Z")
    assert len(scan_new(repo, now=NOW)) == 1
    mark = read_scan_mark(repo)
    commit(repo, "scripts/old.py", "scout: driver", date="2026-11-11T01:00:00Z")
    commit(repo, "scripts/new.py", "scout: driver", date="2026-11-16T01:00:00Z")
    assert len(scan(repo, since=datetime(2026, 11, 15, tzinfo=UTC))) == 1
    assert read_scan_mark(repo) == mark
    # The plain scan still reads the commit the narrow window skipped, and not the one it already took.
    assert [c.files for c in scan_new(repo, now=NOW)] == [("scripts/old.py",)]


def test_a_mark_whose_commit_is_gone_falls_back_to_the_window(repo):
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — old", date="2026-10-01T01:00:00Z")
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — recent", date="2026-11-10T01:00:00Z")
    write_scan_mark(repo, "f" * 40, NOW)
    assert [c.summary for c in scan_new(repo, now=NOW)] == ["SKILL.md self-edit — recent"]


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[1]",
        '{"schema": 2, "head": "' + "a" * 40 + '"}',
        '{"schema": 1, "head": "--output=/tmp/x"}',  # a run can write .scout-logs/: never an option to git
        '{"schema": 1, "head": 7}',
        '{"schema": 1, "head": "' + "A" * 40 + '"}',
    ],
)
def test_unusable_mark_records_are_no_mark(repo, text):
    (repo / ".scout-logs").mkdir()
    (repo / ".scout-logs" / SCAN_MARK_NAME).write_text(text)
    assert read_scan_mark(repo) is None


def test_a_sha256_mark_is_accepted(repo):
    write_scan_mark(repo, "b" * 64, NOW)
    assert read_scan_mark(repo) == "b" * 64


def test_a_failed_log_read_leaves_the_mark_and_records_nothing(repo, monkeypatch):
    from scout.changes import scan as scan_mod

    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — a", date="2026-11-10T01:00:00Z")
    real = scan_mod._git
    monkeypatch.setattr(scan_mod, "_git", lambda vault, *args: None if args[0] == "log" else real(vault, *args))
    assert scan_new(repo, now=NOW) == []
    assert read_scan_mark(repo) is None
    assert scan(repo, since=None) == []


def test_a_git_timeout_is_no_new_changes(repo, monkeypatch):
    from scout.changes import scan as scan_mod

    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — a", date="2026-11-10T01:00:00Z")
    seen = {}

    def hang(cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(scan_mod.subprocess, "run", hang)
    assert scan_new(repo, now=NOW) == []
    assert scan(repo, since=None) == []
    assert seen["timeout"] == scan_mod.GIT_TIMEOUT_S == 30.0
    assert read_scan_mark(repo) is None


def test_a_repo_without_commits_scans_nothing_and_sets_no_mark(repo, tmp_path):
    assert scan_new(repo, now=NOW) == []
    assert scan_new(tmp_path / "not-a-repo", now=NOW) == []
    assert not (repo / ".scout-logs" / SCAN_MARK_NAME).exists()


@pytest.mark.parametrize("failing", ["replace", "mkstemp"])
def test_a_mark_that_fails_to_save_is_swallowed_and_leaves_no_temp_file(repo, monkeypatch, failing):
    from scout.changes import store

    def boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(store.os if failing == "replace" else store.tempfile, failing, boom)
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — a", date="2026-11-10T01:00:00Z")
    assert len(scan_new(repo, now=NOW)) == 1
    assert sorted(p.name for p in (repo / ".scout-logs").iterdir()) == ["changes.jsonl"]


# --- rows that can't be forged: the report re-derives every row from git (parked M3) ---


def test_trailer_reverts_makes_a_rollback_row(repo):
    commit(repo, "SKILL.md", "dreaming [22:12]: undo it\n\nScout-Change-Reverts: chg-0123456789ab")
    [c] = scan(repo, since=None)
    assert (c.kind, c.reverts) == ("rollback", "chg-0123456789ab")


def test_git_revert_body_sets_reverts(repo):
    target = commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — x")
    commit(repo, "SKILL.md", f'Revert "x"\n\nThis reverts commit {target}.')
    got = {c.kind: c for c in scan(repo, since=None)}
    assert got["rollback"].reverts == "chg-" + target[:12]
    assert got["brain-edit"].reverts is None


def test_a_malformed_reverts_trailer_is_still_a_rollback_without_a_target(repo):
    commit(repo, "SKILL.md", "dreaming [22:12]: undo\n\nScout-Change-Reverts: not-an-id")
    [c] = scan(repo, since=None)
    assert (c.kind, c.reverts) == ("rollback", None)


def test_verdict_why_trailer(repo):
    commit(repo, "SKILL.md", "dreaming [22:12]: x\n\nScout-Change-Why: verdict:chg-0123456789ab")
    [c] = scan(repo, since=None)
    assert (c.why.type, c.why.ref, c.why.inferred) == ("verdict", "verdict:chg-0123456789ab", False)


def _seed(repo: Path, sha: str, **fields):
    row = {
        "id": "chg-" + sha[:12],
        "ts": NOW,
        "kind": "brain-edit",
        "summary": "seeded",
        "why": Why("unknown", ""),
        "commit": sha,
        "files": ("SKILL.md",),
    }
    append_change(repo, Change(**{**row, **fields}))


def _report(repo: Path) -> dict:
    return changes_report(repo, now=datetime(2027, 1, 1, tzinfo=UTC), since=datetime(2026, 1, 1, tzinfo=UTC))


def test_report_drops_a_seeded_row_for_a_non_change_commit(repo):
    sha = commit(repo, "trading/log.md", "trading [15:31]: NAV $45.82")
    _seed(repo, sha)
    doc = _report(repo)
    assert doc["changes"] == [] and doc["skipped_rows"] == 1 and doc["verified"] is True


def test_report_drops_rows_with_a_mismatched_id_or_a_malformed_commit(repo):
    sha = commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — x")
    _seed(repo, sha, id="chg-" + "0" * 12)
    _seed(repo, "--output=/tmp/x", id="chg-" + "1" * 12)
    _seed(repo, "f" * 40)  # a well-formed sha git doesn't have
    doc = _report(repo)
    assert doc["changes"] == [] and doc["skipped_rows"] == 3 and doc["verified"] is True


def test_report_overrides_a_seeded_rows_fields_with_gits(repo):
    sha = commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — real")
    _seed(repo, sha, governance=True, summary="x", kind="driver", backfilled=True)
    [row] = _report(repo)["changes"]
    assert (row["governance"], row["summary"], row["kind"]) == (False, "SKILL.md self-edit — real", "brain-edit")
    assert row["backfilled"] is True  # the one field git can't know


def test_report_without_git_marks_unverified(tmp_path):
    _seed(tmp_path, "a" * 40, summary="as written")
    doc = _report(tmp_path)
    assert doc["verified"] is False and [c["summary"] for c in doc["changes"]] == ["as written"]


def test_derive_maps_each_sha_and_is_none_without_git(repo, tmp_path):
    good = commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — x")
    bad = commit(repo, "trading/log.md", "trading [15:31]: NAV")
    got = derive(repo, [good, bad, "e" * 40, "nonsense"])
    assert got is not None and got[good] is not None and got[good].id == "chg-" + good[:12]
    assert got[bad] is None and got["e" * 40] is None and got["nonsense"] is None
    assert derive(tmp_path, [good]) is None
    assert derive(repo, []) == {}


def test_git_calls_never_take_optional_locks(repo, monkeypatch):
    from scout.changes import scan as scan_mod

    seen = {}
    real = subprocess.run

    def spy(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return real(cmd, **kwargs)

    monkeypatch.setattr(scan_mod.subprocess, "run", spy)
    scan_mod._git(repo, "status")
    assert seen["env"]["GIT_OPTIONAL_LOCKS"] == "0"

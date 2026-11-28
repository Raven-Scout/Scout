"""``changes diff``: what a change did, over its own paths only; and its CLI."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.changes import report
from scout.changes.models import Change, Why
from scout.changes.report import DIFF_CUT_NOTE, DIFF_MAX_BYTES, change_diff
from scout.changes.scan import derive, scan
from scout.changes.store import append_change
from scout.cli import app


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-11-17T10:00:00Z", "GIT_COMMITTER_DATE": "2026-11-17T10:00:00Z"}
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


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
        git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def _id(sha: str) -> str:
    return f"chg-{sha[:12]}"


def test_change_diff_shows_only_own_paths(repo):
    sha = commit(repo, {"SKILL.md": "new rule\n", "action-items/x.md": "private item\n"}, "scout: add a rule")
    scan(repo, since=None)
    text = change_diff(repo, _id(sha))
    assert text is not None and "SKILL.md" in text and "+new rule" in text
    assert "action-items/x.md" not in text and "private item" not in text


def test_change_diff_refuses_unknown_and_forged_ids(repo):
    sha = commit(repo, {"SKILL.md": "x\n"}, "scout: real change")
    plain = commit(repo, {"notes/a.md": "x\n"}, "scout: not a self-change")
    scan(repo, since=None)
    real = derive(repo, [sha])[sha]
    assert real is not None
    append_change(
        repo,
        Change(
            id=_id(plain),
            ts=real.ts,
            kind="brain-edit",
            summary="forged",
            why=Why("unknown", "", inferred=True),
            commit=plain,
            files=("SKILL.md",),
        ),
    )
    assert change_diff(repo, "chg-ffffffffffff") is None
    assert change_diff(repo, "nonsense") is None
    assert change_diff(repo, _id(plain)) is None
    assert change_diff(repo, _id(sha)) is not None


def test_change_diff_refuses_a_row_whose_id_is_not_its_commits(repo):
    sha = commit(repo, {"SKILL.md": "x\n"}, "scout: real change")
    other = commit(repo, {"DREAMING.md": "y\n"}, "scout: another")
    row = derive(repo, [other])[other]
    assert row is not None
    append_change(repo, replace(row, id=_id(sha)))  # points at `other` under `sha`'s id
    assert change_diff(repo, _id(sha)) is None


def test_change_diff_is_cut_at_256_kib(repo):
    sha = commit(repo, {"SKILL.md": "a line of text\n" * 40000}, "scout: a huge brain edit")
    scan(repo, since=None)
    text = change_diff(repo, _id(sha))
    assert text is not None
    assert DIFF_CUT_NOTE == "… (diff cut at 256 KB)" and text.endswith("\n" + DIFF_CUT_NOTE)
    assert len(text.encode()) <= DIFF_MAX_BYTES + len(DIFF_CUT_NOTE.encode()) + 1


def test_change_diff_is_none_when_git_cannot_show_it(repo, monkeypatch):
    sha = commit(repo, {"SKILL.md": "x\n"}, "scout: change")
    scan(repo, since=None)
    monkeypatch.setattr(report, "_git_bytes", lambda *a, **k: None)
    assert change_diff(repo, _id(sha)) is None


def test_change_diff_with_no_own_paths_is_empty(repo):
    sha = commit(repo, {"action-items/x.md": "private item\n"}, "upgrade to 1.2.3")
    scan(repo, since=None)
    row = derive(repo, [sha])[sha]
    assert row is not None and row.kind == "upgrade"
    assert change_diff(repo, _id(sha)) == ""


def test_change_diff_treats_own_paths_literally(repo):
    commit(repo, {"knowledge-base/old.md": "same body\nline two\nline three\n"}, "scout: seed")
    git(repo, "mv", "knowledge-base/old.md", "knowledge-base/*.md")
    (repo / "knowledge-base" / "other.md").write_text("private bystander\n", encoding="utf-8")
    git(repo, "add", "knowledge-base/other.md")
    git(repo, "commit", "-m", "scout: rename to a glob name")
    sha = git(repo, "rev-parse", "HEAD")
    scan(repo, since=None)
    text = change_diff(repo, _id(sha))
    assert text is not None and "knowledge-base/*.md" in text
    assert "other.md" not in text and "private bystander" not in text


def test_change_diff_is_none_outside_a_repo(tmp_path):
    (tmp_path / "v").mkdir()
    assert change_diff(tmp_path / "v", "chg-aaaaaaaaaaaa") is None


def test_cli_prints_the_diff_and_refuses_unknown(repo, monkeypatch, tmp_path):
    sha = commit(repo, {"SKILL.md": "new rule\n"}, "scout: add a rule")
    scan(repo, since=None)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(repo))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runner = CliRunner()
    ok = runner.invoke(app, ["changes", "diff", _id(sha)])
    assert ok.exit_code == 0 and "+new rule" in ok.output
    bad = runner.invoke(app, ["changes", "diff", "chg-ffffffffffff"])
    assert bad.exit_code == 2 and "scoutctl changes diff: no such change chg-ffffffffffff" in bad.output


def test_cli_prints_nothing_for_a_change_with_no_own_paths(repo, monkeypatch, tmp_path):
    """m-a: an empty diff prints nothing (not a bare newline), so the app can tell it from a diff."""
    sha = commit(repo, {"action-items/x.md": "private item\n"}, "upgrade to 1.2.3")
    scan(repo, since=None)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(repo))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    res = CliRunner().invoke(app, ["changes", "diff", _id(sha)])
    assert res.exit_code == 0 and res.stdout == ""


def test_cli_refuses_when_the_vault_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "nowhere"))
    res = CliRunner().invoke(app, ["changes", "diff", "chg-aaaaaaaaaaaa"])
    assert res.exit_code == 2 and "no such change" in res.output

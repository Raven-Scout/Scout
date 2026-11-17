"""CLI tests for `scoutctl changes scan` and `scoutctl engagement record|consent`."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app

_runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    return h


@pytest.fixture
def vault(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


@pytest.fixture
def cli():
    return lambda args: _runner.invoke(app, args)


@pytest.fixture
def git_vault(vault: Path) -> Path:
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(vault), *a], check=True, capture_output=True, env=dict(os.environ))

    git("init", "-q")
    git("config", "user.name", "Scout Test")
    git("config", "user.email", "scout@example.invalid")
    git("config", "commit.gpgsign", "false")
    (vault / "SKILL.md").write_text("x\n")
    git("add", "SKILL.md")
    git("commit", "-q", "-m", "dreaming [22:12]: SKILL.md self-edit — x")
    return vault


def test_record_refuses_owner_kind_in_a_run(cli, vault, home, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(["engagement", "record", "session-reliance", "--surface", "kb"])
    assert r.exit_code == 2 and not any(home.rglob("engagement.jsonl"))


def test_record_owner_kind_in_an_owner_session(cli, vault, home):
    r = cli(["engagement", "record", "approval", "--surface", "action-items"])
    assert r.exit_code == 0
    assert len(list(home.rglob("engagement.jsonl"))) == 1
    assert not (vault / ".scout-logs/engagement.jsonl").exists()


def test_record_run_kind_in_a_run(cli, vault, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(
        [
            "engagement",
            "record",
            "dm-reply",
            "--surface",
            "slack-dm",
            "--ref",
            "1790330702.929009",
            "--sentiment",
            "positive",
        ]
    )
    assert r.exit_code == 0 and (vault / ".scout-logs/engagement.jsonl").read_text().count("\n") == 1


def test_record_rejects_unknown_surface(cli, vault):
    assert cli(["engagement", "record", "dm-reply", "--surface", "carrier-pigeon"]).exit_code == 2


def test_record_rejects_unknown_kind_sentiment_and_ts(cli, vault):
    assert cli(["engagement", "record", "nope", "--surface", "kb"]).exit_code == 2
    assert cli(["engagement", "record", "dm-reply", "--surface", "kb", "--sentiment", "ecstatic"]).exit_code == 2
    assert cli(["engagement", "record", "dm-reply", "--surface", "kb", "--ts", "yesterday-ish"]).exit_code == 2


def test_consent_owner_only(cli, home, vault, monkeypatch):
    assert cli(["engagement", "consent", "--show"]).output.strip() == "not asked"
    assert cli(["engagement", "consent", "--out-of-vault", "yes"]).exit_code == 0
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    assert cli(["engagement", "consent", "--out-of-vault", "no"]).exit_code == 2
    monkeypatch.delenv("SCOUT_MODE")
    assert cli(["engagement", "consent", "--show"]).output.strip() == "yes"
    [path] = home.rglob("consent.json")
    assert path.stat().st_mode & 0o777 == 0o600


def test_consent_rejects_bad_answer(cli, vault):
    assert cli(["engagement", "consent", "--out-of-vault", "maybe"]).exit_code == 2


def test_changes_scan_cli(cli, git_vault):
    assert cli(["changes", "scan"]).output.strip() == "1 new change(s)"
    assert cli(["changes", "scan"]).output.strip() == "0 new change(s)"


def test_changes_scan_bad_since(cli, git_vault):
    assert cli(["changes", "scan", "--since", "soonish"]).exit_code == 2


def test_bare_groups_print_help(cli, vault):
    assert "scan" in cli(["changes"]).output
    assert "record" in cli(["engagement"]).output

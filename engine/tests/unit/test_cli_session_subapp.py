"""CLI smoke tests for `scoutctl session {index,list,cc-cache}`. Never calls the real gh."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import scout.sessions.github as gh
from scout.cli import app
from tests.unit.sessions_helpers import support_dir, write_desktop_record

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "default_runner", lambda argv: None)


def test_index_json_prints_schema_and_writes_file(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A")
    result = runner.invoke(app, ["session", "index", "--json", "--no-gh"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and len(payload["sessions"]) == 1
    assert (fake_data_dir / ".scout-cache" / "sessions-index.json").exists()
    assert not (fake_data_dir / ".scout-cache" / "cc-sessions.md").exists()


def test_index_render_writes_digest_and_prints_summary_line(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A")
    result = runner.invoke(app, ["session", "index", "--render", "--no-gh", "--timezone", "UTC"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "sessions-index.json" in result.stdout and "1 sessions" in result.stdout
    assert (
        (fake_data_dir / ".scout-cache" / "cc-sessions.md")
        .read_text(encoding="utf-8")
        .startswith("# Claude Code Sessions")
    )


def test_index_strict_fails_on_source_error(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_desktop_record(support_dir(), "local_A", prs=[{"prNumber": 1, "repo": "example-org/example-repo"}])
    monkeypatch.setattr(gh, "gh_available", lambda: False)  # use_gh on, gh missing → one source_error
    ok = runner.invoke(app, ["session", "index"])
    assert ok.exit_code == 0
    strict = runner.invoke(app, ["session", "index", "--strict"])
    assert strict.exit_code == 1 and "gh not found" in strict.stderr


def test_index_returns_1_when_cache_dir_unwritable(fake_data_dir: Path) -> None:
    cache = fake_data_dir / ".scout-cache"
    for p in cache.iterdir():
        p.unlink()
    cache.rmdir()
    cache.write_text("blocker", encoding="utf-8")
    result = runner.invoke(app, ["session", "index", "--no-gh"])
    assert result.exit_code == 1 and "could not write" in result.stderr


def test_list_filters_by_state_and_project(fake_data_dir: Path) -> None:
    s = support_dir()
    write_desktop_record(s, "local_A", title="Alpha work")
    write_desktop_record(s, "local_B", title="Beta work", isArchived=True)
    write_desktop_record(
        s, "local_C", title="Gamma work", cwd="/Users/alex/code/other", originCwd="/Users/alex/code/other"
    )
    runner.invoke(app, ["session", "index", "--no-gh"])

    default = runner.invoke(app, ["session", "list"])
    assert default.exit_code == 0 and "Alpha work" in default.stdout and "Gamma work" in default.stdout
    assert "Beta work" not in default.stdout  # archived hidden by default

    archived = runner.invoke(app, ["session", "list", "--include-archived", "--state", "done"])
    assert "Beta work" in archived.stdout and "Alpha work" not in archived.stdout

    by_project = runner.invoke(app, ["session", "list", "--project", "other", "--json"])
    rows = json.loads(by_project.stdout)
    assert [r["title"] for r in rows] == ["Gamma work"]


def test_list_builds_index_when_missing(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A", title="Fresh")
    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0 and "Fresh" in result.stdout
    assert (fake_data_dir / ".scout-cache" / "sessions-index.json").exists()


def test_cc_cache_alias_still_writes_digest_with_legacy_flags(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gh, "gh_available", lambda: False)
    result = runner.invoke(
        app, ["session", "cc-cache", "--hours", "12", "--instance-name", "Scout", "--timezone", "UTC"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    digest = (fake_data_dir / ".scout-cache" / "cc-sessions.md").read_text(encoding="utf-8")
    assert "last 12h" in digest
    assert "CC session cache written to" in result.stdout

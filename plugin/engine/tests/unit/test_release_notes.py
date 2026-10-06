"""Golden-file tests for the combined GitHub release body (spec §3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scout.scripts import release_notes

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "release_notes"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_extract_section_stops_at_the_next_version():
    body = release_notes.extract_section(_read("plugin-CHANGELOG.md"), "0.15.0")
    assert body.startswith("### Added") and "Older entry" not in body and "Next fix" not in body


def test_extract_unreleased_and_missing():
    assert "Next fix" in release_notes.extract_section(_read("plugin-CHANGELOG.md"), "Unreleased")
    assert release_notes.extract_section(_read("plugin-CHANGELOG.md"), "9.9.9") == ""


def test_render_release_matches_golden():
    out = release_notes.render(
        "0.15.0",
        app=release_notes.extract_section(_read("app-CHANGELOG.md"), "0.15.0"),
        plugin=release_notes.extract_section(_read("plugin-CHANGELOG.md"), "0.15.0"),
        prev_tag="plugin/v0.14.0",
        repo_slug="Raven-Scout/Scout",
    )
    assert out == _read("expected-release.md")


def test_extract_section_stops_at_any_level_two_heading():
    """The app changelog ends with `## Releases before the monorepo`: not a version, still a boundary."""
    body = release_notes.extract_section(_read("app-CHANGELOG-trailing-section.md"), "0.15.0")
    assert body.endswith("**Onboarding installs the engine.**")
    assert "Something else" not in body and "never reach" not in body


def test_render_release_with_a_trailing_section_matches_golden():
    out = release_notes.render(
        "0.15.0",
        app=release_notes.extract_section(_read("app-CHANGELOG-trailing-section.md"), "0.15.0"),
        plugin=release_notes.extract_section(_read("plugin-CHANGELOG.md"), "0.15.0"),
        prev_tag="plugin/v0.14.0",
        repo_slug="Raven-Scout/Scout",
    )
    assert out == _read("expected-release.md")


def test_render_empty_app_section_and_no_prev():
    out = release_notes.render(
        "0.14.0",
        app=release_notes.extract_section(_read("app-CHANGELOG.md"), "0.14.0"),
        plugin=release_notes.extract_section(_read("plugin-CHANGELOG.md"), "0.14.0"),
        prev_tag=None,
        repo_slug="Raven-Scout/Scout",
    )
    assert out == _read("expected-no-app-changes.md")


def _repo(tmp_path: Path) -> Path:
    for rel, name in (("plugin/CHANGELOG.md", "plugin-CHANGELOG.md"), ("apps/macos/CHANGELOG.md", "app-CHANGELOG.md")):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(_read(name), encoding="utf-8")
    return tmp_path


def test_cli_rc_renders_the_version_section(tmp_path):
    repo, out = _repo(tmp_path), tmp_path / "notes.md"
    cmd = [sys.executable, "-m", "scout.scripts.release_notes", "--repo-root", str(repo), "0.15.0"]
    cmd += ["--repo", "Raven-Scout/Scout", "--rc", "v0.15.0-rc.1", "--out", str(out)]
    subprocess.run(cmd, check=True)
    assert out.read_text(encoding="utf-8") == _read("expected-rc.md")


def test_cli_refuses_when_both_sections_are_empty(tmp_path):
    repo = _repo(tmp_path)
    cmd = [sys.executable, "-m", "scout.scripts.release_notes", "--repo-root", str(repo), "9.9.9"]
    cmd += ["--repo", "Raven-Scout/Scout", "--out", str(tmp_path / "n.md")]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 1 and "no [9.9.9] section in either changelog" in done.stderr


def test_cli_fails_with_missing_app_changelog(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plugin").mkdir()
    (repo / "plugin/CHANGELOG.md").write_text(_read("plugin-CHANGELOG.md"), encoding="utf-8")
    cmd = [sys.executable, "-m", "scout.scripts.release_notes", "--repo-root", str(repo), "0.15.0"]
    cmd += ["--repo", "Raven-Scout/Scout", "--out", str(tmp_path / "n.md")]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 1 and "missing apps/macos/CHANGELOG.md" in done.stderr and "Traceback" not in done.stderr


# --- in-process main() coverage ---------------------------------------------
#
# The subprocess tests above exercise the real CLI end to end, but
# `sys.executable -m ...` runs in a child process that coverage.py can't see.
# These call `release_notes.main([...])` directly so `main()` itself — and the
# `rc` banner branch inside `render()` that only main's `--rc` plumbs into —
# are measured.


def test_main_rc_happy_path_writes_the_expected_file(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "notes.md"
    rc = release_notes.main(
        ["--repo-root", str(repo), "0.15.0", "--repo", "Raven-Scout/Scout", "--rc", "v0.15.0-rc.1", "--out", str(out)]
    )
    assert rc == 0
    assert out.read_text(encoding="utf-8") == _read("expected-rc.md")


def test_main_fails_with_missing_plugin_changelog(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "apps/macos").mkdir(parents=True)
    (repo / "apps/macos/CHANGELOG.md").write_text(_read("app-CHANGELOG.md"), encoding="utf-8")
    out = tmp_path / "n.md"
    rc = release_notes.main(["--repo-root", str(repo), "0.15.0", "--repo", "Raven-Scout/Scout", "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing plugin/CHANGELOG.md" in err and "Traceback" not in err
    assert not out.exists()


def test_main_fails_with_missing_app_changelog(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "plugin").mkdir(parents=True)
    (repo / "plugin/CHANGELOG.md").write_text(_read("plugin-CHANGELOG.md"), encoding="utf-8")
    out = tmp_path / "n.md"
    rc = release_notes.main(["--repo-root", str(repo), "0.15.0", "--repo", "Raven-Scout/Scout", "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing apps/macos/CHANGELOG.md" in err and "Traceback" not in err
    assert not out.exists()


def test_main_fails_when_no_section_in_either_changelog(tmp_path, capsys):
    repo = _repo(tmp_path)
    out = tmp_path / "n.md"
    rc = release_notes.main(["--repo-root", str(repo), "9.9.9", "--repo", "Raven-Scout/Scout", "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no [9.9.9] section in either changelog" in err and "Traceback" not in err
    assert not out.exists()

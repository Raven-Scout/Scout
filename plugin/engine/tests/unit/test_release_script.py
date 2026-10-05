"""Behaviour tests for scripts/release.sh. Every signing/publishing tool is a stub (release_harness)."""

from __future__ import annotations

import shutil
import subprocess

from tests.unit.release_harness import make_repo


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_harness_shadows_every_signing_and_publishing_tool(tmp_path):
    r = make_repo(tmp_path)
    for tool in ("gh", "xcodebuild", "codesign", "xcrun", "spctl", "hdiutil", "ditto", "security"):
        found = shutil.which(tool, path=r.env["PATH"])
        assert found and found.startswith(str(tmp_path / "stubs")), tool


def test_prepare_bumps_all_five_places_and_both_changelogs(tmp_path):
    r = make_repo(tmp_path)
    done = r.run("prepare", extra_env={"SKIP_RELEASE": "1"})
    assert done.returncode == 0, done.stderr
    assert _git(r.root, "rev-parse", "--abbrev-ref", "HEAD") == "release/v0.15.0"  # feat: since plugin/v0.14.0
    assert _git(r.root, "log", "-1", "--format=%s") == "release: v0.15.0"
    pbx = (r.root / "apps/macos/Scout.xcodeproj/project.pbxproj").read_text()
    assert pbx.count("MARKETING_VERSION = 0.15.0;") == 4
    for rel in ("plugin/.claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        assert '"0.15.0"' in (r.root / rel).read_text()
    for rel in ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md"):
        text = (r.root / rel).read_text()
        assert text.index("## [Unreleased]") < text.index("## [0.15.0] - ")


def test_prepare_dry_run_pushes_nothing_and_opens_no_pr(tmp_path):
    r = make_repo(tmp_path)
    assert r.run("prepare", "patch", extra_env={"SKIP_RELEASE": "1"}).returncode == 0
    assert not any(c.startswith("gh ") for c in r.calls())
    assert _git(r.origin, "branch", "--list", "release/*") == ""


def test_prepare_pushes_the_branch_and_opens_the_pr(tmp_path):
    r = make_repo(tmp_path)
    done = r.run("prepare", "patch")
    assert done.returncode == 0, done.stderr
    assert "release/v0.14.1" in _git(r.origin, "branch", "--list", "release/*")
    pr = [c for c in r.calls() if c.startswith("gh pr create")]
    assert len(pr) == 1 and "--repo Raven-Scout/Scout" in pr[0] and "--title release: v0.14.1" in pr[0]


def test_prepare_refuses_a_dirty_tree(tmp_path):
    r = make_repo(tmp_path)
    (r.root / "stray.txt").write_text("x")
    done = r.run("prepare")
    assert done.returncode != 0 and "not clean" in done.stderr


def test_prepare_refuses_off_main_and_out_of_sync(tmp_path):
    r = make_repo(tmp_path)
    _git(r.root, "switch", "-q", "-c", "other")
    assert "run from main" in r.run("prepare").stderr
    _git(r.root, "switch", "-q", "main")
    _git(r.root, "commit", "-q", "--allow-empty", "-m", "chore: local only")
    assert "not in sync" in r.run("prepare").stderr


def test_refuses_a_clone_of_another_repo(tmp_path):
    r = make_repo(tmp_path, origin_url="https://github.com/Raven-Scout/scout-app-legacy.git")
    for args in (("prepare",), ("finalize", "v0.15.0"), ("rc", "v0.15.1-rc.1")):
        done = r.run(*args)
        assert done.returncode != 0 and "not Raven-Scout/Scout" in done.stderr, args
    assert r.calls() == []

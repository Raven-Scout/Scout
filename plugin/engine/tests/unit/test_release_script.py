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


def _merge_release(r, version="0.15.0"):
    """Simulate prepare + a merged release PR: bump on main and push it."""
    assert r.run("prepare", version, extra_env={"SKIP_RELEASE": "1"}).returncode == 0
    _git(r.root, "switch", "-q", "main")
    _git(r.root, "merge", "-q", "--ff-only", f"release/v{version}")
    _git(r.root, "push", "-q", "origin", "main")
    return _git(r.root, "rev-parse", "HEAD")


def test_finalize_publishes_once_after_notarization(tmp_path):
    r = make_repo(tmp_path)
    sha = _merge_release(r)
    done = r.run("finalize", "v0.15.0")
    assert done.returncode == 0, done.stderr
    calls = r.calls()
    publish = [i for i, c in enumerate(calls) if c.startswith("gh release create")]
    notarize = [i for i, c in enumerate(calls) if c.startswith("xcrun notarytool submit")]
    assert len(publish) == 1 and len(notarize) == 2 and max(notarize) < publish[0]
    line = calls[publish[0]]
    assert f"--target {sha}" in line and " --latest " in f"{line} " and "--prerelease" not in line
    assert "Scout-0.15.0.dmg" in line and "appcast.xml" not in line
    assert "MARKETING_VERSION=0.15.0" in next(c for c in calls if c.startswith("xcodebuild"))
    assert f"CURRENT_PROJECT_VERSION={_git(r.root, 'rev-list', '--count', sha)}" in next(
        c for c in calls if c.startswith("xcodebuild")
    )
    assert _git(r.root, "ls-remote", "--tags", "origin", "v0.15.0") == ""  # gh (stubbed) owns tag creation
    assert not (r.root / ".release" / "v0.15.0").exists()


def test_finalize_attaches_the_appcast_when_present(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    assert r.run("finalize", "v0.15.0", extra_env={"FAKE_APPCAST": "1"}).returncode == 0
    assert "appcast.xml" in next(c for c in r.calls() if c.startswith("gh release create"))


def test_refuses_before_the_release_pr_merged(tmp_path):
    r = make_repo(tmp_path)
    done = r.run("finalize", "v0.15.0")
    assert done.returncode != 0 and "Merge the release PR first" in done.stderr
    assert not any(c.startswith(("xcodebuild", "gh release")) for c in r.calls())


def test_refuses_an_existing_tag(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    _git(r.root, "tag", "v0.15.0")
    done = r.run("finalize", "v0.15.0")
    assert done.returncode != 0 and "already exists" in done.stderr


def test_rejected_notarization_publishes_nothing(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0", extra_env={"FAKE_NOTARY_EXIT": "1"})
    assert done.returncode != 0
    calls = r.calls()
    assert not any(c.startswith("gh release create") for c in calls)
    assert calls[-1].startswith("xcrun notarytool submit")  # died for the right reason, not some later step


def test_rerun_after_failure_replaces_the_stale_worktree(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    assert r.run("finalize", "v0.15.0", extra_env={"FAKE_GH_EXIT": "1"}).returncode != 0
    assert (r.root / ".release" / "v0.15.0").exists()
    assert r.run("finalize", "v0.15.0").returncode == 0
    assert sum(c.startswith("gh release create") for c in r.calls()) == 2


def test_skip_flags(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0", extra_env={"SKIP_NOTARIZE": "1", "SKIP_RELEASE": "1"})
    assert done.returncode == 0, done.stderr
    assert not any(c.startswith(("xcrun", "spctl", "gh release")) for c in r.calls())


def test_skip_notarize_without_skip_release_is_refused(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0", extra_env={"SKIP_NOTARIZE": "1"})
    assert done.returncode != 0 and "SKIP_NOTARIZE=1 would publish" in done.stderr
    assert not any(c.startswith("xcodebuild") for c in r.calls())


def test_xcodebuild_failure_is_fatal_with_the_log_tailed(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0", extra_env={"FAKE_XCODEBUILD_EXIT": "65"})
    assert done.returncode != 0
    assert "fake compile failure" in done.stderr and "xcodebuild failed" in done.stderr
    assert not any(c.startswith("gh release create") for c in r.calls())


def test_bundled_engine_must_match_and_exist(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    wrong = r.run("finalize", "v0.15.0", extra_env={"FAKE_ENGINE_VERSION": "0.14.0"})
    assert wrong.returncode != 0 and "bundled engine is 0.14.0" in wrong.stderr
    missing = r.run("finalize", "v0.15.0", extra_env={"FAKE_NO_ENGINE": "1"})
    assert missing.returncode != 0 and "no bundled engine" in missing.stderr
    dry = r.run("finalize", "v0.15.0", extra_env={"FAKE_NO_ENGINE": "1", "SKIP_RELEASE": "1"})
    assert dry.returncode == 0 and "no bundled engine" in dry.stdout
    assert not any(c.startswith("gh release create") for c in r.calls())


def test_rc_is_a_prerelease_never_latest(tmp_path):
    r = make_repo(tmp_path)
    assert r.run("prepare", "0.15.0").returncode == 0  # leaves HEAD on release/v0.15.0, pushed, carrying 0.15.0
    sha = _git(r.root, "rev-parse", "HEAD")
    done = r.run("rc", "v0.15.0-rc.1")
    assert done.returncode == 0, done.stderr
    line = next(c for c in r.calls() if c.startswith("gh release create"))
    assert "--prerelease" in line and "--latest=false" in line and f"--target {sha}" in line
    assert "Scout-0.15.0-rc.1.dmg" in line and " --latest " not in f"{line} "


def test_rc_refuses_a_commit_that_does_not_carry_its_version(tmp_path):
    r = make_repo(tmp_path)  # fresh main, still 0.14.0 — no prepare has run
    done = r.run("rc", "v0.15.0-rc.1")
    assert done.returncode != 0
    assert "Cut release candidates from release/v0.15.0" in done.stderr
    assert not any(c.startswith(("xcodebuild", "gh release")) for c in r.calls())


def test_rc_and_finalize_validate_tag_shapes(tmp_path):
    r = make_repo(tmp_path)
    assert "finalize needs vX.Y.Z" in r.run("finalize", "0.15.0").stderr
    assert "use 'rc'" in r.run("finalize", "v0.15.1-rc.1").stderr
    assert "rc needs vX.Y.Z-rc.N" in r.run("rc", "v0.15.1").stderr


def test_finalize_rejects_tag_glob_over_acceptance(tmp_path):
    r = make_repo(tmp_path)
    for bad in ("v1.2.3foo", "v1.2.3.4", "v01.2.3", "v0.15.0/../../x"):
        done = r.run("finalize", bad)
        assert done.returncode != 0 and "finalize needs vX.Y.Z" in done.stderr, bad
        assert r.calls() == [], bad


def test_rc_rejects_tag_glob_over_acceptance(tmp_path):
    r = make_repo(tmp_path)
    for bad in (
        "v0.15.0-rc.1foo",
        "v0.15.0-rc.2,",
        "v0.15.0.1-rc.1",
        "v0.15.0-beta-rc.1",
        "v0.15.0-rc.0",
    ):
        done = r.run("rc", bad)
        assert done.returncode != 0 and "rc needs vX.Y.Z-rc.N" in done.stderr, bad
        assert r.calls() == [], bad


_HOOK = """#!/bin/bash
echo "sparkle-release.sh $*" >> "$FAKE_LOG"
case "$1" in appcast) [ -n "${FAKE_HOOK_NO_APPCAST:-}" ] || echo '<rss/>' > "$6" ;; esac
exit 0
"""


def _add_sparkle_hook(r):
    hook = r.root / "apps/macos/scripts/sparkle-release.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    _git(r.root, "add", "apps/macos/scripts/sparkle-release.sh")
    _git(r.root, "commit", "-q", "-m", "feat(app): sparkle release hook")
    _git(r.root, "push", "-q", "origin", "main")


def test_sparkle_hook_signs_inside_out_and_writes_the_appcast(tmp_path):
    r = make_repo(tmp_path)
    _add_sparkle_hook(r)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0")
    assert done.returncode == 0, done.stderr
    calls = r.calls()
    idx = {
        k: next(i for i, c in enumerate(calls) if c.startswith(k))
        for k in (
            "xcodebuild",
            "sparkle-release.sh preflight",
            "sparkle-release.sh sign",
            "sparkle-release.sh appcast",
            "gh release create",
        )
    }
    assert idx["xcodebuild"] < idx["sparkle-release.sh preflight"] < idx["sparkle-release.sh sign"]
    assert not any(c.startswith("codesign --force --options runtime") for c in calls)  # the hook signs the app
    last_dmg_notary = max(i for i, c in enumerate(calls) if c.startswith("xcrun stapler staple") and c.endswith(".dmg"))
    assert last_dmg_notary < idx["sparkle-release.sh appcast"] < idx["gh release create"]
    assert "appcast.xml" in calls[idx["gh release create"]]
    appcast = calls[idx["sparkle-release.sh appcast"]].split()
    assert appcast[2].endswith("Scout-0.15.0.dmg") and appcast[3:5] == ["v0.15.0", "Raven-Scout/Scout"]


def test_release_without_appcast_is_fatal_when_sparkle_is_present(tmp_path):
    r = make_repo(tmp_path)
    _add_sparkle_hook(r)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0", extra_env={"FAKE_HOOK_NO_APPCAST": "1"})
    assert done.returncode != 0 and "no appcast.xml" in done.stderr
    assert not any(c.startswith("gh release create") for c in r.calls())


def test_rc_without_appcast_only_warns(tmp_path):
    r = make_repo(tmp_path)
    _add_sparkle_hook(r)
    assert r.run("prepare", "0.15.0").returncode == 0  # leaves HEAD on release/v0.15.0, pushed, carrying 0.15.0
    done = r.run("rc", "v0.15.0-rc.1", extra_env={"FAKE_HOOK_NO_APPCAST": "1"})
    assert done.returncode == 0, done.stderr
    assert "no appcast.xml" in done.stdout
    assert "appcast.xml" not in next(c for c in r.calls() if c.startswith("gh release create"))

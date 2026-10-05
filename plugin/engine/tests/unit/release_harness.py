"""A throwaway Scout-shaped repo plus stub signing/publishing tools for testing scripts/release.sh.

SAFETY: the script runs with PATH = <stubs>:<realbin>, where realbin holds symlinks to a short allowlist
of harmless tools. No real codesign, xcrun, xcodebuild, security, spctl, hdiutil, ditto or gh is
reachable, so a missing stub fails loudly instead of signing, notarizing or publishing anything.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

REAL_REPO = Path(__file__).resolve().parents[4]
SCRIPT = REAL_REPO / "scripts" / "release.sh"
ENGINE = REAL_REPO / "plugin" / "engine"

_SAFE_TOOLS = (
    "bash sh env git date mkdir rm rmdir cp mv ln ls cat grep sed awk tr head tail dirname basename "
    "mktemp printf echo sort wc uname find xargs readlink chmod touch cut tee true false test expr id"
).split()
_STUBBED = ("gh", "xcodebuild", "codesign", "xcrun", "spctl", "hdiutil", "ditto", "security", "shellcheck")

_STUB = dedent(
    """\
    #!/bin/bash
    name="$(basename "$0")"
    echo "$name $*" >> "$FAKE_LOG"
    case "$name" in
      security) echo '  1) ABCDEF "Developer ID Application: Test (TEAMID)"' ;;
      xcodebuild)
        if [ -n "${FAKE_XCODEBUILD_EXIT:-}" ]; then
          echo "error: fake compile failure" >&2
          exit "$FAKE_XCODEBUILD_EXIT"
        fi
        dd=""; proj=""; prev=""
        for a in "$@"; do
          [ "$prev" = "-derivedDataPath" ] && dd="$a"
          [ "$prev" = "-project" ] && proj="$a"
          prev="$a"
        done
        wt="${proj%/apps/macos/Scout.xcodeproj}"
        app="$dd/Build/Products/Release/Scout.app"
        mkdir -p "$app/Contents/Resources"
        if [ -z "${FAKE_NO_ENGINE:-}" ]; then
          pin="$wt/plugin/.claude-plugin/plugin.json"
          ev="$(grep '"version"' "$pin" | head -1 | sed -E 's/.*"version": *"([^"]+)".*/\\1/')"
          printf '{"version": "%s"}\\n' "${FAKE_ENGINE_VERSION:-$ev}" > "$app/Contents/Resources/engine-release.json"
        fi
        [ -n "${FAKE_APPCAST:-}" ] && echo '<rss/>' > "$dd/appcast.xml"
        exit 0 ;;
      xcrun) [ "$1" = notarytool ] && exit "${FAKE_NOTARY_EXIT:-0}"; exit 0 ;;
      hdiutil) for last in "$@"; do :; done; mkdir -p "$(dirname "$last")"; : > "$last" ;;
      gh)
        case "$1 $2" in
          "pr create") echo "https://github.com/Raven-Scout/Scout/pull/999" ;;
          "release create")
            [ -n "${FAKE_GH_EXIT:-}" ] && exit "$FAKE_GH_EXIT"
            prerelease=""
            for a in "$@"; do [ "$a" = "--prerelease" ] && prerelease=1; done
            [ -n "$prerelease" ] || echo "$3" > "$FAKE_LOG.latest" ;;
          "api repos/Raven-Scout/Scout/releases/latest") cat "$FAKE_LOG.latest" ;;
        esac ;;
    esac
    exit 0
    """
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _seed(root: Path, version: str) -> None:
    _write(root, "plugin/.claude-plugin/plugin.json", f'{{\n  "name": "scout",\n  "version": "{version}"\n}}\n')
    _write(
        root,
        ".claude-plugin/marketplace.json",
        f'{{\n  "name": "scout-plugin",\n  "plugins": [\n    {{\n      "name": "scout",\n'
        f'      "source": "./plugin",\n      "version": "{version}"\n    }}\n  ]\n}}\n',
    )
    _write(root, "plugin/engine/pyproject.toml", f'[project]\nname = "scout-engine"\nversion = "{version}"\n')
    _write(root, "plugin/engine/scout/__init__.py", f'__version__ = "{version}"\n')
    _write(
        root,
        "apps/macos/Scout.xcodeproj/project.pbxproj",
        "".join(f"\t\t\t\tMARKETING_VERSION = {version};\n" for _ in range(4)),
    )
    _write(root, "plugin/CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n### Added\n- a plugin thing\n")
    _write(root, "apps/macos/CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n### Added\n- an app thing\n")
    _write(root, ".gitignore", ".release/\n")
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy2(SCRIPT, root / "scripts" / "release.sh")


@dataclass
class Repo:
    root: Path
    origin: Path
    log: Path
    env: dict[str, str]

    def run(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "release.sh"), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


def make_repo(tmp_path: Path, *, origin_url: str | None = None, version: str = "0.14.0") -> Repo:
    stubs, realbin = tmp_path / "stubs", tmp_path / "realbin"
    stubs.mkdir()
    realbin.mkdir()
    for name in _STUBBED:
        (stubs / name).write_text(_STUB, encoding="utf-8")
        (stubs / name).chmod(0o755)
    for tool in _SAFE_TOOLS:
        found = shutil.which(tool)
        if found:
            (realbin / tool).symlink_to(found)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    root = tmp_path / "Scout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _seed(root, version)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: seed")
    _git(root, "tag", f"plugin/v{version}")
    _git(root, "commit", "-q", "--allow-empty", "-m", "feat: a feature after the last release")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main", "--tags")
    _git(root, "branch", "-q", "--set-upstream-to=origin/main", "main")
    if origin_url:
        # Make origin *claim* another repo, so the slug check sees it. Such a test never fetches.
        _git(root, "config", "remote.origin.url", origin_url)
    log = tmp_path / "calls.log"
    env = {
        "PATH": f"{stubs}:{realbin}",
        "HOME": str(tmp_path / "home"),
        "FAKE_LOG": str(log),
        "SCOUT_PY": sys.executable,
        "PYTHONPATH": str(ENGINE),
        "SCOUT_RELEASE_SKIP_LINT": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    if not origin_url:
        env["SCOUT_REPO_SLUG"] = "Raven-Scout/Scout"
    (tmp_path / "home").mkdir()
    return Repo(root=root, origin=origin, log=log, env=env)

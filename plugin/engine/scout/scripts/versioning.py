"""Single-source-of-truth versioning for the scout plugin.

The canonical version lives in .claude-plugin/plugin.json. Four derived places
must always carry the SAME version: marketplace.json, pyproject.toml,
__init__.py, and the macOS app's project.pbxproj, whose MARKETING_VERSION is
duplicated once per build configuration (four copies in the real project,
all of which must agree). This module reads, asserts-in-sync, bumps, and
propagates that version, and promotes the CHANGELOG on release.

Writes use targeted regex so existing file formatting (JSON indentation, TOML
layout) is preserved — minimal diffs, no reserialization churn.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# engine/scout/scripts/versioning.py -> parents[3] == the plugin subtree root
PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# Root kinds for _TARGETS. marketplace.json is the repo's marketplace entry
# point and lives ABOVE the plugin subtree — `claude plugin marketplace add`
# reads it from the repo root — so it cannot resolve against PLUGIN_ROOT.
_PLUGIN = "plugin"
_REPO = "repo"

# (label, root kind, relative path, regex capturing the version as 'v', every)
# `every`: the file carries the version once per build configuration; all copies
# must agree, and set_version rewrites them all.
_TARGETS = [
    ("plugin.json", _PLUGIN, ".claude-plugin/plugin.json", re.compile(r'("version":\s*")(?P<v>[^"]+)(")'), False),
    (
        "marketplace.json",
        _REPO,
        ".claude-plugin/marketplace.json",
        re.compile(r'("plugins"[\s\S]*?"version":\s*")(?P<v>[^"]+)(")'),
        False,
    ),
    ("pyproject.toml", _PLUGIN, "engine/pyproject.toml", re.compile(r'(?m)^(version\s*=\s*")(?P<v>[^"]+)(")'), False),
    (
        "__init__.py",
        _PLUGIN,
        "engine/scout/__init__.py",
        re.compile(r'(?m)^(__version__\s*=\s*")(?P<v>[^"]+)(")'),
        False,
    ),
    (
        "MARKETING_VERSION",
        _REPO,
        "apps/macos/Scout.xcodeproj/project.pbxproj",
        re.compile(r"(?m)^(\s*MARKETING_VERSION = )(?P<v>[^;\s]+)(;)"),
        True,
    ),
]

_CHANGELOGS = ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md")
_UNRELEASED_HEADING = re.compile(r"(?m)^## \[Unreleased\][^\n]*$")
_RELEASE_TAG = re.compile(r"^(?:app/|plugin/)?v(\d+)\.(\d+)\.(\d+)$")
_FEAT = re.compile(r"^feat(\([^)]*\))?!?:")


def _target_path(kind: str, rel: str, plugin_root: Path, repo_root: Path) -> Path:
    return (plugin_root if kind == _PLUGIN else repo_root) / rel


def read_versions(root: Path = PLUGIN_ROOT, repo_root: Path | None = None) -> dict[str, str]:
    repo = root.parent if repo_root is None else repo_root
    out: dict[str, str] = {}
    for label, kind, rel, rx, every in _TARGETS:
        path = _target_path(kind, rel, root, repo)
        text = path.read_text(encoding="utf-8")
        if every:
            found = {m.group("v") for m in rx.finditer(text)}
        else:
            m = rx.search(text)
            found = {m.group("v")} if m else set()
        if not found:
            raise ValueError(f"no version field found in {path}")
        if len(found) > 1:
            raise ValueError(f"{path}: {label} differs across build configurations: {sorted(found)}")
        out[label] = found.pop()
    return out


def assert_in_sync(root: Path = PLUGIN_ROOT, repo_root: Path | None = None) -> str:
    versions = read_versions(root, repo_root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise ValueError(f"version drift across manifests: {versions}")
    return distinct.pop()


class VersionNotAboveError(ValueError):
    """An explicit next version that does not move forward from the current one."""


def bump(current: str, level: str) -> str:
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"current version {current!r} is not semver X.Y.Z")
    major, minor, patch = (int(p) for p in parts)
    if re.fullmatch(r"\d+\.\d+\.\d+", level):
        # Explicit version: must be strictly above, or a release would re-cut or go back. `set` is the escape hatch.
        if tuple(int(p) for p in level.split(".")) <= (major, minor, patch):
            raise VersionNotAboveError(f"{level} is not above the current version {current}")
        return level
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump level: {level!r} (use major|minor|patch|X.Y.Z)")


def set_version(
    root: Path = PLUGIN_ROOT,
    version: str | None = None,
    repo_root: Path | None = None,
) -> None:
    if version is None:
        raise ValueError("set_version requires a version")
    repo = root.parent if repo_root is None else repo_root
    for _label, kind, rel, rx, every in _TARGETS:
        path = _target_path(kind, rel, root, repo)
        text = path.read_text(encoding="utf-8")
        new_text, n = rx.subn(lambda m: m.group(1) + version + m.group(3), text, count=0 if every else 1)
        if n < 1:
            raise ValueError(f"failed to rewrite version in {path}")
        path.write_text(new_text, encoding="utf-8")


def _promote(path: Path, *, version: str, date: str) -> None:
    # Anchored to a heading line: header prose may quote `## [Unreleased]` inline (the app's does).
    text = path.read_text(encoding="utf-8")
    new_text, n = _UNRELEASED_HEADING.subn(lambda m: f"{m.group(0)}\n\n## [{version}] - {date}", text, count=1)
    if n == 0:
        raise ValueError(f"{path} has no '## [Unreleased]' section")
    path.write_text(new_text, encoding="utf-8")


def promote_changelog(root: Path = PLUGIN_ROOT, *, version: str, date: str) -> None:
    """Plugin changelog only. Kept for release-plugin.sh until it is retired."""
    _promote(root / "CHANGELOG.md", version=version, date=date)


def promote_changelogs(repo_root: Path, *, version: str, date: str) -> None:
    """Promote `## [Unreleased]` in both the plugin and the app changelog."""
    for rel in _CHANGELOGS:
        _promote(repo_root / rel, version=version, date=date)


def _git(repo_root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=True)
    return done.stdout


def previous_release(repo_root: Path, ref: str = "HEAD", exclude: str | None = None) -> tuple[str, str] | None:
    """The highest-versioned vX.Y.Z / app/vX.Y.Z / plugin/vX.Y.Z tag reachable from ref.

    Pre-releases (-rc.N) are never a "previous release". Equal versions (app/v0.14.0 and
    plugin/v0.14.0) tie-break to the later commit, i.e. the one with more ancestors.
    """
    best: tuple[tuple[int, int, int, int], str, str] | None = None
    for tag in _git(repo_root, "tag", "--merged", ref).split():
        m = _RELEASE_TAG.match(tag)
        if tag == exclude or not m:
            continue
        sha = _git(repo_root, "rev-list", "-n", "1", tag).strip()
        depth = int(_git(repo_root, "rev-list", "--count", sha).strip())
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)), depth)
        if best is None or key > best[0]:
            best = (key, tag, sha)
    return None if best is None else (best[1], best[2])


def recommend_level(repo_root: Path, since: str | None, ref: str = "HEAD") -> str:
    """minor if any non-merge commit subject since `since` is a feat:, else patch."""
    span = f"{since}..{ref}" if since else ref
    subjects = _git(repo_root, "log", span, "--no-merges", "--format=%s").splitlines()
    return "minor" if any(_FEAT.match(s) for s in subjects) else "patch"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root, repo = PLUGIN_ROOT, PLUGIN_ROOT.parent
    if argv[:1] == ["--repo-root"]:
        if len(argv) < 2:
            print("--repo-root requires a path", file=sys.stderr)
            return 2
        repo = Path(argv[1]).resolve()
        root = repo / "plugin"
        argv = argv[2:]
    if not argv:
        print(
            "usage: versioning.py [--repo-root PATH] "
            "{check|current|next <level>|bump <level>|set <X.Y.Z>|promote <X.Y.Z> <date>|previous-release|recommend}",
            file=sys.stderr,
        )
        return 2
    cmd, rest = argv[0], argv[1:]

    def opt(name: str) -> str | None:
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else None

    if cmd == "check":
        print(assert_in_sync(root, repo))
        return 0
    if cmd == "current":
        print(read_versions(root, repo)["plugin.json"])
        return 0
    if cmd in ("next", "bump", "set"):
        if not rest:
            print(f"{cmd} requires an argument", file=sys.stderr)
            return 2
        if cmd == "set" and not re.fullmatch(r"\d+\.\d+\.\d+", rest[0]):
            print(f"set requires an X.Y.Z version, got {rest[0]!r}", file=sys.stderr)
            return 2
        try:
            new = rest[0] if cmd == "set" else bump(read_versions(root, repo)["plugin.json"], rest[0])
        except VersionNotAboveError as e:
            print(e, file=sys.stderr)
            return 1
        if cmd != "next":
            set_version(root, new, repo)
        print(new)
        return 0
    if cmd == "promote":
        if len(rest) != 2:
            print("promote requires <X.Y.Z> <YYYY-MM-DD>", file=sys.stderr)
            return 2
        promote_changelogs(repo, version=rest[0], date=rest[1])
        return 0
    if cmd == "previous-release":
        found = previous_release(repo, ref=opt("--ref") or "HEAD", exclude=opt("--exclude"))
        if found:
            print(f"{found[0]} {found[1]}")
        return 0
    if cmd == "recommend":
        print(recommend_level(repo, opt("--since"), ref=opt("--ref") or "HEAD"))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

# Unified Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One root `scripts/release.sh` that cuts Scout `vX.Y.Z`: one version across the app and the plugin, one tag, one GitHub release with the notarized DMG. Its `prepare` / `finalize` / `rc` flow is fully testable without signing or publishing anything.

**Architecture:**
- `versioning` (Python, in the engine package) owns the version in all five places, including the app's `MARKETING_VERSION`.
- It also learns to find the previous release and to recommend a bump level.
- A small `release_notes` module renders the combined release body from the two changelogs.
- `scripts/release.sh` is plain bash 3.2 (macOS's `/bin/bash`). It orchestrates git, `xcodebuild`, signing, notarization and `gh`.
- Its tests run the real script against a throwaway git repo. Every signing, notarizing and publishing tool is replaced by a logging stub on a `PATH` that contains no real copies of those tools.

**Tech Stack:** Python 3.11/3.12 (engine, pytest, ruff, mypy) · bash 3.2 · git · `gh` · Xcode `xcodebuild`, `codesign`, `xcrun notarytool`/`stapler`, `hdiutil`, `spctl`, `ditto`, `security` (all stubbed in tests)

**Spec:** [`docs/superpowers/specs/2026-10-05-unified-release-design.md`](../specs/2026-10-05-unified-release-design.md). This plan covers the spec's §6 item 1, the pipeline. Parts B and C, Sparkle (#74) and the retirement of `/scout-setup` are out of scope.

## Global Constraints

- **Never run the real release.** Agents never invoke `scripts/release.sh finalize|rc`, `apps/macos/scripts/release-app.sh`, `plugin/scripts/release-plugin.sh`, `xcrun notarytool`, `codesign` on a release build, `gh release create`, or `git push` of a tag. The exception is when Jordan explicitly asks for that specific run. Tests use only the stub harness from Task 3.
- `scripts/release.sh` must run under **macOS `/bin/bash` 3.2**: no `mapfile` or `readarray`, no associative arrays, no `${var^^}`. Run it with `set -euo pipefail`.
- **No `cmd | grep -q` under `pipefail`.** It is SIGPIPE-exposed, and it was the #225 bug. Use a herestring or a file argument instead (`grep -q x <<<"$out"`, `grep -q x file`).
- **One version `X.Y.Z`** lives in five places, all read and written by `versioning` (spec §3):
  - `plugin/.claude-plugin/plugin.json` (canonical);
  - the root `.claude-plugin/marketplace.json`;
  - `plugin/engine/pyproject.toml`;
  - `plugin/engine/scout/__init__.py`;
  - **every** `MARKETING_VERSION = …;` line in `apps/macos/Scout.xcodeproj/project.pbxproj`. There are 4 today: Debug and Release for each of two targets.
- **Tags:** releases are `vX.Y.Z`, and release candidates `vX.Y.Z-rc.N`. Never create `app/v*` or `plugin/v*` tags.
- **Publishing:** exactly one `gh release create <tag> … --target <sha>` call, made only after the DMG is built (and notarized, unless `SKIP_NOTARIZE=1`).
  - A release passes `--latest`. An rc passes `--prerelease --latest=false`.
  - The script never runs `git tag` or `git push` for the release tag. `gh release create --target` creates the tag on GitHub.
- **Dry runs:** `SKIP_RELEASE=1` makes `prepare` stop after the local commit (no push, no PR), and `finalize`/`rc` stop before `gh release create`. `SKIP_NOTARIZE=1` skips every `xcrun notarytool`, `xcrun stapler` and `spctl` call.
- **Sparkle hook (contract with #318, the in-app updates plan).** It's active when `apps/macos/scripts/sparkle-release.sh` is executable **in the commit being built** (`$wt/…`). `build_and_publish` exports `SPARKLE_BIN="$build/SourcePackages/artifacts/sparkle/Sparkle/bin"` and calls three subcommands:
  - `preflight "$app"`, right after `xcodebuild … build` and before any `codesign`;
  - `sign "$app" "$ident"`, **instead of** the flat app `codesign --force --options runtime …`. Sparkle's nested XPC services, `Autoupdate` and `Updater.app` must be signed inside-out, and a flat outer signature fails notarization. `codesign --verify --strict` on the app stays.
  - `appcast "$dmg" "$tag" "$slug" "$notes" "$build/appcast.xml"`, after the DMG is final and the notes are rendered.
  - With the hook active, a **release** without `$build/appcast.xml` is fatal. An rc or a dry run only warns. Without the hook, the flat app codesign is used and no appcast is required.
  - The DMG's own `codesign` stays flat in `release.sh`.
- Build number: `CURRENT_PROJECT_VERSION = git rev-list --count <sha>`. `SCOUT_PLUGIN_FLOOR` equals the release's own version `X.Y.Z`.
- The release's repo must be `Raven-Scout/Scout`, derived from `origin` and compared case-insensitively. `SCOUT_REPO_SLUG` overrides it, for tests only.
- Bundled-engine contract with Part C: `Scout.app/Contents/Resources/engine-release.json` has a top-level `"version"` string. A real `finalize` refuses when the file is missing or its version ≠ `X.Y.Z`. A dry run or an rc only warns when it is missing.
- Engine code style: ruff (line length 120), `ruff format`, mypy clean. Run them from `plugin/engine` with `.venv/bin/…`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Running from an old app clone.** `~/scout-app`'s `origin` is `Raven-Scout/scout-app-legacy`. `prepare`, `finalize` and `rc` must refuse before touching anything. Owned by Task 3, test `test_refuses_a_clone_of_another_repo`.
2. **Re-running `finalize` after a failure.** A `.release/vX.Y.Z` worktree is left over from the failed attempt. It must be replaced cleanly, and the second run must publish. Owned by Task 4, test `test_rerun_after_failure_replaces_the_stale_worktree`.
3. **Notarization rejected.** `notarytool` exits non-zero. Nothing is published and no `gh release create` happens. Owned by Task 4, test `test_rejected_notarization_publishes_nothing`.
4. **`finalize` before the release PR merged.** `origin/main` still carries the old version. Refuse with "merge the release PR first", before any build. Owned by Task 4, test `test_refuses_before_the_release_pr_merged`.
5. **An rc tag already exists** (`v0.15.1-rc.1`) when picking the previous release or the next level. Rc tags are ignored, and an rc is never published as Latest. Owned by Task 1, test `test_previous_release_ignores_rc_tags`, and Task 4, test `test_rc_is_a_prerelease_never_latest`.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `plugin/engine/scout/scripts/versioning.py` | Version in all five places; both changelogs; previous release; recommended level; `--repo-root` CLI | 1 |
| `apps/macos/Scout.xcodeproj/project.pbxproj` | `MARKETING_VERSION` 0.12.0 → 0.14.0 (×4) so `versioning check` passes | 1 |
| `plugin/engine/tests/unit/test_versioning.py`, `test_versioning_cli.py` | Fixture gains the project file; new cases | 1 |
| `plugin/engine/scout/scripts/release_notes.py` | Extract `[X.Y.Z]` / `[Unreleased]` sections; render the combined body | 2 |
| `plugin/engine/tests/unit/test_release_notes.py`, `plugin/engine/tests/fixtures/release_notes/*` | Golden-file tests | 2 |
| `scripts/release.sh` | `prepare`, `finalize`, `rc` | 3, 4 |
| `plugin/engine/tests/unit/release_harness.py` | Throwaway repo, bare origin, stub tools, safe `PATH` | 3 |
| `plugin/engine/tests/unit/test_release_script.py` | Behaviour tests of `release.sh` | 3, 4 |
| `.gitignore` | `.release/` (finalize's build worktrees) | 3 |
| `.github/workflows/{plugin-test,plugin-lint,contract}.yml` | Path filters, shellcheck, version-match step | 5 |
| `CLAUDE.md`, `apps/macos/CHANGELOG.md`, `apps/macos/README.md`, `apps/macos/Scout/Shell/PluginVersion.swift`, runbook | Docs for the new flow | 6 |
| `apps/macos/scripts/release-app.sh`, `plugin/scripts/release-plugin.sh`, `.github/workflows/release-plugin.yml` | Deleted in a **separate PR**, merged when v0.15.0 is cut | 7 |

---

### Task 1: `versioning` covers the app, both changelogs, and the previous release

**Files:**
- Modify: `plugin/engine/scout/scripts/versioning.py`
- Modify: `apps/macos/Scout.xcodeproj/project.pbxproj`: all four `MARKETING_VERSION = 0.12.0;` → `MARKETING_VERSION = 0.14.0;`
- Modify: `plugin/engine/tests/unit/test_versioning.py` (the fixture and new tests)
- Modify: `plugin/engine/tests/unit/test_versioning_cli.py` (its fixture, if it builds one, gains the project file)

**Interfaces:**
- Produces, Python:
  - `read_versions(root, repo_root=None) -> dict[str, str]`, now with the key `"MARKETING_VERSION"`;
  - `set_version(root, version, repo_root=None)`, which rewrites every project-file occurrence;
  - `promote_changelogs(repo_root: Path, *, version: str, date: str) -> None`;
  - `previous_release(repo_root: Path, ref: str = "HEAD", exclude: str | None = None) -> tuple[str, str] | None`, returning `(tag, sha)`;
  - `recommend_level(repo_root: Path, since: str | None, ref: str = "HEAD") -> str`, returning `"minor"` or `"patch"`.
- Produces, CLI: `python -m scout.scripts.versioning [--repo-root PATH] <cmd>`, where `<cmd>` is one of:
  - `check` prints `X.Y.Z`, or fails on drift;
  - `current`;
  - `next <patch|minor|major|X.Y.Z>` prints the new version and **writes nothing**;
  - `bump <level>` writes and prints, as today;
  - `set <X.Y.Z>`;
  - `promote <X.Y.Z> <YYYY-MM-DD>` promotes both changelogs;
  - `previous-release [--ref REF] [--exclude TAG]` prints `TAG SHA`, or nothing when there is none (exit 0);
  - `recommend [--since SHA] [--ref REF]` prints `minor` or `patch`.
  - With `--repo-root`, `root = repo_root / "plugin"`. Without it, both default as today (`PLUGIN_ROOT`, `PLUGIN_ROOT.parent`).
- Keeps `promote_changelog(root, *, version, date)` (plugin only) unchanged, because `release-plugin.sh` still calls it until Task 7.

- [ ] **Step 1: Extend the test fixture with the project file.** In `test_versioning.py`, make `_fake_plugin` also write the project file. The two targets × two configurations mirror the real file:

```python
    pbx = repo / "apps" / "macos" / "Scout.xcodeproj"
    pbx.mkdir(parents=True)
    (pbx / "project.pbxproj").write_text(
        "\n".join(
            f"\t\t\t\tMARKETING_VERSION = {version};" if i % 2 == 0 else "\t\t\t\tPRODUCT_NAME = Scout;"
            for i in range(8)
        )
        + "\n",
        encoding="utf-8",
    )
    for rel in ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md"):
        (repo / rel).write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n- a thing\n", encoding="utf-8")
```

If `test_versioning_cli.py` builds its own fixture, give it the same lines. If it imports `_fake_plugin`, nothing is needed.

- [ ] **Step 2: Write the failing tests.** Add `import subprocess` to the imports at the top of `test_versioning.py`, then append:

```python
def test_marketing_version_is_read_and_must_agree(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    assert versioning.read_versions(tmp_path / "plugin", tmp_path)["MARKETING_VERSION"] == "1.2.3"
    pbx = tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj"
    pbx.write_text(pbx.read_text().replace("1.2.3;", "9.9.9;", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="MARKETING_VERSION differs"):
        versioning.read_versions(tmp_path / "plugin", tmp_path)


def test_set_version_rewrites_every_marketing_version(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(tmp_path / "plugin", "1.3.0", tmp_path)
    text = (tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj").read_text()
    assert text.count("MARKETING_VERSION = 1.3.0;") == 4 and "1.2.3" not in text
    assert versioning.assert_in_sync(tmp_path / "plugin", tmp_path) == "1.3.0"


def test_drift_between_app_and_plugin_fails_check(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    pbx = tmp_path / "apps/macos/Scout.xcodeproj/project.pbxproj"
    pbx.write_text(pbx.read_text().replace("1.2.3", "1.2.4"), encoding="utf-8")
    with pytest.raises(ValueError, match="version drift"):
        versioning.assert_in_sync(tmp_path / "plugin", tmp_path)


def test_promote_changelogs_promotes_both(tmp_path):
    _fake_plugin(tmp_path, "1.2.3")
    versioning.promote_changelogs(tmp_path, version="1.3.0", date="2026-10-05")
    for rel in ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md"):
        text = (tmp_path / rel).read_text()
        assert text.index("## [Unreleased]") < text.index("## [1.3.0] - 2026-10-05") < text.index("- a thing")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo_with_tags(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for msg, tag in [
        ("chore: one", "app/v0.14.0"),
        ("fix: two", "plugin/v0.14.0"),
        ("chore: three", "v0.15.1-rc.1"),
        ("feat: four", None),
    ]:
        _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
        if tag:
            _git(repo, "tag", tag)
    return repo


def test_previous_release_picks_highest_then_newest_and_ignores_rc(tmp_path):
    repo = _repo_with_tags(tmp_path)
    tag, sha = versioning.previous_release(repo)
    assert tag == "plugin/v0.14.0"  # same version as app/v0.14.0, but the later commit
    assert sha == _git(repo, "rev-list", "-n", "1", "plugin/v0.14.0")


def test_previous_release_ignores_rc_tags(tmp_path):
    repo = _repo_with_tags(tmp_path)
    assert versioning.previous_release(repo)[0] != "v0.15.1-rc.1"


def test_previous_release_excludes_and_respects_ref(tmp_path):
    repo = _repo_with_tags(tmp_path)
    assert versioning.previous_release(repo, exclude="plugin/v0.14.0")[0] == "app/v0.14.0"
    assert versioning.previous_release(repo, ref="app/v0.14.0", exclude="app/v0.14.0") is None


def test_recommend_level(tmp_path):
    repo = _repo_with_tags(tmp_path)
    since = _git(repo, "rev-list", "-n", "1", "plugin/v0.14.0")
    assert versioning.recommend_level(repo, since) == "minor"  # "feat: four" landed after it
    assert versioning.recommend_level(repo, since, ref="v0.15.1-rc.1") == "patch"
```

- [ ] **Step 3: Run them and watch them fail.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_versioning.py -q`
Expected: FAIL. `KeyError: 'MARKETING_VERSION'` and `AttributeError: … has no attribute 'promote_changelogs'` / `'previous_release'`.

- [ ] **Step 4: Implement.** In `versioning.py`:

```python
import subprocess

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
    ("__init__.py", _PLUGIN, "engine/scout/__init__.py", re.compile(r'(?m)^(__version__\s*=\s*")(?P<v>[^"]+)(")'), False),
    (
        "MARKETING_VERSION",
        _REPO,
        "apps/macos/Scout.xcodeproj/project.pbxproj",
        re.compile(r"(?m)^(\s*MARKETING_VERSION = )(?P<v>[^;\s]+)(;)"),
        True,
    ),
]

_CHANGELOGS = ("plugin/CHANGELOG.md", "apps/macos/CHANGELOG.md")
_RELEASE_TAG = re.compile(r"^(?:app/|plugin/)?v(\d+)\.(\d+)\.(\d+)$")
_FEAT = re.compile(r"^feat(\([^)]*\))?!?:")


def read_versions(root: Path = PLUGIN_ROOT, repo_root: Path | None = None) -> dict[str, str]:
    repo = root.parent if repo_root is None else repo_root
    out: dict[str, str] = {}
    for label, kind, rel, rx, every in _TARGETS:
        path = _target_path(kind, rel, root, repo)
        text = path.read_text(encoding="utf-8")
        found = {m.group("v") for m in rx.finditer(text)} if every else ({m.group("v")} if (m := rx.search(text)) else set())
        if not found:
            raise ValueError(f"no version field found in {path}")
        if len(found) > 1:
            raise ValueError(f"{path}: {label} differs across build configurations: {sorted(found)}")
        out[label] = found.pop()
    return out


def set_version(root: Path = PLUGIN_ROOT, version: str | None = None, repo_root: Path | None = None) -> None:
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
    text = path.read_text(encoding="utf-8")
    marker = "## [Unreleased]"
    if marker not in text:
        raise ValueError(f"{path} has no '## [Unreleased]' section")
    path.write_text(text.replace(marker, f"{marker}\n\n## [{version}] - {date}\n", 1), encoding="utf-8")


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
```

Replace `main` so that it parses an optional leading `--repo-root PATH` and the new commands:

```python
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
        new = rest[0] if cmd == "set" else bump(read_versions(root, repo)["plugin.json"], rest[0])
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
```

Update the module docstring's first paragraph, which says "Three derived files". It should name the five places: four files, with the project file's `MARKETING_VERSION` as the fifth, and say the project file carries one copy per build configuration.

- [ ] **Step 5: Set the real project file to the current version.** Replace all four `MARKETING_VERSION = 0.12.0;` lines in `apps/macos/Scout.xcodeproj/project.pbxproj` with `MARKETING_VERSION = 0.14.0;`. Then check:

Run: `cd plugin/engine && .venv/bin/python -m scout.scripts.versioning check`
Expected: `0.14.0`

- [ ] **Step 6: Run the tests and watch them pass, along with the existing version tests.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_versioning.py tests/unit/test_versioning_cli.py tests/unit/test_version_sync.py tests/unit/test_self_update.py -q && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout`
Expected: all pass. If `test_versioning_cli.py` fails with `no version field found in …project.pbxproj`, its fixture needs the Step 1 lines.

- [ ] **Step 7: Commit.**

```bash
git add plugin/engine/scout/scripts/versioning.py plugin/engine/tests/unit/test_versioning.py plugin/engine/tests/unit/test_versioning_cli.py apps/macos/Scout.xcodeproj/project.pbxproj
git commit -m "feat(versioning): one version across app and plugin; promote both changelogs; find the previous release

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Combined release notes

**Files:**
- Create: `plugin/engine/scout/scripts/release_notes.py`
- Create: `plugin/engine/tests/unit/test_release_notes.py`
- Create: `plugin/engine/tests/fixtures/release_notes/plugin-CHANGELOG.md`, `app-CHANGELOG.md`, `expected-release.md`, `expected-no-app-changes.md`, `expected-rc.md`

**Interfaces:**
- Produces, Python:
  - `extract_section(text: str, version: str) -> str` returns the body under `## [<version>]`, up to the next `## [`, stripped. `version="Unreleased"` gives the Unreleased body.
  - `render(version: str, *, app: str, plugin: str, prev_tag: str | None, repo_slug: str, rc: str | None = None) -> str`
- Produces, CLI: `python -m scout.scripts.release_notes --repo-root PATH <X.Y.Z> --repo SLUG --out FILE [--prev TAG] [--rc vX.Y.Z-rc.N]`.
  - It reads `plugin/CHANGELOG.md` and `apps/macos/CHANGELOG.md` under PATH.
  - `--rc` takes the `[Unreleased]` sections.
  - It exits 1, with `no [X.Y.Z] section in either changelog`, when both sections are empty.

- [ ] **Step 1: Write the fixtures.**

`plugin/engine/tests/fixtures/release_notes/plugin-CHANGELOG.md`:

```markdown
# Changelog

## [Unreleased]

### Fixed
- **Next fix** waiting for the next release.

## [0.15.0] - 2026-11-01

### Added
- **Day planning** (`commands/scout-plan.md`).

### Fixed
- **Launcher probe** checks the import (#303).

## [0.14.0] - 2026-10-05

### Added
- Older entry.
```

`app-CHANGELOG.md`:

```markdown
# Changelog — Scout for macOS

## [Unreleased]

## [0.15.0] - 2026-11-01

### Added
- **Onboarding installs the engine.**

## [0.14.0] - 2026-10-05
```

`expected-release.md`:

```markdown
## App

### Added
- **Onboarding installs the engine.**

## Plugin and engine

### Added
- **Day planning** (`commands/scout-plan.md`).

### Fixed
- **Launcher probe** checks the import (#303).

**Full changelog**: https://github.com/Raven-Scout/Scout/compare/plugin/v0.14.0...v0.15.0

---

## Install

1. Download `Scout-0.15.0.dmg` below.
2. Open it and drag **Scout.app** into **Applications**.
3. Open Scout. Onboarding sets up everything else: the engine, Claude Code's plugin, and your vault.

Terminal only? `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`
```

`expected-no-app-changes.md`: identical to `expected-release.md`, except the `## App` section body is `_No app changes._`, for version `0.14.0` with no `--prev`. Here is the full file:

```markdown
## App

_No app changes._

## Plugin and engine

### Added
- Older entry.

---

## Install

1. Download `Scout-0.14.0.dmg` below.
2. Open it and drag **Scout.app** into **Applications**.
3. Open Scout. Onboarding sets up everything else: the engine, Claude Code's plugin, and your vault.

Terminal only? `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`
```

`expected-rc.md`, for rc `v0.15.1-rc.1` from the `[Unreleased]` sections:

```markdown
> **Release candidate v0.15.1-rc.1.** For testing; not for general use.

## App

_No app changes._

## Plugin and engine

### Fixed
- **Next fix** waiting for the next release.

---

## Install

1. Download `Scout-0.15.1-rc.1.dmg` below.
2. Open it and drag **Scout.app** into **Applications**.
3. Open Scout. Onboarding sets up everything else: the engine, Claude Code's plugin, and your vault.

Terminal only? `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`
```

- [ ] **Step 2: Write the failing tests.** `test_release_notes.py`:

```python
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


def test_cli_rc_uses_unreleased(tmp_path):
    repo, out = _repo(tmp_path), tmp_path / "notes.md"
    cmd = [sys.executable, "-m", "scout.scripts.release_notes", "--repo-root", str(repo), "0.15.1"]
    cmd += ["--repo", "Raven-Scout/Scout", "--rc", "v0.15.1-rc.1", "--out", str(out)]
    subprocess.run(cmd, check=True)
    assert out.read_text(encoding="utf-8") == _read("expected-rc.md")


def test_cli_refuses_when_both_sections_are_empty(tmp_path):
    repo = _repo(tmp_path)
    cmd = [sys.executable, "-m", "scout.scripts.release_notes", "--repo-root", str(repo), "9.9.9"]
    cmd += ["--repo", "Raven-Scout/Scout", "--out", str(tmp_path / "n.md")]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 1 and "no [9.9.9] section in either changelog" in done.stderr
```

- [ ] **Step 3: Run them and watch them fail.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_notes.py -q`
Expected: FAIL with `ImportError: cannot import name 'release_notes'`.

- [ ] **Step 4: Implement `release_notes.py`.**

```python
"""Render the GitHub release body for a Scout release: App + Plugin and engine sections
taken from the two changelogs, a compare link, and install steps
(spec: docs/superpowers/specs/2026-10-05-unified-release-design.md §3)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_INSTALL = """## Install

1. Download `Scout-{dmg_version}.dmg` below.
2. Open it and drag **Scout.app** into **Applications**.
3. Open Scout. Onboarding sets up everything else: the engine, Claude Code's plugin, and your vault.

Terminal only? `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`"""


def extract_section(text: str, version: str) -> str:
    head = f"## [{version}]"
    out: list[str] = []
    grab = False
    for line in text.splitlines():
        if line.startswith(head):
            grab = True
            continue
        if grab and line.startswith("## ["):
            break
        if grab:
            out.append(line)
    return "\n".join(out).strip()


def render(
    version: str, *, app: str, plugin: str, prev_tag: str | None, repo_slug: str, rc: str | None = None
) -> str:
    parts: list[str] = []
    if rc:
        parts += [f"> **Release candidate {rc}.** For testing; not for general use.", ""]
    parts += ["## App", "", app or "_No app changes._", "", "## Plugin and engine", "", plugin or "_No plugin changes._", ""]
    if prev_tag:
        parts += [f"**Full changelog**: https://github.com/{repo_slug}/compare/{prev_tag}...v{version}", ""]
    parts += ["---", "", _INSTALL.format(dmg_version=rc.removeprefix("v") if rc else version)]
    return "\n".join(parts).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a Scout GitHub release body from both changelogs.")
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("version")
    ap.add_argument("--repo", required=True, help="owner/repo for the compare link")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prev", default=None, help="previous release tag, for the compare link")
    ap.add_argument("--rc", default=None, help="vX.Y.Z-rc.N: render the [Unreleased] sections as a candidate")
    args = ap.parse_args(argv)
    section = "Unreleased" if args.rc else args.version
    plugin = extract_section((args.repo_root / "plugin/CHANGELOG.md").read_text(encoding="utf-8"), section)
    app = extract_section((args.repo_root / "apps/macos/CHANGELOG.md").read_text(encoding="utf-8"), section)
    if not args.rc and not plugin and not app:
        print(f"no [{args.version}] section in either changelog", file=sys.stderr)
        return 1
    body = render(args.version, app=app, plugin=plugin, prev_tag=args.prev, repo_slug=args.repo, rc=args.rc)
    args.out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The rc fixture has no compare link, because the CLI test passes no `--prev`. Keep it that way.

- [ ] **Step 5: Run them and watch them pass, then lint.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_notes.py -q && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout`
Expected: PASS. If a golden comparison fails only on trailing whitespace or newlines, fix the fixture file, not `render`. `render` always ends with exactly one `\n`.

- [ ] **Step 6: Commit.**

```bash
git add plugin/engine/scout/scripts/release_notes.py plugin/engine/tests/unit/test_release_notes.py plugin/engine/tests/fixtures/release_notes
git commit -m "feat(release): combined release notes from the app and plugin changelogs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/release.sh prepare`, and the safe test harness

**Files:**
- Create: `scripts/release.sh`. Its `finalize` and `rc` dispatch to stubs that exit 2 with "not implemented"; Task 4 replaces them.
- Create: `plugin/engine/tests/unit/release_harness.py`
- Create: `plugin/engine/tests/unit/test_release_script.py`
- Modify: `.gitignore` (add `.release/`)

**Interfaces:**
- Consumes (Task 1): `versioning --repo-root R check | next <level> | set <X.Y.Z> | promote <X.Y.Z> <date> | previous-release | recommend [--since SHA]`.
- Produces:
  - `scripts/release.sh prepare [patch|minor|major|X.Y.Z]`;
  - helper functions inside the script, used by Task 4: `die MSG`, `vers ARGS…` (runs versioning against `$REPO_ROOT`), `repo_slug`, `require_slug` (prints the slug), `require_clean_synced_main`.
  - `release_harness.make_repo(tmp_path, *, origin_url=None) -> Repo`, which returns a dataclass with these fields:
    - `root: Path` — the clone;
    - `origin: Path` — a bare repo;
    - `log: Path` — the stub log;
    - `env: dict[str, str]`;
    - `run(*args, extra_env=None) -> subprocess.CompletedProcess[str]`;
    - `calls() -> list[str]` — the logged stub lines, in order.

- [ ] **Step 1: Write the harness.** `plugin/engine/tests/unit/release_harness.py`:

```python
"""A throwaway Scout-shaped repo plus stub signing/publishing tools for testing scripts/release.sh.

SAFETY: the script runs with PATH = <stubs>:<realbin>, where realbin holds symlinks to a short allowlist
of harmless tools. No real codesign, xcrun, xcodebuild, security, spctl, hdiutil, ditto or gh is
reachable, so a missing stub fails loudly instead of signing, notarizing or publishing anything.
"""

from __future__ import annotations

import os
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
        dd=""; mv_=""; prev=""
        for a in "$@"; do
          [ "$prev" = "-derivedDataPath" ] && dd="$a"
          case "$a" in MARKETING_VERSION=*) mv_="${a#MARKETING_VERSION=}";; esac
          prev="$a"
        done
        app="$dd/Build/Products/Release/Scout.app"
        mkdir -p "$app/Contents/Resources"
        if [ -z "${FAKE_NO_ENGINE:-}" ]; then
          printf '{"version": "%s"}\\n' "${FAKE_ENGINE_VERSION:-$mv_}" > "$app/Contents/Resources/engine-release.json"
        fi
        [ -n "${FAKE_APPCAST:-}" ] && echo '<rss/>' > "$dd/appcast.xml"
        exit 0 ;;
      xcrun) [ "$1" = notarytool ] && exit "${FAKE_NOTARY_EXIT:-0}"; exit 0 ;;
      hdiutil) for last in "$@"; do :; done; mkdir -p "$(dirname "$last")"; : > "$last" ;;
      gh)
        case "$1 $2" in
          "pr create") echo "https://github.com/Raven-Scout/Scout/pull/999" ;;
          "release create") [ -n "${FAKE_GH_EXIT:-}" ] && exit "$FAKE_GH_EXIT"; echo "$3" > "$FAKE_LOG.latest" ;;
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
            cwd=self.root, env=env, capture_output=True, text=True,
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
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    if not origin_url:
        env["SCOUT_REPO_SLUG"] = "Raven-Scout/Scout"
    (tmp_path / "home").mkdir()
    return Repo(root=root, origin=origin, log=log, env=env)
```

- [ ] **Step 2: Write the failing `prepare` tests.** `test_release_script.py`:

```python
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
```

The `rc` and `finalize` arguments in `test_refuses_a_clone_of_another_repo` must refuse on the slug before Task 4's logic runs. So the Task 3 stubs for `finalize`/`rc` must call `require_slug` first (Step 3 shows how).

- [ ] **Step 3: Run them and watch them fail.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_script.py -q`
Expected: FAIL. `scripts/release.sh` doesn't exist yet (`FileNotFoundError` from `shutil.copy2`).

- [ ] **Step 4: Write `scripts/release.sh` with `prepare`.**

```bash
#!/usr/bin/env bash
# Release Scout: one version, one tag, one GitHub release.
# Spec: docs/superpowers/specs/2026-10-05-unified-release-design.md
#
#   scripts/release.sh prepare [patch|minor|major|X.Y.Z]  # bump everything on release/vX.Y.Z, open the release PR
#   scripts/release.sh finalize vX.Y.Z                    # after the PR merges: build, sign, notarize, publish
#   scripts/release.sh rc vX.Y.Z-rc.N                     # release candidate from HEAD: a pre-release, never Latest
#
# LIVE: finalize and rc sign with the Developer ID, submit to Apple and publish to GitHub.
# Agents never run them for real unless Jordan asks for that specific run.
#
# Environment:
#   SKIP_RELEASE=1     prepare: commit locally, no push, no PR. finalize/rc: stop before publishing.
#   SKIP_NOTARIZE=1    finalize/rc: sign and package, but skip Apple's notary service.
#   SCOUT_SIGN_IDENTITY (default "Developer ID Application"), SCOUT_NOTARY_PROFILE (default "scout-notary")
#   SCOUT_PY           Python with the engine importable (default plugin/engine/.venv/bin/python)
#   SCOUT_REPO_SLUG    tests only: skip deriving owner/repo from origin
#   SCOUT_RELEASE_SKIP_LINT=1  tests only: skip ruff/mypy/shellcheck in prepare
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${SCOUT_PY:-$REPO_ROOT/plugin/engine/.venv/bin/python}"
EXPECTED_SLUG="Raven-Scout/Scout"

die() { echo "error: $*" >&2; exit 1; }
vers() { "$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" "$@"; }

repo_slug() {
  if [ -n "${SCOUT_REPO_SLUG:-}" ]; then echo "$SCOUT_REPO_SLUG"; return 0; fi
  local url
  url="$(git -C "$REPO_ROOT" config --get remote.origin.url || true)"
  case "$url" in
    https://github.com/*) url="${url#https://github.com/}" ;;
    git@github.com:*) url="${url#git@github.com:}" ;;
    *) echo "${url:-<no origin>}"; return 0 ;;
  esac
  echo "${url%.git}"
}

# Prints the slug; dies unless it is Raven-Scout/Scout (case-insensitive) or SCOUT_REPO_SLUG is set.
require_slug() {
  local slug lower expected
  slug="$(repo_slug)"
  lower="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]')"
  expected="$(printf '%s' "$EXPECTED_SLUG" | tr '[:upper:]' '[:lower:]')"
  if [ -z "${SCOUT_REPO_SLUG:-}" ] && [ "$lower" != "$expected" ]; then
    die "origin is $slug, not $EXPECTED_SLUG. Run this from a clone of $EXPECTED_SLUG."
  fi
  echo "$slug"
}

require_clean_synced_main() {
  [ "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)" = main ] || die "run from main"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree not clean"
  git -C "$REPO_ROOT" fetch -q origin
  [ "$(git -C "$REPO_ROOT" rev-parse HEAD)" = "$(git -C "$REPO_ROOT" rev-parse origin/main)" ] \
    || die "local main is not in sync with origin/main"
}

cmd_prepare() {
  local level="${1:-}" slug current prev since new branch today
  slug="$(require_slug)"
  require_clean_synced_main
  current="$(vers check)" || die "versions drift across the manifests; run: versioning check"
  if [ -z "$level" ]; then
    prev="$(vers previous-release)"
    since=""
    [ -z "$prev" ] || since="${prev#* }"
    level="$(vers recommend ${since:+--since "$since"})"
    echo "→ $level (feat→minor, else→patch) since ${prev%% *}"
  fi
  new="$(vers next "$level")"
  branch="release/v$new"
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/v$new" >/dev/null || die "tag v$new already exists"
  git -C "$REPO_ROOT" checkout -q -b "$branch"
  vers set "$new" >/dev/null
  today="$(date '+%Y-%m-%d')"
  vers promote "$new" "$today"
  [ "$(vers check)" = "$new" ] || die "versioning check does not report $new after set"
  if [ "${SCOUT_RELEASE_SKIP_LINT:-0}" != 1 ]; then
    ( cd "$REPO_ROOT/plugin/engine" && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
        && .venv/bin/mypy scout )
    shellcheck -S error "$REPO_ROOT/scripts/release.sh"
  fi
  git -C "$REPO_ROOT" add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json \
    plugin/engine/pyproject.toml plugin/engine/scout/__init__.py \
    apps/macos/Scout.xcodeproj/project.pbxproj plugin/CHANGELOG.md apps/macos/CHANGELOG.md
  git -C "$REPO_ROOT" commit -q -m "release: v$new"
  if [ "${SKIP_RELEASE:-0}" = 1 ]; then
    echo "→ SKIP_RELEASE=1: committed 'release: v$new' on $branch locally. Not pushing, no PR."
    return 0
  fi
  git -C "$REPO_ROOT" push -q -u origin "$branch"
  gh pr create --repo "$slug" --base main --head "$branch" --title "release: v$new" \
    --body "Release prep for Scout v$new. Once the four required checks pass, squash-merge it, then run \`scripts/release.sh finalize v$new\`."
  echo "→ Release PR opened. After it merges: scripts/release.sh finalize v$new"
}

cmd_finalize() { require_slug >/dev/null; die "finalize: not implemented yet"; }
cmd_rc() { require_slug >/dev/null; die "rc: not implemented yet"; }

[ -x "$PY" ] || die "no Python at $PY. Build the engine venv first: bash plugin/scripts/install-venv.sh"

case "${1:-}" in
  prepare) shift; cmd_prepare "$@" ;;
  finalize) shift; cmd_finalize "$@" ;;
  rc) shift; cmd_rc "$@" ;;
  *) echo "usage: scripts/release.sh {prepare [patch|minor|major|X.Y.Z] | finalize vX.Y.Z | rc vX.Y.Z-rc.N}" >&2; exit 2 ;;
esac
```

`chmod +x scripts/release.sh`, and add the line `.release/` to the root `.gitignore`.

- [ ] **Step 5: Run the tests and watch them pass. Shellcheck the script.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_script.py -q && shellcheck -S warning ../../scripts/release.sh`
Expected: 7 passed. Shellcheck is clean, or shows only SC2086 on `${since:+--since "$since"}`, which is intentional word-splitting of an optional flag pair. If it appears, add `# shellcheck disable=SC2086` on the line above.

- [ ] **Step 6: Commit.**

```bash
git add scripts/release.sh .gitignore plugin/engine/tests/unit/release_harness.py plugin/engine/tests/unit/test_release_script.py
git commit -m "feat(release): scripts/release.sh prepare, with a stub-only test harness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `finalize` and `rc`: build, sign, notarize, then publish in one call

**Files:**
- Modify: `scripts/release.sh`. Replace the `cmd_finalize` and `cmd_rc` stubs, and add `build_and_publish` and `check_bundled_engine`.
- Modify: `plugin/engine/tests/unit/test_release_script.py` (append tests)

**Interfaces:**
- Consumes:
  - from Task 1, `versioning --repo-root <worktree> check` and `versioning --repo-root R previous-release --ref SHA --exclude TAG`;
  - from Task 2, `release_notes --repo-root <worktree> X.Y.Z --repo SLUG --out FILE [--prev TAG] [--rc TAG]`;
  - from Task 3, `die`, `require_slug`, `make_repo`.
- Produces:
  - `scripts/release.sh finalize vX.Y.Z` and `scripts/release.sh rc vX.Y.Z-rc.N`;
  - build worktrees at `$REPO_ROOT/.release/<tag>`, removed after a successful publish;
  - the DMG at `.release/<tag>/apps/macos/build/release/Scout-<X.Y.Z or X.Y.Z-rc.N>.dmg`;
  - the Sparkle hook, per Global Constraints: `sparkle-release.sh preflight|sign|appcast` from the commit being built. `$build/appcast.xml`, if it exists when publishing, is attached as a second asset. It's mandatory for a release when the hook is active.

- [ ] **Step 1: Write the failing tests.** Append to `test_release_script.py`:

```python
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
    assert f"--target {sha}" in line and "--latest" in line and "Scout-0.15.0.dmg" in line and "appcast.xml" not in line
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
    assert not any(c.startswith("gh release create") for c in r.calls())


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
    sha = _git(r.root, "rev-parse", "HEAD")
    done = r.run("rc", "v0.15.1-rc.1")
    assert done.returncode == 0, done.stderr
    line = next(c for c in r.calls() if c.startswith("gh release create"))
    assert "--prerelease" in line and "--latest=false" in line and f"--target {sha}" in line
    assert "Scout-0.15.1-rc.1.dmg" in line and " --latest " not in f"{line} "


def test_rc_and_finalize_validate_tag_shapes(tmp_path):
    r = make_repo(tmp_path)
    assert "finalize needs vX.Y.Z" in r.run("finalize", "0.15.0").stderr
    assert "use 'rc'" in r.run("finalize", "v0.15.1-rc.1").stderr
    assert "rc needs vX.Y.Z-rc.N" in r.run("rc", "v0.15.1").stderr


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
    idx = {k: next(i for i, c in enumerate(calls) if c.startswith(k)) for k in (
        "xcodebuild", "sparkle-release.sh preflight", "sparkle-release.sh sign", "sparkle-release.sh appcast",
        "gh release create")}
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
    done = r.run("rc", "v0.15.0-rc.1", extra_env={"FAKE_HOOK_NO_APPCAST": "1"})
    assert done.returncode == 0, done.stderr
    assert "no appcast.xml" in done.stdout
    assert "appcast.xml" not in next(c for c in r.calls() if c.startswith("gh release create"))
```

- [ ] **Step 2: Run them and watch them fail.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_script.py -q`
Expected: the Task 3 tests pass. The new tests FAIL with `finalize: not implemented yet` / `rc: not implemented yet`.

- [ ] **Step 3: Implement.** Replace the two stub functions in `scripts/release.sh` with:

```bash
check_bundled_engine() {
  local app="$1" v="$2" kind="$3" pin got
  pin="$app/Contents/Resources/engine-release.json"
  if [ ! -f "$pin" ]; then
    if [ "${SKIP_RELEASE:-0}" = 1 ] || [ "$kind" = rc ]; then
      echo "⚠ no bundled engine in Scout.app (Contents/Resources/engine-release.json): fine for a dry run or an rc"
      return 0
    fi
    die "Scout.app has no bundled engine (Contents/Resources/engine-release.json). A release needs Part C's bundling."
  fi
  got="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$pin")"
  [ "$got" = "$v" ] || die "bundled engine is $got, the app is $v"
}

# build_and_publish TAG APP_VERSION SHA SLUG KIND   (KIND: release | rc)
build_and_publish() {
  local tag="$1" v="$2" sha="$3" slug="$4" kind="$5"
  local wt="$REPO_ROOT/.release/$tag" ident profile idents build app count dmg_name dmg stage notes prev latest at hook
  ident="${SCOUT_SIGN_IDENTITY:-Developer ID Application}"
  profile="${SCOUT_NOTARY_PROFILE:-scout-notary}"

  if [ -e "$wt" ]; then
    git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  fi
  git -C "$REPO_ROOT" worktree prune
  mkdir -p "$REPO_ROOT/.release"
  git -C "$REPO_ROOT" worktree add -q --detach "$wt" "$sha"

  at="$("$PY" -m scout.scripts.versioning --repo-root "$wt" check)" || die "versions drift across the manifests at $sha"
  if [ "$kind" = release ]; then
    [ "$at" = "$v" ] || die "$sha carries version $at, not $v. Merge the release PR first."
    grep -q "^## \[$v\]" "$wt/plugin/CHANGELOG.md" || die "plugin/CHANGELOG.md at $sha has no [$v] section"
    grep -q "^## \[$v\]" "$wt/apps/macos/CHANGELOG.md" || die "apps/macos/CHANGELOG.md at $sha has no [$v] section"
  fi

  idents="$(security find-identity -v -p codesigning)"
  case "$idents" in *"$ident"*) ;; *) die "no codesigning identity matching \"$ident\" in the keychain" ;; esac

  build="$wt/apps/macos/build"
  count="$(git -C "$REPO_ROOT" rev-list --count "$sha")"
  echo "→ Building Scout $v (build $count) from $sha"
  xcodebuild -project "$wt/apps/macos/Scout.xcodeproj" -scheme Scout -configuration Release \
    -destination 'generic/platform=macOS' -derivedDataPath "$build" \
    MARKETING_VERSION="$v" CURRENT_PROJECT_VERSION="$count" SCOUT_PLUGIN_FLOOR="$v" \
    CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=NO ARCHS="arm64 x86_64" \
    clean build >/dev/null
  app="$build/Build/Products/Release/Scout.app"
  [ -d "$app" ] || die "Scout.app not found at $app"
  check_bundled_engine "$app" "$v" "$kind"

  # Sparkle hook (contract with #318): active when the commit being built ships it.
  hook="$wt/apps/macos/scripts/sparkle-release.sh"
  export SPARKLE_BIN="$build/SourcePackages/artifacts/sparkle/Sparkle/bin"
  if [ -x "$hook" ]; then
    "$hook" preflight "$app"
    "$hook" sign "$app" "$ident"     # inside-out: Sparkle's XPC services, Autoupdate and Updater.app first
  else
    codesign --force --options runtime --timestamp --sign "$ident" "$app"
  fi
  codesign --verify --strict --verbose=2 "$app"
  if [ "${SKIP_NOTARIZE:-0}" != 1 ]; then
    ditto -c -k --keepParent "$app" "$build/Scout-notarize.zip"
    xcrun notarytool submit "$build/Scout-notarize.zip" --keychain-profile "$profile" --wait
    xcrun stapler staple "$app"
    spctl --assess --type execute --verbose=2 "$app"
  fi

  dmg_name="Scout-${tag#v}.dmg"
  dmg="$build/release/$dmg_name"
  stage="$build/dmg-stage"
  rm -rf "$stage"
  mkdir -p "$stage" "$build/release"
  cp -R "$app" "$stage/"
  ln -s /Applications "$stage/Applications"
  hdiutil create -volname "Scout ${tag#v}" -srcfolder "$stage" -ov -format UDZO "$dmg" >/dev/null
  codesign --force --timestamp --sign "$ident" "$dmg"
  if [ "${SKIP_NOTARIZE:-0}" != 1 ]; then
    xcrun notarytool submit "$dmg" --keychain-profile "$profile" --wait
    xcrun stapler staple "$dmg"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
  fi

  notes="$build/release-notes.md"
  prev="$("$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" previous-release --ref "$sha" --exclude "$tag")"
  if [ "$kind" = release ]; then
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" \
      ${prev:+--prev "${prev%% *}"}
  else
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" --rc "$tag"
  fi

  if [ -x "$hook" ]; then
    "$hook" appcast "$dmg" "$tag" "$slug" "$notes" "$build/appcast.xml"
    if [ ! -f "$build/appcast.xml" ]; then
      if [ "$kind" = release ] && [ "${SKIP_RELEASE:-0}" != 1 ]; then
        die "no appcast.xml after sparkle-release.sh appcast: a Latest release without it 404s every installed app's update feed"
      fi
      echo "⚠ no appcast.xml: fine for an rc or a dry run"
    fi
  fi

  set -- "$dmg"
  [ ! -f "$build/appcast.xml" ] || set -- "$@" "$build/appcast.xml"

  if [ "${SKIP_RELEASE:-0}" = 1 ]; then
    echo "→ SKIP_RELEASE=1: built $dmg (notes in $notes). Not publishing."
    return 0
  fi
  if [ "$kind" = release ]; then
    gh release create "$tag" "$@" --repo "$slug" --target "$sha" --title "Scout $v" --notes-file "$notes" --latest
    latest="$(gh api "repos/$slug/releases/latest" --jq .tag_name)"
    [ "$latest" = "$tag" ] || die "published $tag, but /releases/latest is $latest"
  else
    gh release create "$tag" "$@" --repo "$slug" --target "$sha" --title "Scout ${tag#v}" --notes-file "$notes" \
      --prerelease --latest=false
  fi
  git -C "$REPO_ROOT" worktree remove --force "$wt"
  echo "✓ Published $tag"
}

cmd_finalize() {
  local tag="${1:-}" v slug sha
  slug="$(require_slug)"
  case "$tag" in v[0-9]*.[0-9]*.[0-9]*) ;; *) die "finalize needs vX.Y.Z, got '$tag'" ;; esac
  case "$tag" in *-*) die "finalize is for releases; use 'rc' for $tag" ;; esac
  v="${tag#v}"
  git -C "$REPO_ROOT" fetch -q --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  sha="$(git -C "$REPO_ROOT" rev-parse origin/main)"
  build_and_publish "$tag" "$v" "$sha" "$slug" release
}

cmd_rc() {
  local tag="${1:-}" v slug sha
  slug="$(require_slug)"
  case "$tag" in v[0-9]*.[0-9]*.[0-9]*-rc.[0-9]*) ;; *) die "rc needs vX.Y.Z-rc.N, got '$tag'" ;; esac
  v="${tag#v}"
  v="${v%%-rc.*}"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree not clean"
  git -C "$REPO_ROOT" fetch -q --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  [ -n "$(git -C "$REPO_ROOT" branch -r --contains "$sha")" ] || die "HEAD $sha is not on any pushed branch; push it first"
  build_and_publish "$tag" "$v" "$sha" "$slug" rc
}
```

There are two reasons the slug check comes first in both commands. First, `test_refuses_a_clone_of_another_repo` must see "not Raven-Scout/Scout" with no stub calls. Second, `release.sh` reuses the positional parameters for the asset list (`set -- …`), so `build_and_publish` must not need `$@` after that line, and it doesn't.

`ln -s` and `cp -R` come from the harness's `realbin`. Leave them as they are, since the real script needs them too.

- [ ] **Step 4: Run the tests and watch them pass. Shellcheck again.**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_release_script.py -q && shellcheck -S warning ../../scripts/release.sh`
Expected: all pass (7 from Task 3 + 13 new). If `test_rc_is_a_prerelease_never_latest` fails on "not on any pushed branch", the harness forgot to push `main`. `make_repo` pushes it, so check that the test didn't add commits after `make_repo`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/release.sh plugin/engine/tests/unit/test_release_script.py
git commit -m "feat(release): finalize and rc — build from the merge commit, notarize, then one gh release create --target

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: CI sees the new script and checks that app and plugin versions match

**Files:**
- Modify: `.github/workflows/plugin-test.yml`. In the `changes` job's `changed-paths.sh` regex, add `scripts/` and `apps/macos/Scout\.xcodeproj/project\.pbxproj$`.
- Modify: `.github/workflows/plugin-lint.yml`. Add the same two paths to its regex, and add `scripts/*.sh` to the `shellcheck -S error …` line.
- Modify: `.github/workflows/contract.yml`. Add `\.claude-plugin/|plugin/\.claude-plugin/|plugin/engine/pyproject\.toml$|apps/macos/Scout\.xcodeproj/project\.pbxproj$` to its regex, and add a verify step.

**Interfaces:**
- Consumes (Task 1): `versioning check` covers `MARKETING_VERSION`.

- [ ] **Step 1: Edit the three filters.** Current values:
  - plugin-test: `'^(plugin/|\.claude-plugin/|\.github/workflows/plugin-test\.yml$|\.github/scripts/changed-paths\.sh$)'`
  - plugin-lint: `'^(plugin/|install\.sh$|apps/macos/scripts/|\.claude-plugin/|\.github/workflows/plugin-lint\.yml$|\.github/scripts/changed-paths\.sh$)'`
  - contract: `'^(plugin/engine/scout/|plugin/engine/tests/fixtures/contract/|apps/.*/Fixtures/|apps/.*/Resources/|\.github/workflows/contract\.yml$|\.github/scripts/changed-paths\.sh$)'`

  Add the alternatives inside the outer parentheses. For example, plugin-test becomes `'^(plugin/|\.claude-plugin/|scripts/|apps/macos/Scout\.xcodeproj/project\.pbxproj$|\.github/workflows/plugin-test\.yml$|\.github/scripts/changed-paths\.sh$)'`.

- [ ] **Step 2: Shellcheck the release script in plugin-lint.** Change
  `shellcheck -S error install.sh plugin/scripts/*.sh apps/macos/scripts/*.sh .github/scripts/changed-paths.sh`
  to
  `shellcheck -S error install.sh scripts/*.sh plugin/scripts/*.sh apps/macos/scripts/*.sh .github/scripts/changed-paths.sh`.

- [ ] **Step 3: Add the version-match step to contract's `verify` job,** after "Install engine", with `working-directory: plugin/engine`:

```yaml
      # One version for Scout (spec 2026-10-05 §3): the app's MARKETING_VERSION
      # (every build configuration) must equal plugin.json and the other manifests.
      - name: App and plugin versions match
        working-directory: plugin/engine
        run: .venv/bin/python -m scout.scripts.versioning check
```

- [ ] **Step 4: Verify locally.** Each workflow must still parse, the new step must pass, and the changed-paths regexes must match their intended paths:

Run:
```bash
for f in .github/workflows/{plugin-test,plugin-lint,contract}.yml; do plugin/engine/.venv/bin/python -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1]))' "$f" && echo "ok $f"; done
(cd plugin/engine && .venv/bin/python -m scout.scripts.versioning check)
for p in scripts/release.sh apps/macos/Scout.xcodeproj/project.pbxproj; do grep -qE '^(plugin/|\.claude-plugin/|scripts/|apps/macos/Scout\.xcodeproj/project\.pbxproj$)' <<<"$p" && echo "plugin-test sees $p"; done
shellcheck -S error install.sh scripts/*.sh plugin/scripts/*.sh apps/macos/scripts/*.sh .github/scripts/changed-paths.sh && echo shellcheck-ok
```
Expected: three `ok` lines, `0.14.0`, two `plugin-test sees` lines, and `shellcheck-ok`.

- [ ] **Step 5: Commit.**

```bash
git add .github/workflows/plugin-test.yml .github/workflows/plugin-lint.yml .github/workflows/contract.yml
git commit -m "ci: run release tests and shellcheck for scripts/, and check that app and plugin versions match

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs for one version and one release

**Files:**
- Modify: `CLAUDE.md` (root)
- Modify: `apps/macos/CHANGELOG.md` (header)
- Modify: `apps/macos/README.md` (the maintainers' release section)
- Modify: `apps/macos/Scout/Shell/PluginVersion.swift` (doc comment, line 12)
- Modify: `docs/superpowers/plans/2026-10-04-monorepo-cutover-runbook.md` (Phase 7)

- [ ] **Step 1: Root `CLAUDE.md`.** Replace the tag bullet, which today starts with "**Tags are prefixed:** `plugin/vX.Y.Z`, `app/vX.Y.Z`. Never push a bare…", with:

```markdown
- **One version, one tag, one release.** Scout's app and plugin share one version `X.Y.Z`. It lives in
  `plugin/.claude-plugin/plugin.json`, the root `marketplace.json`, `plugin/engine/pyproject.toml`,
  `plugin/engine/scout/__init__.py`, and every `MARKETING_VERSION` in `apps/macos/Scout.xcodeproj`;
  `versioning check` (CI: `contract`) fails on drift. From v0.15.0 every release is ONE tag `vX.Y.Z`
  (candidates `vX.Y.Z-rc.N`), cut with `scripts/release.sh prepare` then `finalize`. Never create `app/v*` or
  `plugin/v*` tags; the existing ones (`app/v0.1.0`–`app/v0.14.0`, `plugin/v0.14.0`) are history. Bare
  `v0.4.0`–`v0.13.0` are the plugin's pre-monorepo releases. Agents never run `finalize`/`rc` for real.
```

- [ ] **Step 2: `apps/macos/CHANGELOG.md` header.** Replace its first paragraph and the "## Releases before the monorepo" section with:

```markdown
# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## Releases before the monorepo

Up to v0.14.0 the app was released from its own repository, now archived as
[Raven-Scout/scout-app-legacy](https://github.com/Raven-Scout/scout-app-legacy/releases). Those releases are
re-tagged `app/vX.Y.Z` here.
```

Keep the existing `## [Unreleased]` block between the header and "Releases before the monorepo".

- [ ] **Step 3: `apps/macos/README.md`.** Replace the "Maintainers: `scripts/release-app.sh …`" paragraph and its code block (around lines 116–120) with:

````markdown
Maintainers: releases are cut from the repo root with one script, which builds the app with the plugin
bundled from the same commit, signs and notarizes it, and publishes one `vX.Y.Z` release:

```bash
scripts/release.sh prepare            # opens the release PR (auto-picks patch/minor)
scripts/release.sh finalize v0.15.0   # after the PR merges
SKIP_NOTARIZE=1 SKIP_RELEASE=1 scripts/release.sh finalize v0.15.0   # dry run, publishes nothing
```
````

- [ ] **Step 4: `PluginVersion.swift` line 12.** Change "written by `scripts/release-app.sh`" to "written by `scripts/release.sh` (equal to the app's own version)".

- [ ] **Step 5: Runbook Phase 7.** In `docs/superpowers/plans/2026-10-04-monorepo-cutover-runbook.md`, replace the `## Phase 7 — Releases …` section body with a short status block. Keep the heading.

```markdown
- [x] **7.1 Plugin v0.14.0.** Released 2026-10-05 as `plugin/v0.14.0` (#306) with the old
  `release-plugin.sh`, marketplace only. The 2.2 sandbox upgraded 0.13.0 → 0.14.0 through the rename redirect.
  It is the last prefixed release, apart from an urgent `plugin/v0.14.x` patch.
- [ ] **7.2 From v0.15.0: one release.** See `docs/superpowers/specs/2026-10-05-unified-release-design.md` and
  `scripts/release.sh`. v0.15.0 is the first one-download release, and it needs Parts B and C, Sparkle (#74), and the
  spec §6 acceptance test. Jordan runs `prepare` and `finalize`; agents never run them for real.
```

- [ ] **Step 6: Check that no live doc still points at the old scripts as the way to release.**

Run: `git grep -n -E 'release-app\.sh|release-plugin\.sh' -- ':!docs/superpowers/plans/2026-09-03*' ':!docs/superpowers/specs/2026-09-03*' ':!docs/superpowers/plans/2026-10-04*' ':!docs/superpowers/specs/2026-10-05*' ':!docs/superpowers/plans/2026-10-05*' ':!apps/macos/scripts/release-app.sh' ':!plugin/scripts/release-plugin.sh'`
Expected: no output. Historical plans and specs are excluded on purpose.

- [ ] **Step 7: Commit.**

```bash
git add CLAUDE.md apps/macos/CHANGELOG.md apps/macos/README.md apps/macos/Scout/Shell/PluginVersion.swift docs/superpowers/plans/2026-10-04-monorepo-cutover-runbook.md
git commit -m "docs: one version, one tag, one release — scripts/release.sh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Retire the old release scripts (a SEPARATE PR, merged when v0.15.0 is cut)

Keep this out of the Task 1–6 PR. The spec keeps `release-plugin.sh` for urgent v0.14.x plugin patches until v0.15.0, so this PR lands as part of cutting v0.15.0. Open it as a draft from a branch based on `main` after Tasks 1–6 merge.

**Files:**
- Delete: `apps/macos/scripts/release-app.sh`, `plugin/scripts/release-plugin.sh`, `.github/workflows/release-plugin.yml`
- Modify: `plugin/engine/scout/scripts/versioning.py`. Remove `promote_changelog` (plugin-only) if `git grep -n promote_changelog\\b` shows no caller left besides the tests, and delete those tests.
- Modify: `.github/workflows/plugin-test.yml`. Its comment about `workflow_call` from release-plugin, and the `workflow_call` trigger itself, go if nothing else calls it (`git grep -n 'uses: ./.github/workflows/plugin-test.yml'`).
- Modify: `.github/scripts/changed-paths.sh`, but only its comment that names `release-plugin`.

- [ ] **Step 1: Confirm nothing still calls them.**

Run: `git grep -n -E 'release-app\.sh|release-plugin\.sh|release-plugin\.yml|promote_changelog\b' -- ':!docs/superpowers'`
Expected: only the files being deleted or edited in this task.

- [ ] **Step 2: Delete and edit as listed, then run the engine suite and the workflow parse.**

Run: `cd plugin/engine && .venv/bin/pytest tests/ -q && cd ../.. && for f in .github/workflows/*.yml; do plugin/engine/.venv/bin/python -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1]))' "$f" || echo "BAD $f"; done`
Expected: the suite passes (the known flakes are quarantined separately). No `BAD` lines.

- [ ] **Step 3: Commit, and open the PR as a draft titled "chore(release): retire release-app.sh, release-plugin.sh and release-plugin.yml (merge with v0.15.0)".**

```bash
git rm apps/macos/scripts/release-app.sh plugin/scripts/release-plugin.sh .github/workflows/release-plugin.yml
git add -A plugin/engine .github
git commit -m "chore(release): retire the per-artifact release scripts; scripts/release.sh is the only way to release

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

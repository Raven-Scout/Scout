# Monorepo Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb `Raven-Scout/scout-plugin` into `Raven-Scout/Scout` as a monorepo (`plugin/` + `apps/macos/`), so every engine↔client contract artifact is verifiable by one CI job on one commit.

**Architecture:** The plugin becomes a subdirectory-sourced marketplace entry — repo-root `.claude-plugin/marketplace.json` with `"source": "./plugin"` — so `CLAUDE_PLUGIN_ROOT` resolves to `plugin/` and every existing `${CLAUDE_PLUGIN_ROOT}/engine/...` path keeps working unchanged. Both histories are preserved via `git subtree`. Release cadences stay independent behind prefixed tags (`plugin/vX.Y.Z`, `app/vX.Y.Z`). The payoff is `contract.yml`: one cheap ubuntu job that regenerates the snapshots from their YAML sources and diffs them against every committed copy — test fixtures *and* shipped bundle resources.

**Tech Stack:** git subtree · GitHub Actions · Python 3.11/3.12 + uv + Typer (engine) · Swift 6 / swift-testing + Xcode 26 (macOS app) · `gh` CLI

**Spec:** [`docs/superpowers/specs/2026-09-03-monorepo-consolidation-design.md`](../specs/2026-09-03-monorepo-consolidation-design.md)

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **Absorbing repo is `Raven-Scout/Scout`.** `scout-plugin` is archived, not deleted. (§6)
- **Tags are prefixed:** `plugin/vX.Y.Z` and `app/vX.Y.Z`. First monorepo releases are **`plugin/v0.10.0`** (from 0.9.0) and **`app/v0.11.3`** (from 0.11.2). Historical bare `vX.Y.Z` tags are **not** imported from `scout-plugin` — they stay on the archived repo. (§6)
- **Plugin lives at `plugin/`; clients live at `apps/<platform>/`.** The macOS app goes to `apps/macos/`, never `app/`. (§5, §4)
- **`.claude-plugin/marketplace.json` MUST stay at the repo root** — `claude plugin marketplace add` reads it from there. `plugin.json` moves with the plugin to `plugin/.claude-plugin/plugin.json`. (§5, §6)
- **Everything the plugin needs at runtime MUST live under `plugin/`.** `CLAUDE_PLUGIN_ROOT` resolves to the materialized `plugin/` subtree only. No phase markdown, skill, hook, or `${CLAUDE_PLUGIN_ROOT}` path may change. (§5)
- **Edit-and-go is preserved.** No build step may appear between editing a phase markdown file and having it take effect. (§2 goal 5)
- **App releases stay a local script.** No `release-app.yml`; notarization needs a Developer ID keychain identity. (§6, §8, §11 Q1)
- **Path filters on every workflow are mandatory, not an optimization.** `apps/macos` CI is `macos-15` at a 10× billing multiplier. `contract.yml` is the one job that must never be filtered out of a cross-cutting change. (§8)
- **`scout-plugin` stays live and authoritative until Phase 4 completes.** Phases 1–3 are abandonable by deleting a branch. (§9)
- **Out of scope for this plan:** the one-command installer (§2, §11 Q2), Stage 2 fixture deletion (§7), absorbing iOS/Android (§9 Phase 5). Each is a separate plan.

## Known-Bad Baseline

These are the three drifts the migration must surface and then fix. **Record them; Task 8's acceptance test is that CI reports exactly these.**

| Copy | Current SHA-256 | Canonical SHA-256 | Defect |
|---|---|---|---|
| `ScoutTests/Fixtures/connectors.snapshot.json` | `7a57c9cc…` | `18a87be7…` | missing `mcp:fathom` |
| `Scout/Resources/connectors.snapshot.json` | `7a57c9cc…` | `18a87be7…` | missing `mcp:fathom` — **ships to users** |
| `ScoutTests/Fixtures/schedule.snapshot.json` | `23102d11…` | `e05c88e4…` | 4 slots read `on_miss: "skip"`, canonical says `"collapse"` |

Also fixed en route: `AppState.resolveScoutctlPath()`'s first candidate `~/scout-plugin/bin/scoutctl` does not exist (Task 11).

## File Structure

**Created:**
- `.claude-plugin/marketplace.json` — moved from `plugin/.claude-plugin/`; the repo's marketplace entry point. Sole responsibility: name the plugin and point `source` at `./plugin`.
- `CLAUDE.md` (repo root) — routes agents to the right subtree. Does not duplicate either subtree's content.
- `README.md` (repo root) — repo map; which artifact a reader wants and how to get it.
- `.github/workflows/contract.yml` — the cross-client contract check. Sole responsibility: regenerate snapshots from YAML and diff against every committed copy.
- `.github/workflows/app-ci.yml` — replaces `ci.yml`, path-filtered to `apps/macos/**`.
- `.github/workflows/plugin-test.yml`, `plugin-lint.yml`, `release-plugin.yml` — re-rooted and path-filtered versions of the absorbed workflows.
- `apps/macos/CHANGELOG.md` — new; the app had none.
- `apps/macos/Scout/Shell/ScoutctlLocator.swift` — extracted testable resolver (Task 11).
- `apps/macos/Scout/Shell/PluginVersion.swift` — installed-version reader + floor comparison (Task 12).

**Modified:**
- `plugin/engine/scout/scripts/versioning.py` — marketplace target resolves against the repo root, not the plugin root (Task 5).
- `plugin/engine/tests/unit/test_versioning.py` — fixture mirrors the nested layout (Task 5).
- `apps/macos/Scout/Shell/AppState.swift` — delegates to `ScoutctlLocator` (Task 11).
- `apps/macos/scripts/release-app.sh`, `plugin/scripts/release-plugin.sh` — prefixed tags, re-rooted paths (Task 13).

**Moved (git mv / subtree, no content change):** the whole macOS tree into `apps/macos/`, the whole plugin tree into `plugin/`.

---

### Task 1: Clear the runway (Phase 0)

No code. This is a gate: a restructure that silently invalidates a contributor's branch is the migration's largest avoidable cost (spec §10).

**Files:** none.

**Interfaces:**
- Consumes: nothing.
- Produces: a `scout-plugin` repo with **zero open PRs from external authors**, and a public freeze notice. Task 3 depends on this.

- [ ] **Step 1: List the external PRs that must be resolved**

```bash
gh pr list --repo Raven-Scout/scout-plugin --state open --limit 100 \
  --json number,title,author,updatedAt \
  --jq '.[] | select(.author.login != "jordanrburger") | "\(.number)\t\(.author.login)\t\(.title)"'
```

Expected at time of writing: 6 rows, including `205 PavelDo` (governance contributions) and `180 cvrysanek` (Asana/Jira/Google Chat connectors).

- [ ] **Step 2: Land or close each one, with a comment explaining why now**

For each PR number `N` from Step 1, either merge it or close it with a pointer. Do not leave it open.

```bash
gh pr comment N --repo Raven-Scout/scout-plugin --body "Heads-up: scout-plugin is being merged into Raven-Scout/Scout as a monorepo (the plugin will live at plugin/ in that repo). Resolving this PR before the move so your branch doesn't get invalidated mid-review. See Raven-Scout/Scout#99 for the design."
```

- [ ] **Step 3: Post the freeze notice as a pinned issue**

```bash
gh issue create --repo Raven-Scout/scout-plugin \
  --title "Notice: scout-plugin is moving into Raven-Scout/Scout (monorepo)" \
  --body "$(cat <<'EOF'
`scout-plugin` is being absorbed into [Raven-Scout/Scout](https://github.com/Raven-Scout/Scout) as a monorepo. The plugin will live at `plugin/` and remain a first-class marketplace entry.

**What you need to do once the move lands** (announced here and in the final release):

    claude plugin marketplace remove scout-plugin
    claude plugin marketplace add Raven-Scout/Scout
    claude plugin install scout@Scout

**Why:** contract artifacts (the connector roster, the schedule snapshot, the parser corpus) are single logical files that physically live in two or three repos, and no CI job can see across a repo edge. Two of them are currently stale in the shipped Mac app. Design: Raven-Scout/Scout#99.

**Until the move completes, this repo stays authoritative.** New PRs are welcome but may need re-targeting — comment here first and I'll tell you where to aim.
EOF
)"
```

- [ ] **Step 4: Verify the gate is green**

```bash
gh pr list --repo Raven-Scout/scout-plugin --state open --limit 100 \
  --json author --jq '[.[] | select(.author.login != "jordanrburger")] | length'
```

Expected: `0`

- [ ] **Step 5: Commit** — nothing to commit; this task's artifact lives on GitHub. Proceed to Task 2.

---

### Task 2: Move the macOS app into `apps/macos/` (Phase 1)

**Files:**
- Move: everything currently at the repo root of `Raven-Scout/Scout` except `docs/`, `LICENSE`, `.git*` → `apps/macos/`
- Modify: `.github/workflows/ci.yml` (path to the Xcode project)

**Interfaces:**
- Consumes: nothing.
- Produces: `apps/macos/Scout.xcodeproj` and `apps/macos/ScoutTests/`. Tasks 8, 9, 10, 11, 12, 13 all reference paths under `apps/macos/`.

- [ ] **Step 1: Create the migration branch from `main`**

```bash
cd ~/scout-app
git fetch origin
git worktree add -b migrate/monorepo .claude/worktrees/migrate-monorepo origin/main
cd .claude/worktrees/migrate-monorepo
```

- [ ] **Step 2: Record the pre-move file inventory (the completeness oracle)**

```bash
git ls-files | sort > /tmp/app-files-before.txt
wc -l < /tmp/app-files-before.txt
```

Expected: `311`

- [ ] **Step 3: Move the app tree**

`docs/` stays at the repo root — the spec and this plan live there, and Task 4 merges the plugin's docs into it. `LICENSE` stays at the root (both repos ship MIT; §5).

```bash
mkdir -p apps/macos
git mv Scout Scout.xcodeproj ScoutTests BACKLOG.md CLAUDE.md README.md design scripts apps/macos/
git status --short | head
```

- [ ] **Step 4: Verify nothing was lost — only re-prefixed**

```bash
git ls-files | sed 's#^apps/macos/##' | sort > /tmp/app-files-after.txt
diff /tmp/app-files-before.txt /tmp/app-files-after.txt && echo "IDENTICAL SET — no files lost"
```

Expected: `IDENTICAL SET — no files lost`

- [ ] **Step 5: Re-root the app CI workflow**

`xcodebuild` is invoked with `-project Scout.xcodeproj` from the repo root, which no longer resolves. Add a working directory. (Path filters come in Task 9 — one concern per task.)

In `.github/workflows/ci.yml`, change the `Run ScoutTests` step to:

```yaml
      - name: Run ScoutTests
        working-directory: apps/macos
        run: |
          set -o pipefail
          xcodebuild test \
            -project Scout.xcodeproj \
            -scheme Scout \
            -destination 'platform=macOS' \
            -only-testing:ScoutTests \
            -resultBundlePath TestResults.xcresult \
            CODE_SIGNING_ALLOWED=NO \
            CODE_SIGNING_REQUIRED=NO \
            CODE_SIGN_IDENTITY="" \
            | tee xcodebuild.log
```

and the upload step's paths to:

```yaml
          path: |
            apps/macos/TestResults.xcresult
            apps/macos/xcodebuild.log
```

- [ ] **Step 6: Verify the app still builds and tests green from its new home**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | tail -20
cd ../..
```

Expected: `** TEST SUCCEEDED **`. The `.xcodeproj` uses project-relative paths, so no project-file edit is needed — this step proves it.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(layout): move the macOS app into apps/macos/

Prepares for the scout-plugin subtree absorption. apps/<platform>/ rather
than app/, because four clients exist (macOS, iOS, Android, plus the plugin's
own CLI) and the iOS repo carries its own parser-corpus copy — see spec §4.

File set verified identical modulo the apps/macos/ prefix (311 files).
docs/ and LICENSE stay at the repo root; Task 4 merges the plugin's docs in.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Absorb `scout-plugin` into `plugin/` via subtree (Phase 1)

**Files:**
- Create: `plugin/**` (347 files, full history preserved)

**Interfaces:**
- Consumes: Task 1's cleared runway; Task 2's `apps/macos/` layout.
- Produces: `plugin/.claude-plugin/plugin.json`, `plugin/engine/bin/scoutctl`, `plugin/engine/scout/connectors.snapshot.json`, `plugin/engine/scout/schedule.snapshot.json`, `plugin/engine/tests/fixtures/contract/parser-corpus.json`, `plugin/CHANGELOG.md`, `plugin/templates/`, `plugin/scripts/release.sh`. Tasks 4–13 reference these.

- [ ] **Step 1: Record the plugin's pre-absorption inventory**

```bash
git -C ~/scout-plugin fetch origin
git -C ~/scout-plugin ls-tree -r --name-only origin/main | sort > /tmp/plugin-files-before.txt
wc -l < /tmp/plugin-files-before.txt
```

Expected: `347`

- [ ] **Step 2: Add the subtree, preserving history**

```bash
cd ~/scout-app/.claude/worktrees/migrate-monorepo
git subtree add --prefix=plugin https://github.com/Raven-Scout/scout-plugin.git main
```

Expected: `Added dir 'plugin'`. This creates a merge commit joining both histories. Note `git subtree` does **not** import tags — which is exactly the §6 requirement (no `v0.5.0`–`v0.9.0` collision).

- [ ] **Step 3: Verify the tree is complete**

```bash
git ls-files plugin/ | sed 's#^plugin/##' | sort > /tmp/plugin-files-after.txt
diff /tmp/plugin-files-before.txt /tmp/plugin-files-after.txt && echo "IDENTICAL SET — plugin fully absorbed"
```

Expected: `IDENTICAL SET — plugin fully absorbed`

- [ ] **Step 4: Verify history survived (not squashed)**

```bash
git log --oneline plugin/engine/scout/cli.py | wc -l
```

Expected: a number well above 1 (dozens). If it prints `1`, the subtree was squashed — reset and re-run Step 2 without `--squash`.

- [ ] **Step 5: Verify no tags were imported**

```bash
git tag --list 'v0.9.0' && echo "PROBLEM: plugin tags imported" || echo "OK: no plugin tags"
git tag --list | wc -l
```

Expected: `OK: no plugin tags` is wrong here — `v0.9.0` **also exists as a macOS app tag**, so it will list. The correct assertion is that the tag still points at the app's commit:

```bash
git log -1 --format='%s' v0.9.0
```

Expected: a macOS app commit subject, not a plugin one. (This is the §4 collision, resolved by never importing plugin tags.)

- [ ] **Step 6: Verify the engine still installs and tests green from its new home**

```bash
cd plugin/engine
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/bin/pytest tests/ -q 2>&1 | tail -15
cd ../..
```

Expected: all tests pass. `versioning.py`'s `PLUGIN_ROOT = parents[3]` still resolves correctly to `plugin/` for three of its four targets — the marketplace target is broken and Task 5 fixes it. If `test_versioning.py` fails here, that is the expected failure Task 5 addresses; note it and continue.

- [ ] **Step 7: Commit** — `git subtree add` already committed. Verify and move on.

```bash
git log --oneline -1
git status --porcelain | wc -l
```

Expected: the subtree merge commit; `0` uncommitted changes.

---

### Task 4: Resolve the seven collisions and add repo-root docs (Phase 1)

The spec's §4 collision list: `.github`, `.gitignore`, `CLAUDE.md`, `LICENSE`, `README.md`, `docs`, `scripts`. Tasks 2 and 3 already avoided four of them by moving the app's copies under `apps/macos/`. This task resolves the rest.

**Files:**
- Create: `CLAUDE.md` (root), `README.md` (root), `apps/macos/CHANGELOG.md`
- Move: `plugin/.claude-plugin/marketplace.json` → `.claude-plugin/marketplace.json`; `plugin/install.sh` → `install.sh`; `plugin/docs/**` → `docs/**`; `plugin/scripts/release.sh` → `plugin/scripts/release-plugin.sh`; `apps/macos/scripts/release.sh` → `apps/macos/scripts/release-app.sh`
- Modify: `.gitignore` (merge both)

**Interfaces:**
- Consumes: `plugin/` and `apps/macos/` from Tasks 2–3.
- Produces: `.claude-plugin/marketplace.json` at the repo root (Task 6 edits its `source`); `plugin/scripts/release-plugin.sh` and `apps/macos/scripts/release-app.sh` (Task 13 edits both); `plugin/PRIVACY.md`, `plugin/TERMS.md`, `plugin/LICENSE` decisions below.

- [ ] **Step 1: Lift the marketplace manifest to the repo root**

`claude plugin marketplace add` reads `.claude-plugin/marketplace.json` from the repo root. `plugin.json` stays with the plugin.

```bash
mkdir -p .claude-plugin
git mv plugin/.claude-plugin/marketplace.json .claude-plugin/marketplace.json
ls .claude-plugin/ plugin/.claude-plugin/
```

Expected: `.claude-plugin/marketplace.json` and `plugin/.claude-plugin/plugin.json`.

- [ ] **Step 2: Lift the installer and legal files to the repo root**

`install.sh` installs the plugin *and* is the documented entrypoint (`curl … | bash`), so it belongs at the root. The legal docs cover the whole project.

```bash
git mv plugin/install.sh install.sh
git mv plugin/PRIVACY.md PRIVACY.md
git mv plugin/TERMS.md TERMS.md
git rm -q plugin/LICENSE   # identical MIT text to the root LICENSE
diff <(git show HEAD:LICENSE) <(git show HEAD:plugin/LICENSE) && echo "confirmed identical before removal"
```

- [ ] **Step 3: Merge `docs/` by path**

The two `docs/` trees have no overlapping filenames (app: `superpowers/specs|plans`, `ROADMAP.md`, …; plugin: `plans/`, `superpowers/specs/`, `wishlist/`). Verify, then merge.

```bash
comm -12 <(git ls-files docs/ | sed 's#^docs/##' | sort) \
         <(git ls-files plugin/docs/ | sed 's#^plugin/docs/##' | sort)
```

Expected: no output (no filename collisions). Then:

```bash
git ls-files plugin/docs/ | while read -r f; do
  dest="docs/${f#plugin/docs/}"
  mkdir -p "$(dirname "$dest")"
  git mv "$f" "$dest"
done
git status --short docs/ | wc -l
```

If Step 3's `comm` printed anything, stop and rename the colliding plugin file with a `plugin-` prefix before moving.

- [ ] **Step 4: Disambiguate the two release scripts**

```bash
git mv plugin/scripts/release.sh plugin/scripts/release-plugin.sh
git mv apps/macos/scripts/release.sh apps/macos/scripts/release-app.sh
```

- [ ] **Step 5: Merge the two `.gitignore` files**

Both are small. Concatenate, dedupe, and re-scope the app-specific entries.

```bash
{
  echo "# ---- repo-wide ----"
  cat .gitignore
  echo
  echo "# ---- absorbed from scout-plugin ----"
  git show HEAD:plugin/.gitignore
} | awk '!seen[$0]++' > /tmp/merged-gitignore
mv /tmp/merged-gitignore .gitignore
git rm -q plugin/.gitignore
grep -c . .gitignore
```

- [ ] **Step 6: Write the repo-root `CLAUDE.md` that routes agents**

```bash
cat > CLAUDE.md <<'EOF'
# CLAUDE.md

Working notes for AI agents in the Scout monorepo.

## This repo holds two shipping artifacts

| Path | Artifact | Read next |
|---|---|---|
| `plugin/` | The Scout Claude Code plugin + Python engine (`scoutctl`) | `plugin/CLAUDE.md` |
| `apps/macos/` | The Scout macOS menu-bar app (SwiftUI) | `apps/macos/CLAUDE.md` |

**Work in the subtree that owns the change, and read that subtree's `CLAUDE.md`
first.** The two have different languages, test runners, and release flows.

## Rules that span both subtrees

- **`.claude-plugin/marketplace.json` lives at the REPO ROOT**, not in `plugin/`.
  `claude plugin marketplace add` reads it from there. `plugin.json` — the
  canonical version — lives at `plugin/.claude-plugin/plugin.json`.
- **Everything the plugin needs at runtime must stay under `plugin/`.**
  `CLAUDE_PLUGIN_ROOT` resolves to the materialized `plugin/` subtree only; a
  plugin file that reaches outside it is broken for every installed user, and
  nothing in this repo will tell you.
- **Tags are prefixed:** `plugin/vX.Y.Z`, `app/vX.Y.Z`. Never push a bare
  `vX.Y.Z` — the two artifacts collided on `v0.5.0`–`v0.9.0` before the merge.
- **Contract artifacts are generated, never hand-edited.** The connector roster
  and schedule snapshot have one canonical source under
  `plugin/engine/scout/` and are copied into `apps/*/`. Edit the `.yaml`, then
  regenerate — `contract.yml` fails the build if a copy drifts. See
  `docs/superpowers/specs/2026-09-03-monorepo-consolidation-design.md` §7.
- **Never edit a `*.snapshot.json` by hand.** That is what broke the shipped
  connector roster for four months.

## Cross-cutting changes

A change touching both subtrees (a new `scoutctl` subcommand the app calls, a
snapshot schema change) lands as ONE commit, and `contract.yml` is the gate.
That is the entire reason these repos were merged — do not split such a change
across two PRs out of habit.
EOF
```

- [ ] **Step 7: Write the repo-root `README.md`**

```bash
cat > README.md <<'EOF'
# Scout

Autonomous knowledge management and daily briefing for Claude Code — a
scheduled engine that cross-checks your work tools and maintains a persistent,
Obsidian-compatible knowledge base, plus native apps to read and steer it.

## What's in here

| Path | What it is |
|---|---|
| [`plugin/`](./plugin) | The Claude Code plugin and its Python engine (`scoutctl`). This is the part that does the work. |
| [`apps/macos/`](./apps/macos) | The macOS menu-bar app — Control Center, Action Items, Knowledge Base, schedules. |
| [`docs/`](./docs) | Design specs and implementation plans for both. |

## Install

One command sets up the plugin and engine:

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
```

Then open Claude Code and run `/scout-setup` to create your vault.

The macOS app is a separate download — grab the latest `.dmg` from
[Releases](https://github.com/Raven-Scout/Scout/releases) (look for an
`app/v*` tag). It is optional: the engine is fully usable from Claude Code and
the CLI without it.

> **Moved from `Raven-Scout/scout-plugin`?** Re-point your marketplace:
> ```
> claude plugin marketplace remove scout-plugin
> claude plugin marketplace add Raven-Scout/Scout
> claude plugin install scout@Scout
> ```

## Releases

The two artifacts version independently, behind prefixed tags:
`plugin/vX.Y.Z` and `app/vX.Y.Z`. The app declares a minimum plugin version and
tells you when your engine is behind.

## Legal

MIT — see [LICENSE](./LICENSE), [PRIVACY.md](./PRIVACY.md), [TERMS.md](./TERMS.md).
EOF
```

- [ ] **Step 8: Seed the app changelog (the app had none)**

```bash
cat > apps/macos/CHANGELOG.md <<'EOF'
# Changelog — Scout for macOS

Versions are tagged `app/vX.Y.Z`. The plugin has its own changelog at
[`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md) and its own version line.

## [Unreleased]

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.

## [0.11.2] and earlier

Released from the standalone `Raven-Scout/Scout` repository under bare `vX.Y.Z`
tags. See [releases](https://github.com/Raven-Scout/Scout/releases) and
`git log` for history.
EOF
```

- [ ] **Step 9: Verify no file is orphaned and both trees are intact**

```bash
git status --porcelain | grep -v '^[AMR]' | head
git ls-files | awk -F/ '{print $1}' | sort | uniq -c | sort -rn
```

Expected top-level entries: `apps`, `plugin`, `docs`, `.github`, `.claude-plugin`, plus the root files. No stray `Scout/`, `ScoutTests/`, or `templates/` at the root.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(layout): resolve collisions; add repo-root README + CLAUDE.md

- .claude-plugin/marketplace.json lifted to the repo root (claude plugin
  marketplace add reads it from there); plugin.json stays at
  plugin/.claude-plugin/ as the canonical version.
- install.sh, PRIVACY.md, TERMS.md lifted to the root; the duplicate MIT
  LICENSE under plugin/ removed after confirming byte-identical text.
- docs/ trees merged by path (verified zero filename collisions).
- The two release.sh scripts disambiguated as release-plugin.sh /
  release-app.sh.
- .gitignore merged and deduped.
- New root CLAUDE.md routes agents to the owning subtree and states the
  CLAUDE_PLUGIN_ROOT containment rule; new root README maps the repo and
  carries the marketplace re-point instructions.
- apps/macos/CHANGELOG.md seeded — the app never had one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Fix `versioning.py` for the split marketplace root (Phase 1)

Task 4 moved `marketplace.json` to the repo root, one level *above* the plugin subtree. `versioning.py` resolves all four of its targets against `PLUGIN_ROOT`, so `versioning check` now fails on a missing file. This is the one genuine code change in Phase 1.

**Files:**
- Modify: `plugin/engine/scout/scripts/versioning.py`
- Test: `plugin/engine/tests/unit/test_versioning.py`

**Interfaces:**
- Consumes: `.claude-plugin/marketplace.json` at repo root, `plugin/.claude-plugin/plugin.json` (Task 4).
- Produces: `read_versions(root=PLUGIN_ROOT, repo_root=None)`, `assert_in_sync(root=PLUGIN_ROOT, repo_root=None)`, `set_version(root=PLUGIN_ROOT, version=None, repo_root=None)` — all defaulting `repo_root` to `root.parent`. Task 6 and Task 13 call `versioning check`; Task 12 reads `plugin.json` directly and does not use this module.

- [ ] **Step 1: Write the failing test**

The fixture must mirror the real nested layout: plugin root at `tmp_path/plugin`, marketplace manifest at `tmp_path/.claude-plugin`. Replace `_fake_plugin` in `plugin/engine/tests/unit/test_versioning.py`:

```python
def _fake_plugin(tmp_path: Path, version: str = "1.2.3") -> Path:
    """Build the monorepo layout: plugin subtree + repo-root marketplace.json.

    Mirrors the real tree — marketplace.json is the repo's entry point and
    lives ABOVE the plugin subtree, because `claude plugin marketplace add`
    reads it from the repo root.
    """
    repo = tmp_path
    plugin = repo / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        f'{{\n  "name": "scout",\n  "version": "{version}"\n}}\n', encoding="utf-8"
    )
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        f'{{\n  "name": "scout-plugin",\n  "plugins": [\n'
        f'    {{\n      "name": "scout",\n      "source": "./plugin",\n'
        f'      "version": "{version}"\n    }}\n  ]\n}}\n',
        encoding="utf-8",
    )
    (plugin / "engine").mkdir()
    (plugin / "engine" / "pyproject.toml").write_text(
        f'[project]\nname = "scout-engine"\nversion = "{version}"\n', encoding="utf-8"
    )
    (plugin / "engine" / "scout").mkdir()
    (plugin / "engine" / "scout" / "__init__.py").write_text(
        f'"""scout."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    return plugin
```

Then add this test asserting the split-root resolution:

```python
def test_read_versions_reads_marketplace_from_the_repo_root(tmp_path):
    """marketplace.json sits above the plugin subtree; the other three inside it."""
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(plugin_root)
    assert versions == {
        "plugin.json": "1.2.3",
        "marketplace.json": "1.2.3",
        "pyproject.toml": "1.2.3",
        "__init__.py": "1.2.3",
    }


def test_set_version_writes_the_repo_root_marketplace(tmp_path):
    plugin_root = _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(plugin_root, version="1.3.0")
    marketplace = (tmp_path / ".claude-plugin" / "marketplace.json").read_text()
    assert '"version": "1.3.0"' in marketplace
    assert versioning.assert_in_sync(plugin_root) == "1.3.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd plugin/engine
.venv/bin/pytest tests/unit/test_versioning.py -v 2>&1 | tail -20
```

Expected: FAIL — `FileNotFoundError` for `<tmp>/plugin/.claude-plugin/marketplace.json`, because every target still resolves against the plugin root.

- [ ] **Step 3: Implement the split-root resolution**

In `plugin/engine/scout/scripts/versioning.py`, replace the `PLUGIN_ROOT` / `_TARGETS` block and the three functions that walk it:

```python
# engine/scout/scripts/versioning.py -> parents[3] == the plugin subtree root
PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# Root kinds for _TARGETS. marketplace.json is the repo's marketplace entry
# point and lives ABOVE the plugin subtree — `claude plugin marketplace add`
# reads it from the repo root — so it cannot resolve against PLUGIN_ROOT.
_PLUGIN = "plugin"
_REPO = "repo"

# (label, root kind, relative path, compiled regex capturing the version in group 'v')
_TARGETS = [
    (
        "plugin.json",
        _PLUGIN,
        ".claude-plugin/plugin.json",
        re.compile(r'("version":\s*")(?P<v>[^"]+)(")'),
    ),
    (
        "marketplace.json",
        _REPO,
        ".claude-plugin/marketplace.json",
        re.compile(r'("plugins"[\s\S]*?"version":\s*")(?P<v>[^"]+)(")'),
    ),
    (
        "pyproject.toml",
        _PLUGIN,
        "engine/pyproject.toml",
        re.compile(r'(?m)^(version\s*=\s*")(?P<v>[^"]+)(")'),
    ),
    (
        "__init__.py",
        _PLUGIN,
        "engine/scout/__init__.py",
        re.compile(r'(?m)^(__version__\s*=\s*")(?P<v>[^"]+)(")'),
    ),
]


def _target_path(kind: str, rel: str, plugin_root: Path, repo_root: Path) -> Path:
    return (plugin_root if kind == _PLUGIN else repo_root) / rel


def read_versions(root: Path = PLUGIN_ROOT, repo_root: Path | None = None) -> dict[str, str]:
    repo = root.parent if repo_root is None else repo_root
    out: dict[str, str] = {}
    for label, kind, rel, rx in _TARGETS:
        path = _target_path(kind, rel, root, repo)
        text = path.read_text(encoding="utf-8")
        m = rx.search(text)
        if not m:
            raise ValueError(f"no version field found in {path}")
        out[label] = m.group("v")
    return out


def assert_in_sync(root: Path = PLUGIN_ROOT, repo_root: Path | None = None) -> str:
    versions = read_versions(root, repo_root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise ValueError(f"version drift across manifests: {versions}")
    return distinct.pop()


def set_version(
    root: Path = PLUGIN_ROOT,
    version: str | None = None,
    repo_root: Path | None = None,
) -> None:
    if version is None:
        raise ValueError("set_version requires a version")
    repo = root.parent if repo_root is None else repo_root
    for _label, kind, rel, rx in _TARGETS:
        path = _target_path(kind, rel, root, repo)
        text = path.read_text(encoding="utf-8")
        new_text, n = rx.subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
        if n != 1:
            raise ValueError(f"failed to rewrite version in {path}")
        path.write_text(new_text, encoding="utf-8")
```

Note the error messages now interpolate the resolved `path` rather than `rel` — with two roots in play, a bare relative path no longer identifies the file.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd plugin/engine
.venv/bin/pytest tests/unit/test_versioning.py tests/unit/test_version_sync.py -v 2>&1 | tail -20
```

Expected: PASS, all tests.

- [ ] **Step 5: Verify the real tree is in sync**

```bash
cd plugin/engine
.venv/bin/python -m scout.scripts.versioning check
```

Expected: `0.9.0`

- [ ] **Step 6: Lint clean**

```bash
cd plugin/engine
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

Expected: no findings.

- [ ] **Step 7: Commit**

```bash
cd ~/scout-app/.claude/worktrees/migrate-monorepo
git add plugin/engine/scout/scripts/versioning.py plugin/engine/tests/unit/test_versioning.py
git commit -m "fix(versioning): resolve marketplace.json against the repo root

marketplace.json moved to the repo root in the monorepo layout, one level
above the plugin subtree, because \`claude plugin marketplace add\` reads it
from there. All four version targets previously resolved against PLUGIN_ROOT,
so \`versioning check\` broke on a missing file.

_TARGETS entries now carry a root kind (plugin | repo); read_versions,
assert_in_sync and set_version take an optional repo_root defaulting to
root.parent, so every existing single-argument call site is unchanged. Error
messages interpolate the resolved absolute path — with two roots a bare
relative path no longer identifies the file.

Test fixture rebuilt to mirror the real nested layout rather than a flat one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Point the marketplace at `./plugin` and prove a real install resolves (Phase 1)

**Files:**
- Modify: `.claude-plugin/marketplace.json`

**Interfaces:**
- Consumes: `.claude-plugin/marketplace.json` at the repo root (Task 4), `plugin/.claude-plugin/plugin.json` (Task 3).
- Produces: a marketplace manifest a real `claude plugin marketplace add` can resolve. Task 14's cutover depends on this working.

- [ ] **Step 1: Repoint `source` and the repo URLs**

Edit `.claude-plugin/marketplace.json` — `source` becomes `./plugin`, and both URLs move to the monorepo:

```json
{
  "name": "scout-plugin",
  "owner": {
    "name": "Jordan Burger"
  },
  "description": "Marketplace for the Scout autonomous knowledge management plugin",
  "plugins": [
    {
      "name": "scout",
      "source": "./plugin",
      "description": "Autonomous knowledge management and daily briefing system for Claude Code",
      "version": "0.9.0",
      "author": {
        "name": "Jordan Burger"
      },
      "homepage": "https://github.com/Raven-Scout/Scout",
      "repository": "https://github.com/Raven-Scout/Scout",
      "keywords": [
        "knowledge-management",
        "briefing",
        "automation",
        "scheduling",
        "obsidian"
      ]
    }
  ]
}
```

- [ ] **Step 2: Match the `homepage` / `repository` in `plugin.json`**

`plugin/.claude-plugin/plugin.json` still points at `Raven-Scout/scout-plugin`. Update both fields to `https://github.com/Raven-Scout/Scout`. Leave `name`, `version`, `description`, and `keywords` untouched — `versioning.py` regex-matches `version` and reserialization would churn the diff.

- [ ] **Step 3: Verify both manifests still parse and carry required keys**

This is the same assertion `plugin-lint.yml` makes (Task 9), run locally first:

```bash
python3 - <<'PY'
import json, sys
plugin = json.load(open("plugin/.claude-plugin/plugin.json"))
for key in ("name", "version"):
    if key not in plugin:
        sys.exit(f"plugin.json missing required key: {key!r}")
market = json.load(open(".claude-plugin/marketplace.json"))
if "name" not in market:
    sys.exit("marketplace.json missing required key: 'name'")
plugins = market.get("plugins")
if not isinstance(plugins, list) or not plugins:
    sys.exit("marketplace.json 'plugins' must be a non-empty list")
for i, entry in enumerate(plugins):
    for key in ("name", "source", "version"):
        if key not in entry:
            sys.exit(f"marketplace.json plugins[{i}] missing required key: {key!r}")
assert plugins[0]["source"] == "./plugin", plugins[0]["source"]
print("manifests valid; source =", plugins[0]["source"])
PY
```

Expected: `manifests valid; source = ./plugin`

- [ ] **Step 4: Verify versions still agree across all four files**

```bash
cd plugin/engine && .venv/bin/python -m scout.scripts.versioning check; cd ../..
```

Expected: `0.9.0`

- [ ] **Step 5: Prove a real marketplace add resolves the subdirectory**

This is the load-bearing verification for the whole layout — do not skip it. Add the local checkout as a marketplace under a throwaway name, confirm the plugin is discovered, then remove it.

```bash
claude plugin marketplace add "$(pwd)" 2>&1 | tail -5
claude plugin marketplace list 2>&1 | tail -20
```

Expected: the marketplace registers and lists a `scout` plugin. Confirm the resolved source is the `plugin/` subdirectory, then clean up:

```bash
claude plugin marketplace remove scout-plugin 2>&1 | tail -3
```

If the add fails, **stop** — the layout is wrong and every later task builds on it. Re-check that `.claude-plugin/marketplace.json` is at the repo root and `source` is `./plugin` (relative, with the leading `./`).

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json
git commit -m "feat(marketplace): source the plugin from ./plugin

marketplace.json stays at the repo root (claude plugin marketplace add reads
it from there) and now sources the plugin from the ./plugin subdirectory —
the standard multi-artifact pattern, verified against keboola-claude-kit,
keboola-agent-cli, and claude-plugins-official, all of which use
./plugins/<name> sources.

A subdir-sourced plugin materializes ONLY its own subdirectory into
~/.claude/plugins/cache, so apps/ never ships to plugin users and
CLAUDE_PLUGIN_ROOT keeps resolving engine/ paths unchanged.

homepage/repository in both manifests repointed to Raven-Scout/Scout.
Verified with a real 'claude plugin marketplace add' against the local
checkout.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Rewrite absolute cross-repo doc paths (Phase 1)

The merged specs and plans cross-reference each other by absolute machine path (`/Users/jordanburger/scout-app/docs/...`, `~/scout-plugin/...`). Inside one repo those become repo-relative links that actually resolve on GitHub.

**Files:**
- Modify: `docs/**/*.md` (the merged spec and plan trees)

**Interfaces:**
- Consumes: the merged `docs/` from Task 4.
- Produces: nothing other tasks consume. Independently reviewable and revertible.

- [ ] **Step 1: Inventory the absolute references**

```bash
grep -rln -E '/Users/jordanburger/(scout-app|scout-plugin)|~/(scout-app|scout-plugin)' docs/ | sort
grep -rc -E '/Users/jordanburger/(scout-app|scout-plugin)|~/(scout-app|scout-plugin)' docs/ 2>/dev/null \
  | grep -v ':0$' | awk -F: '{s+=$2} END {print "total references:", s}'
```

- [ ] **Step 2: Rewrite them to repo-relative paths**

Two mappings, applied in this order (longest prefix first, so `scout-app/docs` doesn't get half-rewritten):

```bash
grep -rl -E '/Users/jordanburger/(scout-app|scout-plugin)|~/(scout-app|scout-plugin)' docs/ \
| while read -r f; do
    /usr/bin/sed -i '' \
      -e 's#/Users/jordanburger/scout-app/docs/#docs/#g' \
      -e 's#~/scout-app/docs/#docs/#g' \
      -e 's#/Users/jordanburger/scout-app/#apps/macos/#g' \
      -e 's#~/scout-app/#apps/macos/#g' \
      -e 's#/Users/jordanburger/scout-plugin/#plugin/#g' \
      -e 's#~/scout-plugin/#plugin/#g' \
      "$f"
  done
```

- [ ] **Step 3: Verify none remain, and that no `~/Scout` vault path was touched**

`~/Scout` is the user's vault and must keep its absolute form — it is a real runtime path, not a repo location. The patterns above are anchored to `scout-app` / `scout-plugin`, so it should be untouched; verify.

```bash
grep -rn -E '/Users/jordanburger/(scout-app|scout-plugin)|~/(scout-app|scout-plugin)' docs/ | head
echo "--- remaining (expected: none above) ---"
grep -rc '~/Scout\b' docs/ 2>/dev/null | grep -v ':0$' | head -5
echo "--- vault references preserved (expected: some above) ---"
```

- [ ] **Step 4: Spot-check that a rewritten link resolves**

```bash
grep -rn 'plugin/engine/tests/fixtures/contract/parser-corpus.json' docs/ | head -3
ls plugin/engine/tests/fixtures/contract/parser-corpus.json
```

Expected: the referenced file exists at the rewritten path.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: rewrite cross-repo absolute paths as repo-relative

The merged spec and plan trees referenced each other by machine-absolute
path (/Users/jordanburger/scout-app/..., ~/scout-plugin/...). Inside one repo
those become repo-relative links that resolve on GitHub.

~/Scout vault paths are deliberately left absolute — that is a real runtime
location, not a repo one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `contract.yml` — the cross-client contract check (Phase 2)

**The payoff task.** Its acceptance test is that it goes **red** on the three known drifts. A green result here means the check is wired to nothing.

**Files:**
- Create: `.github/workflows/contract.yml`

**Interfaces:**
- Consumes: `plugin/engine/scout/connectors.yaml` → `connectors.snapshot.json`, `plugin/engine/scout/schedule.yaml` → `schedule.snapshot.json`, and the client copies at `apps/macos/ScoutTests/Fixtures/` and `apps/macos/Scout/Resources/`.
- Produces: a required check named `contract`. Task 10 makes it green; Task 14 requires it in the ruleset.

- [ ] **Step 1: Write the workflow**

```bash
cat > .github/workflows/contract.yml <<'EOF'
name: contract

# The cross-client contract gate. Before the monorepo, these artifacts were
# single logical files living in two or three repositories, and no CI job could
# see across a repo edge — so two of the three drifted, one of them into the
# shipped app bundle. This job is the whole reason the repos were merged.
#
# Deliberately cheap (ubuntu, Python only, no Xcode) so it can run on every PR
# touching either side without cost pressure. Never path-filter it OUT of a
# cross-cutting change.

on:
  pull_request:
    paths:
      - "plugin/engine/scout/*.yaml"
      - "plugin/engine/scout/*.snapshot.json"
      - "plugin/engine/scout/scripts/connectors_snapshot.py"
      - "plugin/engine/scout/scripts/schedule_snapshot.py"
      - "plugin/engine/tests/fixtures/contract/**"
      - "apps/**/Fixtures/**"
      - "apps/**/Resources/**"
      - ".github/workflows/contract.yml"
  push:
    branches: [main, "migrate/**"]

jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - name: Setup Python
        run: uv python install 3.12
      - name: Create venv
        working-directory: plugin/engine
        run: uv venv --python 3.12
      - name: Install engine
        working-directory: plugin/engine
        run: uv pip install -e ".[dev]"

      # 1. The canonical snapshots must match their YAML sources. (This check
      #    existed pre-merge and is retained verbatim.)
      - name: Canonical connectors snapshot matches connectors.yaml
        working-directory: plugin/engine
        run: .venv/bin/python -m scout.scripts.connectors_snapshot --check --target scout/connectors.snapshot.json
      - name: Canonical schedule snapshot matches schedule.yaml
        working-directory: plugin/engine
        run: .venv/bin/python -m scout.scripts.schedule_snapshot --check --target scout/schedule.snapshot.json

      # 2. Every client copy must match the canonical file. This is the check
      #    that could not exist before the merge. Resources/ is included
      #    because it SHIPS — a fixtures-only check would have left the
      #    user-facing drift live for another four months.
      - name: Client copies match the canonical snapshots
        run: |
          set -uo pipefail
          status=0
          check() {
            local canonical="$1" copy="$2"
            if [ ! -f "$copy" ]; then
              echo "::error file=$copy::missing client copy of $(basename "$canonical")"
              status=1
              return
            fi
            # generated_from carries the source repo SHA and legitimately
            # differs between regenerations; every other field must agree.
            if diff -u \
                 <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("generated_from", None); print(json.dumps(d, indent=2, sort_keys=True))' "$canonical") \
                 <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("generated_from", None); print(json.dumps(d, indent=2, sort_keys=True))' "$copy"); then
              echo "OK  $copy"
            else
              echo "::error file=$copy::drifted from $canonical — regenerate, do not hand-edit"
              status=1
            fi
          }

          check plugin/engine/scout/connectors.snapshot.json apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
          check plugin/engine/scout/connectors.snapshot.json apps/macos/Scout/Resources/connectors.snapshot.json
          check plugin/engine/scout/schedule.snapshot.json   apps/macos/ScoutTests/Fixtures/schedule.snapshot.json

          exit $status

      # 3. The parser corpus must be byte-identical across every client that
      #    vendors it. Same-tree now, so a plain cmp replaces the two
      #    hand-maintained SHA-256 constants (those come out in Stage 2).
      - name: Parser corpus is byte-identical across clients
        run: |
          set -uo pipefail
          canonical=plugin/engine/tests/fixtures/contract/parser-corpus.json
          status=0
          for copy in apps/*/[Ss]cout*Tests/Fixtures/parser-corpus.json; do
            [ -f "$copy" ] || continue
            if cmp -s "$canonical" "$copy"; then
              echo "OK  $copy"
            else
              echo "::error file=$copy::parser-corpus.json is not byte-identical to $canonical"
              status=1
            fi
          done
          exit $status
EOF
```

- [ ] **Step 2: Run the client-copy check locally and confirm it FAILS with exactly the known drifts**

```bash
set +e
canonical=plugin/engine/scout/connectors.snapshot.json
for copy in apps/macos/ScoutTests/Fixtures/connectors.snapshot.json \
            apps/macos/Scout/Resources/connectors.snapshot.json; do
  echo "=== $copy ==="
  diff -u <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' "$canonical") \
          <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' "$copy") \
  | head -20
done
echo "=== schedule ==="
diff -u <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' plugin/engine/scout/schedule.snapshot.json) \
        <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' apps/macos/ScoutTests/Fixtures/schedule.snapshot.json) \
| head -20
set -e
```

Expected — **three failures, matching the Known-Bad Baseline exactly**:
1. `ScoutTests/Fixtures/connectors.snapshot.json` — missing the `mcp:fathom` entry
2. `Scout/Resources/connectors.snapshot.json` — missing the `mcp:fathom` entry
3. `ScoutTests/Fixtures/schedule.snapshot.json` — 4 × `on_miss` `"skip"` vs `"collapse"`

**If any of these three comes up clean, stop.** Either the check is comparing the wrong paths or someone reconciled a copy out of band — reconcile the Known-Bad Baseline table with reality before continuing.

- [ ] **Step 3: Confirm the parser-corpus check passes (the control)**

```bash
cmp plugin/engine/tests/fixtures/contract/parser-corpus.json \
    apps/macos/ScoutTests/Fixtures/parser-corpus.json && echo "OK — corpus byte-identical"
```

Expected: `OK — corpus byte-identical`. This is the artifact whose two-sided checksum guard worked; it is the control that proves the check itself isn't just failing everything.

- [ ] **Step 4: Commit — deliberately committing a RED check**

```bash
git add .github/workflows/contract.yml
git commit -m "ci(contract): verify every client copy against the canonical snapshot

The check that could not exist before the monorepo. Regenerates the connector
and schedule snapshots from their YAML sources, then diffs every committed
client copy against the canonical file — test fixtures AND the shipped bundle
resource, since Scout/Resources/connectors.snapshot.json is the roster the
rail card actually renders and had no sync mechanism at all.

generated_from is excluded from the comparison: it carries the source repo SHA
and legitimately differs between regenerations.

THIS COMMIT LANDS THE CHECK RED, ON PURPOSE. It reports exactly the three
known drifts:
  - ScoutTests/Fixtures/connectors.snapshot.json  missing mcp:fathom
  - Scout/Resources/connectors.snapshot.json      missing mcp:fathom (ships!)
  - ScoutTests/Fixtures/schedule.snapshot.json    4x on_miss skip vs collapse
Task 10 reconciles them. A green result here would have meant the check was
wired to nothing.

The parser-corpus leg passes — that is the artifact whose two-sided SHA-256
guard held across three repos, and it serves as the control.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Path-filter and re-root the absorbed workflows (Phase 2)

**Files:**
- Create: `.github/workflows/plugin-test.yml`, `.github/workflows/plugin-lint.yml`, `.github/workflows/release-plugin.yml`, `.github/workflows/app-ci.yml`
- Delete: `.github/workflows/ci.yml`, `plugin/.github/workflows/{test,lint,release}.yml`

**Interfaces:**
- Consumes: `plugin/` and `apps/macos/` layouts.
- Produces: check names `app-ci` / `ScoutTests`, `plugin-test`, `plugin-lint` — Task 14 requires them in the ruleset. `release-plugin.yml` fires on `plugin/v*`, consumed by Task 13.

- [ ] **Step 1: Move the app workflow and add its path filter**

```bash
git mv .github/workflows/ci.yml .github/workflows/app-ci.yml
```

Then replace the `on:` block in `.github/workflows/app-ci.yml` (keep `concurrency` and the job body from Task 2 unchanged):

```yaml
name: app-ci

on:
  push:
    branches: [main]
    paths:
      - "apps/macos/**"
      - ".github/workflows/app-ci.yml"
  pull_request:
    branches: [main]
    paths:
      - "apps/macos/**"
      - ".github/workflows/app-ci.yml"
```

This is the 10×-multiplier `macos-15` runner — without the filter, a phase-markdown typo triggers a 30-minute Xcode build.

- [ ] **Step 2: Move the plugin workflows to the repo's workflow directory**

Workflows only run from `.github/workflows/` at the repo root; the absorbed `plugin/.github/workflows/` is inert.

```bash
git mv plugin/.github/workflows/test.yml    .github/workflows/plugin-test.yml
git mv plugin/.github/workflows/lint.yml    .github/workflows/plugin-lint.yml
git mv plugin/.github/workflows/release.yml .github/workflows/release-plugin.yml
rmdir -p plugin/.github/workflows 2>/dev/null || true
```

- [ ] **Step 3: Re-root and filter `plugin-test.yml`**

Change `name:` to `plugin-test`, the `on:` block to add filters, and every `working-directory: engine` to `plugin/engine`:

```yaml
name: plugin-test

on:
  push:
    branches: [main, "migrate/**"]
    paths:
      - "plugin/**"
      - ".github/workflows/plugin-test.yml"
  pull_request:
    paths:
      - "plugin/**"
      - ".github/workflows/plugin-test.yml"
  workflow_call:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    defaults:
      run:
        working-directory: plugin/engine
```

Keep the steps as-is. **Delete the two snapshot-drift steps** — `contract.yml` (Task 8) now owns snapshot verification, and duplicating it here means two jobs to update on every schema change:

```yaml
      # (removed) Verify connectors.snapshot.json drift  -> contract.yml
      # (removed) Verify schedule.snapshot.json drift    -> contract.yml
```

- [ ] **Step 4: Re-root and filter `plugin-lint.yml`**

Change `name:` to `plugin-lint`, add the same `paths:` filters, and set `working-directory: plugin/engine` in `defaults`. Then fix the three steps that use repo-root-relative paths — **this is where the move actually bites.**

The `Version sync guard` step's redundant `working-directory: engine` override must become `plugin/engine`:

```yaml
      - name: Version sync guard
        run: .venv/bin/python -m scout.scripts.versioning check
        working-directory: plugin/engine
```

`Shellcheck` runs from `${{ github.workspace }}` and every path shifted. `install.sh` is now at the repo root (Task 4) and the release scripts split across two subtrees:

```yaml
      - name: Shellcheck
        working-directory: ${{ github.workspace }}
        run: |
          sudo apt-get update && sudo apt-get install -y shellcheck
          # engine launcher — full severity (existing guard)
          shellcheck plugin/engine/bin/scoutctl
          # installer (repo root) + both release scripts — error severity
          shellcheck -S error install.sh plugin/scripts/*.sh apps/macos/scripts/*.sh
          # all shell templates seeded into user vaults (placeholders don't
          # break shellcheck parsing) — error severity
          find plugin/templates -name '*.sh.tmpl' -print0 | xargs -0 -r shellcheck -S error
```

`Validate plugin manifests` opened both manifests from one directory; they now live at two different roots:

```yaml
      - name: Validate plugin manifests
        working-directory: ${{ github.workspace }}
        run: |
          # plugin.json lives in the plugin subtree; marketplace.json is the
          # repo's entry point and lives at the repo root. Parse both as JSON
          # (the version-sync guard above uses regex, so a malformed manifest
          # that still regex-matches its version would slip past it) and assert
          # required keys are present.
          python3 - <<'PY'
          import json, sys

          plugin = json.load(open("plugin/.claude-plugin/plugin.json"))
          for key in ("name", "version"):
              if key not in plugin:
                  sys.exit(f"plugin.json missing required key: {key!r}")

          market = json.load(open(".claude-plugin/marketplace.json"))
          if "name" not in market:
              sys.exit("marketplace.json missing required key: 'name'")
          plugins = market.get("plugins")
          if not isinstance(plugins, list) or not plugins:
              sys.exit("marketplace.json 'plugins' must be a non-empty list")
          for i, entry in enumerate(plugins):
              for key in ("name", "source", "version"):
                  if key not in entry:
                      sys.exit(f"marketplace.json plugins[{i}] missing required key: {key!r}")

          if plugins[0]["source"] != "./plugin":
              sys.exit(f"marketplace.json source must be './plugin', got {plugins[0]['source']!r}")

          print("plugin manifests valid")
          PY
```

- [ ] **Step 5: Re-root `release-plugin.yml` and prefix its tag trigger**

```yaml
name: release-plugin

on:
  push:
    tags: ["plugin/v*"]

jobs:
  test:
    uses: ./.github/workflows/plugin-test.yml

  publish:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Verify tag matches manifest version
        run: |
          TAG_VERSION="${GITHUB_REF_NAME#plugin/v}"
          MANIFEST_VERSION="$(python3 -c "import json; print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])")"
          if [ "$TAG_VERSION" != "$MANIFEST_VERSION" ]; then
            echo "::error::tag ${GITHUB_REF_NAME} (version ${TAG_VERSION}) does not match plugin.json version ${MANIFEST_VERSION}"
            exit 1
          fi
          echo "tag matches manifest version: ${MANIFEST_VERSION}"
      - name: Extract changelog section for this tag
        id: notes
        run: |
          VERSION="${GITHUB_REF_NAME#plugin/v}"
          awk -v v="$VERSION" '
            $0 ~ "^## \\[" v "\\]" {grab=1; next}
            grab && /^## \[/ {exit}
            grab {print}
          ' plugin/CHANGELOG.md > RELEASE_NOTES.md
          echo "Extracted notes for $VERSION:"; cat RELEASE_NOTES.md
          if [ ! -s RELEASE_NOTES.md ]; then
            echo "::error::no CHANGELOG section found for ${VERSION} — refusing to publish an empty release"
            exit 1
          fi
      - name: Create GitHub Release
        run: gh release create "$GITHUB_REF_NAME" --title "$GITHUB_REF_NAME" --notes-file RELEASE_NOTES.md
        env:
          GH_TOKEN: ${{ github.token }}
```

Three changes from the original: the tag glob, `${GITHUB_REF_NAME#plugin/v}` (was `#v`), and the two `plugin/`-prefixed file paths.

- [ ] **Step 6: Verify every workflow is valid YAML with the paths it claims**

```bash
for f in .github/workflows/*.yml; do
  python3 -c "import yaml,sys; d=yaml.safe_load(open(sys.argv[1])); print(sys.argv[1], '->', d.get('name'))" "$f"
done
echo "--- referenced paths must exist ---"
ls plugin/engine/bin/scoutctl install.sh plugin/scripts/release-plugin.sh \
   apps/macos/scripts/release-app.sh plugin/.claude-plugin/plugin.json \
   .claude-plugin/marketplace.json apps/macos/Scout.xcodeproj
ls -d plugin/templates >/dev/null && echo "plugin/templates OK"
```

Expected: five workflow names printed; every path listed without error.

- [ ] **Step 7: Run the lint job's real commands locally**

```bash
cd plugin/engine
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
  && .venv/bin/mypy scout && .venv/bin/python -m scout.scripts.versioning check
cd ../..
command -v shellcheck >/dev/null && {
  shellcheck plugin/engine/bin/scoutctl
  shellcheck -S error install.sh plugin/scripts/*.sh apps/macos/scripts/*.sh
  find plugin/templates -name '*.sh.tmpl' -print0 | xargs -0 -r shellcheck -S error
  echo "shellcheck clean"
} || echo "shellcheck not installed locally — CI will cover it"
```

- [ ] **Step 8: Commit**

```bash
git add -A .github/ plugin/
git commit -m "ci: re-root and path-filter every workflow

Path filters are mandatory, not an optimization: apps/macos CI is macos-15
with a 30-minute timeout at a 10x billing multiplier, so without them a phase
markdown typo triggers a full Xcode build and an app-only Swift change runs
the 4-cell Python matrix.

- ci.yml -> app-ci.yml, filtered to apps/macos/**
- plugin/.github/workflows/{test,lint,release}.yml -> .github/workflows/
  plugin-{test,lint}.yml + release-plugin.yml, filtered to plugin/**
  (workflows only run from the repo root; the absorbed copies were inert)
- plugin-test.yml: working-directory engine -> plugin/engine; the two
  snapshot-drift steps REMOVED — contract.yml owns snapshot verification now,
  and duplicating it means two jobs to update per schema change.
- plugin-lint.yml: the three repo-root-relative steps re-rooted. Shellcheck
  now covers install.sh at the root plus BOTH release scripts. The manifest
  validator opens plugin.json and marketplace.json from their two different
  roots, and additionally asserts source == './plugin'.
- release-plugin.yml: tag glob plugin/v*, prefix stripped with #plugin/v,
  and CHANGELOG/plugin.json paths prefixed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Reconcile the three drifts (Phase 3)

**Files:**
- Modify: `apps/macos/ScoutTests/Fixtures/connectors.snapshot.json`, `apps/macos/Scout/Resources/connectors.snapshot.json`, `apps/macos/ScoutTests/Fixtures/schedule.snapshot.json`

**Interfaces:**
- Consumes: Task 8's `contract.yml`.
- Produces: a green `contract` check. Task 14's ruleset requires it.

- [ ] **Step 1: Confirm the canonical snapshots match their YAML sources first**

Never propagate a canonical file that is itself stale.

```bash
cd plugin/engine
.venv/bin/python -m scout.scripts.connectors_snapshot --check --target scout/connectors.snapshot.json
.venv/bin/python -m scout.scripts.schedule_snapshot --check --target scout/schedule.snapshot.json
cd ../..
```

Expected: both report OK. If either reports drift, regenerate the canonical file from YAML **before** copying anything to the clients.

- [ ] **Step 2: Regenerate the two client connector copies from canonical**

`--also-write-app-fixture` still points at the old `~/scout-app/ScoutTests/...` layout and knows nothing about `Resources/`, so write each target explicitly and disable the stale dual-write. (Retiring that flag entirely is Stage 2, spec §7.)

```bash
cd plugin/engine
.venv/bin/python -m scout.scripts.connectors_snapshot \
  --target ../../apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
.venv/bin/python -m scout.scripts.connectors_snapshot \
  --target ../../apps/macos/Scout/Resources/connectors.snapshot.json
cd ../..
```

If `connectors_snapshot.py`'s module entrypoint does not accept `--target` without also attempting the dual-write, use the `scoutctl` surface with the flag disabled:

```bash
plugin/engine/bin/scoutctl connectors snapshot \
  --no-also-write-app-fixture \
  --target apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
plugin/engine/bin/scoutctl connectors snapshot \
  --no-also-write-app-fixture \
  --target apps/macos/Scout/Resources/connectors.snapshot.json
```

- [ ] **Step 3: Regenerate the schedule fixture**

```bash
plugin/engine/bin/scoutctl schedule snapshot \
  --no-also-write-app-fixture \
  --target apps/macos/ScoutTests/Fixtures/schedule.snapshot.json
```

- [ ] **Step 4: Verify `mcp:fathom` and `on_miss: collapse` now reach the clients**

The specific defects from the Known-Bad Baseline:

```bash
grep -c 'fathom' apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
grep -c 'fathom' apps/macos/Scout/Resources/connectors.snapshot.json
grep -c '"on_miss": "skip"' apps/macos/ScoutTests/Fixtures/schedule.snapshot.json
grep -c '"on_miss": "collapse"' apps/macos/ScoutTests/Fixtures/schedule.snapshot.json
```

Expected: `1`, `1`, `0`, `4`.

- [ ] **Step 5: Run the full contract check locally — it must now be GREEN**

```bash
set -uo pipefail
status=0
check() {
  if diff -q <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' "$1") \
             <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("generated_from",None);print(json.dumps(d,indent=2,sort_keys=True))' "$2") >/dev/null; then
    echo "OK  $2"; else echo "DRIFT  $2"; status=1; fi
}
check plugin/engine/scout/connectors.snapshot.json apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
check plugin/engine/scout/connectors.snapshot.json apps/macos/Scout/Resources/connectors.snapshot.json
check plugin/engine/scout/schedule.snapshot.json   apps/macos/ScoutTests/Fixtures/schedule.snapshot.json
cmp -s plugin/engine/tests/fixtures/contract/parser-corpus.json apps/macos/ScoutTests/Fixtures/parser-corpus.json \
  && echo "OK  parser-corpus" || { echo "DRIFT  parser-corpus"; status=1; }
echo "exit: $status"
```

Expected: four `OK` lines, `exit: 0`.

- [ ] **Step 6: Run the app test suite — a roster test may now legitimately fail**

`ConnectorRosterTests` / `ConnectorHealthServiceTests` pinned the *old* roster. A test asserting a connector count or an exact key list will fail against the corrected snapshot. **That failure is correct** — the test was ratifying the bug (spec §1 symptom 2). Update the expectation to the new roster; do not revert the snapshot.

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | grep -E 'Test Case|failed|TEST (SUCCEEDED|FAILED)' | tail -25
cd ../..
```

Expected: either `** TEST SUCCEEDED **`, or failures confined to connector-roster expectations. For each such failure, update the expected roster in the test to include `mcp:fathom`.

- [ ] **Step 7: Commit**

```bash
git add apps/macos/ScoutTests/Fixtures/ apps/macos/Scout/Resources/
git commit -m "fix(contract): reconcile the three stale client snapshots

Turns contract.yml (Task 8) green. All three copies regenerated from the
canonical files, which were verified against their YAML sources first.

  - ScoutTests/Fixtures/connectors.snapshot.json  + mcp:fathom
  - Scout/Resources/connectors.snapshot.json      + mcp:fathom
  - ScoutTests/Fixtures/schedule.snapshot.json    on_miss skip -> collapse (x4)

The Resources/ copy is the one users actually saw: ConnectorHealthService
loads it from Bundle.main, so the shipped rail card has been rendering a
roster from 2026-05-04 that predates Fathom. It had no sync mechanism at all
— the engine writer only ever targeted the canonical file and the test
fixture, despite a code comment claiming three sinks.

Roster expectations in ConnectorRosterTests / ConnectorHealthServiceTests
updated where they pinned the old list. Those tests were asserting the
shipped-but-wrong roster; they ratified the bug rather than catching it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Fix `scoutctl` resolution (Phase 3)

The app's first-priority candidate `~/scout-plugin/bin/scoutctl` has never existed — the executable is at `engine/bin/scoutctl`. The app silently falls through to `/usr/bin/env scoutctl` and works by `$PATH` accident. The real install location is the plugin cache, whose path is recorded in `~/.claude/plugins/installed_plugins.json`.

**Files:**
- Create: `apps/macos/Scout/Shell/ScoutctlLocator.swift`
- Modify: `apps/macos/Scout/Shell/AppState.swift` (replace `resolveScoutctlPath()`)
- Test: `apps/macos/ScoutTests/Shell/ScoutctlLocatorTests.swift`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ScoutctlLocator.resolve(home:installedPluginsJSON:isExecutable:) -> AppState.ScoutctlInvocation` and `ScoutctlLocator.installedPluginPath(json:) -> String?`. Task 12 reuses `installedPluginPath` to read the installed version.

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p apps/macos/ScoutTests/Shell
cat > apps/macos/ScoutTests/Shell/ScoutctlLocatorTests.swift <<'EOF'
import Testing
import Foundation
@testable import Scout

@Suite("scoutctl resolution")
struct ScoutctlLocatorTests {
    /// Shape of ~/.claude/plugins/installed_plugins.json as of plugin 0.8.0.
    static let installedJSON = """
    {"plugins":{"scout@scout-plugin":[{"scope":"user",\
    "installPath":"/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0",\
    "version":"0.8.0","installedAt":"2026-05-09T11:18:42.951Z"}]}}
    """

    @Test func prefersTheInstalledPluginCache() {
        let home = URL(fileURLWithPath: "/Users/x")
        let expected = "/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0/engine/bin/scoutctl"
        let result = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: Self.installedJSON,
            isExecutable: { $0.path == expected }
        )
        #expect(result.executable.path == expected)
        #expect(result.argsPrefix.isEmpty)
    }

    @Test func fallsBackToDevCheckoutAtEngineBin() {
        // The historical bug: the dev candidate was ~/scout-plugin/bin/scoutctl,
        // which has never existed. It must be engine/bin/scoutctl.
        let home = URL(fileURLWithPath: "/Users/x")
        let dev = "/Users/x/scout-plugin/engine/bin/scoutctl"
        let result = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: nil,
            isExecutable: { $0.path == dev }
        )
        #expect(result.executable.path == dev)
    }

    @Test func neverProposesTheNonexistentBinPath() {
        let home = URL(fileURLWithPath: "/Users/x")
        var probed: [String] = []
        _ = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: nil,
            isExecutable: { probed.append($0.path); return false }
        )
        #expect(!probed.contains("/Users/x/scout-plugin/bin/scoutctl"),
                "the bin/ path never existed; probing it is the bug being fixed")
    }

    @Test func fallsBackToEnvOnPathWhenNothingOnDisk() {
        let result = ScoutctlLocator.resolve(
            home: URL(fileURLWithPath: "/Users/x"),
            installedPluginsJSON: nil,
            isExecutable: { _ in false }
        )
        #expect(result.executable.path == "/usr/bin/env")
        #expect(result.argsPrefix == ["scoutctl"])
    }

    @Test func installedPluginPathParsesTheManifest() {
        #expect(ScoutctlLocator.installedPluginPath(json: Self.installedJSON)
                == "/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0")
    }

    @Test func installedPluginPathToleratesGarbage() {
        #expect(ScoutctlLocator.installedPluginPath(json: "not json") == nil)
        #expect(ScoutctlLocator.installedPluginPath(json: #"{"plugins":{}}"#) == nil)
    }
}
EOF
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests/ScoutctlLocatorTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | tail -15
cd ../..
```

Expected: build failure — `cannot find 'ScoutctlLocator' in scope`.

- [ ] **Step 3: Write the implementation**

```bash
cat > apps/macos/Scout/Shell/ScoutctlLocator.swift <<'EOF'
import Foundation

/// Resolves where `scoutctl` lives on this machine.
///
/// Extracted from `AppState.resolveScoutctlPath()` so it can be tested without
/// touching the real filesystem — the previous inline version carried a
/// first-priority candidate (`~/scout-plugin/bin/scoutctl`) that has never
/// existed, and no test could see it. The executable is at
/// `engine/bin/scoutctl`.
///
/// Priority order, most authoritative first:
///  1. The installed plugin cache, whose path is recorded in
///     `~/.claude/plugins/installed_plugins.json`. This is where a normal
///     (non-developer) user's scoutctl actually is.
///  2. A developer checkout at `~/scout-plugin/engine/bin/scoutctl`.
///  3. pip/pipx/homebrew install locations.
///  4. `/usr/bin/env scoutctl`, leaning on `$PATH`.
enum ScoutctlLocator {
    /// Key under `plugins` in installed_plugins.json.
    private static let pluginKey = "scout@scout-plugin"

    /// Extract the installed plugin's `installPath` from the manifest JSON.
    /// Returns nil for absent, malformed, or empty manifests.
    static func installedPluginPath(json: String?) -> String? {
        guard let json,
              let data = json.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let plugins = root["plugins"] as? [String: Any],
              let entries = plugins[pluginKey] as? [[String: Any]],
              let first = entries.first,
              let path = first["installPath"] as? String,
              !path.isEmpty
        else { return nil }
        return path
    }

    /// Extract the installed plugin's version from the manifest JSON.
    static func installedPluginVersion(json: String?) -> String? {
        guard let json,
              let data = json.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let plugins = root["plugins"] as? [String: Any],
              let entries = plugins[pluginKey] as? [[String: Any]],
              let version = entries.first?["version"] as? String,
              !version.isEmpty
        else { return nil }
        return version
    }

    /// Default location of the plugin manifest.
    static func installedPluginsJSONURL(home: URL) -> URL {
        home.appendingPathComponent(".claude/plugins/installed_plugins.json")
    }

    static func resolve(
        home: URL,
        installedPluginsJSON: String?,
        isExecutable: (URL) -> Bool
    ) -> AppState.ScoutctlInvocation {
        var candidates: [URL] = []

        if let installed = installedPluginPath(json: installedPluginsJSON) {
            candidates.append(
                URL(fileURLWithPath: installed).appendingPathComponent("engine/bin/scoutctl")
            )
        }
        candidates += [
            home.appendingPathComponent("scout-plugin/engine/bin/scoutctl"),
            home.appendingPathComponent("miniconda3/bin/scoutctl"),
            home.appendingPathComponent(".local/bin/scoutctl"),
            URL(fileURLWithPath: "/opt/homebrew/bin/scoutctl"),
            URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        ]

        for url in candidates where isExecutable(url) {
            return AppState.ScoutctlInvocation(executable: url, argsPrefix: [])
        }
        return AppState.ScoutctlInvocation(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            argsPrefix: ["scoutctl"]
        )
    }
}
EOF
```

- [ ] **Step 4: Add the new file to the Xcode target**

`Scout.xcodeproj` uses explicit file references. Open the project and add both new files, or verify they were picked up if the target uses a synchronized group:

```bash
cd apps/macos
grep -c 'ScoutctlLocator' Scout.xcodeproj/project.pbxproj || echo "NOT REFERENCED — add via Xcode: File > Add Files to \"Scout\""
cd ../..
```

If the count is `0`, add `Scout/Shell/ScoutctlLocator.swift` to the **Scout** target and `ScoutTests/Shell/ScoutctlLocatorTests.swift` to the **ScoutTests** target in Xcode before continuing.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests/ScoutctlLocatorTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | grep -E 'Test Case|TEST (SUCCEEDED|FAILED)' | tail -12
cd ../..
```

Expected: 6 passing tests, `** TEST SUCCEEDED **`.

- [ ] **Step 6: Delegate from `AppState`**

Replace the body of `resolveScoutctlPath()` in `apps/macos/Scout/Shell/AppState.swift` — keep the signature so callers are untouched, and keep `ScoutctlInvocation` where it is (the locator references it):

```swift
    /// Resolve scoutctl's location. See `ScoutctlLocator` for the priority
    /// order and the tests that pin it.
    static func resolveScoutctlPath() -> ScoutctlInvocation {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let manifest = try? String(
            contentsOf: ScoutctlLocator.installedPluginsJSONURL(home: home),
            encoding: .utf8
        )
        return ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: manifest,
            isExecutable: { FileManager.default.isExecutableFile(atPath: $0.path) }
        )
    }
```

Delete the old candidate list and its comment block.

- [ ] **Step 7: Run the full suite and verify resolution against the real machine**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | grep -E 'TEST (SUCCEEDED|FAILED)' | tail -3
cd ../..
echo "--- what the app will now resolve on this machine ---"
python3 -c "
import json
d=json.load(open('$HOME/.claude/plugins/installed_plugins.json'))
p=d['plugins']['scout@scout-plugin'][0]['installPath']
print(p + '/engine/bin/scoutctl')
"
ls -la "$(python3 -c "
import json
d=json.load(open('$HOME/.claude/plugins/installed_plugins.json'))
print(d['plugins']['scout@scout-plugin'][0]['installPath'])
")/engine/bin/scoutctl"
```

Expected: `** TEST SUCCEEDED **`, and the resolved path exists and is executable — a real file this time, not `$PATH` luck.

- [ ] **Step 8: Commit**

```bash
git add apps/macos/Scout/Shell/ScoutctlLocator.swift \
        apps/macos/Scout/Shell/AppState.swift \
        apps/macos/ScoutTests/Shell/ScoutctlLocatorTests.swift \
        apps/macos/Scout.xcodeproj/project.pbxproj
git commit -m "fix(app): resolve scoutctl from the installed plugin cache

AppState's first-priority candidate was ~/scout-plugin/bin/scoutctl, which
has never existed — the executable is at engine/bin/scoutctl. The app fell
through to /usr/bin/env scoutctl and worked by \$PATH accident. Nothing could
catch it: the candidate list was inline in a static func with no seam, and
the path pointed into a different repository.

Extracted as ScoutctlLocator with injected home / manifest / probe, and now
resolved primarily from ~/.claude/plugins/installed_plugins.json ->
plugins['scout@scout-plugin'][0].installPath + engine/bin/scoutctl, which is
where a non-developer user's scoutctl actually lives. Dev checkout, pip/pipx/
homebrew, and \$PATH follow as fallbacks.

One test asserts the nonexistent bin/ path is never even probed, so the
regression cannot come back silently.

installedPluginVersion() lands here too — Task 12 needs it for the version
floor, and it parses the same manifest.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Stamp and enforce the plugin version floor (Phase 4)

Spec §6's payoff: the app asserts a minimum plugin version generated at build time from the tree it was built from, replacing the hand-maintained constant §8 of the v0.4 spec asked for and nobody ever wrote. Jordan's own machine currently runs plugin `0.8.0` against a repo at `0.9.0`, so this has a live case to detect.

**Files:**
- Create: `apps/macos/Scout/Shell/PluginVersion.swift`
- Test: `apps/macos/ScoutTests/Shell/PluginVersionTests.swift`
- Modify: `apps/macos/scripts/release-app.sh` (stamp the floor), `apps/macos/Scout.xcodeproj/project.pbxproj`

**Interfaces:**
- Consumes: `ScoutctlLocator.installedPluginVersion(json:)` from Task 11.
- Produces: `PluginVersion.compare(_:_:) -> ComparisonResult`, `PluginVersion.requiredFloor` (from `Info.plist` key `SCScoutPluginFloor`), `PluginVersion.isSatisfied(installed:floor:) -> Bool`.

- [ ] **Step 1: Write the failing test**

```bash
cat > apps/macos/ScoutTests/Shell/PluginVersionTests.swift <<'EOF'
import Testing
import Foundation
@testable import Scout

@Suite("plugin version floor")
struct PluginVersionTests {
    @Test func comparesSemverNumerically() {
        // The bug a string compare would introduce: "0.10.0" < "0.9.0".
        #expect(PluginVersion.compare("0.10.0", "0.9.0") == .orderedDescending)
        #expect(PluginVersion.compare("0.9.0", "0.10.0") == .orderedAscending)
        #expect(PluginVersion.compare("0.9.0", "0.9.0") == .orderedSame)
        #expect(PluginVersion.compare("1.0.0", "0.99.99") == .orderedDescending)
    }

    @Test func satisfiedWhenInstalledMeetsOrExceedsFloor() {
        #expect(PluginVersion.isSatisfied(installed: "0.10.0", floor: "0.10.0"))
        #expect(PluginVersion.isSatisfied(installed: "0.11.0", floor: "0.10.0"))
        #expect(!PluginVersion.isSatisfied(installed: "0.9.0", floor: "0.10.0"))
    }

    @Test func liveSkewOnThisMachineIsDetected() {
        // Installed 0.8.0 against a 0.9.0 repo — the real state when this
        // was written, and exactly what the floor exists to surface.
        #expect(!PluginVersion.isSatisfied(installed: "0.8.0", floor: "0.9.0"))
    }

    @Test func unknownInstalledVersionIsNotSatisfied() {
        #expect(!PluginVersion.isSatisfied(installed: nil, floor: "0.10.0"))
    }

    @Test func absentFloorIsAlwaysSatisfied() {
        // A dev build with no stamped floor must not nag.
        #expect(PluginVersion.isSatisfied(installed: "0.1.0", floor: nil))
        #expect(PluginVersion.isSatisfied(installed: nil, floor: nil))
    }

    @Test func toleratesShortAndDirtyVersionStrings() {
        #expect(PluginVersion.compare("1.2", "1.2.0") == .orderedSame)
        #expect(PluginVersion.compare("garbage", "0.0.1") == .orderedAscending)
    }
}
EOF
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests/PluginVersionTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | tail -12
cd ../..
```

Expected: build failure — `cannot find 'PluginVersion' in scope`.

- [ ] **Step 3: Write the implementation**

```bash
cat > apps/macos/Scout/Shell/PluginVersion.swift <<'EOF'
import Foundation

/// The app↔engine version contract.
///
/// Spec §6: the floor is stamped into Info.plist at build time from
/// `plugin/.claude-plugin/plugin.json` in the same tree the binary was built
/// from — a fact about the source, not a hand-maintained Swift literal. This
/// is what the v0.4 unification spec §8 called `CapabilityChecker` and never
/// got, which is why version skew was only ever detected by grepping
/// `scoutctl --help` output.
enum PluginVersion {
    /// Info.plist key written by `scripts/release-app.sh`.
    static let floorInfoKey = "SCScoutPluginFloor"

    /// Minimum plugin version this build requires; nil in unstamped dev builds.
    static var requiredFloor: String? {
        guard let v = Bundle.main.object(forInfoDictionaryKey: floorInfoKey) as? String,
              !v.isEmpty, v != "$(SCOUT_PLUGIN_FLOOR)"
        else { return nil }
        return v
    }

    /// Numeric semver-ish comparison. Missing components read as 0;
    /// non-numeric components read as 0, so garbage sorts below any real
    /// version rather than throwing.
    static func compare(_ lhs: String, _ rhs: String) -> ComparisonResult {
        let l = lhs.split(separator: ".").map { Int($0) ?? 0 }
        let r = rhs.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(l.count, r.count) {
            let a = i < l.count ? l[i] : 0
            let b = i < r.count ? r[i] : 0
            if a != b { return a < b ? .orderedAscending : .orderedDescending }
        }
        return .orderedSame
    }

    /// True when `installed` meets or exceeds `floor`.
    /// An absent floor is always satisfied (dev builds must not nag).
    /// An absent installed version is never satisfied when a floor exists —
    /// we could not find the plugin, which is itself the problem to surface.
    static func isSatisfied(installed: String?, floor: String?) -> Bool {
        guard let floor else { return true }
        guard let installed else { return false }
        return compare(installed, floor) != .orderedAscending
    }
}
EOF
```

- [ ] **Step 4: Add both files to the Xcode targets**

```bash
cd apps/macos
grep -c 'PluginVersion' Scout.xcodeproj/project.pbxproj || echo "NOT REFERENCED — add via Xcode"
cd ../..
```

If `0`, add `Scout/Shell/PluginVersion.swift` to the **Scout** target and `ScoutTests/Shell/PluginVersionTests.swift` to **ScoutTests**.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd apps/macos
xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests/PluginVersionTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | grep -E 'Test Case|TEST (SUCCEEDED|FAILED)' | tail -12
cd ../..
```

Expected: 6 passing tests.

- [ ] **Step 6: Stamp the floor in `release-app.sh`**

`release-app.sh` already stamps `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` via `xcodebuild` overrides. Add the floor, read from the plugin subtree in the same tree.

Near the existing `BUILD_NUMBER` assignment, add:

```bash
# The plugin version this build requires. Read from the plugin subtree in THIS
# tree, so the floor is a fact about the source the binary came from rather
# than a hand-maintained constant (spec §6).
PLUGIN_FLOOR="$(python3 -c "import json;print(json.load(open('$REPO_ROOT/plugin/.claude-plugin/plugin.json'))['version'])")"
[ -n "$PLUGIN_FLOOR" ] || { echo "✗ could not read plugin version floor" >&2; exit 1; }
echo "→ plugin version floor: $PLUGIN_FLOOR"
```

and add to the `xcodebuild` invocation alongside the existing overrides:

```bash
  SCOUT_PLUGIN_FLOOR="$PLUGIN_FLOOR" \
```

Then in Xcode, add an `Info.plist` entry `SCScoutPluginFloor` with value `$(SCOUT_PLUGIN_FLOOR)`, and set a `SCOUT_PLUGIN_FLOOR` build setting with an empty default so dev builds stay unstamped (the `requiredFloor` getter already treats the unexpanded placeholder as absent).

- [ ] **Step 7: Verify the floor is read correctly from the real tree**

```bash
python3 -c "import json;print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])"
```

Expected: `0.9.0` (or `0.10.0` after Task 13's bump).

- [ ] **Step 8: Commit**

```bash
git add apps/macos/Scout/Shell/PluginVersion.swift \
        apps/macos/ScoutTests/Shell/PluginVersionTests.swift \
        apps/macos/scripts/release-app.sh \
        apps/macos/Scout.xcodeproj/project.pbxproj
git commit -m "feat(app): stamp and enforce a plugin version floor

Spec §6. release-app.sh reads plugin/.claude-plugin/plugin.json from the tree
it is building and stamps it into Info.plist as SCScoutPluginFloor, so the
floor is a fact about the source rather than a hand-maintained Swift literal.
This is the CapabilityChecker the v0.4 spec §8 specified and the app never
got — until now, skew was detected only by grepping 'scoutctl --help'.

Comparison is numeric, not lexicographic: a string compare would rank
'0.10.0' below '0.9.0', which is precisely the boundary the next plugin
release crosses. One test pins the live skew on this machine (installed
0.8.0 vs repo 0.9.0). An absent floor is always satisfied so dev builds do
not nag; an absent installed version never satisfies a real floor, because
'the plugin could not be found' is itself the thing to surface.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Re-prefix both release scripts (Phase 4)

**Files:**
- Modify: `plugin/scripts/release-plugin.sh`, `apps/macos/scripts/release-app.sh`

**Interfaces:**
- Consumes: `release-plugin.yml` firing on `plugin/v*` (Task 9); `versioning.py`'s split roots (Task 5).
- Produces: `plugin/scripts/release-plugin.sh [patch|minor|major|X.Y.Z]` + `--finalize plugin/vX.Y.Z`; `apps/macos/scripts/release-app.sh X.Y.Z` tagging `app/vX.Y.Z`.

- [ ] **Step 1: Re-root and re-prefix `release-plugin.sh`**

Four changes. `ROOT` currently resolves to the repo root via `dirname/..`; it must resolve to the **plugin subtree** root, because `versioning.py` and `CHANGELOG.md` are plugin-relative:

```bash
# scripts/ is inside the plugin subtree, so ../ is the plugin root.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
PY="$ROOT/engine/.venv/bin/python"
```

Tag construction and the finalize guard take the prefix:

```bash
TAG="plugin/v$NEW"
...
if [ "${1:-}" = "--finalize" ]; then
    TAG="${2:-}"
    [ -n "$TAG" ] || { echo "error: --finalize requires a tag, e.g. --finalize plugin/v0.10.0" >&2; exit 1; }
    VER="${TAG#plugin/v}"
```

The CHANGELOG check reads the plugin's file at its new path:

```bash
    if ! git -C "$REPO_ROOT" show "origin/main:plugin/CHANGELOG.md" | grep -q "## \[$VER\]"; then
        echo "error: origin/main plugin/CHANGELOG.md has no [$VER] section — merge the release PR first" >&2
        exit 1
    fi
```

And `git add` must name files across both roots — `marketplace.json` is now at the repo root:

```bash
git -C "$REPO_ROOT" add \
        .claude-plugin/marketplace.json \
        plugin/.claude-plugin/plugin.json \
        plugin/engine/pyproject.toml \
        plugin/engine/scout/__init__.py \
        plugin/CHANGELOG.md
```

Every other `git` invocation in the script must also gain `-C "$REPO_ROOT"` (branch checks, fetch, commit, push, tag) — it operates on the repo, not the subtree.

- [ ] **Step 2: Re-prefix `release-app.sh`**

```bash
TAG="app/v$VERSION"
```

`REPO_ROOT` in this script resolves to `apps/macos` after the move, which is correct for `Scout.xcodeproj` and `build/`. But `BUILD_NUMBER` must count the **whole repo's** commits (spec §6 — Sparkle's monotonic key), and Task 12's `PLUGIN_FLOOR` reads from the repo root. Add:

```bash
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"       # apps/macos
REPO_ROOT="$(cd "$APP_ROOT/../.." && pwd)"          # monorepo root
BUILD_NUMBER="$(git -C "$REPO_ROOT" rev-list --count HEAD)"
```

and repoint the `PLUGIN_FLOOR` read from Task 12 at `$REPO_ROOT/plugin/.claude-plugin/plugin.json`. Rename the existing `REPO_ROOT` uses that mean "the app directory" to `APP_ROOT` (`BUILD_DIR`, `DMG`, the `xcodebuild -project` path).

- [ ] **Step 3: Verify both scripts pass shellcheck**

```bash
shellcheck -S error plugin/scripts/release-plugin.sh apps/macos/scripts/release-app.sh && echo "shellcheck clean"
```

- [ ] **Step 4: Dry-run the plugin release preparation logic without mutating anything**

```bash
bash -n plugin/scripts/release-plugin.sh && echo "release-plugin.sh parses"
bash -n apps/macos/scripts/release-app.sh && echo "release-app.sh parses"
cd plugin/engine && .venv/bin/python -m scout.scripts.versioning bump minor; cd ../..
```

Expected: both parse; `bump minor` prints `0.10.0` **without writing** (it is a pure function — `set` is what writes).

- [ ] **Step 5: Verify the build number is repo-wide and monotonic**

```bash
git rev-list --count HEAD
```

Expected: a value above 176 (the app's pre-merge count) — the one-time jump from absorbing the plugin's 338 commits, documented in spec §6. Monotonicity, Sparkle's only requirement, holds.

- [ ] **Step 6: Commit**

```bash
git add plugin/scripts/release-plugin.sh apps/macos/scripts/release-app.sh
git commit -m "build: prefix release tags and re-root both release scripts

Tags are now plugin/vX.Y.Z and app/vX.Y.Z (spec §6), which is what lets both
histories live in one repo — the two artifacts collided on bare v0.5.0-v0.9.0
before the merge.

release-plugin.sh: ROOT resolves to the plugin subtree (versioning.py and
CHANGELOG.md are plugin-relative) with REPO_ROOT above it, since every git
operation acts on the repo and marketplace.json now lives at the repo root.
Finalize strips #plugin/v and reads origin/main:plugin/CHANGELOG.md.

release-app.sh: APP_ROOT/REPO_ROOT split. BUILD_NUMBER counts the whole
repo's commits so Sparkle's comparison key stays monotonic across the merge
(it jumps once, by the plugin's 338 commits — expected, not a bug), and the
Task 12 version floor reads plugin/.claude-plugin/plugin.json from the repo
root.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: Cut over (Phase 4)

The point of no easy return. Everything before this is abandonable by deleting a branch.

**Files:**
- Modify: `install.sh`
- GitHub: rulesets on `Raven-Scout/Scout`; archive `Raven-Scout/scout-plugin`

**Interfaces:**
- Consumes: every prior task.
- Produces: a live monorepo with `plugin/v0.10.0` and `app/v0.11.3` released, and an archived `scout-plugin`.

- [ ] **Step 1: Fix `install.sh`'s marketplace target and its brittle path match**

`install.sh` adds the marketplace `Raven-Scout/scout-plugin` and locates the install root by matching the substring `scout-plugin` against `installPath` — both wrong after the move. Update:

```bash
MARKETPLACE="Raven-Scout/Scout"
```

and the root resolution, matching on the plugin's *name* rather than a repo-path substring:

```bash
ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout' in p['installPath'].lower()))" 2>/dev/null || true)"
```

The `claude plugin install` line's plugin spec follows the marketplace name:

```bash
claude plugin install scout@Scout
```

- [ ] **Step 2: Verify `install.sh --check` still passes and shellcheck is clean**

```bash
bash install.sh --check
shellcheck -S error install.sh && echo "shellcheck clean"
```

Expected: `preconditions OK (...)`.

- [ ] **Step 3: Run the whole verification suite one final time**

```bash
cd plugin/engine && .venv/bin/pytest tests/ -q 2>&1 | tail -5 && \
  .venv/bin/ruff check scout tests && .venv/bin/mypy scout && \
  .venv/bin/python -m scout.scripts.versioning check; cd ../..
cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout \
  -destination 'platform=macOS' -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  2>&1 | grep -E 'TEST (SUCCEEDED|FAILED)'; cd ../..
```

Expected: pytest green, lints clean, `0.9.0`, `** TEST SUCCEEDED **`.

- [ ] **Step 4: Commit and open the migration PR**

```bash
git add install.sh
git commit -m "feat(install): point the installer at the Raven-Scout/Scout marketplace

MARKETPLACE becomes Raven-Scout/Scout and the plugin spec becomes
scout@Scout. The install-root resolution matched the substring 'scout-plugin'
against installPath, which the monorepo's cache path no longer contains — now
matched on the plugin name instead.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin migrate/monorepo
gh pr create --base main --head migrate/monorepo \
  --title "refactor: absorb scout-plugin into the Scout monorepo" \
  --body "Implements docs/superpowers/plans/2026-09-03-monorepo-consolidation.md (Phases 0-4). Design: #99.

Verification gates met:
- plugin tree absorbed with history (347 files, identical set)
- app tree moved to apps/macos/ (311 files, identical set)
- \`claude plugin marketplace add\` resolves the ./plugin source
- \`contract.yml\` landed RED on the three known drifts, then green after reconciliation
- the shipped connector roster now carries mcp:fathom
- scoutctl resolves to a real file instead of \$PATH luck
- both suites green

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Update the branch ruleset to require the new checks**

The check names all changed. In `Raven-Scout/Scout` settings, require: `app-ci`, `plugin-test`, `plugin-lint`, and **`contract`**. Verify:

```bash
gh api repos/Raven-Scout/Scout/rulesets --jq '.[] | {id, name, target}'
```

`contract` must be required. An unrequired check that catches the bug this whole migration targets would be a poor outcome (spec §8).

- [ ] **Step 6: Merge, then tag both artifacts**

After CI is green and the PR is merged:

```bash
cd ~/scout-app/.claude/worktrees/migrate-monorepo
git checkout main && git pull
cd plugin/engine && .venv/bin/python -m scout.scripts.versioning set 0.10.0; cd ../..
# commit the bump + a plugin/CHANGELOG.md [0.10.0] section via the normal
# release PR flow, then:
bash plugin/scripts/release-plugin.sh --finalize plugin/v0.10.0
bash apps/macos/scripts/release-app.sh 0.11.3
```

Expected: `release-plugin.yml` publishes the `plugin/v0.10.0` release from the CHANGELOG section; `release-app.sh` builds, signs, notarizes, and publishes the `app/v0.11.3` DMG.

- [ ] **Step 7: Verify a clean-machine install works before archiving anything**

**Do not skip this.** It is the last chance to catch a broken marketplace before existing users are stranded (spec §10, High).

```bash
claude plugin marketplace remove scout-plugin 2>/dev/null || true
claude plugin marketplace add Raven-Scout/Scout
claude plugin install scout@Scout
claude plugin list --json | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k,v in d.get('plugins',{}).items():
    if 'scout' in k.lower():
        print(k, v[0]['version'], v[0]['installPath'])
"
ls \"$(claude plugin list --json | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(next(p['installPath'] for m in d.get('plugins',{}).values() for p in m if 'scout' in p['installPath'].lower()))
")/engine/bin/scoutctl\"
```

Expected: the plugin installs at `0.10.0` and `engine/bin/scoutctl` exists in the cache. If either fails, **do not archive `scout-plugin`** — fix forward first.

- [ ] **Step 8: Publish the final `scout-plugin` release, then archive**

```bash
gh release create v0.9.1 --repo Raven-Scout/scout-plugin \
  --title "v0.9.1 — moved to Raven-Scout/Scout" \
  --notes "$(cat <<'EOF'
**This repository has moved.** Scout's plugin now lives at
[Raven-Scout/Scout](https://github.com/Raven-Scout/Scout) under `plugin/`,
alongside the macOS app, so contract artifacts shared between the engine and
its clients are verified by one CI job on one commit.

**Re-point your marketplace:**

    claude plugin marketplace remove scout-plugin
    claude plugin marketplace add Raven-Scout/Scout
    claude plugin install scout@Scout

This repo is now archived and will publish no further releases. Its history,
tags, and releases stay browsable here; development continues at
Raven-Scout/Scout.

Why: Raven-Scout/Scout#99.
EOF
)"
gh repo archive Raven-Scout/scout-plugin --yes
```

- [ ] **Step 9: Update the org profile README and close the freeze notice**

```bash
gh issue close <freeze-notice-number> --repo Raven-Scout/scout-plugin \
  --comment "Done — the monorepo is live. Re-point with: claude plugin marketplace remove scout-plugin && claude plugin marketplace add Raven-Scout/Scout && claude plugin install scout@Scout"
```

Then edit `Raven-Scout/.github`'s profile README so every `scout-plugin` link points at `Raven-Scout/Scout`.

- [ ] **Step 10: Verify the cutover is complete**

```bash
gh repo view Raven-Scout/scout-plugin --json isArchived --jq '.isArchived'
gh release list --repo Raven-Scout/Scout --limit 5
grep -rn 'scout-plugin' README.md install.sh .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json | grep -v 'marketplace remove' | head
```

Expected: `true`; releases list `plugin/v0.10.0` and `app/v0.11.3`; the only surviving `scout-plugin` references are the deliberate migration instructions and the `marketplace.json` `name` field (which is the marketplace's own identifier, not a repo path — renaming it would strand users mid-migration; revisit in a later release).

---

## Self-Review

**1. Spec coverage.**

| Spec section | Task(s) |
|---|---|
| §5 target layout | 2, 3, 4 |
| §5 `CLAUDE_PLUGIN_ROOT` invariant | 4 (root `CLAUDE.md` rule), 6 (verified by real install) |
| §6 prefixed tags, historical tags left behind | 3 Step 5, 9 Step 5, 13 |
| §6 independent cadences, two changelogs | 4 Step 8, 9 Step 5, 13 |
| §6 version floor | 12 |
| §6 `CURRENT_PROJECT_VERSION` jump | 13 Step 5 |
| §7 Stage 1 contract check | 8 |
| §7 Stage 2 | **deferred by design** — separate plan (Global Constraints) |
| §8 path filters | 9 |
| §8 required-check migration | 14 Step 5 |
| §9 Phase 0–4 | 1 / 2–7 / 8–9 / 10–11 / 12–14 |
| §9 Phase 5 (iOS, Android) | **deferred by design** — separate plan |
| §10 archived-repo risk | 14 Steps 7, 8, 9 |
| §10 external contributors | 1 |
| §4 tag collision | 3 Step 5, 13 |

Two gaps found and closed while reviewing: **`versioning.py`'s split marketplace root** (§5 implies it; no spec section calls it out) became Task 5, and **`install.sh`'s `scout-plugin` substring match** became Task 14 Step 1. Both would have broken silently.

One item is scheduled beyond where the spec puts it: the §6 version floor has no assigned phase in §9. It lands in Phase 4 (Task 12) because that is where the release scripts change, and stamping the floor is a release-script change.

**2. Placeholder scan.** No `TBD`/`TODO`/"similar to Task N". Every code step carries the actual content. Two steps are deliberately conditional rather than vague — Task 4 Step 3 (`comm` first, then act on the result) and Tasks 11/12 Step 4 (check `project.pbxproj`, add via Xcode if absent) — because `.pbxproj` membership cannot be reliably scripted and pretending otherwise would be the placeholder.

**3. Type consistency.** `AppState.ScoutctlInvocation` is the return type of `ScoutctlLocator.resolve` and is left in `AppState.swift` (Task 11 Step 6 keeps it there, and the locator qualifies it). `installedPluginVersion(json:)` is introduced in Task 11 Step 3 and consumed in Task 12's Interfaces block. `PluginVersion.isSatisfied(installed:floor:)` takes two optionals in both the test and the implementation. `SCScoutPluginFloor` / `SCOUT_PLUGIN_FLOOR` are used consistently across Task 12 Steps 3, 6 and Task 13 Step 2. `REPO_ROOT`/`APP_ROOT`/`ROOT` are explicitly disambiguated in Task 13, which is where the same name previously meant two different directories.

**4. Ambiguity check.** Task 3 Step 5's assertion was rewritten once — `git tag --list v0.9.0` *does* match, because the app owns a `v0.9.0` too; the real check is that the tag still points at an app commit. Left in with that reasoning visible, since a reader would otherwise "fix" the test to match the wrong expectation.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-monorepo-consolidation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

Note that Task 1 is a human-judgment gate (six external contributors' PRs) and Task 14 Steps 5–9 need repo-admin rights and an Apple notarization keychain — neither is agent work. Tasks 2–13 are.

# Monorepo Consolidation Design

**Date:** 2026-09-03
**Status:** Design drafted, awaiting review
**Author:** Jordan Burger (brainstormed with Claude)
**Repos affected:** `Raven-Scout/Scout` (absorbing), `Raven-Scout/scout-plugin` (absorbed), `Raven-Scout/scout-iOS-app` + `Raven-Scout/scout-android` (future phases)
**Supersedes nothing.** Extends [`2026-04-24-scout-unification-design.md`](./2026-04-24-scout-unification-design.md) §8 (Distribution and update flows) and §11 (plugin/vault content boundary).

## 1. Problem statement

The v0.4 unification spec drew the right boundary — engine is canonical, vault is
user state, clients are UI — and then distributed those pieces across separate
git repositories. The boundary held. The **repository split did not**, because
several artifacts are logically single files that physically live in two or three
repos, and nothing in CI can see across a repo edge.

### The cross-repo contract surface today

| Artifact | Canonical home | Copies | Guard | State (2026-09-03) |
|---|---|---|---|---|
| `parser-corpus.json` | `scout-plugin/engine/tests/fixtures/contract/` | macOS `ScoutTests/Fixtures/`, iOS `ScoutMobileTests/Fixtures/` | SHA-256 asserted on **both** sides (`canonicalSHA256`, `EXPECTED_SHA256`) | ✅ in sync — `745dc8f8…` in all three |
| `connectors.snapshot.json` | `scout-plugin/engine/scout/` | macOS `ScoutTests/Fixtures/` | best-effort dual-write, **no cross-repo check** | ❌ drifted ~4 months |
| `schedule.snapshot.json` | `scout-plugin/engine/scout/` | macOS `ScoutTests/Fixtures/` | best-effort dual-write, **no cross-repo check** | ❌ drifted |
| `scoutctl` invocation contract | `engine/scout/cli.py` | macOS `ActionItemsWriter`, `ScheduleService`, … | runtime `--help` probe | ⚠️ probe-only, no version floor |
| `manifest.json` capability contract | `engine/scout/manifest.py` | *(intended: `CapabilityChecker.swift`)* | — | ❌ never built app-side |

This is a natural experiment with a clear result. **The one artifact guarded by a
checksum on both sides is correct across three repositories. Both artifacts
guarded only by a convenience mechanism have drifted.** The guard works; the
convenience does not.

### Observable symptoms

1. **`connectors.snapshot.json` is ~4 months stale in the macOS app.** The
   canonical file reads `generated_from: scout-plugin@61b12ca`; the app's copy
   reads `scout-plugin@fa60703` (2026-05-04) and is missing the `mcp:fathom`
   entry entirely. Fathom was added in plugin `8ee0d84` (2026-05-12, *"feat: port
   Fathom connector to v0.4 engine layout"*). The app's copy has not been touched
   since 2026-05-07. A snapshot test asserting "the exact roster the app must
   reflect" has been asserting a roster the engine abandoned.

2. **`schedule.snapshot.json` has drifted semantically.** Four slots read
   `on_miss: "collapse"` canonically and `on_miss: "skip"` in the app's fixture.
   The app's tests pin behavior the engine no longer implements.

3. **The sync mechanism cannot run where it matters.**
   [`cli.py:363`](https://github.com/Raven-Scout/scout-plugin/blob/main/engine/scout/cli.py#L363)
   dual-writes into a hardcoded `~/scout-app/ScoutTests/Fixtures/` and
   *"best-effort … skipped scout-app fixture write"* when that sibling checkout
   is absent. It cannot fire in CI, and it did not fire on the PR that added
   Fathom. Meanwhile `test.yml:31-42` verifies "the canonical file is in sync with
   the YAML" and its own comment says *"scout-app's bundled fixture is a synced
   copy"* — with no step that verifies the copy.

4. **Keeping the guarded file correct costs a 4-step, 3-repo ritual.** The macOS
   `CLAUDE.md` documents it: edit the corpus, copy it byte-for-byte into two
   sibling checkouts, update two checksum constants, then run three test suites
   on two platforms. It works because it is expensive enough to be conspicuous.
   That is not a property to preserve.

5. **Path drift is silent and already present.** The app's *first-priority*
   `scoutctl` candidate is `~/scout-plugin/bin/scoutctl`
   ([`AppState.swift:383`](https://github.com/Raven-Scout/Scout/blob/main/Scout/Shell/AppState.swift#L383)).
   That path does not exist — the executable is at `engine/bin/scoutctl`. The app
   falls through to `/usr/bin/env scoutctl` and works by accident via `$PATH`. No
   test in either repo could have caught this, because neither repo can see the
   other's tree.

6. **Version skew is unpoliced.** The v0.4 spec §8 specified an app-side
   `CapabilityChecker.swift` floor against `engine/manifest.json`. The engine
   ships `manifest.py`; the app contains **zero** references to a manifest. Skew
   is instead detected by shelling out `scoutctl action-items --help` and grepping
   the output — which catches "feature absent" but never "feature changed shape."
   [Scout#74](https://github.com/Raven-Scout/Scout/pull/74) records a live
   instance: installed plugin `0.7.2` against repo `0.7.3`.

### Root cause

An artifact whose correctness is defined by agreement between two repositories
has no CI job that can evaluate it. Every such artifact therefore degrades to
either a manual ritual (expensive, works) or a best-effort convenience (cheap,
fails silently). There is no third option **while the repo edge exists**.

## 2. Goals and non-goals

### Goals

1. Make every engine↔client contract artifact verifiable by a single CI job on a
   single commit.
2. Reduce the parser-corpus change procedure from 4 manual steps across 3 repos
   to one edit plus one CI check.
3. Make one commit produce a coherent `(engine, macOS app)` version pair, so the
   app can assert a plugin floor generated at build time rather than maintained
   by hand.
4. Preserve today's independent release cadences — an app UI fix must not force a
   plugin release, and vice versa.
5. Preserve edit-and-go. No build step may appear between editing a phase
   markdown file and having it take effect.
6. Leave a layout that absorbs `scout-iOS-app` and `scout-android` later without
   a second restructuring.

### Non-goals

- **A single download.** Explicitly out of scope; see §3. The repository layout
  cannot deliver it.
- Merging the two release *mechanisms* into one workflow. They stay separate and
  differently-shaped (§6).
- Absorbing iOS or Android in this migration. Layout accommodates them; the moves
  are separate phases (§9, Phase 5).
- Unifying the two `CLAUDE.md` files into one. Each subtree keeps its own agent
  instructions; only the repo-root one is new.
- Rewriting `install.sh` into a full bootstrapper. The one-command installer is
  follow-on work this migration *enables* (§3), not part of it.
- Any change to the `~/Scout` vault contract. The vault is untouched.

## 3. What a monorepo fixes, and what it cannot

This section exists because the motivating ask bundled two goals that have
different answers.

### It fixes: contract sync, release coordination, version coherence

All three symptom classes in §1 are "two repos cannot see each other." One repo
collapses them into a `diff` (§7).

### It does not fix: two downloads

Verified mechanically, not assumed:

- **A subdirectory-sourced plugin materializes only its own subdirectory.**
  Checked against `keboola-claude-kit/component-developer` (source
  `./plugins/component-developer`) in `~/.claude/plugins/cache/`: the cache
  contains that plugin's `.claude-plugin/`, `agents/`, `commands/`, `skills/`,
  `README.md` — and no repo root, no sibling plugins. So `apps/macos/` would
  never reach a plugin user's disk regardless of repository layout. (This is a
  *benefit*: plugin installs do not carry 27 kLOC of Swift.)

- **The two halves are owned by two package managers.** The plugin arrives via
  `claude plugin install` (Claude Code owns detection *and* application); the app
  arrives as a notarized DMG (Gatekeeper owns it). As
  [Scout#74](https://github.com/Raven-Scout/Scout/pull/74) states: *"The app
  track can self-install… The plugin track can only surface the gap and hand you
  the command — there is no app→Claude-Code interface to drive a slash command."*
  Merging repositories does not merge channels.

A genuinely single download would require bundling a Python runtime inside the
`.app` and abandoning the plugin channel — a much larger change, and one that
forfeits `/scout-setup`, plugin-declared hooks, and marketplace updates.

### What it does buy distribution

One tag on one commit yields a coherent artifact pair, which unblocks the work
already designed elsewhere:

- `install.sh` can install the plugin **and** `curl` the matching DMG from the
  same release — **one command**, two artifacts, provably matched.
- The app's plugin floor becomes a build-time constant read from
  `plugin/.claude-plugin/plugin.json` in the same tree, rather than a hand-bumped
  Swift literal — which is what [Scout#74](https://github.com/Raven-Scout/Scout/pull/74)
  (in-app updates) and [Scout#51](https://github.com/Raven-Scout/Scout/issues/51)
  (Mac-app-first onboarding with assisted engine install) both need.

The honest framing: **from "two downloads, two versions, hope they match" to "one
command, two artifacts, provably matched."**

## 4. Feasibility findings

Each verified against this machine or the live org, not inferred.

### Subdirectory plugin sources are first-class

`marketplace.json` already carries `"source": "./"`; the change is `"./plugin"`.
Locally installed precedents: `keboola-claude-kit` →
`./plugins/component-developer`, `keboola-agent-cli` → `./plugins/kbagent`,
`claude-plugins-official` → `./plugins/agent-sdk-dev`, plus a first-class
`git-subdir` source type used by third-party entries. This is the standard
multi-plugin repository pattern.

### Histories merge trivially

| | commits | pack size |
|---|---|---|
| `Scout` (macOS) | 176 | 252 KiB |
| `scout-plugin` | 338 | 107 KiB |

A `git subtree add` (or `read-tree` merge) preserving both histories is a
non-event at this scale.

### Only seven top-level collisions, all boilerplate

`.github`, `.gitignore`, `CLAUDE.md`, `LICENSE`, `README.md`, `docs`, `scripts`.

Everything load-bearing is disjoint — app-only: `Scout/`, `ScoutTests/`,
`Scout.xcodeproj`, `BACKLOG.md`, `design/`; plugin-only: `.claude-plugin/`,
`engine/`, `phases/`, `skills/`, `commands/`, `hooks/`, `templates/`, `tools/`,
`install.sh`, `CHANGELOG.md`, `PRIVACY.md`, `TERMS.md`.

### Tag namespaces collide — this is the one hard blocker

`v0.5.0`, `v0.6.0`, `v0.7.0`, `v0.8.0`, `v0.9.0` exist in **both** repos with
different meanings. Both histories cannot be merged with tags intact. Resolved by
§6.

### Four clients exist, not one

| Repo | Language | Size | Last push | Carries `parser-corpus.json` |
|---|---|---|---|---|
| `Scout` (macOS) | Swift | — | active | yes |
| `scout-plugin` | Python | — | active | yes (canonical) |
| `scout-iOS-app` | Swift | 1.4 MB | 2026-06-28 | **yes** |
| `scout-android` | Kotlin | 121 KB | 2026-07-12 | no (stub: LICENSE + README) |

This is why the layout is `apps/macos/`, not `app/`. The contract-fixture problem
is a four-client problem; solving it for one client and leaving two more outside
the repo edge reproduces the bug on a delay.

## 5. Target layout

```
Raven-Scout/Scout                     # absorbing repo; scout-plugin archived
├─ .claude-plugin/
│  └─ marketplace.json                # stays at root; source: "./plugin"
├─ plugin/                            # everything CLAUDE_PLUGIN_ROOT must see
│  ├─ .claude-plugin/plugin.json      # canonical plugin version
│  ├─ engine/                         # scout package, bin/scoutctl, tests, pyproject
│  ├─ phases/  skills/  commands/  hooks/  templates/  tools/
│  ├─ CHANGELOG.md                    # plugin changelog, unchanged
│  └─ CLAUDE.md                       # plugin-subtree agent instructions
├─ apps/
│  └─ macos/                          # Scout.xcodeproj, Scout/, ScoutTests/, design/
│     ├─ CHANGELOG.md                 # new: app changelog (see §6)
│     └─ CLAUDE.md                     # app-subtree agent instructions
├─ docs/                              # merged; specs/ and plans/ from both
├─ scripts/
│  ├─ release-plugin.sh               # was scout-plugin/scripts/release.sh
│  ├─ release-app.sh                  # was Scout/scripts/release.sh
│  └─ install-venv.sh
├─ install.sh                         # marketplace-add + plugin-install entrypoint
├─ LICENSE  PRIVACY.md  TERMS.md
├─ README.md                          # new: repo map + which artifact you want
└─ CLAUDE.md                          # new: routes agents to the right subtree
```

`apps/ios/` and `apps/android/` slot in later with no further restructuring.

### Why the plugin sits in a subdirectory rather than at the root

Keeping the plugin at the root would preserve `"source": "./"` and require no
marketplace change — but the plugin cache would then materialize the *entire*
repo, shipping the macOS and (later) iOS Swift trees to every plugin user. The
subdirectory keeps the plugin payload lean and is the pattern every multi-artifact
marketplace already uses (§4).

### `CLAUDE_PLUGIN_ROOT` invariant

`CLAUDE_PLUGIN_ROOT` resolves to the materialized `plugin/` subtree. **Everything
the plugin needs at runtime must live under `plugin/`.** Existing references of
the form `${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl` keep working unchanged,
because `engine/` moves *with* the plugin. No phase markdown, skill, or hook path
changes. This is the property that preserves edit-and-go.

## 6. Versioning and release contract

**Decision: independent, prefixed tags.** The two artifacts keep separate version
lines and separate cadences.

| | Plugin | macOS app |
|---|---|---|
| Tag | `plugin/vX.Y.Z` | `app/vX.Y.Z` |
| Canonical version | `plugin/.claude-plugin/plugin.json` | `release-app.sh` argument |
| Derived into | `marketplace.json`, `engine/pyproject.toml`, `engine/scout/__init__.py` (by `scout.scripts.versioning`) | `MARKETING_VERSION` stamped at build |
| Changelog | `plugin/CHANGELOG.md` | `apps/macos/CHANGELOG.md` (new) |
| Release trigger | `release-plugin.yml`, on pushed tag `plugin/v*` | `release-app.sh` locally; the script itself tags and publishes (§11 Q1) |
| Next version | `plugin/v0.10.0` (from 0.9.0) | `app/v0.11.3` (from 0.11.2) |

### Historical tags

Both repos' bare `vX.Y.Z` tags stay where they are. The absorbed repo's tags are
**not** carried into the monorepo — `scout-plugin` is archived with its releases
and tags intact and permanently browsable. The monorepo's tag history begins at
`plugin/v0.10.0` and `app/v0.11.3`. This sidesteps the §4 collision entirely
rather than renaming 14 historical tags.

The macOS app's existing bare `v0.5.0`–`v0.11.2` tags remain valid in the
absorbing repo and are simply superseded by the `app/` prefix going forward.

### Both release scripts keep their current shape

They are structurally different and should stay so:

- **Plugin** (`release-plugin.sh`) is two-phase and PR-gated, because `main` is
  ruleset-protected: `prepare` bumps the four derived version files on a
  `release/vX.Y.Z` branch and opens a PR; `--finalize` tags the merge commit
  after CI is green. Unchanged except for the tag prefix and the `plugin/`
  working directory.
- **App** (`release-app.sh`) is one-shot and local, because it needs a Developer
  ID keychain identity and Apple's notary round-trip, neither of which lives in
  CI. Unchanged except for the tag prefix and the `apps/macos/` working
  directory.

### The version floor replaces the capability probe

The payoff of coherent tags. `release-app.sh` reads
`plugin/.claude-plugin/plugin.json` at build time and stamps the value into the
app as `requiredPluginVersion`. The app compares it against the installed plugin
version — authoritative in `~/.claude/plugins/installed_plugins.json` at
`plugins["scout@scout-plugin"][0].version`, per
[Scout#74](https://github.com/Raven-Scout/Scout/pull/74) — and surfaces a real
"your engine is behind" state instead of an empty view.

This finally lands v0.4 spec §8's `CapabilityChecker`, and it lands it *without*
a hand-maintained constant, because the floor is now a fact about the tree the
binary was built from. The `scoutctl --help` probe in
`ActionItemsEnvironmentCheck` stays as a belt-and-braces runtime check.

### `CURRENT_PROJECT_VERSION` after the merge

`release-app.sh` derives the build number from `git rev-list --count HEAD`, which
[Scout#74](https://github.com/Raven-Scout/Scout/pull/74) relies on as Sparkle's
monotonic comparison key. Absorbing 338 commits causes a **one-time jump**, and
thereafter plugin-only commits also increment it. Monotonicity — the only property
Sparkle requires — is preserved in both cases. No mitigation needed; documented so
it is not mistaken for a bug when the number leaps.

## 7. The contract-fixture story

The actual payoff, and the reason to do this at all.

### Stage 1 — make drift detectable (migration scope)

One CI job, `contract.yml`, regenerates the snapshots from their YAML sources and
`diff`s them against every client's committed fixture. Both §1 drifts become
red builds on the commit that introduces them. This alone converts three silent
failure modes into loud ones.

### Stage 2 — make drift unrepresentable (immediately after)

Better than detecting a copy is not having one. Two moves:

1. **Delete the macOS app's snapshot fixtures.** Point the Xcode test target at
   `plugin/engine/scout/connectors.snapshot.json` and
   `plugin/engine/scout/schedule.snapshot.json` via a relative path in the same
   tree. Then retire the `--write-app-fixture` dual-write in `cli.py` and its
   hardcoded `~/scout-app` path. There is no copy left to drift.

2. **Delete the parser-corpus checksum guards.** With the corpus and both
   consumers in one tree, `canonicalSHA256` and `EXPECTED_SHA256` are ceremony
   protecting against a distance that no longer exists. The 4-step ritual in the
   macOS `CLAUDE.md` reduces to: edit the corpus, run both suites.

Tests stay hermetic. Reading a sibling path inside the repository is not a live
`~/Scout` vault dependency — the property `test_parser_contract.py` was hardened
for is unaffected.

### Ordering matters

Stage 1 ships **inside** the migration, so the migration itself is verified by
the check it installs — the very first `contract.yml` run should fail red on the
two known drifts, then go green on the commit that reconciles them. That failure
is the acceptance test for the whole exercise.

Stage 2 ships **after** the migration lands, as its own PR. Deleting guards and
rewiring test targets while also moving 658 files is how migrations go wrong.

Note that Stage 2 only removes the copies for clients *inside* the repo. iOS
keeps its corpus copy and its guard until Phase 5 absorbs it — which is the
argument for Phase 5 rather than a reason to delay Stage 2.

## 8. CI design

### Path filters are mandatory, not an optimization

App CI runs on `macos-15` with a 30-minute timeout and a 10× billing multiplier;
plugin CI runs a 4-cell matrix (`ubuntu-latest` + `macos-latest` × Python
3.11/3.12). Without filters, a typo in a phase markdown file triggers a full
Xcode build, and an app-only Swift change runs the Python matrix.

| Workflow | Trigger paths | Runner |
|---|---|---|
| `app-ci.yml` | `apps/macos/**`, `.github/workflows/app-ci.yml` | `macos-15` |
| `plugin-test.yml` | `plugin/**`, `.github/workflows/plugin-test.yml` | ubuntu + macos × py3.11/3.12 |
| `plugin-lint.yml` | `plugin/**` | ubuntu |
| `contract.yml` | `plugin/engine/scout/*.yaml`, `plugin/engine/scout/*.snapshot.json`, `plugin/engine/tests/fixtures/contract/**`, `apps/**/Fixtures/**` | ubuntu |
| `release-plugin.yml` | tag `plugin/v*` | ubuntu |

App releases stay a local script (§6) — notarization needs a Developer ID
keychain identity — so there is no `release-app.yml` in this design. §11 Q1 tracks
whether that should change.

`contract.yml` is deliberately cheap (ubuntu, Python only) so it can run on every
PR touching either side without cost pressure. It is the one job that must never
be path-filtered *out* of a cross-cutting change.

### Required-check migration

Both repos' branch protections reference check names that change. The rulesets on
`Raven-Scout/Scout` must be updated to require `app-ci`, `plugin-test`, and
`contract` — with `contract` required, since an unrequired check that catches the
bug this whole design targets would be a poor outcome.

## 9. Migration sequence

Ordered, gated, and reversible until Phase 4. `scout-plugin` stays live and
authoritative until Phase 4 completes.

**Phase 0 — clear the runway.** 17 PRs are open (4 on `Scout`, 13 on
`scout-plugin`), **6 of them from external contributors**. Land or close the
external six first; a restructure that invalidates a contributor's branch without
warning is the migration's largest avoidable cost. Post a dated notice on
`scout-plugin` announcing the freeze window and the destination repo.

**Phase 1 — absorb the tree.** On a `migrate/monorepo` branch in `Raven-Scout/Scout`:
1. `git mv` the app tree into `apps/macos/`.
2. `git subtree add --prefix=plugin https://github.com/Raven-Scout/scout-plugin.git main`.
3. Resolve the seven boilerplate collisions: root `README.md` / `CLAUDE.md` /
   `LICENSE` become repo-level; per-subtree `CLAUDE.md` files stay put; `docs/`
   merges by path; `scripts/` splits into the two prefixed release scripts.
4. Set `marketplace.json` `"source": "./plugin"`.
5. Rewrite the absolute `~/scout-app/docs/...` and `~/scout-plugin/...` paths
   that the merged specs and plans cross-reference into repo-relative links.

**Phase 2 — CI and rulesets.** Land the workflows from §8 with path filters.
Update required checks. **Gate: `contract.yml` fails red on the two known drifts.**
That red build is the proof the migration achieved its purpose.

**Phase 3 — reconcile the drifts.** Regenerate both snapshots; let `contract.yml`
go green. Fix the `AppState.swift:383` `scoutctl` path to
`plugin/engine/bin/scoutctl` — now a same-tree path a test can assert. **Gate:
full app + plugin suites green; `scoutctl` resolves to a real file, not `$PATH`
luck.**

**Phase 4 — cut over.** Merge `migrate/monorepo`. Tag `plugin/v0.10.0` and
`app/v0.11.3`. Archive `scout-plugin` with a final release whose notes carry the
marketplace re-add instruction. Update `install.sh`, the org profile README, and
`plugin.json`'s `homepage` / `repository` fields to the new URL.

**Phase 5 — later, separately.** Absorb `scout-iOS-app` into `apps/ios/` by the
same pattern, retiring the third corpus copy and its guard. `scout-android` is a
stub; absorb it when it has content worth a contract.

Rollback (Phases 1–3): abandon the branch. `scout-plugin` is untouched and
authoritative. After Phase 4, rollback means un-archiving `scout-plugin` and
reverting the merge — recoverable, but disruptive to anyone who re-added the
marketplace, which is why Phase 4 is the gate.

## 10. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Existing users' marketplace points at the archived repo and silently stops receiving updates | **High** | GitHub's redirect keeps git operations resolving, but an archived repo receives no new releases. Final `scout-plugin` release + org profile README + `/scout-update` messaging must all carry the re-add instruction. Verify with a clean-machine install before Phase 4. |
| 6 external contributors' branches are invalidated | **High** | Phase 0. Land or close first; announce the freeze with a date. |
| macOS runner minutes consumed by plugin-only PRs | Medium | Path filters (§8), landed in Phase 2 before the first plugin PR arrives. |
| Tag-namespace confusion during the transition | Medium | Prefixed tags from the first monorepo release; historical bare tags stay on the archived repo (§6). |
| Merge conflicts in the seven colliding paths | Low | All boilerplate; resolved by hand in Phase 1 with no semantic content at stake. |
| `CURRENT_PROJECT_VERSION` jumps ~338 | Low | Monotonicity — Sparkle's only requirement — is preserved. Documented in §6. |
| Agents get confused about which subtree they are in | Low | Per-subtree `CLAUDE.md` files retained; root `CLAUDE.md` routes. |

## 11. Open questions

1. **Should app releases ever move into CI?** This design keeps them local (§6,
   §8) because notarization needs a Developer ID keychain identity; a CI path
   would mean holding the cert and an app-specific password as repository
   secrets. Recommendation: stay local through the migration, then revisit
   alongside Scout#74's Sparkle work, which rewrites the signing procedure anyway
   (inside-out signing of nested Sparkle code).

2. **Should `install.sh` fetch the DMG in the same command?** §3 says the
   monorepo makes this honest. It is follow-on work — but it interacts with
   Scout#51 (Mac-app-first onboarding), which inverts the order: app first, then
   assisted engine install. These two want opposite entrypoints and should be
   reconciled in one design rather than built independently.

3. **`plugin/CHANGELOG.md` vs a root `CHANGELOG.md`.** This spec keeps the plugin
   changelog in its subtree and adds a new app changelog beside it, because
   `release-plugin.yml` extracts release notes from the plugin's file by version
   heading and independent cadences make a merged file confusing. Worth a second
   look if a unified user-facing "what's new" surface is wanted later.

4. **Is `apps/macos/BACKLOG.md` still live?** Last modified 2026-06-15; the repo
   now tracks work in GitHub issues. If dead, the migration is a natural moment
   to retire it rather than carry it.

## 12. References

- [`2026-04-24-scout-unification-design.md`](./2026-04-24-scout-unification-design.md) — §8 distribution and update flows, §11 plugin/vault content boundary. This spec revises the repository-topology half of §8.
- [`2026-04-25-scout-event-architecture-design.md`](./2026-04-25-scout-event-architecture-design.md) — v0.5+ trajectory; unaffected by repository layout.
- [Scout#74](https://github.com/Raven-Scout/Scout/pull/74) — in-app updates (Sparkle + plugin detect/hand-off). Source of the installed-plugin-version lookup and the app/plugin channel asymmetry.
- [Scout#51](https://github.com/Raven-Scout/Scout/issues/51) — Mac-app-first onboarding with assisted engine install.
- [scout-plugin#26](https://github.com/Raven-Scout/scout-plugin/issues/26) — `scoutctl bootstrap auto`, a unified install/upgrade entrypoint.
- [scout-plugin#195](https://github.com/Raven-Scout/scout-plugin/issues/195) — `auto_update.enabled` is inert.
- `plugin/engine/scout/scripts/versioning.py` — canonical version in `plugin.json`, three derived files.
- `docs/plans/2026-06-10-batch-1-quick-wins.md` (plugin) — the cross-repo corpus contract as originally specified, including the stale-digest incident that motivated the checksum guards.

# Unified release: one version, one release, the app is the only install

**Date:** 2026-10-05
**Status:** Design approved in conversation (Jordan, 2026-10-05). The written spec is awaiting review.
**Author:** Jordan Burger (brainstormed with Claude)
**Builds on:**
- [`2026-09-03-monorepo-consolidation-design.md`](2026-09-03-monorepo-consolidation-design.md): one repo, `plugin/` + `apps/macos/`;
- [`2026-09-08-app-managed-engine-design.md`](2026-09-08-app-managed-engine-design.md): Parts B and C, in which Scout.app installs and owns the engine;
- [`2026-07-07-in-app-updates-design.md`](2026-07-07-in-app-updates-design.md): Sparkle, Raven-Scout/scout-app-legacy#74.

**Supersedes:** the monorepo spec's "independent, prefixed tags" decision, from v0.15.0 on.

## 1. Intent

Jordan, 2026-10-05: *"I don't want people to have to install two things
separately for it to work — that was the whole point of my previous app/engine
unification work. Installing the app should be the ONLY step for most users. By
installing the app, everything else will happen for them and we can actually
move some of the setup steps into the app itself."*

**Success looks like this:**
- A new Mac user downloads one DMG and opens it. Onboarding ends with a working
  Scout and a first briefing, with no terminal.
- Cutting a version is **one release action**. It produces the DMG with the
  matching plugin inside, and marketplace users get the same version.
- Later versions reach app users by themselves. Sparkle updates the app, and the
  app upgrades the engine and the plugin.

**Assumptions** (Jordan didn't correct them):
- "Most users" means Mac users starting fresh.
- Terminal-only users (Linux, or anyone who prefers it) keep the Claude Code
  marketplace and the `curl … install.sh` path.
- Today's marketplace users keep working unchanged. They are offered Part C's
  one-click migration later.

## 2. Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | plugin v0.14.0 | Shipped 2026-10-05 as `plugin/v0.14.0`, marketplace only, under today's scheme. It and any urgent `plugin/v0.14.x` patches before v0.15.0 are the last prefixed releases. |
| D2 | Versioning | **One version for Scout.** App and plugin share `X.Y.Z`. The first unified release is **v0.15.0**. App and plugin both sit at 0.14.0 now, so nothing is renumbered. |
| D3 | Release pipeline | **One script, one tag, one GitHub release, signed locally** (approach A). CI-signed releases (B) stay a later option, and the outcome for users is the same either way. |
| D4 | Setup | `/scout-setup` retires. The app's onboarding does all of setup. Without the app, `install.sh` does the whole setup as one command. This is a separate spec (§8). |

## 3. Versions and tags

- **One version, `X.Y.Z`.** It lives in
  `plugin/.claude-plugin/plugin.json` (canonical), the root
  `.claude-plugin/marketplace.json`, `plugin/engine/pyproject.toml`,
  `plugin/engine/scout/__init__.py`, and the app's `MARKETING_VERSION`.
  - Today the app's version is derived from tags at release time, and the
    project file holds a stale `0.12.0`.
  - From now on `versioning set` also writes the project file's
    `MARKETING_VERSION`, so Debug builds report the same version.
  - Release builds pass `MARKETING_VERSION` from `plugin.json`.
  - `versioning check` covers all of these, so CI fails on any drift.
- **The build number stays separate** (`CFBundleVersion` =
  `git rev-list --count HEAD`), because Sparkle compares it. It stays monotonic
  across the move: v0.14.0 shipped as build 214 (the app repo's count at
  d5348a4), and `main` counts 651.
- **One tag per release: `vX.Y.Z`.** Bare `v*` tags in this repo are already
  the plugin's series, `v0.4.0`–`v0.13.0`, so they become Scout's.
  - `plugin/v0.14.0`, plus any urgent `plugin/v0.14.x` patch before
    v0.15.0 (D1, §6), are the last prefixed tags.
  - No more `app/v*` or `plugin/v*` tags. The existing ones stay as history:
    `app/v0.1.0`–`app/v0.14.0` and `plugin/v0.14.0`.
  - The root `CLAUDE.md` rule changes from "never push a bare `v*`" to "every
    release is one `vX.Y.Z`; never add prefixed tags".
- **One GitHub release per version.**
  - Body: one set of notes with an **App** section and a **Plugin and engine**
    section, from `apps/macos/CHANGELOG.md` and `plugin/CHANGELOG.md`.
    Wiring the app changelog into releases settles open decision M6.
  - Assets: the DMG, plus `appcast.xml` once Sparkle lands (§5).
  - Marked **Latest**, so `/releases/latest` (website, READMEs, Sparkle's feed)
    is always the current Scout.
- **Marketplace users** read the version from `main`'s root
  `marketplace.json`. That channel doesn't change.

## 4. The release script

One root `scripts/release.sh` replaces `apps/macos/scripts/release-app.sh`,
`plugin/scripts/release-plugin.sh` and `.github/workflows/release-plugin.yml`.

**`release.sh prepare [patch|minor|major|X.Y.Z]`**:
1. Refuse unless the tree is clean, on `main`, and in sync with `origin/main`.
2. Read the current version from `plugin.json` through `versioning check`,
   **not from tags**, so the mixed tag history above is never consulted.
   - Default level: `minor` if any `feat:` subject landed since the previous
     release's commit (any path), otherwise `patch`.
   - The "previous release commit" is the commit of the **highest-versioned**
     tag matching `vX.Y.Z`, `app/vX.Y.Z` or `plugin/vX.Y.Z` (prefix stripped,
     semver order) whose commit is an ancestor of `HEAD`. Pre-release tags
     (`-rc.N`) are ignored. For v0.15.0 that is `plugin/v0.14.0`, or the
     latest `plugin/v0.14.x`.
3. `versioning set NEW`: the four plugin files plus the project file's
   `MARKETING_VERSION`.
4. Promote `## [Unreleased]` to `## [NEW] - <date>` in **both** changelogs,
   with a fresh `[Unreleased]` above. An empty app section is allowed and
   renders as "No app changes".
5. Run the fast local checks: ruff, ruff format, mypy, `versioning check`,
   shellcheck on the release script. Commit `release: vNEW` on
   `release/vNEW`, push, and open the release PR.
6. The PR's four required checks (`app-ci`, `plugin-test`, `plugin-lint`,
   `contract`) run in full. `app-ci` builds the app with the bundled plugin
   (§5) and runs `ScoutTests`.

**`release.sh finalize vX.Y.Z`**, after the release PR merges:
1. Refuse unless `origin/main`'s changelogs have `[X.Y.Z]` and `versioning
   check` on `origin/main` prints `X.Y.Z`. Also refuse if the tag already
   exists, locally or on the remote.
2. `git worktree add` a throwaway checkout of the merge commit. Every build
   input comes from that commit.
3. Build the app there, with `plugin/` bundled from the same commit (§5).
   Sign it with the Developer ID, notarize, staple, and build the DMG.
   - Once Sparkle has merged, the commit carries
     `apps/macos/scripts/sparkle-release.sh` (#318). Signing the app is then
     delegated to it: `preflight "$app"` after the build, and `sign "$app"
     "$ident"` instead of one flat signature, because Sparkle's nested code
     must be signed inside-out to pass notarization.
   - The DMG's own signature stays in `release.sh`. This
   logic moves over from `release-app.sh`, including the `SCScoutPluginFloor`
   stamp, which now always equals the app's own version.
4. Render the release notes (§3). Once Sparkle lands, also call
   `sparkle-release.sh appcast "$dmg" "$tag" "$slug" "$notes"
   "$build/appcast.xml"`, which renders and EdDSA-signs the feed for this DMG
   (§5).
   - With the hook present, a **release** without `appcast.xml` is refused: a
     Latest release without it would 404 every installed copy's update feed.
   - An rc or a dry run only warns.
5. Publish with **one call**:
   `gh release create vX.Y.Z --target <merge sha> --latest --title "Scout X.Y.Z" --notes-file … <DMG> [appcast.xml]`.
   That creates the tag and the release together.
6. Verify: `/releases/latest` is `vX.Y.Z` with the DMG.

**Failure handling.** Nothing is published, and **no tag exists, until the
notarized DMG is in hand**. A failure anywhere in steps 1–5 leaves the remote
untouched. The fix is to re-run `finalize`.

This replaces today's `release-plugin.sh --finalize`, which pushes the tag
first and leaves publishing to a workflow that can fail after the tag is
already public.

**Who runs it.** Jordan, on his Mac, because signing needs the Developer ID
identity and the `scout-notary` profile.
- `SKIP_RELEASE=1` makes `prepare` stop after the local commit, with no push
  and no PR. It makes `finalize` stop before `gh release create`. Combined
  with `SKIP_NOTARIZE=1`, that is a full dry run that changes nothing outside
  the machine.
- Agents never run `release.sh` for real unless Jordan asks for that run.

**Marketplace timing.** Marketplace users see `X.Y.Z` as soon as the release PR
merges, because they read `main`'s manifest. App users see it once `finalize`
publishes. That gap is usually minutes, and it's accepted.

## 5. How the app delivers and updates everything

**Delivery is Part C, simplified by the monorepo.**
- **At build time:** `bundle-engine.sh` archives `plugin/` from the build's
  own commit (`git archive HEAD:plugin`, deterministic, no network) into the
  app's `Resources/`.
  - `engine-release.json` is generated from `plugin.json` at build time, not
    hand-pinned, so nothing needs bumping.
  - `EngineReleaseTests` asserts that the bundled manifest equals the app's
    version (D2).
- **First launch:** onboarding runs in this order:
  - check Claude Code is installed and signed in;
  - install uv (pinned, checksum-verified) and check git;
  - unpack the plugin to `~/.local/share/scout/engine/<version>`, with
    `current` pointing at it;
  - build the venv at `~/.local/share/scout/venv/<version>`;
  - register a **`scout-plugin` directory marketplace** at `engine/current`
    and install `scout@scout-plugin`;
  - run `scoutctl bootstrap auto`;
  - write the engine pointer with `managed_by: scout-app`.
- **Every launch:** if the bundled version is newer than the installed one,
  `EngineUpgrader` installs it alongside the old version.
  - `bootstrap upgrade` is the atomic switch.
  - It then runs `claude plugin marketplace update scout-plugin` and
    `claude plugin update scout@scout-plugin`.
  - It keeps one previous version on disk.

**Updates go through Sparkle (#74), with one change.**
- The app updates itself through Sparkle. The feed URL (`SUFeedURL`) is
  `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml`.
  - It is **a release asset that `finalize` attaches**, not #74's commit to
    `main`. `main` is ruleset-protected and accepts only PRs.
  - This works because every release is now the Latest, app-carrying one (D2).
- The update chain: Sparkle installs the new app, the app relaunches,
  `EngineUpgrader` installs the bundled plugin and engine, and Claude Code
  picks up the new version. No terminal is involved.
- #74's other parts stand: `UpdateService`, Settings ▸ Updates, and the badge.
  - #74's plugin row ("copy `/scout-update`") becomes the path only for
    engines the app doesn't manage: dev checkouts, and GitHub-marketplace
    installs that haven't migrated. App-managed engines never show it.
- Release candidates: `vX.Y.Z-rc.N` is published as a GitHub **pre-release**
  with its own appcast. An installed build is pointed at it with #74's
  `SCOUT_APPCAST_URL` hook. It's never Latest, so users' feeds never see it.
- **One-time setup only Jordan can do:** generate the EdDSA key pair (Sparkle's
  `generate_keys`). The public key goes into the app's `SUPublicEDKey`, and the
  private key stays in his login keychain beside the Developer ID.

**Existing users are never forced.** These rows are Part C's §10 table,
unchanged.

| Install found | What the app does |
|---|---|
| none | onboarding, fresh install |
| `managed_by: scout-app` | normal; upgrades from the bundle |
| a GitHub-marketplace install | adopt read-only; later Part C's one-click "Migrate to app-managed" |
| a dev checkout (`managed_by: dev`) | adopt read-only, never modify |

GitHub-marketplace and dev-checkout installs keep updating through the
marketplace, on the same version numbers (D2).

## 6. Order of work, and the bar for v0.15.0

**These can start now, in parallel:**

1. **This spec's pipeline.**
   - `release.sh`, plus a bash test of its preflight logic.
   - `versioning` covers `MARKETING_VERSION`.
   - The `contract` job checks that the app and plugin versions match.
   - The combined release notes.
   - The `CLAUDE.md` tag rule.
   - `release-app.sh`, `release-plugin.sh` and `release-plugin.yml` are
     deleted.
   - The runbook's Phase 7 gets rewritten.
   - It doesn't depend on Parts B and C.
2. **Parts B and C**, owned by their session.
   - Part B is re-opened from legacy #125 and merged.
   - Then Part C is rebased from legacy #128, with §5's bundling change and a
     generated `engine-release.json`.
3. **Sparkle (#74), in a new implementation session.** The appcast becomes a
   release asset that `finalize` signs.
4. **The setup-retirement spec (§8).** It also unblocks #261's paused
   connector-setup step.

**The bar for v0.15.0, the first one-download release:**
- Parts B and C merged, Sparkle merged, `release.sh` merged.
- A dry run (`SKIP_NOTARIZE=1 SKIP_RELEASE=1 release.sh prepare minor`, then
  `finalize` on that local commit) passes on `main`.
- **Acceptance test (Part C's C10).** `finalize` has no draft mode, so it's
  rehearsed with release candidates (agreed with #318):
  - publish `rc v0.15.0-rc.1`;
  - on a fresh macOS user account, install its DMG and complete onboarding
    through a first briefing, with no terminal;
  - publish `rc v0.15.0-rc.2` from a **later commit**, so its build number,
    which Sparkle compares, is higher;
  - point the rc.1 install at rc.2's appcast through `SCOUT_APPCAST_URL`, and
    check that Sparkle updates the app and the engine upgrades itself;
  - check that the vault's runs keep working;
  - then `finalize v0.15.0`, and repeat the update check with
    `v0.15.1-rc.1` afterwards.
- Retiring `/scout-setup` is **not** required. The app's onboarding already
  makes the app the only step for new Mac users. The retirement ships in
  v0.15.0 if it's ready, otherwise v0.16.0.

**Between now and v0.15.0:**
- `release-plugin.sh` stays, only for urgent v0.14.x plugin patches to the
  marketplace.
- No app build ships until Part B merges. That rule already exists.
- From v0.15.0 on, `release.sh` is the only way to release.

## 7. Testing

- **`versioning`:** unit tests for `set` and `check` over all five files,
  using a fixture copy of the project file. Drift in any file fails, and so
  does a project file with two different `MARKETING_VERSION` values.
- **`release.sh`:** bash tests for the refusals (dirty tree, not on `main`,
  out of sync, tag exists, changelog missing `[X.Y.Z]`). Also a test that
  publishing is one `gh release create … --target` call made after the
  notarization step, run against a stubbed `gh`/`xcrun` on `PATH`. No test
  ever runs the real signing.
- **Release notes:** golden-file tests of the combined body (both sections,
  an empty app section, and the link rewriting).
- **`contract`:** a step asserting that `plugin.json`'s version equals the
  project file's `MARKETING_VERSION`.
- **Bundling** (with Part C): `EngineReleaseTests` asserts that the bundled
  `plugin.json` version equals the app's version.
- **End to end:** the C10 acceptance test in §6.

## 8. Separate spec: retiring `/scout-setup`

Decided here (D4), designed separately, next:
- The app's onboarding takes over everything `/scout-setup` does: identity,
  vault, connectors, schedule and budget defaults, first run.
- `install.sh` becomes the complete terminal installer. It asks the same few
  questions in the shell, then runs `scoutctl bootstrap install`.
- `/scout-setup` shrinks to a pointer to the app or `install.sh`.
- Custom connectors (Raven-Scout/Scout#261) paused its plan's Task 9 (a new
  `/scout-setup` step and `/scout-connect`) until that spec says where
  connector setup lives.

## 9. Out of scope

- CI-signed releases (D3, approach B). They would need the Developer ID
  certificate and a notary key in Actions secrets. They can follow later
  without changing anything users see.
- Making the marketplace read the latest release instead of `main`. That
  would close the minutes-long gap in §4.
- The iOS and Android apps. They join the monorepo in later phases (monorepo
  spec), and the version rule will apply to them then.
- Linux packaging beyond `install.sh`.

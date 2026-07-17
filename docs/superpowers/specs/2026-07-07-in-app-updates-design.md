# In-app updates — app binary (Sparkle) + plugin (detect & hand off)

**Date:** 2026-07-07
**Status:** design approved; ready for implementation plan.

## Summary

Give Scout.app a single in-app update surface that tracks **two independent
things** and tells you (and, for the app, acts) when either is behind:

1. **The Scout.app binary** — shipped as a signed + notarized DMG on GitHub
   Releases (`Raven-Scout/Scout`). Today updating means manually visiting the
   Releases page, downloading the DMG, and dragging to `/Applications`. New:
   **Sparkle** detects a newer version from an appcast feed, then downloads,
   installs in place, and relaunches — the standard non-App-Store Mac flow.
2. **The scout-plugin** — the Claude Code plugin doing the work in `~/Scout/`
   (`installed 0.7.2`, repo `0.7.3` at time of writing). The app **cannot apply
   a plugin update itself** (that happens inside Claude Code via `/scout-update`
   or the `/plugin` UI), so this track is **detect + notify + hand off**: show
   installed → latest, and copy `/scout-update` to the clipboard for you to run.

The asymmetry is the whole point: the app track self-installs; the plugin track
can only surface the gap and hand you the command.

Both tracks feed one observable `UpdateService`, rendered as a **Settings ▸
Updates** section plus a **badge** on the sidebar and menu-bar icon when an
update is available.

## Decisions (from brainstorm)

- **Scope:** both the app binary and the plugin, in one unified surface.
- **App-binary mechanism:** **Sparkle** (full auto-download-install-relaunch),
  not a browser hand-off. The app is **not sandboxed** (no entitlements file, no
  `com.apple.security.*` keys), so the simple Sparkle path applies — no XPC
  sandbox entitlements. SPM is already used (`Grape`), so Sparkle is added the
  same way.
- **Appcast hosting:** a **checked-in `appcast.xml`** served from
  `https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml`. No
  Pages/host to stand up; `release.sh` writes and pushes it. (Trade-off: raw has
  a ~5-min CDN cache — acceptable for a low-frequency desktop app.)
- **UI surface:** Settings ▸ Updates section **plus** a badge (dot/pill) on the
  sidebar and menu-bar icon when either track has an update — so it reads as a
  notification, not just an on-demand check.
- **Plugin "apply":** copy `/scout-update` to the clipboard + a one-line
  instruction. No attempt to drive a Claude Code slash command from the app
  (there is no such interface).
- **Plugin "latest" is source-aware:** end users install the plugin from GitHub;
  this dev machine's `scout-plugin` marketplace source is a **local directory**.
  Detection reads the marketplace source and resolves latest accordingly.

## Architecture

### Shared — `UpdateService` (observable)

One `@Observable` (or `ObservableObject`, matching `AppState`'s existing idiom)
owning:

```
struct UpdateStatus {
    let currentVersion: String?      // installed / running
    var latestVersion: String?       // nil until known
    var state: State                 // .idle .checking .upToDate .available .error
}
enum Track { case app, plugin }
```

- `appUpdate: UpdateStatus` — mirrored from Sparkle (see below), not from a
  separate GitHub call.
- `pluginUpdate: UpdateStatus` — computed by `PluginUpdateChecker`.
- `anyUpdateAvailable: Bool` — drives the badge.
- `check(_ track:)` / `checkAll()` — manual triggers for the Settings buttons.

All network + file parsing runs on a background task; results hop to
`@MainActor` before mutating published state (consistent with the repo's
main-actor-isolation discipline — see the WriteOp isolation note in project
memory; keep the mutation site `@MainActor`).

### App track — Sparkle

- Add **Sparkle** as an SPM package dependency on the Scout target.
- Own it via `SPUStandardUpdaterController` (starts the updater, provides the
  standard user driver / update dialog) constructed in `ScoutApp`.
- `Info.plist` keys:
  - `SUFeedURL` = `https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml`
  - `SUPublicEDKey` = the EdDSA public key (from one-time `generate_keys`)
  - `SUEnableAutomaticChecks` = `YES`
  - `SUScheduledCheckInterval` = `86400` (daily)
- Enclosure = the **existing notarized DMG** (Sparkle mounts + installs a DMG;
  no new artifact type).
- **`SparkleUpdaterDelegate`** (thin): implements the updater delegate to catch
  "valid update found" / "no update found" / "check failed" and the current
  version, and maps them onto `appUpdate: UpdateStatus`. This keeps the unified
  UI and badge in sync with what Sparkle already knows — **no duplicate GitHub
  API poll for the app.**
- A **"Check for Updates…"** menu command (standard app-menu item) and a Settings
  button both call `updater.checkForUpdates()`.

### Plugin track — `PluginUpdateChecker`

**Installed version** — authoritative source is
`~/.claude/plugins/installed_plugins.json`:

```
plugins["scout@scout-plugin"][0].version   // e.g. "0.7.2"
```

(also carries `installPath`, `gitCommitSha`, `lastUpdated`). Fallback if the
entry is absent/malformed: the newest semver dir under
`~/.claude/plugins/cache/scout-plugin/scout/`. Absent/unparseable →
`currentVersion = nil`, plugin row hidden; never a crash.

**Latest version — source-aware.** Read the `scout-plugin` marketplace entry in
`~/.claude/plugins/known_marketplaces.json` → `.source`:

- `source == "github"` (`{repo: "<org>/scout-plugin"}`) or `"git"` (`{url}`):
  fetch `.claude-plugin/plugin.json` from the remote **default branch** via
  `https://raw.githubusercontent.com/<org>/scout-plugin/<branch>/.claude-plugin/plugin.json`
  (public repo, no auth), read `.version`.
- `source == "directory"` (`{path}`): read `<path>/.claude-plugin/plugin.json`
  locally. (Correct behavior for dev checkouts — "latest" = the working copy.)

**Compare** with a small `SemVer` value type (`major.minor.patch`, optional
pre-release, ignore build metadata). `installed < latest` → `state = .available`.

**Apply (hand off):** primary action copies `/scout-update` to the clipboard and
shows a one-line "paste this into Claude Code" hint; secondary "What's new" link
opens the plugin `CHANGELOG.md` on GitHub. No auto-apply.

### UI

- **`UpdatesSettingsSection`** inside the existing `SettingsView`: two rows
  (App, Plugin), each showing `current → latest`, a state chip
  (`Up to date` / `Update available` / `Checking…` / `Couldn't check`), a
  primary action (App: **Install…** → Sparkle; Plugin: **Copy `/scout-update`**),
  and a manual **Check now**. Hidden rows collapse gracefully when a version is
  unknown.
- **Badge** — a small dot/pill on the `SidebarView` Settings entry and on the
  menu-bar icon (`MenuBarIcon`/`MenuBarExtraContent`) bound to
  `updateService.anyUpdateAvailable`. Sparkle additionally shows its own
  "update available" dialog on scheduled checks; the badge covers the plugin
  track and the between-checks app state.

### Data flow

```
launch / manual "Check now"
      │
      ├─ Sparkle scheduled check ──► delegate ──► appUpdate ─┐
      │                                                       ├─► anyUpdateAvailable ─► badge
      └─ PluginUpdateChecker.check() ─► pluginUpdate ────────┘
                                                              └─► Settings ▸ Updates rows
```

- App track: Sparkle drives checks (launch-ish + daily) and the install; we only
  observe.
- Plugin track: check **on launch** + **manual**; cache the last result. No
  background timer (plugin updates aren't time-critical; keeps it cheap).

### Error handling

- Offline / GitHub unreachable / raw fetch fails → `state = .error`, **silent**
  (no notification, no dialog); visible only in the Settings section. Never
  blocks the app.
- Malformed / missing `installed_plugins.json` or `known_marketplaces.json`, or
  no scout entry → plugin `currentVersion`/`latestVersion` = nil, row hidden,
  app track unaffected.
- Sparkle download/install/signature failures surface through Sparkle's own
  dialogs (its user driver).

## Release-infra changes (`scripts/release.sh`)

This is the part with real teeth — Sparkle changes the signing topology.

1. **One-time key setup.** Run Sparkle's `generate_keys` → EdDSA keypair. Public
   key → `Info.plist` `SUPublicEDKey`. Private key stays in the **local
   Keychain** (same trust model as the existing "Developer ID Application"
   identity — releases are cut locally, so no CI secret needed).
2. **Signing must sign nested code.** Today the script signs only the flat
   `.app` (its comment: "no nested frameworks/helpers… no `--deep`"). Sparkle
   adds nested code — `Sparkle.framework`, `Autoupdate`, `Updater.app`, and XPC
   services — that **must be signed inside-out** with Developer ID + hardened
   runtime **before** the outer `.app`, then notarized as a whole. This is a
   required change, not cosmetic; get the order right or notarization rejects.
3. **Per release, after the DMG is notarized + stapled:**
   - `sign_update Scout-<version>.dmg` → `sparkle:edSignature` + length.
   - Append/regenerate an `appcast.xml` `<item>`: `sparkle:version`
     (= `CURRENT_PROJECT_VERSION`, the commit-count build number),
     `sparkle:shortVersionString` (= `MARKETING_VERSION`), `<enclosure url>` =
     the GitHub release **DMG download URL**, `sparkle:edSignature`, length, and
     release notes (reuse the auto-generated `What's changed` block).
   - `git add appcast.xml && git commit && git push` to `main` (that's the feed
     URL). Keep the DMG on the GitHub release as today.

`appcast.xml` versioning uses the same `MARKETING_VERSION` / commit-count
`CURRENT_PROJECT_VERSION` the script already stamps, so the feed and the About
panel stay consistent.

## Testing (TDD)

Network + filesystem behind protocols so tests never touch GitHub or the real
`~/.claude`:

- **`SemVer`** comparator: `0.7.2 < 0.7.3`, equality, `1.0.0 > 0.9.9`,
  pre-release ordering (`1.0.0-rc.1 < 1.0.0`), malformed input → parse failure
  (not a crash, not a false "up to date").
- **Installed-version parser** against an **anonymized** `installed_plugins.json`
  fixture: reads `plugins["scout@scout-plugin"][0].version`; missing key /
  empty array / bad JSON → nil.
- **Latest-version resolver**: `github` source → builds the correct raw URL and
  parses `.version` from a `plugin.json` fixture; `directory` source → reads a
  local fixture path; unknown source → nil.
- **`UpdateService`** state transitions with a fake fetcher + fake Sparkle
  updater: idle → checking → available / upToDate / error; `anyUpdateAvailable`
  reflects either track.
- **Sparkle wrapper** kept thin; test only the delegate → `UpdateStatus`
  mapping with a fake updater (Sparkle's own machinery is not unit-tested).
- UI layout not unit-tested (consistent with the rest of the suite); verified by
  build + rendering the Settings section and the badge.

Any new `.swift` files under `Scout/` and `ScoutTests/` auto-compile (synchronized
file groups — no `.pbxproj` edits); the SPM Sparkle dependency **does** require a
`.pbxproj` package reference (same as the existing `Grape` reference).

## Out of scope (deferred)

- **Delta updates** (Sparkle's binary deltas) — full-DMG updates only for v1;
  deltas are an optimization once release cadence justifies it.
- **In-app changelog rendering** — link out to GitHub Releases / plugin
  `CHANGELOG.md`; no in-app markdown changelog viewer.
- **Auto-applying plugin updates** — blocked by there being no app→Claude-Code
  slash-command interface; revisit if `scoutctl` gains a headless
  `plugin update` path.
- **Rollback / channel selection (beta vs stable)** — single stable channel for
  v1.

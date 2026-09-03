# In-App Auto-Update (Sparkle) — Design

**Date:** 2026-09-02
**Status:** Proposed (for review)
**Surface:** App menu, menu-bar extra, Settings → Updates, `scripts/release.sh`

## Context

Scout ships as a Developer-ID-signed, notarized DMG attached to a GitHub
Release. Getting a new version today means: notice that a release happened
(nothing tells you), open the Releases page, download the DMG, drag over the
old app. Releases land several times a week, so most installs are stale most
of the time — the `/Applications/Scout.app` on the release machine reads
`0.8.4` while the latest release is `0.11.2`.

What exists and constrains the design:

- **Release pipeline is local.** `scripts/release.sh` builds a universal
  Release app, signs it (Developer ID + hardened runtime), notarizes and
  staples the app *and* the DMG, then `gh release create`s the tag with the
  DMG attached. There are no CI secrets; CI only runs tests.
- **Version stamping already fits an updater.** `MARKETING_VERSION` is the
  release version and `CURRENT_PROJECT_VERSION` is `git rev-list --count
  HEAD` — a monotonic integer, exactly what a machine-readable build number
  should be.
- **App shape.** SwiftUI `App` with a `WindowGroup`, a `MenuBarExtra`
  (`.menu` style), and a `Settings` scene. Not sandboxed, hardened runtime
  on. One SPM dependency (Grape, source-based, statically linked). Debug and
  Release use different bundle IDs (`com.scout.Scout.dev` / `com.scout.Scout`)
  so both can run side by side.
- **Info.plist is generated** (`GENERATE_INFOPLIST_FILE = YES`, no plist
  file on disk).
- **Repo is public** at `Raven-Scout/Scout`; GitHub Pages is not enabled.

## Goal

Scout keeps itself up to date. A running Scout notices a new release within
hours, downloads it in the background, and offers a one-click **Install and
Relaunch**. Users can also check on demand from the app menu, the menu-bar
extra, or Settings. The release script produces everything the updater needs
as part of the existing one-command release — no extra manual steps per
release beyond what exists today.

## Approaches considered

1. **Sparkle 2 (recommended).** The standard macOS updater framework:
   EdDSA-signed appcast, background download, Developer ID continuity checks,
   DMG support, install-and-relaunch, standard UI. Mature, actively
   maintained (2.9.6, Aug 2026), SPM-installable.
2. **Hand-rolled updater.** Poll the GitHub Releases API, download the DMG,
   mount, copy, relaunch. Re-implements the hard parts (atomic replace of a
   running bundle, signature verification, relaunch, progress UI, edge cases
   like the app living somewhere other than `/Applications`) with none of the
   battle-testing. Rejected.
3. **Notify only.** Check the Releases API and show "0.12.0 is available —
   open Releases page." Removes the "no one knows" half of the problem but
   keeps the download-and-drag half. Rejected; it is not meaningfully cheaper
   than Sparkle once we have to do a network check and UI anyway.

Sparkle it is. The remaining decisions are about *where the appcast lives*,
*how the release script feeds it*, and *how the app exposes it*.

## Design

### 1. Appcast hosting: a GitHub Release asset behind the `latest` redirect

Each release uploads two assets: `Scout-<version>.dmg` (as today) and
`appcast.xml`. The app's feed URL is the stable redirect

```
https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml
```

GitHub answers that with a `302` to the asset of the newest non-prerelease,
non-draft release (verified today against the DMG: `302 →
/releases/download/v0.11.2/Scout-0.11.2.dmg`). Sparkle follows redirects.

Why this over the alternatives:

- **GitHub Pages / `gh-pages` branch** — needs Pages enabled and a commit per
  release to a second branch; more moving parts for no benefit.
- **`appcast.xml` committed to `main`, served from `raw.githubusercontent.com`**
  — the release script would have to commit and push to `main` after every
  release, which fights branch protection and produces noise commits.
- **Release asset (chosen)** — the appcast lives next to the DMG it describes,
  is produced by the same `gh release create` call that already runs, and is
  self-healing: if a bad release is deleted, `latest` falls back to the
  previous release whose appcast points at itself.

The appcast contains **one `<item>`** — the release it ships with. Sparkle
compares that item's `sparkle:version` against the running
`CFBundleVersion`; anyone on any older build is offered the latest directly.
There is no need to carry history.

```xml
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Scout</title>
    <link>https://github.com/Raven-Scout/Scout/releases</link>
    <item>
      <title>Scout 0.12.0</title>
      <link>https://github.com/Raven-Scout/Scout/releases/tag/v0.12.0</link>
      <sparkle:version>412</sparkle:version>
      <sparkle:shortVersionString>0.12.0</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>15.7</sparkle:minimumSystemVersion>
      <sparkle:fullReleaseNotesLink>https://github.com/Raven-Scout/Scout/releases/tag/v0.12.0</sparkle:fullReleaseNotesLink>
      <pubDate>Wed, 02 Sep 2026 20:15:00 +0000</pubDate>
      <description><![CDATA[
        <h2>What's changed</h2>
        <h3>Features</h3>
        <ul><li>feat(updates): in-app auto-update via Sparkle (<code>ab12cd3</code>)</li></ul>
      ]]></description>
      <enclosure url="https://github.com/Raven-Scout/Scout/releases/download/v0.12.0/Scout-0.12.0.dmg"
                 sparkle:edSignature="…base64…"
                 length="9012345"
                 type="application/octet-stream"/>
    </item>
  </channel>
</rss>
```

- `sparkle:version` = `CURRENT_PROJECT_VERSION` = commit count (what Sparkle
  compares). `sparkle:shortVersionString` = the release version (what users
  see).
- `sparkle:minimumSystemVersion` is read from the built app's
  `LSMinimumSystemVersion` so it cannot drift from the deployment target.
- `<description>` is the same "What's changed" section the GitHub release
  notes get, emitted as HTML (not converted from Markdown — see §4).
- `sparkle:edSignature` / `length` come from Sparkle's `sign_update` run on
  the **final, stapled** DMG.

### 2. Signing key (EdDSA)

Sparkle 2 requires every enclosure to carry an ed25519 signature made with a
private key that never leaves the release machine. Setup is a one-time step,
performed by Jordan (not by an agent — this key is the root of trust for every
future update):

```bash
# from the resolved SPM artifacts, see §5 for the path
generate_keys                       # creates the key in the login keychain, prints the public key
generate_keys -x ~/scout-sparkle-ed25519.key   # export → store in a password manager, then delete the file
```

- The private key is a generic-password item (service
  `https://sparkle-project.org`, account `ed25519`) in the login keychain of
  the machine that runs `scripts/release.sh` — the same place the
  `scout-notary` notarytool profile already lives.
- The public key is committed as `SUPublicEDKey` (see §3).
- **Losing the private key strands every installed copy** (Sparkle will
  refuse anything signed with a different key; the only recovery is a manual
  DMG install for everyone). Hence the export-and-store step is mandatory,
  not optional.
- Trust is layered: Sparkle also checks that the downloaded app is signed by
  the **same Developer ID team** as the running app, and both the feed and
  the download are HTTPS.

### 3. App integration

#### Dependency and Info.plist

- Add the Sparkle package (`https://github.com/sparkle-project/Sparkle`,
  product `Sparkle`) to the `Scout` target, pinned **exactly** to `2.9.6`.
  Grape uses `upToNextMajor`, but the release script signs Sparkle's nested
  executables by path, so Sparkle upgrades should be deliberate.
- Add a typed `Scout/Info.plist` with only the Sparkle keys and point
  `INFOPLIST_FILE` at it on both configurations. Xcode merges it with the
  generated plist (`GENERATE_INFOPLIST_FILE` stays `YES`), and a real plist
  guarantees the booleans and the number are typed rather than strings.

  | Key | Value | Why |
  | --- | --- | --- |
  | `SUFeedURL` | `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml` | §1 |
  | `SUPublicEDKey` | base64 public key from `generate_keys` | §2 |
  | `SUEnableAutomaticChecks` | `true` | Skip Sparkle's second-launch "check automatically?" prompt; checks are on by default |
  | `SUAutomaticallyUpdate` | `true` | Download in the background by default; the user still confirms the relaunch |
  | `SUScheduledCheckInterval` | `21600` | Check every 6 h (Sparkle minimum is 1 h; default is 24 h). Releases land several times a week, and the check is one small redirected GET |

  Both configurations get the same plist. Debug builds carry the keys but
  never start the updater (below), so nothing happens.

#### `AppUpdater` (Scout/Services/AppUpdater.swift)

One `@MainActor final class AppUpdater: ObservableObject` wraps
`SPUStandardUpdaterController` so the rest of the app never imports Sparkle:

```swift
@MainActor final class AppUpdater: ObservableObject {
    /// False in Debug builds: the controller exists but is never started.
    let isEnabled: Bool
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var lastUpdateCheckDate: Date?
    var automaticallyChecksForUpdates: Bool { get set }   // → SPUUpdater
    var automaticallyDownloadsUpdates: Bool { get set }   // → SPUUpdater
    func checkForUpdates()                                 // no-op when disabled
}
```

- The controller is always constructed with `startingUpdater: false`, and
  `startUpdater()` is called only when `isEnabled`. `isEnabled` is a
  compile-time constant (`false` under `DEBUG`). **All Sparkle code compiles
  in both configurations** — CI builds Debug, so a Release-only `#if` block
  would be the one thing CI could never catch.
- Why dev builds don't update: a dev build lives in DerivedData under
  `com.scout.Scout.dev`; letting it swap itself for the release
  `com.scout.Scout` bundle would be confusing at best and fail Sparkle's
  bundle checks at worst. The Settings UI says so plainly.
- `canCheckForUpdates` and `lastUpdateCheckDate` are mirrored from the
  updater via KVO publishers (`updater.publisher(for: \.canCheckForUpdates)`),
  the pattern Sparkle's own SwiftUI example uses.
- The updater delegate (`NSObject, SPUUpdaterDelegate`) implements exactly one
  method, `feedURLString(for:)`, which returns the value of the
  `SCOUT_APPCAST_URL` environment variable when it is a well-formed `https`
  URL and `nil` otherwise (falling back to `SUFeedURL`). This is the
  sanctioned Sparkle 2 override point and exists so a release-candidate
  build can be pointed at a pre-release appcast for end-to-end testing
  (§6). It is process-scoped (an env var, not a persisted default) and cannot
  weaken security: a hostile feed still cannot produce a valid EdDSA
  signature or a Developer-ID-matching bundle. The parsing lives in a pure
  `UpdateFeed.overrideURLString(environment:)` so it is unit-testable.

#### Surfaces

- **App menu** — `CommandGroup(after: .appInfo)` adds **Check for Updates…**
  directly under *About Scout*, disabled while `canCheckForUpdates` is false
  (a check is already running, or the updater is disabled).
- **Menu-bar extra** — the same **Check for Updates…** item, placed after
  *Open Scout folder in Finder*. Shown only when `isEnabled`, so dev builds
  don't grow a dead menu item.
- **Settings → Updates** (new section between *Notifications* and *About*):
  - *Check for updates automatically* — toggle bound to
    `automaticallyChecksForUpdates`.
  - *Download and install automatically* — toggle bound to
    `automaticallyDownloadsUpdates`; help text: "Updates download in the
    background. Scout always asks before relaunching."
  - *Check now* — button, plus "Last checked: <relative date>" or "Never".
  - In dev builds the section shows one row: "Automatic updates are disabled
    in development builds."
- Ownership: `ScoutApp` holds `@StateObject private var updater = AppUpdater()`
  and injects it as an environment object alongside `AppState`. `AppState`
  is untouched.

### 4. Release script (`scripts/release.sh`)

The script keeps its shape and one-command usage. Changes, in pipeline order:

1. **Tag selection ignores pre-release tags.** `LATEST_TAG` / `PREV_TAG` only
   consider plain `vMAJOR.MINOR.PATCH` tags, so an `rc` tag never skews the
   version rule or the changelog range.
2. **Version validation + `PRERELEASE=1`.** `VERSION` must match
   `^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$`. A suffixed version (e.g.
   `0.12.0-rc.1`) is only accepted with `PRERELEASE=1`, and `PRERELEASE=1`
   requires an explicit version. The release is created with `--prerelease`,
   which GitHub excludes from `releases/latest` — production users never see
   it.
3. **Monotonic build number guard.** Fail if `BUILD_NUMBER` is not strictly
   greater than `git rev-list --count <LATEST_TAG>`. Two releases cut from the
   same commit would share a `CFBundleVersion`, and Sparkle would never offer
   the second one.
4. **Locate Sparkle's tools** in the build's own SPM artifacts:
   `$BUILD_DIR/SourcePackages/artifacts/sparkle/Sparkle/bin/` (found with
   `find`, not a hardcoded path). Fail fast if missing.
5. **Key preflight.** `generate_keys -p` (public key in the keychain) must
   equal `SUPublicEDKey` in the built `Info.plist`, and `SUFeedURL` must be
   present. A mismatch would ship an app that can never verify an update, so
   it fails the build before notarization.
6. **Sign inside-out.** The bundle now has nested executables. Before signing
   the app, sign each Sparkle component with the same identity and hardened
   runtime, in the order Sparkle documents:

   ```
   Sparkle.framework/Versions/B/XPCServices/Installer.xpc
   Sparkle.framework/Versions/B/XPCServices/Downloader.xpc   (--preserve-metadata=entitlements)
   Sparkle.framework/Versions/B/Autoupdate
   Sparkle.framework/Versions/B/Updater.app
   Sparkle.framework
   Scout.app                                                 (as today)
   ```

   The script resolves each component under
   `Scout.app/Contents/Frameworks/Sparkle.framework` and fails if any expected
   component is missing (a Sparkle upgrade that moved things would stop the
   release rather than ship an unsigned helper). `--deep` stays out (Apple and
   Sparkle both advise against it). The stale comment claiming the bundle has
   no nested code goes away. Verification adds `codesign --verify --strict
   --deep` and the existing `spctl --assess`.
7. **EdDSA-sign the final DMG.** After the DMG is notarized and **stapled**
   (stapling changes the bytes), run `sign_update "$DMG"` and parse
   `sparkle:edSignature` and `length`.
8. **Render release notes once, in two formats.** The commit grouping
   (feat / fix / other) already exists; it is refactored into a function that
   emits either the Markdown used for the GitHub release or an HTML fragment
   for the appcast `<description>` (`<h2>/<h3>/<ul><li>/<code>`, with `& < >`
   escaped). Emitting HTML directly avoids a Markdown converter and keeps the
   two in lock-step.
9. **Generate `appcast.xml`** with `scripts/appcast.sh`, a small pure helper
   (arguments in, XML on stdout) so it can be smoke-tested in isolation. The
   release script validates the output with `xmllint --noout`.
10. **Upload both assets:** `gh release create "$TAG" "$DMG" "$APPCAST" …`
    (+ `--prerelease` when set).
11. **Install boilerplate** in the notes gains: "Already on Scout 0.12.0 or
    later? It updates itself — wait for the automatic check or use Scout →
    Check for Updates…"

`SKIP_NOTARIZE=1` and `SKIP_RELEASE=1` keep working; with `SKIP_NOTARIZE=1`
the appcast is still generated (unstapled DMG, signature valid for local
testing).

### 5. Developer setup

- `xcodebuild -resolvePackageDependencies -project Scout.xcodeproj -scheme
  Scout -derivedDataPath build` (or any build) downloads the Sparkle binary
  artifact; the tools then live at
  `build/SourcePackages/artifacts/sparkle/Sparkle/bin/{generate_keys,sign_update,generate_appcast}`.
- README gets: the auto-update note in *Install*; the key setup + backup and
  `PRERELEASE=1` flow in *Cutting a release*.

### 6. Rollout and end-to-end verification

The first Sparkle-enabled release (0.12.0 — `feat:` → minor) is the last one
anyone installs by hand. It is also the one release where a broken updater is
unrecoverable through the updater, so the pipeline is proven on pre-releases
first:

1. `PRERELEASE=1 scripts/release.sh 0.12.0-rc.1` → install the rc.1 app to
   a scratch location (not `/Applications`).
2. Land at least one commit (so the build number grows), then
   `PRERELEASE=1 scripts/release.sh 0.12.0-rc.2`.
3. Launch rc.1 with the feed pointed at rc.2's asset:
   `open -a <rc.1 path> --env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/v0.12.0-rc.2/appcast.xml`,
   then *Check for Updates…* → expect rc.2 offered with release notes →
   *Install and Relaunch* → Settings → About shows `0.12.0-rc.2`.
4. Also confirm: `Contents/Frameworks/Sparkle.framework` present and
   universal (`lipo -info`), `spctl --assess` passes on the notarized app, and
   Console shows no Sparkle signing/validation errors.
5. Delete the two pre-releases and their tags
   (`gh release delete v0.12.0-rc.N --cleanup-tag --yes`), then run the real
   `scripts/release.sh`.

Rollback model: Sparkle never downgrades, so a bad release is fixed **forward**
with a patch release. Deleting a bad release is still safe (the `latest`
redirect falls back), it just doesn't help users who already installed it.

## Error handling

- **Feed unreachable / 404** (e.g. before 0.12.0 has an appcast, or offline):
  background checks fail silently and retry on schedule; user-initiated
  checks show Sparkle's standard error alert.
- **Signature or Developer-ID mismatch:** Sparkle refuses to install and
  reports it. The key preflight in the release script is what makes this
  path unreachable for a healthy release.
- **Release from a stale/equal commit count:** blocked by the build-number
  guard before anything is built.
- **User declines / postpones:** standard Sparkle behavior (remind later,
  install on quit). Nothing is ever installed without an explicit click.
- **Dev builds:** updater never starts; Settings explains why.

## Testing

- **Unit (Swift Testing, `ScoutTests/Services/`):**
  - `UpdateFeedTests` — `overrideURLString(environment:)`: returns the URL for
    a well-formed `https` value; `nil` when the variable is absent, empty,
    `http`, or not a URL.
  - `UpdateInfoPlistTests` — the test host's `Info.plist` has the exact
    `SUFeedURL`, a `SUPublicEDKey` that base64-decodes to 32 bytes,
    `SUEnableAutomaticChecks == true`, `SUAutomaticallyUpdate == true`,
    `SUScheduledCheckInterval == 21600`. Guards against the key or feed being
    dropped or retyped as strings.
  - `AppUpdaterTests` — a disabled updater reports `canCheckForUpdates ==
    false` and `checkForUpdates()` is a no-op; the delegate returns `nil`
    for a non-`https` override.
- **Script:** `scripts/appcast.sh` smoke test — run with dummy arguments,
  `xmllint --noout`, and grep the required fields. Added as a cheap CI step so
  the helper cannot silently break.
- **Manual:** the rc.1 → rc.2 procedure in §6, before 0.12.0.
- Existing suites unchanged and green; CI's Debug test build now downloads
  the Sparkle binary artifact (public, checksummed by SPM).

## Non-goals

- Delta updates, beta channel, release-notes signing (`SURequireSignedFeed`).
- Moving release builds into CI (the Developer ID cert, notary profile, and
  now the EdDSA key all stay on the release machine).
- Sandboxing, App Store distribution, or trimming Sparkle's unused XPC
  services from the bundle.
- Auto-updating the plugin or the iOS app.
- Updating existing installs older than 0.12.0 — they have no updater; the
  release notes tell them to download once more.

## Risks

- **Key loss** is the only unrecoverable failure; mitigated by the mandatory
  export to a password manager.
- **`releases/latest` semantics** — a future non-prerelease "hotfix for an
  old minor" would become `latest` and be offered to everyone. Acceptable for
  a single-line release train; noted in the README.
- **Notarization with nested Sparkle code** is a new path for this script;
  the rc dry-run in §6 exercises it before it matters.
- **Sparkle minor upgrades** could move the nested-component paths; pinning
  to an exact version plus `find`-based signing keeps that a deliberate,
  visible change.

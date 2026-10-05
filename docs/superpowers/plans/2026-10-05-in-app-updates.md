# In-App Updates (Sparkle) for v0.15.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Supersedes:** `docs/superpowers/plans/2026-09-02-in-app-updates.md` (the "old plan"). Where a task below says "old plan Task N, verbatim", copy that code from the old plan and apply only the edits listed. Every path, URL and identifier in this file wins over the old plan.

**Goal:** Scout.app updates itself through Sparkle 2.10.0. It reads a release-asset feed at `releases/latest/download/appcast.xml`, which `release.sh finalize` builds and attaches. For engines the app doesn't manage, the app also detects when the plugin is behind and hands over `/scout-update`. Both tracks feed Settings ▸ Updates and an update badge. This is a hard prerequisite of v0.15.0.

**Architecture:** The old plan's Sparkle-free `UpdateService` state machine, unchanged in shape. The app track is a thin `AppUpdater` adapter around `SPUStandardUpdaterController`, which never starts in Debug builds. The plugin track is now gated on Part B's `EngineState`: `.managed` engines never show a plugin row, and `.external` ones get the old design's detect-and-hand-off. It reuses Part B's `EngineVersion` and `ClaudePluginsRegistry`. Release-time Sparkle work lives in `apps/macos/scripts/sparkle-release.sh` (`preflight`, `sign`, `appcast`), and the root `release.sh` (Raven-Scout/Scout#317, owned by the "Scout monorepo consolidation" session; it carries #314's spec and plan) calls it at three fixed points.

**Tech Stack:** Swift 6.2 (Swift 5 language mode, default MainActor isolation), SwiftUI, Combine, Sparkle 2.10.0, Swift Testing, xcodebuild, `/bin/bash` 3.2-compatible shell.

**Spec:** `docs/superpowers/specs/2026-07-07-in-app-updates-design.md`. Read the whole file, including both amendment sections. **Amendments (2026-10-05)** is the one this plan implements, and Raven-Scout/Scout#314 §5–§6 wins wherever it conflicts.

## Global Constraints

- Repo `Raven-Scout/Scout`. App in `apps/macos/`. Run every app command from `apps/macos/`. Always pass `--repo Raven-Scout/Scout` to `gh`.
- Branch `feat/in-app-updates-sparkle`, base `main`. Rebase onto `origin/main` before each task. Parts B and C keep landing app-startup and engine changes.
- **Do not edit** `Scout/Engine/EngineLocator.swift`, `EnginePointer.swift`, `EngineLayout.swift`, `EngineHealthService.swift`, `ClaudePluginsRegistry.swift`, `EngineVersion.swift`, or any `EngineUpgrader*`. They belong to the Part B/C session. Consume them read-only. If one of them blocks you, stop and report it.
- Sparkle package `https://github.com/sparkle-project/Sparkle`, product `Sparkle`, **exact** `2.10.0`.
- Feed URL, verbatim in Info.plist, tests and docs: `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml`.
- Info.plist (`apps/macos/Scout-Info.plist`, already wired as `INFOPLIST_FILE` for both Scout configurations) gets `SUFeedURL`, `SUPublicEDKey`, `SUEnableAutomaticChecks` = `<true/>` and `SUScheduledCheckInterval` = `<integer>86400</integer>`. There is no `SUAutomaticallyUpdate`. Keep the existing `SCScoutPluginFloor`.
- Feed override env var `SCOUT_APPCAST_URL`, honored only for well-formed `https` URLs.
- Debug builds never start Sparkle. The **only** `#if DEBUG` is `AppUpdater.updatesEnabledForThisBuild`. The plugin track works in Debug.
- Plugin identifiers: `ClaudePluginsRegistry.scoutPluginID` (`scout@scout-plugin`) and `.scoutMarketplaceName` (`scout-plugin`). The marketplace manifest is `.claude-plugin/marketplace.json`, and its plugin entry is the one named `scout`. The hand-off command is `/scout-update`.
- Signing order, never `--deep` (except for `codesign --verify`): `Installer.xpc` → `Downloader.xpc` (`--preserve-metadata=entitlements`) → `Autoupdate` → `Updater.app` → `Sparkle.framework` → `Scout.app`. Each is signed with `--force --options runtime --timestamp`.
- Tests: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteTypeName> -resultBundlePath "$SCRATCH/<name>.xcresult" CODE_SIGNING_ALLOWED=NO 2>&1 | tee "$SCRATCH/<name>.log"`, then `grep -E "TEST (SUCCEEDED|FAILED)|Executed|Test run with" "$SCRATCH/<name>.log"`. Afterwards, `rm -rf "$SCRATCH"/*.xcresult`.
  - `-only-testing` must name a real `@Suite` **type**, or the whole `ScoutTests` target. A type+method or directory selector runs ZERO tests and reports success.
  - Known flakes (DebouncedFileEventsTests, the FS-watcher tests): re-run once before investigating.
- New `.swift` files and fixtures under `Scout/` or `ScoutTests/` auto-compile (synchronized groups). Fixtures land flattened in the bundle, so their names must be unique. The **only** `project.pbxproj` edits are Task 1's.
- `MemberImportVisibility` is on, so test files import what they use (`Foundation`, `Combine`). SourceKit "Cannot find type" / "No such module" diagnostics are false positives; xcodebuild is authoritative.
- Fixtures are anonymized (root and app `CLAUDE.md`): `example-org/…`, `/Users/alex/…`, no real SHAs. `Raven-Scout/Scout` appears only where it is the product's real default (the feed URL).
- Shell: macOS `/bin/bash` 3.2 (no `declare -A`, no `local -n`, no possibly-empty `"${arr[@]}"` under `set -u`). `shellcheck -S warning` stays clean.
- Commits are conventional and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push after every task.
- **Never** run `release.sh`, tag, notarize, or publish a release. **Never** run `generate_keys`, read the private key, or export it. Task 2 is Jordan's.

## File map

| Path (under `apps/macos/` unless rooted) | Responsibility |
| --- | --- |
| `Scout.xcodeproj/project.pbxproj` | Sparkle package reference (Task 1 only) |
| `Scout-Info.plist` | Sparkle keys, typed (Task 3) |
| `Scout/Services/Updates/UpdateService.swift` | `UpdateTrack`, `UpdateStatus`, `AppUpdateEvent`, `AppUpdateController`, `PluginUpdateResult`, `PluginUpdateChecking`, `UpdateService` |
| `Scout/Services/Updates/PluginRelease.swift` | marketplace-manifest reader, latest-manifest and releases-page URL builders |
| `Scout/Services/Updates/PluginUpdateChecker.swift` | `RemoteDataFetcher`, `URLSessionFetcher`, `PluginUpdateChecker` (engine-state gated) |
| `Scout/Services/Updates/UpdateFeed.swift` | `SCOUT_APPCAST_URL` parser |
| `Scout/Services/Updates/AppUpdater.swift` | the only file that imports Sparkle |
| `Scout/Shell/CheckForUpdatesView.swift` | **Check for Updates…** command |
| `Scout/Shell/UpdatesSettingsSection.swift` | Settings ▸ Updates card and rows |
| `Scout/ScoutApp.swift`, `Scout/Shell/SettingsView.swift`, `SidebarView.swift`, `MainWindowView.swift`, `MenuBarIcon.swift`, `MenuBarExtraContent.swift` | wiring and badges |
| `ScoutTests/Services/Updates/*Tests.swift`, `ScoutTests/Fixtures/updates-*.json` | tests and anonymized fixtures |
| `scripts/sparkle-release.sh`, `scripts/tests/sparkle-release.test.sh` | release-time Sparkle steps that `release.sh` calls, plus their bash tests |
| `scripts/release-app.sh` | a guard: refuse to build a Sparkle app (Task 10) |
| `/.github/workflows/app-ci.yml` | runs the sparkle-release bash tests |
| `README.md`, `CHANGELOG.md` | docs |

## Task order and owners

1. Sparkle package. 2. **Jordan: `generate_keys`.** It needs Task 1's build, and Tasks 4–10 don't wait for it. 3. Info.plist keys (needs Task 2's public key). 4. `UpdateService`. 5. `PluginRelease`. 6. `PluginUpdateChecker`. 7. `AppUpdater`, feed override, menu command and wiring. 8. Settings ▸ Updates. 9. Badges and the menu-bar panel. 10. `sparkle-release.sh`, its tests, the CI step and the `release-app.sh` guard. 11. Docs, changelog, and keeping the interface note on #317 current. 12. **Jordan: rc rehearsal**, after merge and once `release.sh` exists.

If Task 2's key hasn't arrived when Task 10 is done, finish Task 11, then do Task 3 last. The PR stays draft until Task 3 is in.

---

### Task 1: Add Sparkle 2.10.0 to the Scout target

**Files:** Modify `Scout.xcodeproj/project.pbxproj`.

This is old plan Task 1 verbatim, with these edits:
- `version = 2.10.0;` in the `XCRemoteSwiftPackageReference "Sparkle"` block.
- The old plan's line numbers are stale. Anchor each insertion on its section comment or on the Grape entry next to it (`BEEE45A0…` build file, `BEEE45A1…` package reference, `BEEE45A2…` product dependency). The Scout target and frameworks-phase IDs (`BEEE4553…`, `BEEE4551…`) are unchanged. Check them with `grep -n 'BEEE4551\|BEEE4553' Scout.xcodeproj/project.pbxproj`.
- Before inserting, confirm the three new IDs `5AC0FFEE2F9599AB0000000{1,2,3}` don't already occur in the file.
- Step 2 builds with `-derivedDataPath build` from `apps/macos/`. `build/` is already ignored; confirm with `git check-ignore -v build/x`. Expected: `Contents/Frameworks/Sparkle.framework`, and `generate_appcast generate_keys sign_update` (plus `BinaryDelta`) in `build/SourcePackages/artifacts/sparkle/Sparkle/bin/`. If the framework isn't embedded, use the old plan's embed-phase fallback.
- Step 3 runs the whole `ScoutTests` target and records the executed count in the task report. Later tasks compare against it.
- Commit: `build(updates): add Sparkle 2.10.0 (exact) as an SPM dependency of the Scout target`.

---

### Task 2: EdDSA key (owner: Jordan)

**Files:** none. The output is the 44-character base64 public key, posted as a PR comment. The public key is public by design.

The implementer **stops** here, gives Jordan these commands, and carries on with Task 4:

```bash
cd ~/.scout-worktrees/scout-in-app-updates/apps/macos   # or any checkout after Task 1's build
SPARKLE_BIN=build/SourcePackages/artifacts/sparkle/Sparkle/bin
"$SPARKLE_BIN/generate_keys"            # stores the private key in the login keychain, prints the public key
"$SPARKLE_BIN/generate_keys" -x ~/Desktop/scout-sparkle-ed25519.key   # backup: save into the password manager, then delete the file
```

If `generate_keys` says a key already exists, print it with `generate_keys -p`. Do **not** make a second one. Losing the private key means every installed copy needs a manual reinstall.

---

### Task 3: Sparkle keys in `Scout-Info.plist` and a contract test

**Files:** Modify `Scout-Info.plist`. Test: `ScoutTests/Services/Updates/UpdateInfoPlistTests.swift` (create). No pbxproj edits: `INFOPLIST_FILE = "Scout-Info.plist"` is already set in both Scout configurations.

- [ ] Step 1. Write the failing test: old plan Task 3 Step 1, verbatim, except that `feedURLIsTheCheckedInAppcastOnMain` becomes `feedURLIsTheLatestReleaseAsset`, expecting `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml`. Add one more test:

```swift
    @Test func pluginFloorStampSurvives() {
        // The Sparkle keys share this plist with Part B's floor stamp.
        #expect(info.keys.contains(EngineVersion.floorInfoKey))
    }
```

- [ ] Step 2. Run `-only-testing:ScoutTests/UpdateInfoPlistTests`. Expected: 4 failures. `noSilentAutoDownload` and `pluginFloorStampSurvives` pass.
- [ ] Step 3. Add the four keys to the existing `<dict>` after `SCScoutPluginFloor`, with the values from Global Constraints and Jordan's public key. Then run `plutil -lint Scout-Info.plist`.
- [ ] Step 4. Re-run: 6 passed. Run `plutil -p build/Build/Products/Debug/Scout.app/Contents/Info.plist | grep -E 'SU[A-Z]|SCScout'` after a `-derivedDataPath build` build. Expected: the booleans and numbers are typed (`1`, `86400`), not the string `"YES"`.
- [ ] Step 5. Commit `feat(updates): Sparkle feed URL, public key and defaults in Scout-Info.plist (+ contract test)`.

---

### Task 4: `UpdateService`, gated on the engine

**Files:** Create `Scout/Services/Updates/UpdateService.swift`. Test: `ScoutTests/Services/Updates/UpdateServiceTests.swift`.

**Interfaces** (the old plan Task 5 interface, with the changes marked):

```swift
enum UpdateTrack: CaseIterable, Sendable { case app, plugin }
struct UpdateStatus: Equatable, Sendable { … }            // unchanged
enum AppUpdateEvent: Equatable, Sendable { … }            // unchanged
@MainActor protocol AppUpdateController: AnyObject { … }  // unchanged
struct PluginUpdateResult: Equatable, Sendable {
    var applicable: Bool          // NEW: false → the plugin row is hidden (managed / not installed / broken engine)
    var installed: String?; var latest: String?; var isUpdateAvailable: Bool
    var releasesURL: URL?         // RENAMED from changelogURL: the source repo's Releases page
    var error: String?
    static let notApplicable: PluginUpdateResult
}
protocol PluginUpdateChecking: Sendable {
    func check(engine: EngineState) async -> PluginUpdateResult   // CHANGED: takes the engine state
}
@MainActor final class UpdateService: ObservableObject {
    static let pluginUpdateCommand = "/scout-update"
    init(pluginChecker: any PluginUpdateChecking,
         engineStates: AnyPublisher<EngineState, Never>,           // NEW: EngineHealthService.$state
         makeAppController: (@escaping @MainActor (AppUpdateEvent) -> Void) -> any AppUpdateController)
    @Published private(set) var appUpdate: UpdateStatus
    @Published private(set) var pluginUpdate: UpdateStatus
    @Published private(set) var pluginRowVisible: Bool            // NEW
    @Published private(set) var pluginReleasesURL: URL?           // RENAMED
    var appUpdatesEnabled: Bool; var anyUpdateAvailable: Bool; var availableCount: Int
    func applyAppEvent(_ event: AppUpdateEvent)
    func checkApp()
    func checkPlugin() async          // uses the latest engine state received
    func check(_ track: UpdateTrack)
    func checkAll()
}
```

**Behaviour changes from old plan Task 5:**
- The service subscribes to `engineStates`. It keeps the latest value and calls `check(.plugin)` whenever the **eligibility key** changes. The key is `nil` for not-applicable states (`.managed`, `.notInstalled`, `.broken`), otherwise the external install's `root` path. Use `removeDuplicates()` on that key, so the 10-minute engine re-check and doctor refreshes don't refetch. The publisher's first value replaces the old `startLaunchChecks()`: a `@Published` publisher replays its current value, so the launch check is automatic. `startLaunchChecks()` is gone.
- `checkPlugin()` with no engine state received yet is a no-op.
- A result with `applicable == false` hides the row: `pluginRowVisible = false`, `pluginUpdate = UpdateStatus()`, and the result counts 0 toward the badge. `applicable == true` shows the row and maps as in the old plan.
- `availableCount` counts the plugin track only while `pluginRowVisible`.

- [ ] Step 1: Failing tests. Port old plan Task 5's tests. Their fakes adapt as follows:
  - `FakePluginChecker` records the `EngineState`s it was called with.
  - Tests drive a `PassthroughSubject<EngineState, Never>` or a `CurrentValueSubject` in place of `engineStates`.

  Build the `EngineState` fixtures from `EngineInstall(root:scoutctl:python:version:vault:)` with `/Users/alex/…` paths. Add these tests:
  - `managedEngineHidesThePluginRowWithoutChecking`: `.managed` → `pluginRowVisible == false`. The checker is called with `.managed` and returns `.notApplicable`, and `availableCount` ignores the plugin.
  - `externalEngineShowsTheRow`: `.external(…, .marketplaceCache)` with an available result → row visible, `availableCount == 1`.
  - `engineStateChangeRechecks`: external A → external B (different root) → two checker calls. Emitting A, then A again → one call.
  - `periodicRefreshWithSameEngineDoesNotRefetch`: the same external state emitted three times → one call.
  - `switchingToManagedHidesTheRow`: external (available) → `.managed` → row hidden, `availableCount == 0`.
  - `checkPluginBeforeAnyEngineStateIsANoOp`.

  Await async checks deterministically. Expose `pluginTask` as `internal private(set)` and `await service.pluginTask?.value`. Do not sleep.
- [ ] Step 2: Run `-only-testing:ScoutTests/UpdateServiceTests` and watch it fail.
- [ ] Step 3: Implement. Start from old plan Task 5's implementation and apply the changes above. Store the Combine subscription in a `Set<AnyCancellable>`. The sink runs on the main actor: use `.receive(on: DispatchQueue.main)` and, where needed, `MainActor.assumeIsolated` inside the sink.
- [ ] Step 4: Run again: all pass. Then commit `feat(updates): UpdateService — app + plugin tracks, plugin row gated on the engine state`.

---

### Task 5: `PluginRelease`, the marketplace-manifest reader

**Files:**
- Create `Scout/Services/Updates/PluginRelease.swift` and fixture `ScoutTests/Fixtures/updates-marketplace-manifest.json`.
- Test: `ScoutTests/Services/Updates/PluginReleaseTests.swift`.

This replaces old plan Tasks 4 (`SemVer`) and 6 (`PluginManifests`). Use `EngineVersion` for comparison and `ClaudePluginsRegistry` / `MarketplaceSource` for the Claude Code files. Do **not** re-parse `installed_plugins.json` or `known_marketplaces.json`.

**Interfaces:**

```swift
nonisolated enum PluginRelease {
    static let marketplaceManifestPath = ".claude-plugin/marketplace.json"
    static let pluginName = "scout"
    /// `plugins[name == "scout"].version` from a marketplace.json; nil if absent/malformed/empty.
    static func scoutVersion(fromMarketplaceManifest data: Data) -> String?
    /// github(repo) / git(github url) → https://raw.githubusercontent.com/<o>/<r>/HEAD/.claude-plugin/marketplace.json
    /// directory(path) → file URL <path>/.claude-plugin/marketplace.json; other / non-GitHub git → nil
    static func latestManifestURL(for source: MarketplaceSource) -> URL?
    /// github / git(github) → https://github.com/<o>/<r>/releases; directory / other → nil
    static func releasesURL(for source: MarketplaceSource) -> URL?
    /// https://github.com/o/r(.git) | git@github.com:o/r(.git) → "o/r"; anything else → nil
    static func githubRepo(fromGitURL url: String) -> String?
}
```

The fixture is a root `marketplace.json` that has the real file's shape, anonymized: `name: "scout-plugin"`, owner `Alex`, and two plugins, `other` at `1.2.3` and `scout` with `source: "./plugin"`, version `0.15.0`, and homepage/repository `https://github.com/example-org/scout`.

- [ ] Step 1. Write the failing tests:
  - reads `0.15.0`, skipping `other`;
  - nil for no `scout` entry, an empty version, a non-array `plugins`, or non-JSON;
  - URL builders for every `MarketplaceSource` case, including `git@github.com:` and `.git`-suffixed URLs, a GitLab URL → nil, and `.other("npm")` → nil;
  - `releasesURL` for GitHub only.
- [ ] Step 2. Run `-only-testing:ScoutTests/PluginReleaseTests` and watch it fail.
- [ ] Step 3. Implement, using `JSONSerialization` leniently. `githubRepo(fromGitURL:)` is old plan Task 6's code, verbatim.
- [ ] Step 4. Run again: all pass. Commit `feat(updates): PluginRelease — read the scout version from a marketplace manifest`.

---

### Task 6: `PluginUpdateChecker`, gated on the engine

**Files:** Create `Scout/Services/Updates/PluginUpdateChecker.swift`. Test: `ScoutTests/Services/Updates/PluginUpdateCheckerTests.swift`.

**Interfaces:**

```swift
protocol RemoteDataFetcher: Sendable { func data(from url: URL) async throws -> Data }   // old plan Task 7, verbatim
struct URLSessionFetcher: RemoteDataFetcher { init() }                                  // old plan Task 7, verbatim
struct PluginUpdateChecker: PluginUpdateChecking {
    init(pluginsDir: URL, fetcher: any RemoteDataFetcher)
    static func standard(fetcher: any RemoteDataFetcher = URLSessionFetcher()) -> PluginUpdateChecker  // EngineLayout.live.claudePluginsDir
    func check(engine: EngineState) async -> PluginUpdateResult
}
```

**Rules:**
1. `.managed`, `.notInstalled` or `.broken` → `.notApplicable`, with no file reads and no fetch.
2. `.external(install, _)`:
   - installed = `ClaudePluginsRegistry.scoutPlugin(pluginsDir:)?.version`, falling back to `install.version`;
   - if neither exists → `applicable: true`, `installed: nil`, error "Couldn't tell which plugin version is installed.";
   - no `scout-plugin` marketplace entry → error "Couldn't read the scout-plugin marketplace entry.";
   - `latestManifestURL == nil` → error "Unsupported plugin source.";
   - file URLs are read from disk; others go through `fetcher`;
   - a fetch or read failure, or a manifest without a scout version, is an error and never a false "up to date";
   - compare with `EngineVersion`. An unparseable version on either side is an error that quotes both;
   - `releasesURL` comes from `PluginRelease.releasesURL(for:)`.
3. The result never throws.

- [ ] Step 1. Write the failing tests. Make them table-like and use temp dirs.
  - Write `installed_plugins.json` and `known_marketplaces.json` into a temp `pluginsDir`, modelled on the existing `ScoutTests/Fixtures/claude-plugins/` fixtures. Read those first and reuse their shape.
  - Stub the fetcher with a `[URL: Data]` map.
  - Cases:
    - managed / notInstalled / broken → `.notApplicable`, and the fetcher is never called (it records calls);
    - github source, newer remote → available, with `releasesURL`;
    - github source, equal remote → up to date;
    - git source → the same raw URL;
    - directory source → reads `<tmp>/.claude-plugin/marketplace.json`, no fetch, `releasesURL == nil`;
    - network error → error, `latest == nil`;
    - remote has no scout entry → error;
    - remote version `"latest"` → error containing `latest`;
    - no installed_plugins entry → falls back to `install.version`;
    - neither → error;
    - missing marketplace entry → error.
- [ ] Step 2. Run `-only-testing:ScoutTests/PluginUpdateCheckerTests` and watch it fail.
- [ ] Step 3. Implement.
- [ ] Step 4. Run again: all pass. Commit `feat(updates): PluginUpdateChecker — external engines only, latest from the marketplace manifest`.

---

### Task 7: `AppUpdater`, feed override, **Check for Updates…**, `ScoutApp` wiring

**Files:**
- Create `Scout/Services/Updates/UpdateFeed.swift`, `Scout/Services/Updates/AppUpdater.swift` and `Scout/Shell/CheckForUpdatesView.swift`.
- Modify `Scout/ScoutApp.swift`.
- Tests: `ScoutTests/Services/Updates/UpdateFeedTests.swift` and `AppUpdaterTests.swift`.

These are old plan Task 8, Steps 1–4, verbatim (`UpdateFeed`, `AppUpdater`, `AppUpdaterDelegate`, and both test files), with these edits:
- In `UpdateFeedTests.httpsOverrideIsReturnedVerbatim`, use `https://github.com/example-org/repo/releases/download/v0.15.1-rc.1/appcast.xml`.
- The `AppUpdater` doc comment says the feed is the latest release's `appcast.xml` asset.
- `CheckForUpdatesView` is verbatim.
- If Swift 6.2 rejects the delegate conformance, use `@preconcurrency SPUUpdaterDelegate` (old plan note).

`ScoutApp` wiring (replaces old plan Step 5). Today's `ScoutApp` has a single `Window("Scout", id: "main")`, a `.window`-style `MenuBarExtra`, and `launchMinimized`. Keep all of that. Re-read the file first, since Part C may have changed it. Then:

```swift
    @StateObject private var appState: AppState
    @StateObject private var updates: UpdateService

    init() {
        let state = AppState(configuration: .forCurrentProcess())
        _appState = StateObject(wrappedValue: state)
        _updates = StateObject(wrappedValue: UpdateService(
            pluginChecker: PluginUpdateChecker.standard(),
            engineStates: state.engineHealth.$state.eraseToAnyPublisher(),
            makeAppController: { AppUpdater(onEvent: $0) }))
    }
```

- Pass `.environmentObject(updates)` to `MainWindowView`, `MenuBarExtraContent` and `SettingsView`.
- Add `CommandGroup(after: .appInfo) { CheckForUpdatesView(updates: updates) }` to `.commands`.
- Leave the `MenuBarIcon` label as is until Task 9.
- `ScoutApp.swift` needs `import Combine` for `eraseToAnyPublisher`, because of MemberImportVisibility.

Unit tests must never construct `ScoutApp`. `AppState`'s test configurations are untouched, so no test builds an `AppUpdater` except `AppUpdaterTests`, which runs in the Debug host and so never starts it.

- [ ] Steps:
  - Run the two suites with `-only-testing:ScoutTests/UpdateFeedTests -only-testing:ScoutTests/AppUpdaterTests` and watch them fail. Implement, then run again: 7 pass.
  - Run the whole `ScoutTests` target. Expected: green, with Task 1's count plus the new tests.
  - Launch the Debug app built to `-derivedDataPath build`. The app menu shows **Check for Updates…** greyed out under **About Scout Dev**. Quit it.
  - Commit `feat(updates): Sparkle adapter (never starts in Debug), https-only feed override, Check for Updates… command`.

---

### Task 8: Settings ▸ Updates

**Files:**
- Create `Scout/Shell/UpdatesSettingsSection.swift`.
- Modify `Scout/Shell/SettingsView.swift`, adding one `section(label: "Updates") { UpdatesSettingsSection() }` directly before `section(label: "About")`.

Keeping the rows in their own file keeps the `SettingsView` diff to two lines. Part B/C edits that file too.

`UpdatesSettingsSection` reads `@EnvironmentObject UpdateService` and renders one `SettingsCard` (from `SettingsComponents.swift`; check its signature) with these rows:
- **Scout.app**: old plan Task 9's `UpdateTrackRow` call for the app track, verbatim.
- **Plugin**, only when `updates.pluginRowVisible`. This is old plan Task 9's plugin-row call, with these edits:
  - the title is `"Plugin (not managed by Scout.app)"`;
  - the footnote while an update is available: "Paste it into Claude Code to update the plugin. Scout.app updates only the engines it manages.";
  - `linkTitle` is "What's new" when `pluginReleasesURL != nil`;
  - `primaryAction` copies `UpdateService.pluginUpdateCommand` to `NSPasteboard.general`. Keep the copy helper private to this file.
- `UpdateTrackRow` and `UpdateChip` are old plan Task 9 Step 4, verbatim, made `private` to this file.
  - Check the `DS.*` tokens they use against the design-system file before using them (`grep -rn "enum DS" Scout/`).
  - If a token was renamed, use its current name. Don't add new ones.

- [ ] Steps:
  - Run the whole target: green.
  - Launch the Debug app and press ⌘,. The **Updates** card shows Scout.app with "Automatic updates are disabled in development builds." and no buttons.
  - On a machine whose engine is `.managed`, there's no plugin row. On Jordan's dev machine (external engine), the plugin row shows the installed version, then up to date or available.
  - Take a screenshot into the scratchpad for the task report, then quit.
  - Commit `feat(updates): Settings ▸ Updates — app row, plugin row for engines Scout.app doesn't manage`.

---

### Task 9: Badges and the menu-bar panel

**Files:** Modify `Scout/Shell/SidebarView.swift`, `MainWindowView.swift`, `MenuBarIcon.swift`, `MenuBarExtraContent.swift` and `ScoutApp.swift` (the label line).

- **Sidebar.** The Settings row already takes `attention: settingsAttention`, Part B's engine flag. Add `var settingsBadge: Int = 0` and pass `badge: settingsBadge` to the same row. `row(_:label:system:badge:attention:)` already supports both. `MainWindowView` passes `settingsBadge: updates.availableCount` next to the existing `settingsAttention:`. Don't add a `.task` launch check: `UpdateService` runs it from the engine-state publisher (Task 4).
- **Menu-bar icon.** Old plan Task 10 Step 2. Re-read `MenuBarIcon.swift` first: keep its current symbol switch and add only the `updateAvailable` parameter and the overlay dot. The `ScoutApp` label becomes `MenuBarIcon(status: appState.menuBarStatus, updateAvailable: updates.anyUpdateAvailable)`.
- **Menu-bar panel.** It is now `.menuBarExtraStyle(.window)` with `footerButton(_:systemImage:action:)` rows, not the old plain menu. Add an `@EnvironmentObject var updates: UpdateService` and:
  - a footer button "Check for Updates…" (`systemImage: "arrow.triangle.2.circlepath"`) calling `updates.check(.app)`, shown only when `updates.appUpdatesEnabled`;
  - when `updates.anyUpdateAvailable`, a footer button "Update available" (`systemImage: "arrow.down.circle"`) that opens Settings the way the existing "Open Scout settings" button does. Reuse its action.
- [ ] Steps:
  - Run the whole target: green.
  - Eyeball the Debug app in its current state: no badge when nothing is behind.
    - Don't edit `~/.claude/plugins/*` or the live engine to force a badge, and don't add test-only hooks for eyeballing. The badge logic is covered by Task 4's tests.
    - List the forced-state check (plugin behind → Settings badge, icon dot, panel item) in the report for Jordan to do by hand.
  - Commit `feat(updates): update badge on the Settings row and menu-bar icon; panel items`.

---

### Task 10: `sparkle-release.sh`, the interface `release.sh` calls

**Files:**
- Create `scripts/sparkle-release.sh` and `scripts/tests/sparkle-release.test.sh`.
- Modify `scripts/release-app.sh` (a guard) and `/.github/workflows/app-ci.yml` (one step).

**The interface.** Posted on #317 (https://github.com/Raven-Scout/Scout/pull/317#issuecomment-5997374621). Agree any change with the "Scout monorepo consolidation" session there, and never edit `release.sh` here. `release.sh` exports `SPARKLE_BIN="$build/SourcePackages/artifacts/sparkle/Sparkle/bin"`, then calls:

| Call site in `build_and_publish` (#317, the unified-release plan's Task 4) | Command | Effect |
| --- | --- | --- |
| right after `xcodebuild … build`, before any `codesign` | `apps/macos/scripts/sparkle-release.sh preflight "$app"` | dies unless `Sparkle.framework` and all five nested components exist, the built `SUPublicEDKey` equals `generate_keys -p`, `SUFeedURL` is the release-asset URL, and `CFBundleVersion` is a positive integer |
| **replaces** `codesign --force --options runtime --timestamp --sign "$ident" "$app"` | `apps/macos/scripts/sparkle-release.sh sign "$app" "$ident"` | signs inside-out in the Global Constraints order, then the outer app, then `codesign --verify --strict --deep` |
| after the DMG is final (stapled, or signed under `SKIP_NOTARIZE=1`) and the notes are rendered | `apps/macos/scripts/sparkle-release.sh appcast "$dmg" "$tag" "$slug" "$notes" "$build/appcast.xml"` | writes the one-item appcast that #317's existing hook attaches |

`appcast` does the following:
1. Stages the DMG, and the notes copied as `<dmg-basename>.md`, in a fresh temp dir.
2. Runs:

   ```
   "$SPARKLE_BIN/generate_appcast"
     --download-url-prefix "https://github.com/$slug/releases/download/$tag/"
     --full-release-notes-url "https://github.com/$slug/releases/tag/$tag"
     --link "https://github.com/$slug"
     --embed-release-notes --maximum-versions 1 --maximum-deltas 0
     -o "$out" "$stage"
   ```

   This signs with the keychain key.
3. Then checks the output:
   - `xmllint --noout`;
   - exactly one `<item>`;
   - `sparkle:version` equals the app's `CFBundleVersion`. The app is read from the DMG, which is attached read-only and detached by a trap;
   - the enclosure URL equals the prefix + DMG name;
   - `sparkle:edSignature` is present.

**Agreed with #317 (2026-10-05; #317 spec and plan at 77a7529, "Interface with Sparkle (#318), agreed" in its description).** Build to exactly this argv. Any change must be agreed with the "Scout monorepo consolidation" session first.
- `release.sh` uses the hook only when `apps/macos/scripts/sparkle-release.sh` is executable **in the commit being built** (the finalize worktree). The file must be committed with mode `100755`; check with `git ls-files -s apps/macos/scripts/sparkle-release.sh`.
- `sign` replaces only the flat **app** codesign. `release.sh` still signs the DMG itself, and still runs `codesign --verify --strict "$app"` after `sign`. Running that a second time is harmless.
- #317's tests pin the order: xcodebuild < preflight < sign, no flat app codesign, and DMG staple < appcast < publish.

Both asks below were accepted into #317, recorded here for context:
- (a) For `kind=release`, a missing `$build/appcast.xml` must be fatal once this PR is merged. Otherwise a Latest release without the asset 404s every installed copy's feed until the next release.
- (b) #314's v0.15.0 bar installs v0.15.0 "from a draft release". `finalize` has no draft mode, so a rehearsal before v0.15.0 is public needs the `rc` path (Task 12).

- [ ] Step 1. Confirm the `generate_appcast` flags against the built tool: `build/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_appcast --help`. The flags above were read from Sparkle 2.10.0's source. If one differs, use the real flag and note it in the report.
- [ ] Step 2. Write the failing tests in `scripts/tests/sparkle-release.test.sh`. Use a stub `PATH` dir with fake `codesign`, `xcrun`, `hdiutil` and `xmllint` (pass-through to the real one), plus a stub `SPARKLE_BIN` with fake `generate_keys` and `generate_appcast`. Each stub appends its argv to a log. Build a fake `Scout.app` tree with the five Sparkle paths and an `Info.plist` written with `plutil`. Assert:
  - `sign` order: the log has exactly six `codesign --force` lines, in order. `Downloader.xpc` has `--preserve-metadata=entitlements`, every line has `--options runtime --timestamp`, no signing line has `--deep`, and a final verify line has `--deep --strict`;
  - `sign` dies, naming the path, when a component is missing, and has signed nothing;
  - `preflight` passes on a matching key. It dies on a mismatched key, a missing framework, a wrong `SUFeedURL`, or a non-numeric `CFBundleVersion`;
  - `appcast` passes the exact flags and stages `<base>.md`. The fake `generate_appcast` writes a canned one-item feed whose `sparkle:version` matches the fake app. It dies when the canned feed has two items, a mismatched version, or no signature;
  - an unknown subcommand, or missing args → usage and exit 2.
- [ ] Step 3. Run `bash scripts/tests/sparkle-release.test.sh`. Expected: fails (no script).
- [ ] Step 4. Implement `scripts/sparkle-release.sh`.
  - Use `set -euo pipefail` and bash 3.2.
  - Write a header comment that states the interface table above and points at the spec amendment.
  - Read plist values with `/usr/libexec/PlistBuddy -c 'Print :Key'`, or with `plutil -extract Key raw`, whichever exists on macOS 15. Tests use the real one.
- [ ] Step 5. Run the tests: all pass. Then run `shellcheck -S warning scripts/sparkle-release.sh scripts/tests/sparkle-release.test.sh`: clean.
- [ ] Step 6. Add the `release-app.sh` guard, right after its build step and before `codesign`: if `"$APP/Contents/Frameworks/Sparkle.framework"` exists, die with "This build embeds Sparkle; release it with the root scripts/release.sh (#317), which signs Sparkle's nested code." `release-app.sh` signs flat, so the notarization it submits would be rejected. #317's Task 7 deletes this script later.
- [ ] Step 7. CI: in `app-ci.yml`'s macOS job, add this step after "Toolchain info" and before "Run ScoutTests":

  ```yaml
        # Release-time Sparkle steps that scripts/release.sh calls (signing
        # order, key preflight, appcast checks), tested against stubs.
        - name: sparkle-release tests
          working-directory: apps/macos
          run: bash scripts/tests/sparkle-release.test.sh
  ```

- [ ] Step 8. Commit `build(updates): sparkle-release.sh — preflight, inside-out signing, generate_appcast; the hook release.sh calls`.

---

### Task 11: Docs, changelog, and the interface on #317

- `apps/macos/README.md`: an **Updates** section saying three things.
  - From v0.15.0 the app updates itself (**Scout → Check for Updates…**, daily checks), and Debug builds never do.
  - The plugin row appears only for engines Scout.app doesn't manage.
  - To test a release candidate, run `open -a /path/to/Scout.app --env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/vX.Y.Z-rc.N/appcast.xml`.

  Release mechanics stay in #317's docs. Link to `scripts/sparkle-release.sh` for the Sparkle steps.
- `apps/macos/CHANGELOG.md` `## [Unreleased]` → `### Added`: in-app updates through Sparkle, Settings ▸ Updates, the update badge, and the plugin-update hand-off for engines the app doesn't manage.
- If the interface changed while implementing, update the #317 comment (linked in Task 10) and tell the "Scout sessions coordination" session. Never edit #317's branch.
- Commit `docs(updates): README Updates section + changelog`.

---

### Task 12: rc rehearsal (owner: Jordan, after merge and once `release.sh` lands)

**Files:** none. An agent runs this only with Jordan's go-ahead for that specific run.

Recommended before `finalize v0.15.0`, because v0.15.0 is the one release where a broken updater can't be fixed through the updater:
1. Run `scripts/release.sh rc v0.15.0-rc.1`. Copy the DMG's app to `~/scout-e2e/Scout.app`.
2. Land any commit on `main`, then run `scripts/release.sh rc v0.15.0-rc.2` from that **later** commit. `CFBundleVersion` is the commit count, and Sparkle never offers an equal build, so rc.2's `sparkle:version` must be higher than rc.1's.
3. Run `open -a ~/scout-e2e/Scout.app --env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/v0.15.0-rc.2/appcast.xml`, then **Check for Updates…**. Sparkle's sheet shows rc.2, and Settings ▸ Updates shows the badge. **Install and Relaunch**: About reads rc.2's build, and `codesign -dv` shows the team ID. On an error sheet, run `log show --last 10m --predicate 'process == "Scout" AND eventMessage CONTAINS[c] "sparkle"' --info`.
4. Delete both pre-releases and their tags.

Then follow #314's v0.15.0 bar as written: the C10 clean-account test, and `v0.15.1-rc.1` updating the installed v0.15.0, with the engine upgrading itself on relaunch.

---

## Self-review against the spec and #314

- **#314 §5, feed URL as a release asset:** Global Constraints, Task 3, and the Task 10 `preflight` check.
- **`finalize` signs and attaches the appcast:** Task 10's `appcast` subcommand and #317's existing attach hook.
- **The app only consumes the feed:** no feed commit, no checked-in `appcast.xml`, no `release-lib.sh`.
- **rc pre-releases with their own appcast, through `SCOUT_APPCAST_URL`:** Tasks 7, 11 and 12.
- **Plugin row only for engines the app doesn't manage; managed never shows it:** Tasks 4, 6 and 8.
- **`UpdateService`, Settings ▸ Updates, the badge:** Tasks 4, 8 and 9.
- **`generate_keys` is Jordan's, the private key is never handled:** Task 2 and Global Constraints.
- **No release, tag or notarization:** Global Constraints and Task 12's owner.
- **Parts B and C:** their files are read-only here (Global Constraints). The `ScoutApp`, `SettingsView` and `MainWindowView` edits stay small, and each task rebases first.
- **Type consistency:**
  - `PluginUpdateResult.applicable` / `.releasesURL` / `.notApplicable`, `UpdateService.pluginRowVisible` / `.pluginReleasesURL` / `.availableCount`, `PluginUpdateChecking.check(engine:)`: identical in Tasks 4, 6, 8 and 9.
  - `sparkle-release.sh` subcommands: identical in Tasks 10 and 11.

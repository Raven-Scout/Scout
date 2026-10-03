# In-App Updates (Sparkle app track + plugin detect/hand-off) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One in-app update surface: Scout.app updates itself through Sparkle (detect → download → install → relaunch) from a checked-in `appcast.xml`, and the app detects when the scout-plugin is behind and hands you `/scout-update`; both tracks feed a Settings ▸ Updates section plus a badge on the sidebar and menu-bar icon.

**Architecture:** A Sparkle-free `UpdateService` state machine owns two `UpdateStatus` tracks. The app track is fed by a thin `AppUpdater` adapter around `SPUStandardUpdaterController` (delegate events → `UpdateService.applyAppEvent`), which never starts in Debug builds. The plugin track is `PluginUpdateChecker`: installed version from `~/.claude/plugins/installed_plugins.json`, latest resolved source-aware from `known_marketplaces.json` (GitHub raw for end users, local directory for dev checkouts), compared with a small `SemVer`. `scripts/release.sh` signs Sparkle's nested code inside-out, EdDSA-signs the stapled DMG, regenerates `appcast.xml` (one item, the newest release) and commits + pushes it to `main`; `PRERELEASE=1` publishes a GitHub pre-release with the appcast as an asset only, for rehearsing the update path.

**Tech Stack:** Swift 6.2 (Swift 5 language mode, default MainActor isolation), SwiftUI, Sparkle 2.9.6, Swift Testing (`@Test`/`@Suite`), xcodebuild, `/bin/bash` 3.2-compatible shell, `codesign`/`notarytool`/`stapler`, `gh`.

**Spec:** `docs/superpowers/specs/2026-07-07-in-app-updates-design.md` (approved 2026-07-07; read its **Amendments (2026-09-02)** section too — this plan implements both).

## Global Constraints

- Branch: `feat/in-app-updates` (PR Raven-Scout/Scout#74; spec + this plan already committed). Base `main`.
- Sparkle package `https://github.com/sparkle-project/Sparkle`, product `Sparkle`, **exact** version `2.9.6`.
- Feed URL (verbatim; Info.plist, tests, README): `https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml`. The feed file is `appcast.xml` at the repo root, regenerated per release with **one** item (the newest release).
- Info.plist Sparkle keys: `SUFeedURL`, `SUPublicEDKey`, `SUEnableAutomaticChecks = true`, `SUScheduledCheckInterval = 86400` — typed (boolean/number), not strings. No `SUAutomaticallyUpdate` (Sparkle shows its update dialog; the user clicks Install).
- Plugin identifiers (verbatim): installed entry key `scout@scout-plugin` in `installed_plugins.json`; marketplace key `scout-plugin` in `known_marketplaces.json`; manifest path `.claude-plugin/plugin.json`; hand-off command `/scout-update`; canonical public repo `Raven-Scout/scout-plugin`.
- Feed override env var `SCOUT_APPCAST_URL`; honored only for well-formed `https` URLs.
- Debug builds never start Sparkle. The **only** `#if DEBUG` is `AppUpdater.updatesEnabledForThisBuild`; all Sparkle code compiles in both configurations. The plugin track works in Debug too.
- Signing order (never `--deep` when signing; `--deep` only for `codesign --verify`): `Installer.xpc` → `Downloader.xpc` (`--preserve-metadata=entitlements`) → `Autoupdate` → `Updater.app` → `Sparkle.framework` → `Scout.app`, each with `--options runtime --timestamp`.
- `sign_update` runs on the **final, stapled** DMG; appcast `length` = that file's byte size.
- Version rule: `^[0-9]+\.[0-9]+\.[0-9]+$` = release; `^[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.]+$` = pre-release, requires `PRERELEASE=1`; `PRERELEASE=1` requires an explicit version. Only plain `vX.Y.Z` tags feed the version rule / changelog range. Build number must be strictly greater than the latest plain tag's `git rev-list --count`. A non-prerelease release must be cut from a clean checkout of `main` (release.sh pushes `appcast.xml` there).
- Shell scripts must run under macOS `/bin/bash` 3.2 (no `${arr[@]}` on a possibly-empty array under `set -u`, no `declare -A`, no `local -n`).
- Build/test: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteTypeName>`. `-only-testing:` must name a real `@Suite` **type**; a directory-style selector silently runs ZERO tests and reports success.
- New `.swift` and fixture files under `Scout/` or `ScoutTests/` are picked up automatically (synchronized file groups; fixtures land flattened in the test bundle root, so fixture file names must be unique). The **only** `project.pbxproj` edits are the ones written verbatim in Tasks 1 and 3.
- SourceKit may report "Cannot find type … in scope" / "No such module 'Testing'" / "No such module 'Sparkle'" — false positives; `xcodebuild` is authoritative.
- Test files need explicit imports (`import Foundation`, `import Combine`) — `MemberImportVisibility` is on.
- All fixtures anonymized per `CLAUDE.md`: `example-org/…`, `/Users/alex/…`, no real SHAs. The real `Raven-Scout/…` slugs appear only where they are the product's actual defaults.
- No parser/corpus changes; the three-repo `parser-corpus.json` rules do not apply.
- Conventional-commit messages ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File map

| Path | Responsibility |
| --- | --- |
| `Scout/Info.plist` (new) | Sparkle keys, typed; merged with the generated plist |
| `Scout/Services/Updates/SemVer.swift` | `major.minor.patch[-pre]` value type, Comparable |
| `Scout/Services/Updates/UpdateService.swift` | `UpdateTrack`, `UpdateStatus`, `AppUpdateEvent`, `AppUpdateController`, `PluginUpdateResult`, `PluginUpdateChecking`, `UpdateService` |
| `Scout/Services/Updates/PluginManifests.swift` | Pure parsing of `installed_plugins.json`, `known_marketplaces.json`, `plugin.json`; URL builders |
| `Scout/Services/Updates/PluginUpdateChecker.swift` | `RemoteDataFetcher` protocol + `URLSessionFetcher`; `PluginUpdateChecker` |
| `Scout/Services/Updates/UpdateFeed.swift` | `SCOUT_APPCAST_URL` override parser |
| `Scout/Services/Updates/AppUpdater.swift` | Sparkle adapter (`SPUStandardUpdaterController` + delegate) → `AppUpdateEvent` |
| `Scout/Shell/CheckForUpdatesView.swift` | "Check for Updates…" menu item (app menu + menu-bar extra) |
| `Scout/Shell/SettingsView.swift` | new Updates section |
| `Scout/Shell/SidebarView.swift`, `MenuBarIcon.swift`, `MenuBarExtraContent.swift`, `MainWindowView.swift`, `ScoutApp.swift` | badge + wiring |
| `ScoutTests/Services/Updates/*Tests.swift`, `ScoutTests/Fixtures/plugins-*.json` | tests + anonymized fixtures |
| `scripts/release-lib.sh`, `scripts/tests/release-lib.test.sh` | pure release helpers + bash tests |
| `scripts/release.sh`, `appcast.xml`, `.github/workflows/ci.yml`, `README.md` | pipeline, feed, CI step, docs |

---

### Task 1: Add the Sparkle package to the Scout target

**Files:**
- Modify: `Scout.xcodeproj/project.pbxproj` (PBXBuildFile section lines 9–11; Frameworks phase lines 42–49; Scout target `packageProductDependencies` lines 97–99; `packageReferences` lines 153–155; `XCRemoteSwiftPackageReference` section lines 209–218; `XCSwiftPackageProductDependency` section lines 220–226)

**Interfaces:**
- Produces: `import Sparkle` for the `Scout` target (Task 8); `Scout.app/Contents/Frameworks/Sparkle.framework` in built products (Task 12 signs it); Sparkle CLI tools under `<derivedData>/SourcePackages/artifacts/sparkle/Sparkle/bin/` (Tasks 2, 12).

- [ ] **Step 1: Add the build-file, framework-phase and package entries**

New object IDs (24 hex chars, unique in the file):

| ID | Role |
| --- | --- |
| `5AC0FFEE2F9599AB00000001` | `XCRemoteSwiftPackageReference "Sparkle"` |
| `5AC0FFEE2F9599AB00000002` | `XCSwiftPackageProductDependency` `Sparkle` |
| `5AC0FFEE2F9599AB00000003` | `PBXBuildFile` `Sparkle in Frameworks` |

1a. In `/* Begin PBXBuildFile section */`, after the Grape line:

```
		5AC0FFEE2F9599AB00000003 /* Sparkle in Frameworks */ = {isa = PBXBuildFile; productRef = 5AC0FFEE2F9599AB00000002 /* Sparkle */; };
```

1b. Scout target `PBXFrameworksBuildPhase` (`BEEE45512F95613D0078191D /* Frameworks */`):

```
			files = (
				BEEE45A02F9599AB0078191D /* Grape in Frameworks */,
				5AC0FFEE2F9599AB00000003 /* Sparkle in Frameworks */,
			);
```

1c. `BEEE45532F95613D0078191D /* Scout */` native target:

```
			packageProductDependencies = (
				BEEE45A22F9599AB0078191D /* Grape */,
				5AC0FFEE2F9599AB00000002 /* Sparkle */,
			);
```

1d. `PBXProject` object:

```
			packageReferences = (
				BEEE45A12F9599AB0078191D /* XCRemoteSwiftPackageReference "Grape" */,
				5AC0FFEE2F9599AB00000001 /* XCRemoteSwiftPackageReference "Sparkle" */,
			);
```

1e. `/* Begin XCRemoteSwiftPackageReference section */`, after the Grape block:

```
		5AC0FFEE2F9599AB00000001 /* XCRemoteSwiftPackageReference "Sparkle" */ = {
			isa = XCRemoteSwiftPackageReference;
			repositoryURL = "https://github.com/sparkle-project/Sparkle";
			requirement = {
				kind = exactVersion;
				version = 2.9.6;
			};
		};
```

1f. `/* Begin XCSwiftPackageProductDependency section */`, after the Grape block:

```
		5AC0FFEE2F9599AB00000002 /* Sparkle */ = {
			isa = XCSwiftPackageProductDependency;
			package = 5AC0FFEE2F9599AB00000001 /* XCRemoteSwiftPackageReference "Sparkle" */;
			productName = Sparkle;
		};
```

- [ ] **Step 2: Build Debug into a local derived-data dir; verify embedding and tools**

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project Scout.xcodeproj -scheme Scout -configuration Debug \
  -destination 'platform=macOS' -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO build 2>&1 | grep -E "error:|warning: .*Sparkle|BUILD (SUCCEEDED|FAILED)"
ls build/Build/Products/Debug/Scout.app/Contents/Frameworks/
ls build/SourcePackages/artifacts/sparkle/Sparkle/bin/
```

Expected: `BUILD SUCCEEDED`; `Sparkle.framework` in the first listing; `generate_appcast  generate_keys  sign_update` (plus `BinaryDelta`) in the second.

**If `Contents/Frameworks/` has no `Sparkle.framework`**, add an explicit embed phase and re-run this step:

- PBXBuildFile section: `		5AC0FFEE2F9599AB00000005 /* Sparkle in Embed Frameworks */ = {isa = PBXBuildFile; productRef = 5AC0FFEE2F9599AB00000002 /* Sparkle */; settings = {ATTRIBUTES = (CodeSignOnCopy, RemoveHeadersOnCopy, ); }; };`
- New section right after `/* End PBXContainerItemProxy section */`:

```
/* Begin PBXCopyFilesBuildPhase section */
		5AC0FFEE2F9599AB00000004 /* Embed Frameworks */ = {
			isa = PBXCopyFilesBuildPhase;
			buildActionMask = 2147483647;
			dstPath = "";
			dstSubfolderSpec = 10;
			files = (
				5AC0FFEE2F9599AB00000005 /* Sparkle in Embed Frameworks */,
			);
			name = "Embed Frameworks";
			runOnlyForDeploymentPostprocessing = 0;
		};
/* End PBXCopyFilesBuildPhase section */
```

- Scout target `buildPhases`: append `				5AC0FFEE2F9599AB00000004 /* Embed Frameworks */,` after the Resources phase.

- [ ] **Step 3: Full suite still green**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST SUCCEEDED`, same test count as before.

- [ ] **Step 4: Commit**

```bash
git add Scout.xcodeproj/project.pbxproj
git commit -m "build(updates): add Sparkle 2.9.6 (exact) as an SPM dependency of the Scout target

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Generate the EdDSA signing key (owner: Jordan)

**Files:** none. Output: one 44-character base64 public key, consumed by Task 3.

**Owner:** Jordan, on the release machine (the one with the `scout-notary` notarytool profile). An agent runs this only with Jordan's explicit go-ahead in the PR — the private key is the root of trust for every future update and lives in his login keychain.

**Interfaces:**
- Produces: public key → `SUPublicEDKey` (Task 3); private key in the login keychain (service `https://sparkle-project.org`, account `ed25519`) used by `sign_update` / `generate_keys -p` (Task 12).

- [ ] **Step 1: Locate the tools (from Task 1's build)**

```bash
SPARKLE_BIN="$(find build/SourcePackages/artifacts -type d -path '*/sparkle/Sparkle/bin' | head -1)"; echo "$SPARKLE_BIN"
```

- [ ] **Step 2: Generate**

```bash
"$SPARKLE_BIN/generate_keys"
```

Expected: confirmation the key was stored in the Keychain, then the public key (44 base64 chars ending in `=`). If it reports an existing key, print it with `"$SPARKLE_BIN/generate_keys" -p` — do **not** make a second one.

- [ ] **Step 3: Back it up (mandatory — losing it strands every install)**

```bash
"$SPARKLE_BIN/generate_keys" -x ~/Desktop/scout-sparkle-ed25519.key
```

Store the contents in a password-manager entry "Scout Sparkle EdDSA private key", then `rm ~/Desktop/scout-sparkle-ed25519.key`. Restore elsewhere with `generate_keys -f <file>`.

- [ ] **Step 4: Hand the public key to Task 3** (PR comment or directly into `Scout/Info.plist`).

---

### Task 3: Typed `Scout/Info.plist` with the Sparkle keys + contract test

**Files:**
- Create: `Scout/Info.plist`
- Modify: `Scout.xcodeproj/project.pbxproj` (Scout target Debug config lines 347–378 and Release config lines 379–410; `PBXFileSystemSynchronizedRootGroup` for Scout lines 29–33; new exception-set section)
- Test: `ScoutTests/Services/Updates/UpdateInfoPlistTests.swift` (create)

**Interfaces:**
- Consumes: public key (Task 2).
- Produces: `Bundle.main.infoDictionary["SUFeedURL"|"SUPublicEDKey"|"SUEnableAutomaticChecks"|"SUScheduledCheckInterval"]` in both configurations — read by Sparkle and by `release.sh`'s preflight (Task 12).

- [ ] **Step 1: Failing contract test**

Create `ScoutTests/Services/Updates/UpdateInfoPlistTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// The test host is Scout.app, so `Bundle.main` is the app bundle. These pin
/// the Sparkle contract: drop the key, retype a boolean as the string "YES",
/// or edit the feed URL and this suite goes red.
@Suite("Sparkle Info.plist contract")
struct UpdateInfoPlistTests {
    private var info: [String: Any] { Bundle.main.infoDictionary ?? [:] }

    @Test func feedURLIsTheCheckedInAppcastOnMain() {
        #expect(info["SUFeedURL"] as? String
            == "https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml")
    }

    @Test func publicKeyIsA32ByteEd25519Key() throws {
        let key = try #require(info["SUPublicEDKey"] as? String)
        let bytes = try #require(Data(base64Encoded: key))
        #expect(bytes.count == 32)
    }

    @Test func automaticChecksDefaultOn() {
        // `as? Bool` succeeds for a plist <true/> and fails for the string "YES".
        #expect(info["SUEnableAutomaticChecks"] as? Bool == true)
    }

    @Test func checkIntervalIsDaily() {
        #expect((info["SUScheduledCheckInterval"] as? NSNumber)?.intValue == 86400)
    }

    @Test func noSilentAutoDownload() {
        // The approved design shows Sparkle's dialog and lets the user click
        // Install; background auto-download is explicitly not enabled.
        #expect(info["SUAutomaticallyUpdate"] == nil)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateInfoPlistTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST FAILED` — 4 of 5 fail (keys absent; `noSilentAutoDownload` passes).

- [ ] **Step 3: Create the plist** (replace `{{PUBLIC_KEY_FROM_TASK_2}}` with the key from Task 2)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<!-- Sparkle (in-app updates). Everything else in Info.plist is generated
	     from build settings (GENERATE_INFOPLIST_FILE = YES) and merged with
	     this file. See docs/superpowers/specs/2026-07-07-in-app-updates-design.md. -->
	<key>SUFeedURL</key>
	<string>https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml</string>
	<key>SUPublicEDKey</key>
	<string>{{PUBLIC_KEY_FROM_TASK_2}}</string>
	<key>SUEnableAutomaticChecks</key>
	<true/>
	<key>SUScheduledCheckInterval</key>
	<integer>86400</integer>
</dict>
</plist>
```

`plutil -lint Scout/Info.plist` → `Scout/Info.plist: OK`.

- [ ] **Step 4: Point both Scout configs at it; keep it out of Copy Bundle Resources**

4a. In **both** `BEEE45602F95613E0078191D /* Debug */` and `BEEE45612F95613E0078191D /* Release */` (the configs containing `PRODUCT_BUNDLE_IDENTIFIER = com.scout.Scout…`), directly after `GENERATE_INFOPLIST_FILE = YES;`:

```
				INFOPLIST_FILE = Scout/Info.plist;
```

4b. New section after `/* End PBXFileReference section */`:

```
/* Begin PBXFileSystemSynchronizedBuildFileExceptionSet section */
		5AC0FFEE2F9599AB00000006 /* Exceptions for "Scout" folder in "Scout" target */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Info.plist,
			);
			target = BEEE45532F95613D0078191D /* Scout */;
		};
/* End PBXFileSystemSynchronizedBuildFileExceptionSet section */
```

4c. Scout synchronized root group:

```
		BEEE45562F95613D0078191D /* Scout */ = {
			isa = PBXFileSystemSynchronizedRootGroup;
			exceptions = (
				5AC0FFEE2F9599AB00000006 /* Exceptions for "Scout" folder in "Scout" target */,
			);
			path = Scout;
			sourceTree = "<group>";
		};
```

- [ ] **Step 5: Verify**

Run the suite selector from Step 2. Expected: `Test run with 5 tests passed`, `TEST SUCCEEDED`, no "Info.plist in Copy Bundle Resources" warning. Then:

```bash
APP="$(find ~/Library/Developer/Xcode/DerivedData -path '*/Build/Products/Debug/Scout.app' -maxdepth 6 | head -1)"
plutil -p "$APP/Contents/Info.plist" | grep -E 'CFBundleDisplayName|NSAppleEventsUsageDescription|SU[A-Z]'
```

Expected: `"CFBundleDisplayName" => "Scout Dev"`, the Apple-events string, and the four `SU…` keys with `1` / `86400` / URL / key values (not quoted `"YES"`).

- [ ] **Step 6: Commit**

```bash
git add Scout/Info.plist Scout.xcodeproj/project.pbxproj ScoutTests/Services/Updates/UpdateInfoPlistTests.swift
git commit -m "feat(updates): Sparkle feed URL, public key and defaults in a typed Info.plist (+ contract test)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `SemVer`

**Files:**
- Create: `Scout/Services/Updates/SemVer.swift`
- Test: `ScoutTests/Services/Updates/SemVerTests.swift` (create)

**Interfaces:**
- Produces: `struct SemVer: Comparable, Equatable, Hashable, Sendable, CustomStringConvertible { let major, minor, patch: Int; let prerelease: [String]; init?(_ string: String) }` — used by `PluginUpdateChecker` (Task 7) and `PluginManifests.installedVersionFromCache` (Task 6).

- [ ] **Step 1: Failing tests**

Create `ScoutTests/Services/Updates/SemVerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("SemVer")
struct SemVerTests {
    @Test func parsesCoreAndOptionalVPrefix() throws {
        let v = try #require(SemVer("0.7.2"))
        #expect((v.major, v.minor, v.patch) == (0, 7, 2))
        #expect(v.prerelease.isEmpty)
        #expect(SemVer("v1.2.3") == SemVer("1.2.3"))
    }

    @Test func parsesPrereleaseAndIgnoresBuildMetadata() throws {
        let rc = try #require(SemVer("1.0.0-rc.1+build.7"))
        #expect(rc.prerelease == ["rc", "1"])
        #expect(rc.description == "1.0.0-rc.1")
    }

    @Test func rejectsMalformed() {
        #expect(SemVer("") == nil)
        #expect(SemVer("1.2") == nil)
        #expect(SemVer("1.2.x") == nil)
        #expect(SemVer("1.2.3-") == nil)
        #expect(SemVer("latest") == nil)
    }

    @Test func ordersNumericCore() {
        #expect(SemVer("0.7.2")! < SemVer("0.7.3")!)
        #expect(SemVer("0.9.9")! < SemVer("1.0.0")!)
        #expect(SemVer("0.10.0")! > SemVer("0.9.0")!)   // numeric, not lexical
        #expect(SemVer("1.0.0")! == SemVer("1.0.0")!)
    }

    @Test func prereleaseSortsBeforeRelease() {
        #expect(SemVer("1.0.0-rc.1")! < SemVer("1.0.0")!)
        #expect(SemVer("1.0.0-alpha")! < SemVer("1.0.0-beta")!)
        #expect(SemVer("1.0.0-rc.1")! < SemVer("1.0.0-rc.2")!)
        #expect(SemVer("1.0.0-rc.9")! < SemVer("1.0.0-rc.10")!)  // numeric identifiers
        #expect(SemVer("1.0.0-rc")! < SemVer("1.0.0-rc.1")!)     // shorter wins
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SemVerTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — `cannot find 'SemVer' in scope`.

- [ ] **Step 3: Implement**

Create `Scout/Services/Updates/SemVer.swift`:

```swift
import Foundation

/// Minimal semantic version: `MAJOR.MINOR.PATCH[-pre.release][+build]`.
/// Build metadata is parsed and discarded (it never affects precedence).
/// Precedence follows semver 2.0 §11: numeric core, then a release outranks
/// any pre-release, then pre-release identifiers left to right (numeric
/// identifiers compare numerically, otherwise ASCII; a shorter list wins).
struct SemVer: Comparable, Hashable, Sendable, CustomStringConvertible {
    let major: Int
    let minor: Int
    let patch: Int
    let prerelease: [String]

    init?(_ string: String) {
        var s = string.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("v") { s.removeFirst() }
        if let plus = s.firstIndex(of: "+") { s = String(s[..<plus]) }

        let pre: [String]
        if let dash = s.firstIndex(of: "-") {
            let tail = s[s.index(after: dash)...]
            guard !tail.isEmpty else { return nil }
            pre = tail.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
            guard pre.allSatisfy({ !$0.isEmpty }) else { return nil }
            s = String(s[..<dash])
        } else {
            pre = []
        }

        let core = s.split(separator: ".", omittingEmptySubsequences: false)
        guard core.count == 3,
              let maj = Int(core[0]), let min = Int(core[1]), let pat = Int(core[2]),
              maj >= 0, min >= 0, pat >= 0
        else { return nil }
        major = maj; minor = min; patch = pat; prerelease = pre
    }

    var description: String {
        let core = "\(major).\(minor).\(patch)"
        return prerelease.isEmpty ? core : core + "-" + prerelease.joined(separator: ".")
    }

    static func < (lhs: SemVer, rhs: SemVer) -> Bool {
        if lhs.major != rhs.major { return lhs.major < rhs.major }
        if lhs.minor != rhs.minor { return lhs.minor < rhs.minor }
        if lhs.patch != rhs.patch { return lhs.patch < rhs.patch }
        switch (lhs.prerelease.isEmpty, rhs.prerelease.isEmpty) {
        case (true, true):   return false
        case (true, false):  return false   // release > pre-release
        case (false, true):  return true
        case (false, false): break
        }
        for (l, r) in zip(lhs.prerelease, rhs.prerelease) where l != r {
            switch (Int(l), Int(r)) {
            case let (li?, ri?): return li < ri
            case (.some, .none): return true    // numeric < alphanumeric
            case (.none, .some): return false
            default:             return l < r
            }
        }
        return lhs.prerelease.count < rhs.prerelease.count
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Expected: `Test run with 5 tests passed`, `TEST SUCCEEDED`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/Updates/SemVer.swift ScoutTests/Services/Updates/SemVerTests.swift
git commit -m "feat(updates): SemVer value type with semver-2.0 precedence

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `UpdateService` — the Sparkle-free state machine

**Files:**
- Create: `Scout/Services/Updates/UpdateService.swift`
- Test: `ScoutTests/Services/Updates/UpdateServiceTests.swift` (create)

**Interfaces:**
- Produces (used by Tasks 7–10):

```swift
enum UpdateTrack: CaseIterable, Sendable { case app, plugin }
struct UpdateStatus: Equatable, Sendable {
    enum State: Equatable, Sendable { case idle, checking, upToDate, available, error(String) }
    var currentVersion: String?; var latestVersion: String?; var state: State
    var isAvailable: Bool
}
enum AppUpdateEvent: Equatable, Sendable { case found(version: String), upToDate, failed(String) }
@MainActor protocol AppUpdateController: AnyObject {
    var isEnabled: Bool { get }; var currentVersion: String? { get }; func checkForUpdates()
}
struct PluginUpdateResult: Equatable, Sendable {
    var installed: String?; var latest: String?; var isUpdateAvailable: Bool
    var changelogURL: URL?; var error: String?
}
protocol PluginUpdateChecking: Sendable { func check() async -> PluginUpdateResult }
@MainActor final class UpdateService: ObservableObject {
    static let pluginUpdateCommand = "/scout-update"
    init(pluginChecker: any PluginUpdateChecking,
         makeAppController: (@escaping @MainActor (AppUpdateEvent) -> Void) -> any AppUpdateController)
    @Published private(set) var appUpdate: UpdateStatus
    @Published private(set) var pluginUpdate: UpdateStatus
    @Published private(set) var pluginChangelogURL: URL?
    var appUpdatesEnabled: Bool; var anyUpdateAvailable: Bool; var availableCount: Int
    func applyAppEvent(_ event: AppUpdateEvent)
    func checkApp()                 // → controller.checkForUpdates(); state .checking
    func checkPlugin() async
    func check(_ track: UpdateTrack) // fire-and-forget wrappers for buttons
    func checkAll()
    func startLaunchChecks()        // plugin check on launch (Sparkle schedules its own)
}
```

- [ ] **Step 1: Failing tests**

Create `ScoutTests/Services/Updates/UpdateServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@MainActor
private final class FakeAppController: AppUpdateController {
    let isEnabled: Bool
    let currentVersion: String? = "0.11.2"
    var checkCalls = 0
    var emit: (@MainActor (AppUpdateEvent) -> Void)?
    init(isEnabled: Bool) { self.isEnabled = isEnabled }
    func checkForUpdates() { checkCalls += 1 }
}

private struct FakePluginChecker: PluginUpdateChecking {
    let result: PluginUpdateResult
    func check() async -> PluginUpdateResult { result }
}

@Suite("UpdateService")
@MainActor
struct UpdateServiceTests {
    private func make(appEnabled: Bool = true,
                      plugin: PluginUpdateResult = PluginUpdateResult(installed: "0.7.2", latest: "0.7.2", isUpdateAvailable: false, changelogURL: nil, error: nil)
    ) -> (UpdateService, FakeAppController) {
        var controller: FakeAppController!
        let service = UpdateService(pluginChecker: FakePluginChecker(result: plugin)) { emit in
            let c = FakeAppController(isEnabled: appEnabled)
            c.emit = emit
            controller = c
            return c
        }
        return (service, controller)
    }

    @Test func startsIdleWithCurrentVersions() {
        let (service, _) = make()
        #expect(service.appUpdate.state == .idle)
        #expect(service.appUpdate.currentVersion == "0.11.2")
        #expect(service.pluginUpdate.state == .idle)
        #expect(service.anyUpdateAvailable == false)
        #expect(service.availableCount == 0)
    }

    @Test func checkAppMarksCheckingAndAsksTheController() {
        let (service, controller) = make()
        service.checkApp()
        #expect(service.appUpdate.state == .checking)
        #expect(controller.checkCalls == 1)
    }

    @Test func checkAppIsANoOpWhenUpdaterDisabled() {
        let (service, controller) = make(appEnabled: false)
        service.checkApp()
        #expect(service.appUpdatesEnabled == false)
        #expect(service.appUpdate.state == .idle)
        #expect(controller.checkCalls == 0)
    }

    @Test func appEventsDriveTheAppTrack() {
        let (service, controller) = make()
        controller.emit?(.found(version: "0.12.0"))
        #expect(service.appUpdate.state == .available)
        #expect(service.appUpdate.latestVersion == "0.12.0")
        #expect(service.anyUpdateAvailable)
        #expect(service.availableCount == 1)

        controller.emit?(.upToDate)
        #expect(service.appUpdate.state == .upToDate)
        #expect(service.anyUpdateAvailable == false)

        controller.emit?(.failed("offline"))
        #expect(service.appUpdate.state == .error("offline"))
    }

    @Test func pluginCheckAvailable() async {
        let (service, _) = make(plugin: PluginUpdateResult(
            installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true,
            changelogURL: URL(string: "https://github.com/example-org/scout-plugin/blob/HEAD/CHANGELOG.md"),
            error: nil))
        await service.checkPlugin()
        #expect(service.pluginUpdate == UpdateStatus(currentVersion: "0.7.2", latestVersion: "0.8.0", state: .available))
        #expect(service.pluginChangelogURL?.absoluteString == "https://github.com/example-org/scout-plugin/blob/HEAD/CHANGELOG.md")
        #expect(service.availableCount == 1)
    }

    @Test func pluginCheckUpToDateAndError() async {
        let (upToDate, _) = make()
        await upToDate.checkPlugin()
        #expect(upToDate.pluginUpdate.state == .upToDate)

        let (errored, _) = make(plugin: PluginUpdateResult(
            installed: "0.7.2", latest: nil, isUpdateAvailable: false, changelogURL: nil, error: "offline"))
        await errored.checkPlugin()
        #expect(errored.pluginUpdate.state == .error("offline"))
        #expect(errored.pluginUpdate.currentVersion == "0.7.2")
    }

    @Test func pluginNotInstalledStaysIdleWithNoVersion() async {
        let (service, _) = make(plugin: PluginUpdateResult(
            installed: nil, latest: nil, isUpdateAvailable: false, changelogURL: nil, error: nil))
        await service.checkPlugin()
        #expect(service.pluginUpdate.currentVersion == nil)
        #expect(service.pluginUpdate.state == .idle)   // row hidden, nothing to report
    }

    @Test func bothTracksCountTowardTheBadge() async {
        let (service, controller) = make(plugin: PluginUpdateResult(
            installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true, changelogURL: nil, error: nil))
        await service.checkPlugin()
        controller.emit?(.found(version: "0.12.0"))
        #expect(service.availableCount == 2)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateServiceTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — `cannot find type 'AppUpdateController' in scope` (and friends).

- [ ] **Step 3: Implement**

Create `Scout/Services/Updates/UpdateService.swift`:

```swift
import Foundation
import Combine

/// Which of the two independent things can be behind.
enum UpdateTrack: CaseIterable, Sendable { case app, plugin }

/// One track's view of the world. `currentVersion` is what's installed /
/// running; `latestVersion` is nil until a check has answered.
struct UpdateStatus: Equatable, Sendable {
    enum State: Equatable, Sendable {
        case idle, checking, upToDate, available
        case error(String)
    }
    var currentVersion: String?
    var latestVersion: String?
    var state: State = .idle

    var isAvailable: Bool { state == .available }
}

/// What the Sparkle adapter reports back. Kept Sparkle-free so the service
/// (and its tests) never import Sparkle.
enum AppUpdateEvent: Equatable, Sendable {
    case found(version: String)
    case upToDate
    case failed(String)
}

/// The app-track controller the service drives. `AppUpdater` (Sparkle) is
/// the production implementation; tests use a fake.
@MainActor
protocol AppUpdateController: AnyObject {
    /// False in Debug builds — the updater never starts there.
    var isEnabled: Bool { get }
    var currentVersion: String? { get }
    /// User-initiated check. Results come back through `AppUpdateEvent`s.
    func checkForUpdates()
}

/// Outcome of one plugin check. `installed == nil` means "no scout plugin
/// installed / unreadable manifest" — the UI hides the row; it is not an error.
struct PluginUpdateResult: Equatable, Sendable {
    var installed: String?
    var latest: String?
    var isUpdateAvailable: Bool
    var changelogURL: URL?
    var error: String?
}

protocol PluginUpdateChecking: Sendable {
    func check() async -> PluginUpdateResult
}

/// One observable for both tracks; drives Settings ▸ Updates and the badges.
///
/// App track: Sparkle owns detection *and* installation. We only mirror what
/// it tells us (via `applyAppEvent`) and forward the user's "check now".
/// Plugin track: detect + hand off. The app cannot apply a plugin update —
/// that happens inside Claude Code (`/scout-update`) — so the primary action
/// is copying the command.
@MainActor
final class UpdateService: ObservableObject {
    static let pluginUpdateCommand = "/scout-update"

    @Published private(set) var appUpdate: UpdateStatus
    @Published private(set) var pluginUpdate = UpdateStatus()
    @Published private(set) var pluginChangelogURL: URL?

    private var appController: (any AppUpdateController)?
    private let pluginChecker: any PluginUpdateChecking
    private var pluginTask: Task<Void, Never>?

    /// - Parameters:
    ///   - pluginChecker: the plugin-track checker (file reads + one HTTPS GET).
    ///   - makeAppController: builds the app-track controller, handing it the
    ///     sink its delegate must call. A factory (not an instance) so the
    ///     controller can capture the service's sink without a retain cycle.
    init(pluginChecker: any PluginUpdateChecking,
         makeAppController: (@escaping @MainActor (AppUpdateEvent) -> Void) -> any AppUpdateController) {
        self.pluginChecker = pluginChecker
        self.appUpdate = UpdateStatus()
        let controller = makeAppController { [weak self] event in
            self?.applyAppEvent(event)
        }
        self.appController = controller
        self.appUpdate.currentVersion = controller.currentVersion
    }

    var appUpdatesEnabled: Bool { appController?.isEnabled ?? false }
    var anyUpdateAvailable: Bool { availableCount > 0 }
    var availableCount: Int {
        (appUpdate.isAvailable ? 1 : 0) + (pluginUpdate.isAvailable ? 1 : 0)
    }

    // MARK: App track

    func applyAppEvent(_ event: AppUpdateEvent) {
        switch event {
        case .found(let version):
            appUpdate.latestVersion = version
            appUpdate.state = .available
        case .upToDate:
            appUpdate.latestVersion = appUpdate.currentVersion
            appUpdate.state = .upToDate
        case .failed(let message):
            appUpdate.state = .error(message)
        }
    }

    /// Sparkle shows its own UI from here on (found / up to date / error);
    /// we just note that a check is in flight.
    func checkApp() {
        guard let controller = appController, controller.isEnabled else { return }
        appUpdate.state = .checking
        controller.checkForUpdates()
    }

    // MARK: Plugin track

    func checkPlugin() async {
        pluginUpdate.state = .checking
        let result = await pluginChecker.check()
        applyPluginResult(result)
    }

    private func applyPluginResult(_ result: PluginUpdateResult) {
        pluginUpdate.currentVersion = result.installed
        pluginUpdate.latestVersion = result.latest
        pluginChangelogURL = result.changelogURL
        if let error = result.error {
            pluginUpdate.state = .error(error)
        } else if result.installed == nil {
            pluginUpdate.state = .idle          // nothing installed → row hidden
        } else if result.isUpdateAvailable {
            pluginUpdate.state = .available
        } else {
            pluginUpdate.state = .upToDate
        }
    }

    // MARK: Triggers

    func check(_ track: UpdateTrack) {
        switch track {
        case .app:
            checkApp()
        case .plugin:
            pluginTask?.cancel()
            pluginTask = Task { [weak self] in await self?.checkPlugin() }
        }
    }

    func checkAll() {
        UpdateTrack.allCases.forEach(check)
    }

    /// On launch only the plugin is checked here; Sparkle runs its own
    /// launch + daily schedule for the app track.
    func startLaunchChecks() {
        check(.plugin)
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Expected: `Test run with 8 tests passed`, `TEST SUCCEEDED`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/Updates/UpdateService.swift ScoutTests/Services/Updates/UpdateServiceTests.swift
git commit -m "feat(updates): UpdateService — two-track (app/plugin) update state, Sparkle-free

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `PluginManifests` — pure parsing of the Claude Code plugin manifests (+ fixtures)

**Files:**
- Create: `Scout/Services/Updates/PluginManifests.swift`
- Create fixtures: `ScoutTests/Fixtures/plugins-installed.json`, `ScoutTests/Fixtures/plugins-marketplaces-github.json`, `ScoutTests/Fixtures/plugins-marketplaces-git.json`, `ScoutTests/Fixtures/plugins-manifest.json`
- Test: `ScoutTests/Services/Updates/PluginManifestsTests.swift` (create)

**Interfaces:**
- Consumes: `SemVer` (Task 4).
- Produces (used by Task 7):

```swift
enum PluginManifests {
    static let installedKey = "scout@scout-plugin"
    static let marketplaceKey = "scout-plugin"
    enum MarketplaceSource: Equatable, Sendable { case github(repo: String), git(url: String), directory(path: String), unsupported(String) }
    static func installedVersion(from data: Data) -> String?
    static func installedVersionFromCache(directory: URL) -> String?
    static func marketplaceSource(from data: Data) -> MarketplaceSource?
    static func manifestVersion(from data: Data) -> String?
    static func latestManifestURL(for source: MarketplaceSource) -> URL?
    static func changelogURL(for source: MarketplaceSource) -> URL?
}
```

- [ ] **Step 1: Fixtures (anonymized — no real paths, SHAs, or orgs)**

`ScoutTests/Fixtures/plugins-installed.json` — the real file's shape (`installed_plugins.json`, schema `version: 2`):

```json
{
  "version": 2,
  "plugins": {
    "other@example-marketplace": [
      {
        "version": "1.2.3",
        "installPath": "/Users/alex/.claude/plugins/cache/example-marketplace/other/1.2.3",
        "gitCommitSha": "1111111111111111111111111111111111111111",
        "installedAt": "2026-05-01T09:00:00.000Z",
        "lastUpdated": "2026-05-01T09:00:00.000Z"
      }
    ],
    "scout@scout-plugin": [
      {
        "version": "0.7.2",
        "installPath": "/Users/alex/.claude/plugins/cache/scout-plugin/scout/0.7.2",
        "gitCommitSha": "0123456789abcdef0123456789abcdef01234567",
        "installedAt": "2026-05-09T11:18:42.951Z",
        "lastUpdated": "2026-07-13T19:43:57.613Z"
      }
    ]
  }
}
```

`ScoutTests/Fixtures/plugins-marketplaces-github.json` (`known_marketplaces.json`, end-user shape):

```json
{
  "example-marketplace": {
    "source": { "source": "github", "repo": "example-org/other" },
    "installLocation": "/Users/alex/.claude/plugins/marketplaces/example-marketplace",
    "lastUpdated": "2026-07-13T19:43:40.507Z"
  },
  "scout-plugin": {
    "source": { "source": "github", "repo": "example-org/scout-plugin" },
    "installLocation": "/Users/alex/.claude/plugins/marketplaces/scout-plugin",
    "lastUpdated": "2026-07-13T19:43:40.507Z"
  }
}
```

`ScoutTests/Fixtures/plugins-marketplaces-git.json`:

```json
{
  "scout-plugin": {
    "source": { "source": "git", "url": "https://github.com/example-org/scout-plugin.git" },
    "installLocation": "/Users/alex/.claude/plugins/marketplaces/scout-plugin",
    "lastUpdated": "2026-07-13T19:43:40.507Z"
  }
}
```

`ScoutTests/Fixtures/plugins-manifest.json` (`.claude-plugin/plugin.json`):

```json
{
  "name": "scout",
  "version": "0.8.0",
  "description": "Example plugin manifest used by PluginManifestsTests.",
  "author": { "name": "Alex" },
  "homepage": "https://github.com/example-org/scout-plugin",
  "repository": "https://github.com/example-org/scout-plugin"
}
```

- [ ] **Step 2: Failing tests**

Create `ScoutTests/Services/Updates/PluginManifestsTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("PluginManifests")
struct PluginManifestsTests {
    private func fixture(_ name: String) throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"),
                               "fixture \(name).json missing from the test bundle")
        return try Data(contentsOf: url)
    }

    // MARK: installed_plugins.json

    @Test func readsInstalledVersionForTheScoutEntry() throws {
        #expect(PluginManifests.installedVersion(from: try fixture("plugins-installed")) == "0.7.2")
    }

    @Test func installedVersionIsNilWhenEntryMissingOrMalformed() throws {
        #expect(PluginManifests.installedVersion(from: Data(#"{"version":2,"plugins":{}}"#.utf8)) == nil)
        #expect(PluginManifests.installedVersion(from: Data(#"{"version":2,"plugins":{"scout@scout-plugin":[]}}"#.utf8)) == nil)
        #expect(PluginManifests.installedVersion(from: Data(#"{"plugins":{"scout@scout-plugin":[{"installPath":"x"}]}}"#.utf8)) == nil)
        #expect(PluginManifests.installedVersion(from: Data("not json".utf8)) == nil)
    }

    @Test func cacheFallbackPicksTheNewestSemverDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-plugin-cache-\(UUID().uuidString)")
        for dir in ["0.7.2", "0.7.10", "0.8.0-rc.1", "notes.txt"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(dir),
                                                    withIntermediateDirectories: true)
        }
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(PluginManifests.installedVersionFromCache(directory: root) == "0.8.0-rc.1")
        #expect(PluginManifests.installedVersionFromCache(directory: root.appendingPathComponent("missing")) == nil)
    }

    // MARK: known_marketplaces.json

    @Test func readsGithubGitAndDirectorySources() throws {
        #expect(PluginManifests.marketplaceSource(from: try fixture("plugins-marketplaces-github"))
                == .github(repo: "example-org/scout-plugin"))
        #expect(PluginManifests.marketplaceSource(from: try fixture("plugins-marketplaces-git"))
                == .git(url: "https://github.com/example-org/scout-plugin.git"))
        let dir = Data(#"{"scout-plugin":{"source":{"source":"directory","path":"/Users/alex/scout-plugin"}}}"#.utf8)
        #expect(PluginManifests.marketplaceSource(from: dir) == .directory(path: "/Users/alex/scout-plugin"))
        let odd = Data(#"{"scout-plugin":{"source":{"source":"npm","package":"x"}}}"#.utf8)
        #expect(PluginManifests.marketplaceSource(from: odd) == .unsupported("npm"))
        #expect(PluginManifests.marketplaceSource(from: Data(#"{"other":{}}"#.utf8)) == nil)
    }

    // MARK: plugin.json

    @Test func readsManifestVersion() throws {
        #expect(PluginManifests.manifestVersion(from: try fixture("plugins-manifest")) == "0.8.0")
        #expect(PluginManifests.manifestVersion(from: Data(#"{"name":"scout"}"#.utf8)) == nil)
    }

    // MARK: URL builders

    @Test func latestManifestURLPerSource() {
        #expect(PluginManifests.latestManifestURL(for: .github(repo: "example-org/scout-plugin"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout-plugin/HEAD/.claude-plugin/plugin.json")
        #expect(PluginManifests.latestManifestURL(for: .git(url: "https://github.com/example-org/scout-plugin.git"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout-plugin/HEAD/.claude-plugin/plugin.json")
        #expect(PluginManifests.latestManifestURL(for: .git(url: "git@github.com:example-org/scout-plugin.git"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout-plugin/HEAD/.claude-plugin/plugin.json")
        #expect(PluginManifests.latestManifestURL(for: .directory(path: "/Users/alex/scout-plugin"))
                == URL(fileURLWithPath: "/Users/alex/scout-plugin/.claude-plugin/plugin.json"))
        #expect(PluginManifests.latestManifestURL(for: .git(url: "https://gitlab.example.com/x/y.git")) == nil)
        #expect(PluginManifests.latestManifestURL(for: .unsupported("npm")) == nil)
    }

    @Test func changelogURLOnlyForGitHubSources() {
        #expect(PluginManifests.changelogURL(for: .github(repo: "example-org/scout-plugin"))?.absoluteString
                == "https://github.com/example-org/scout-plugin/blob/HEAD/CHANGELOG.md")
        #expect(PluginManifests.changelogURL(for: .directory(path: "/Users/alex/scout-plugin")) == nil)
    }
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PluginManifestsTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — `cannot find 'PluginManifests' in scope`.

- [ ] **Step 4: Implement**

Create `Scout/Services/Updates/PluginManifests.swift`:

```swift
import Foundation

/// Pure readers for the Claude Code plugin manifests the plugin track relies
/// on. All take `Data` (or a directory URL) so tests use fixtures, never the
/// real `~/.claude`. Lenient JSON (JSONSerialization) — unknown keys are fine,
/// anything malformed yields nil rather than a crash.
///
/// Layout (Claude Code, schema version 2):
///   ~/.claude/plugins/installed_plugins.json
///       .plugins["scout@scout-plugin"][0].version   ← authoritative installed version
///   ~/.claude/plugins/known_marketplaces.json
///       ["scout-plugin"].source  = {source: "github", repo} | {source: "git", url}
///                                | {source: "directory", path}
///   <plugin root>/.claude-plugin/plugin.json .version   ← "latest"
enum PluginManifests {
    static let installedKey = "scout@scout-plugin"
    static let marketplaceKey = "scout-plugin"
    static let manifestRelativePath = ".claude-plugin/plugin.json"

    enum MarketplaceSource: Equatable, Sendable {
        case github(repo: String)     // "owner/repo"
        case git(url: String)
        case directory(path: String)
        case unsupported(String)
    }

    // MARK: installed_plugins.json

    static func installedVersion(from data: Data) -> String? {
        guard let root = json(data),
              let plugins = root["plugins"] as? [String: Any],
              let entries = plugins[installedKey] as? [[String: Any]],
              let version = entries.first?["version"] as? String,
              !version.isEmpty
        else { return nil }
        return version
    }

    /// Fallback when the installed entry is missing: the newest semver-named
    /// directory under `~/.claude/plugins/cache/scout-plugin/scout/`.
    static func installedVersionFromCache(directory: URL) -> String? {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path) else { return nil }
        return names.compactMap(SemVer.init).max()?.description
    }

    // MARK: known_marketplaces.json

    static func marketplaceSource(from data: Data) -> MarketplaceSource? {
        guard let root = json(data),
              let entry = root[marketplaceKey] as? [String: Any],
              let source = entry["source"] as? [String: Any],
              let kind = source["source"] as? String
        else { return nil }
        switch kind {
        case "github":
            guard let repo = source["repo"] as? String, !repo.isEmpty else { return nil }
            return .github(repo: repo)
        case "git":
            guard let url = source["url"] as? String, !url.isEmpty else { return nil }
            return .git(url: url)
        case "directory":
            guard let path = source["path"] as? String, !path.isEmpty else { return nil }
            return .directory(path: path)
        default:
            return .unsupported(kind)
        }
    }

    // MARK: plugin.json

    static func manifestVersion(from data: Data) -> String? {
        guard let root = json(data), let version = root["version"] as? String, !version.isEmpty else { return nil }
        return version
    }

    // MARK: URL builders

    /// Where "latest" lives for a source. GitHub-hosted sources read the raw
    /// manifest at `HEAD` (the default branch) — no API call needed. Directory
    /// sources (dev checkouts) read the working copy. Anything else → nil.
    static func latestManifestURL(for source: MarketplaceSource) -> URL? {
        switch source {
        case .github(let repo):
            return URL(string: "https://raw.githubusercontent.com/\(repo)/HEAD/\(manifestRelativePath)")
        case .git(let url):
            guard let repo = githubRepo(fromGitURL: url) else { return nil }
            return URL(string: "https://raw.githubusercontent.com/\(repo)/HEAD/\(manifestRelativePath)")
        case .directory(let path):
            return URL(fileURLWithPath: path).appendingPathComponent(manifestRelativePath)
        case .unsupported:
            return nil
        }
    }

    static func changelogURL(for source: MarketplaceSource) -> URL? {
        switch source {
        case .github(let repo):
            return URL(string: "https://github.com/\(repo)/blob/HEAD/CHANGELOG.md")
        case .git(let url):
            guard let repo = githubRepo(fromGitURL: url) else { return nil }
            return URL(string: "https://github.com/\(repo)/blob/HEAD/CHANGELOG.md")
        case .directory, .unsupported:
            return nil
        }
    }

    /// `https://github.com/o/r(.git)` or `git@github.com:o/r(.git)` → `o/r`.
    static func githubRepo(fromGitURL url: String) -> String? {
        var path: Substring
        if url.hasPrefix("git@github.com:") {
            path = url.dropFirst("git@github.com:".count)
        } else if let components = URLComponents(string: url),
                  components.host?.lowercased() == "github.com" {
            path = Substring(components.path.drop(while: { $0 == "/" }))
        } else {
            return nil
        }
        if path.hasSuffix(".git") { path = path.dropLast(4) }
        let parts = path.split(separator: "/")
        guard parts.count == 2, !parts[0].isEmpty, !parts[1].isEmpty else { return nil }
        return "\(parts[0])/\(parts[1])"
    }

    private static func json(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}
```

- [ ] **Step 5: Run to verify it passes**

Expected: `Test run with 8 tests passed`, `TEST SUCCEEDED`. If a fixture is reported missing, confirm the file names are unique in `ScoutTests/Fixtures/` (they land flattened in the bundle root).

- [ ] **Step 6: Commit**

```bash
git add Scout/Services/Updates/PluginManifests.swift ScoutTests/Services/Updates/PluginManifestsTests.swift ScoutTests/Fixtures/plugins-*.json
git commit -m "feat(updates): PluginManifests — installed/marketplace/manifest readers + anonymized fixtures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `PluginUpdateChecker` — source-aware latest resolution behind a fetcher protocol

**Files:**
- Create: `Scout/Services/Updates/PluginUpdateChecker.swift`
- Test: `ScoutTests/Services/Updates/PluginUpdateCheckerTests.swift` (create)

**Interfaces:**
- Consumes: `PluginManifests` (Task 6), `SemVer` (Task 4), `PluginUpdateChecking` / `PluginUpdateResult` (Task 5).
- Produces:

```swift
protocol RemoteDataFetcher: Sendable { func data(from url: URL) async throws -> Data }
struct URLSessionFetcher: RemoteDataFetcher { init() }
struct PluginUpdateChecker: PluginUpdateChecking {
    init(installedPluginsURL: URL, knownMarketplacesURL: URL, cacheDirectory: URL, fetcher: any RemoteDataFetcher)
    static func standard(fetcher: any RemoteDataFetcher = URLSessionFetcher()) -> PluginUpdateChecker  // ~/.claude/plugins
    func check() async -> PluginUpdateResult
}
```

- [ ] **Step 1: Failing tests**

Create `ScoutTests/Services/Updates/PluginUpdateCheckerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

private struct StubFetcher: RemoteDataFetcher {
    var responses: [URL: Data] = [:]
    var error: Error?
    func data(from url: URL) async throws -> Data {
        if let error { throw error }
        guard let data = responses[url] else { throw URLError(.fileDoesNotExist) }
        return data
    }
}

@Suite("PluginUpdateChecker")
struct PluginUpdateCheckerTests {
    private func fixtureURL(_ name: String) throws -> URL {
        try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"))
    }
    private let rawManifest = URL(string: "https://raw.githubusercontent.com/example-org/scout-plugin/HEAD/.claude-plugin/plugin.json")!
    private func manifest(_ version: String) -> Data { Data(#"{"name":"scout","version":"\#(version)"}"#.utf8) }
    private func tempDir() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("puc-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @Test func githubSourceReportsAvailableWhenRemoteIsNewer() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher(responses: [rawManifest: manifest("0.8.0")]))
        let result = await checker.check()
        #expect(result == PluginUpdateResult(
            installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true,
            changelogURL: URL(string: "https://github.com/example-org/scout-plugin/blob/HEAD/CHANGELOG.md"),
            error: nil))
    }

    @Test func githubSourceUpToDate() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher(responses: [rawManifest: manifest("0.7.2")]))
        let result = await checker.check()
        #expect(result.isUpdateAvailable == false)
        #expect(result.latest == "0.7.2")
        #expect(result.error == nil)
    }

    @Test func gitSourceUsesTheSameRawURL() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-git"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher(responses: [rawManifest: manifest("0.9.0")]))
        #expect(await checker.check().latest == "0.9.0")
    }

    @Test func directorySourceReadsTheWorkingCopy() async throws {
        let plugin = try tempDir()
        try FileManager.default.createDirectory(at: plugin.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try manifest("0.8.1").write(to: plugin.appendingPathComponent(".claude-plugin/plugin.json"))
        let marketplaces = try tempDir().appendingPathComponent("known_marketplaces.json")
        try Data(#"{"scout-plugin":{"source":{"source":"directory","path":"\#(plugin.path)"}}}"#.utf8).write(to: marketplaces)

        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: marketplaces,
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher())                // must never be called
        let result = await checker.check()
        #expect(result.latest == "0.8.1")
        #expect(result.isUpdateAvailable)
        #expect(result.changelogURL == nil)
    }

    @Test func networkFailureIsAnErrorNotAFalseUpToDate() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher(error: URLError(.notConnectedToInternet)))
        let result = await checker.check()
        #expect(result.installed == "0.7.2")
        #expect(result.latest == nil)
        #expect(result.isUpdateAvailable == false)
        #expect(result.error != nil)
    }

    @Test func unparsableRemoteVersionIsAnError() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher(responses: [rawManifest: manifest("latest")]))
        let result = await checker.check()
        #expect(result.isUpdateAvailable == false)
        #expect(result.error?.contains("latest") == true)
    }

    @Test func notInstalledMeansNilVersionsAndNoError() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try tempDir().appendingPathComponent("missing.json"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: try tempDir(),                     // empty → no fallback either
            fetcher: StubFetcher(responses: [rawManifest: manifest("0.8.0")]))
        let result = await checker.check()
        #expect(result == PluginUpdateResult(installed: nil, latest: nil, isUpdateAvailable: false, changelogURL: nil, error: nil))
    }

    @Test func cacheDirectoryIsTheInstalledFallback() async throws {
        let cache = try tempDir()
        try FileManager.default.createDirectory(at: cache.appendingPathComponent("0.7.5"), withIntermediateDirectories: true)
        let checker = PluginUpdateChecker(
            installedPluginsURL: try tempDir().appendingPathComponent("missing.json"),
            knownMarketplacesURL: try fixtureURL("plugins-marketplaces-github"),
            cacheDirectory: cache,
            fetcher: StubFetcher(responses: [rawManifest: manifest("0.8.0")]))
        let result = await checker.check()
        #expect(result.installed == "0.7.5")
        #expect(result.isUpdateAvailable)
    }

    @Test func missingMarketplaceEntryIsAnError() async throws {
        let checker = PluginUpdateChecker(
            installedPluginsURL: try fixtureURL("plugins-installed"),
            knownMarketplacesURL: try tempDir().appendingPathComponent("missing.json"),
            cacheDirectory: try tempDir(),
            fetcher: StubFetcher())
        let result = await checker.check()
        #expect(result.installed == "0.7.2")
        #expect(result.error != nil)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PluginUpdateCheckerTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — `cannot find type 'RemoteDataFetcher' in scope`.

- [ ] **Step 3: Implement**

Create `Scout/Services/Updates/PluginUpdateChecker.swift`:

```swift
import Foundation

/// One HTTPS GET, injectable so tests never touch the network.
protocol RemoteDataFetcher: Sendable {
    func data(from url: URL) async throws -> Data
}

struct URLSessionFetcher: RemoteDataFetcher {
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    func data(from url: URL) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode) from \(url.host ?? url.absoluteString)"])
        }
        return data
    }
}

/// Plugin track: is the installed scout-plugin behind its source?
///
/// Installed: `installed_plugins.json` (fallback: newest semver dir in the
/// plugin cache). Latest: source-aware — GitHub raw manifest for `github`/`git`
/// sources (end users), the working copy for `directory` sources (dev
/// checkouts). Never throws; every failure becomes `PluginUpdateResult.error`
/// and is shown only in Settings.
struct PluginUpdateChecker: PluginUpdateChecking {
    let installedPluginsURL: URL
    let knownMarketplacesURL: URL
    let cacheDirectory: URL
    let fetcher: any RemoteDataFetcher

    /// The real `~/.claude/plugins` layout.
    static func standard(fetcher: any RemoteDataFetcher = URLSessionFetcher()) -> PluginUpdateChecker {
        let plugins = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude").appendingPathComponent("plugins")
        return PluginUpdateChecker(
            installedPluginsURL: plugins.appendingPathComponent("installed_plugins.json"),
            knownMarketplacesURL: plugins.appendingPathComponent("known_marketplaces.json"),
            cacheDirectory: plugins.appendingPathComponent("cache/scout-plugin/scout"),
            fetcher: fetcher)
    }

    func check() async -> PluginUpdateResult {
        guard let installed = installedVersion() else {
            // Nothing installed (or nothing we can read): not an error, the
            // UI hides the plugin row.
            return PluginUpdateResult(installed: nil, latest: nil, isUpdateAvailable: false, changelogURL: nil, error: nil)
        }
        var result = PluginUpdateResult(installed: installed, latest: nil, isUpdateAvailable: false, changelogURL: nil, error: nil)

        guard let marketplaceData = try? Data(contentsOf: knownMarketplacesURL),
              let source = PluginManifests.marketplaceSource(from: marketplaceData) else {
            result.error = "Couldn't read the scout-plugin marketplace entry."
            return result
        }
        result.changelogURL = PluginManifests.changelogURL(for: source)

        guard let manifestURL = PluginManifests.latestManifestURL(for: source) else {
            result.error = "Unsupported plugin source (\(source))."
            return result
        }

        let manifestData: Data
        do {
            manifestData = manifestURL.isFileURL
                ? try Data(contentsOf: manifestURL)
                : try await fetcher.data(from: manifestURL)
        } catch {
            result.error = "Couldn't check the latest plugin version: \(error.localizedDescription)"
            return result
        }

        guard let latest = PluginManifests.manifestVersion(from: manifestData) else {
            result.error = "The latest plugin manifest has no version."
            return result
        }
        result.latest = latest

        guard let installedSemVer = SemVer(installed), let latestSemVer = SemVer(latest) else {
            result.error = "Couldn't compare plugin versions (\(installed) vs \(latest))."
            return result
        }
        result.isUpdateAvailable = installedSemVer < latestSemVer
        return result
    }

    private func installedVersion() -> String? {
        if let data = try? Data(contentsOf: installedPluginsURL),
           let version = PluginManifests.installedVersion(from: data) {
            return version
        }
        return PluginManifests.installedVersionFromCache(directory: cacheDirectory)
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Expected: `Test run with 9 tests passed`, `TEST SUCCEEDED`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/Updates/PluginUpdateChecker.swift ScoutTests/Services/Updates/PluginUpdateCheckerTests.swift
git commit -m "feat(updates): PluginUpdateChecker — source-aware latest resolution behind a fetcher protocol

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Sparkle adapter (`AppUpdater`), feed override, app-menu command, `ScoutApp` wiring

**Files:**
- Create: `Scout/Services/Updates/UpdateFeed.swift`
- Create: `Scout/Services/Updates/AppUpdater.swift`
- Create: `Scout/Shell/CheckForUpdatesView.swift`
- Modify: `Scout/ScoutApp.swift` (whole file, 31 lines)
- Test: `ScoutTests/Services/Updates/UpdateFeedTests.swift`, `ScoutTests/Services/Updates/AppUpdaterTests.swift` (create)

**Interfaces:**
- Consumes: `AppUpdateController`, `AppUpdateEvent`, `UpdateService` (Task 5); `Sparkle` (Task 1).
- Produces:

```swift
enum UpdateFeed { static let overrideEnvironmentKey = "SCOUT_APPCAST_URL"
                  nonisolated static func overrideURLString(environment: [String: String]) -> String? }
@MainActor final class AppUpdater: AppUpdateController {
    static let updatesEnabledForThisBuild: Bool
    init(enabled: Bool = AppUpdater.updatesEnabledForThisBuild, onEvent: @escaping @MainActor (AppUpdateEvent) -> Void)
}
struct CheckForUpdatesView: View { @ObservedObject var updates: UpdateService }
```

- [ ] **Step 1: Failing tests**

Create `ScoutTests/Services/Updates/UpdateFeedTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("UpdateFeed override (SCOUT_APPCAST_URL)")
struct UpdateFeedTests {
    private func override(_ value: String?) -> String? {
        var env: [String: String] = [:]
        if let value { env[UpdateFeed.overrideEnvironmentKey] = value }
        return UpdateFeed.overrideURLString(environment: env)
    }

    @Test func keyNameIsStable() { #expect(UpdateFeed.overrideEnvironmentKey == "SCOUT_APPCAST_URL") }

    @Test func httpsOverrideIsReturnedVerbatim() {
        let url = "https://github.com/example-org/repo/releases/download/v0.12.0-rc.2/appcast.xml"
        #expect(override(url) == url)
    }

    @Test func missingBlankHTTPAndNonURLsAreRejected() {
        #expect(override(nil) == nil)
        #expect(override("") == nil)
        #expect(override("  \n") == nil)
        #expect(override("http://localhost:8000/appcast.xml") == nil)
        #expect(override("HTTP://example.com/appcast.xml") == nil)
        #expect(override("not a url") == nil)
        #expect(override("https://") == nil)
        #expect(override("file:///tmp/appcast.xml") == nil)
    }

    @Test func surroundingWhitespaceIsTrimmed() {
        #expect(override(" https://example.com/appcast.xml\n") == "https://example.com/appcast.xml")
    }
}
```

Create `ScoutTests/Services/Updates/AppUpdaterTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// Runs in the Debug test host, where the updater must never start. The
/// enabled path needs a signed Release build and a real appcast — that is the
/// rc.1 → rc.2 rehearsal in the spec, not a unit test.
@Suite("AppUpdater (disabled build)")
@MainActor
struct AppUpdaterTests {
    @Test func debugBuildsAreDisabledByDefault() {
        #expect(AppUpdater.updatesEnabledForThisBuild == false)
        #expect(AppUpdater(onEvent: { _ in }).isEnabled == false)
    }

    @Test func reportsTheBundleVersion() {
        let expected = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
        #expect(AppUpdater(enabled: false, onEvent: { _ in }).currentVersion == expected)
    }

    @Test func checkForUpdatesIsANoOpWhenDisabled() {
        var events: [AppUpdateEvent] = []
        let updater = AppUpdater(enabled: false) { events.append($0) }
        updater.checkForUpdates()   // must not start Sparkle, show UI, emit, or crash
        #expect(events.isEmpty)
    }
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateFeedTests -only-testing:ScoutTests/AppUpdaterTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — `cannot find 'UpdateFeed' in scope`, `cannot find 'AppUpdater' in scope`.

- [ ] **Step 3: Implement `UpdateFeed`**

Create `Scout/Services/Updates/UpdateFeed.swift`:

```swift
import Foundation

/// Where the app looks for updates.
///
/// The production feed is baked into Info.plist (`SUFeedURL`). For end-to-end
/// testing of a release candidate against a pre-release appcast, the feed can
/// be overridden per process with the `SCOUT_APPCAST_URL` environment
/// variable:
///
///     open -a /path/to/Scout.app --env SCOUT_APPCAST_URL=https://…/appcast.xml
///
/// Only well-formed `https` URLs are honored; anything else falls back to
/// `SUFeedURL`. The override cannot weaken security: Sparkle still requires
/// the enclosure to carry a valid EdDSA signature (private key on the release
/// machine) and the downloaded app to be signed by the same Developer ID team.
enum UpdateFeed {
    static let overrideEnvironmentKey = "SCOUT_APPCAST_URL"

    nonisolated static func overrideURLString(environment: [String: String]) -> String? {
        guard let raw = environment[overrideEnvironmentKey]?
                .trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty,
              let url = URL(string: raw),
              url.scheme?.lowercased() == "https",
              let host = url.host, !host.isEmpty
        else { return nil }
        return raw
    }
}
```

- [ ] **Step 4: Implement `AppUpdater`**

Create `Scout/Services/Updates/AppUpdater.swift`:

```swift
import Foundation
import Sparkle

/// Thin adapter around Sparkle's standard updater controller. The only file
/// that imports Sparkle. Sparkle owns detection, download, install and
/// relaunch (and shows its own dialogs); this class forwards "check now" and
/// maps delegate callbacks onto `AppUpdateEvent`s for `UpdateService`.
///
/// Disabled in Debug builds: a dev build lives in DerivedData under
/// `com.scout.Scout.dev`, and letting it replace itself with the release
/// `com.scout.Scout` bundle would be confusing at best. The controller is
/// still constructed — so this code compiles and runs in Debug/CI — it is
/// just never started.
@MainActor
final class AppUpdater: AppUpdateController {
    /// True in Release builds only. The single `#if DEBUG` in the updater.
    static let updatesEnabledForThisBuild: Bool = {
        #if DEBUG
        return false
        #else
        return true
        #endif
    }()

    let isEnabled: Bool
    private let controller: SPUStandardUpdaterController
    private let delegate: AppUpdaterDelegate

    init(enabled: Bool = AppUpdater.updatesEnabledForThisBuild,
         onEvent: @escaping @MainActor (AppUpdateEvent) -> Void) {
        isEnabled = enabled
        delegate = AppUpdaterDelegate(onEvent: onEvent)
        controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: delegate,
            userDriverDelegate: nil
        )
        if enabled {
            controller.startUpdater()
        }
    }

    var currentVersion: String? {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
    }

    /// User-initiated: Sparkle shows "checking…", then found / up to date /
    /// error UI itself. Scheduled checks are Sparkle's own (launch + daily).
    func checkForUpdates() {
        guard isEnabled else { return }
        controller.updater.checkForUpdates()
    }
}

/// Sparkle calls its delegate on the main thread; we assert that and hop the
/// result into the service. Sparkle holds the delegate weakly, so
/// `AppUpdater` keeps the strong reference.
final class AppUpdaterDelegate: NSObject, SPUUpdaterDelegate {
    private let onEvent: @MainActor (AppUpdateEvent) -> Void

    init(onEvent: @escaping @MainActor (AppUpdateEvent) -> Void) {
        self.onEvent = onEvent
    }

    private nonisolated func emit(_ event: AppUpdateEvent) {
        MainActor.assumeIsolated { onEvent(event) }
    }

    nonisolated func feedURLString(for updater: SPUUpdater) -> String? {
        UpdateFeed.overrideURLString(environment: ProcessInfo.processInfo.environment)
    }

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        emit(.found(version: item.displayVersionString))
    }

    nonisolated func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: any Error) {
        emit(.upToDate)
    }

    nonisolated func updater(_ updater: SPUUpdater, didAbortWithError error: any Error) {
        // Sparkle reports user cancellation through this hook too; keep the
        // Settings row honest but never alarming — it's shown as "Couldn't
        // check", Sparkle already showed any dialog it wanted to.
        emit(.failed(error.localizedDescription))
    }
}
```

If the compiler rejects the protocol conformance under default MainActor isolation, declare `final class AppUpdaterDelegate: NSObject, @preconcurrency SPUUpdaterDelegate` and keep the methods `nonisolated`.

- [ ] **Step 5: Menu item view + `ScoutApp` wiring**

Create `Scout/Shell/CheckForUpdatesView.swift`:

```swift
import SwiftUI

/// "Check for Updates…" menu item, shared by the app menu (under About Scout)
/// and the menu-bar extra. Disabled while a check is in flight or in Debug
/// builds, where the updater never starts.
struct CheckForUpdatesView: View {
    @ObservedObject var updates: UpdateService

    var body: some View {
        Button("Check for Updates…") { updates.check(.app) }
            .disabled(!updates.appUpdatesEnabled || updates.appUpdate.state == .checking)
    }
}
```

Replace `Scout/ScoutApp.swift` with:

```swift
import SwiftUI

@main
struct ScoutApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var appState = AppState()
    /// Both update tracks. The Sparkle adapter is built by the service so it
    /// can hand the adapter its event sink without a retain cycle.
    @StateObject private var updates = UpdateService(
        pluginChecker: PluginUpdateChecker.standard(),
        makeAppController: { AppUpdater(onEvent: $0) }
    )

    var body: some Scene {
        WindowGroup("Scout") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(appState.proposalsDocumentService)
                .environmentObject(updates)
                .frame(minWidth: 1100, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }  // suppress File > New Window
            CommandGroup(after: .appInfo) {        // Scout ▸ Check for Updates… (under About Scout)
                CheckForUpdatesView(updates: updates)
            }
        }

        MenuBarExtra {
            MenuBarExtraContent()
                .environmentObject(appState)
                .environmentObject(updates)
        } label: {
            MenuBarIcon(status: appState.menuBarStatus, updateAvailable: updates.anyUpdateAvailable)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(updates)
        }
    }
}
```

`MenuBarIcon(status:updateAvailable:)` is added in Task 10; until then keep `MenuBarIcon(status: appState.menuBarStatus)` on that line so this task builds — Task 10 swaps it.

- [ ] **Step 6: Run the two suites + the whole target**

Run the selector from Step 2. Expected: `Test run with 7 tests passed`, `TEST SUCCEEDED`. Then `-only-testing:ScoutTests` → `TEST SUCCEEDED`.

Launch the Debug app once (`open "$(find ~/Library/Developer/Xcode/DerivedData -path '*/Build/Products/Debug/Scout.app' -maxdepth 6 | head -1)"`): the **Scout Dev** menu shows *Check for Updates…* greyed out under *About Scout*. Quit it afterwards.

- [ ] **Step 7: Commit**

```bash
git add Scout/Services/Updates/UpdateFeed.swift Scout/Services/Updates/AppUpdater.swift Scout/Shell/CheckForUpdatesView.swift Scout/ScoutApp.swift ScoutTests/Services/Updates/UpdateFeedTests.swift ScoutTests/Services/Updates/AppUpdaterTests.swift
git commit -m "feat(updates): Sparkle adapter (never starts in Debug), https-only feed override, Check for Updates… menu command

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Settings ▸ Updates section

**Files:**
- Modify: `Scout/Shell/SettingsView.swift` (properties lines 12–24; insert a section before `section(label: "About")` at line 178; new private views at the end of the file)

**Interfaces:**
- Consumes: `UpdateService` (Task 5) as `@EnvironmentObject`.

- [ ] **Step 1: Add the environment object**

After `@State private var detectedClaudePath: String?` (line 24):

```swift
    @EnvironmentObject private var updates: UpdateService
```

- [ ] **Step 2: Insert the section before `section(label: "About") {`**

```swift
                section(label: "Updates") {
                    SettingsCard {
                        UpdateTrackRow(
                            title: "Scout.app",
                            status: updates.appUpdate,
                            disabledNote: updates.appUpdatesEnabled ? nil
                                : "Automatic updates are disabled in development builds.",
                            primaryTitle: updates.appUpdate.isAvailable ? "Install…" : nil,
                            primaryAction: { updates.check(.app) },      // Sparkle shows the update dialog
                            checkAction: { updates.check(.app) }
                        )
                        if updates.pluginUpdate.currentVersion != nil {
                            UpdateTrackRow(
                                title: "scout-plugin",
                                status: updates.pluginUpdate,
                                disabledNote: nil,
                                primaryTitle: updates.pluginUpdate.isAvailable ? "Copy /scout-update" : nil,
                                primaryAction: copyPluginUpdateCommand,
                                checkAction: { updates.check(.plugin) },
                                footnote: updates.pluginUpdate.isAvailable
                                    ? "Paste it into Claude Code to update the plugin — the app can't apply plugin updates itself."
                                    : nil,
                                linkTitle: updates.pluginUpdate.isAvailable && updates.pluginChangelogURL != nil ? "What's new" : nil,
                                linkAction: { if let url = updates.pluginChangelogURL { NSWorkspace.shared.open(url) } }
                            )
                        }
                    }
                }

```

- [ ] **Step 3: Add the clipboard helper after `bundleId`** (before the closing `}` of `SettingsView`):

```swift
    private func copyPluginUpdateCommand() {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(UpdateService.pluginUpdateCommand, forType: .string)
    }
```

Also add `import AppKit` at the top of the file (for `NSPasteboard` / `NSWorkspace`).

- [ ] **Step 4: Add the row view at the end of the file**

```swift
// MARK: - Updates

/// One update track: title, `current → latest`, a state chip, an optional
/// primary action (Install… / Copy /scout-update), and Check now.
private struct UpdateTrackRow: View {
    let title: String
    let status: UpdateStatus
    let disabledNote: String?
    let primaryTitle: String?
    let primaryAction: () -> Void
    let checkAction: () -> Void
    var footnote: String? = nil
    var linkTitle: String? = nil
    var linkAction: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 8) {
                        Text(title)
                            .font(DS.sans(13, weight: .medium))
                            .foregroundStyle(DS.Ink.p1)
                        chip
                    }
                    Text(disabledNote ?? versionLine)
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                    if let footnote {
                        Text(footnote)
                            .font(DS.sans(11.5))
                            .foregroundStyle(DS.Ink.p3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if disabledNote == nil {
                    HStack(spacing: 8) {
                        if let linkTitle {
                            Button(linkTitle, action: linkAction).buttonStyle(.link)
                        }
                        if let primaryTitle {
                            Button(primaryTitle, action: primaryAction)
                        }
                        Button("Check now", action: checkAction)
                            .disabled(status.state == .checking)
                    }
                }
            }
            .padding(.vertical, 14)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5).opacity(0.6)
        }
    }

    private var versionLine: String {
        let current = status.currentVersion ?? "—"
        switch status.state {
        case .available:
            return "\(current) → \(status.latestVersion ?? "?") available"
        case .upToDate:
            return "\(current) · up to date"
        case .checking:
            return "\(current) · checking…"
        case .error:
            return "\(current) · last check failed"
        case .idle:
            return current
        }
    }

    @ViewBuilder private var chip: some View {
        switch status.state {
        case .available: UpdateChip(text: "Update available", color: DS.Status.warn)
        case .upToDate:  UpdateChip(text: "Up to date",       color: DS.Status.ok)
        case .checking:  UpdateChip(text: "Checking…",        color: DS.Ink.p3)
        case .error:     UpdateChip(text: "Couldn't check",   color: DS.Status.err)
        case .idle:      EmptyView()
        }
    }
}

private struct UpdateChip: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(DS.sans(10.5, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(color.opacity(0.12)))
    }
}
```

- [ ] **Step 5: Build, run the suite, eyeball**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"` → `TEST SUCCEEDED`.

Launch the Debug app, ⌘,: **UPDATES** card shows *Scout.app* with "Automatic updates are disabled in development builds." and no buttons; *scout-plugin* row shows `0.8.0 · up to date` (this machine's `directory` source) with **Check now** — or, if the sibling checkout is ahead of the installed cache, `0.8.0 → x.y.z available`, **Copy /scout-update**, and a **What's new** link only for GitHub sources. Quit afterwards.

- [ ] **Step 6: Commit**

```bash
git add Scout/Shell/SettingsView.swift
git commit -m "feat(updates): Settings ▸ Updates — app + plugin rows with state chips, Install… / Copy /scout-update

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Badges (sidebar + menu-bar icon), menu-bar item, launch check

**Files:**
- Modify: `Scout/Shell/SidebarView.swift:6-28`
- Modify: `Scout/Shell/MenuBarIcon.swift` (whole file, 14 lines)
- Modify: `Scout/Shell/MenuBarExtraContent.swift:4-20`
- Modify: `Scout/Shell/MainWindowView.swift:3-31`
- Modify: `Scout/ScoutApp.swift` (the `MenuBarIcon(...)` line)

**Interfaces:**
- Consumes: `UpdateService.availableCount`, `.anyUpdateAvailable`, `.startLaunchChecks()`, `CheckForUpdatesView` (Tasks 5, 8).
- Produces: `SidebarView(selection:proposalsBadge:wishlistBadge:researchBadge:settingsBadge:)`; `MenuBarIcon(status:updateAvailable:)`.

- [ ] **Step 1: Sidebar badge on the Settings row**

In `SidebarView`, add after `var researchBadge: Int = 0`:

```swift
    /// Number of update tracks (app, plugin) with an update available — drives
    /// the badge on the Settings row, where Settings ▸ Updates lives.
    var settingsBadge: Int = 0
```

and change the Settings row to:

```swift
            row(.settings,      label: "Settings",       system: "gearshape",         badge: settingsBadge)
```

- [ ] **Step 2: Menu-bar icon dot**

Replace `Scout/Shell/MenuBarIcon.swift` with:

```swift
import SwiftUI

struct MenuBarIcon: View {
    let status: AppState.MenuBarStatus
    /// Draws a small dot on the icon when either update track is behind, so
    /// an update reads as a notification even with the window closed.
    var updateAvailable: Bool = false

    var body: some View {
        symbol
            .overlay(alignment: .topTrailing) {
                if updateAvailable {
                    Circle()
                        .fill(.primary)
                        .frame(width: 5, height: 5)
                        .offset(x: 3, y: -2)
                }
            }
    }

    @ViewBuilder private var symbol: some View {
        switch status {
        case .idle:          Image(systemName: "bolt")
        case .running:       Image(systemName: "circle.dotted")
        case .lastFailed:    Image(systemName: "exclamationmark.triangle")
        case .budgetSkipped: Image(systemName: "pause.circle")
        }
    }
}
```

In `Scout/ScoutApp.swift`, the label becomes `MenuBarIcon(status: appState.menuBarStatus, updateAvailable: updates.anyUpdateAvailable)` (already written that way in Task 8 Step 5 — remove the temporary single-argument form if it is still there).

- [ ] **Step 3: Menu-bar extra item**

In `Scout/Shell/MenuBarExtraContent.swift`:

```swift
struct MenuBarExtraContent: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var updates: UpdateService

    var body: some View {
        statusSection
        Divider()
        scheduleSection
        Divider()
        Button("Install wake-schedule…") { installWakeSchedule() }
        Button("Open Control Center") { openMainWindow() }
        Button("Open Scout folder in Finder") {
            let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Scout")
            NSWorkspace.shared.open(url)
        }
        if updates.appUpdatesEnabled {
            // Dev builds never update themselves — don't grow a dead item.
            CheckForUpdatesView(updates: updates)
        }
        if updates.pluginUpdate.isAvailable {
            Button("Plugin update available — open Settings") { openSettings() }
        }
        Divider()
        Button("Quit Scout") { NSApp.terminate(nil) }
    }
```

and add next to `openMainWindow()`:

```swift
    private func openSettings() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }
```

- [ ] **Step 4: Wire the badge count and the launch check in `MainWindowView`**

```swift
struct MainWindowView: View {
    @State private var selection: SidebarItem = .controlCenter
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var proposalsService: ProposalsDocumentService
    @EnvironmentObject var updates: UpdateService

    var body: some View {
        // (existing comment about NavigationSplitView unchanged)
        NavigationSplitView {
            SidebarView(selection: $selection,
                        proposalsBadge: proposalsService.pendingCount,
                        wishlistBadge: appState.wishlistDocumentService.activeCount,
                        researchBadge: appState.researchDocumentService.activeCount,
                        settingsBadge: updates.availableCount)
                .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 240)
        } detail: {
            detail
                .background(PaperBackdrop())
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            StatusBarView(viewLabel: selection.statusLabel)
        }
        .task {
            // Plugin track: check on launch (+ manual). Sparkle runs its own
            // launch/daily schedule for the app track.
            updates.startLaunchChecks()
        }
    }
```

(Everything else in the file unchanged.)

- [ ] **Step 5: Build, full suite, eyeball**

Run the full `-only-testing:ScoutTests` selector → `TEST SUCCEEDED`. Launch the Debug app: with the local plugin checkout at the same version as the installed cache there is no badge; to see the badge, temporarily bump `version` in `~/scout-plugin/.claude-plugin/plugin.json` (e.g. `0.8.0` → `0.8.1`), relaunch: Settings row shows **1**, the menu-bar icon carries a dot, the menu-bar extra shows *Plugin update available — open Settings*, Settings ▸ Updates shows `0.8.0 → 0.8.1 available` with **Copy /scout-update** (clipboard gets `/scout-update`). **Revert the plugin.json edit** (`git -C ~/scout-plugin checkout -- .claude-plugin/plugin.json`). Quit the app.

- [ ] **Step 6: Commit**

```bash
git add Scout/Shell/SidebarView.swift Scout/Shell/MenuBarIcon.swift Scout/Shell/MenuBarExtraContent.swift Scout/Shell/MainWindowView.swift Scout/ScoutApp.swift
git commit -m "feat(updates): update badge on Settings row + menu-bar icon, menu-bar items, plugin check on launch

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: `scripts/release-lib.sh` (pure helpers + bash tests + CI step) and the initial empty `appcast.xml`

**Files:**
- Create: `scripts/release-lib.sh`, `scripts/tests/release-lib.test.sh`, `appcast.xml` (repo root)
- Modify: `.github/workflows/ci.yml:37-41` (add a step after "Toolchain info")

**Interfaces:**
- Produces (sourced by Task 12's `release.sh`): `release_tags`, `recommend_version <latest-tag|""> <subjects>`, `version_kind <version>` → `release|prerelease|invalid`, `html_escape` (stdin→stdout), `render_changelog <md|html> <prev-tag|""> <tag> <owner/repo>` (stdin `subject|hash` lines), `render_appcast <version> <build> <min-os> <tag> <owner/repo> <dmg-filename> <ed-signature> <length> <pubdate>` (stdin HTML notes), `render_empty_appcast <owner/repo>`, `parse_sign_update <sign_update stdout>` → `<sig> <length>` (exit 1 if unparsable).

- [ ] **Step 1: Failing test script**

Create `scripts/tests/release-lib.test.sh`:

```bash
#!/usr/bin/env bash
# Unit tests for scripts/release-lib.sh (pure helpers behind release.sh).
# Run locally or in CI:  bash scripts/tests/release-lib.test.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../release-lib.sh
source "$HERE/../release-lib.sh"

fails=0
assert_eq() {  # assert_eq <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then echo "ok   $1"
  else echo "FAIL $1"; echo "  expected: $2"; echo "  actual:   $3"; fails=$((fails + 1)); fi
}
assert_contains() {  # assert_contains <name> <needle> <haystack>
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"
  else echo "FAIL $1 — missing: $2"; printf '%s\n' "$3"; fails=$((fails + 1)); fi
}
assert_xml() {  # assert_xml <name> <xml>
  if printf '%s' "$2" | xmllint --noout - 2>/dev/null; then echo "ok   $1"
  else echo "FAIL $1 — not well-formed XML"; printf '%s\n' "$2"; fails=$((fails + 1)); fi
}

# ── version_kind ─────────────────────────────────────────────────────────────
assert_eq "release version"           release    "$(version_kind 0.12.0)"
assert_eq "prerelease version"        prerelease "$(version_kind 0.12.0-rc.1)"
assert_eq "invalid: two components"   invalid    "$(version_kind 1.2)"
assert_eq "invalid: v prefix"         invalid    "$(version_kind v1.2.3)"
assert_eq "invalid: empty suffix"     invalid    "$(version_kind 1.2.3-)"

# ── recommend_version ────────────────────────────────────────────────────────
assert_eq "first release"             0.1.0  "$(recommend_version "" "")"
assert_eq "fix bumps patch"           0.11.3 "$(recommend_version v0.11.2 $'fix(kb): x\nchore: y')"
assert_eq "feat bumps minor"          0.12.0 "$(recommend_version v0.11.2 $'fix: x\nfeat(updates): y')"
assert_eq "breaking feat bumps minor" 0.12.0 "$(recommend_version v0.11.2 'feat!: y')"
assert_eq "no commits bumps patch"    0.11.3 "$(recommend_version v0.11.2 "")"

# ── render_changelog (markdown) ───────────────────────────────────────────────
md="$(printf 'feat(a): one|abc1234\nfix(b): two|def5678\nchore: three|9999999\n' \
  | render_changelog md v0.11.2 v0.12.0 example-org/repo)"
assert_contains "md heading"       "## What's changed" "$md"
assert_contains "md features head" "### Features" "$md"
assert_contains "md feature item"  '- feat(a): one (`abc1234`)' "$md"
assert_contains "md fixes head"    "### Fixes" "$md"
assert_contains "md fix item"      '- fix(b): two (`def5678`)' "$md"
assert_contains "md other head"    "### Other changes" "$md"
assert_contains "md compare link"  "**Full changelog**: https://github.com/example-org/repo/compare/v0.11.2...v0.12.0" "$md"

only_fix="$(printf 'fix: x|1111111\n' | render_changelog md v0.11.2 v0.11.3 example-org/repo)"
if [[ "$only_fix" == *"### Features"* ]]; then echo "FAIL md omits empty sections"; fails=$((fails + 1)); else echo "ok   md omits empty sections"; fi

first="$(printf '' | render_changelog md "" v0.1.0 example-org/repo)"
assert_contains "md first release" "_First tagged release._" "$first"
none="$(printf '' | render_changelog md v0.11.2 v0.11.3 example-org/repo)"
assert_contains "md no commits"    '_No commits between `v0.11.2` and `v0.11.3`._' "$none"

# ── render_changelog (html) ───────────────────────────────────────────────────
html="$(printf 'feat: a <b> & c|abc1234\n' | render_changelog html v0.11.2 v0.12.0 example-org/repo)"
assert_contains "html heading"      "<h2>What's changed</h2>" "$html"
assert_contains "html escaped item" "<li>feat: a &lt;b&gt; &amp; c (<code>abc1234</code>)</li>" "$html"
assert_contains "html list tags"    "<ul>" "$html"
assert_contains "html compare link" '<a href="https://github.com/example-org/repo/compare/v0.11.2...v0.12.0">' "$html"

# ── render_appcast ────────────────────────────────────────────────────────────
appcast="$(printf '<h2>What'"'"'s changed</h2>\n<ul><li>x</li></ul>\n' \
  | render_appcast 0.12.0 412 15.7 v0.12.0 example-org/repo Scout-0.12.0.dmg 'c2lnbmF0dXJl' 9012345 'Wed, 02 Sep 2026 20:15:00 +0000')"
assert_xml      "appcast is well-formed XML" "$appcast"
assert_contains "appcast title"          "<title>Scout 0.12.0</title>" "$appcast"
assert_contains "appcast version"        "<sparkle:version>412</sparkle:version>" "$appcast"
assert_contains "appcast short version"  "<sparkle:shortVersionString>0.12.0</sparkle:shortVersionString>" "$appcast"
assert_contains "appcast min os"         "<sparkle:minimumSystemVersion>15.7</sparkle:minimumSystemVersion>" "$appcast"
assert_contains "appcast release link"   "<link>https://github.com/example-org/repo/releases/tag/v0.12.0</link>" "$appcast"
assert_contains "appcast pubdate"        "<pubDate>Wed, 02 Sep 2026 20:15:00 +0000</pubDate>" "$appcast"
assert_contains "appcast enclosure url"  'url="https://github.com/example-org/repo/releases/download/v0.12.0/Scout-0.12.0.dmg"' "$appcast"
assert_contains "appcast signature"      'sparkle:edSignature="c2lnbmF0dXJl"' "$appcast"
assert_contains "appcast length"         'length="9012345"' "$appcast"
assert_contains "appcast notes"          "<h2>What's changed</h2>" "$appcast"

cdata="$(printf 'text with ]]> inside\n' | render_appcast 0.0.1 1 15.7 v0.0.1 example-org/repo Scout-0.0.1.dmg sig 1 'Wed, 02 Sep 2026 20:15:00 +0000')"
assert_xml "appcast survives ]]> in notes" "$cdata"

empty="$(render_empty_appcast example-org/repo)"
assert_xml      "empty appcast is well-formed" "$empty"
assert_contains "empty appcast has channel link" "<link>https://github.com/example-org/repo/releases</link>" "$empty"
if [[ "$empty" == *"<item>"* ]]; then echo "FAIL empty appcast has no items"; fails=$((fails + 1)); else echo "ok   empty appcast has no items"; fi

# ── parse_sign_update ─────────────────────────────────────────────────────────
assert_eq "parse sign_update" "abc/+== 123" "$(parse_sign_update 'sparkle:edSignature="abc/+==" length="123"')"
if parse_sign_update 'garbage' >/dev/null 2>&1; then echo "FAIL parse_sign_update accepted garbage"; fails=$((fails + 1))
else echo "ok   parse_sign_update rejects garbage"; fi

if (( fails > 0 )); then echo "✗ $fails failure(s)"; exit 1; fi
echo "✓ all release-lib tests passed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash scripts/tests/release-lib.test.sh`
Expected: `…/scripts/release-lib.sh: No such file or directory` (exit 1).

- [ ] **Step 3: Implement the library**

Create `scripts/release-lib.sh`:

```bash
#!/usr/bin/env bash
# Pure helpers for scripts/release.sh — sourced, never executed directly.
# Every function is a function of its arguments/stdin (the only git access is
# the read-only `release_tags`), so scripts/tests/release-lib.test.sh can unit
# test them (CI runs it). Keep this /bin/bash 3.2 compatible — macOS ships no
# newer bash.

# Plain vMAJOR.MINOR.PATCH tags, highest first, one per line. Pre-release
# tags (v0.12.0-rc.1) are excluded so they never skew the version rule or
# the changelog range.
release_tags() {
  git tag --list 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true
}

# recommend_version <latest-plain-tag-or-empty> <commit subjects, newline-separated>
# Any `feat:` (optionally scoped / breaking: `feat(kb):`, `feat!:`) bumps the
# minor and zeroes the patch; anything else bumps the patch. No tag → 0.1.0.
recommend_version() {
  local latest="${1:-}" subjects="${2:-}"
  if [[ -z "$latest" ]]; then echo "0.1.0"; return; fi
  local base="${latest#v}" maj rest min pat
  maj="${base%%.*}"; rest="${base#*.}"; min="${rest%%.*}"; pat="${rest#*.}"
  if printf '%s\n' "$subjects" | grep -qE '^feat(\(.*\))?!?:'; then
    echo "${maj}.$((min + 1)).0"
  else
    echo "${maj}.${min}.$((pat + 1))"
  fi
}

# version_kind <version> → release | prerelease | invalid
version_kind() {
  if [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo release
  elif [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.]+$ ]]; then echo prerelease
  else echo invalid; fi
}

# stdin → stdout with & < > escaped for HTML text content.
html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# ── changelog rendering (one grouping, two output formats) ───────────────────
_cl_heading() {  # _cl_heading <md|html> <level> <text>
  if [[ "$1" == html ]]; then
    printf '<h%s>%s</h%s>\n' "$2" "$3" "$2"
  else
    local marks; marks="$(printf '%*s' "$2" '' | tr ' ' '#')"
    printf '%s %s\n\n' "$marks" "$3"
  fi
}
_cl_para() {  # _cl_para <md|html> <text already in that format>
  if [[ "$1" == html ]]; then printf '<p>%s</p>\n' "$2"; else printf '%s\n\n' "$2"; fi
}
_cl_list() {  # _cl_list <md|html> <"subject|hash" lines>
  if [[ "$1" == html ]]; then
    echo "<ul>"
    printf '%s\n' "$2" | while IFS='|' read -r subject hash; do
      [[ -n "$subject" ]] || continue
      printf '<li>%s (<code>%s</code>)</li>\n' "$(printf '%s' "$subject" | html_escape)" "$hash"
    done
    echo "</ul>"
  else
    printf '%s\n' "$2" | awk -F'|' 'NF { printf "- %s (`%s`)\n", $1, $2 }'
    echo
  fi
}

# render_changelog <md|html> <prev-tag-or-empty> <tag> <owner/repo-or-empty>
# stdin: `subject|shorthash` lines (git log --format='%s|%h'); may be empty.
# Emits the "What's changed" body — Markdown for the GitHub release, HTML for
# the appcast <description>. Same grouping either way: feat / fix / other.
render_changelog() {
  local fmt="$1" prev="$2" tag="$3" slug="$4"
  local commits; commits="$(cat)"

  _cl_heading "$fmt" 2 "What's changed"

  if [[ -z "$prev" ]]; then
    if [[ "$fmt" == html ]]; then _cl_para html '<em>First tagged release.</em>'
    else _cl_para md '_First tagged release._'; fi
    return 0
  fi

  if [[ -z "$commits" ]]; then
    if [[ "$fmt" == html ]]; then
      _cl_para html "$(printf '<em>No commits between <code>%s</code> and <code>%s</code>.</em>' "$prev" "$tag")"
    else
      _cl_para md "$(printf '_No commits between `%s` and `%s`._' "$prev" "$tag")"
    fi
  else
    local feats fixes other
    feats="$(printf '%s\n' "$commits" | grep -E '^feat(\(|:)' || true)"
    fixes="$(printf '%s\n' "$commits" | grep -E '^fix(\(|:)'  || true)"
    other="$(printf '%s\n' "$commits" | grep -vE '^(feat|fix)(\(|:)' || true)"
    if [[ -n "$feats" ]]; then _cl_heading "$fmt" 3 "Features";      _cl_list "$fmt" "$feats"; fi
    if [[ -n "$fixes" ]]; then _cl_heading "$fmt" 3 "Fixes";         _cl_list "$fmt" "$fixes"; fi
    if [[ -n "$other" ]]; then _cl_heading "$fmt" 3 "Other changes"; _cl_list "$fmt" "$other"; fi
  fi

  if [[ -n "$slug" ]]; then
    local url="https://github.com/$slug/compare/$prev...$tag"
    if [[ "$fmt" == html ]]; then
      _cl_para html "<strong>Full changelog</strong>: <a href=\"$url\">$url</a>"
    else
      _cl_para md "**Full changelog**: $url"
    fi
  fi
  return 0
}

# ── appcast ──────────────────────────────────────────────────────────────────
_appcast_open() {  # _appcast_open <owner/repo>
  cat <<EOF
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Scout</title>
    <link>https://github.com/$1/releases</link>
EOF
}
_appcast_close() {
  cat <<EOF
  </channel>
</rss>
EOF
}

# render_empty_appcast <owner/repo> — a valid feed with no items. Committed
# once so the feed URL never 404s before the first Sparkle-enabled release.
render_empty_appcast() {
  _appcast_open "$1"
  _appcast_close
}

# render_appcast <version> <build> <min-os> <tag> <owner/repo> <dmg-filename>
#                <ed-signature> <length> <pubdate-rfc822>
# stdin: HTML release notes for <description>.
# One-item feed: Sparkle compares <sparkle:version> (CFBundleVersion — our
# commit count) with the running app's, so the newest release alone suffices.
render_appcast() {
  local version="$1" build="$2" min_os="$3" tag="$4" slug="$5" dmg="$6" sig="$7" length="$8" pubdate="$9"
  local notes; notes="$(cat)"
  notes="${notes//]]>/]]&gt;}"   # a literal ]]> would terminate the CDATA section
  local release_url="https://github.com/$slug/releases/tag/$tag"
  local dmg_url="https://github.com/$slug/releases/download/$tag/$dmg"
  _appcast_open "$slug"
  cat <<EOF
    <item>
      <title>Scout $version</title>
      <link>$release_url</link>
      <sparkle:version>$build</sparkle:version>
      <sparkle:shortVersionString>$version</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>$min_os</sparkle:minimumSystemVersion>
      <sparkle:fullReleaseNotesLink>$release_url</sparkle:fullReleaseNotesLink>
      <pubDate>$pubdate</pubDate>
      <description><![CDATA[
$notes
      ]]></description>
      <enclosure url="$dmg_url"
                 sparkle:edSignature="$sig"
                 length="$length"
                 type="application/octet-stream"/>
    </item>
EOF
  _appcast_close
}

# parse_sign_update <stdout of `sign_update <file>`> → "<signature> <length>"
# sign_update prints: sparkle:edSignature="…" length="…"
parse_sign_update() {
  local sig length
  sig="$(printf '%s' "$1" | sed -nE 's/.*sparkle:edSignature="([^"]+)".*/\1/p')"
  length="$(printf '%s' "$1" | sed -nE 's/.* length="([0-9]+)".*/\1/p')"
  [[ -n "$sig" && -n "$length" ]] || return 1
  printf '%s %s\n' "$sig" "$length"
}
```

- [ ] **Step 4: Run tests + shellcheck**

Run: `bash scripts/tests/release-lib.test.sh && shellcheck scripts/release-lib.sh scripts/tests/release-lib.test.sh`
Expected: all `ok`, `✓ all release-lib tests passed`, shellcheck silent (fix the script rather than adding disable directives).

- [ ] **Step 5: Commit the initial empty feed**

```bash
bash -c 'source scripts/release-lib.sh; render_empty_appcast Raven-Scout/Scout' > appcast.xml
xmllint --noout appcast.xml && cat appcast.xml
```

Expected: the file contains the `<rss>`/`<channel>` skeleton with the `Raven-Scout/Scout` releases link and no `<item>`.

- [ ] **Step 6: CI step**

In `.github/workflows/ci.yml`, after the "Toolchain info" step (before "Run ScoutTests"):

```yaml
      # Pure shell helpers behind scripts/release.sh (version rule, changelog
      # + appcast rendering). Cheap, and the appcast must stay well-formed.
      - name: release-lib unit tests
        run: bash scripts/tests/release-lib.test.sh
```

- [ ] **Step 7: Commit**

```bash
git add scripts/release-lib.sh scripts/tests/release-lib.test.sh appcast.xml .github/workflows/ci.yml
git commit -m "build(release): release-lib.sh (version rule, changelog md+html, appcast) with tests; empty initial appcast.xml

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Wire `scripts/release.sh` — validation, guards, inside-out signing, EdDSA signature, appcast commit/push

**Files:**
- Modify: `scripts/release.sh` (whole file; section references use the current line numbers)

**Interfaces:**
- Consumes: `scripts/release-lib.sh` (Task 11); `Scout/Info.plist` keys (Task 3); Sparkle tools from the build's SPM artifacts (Task 1); the keychain key (Task 2).
- Produces: `build/release/Scout-<version>.dmg` + `build/release/appcast.xml`; for releases, `appcast.xml` at the repo root committed and pushed to `main`; for `PRERELEASE=1`, a GitHub pre-release with the appcast attached as an asset only.

- [ ] **Step 1: Header comment (replace lines 1–37)**

```bash
#!/usr/bin/env bash
# Build a Release Scout.app, sign it with Developer ID + hardened runtime,
# notarize and staple both the app and the DMG it ships in, EdDSA-sign the
# DMG for Sparkle, publish the DMG as a GitHub Release, then regenerate the
# checked-in appcast.xml (one item — the newest release) and push it to main.
# Installed copies (0.12.0+) read
#   https://raw.githubusercontent.com/<owner>/<repo>/main/appcast.xml
# and update themselves.
#
# Release notes are auto-generated from `git log <prev-tag>..HEAD`, grouped
# by conventional-commit prefix (feat / fix / other), followed by the
# standard install + configure boilerplate. The same grouping, rendered as
# HTML, becomes the appcast <description> (scripts/release-lib.sh).
#
# Usage:
#   scripts/release.sh                          # auto-pick version from commits (see below)
#   scripts/release.sh 0.1.0                    # explicit version (overrides the rule)
#   PRERELEASE=1 scripts/release.sh 0.12.0-rc.1 # pre-release: appcast attached as an
#                                               # asset only; main's feed is untouched
#
# Version rule: the next version is derived from the conventional-commit
# prefixes already used across the repo (and grouped in the changelog below).
# Any `feat:` commit since the latest plain v* tag ⇒ minor bump; otherwise
# (fix / perf / refactor / docs / chore / …) ⇒ patch bump. Pass an explicit
# version to override — e.g. a major/pre-1.0 bump; the script warns if the
# override disagrees with the rule but proceeds with what you passed.
# Pre-release tags (v0.12.0-rc.1) never feed the rule or the changelog range.
#
# A real release must be cut from a clean checkout of `main` — the appcast
# commit is pushed there. Sparkle compares CFBundleVersion (= commit count),
# so the release commit must be ahead of the previous tag; enforced below.
#
# Requirements: xcodebuild, hdiutil, codesign, xcrun (notarytool + stapler),
# xmllint, gh (logged in, with write access to the repo), and Sparkle's
# EdDSA private key in the login keychain (see below).
#
# Signing / notarization config (override via env if your setup differs):
#   SCOUT_SIGN_IDENTITY   codesign identity   (default: "Developer ID Application")
#   SCOUT_NOTARY_PROFILE  notarytool profile  (default: "scout-notary")
# The default identity substring resolves as long as exactly one "Developer ID
# Application" cert is in the keychain. Set the notary profile up once with:
#   xcrun notarytool store-credentials "scout-notary" \
#     --apple-id <you@email> --team-id <TEAMID> --password <app-specific-pw>
#
# Sparkle EdDSA key (one-time; README → "Cutting a release"): after any build,
#   build/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_keys
# stores the private key in the login keychain and prints the public key,
# which must equal SUPublicEDKey in Scout/Info.plist — this script refuses to
# ship a mismatch. Back the private key up (`generate_keys -x <file>`): losing
# it means every installed copy needs a manual reinstall.
#
# Escape hatches (for local iteration):
#   SKIP_NOTARIZE=1  build + sign + DMG + appcast, but skip the Apple round-trips
#                    (neither app nor DMG is stapled — Gatekeeper will still block)
#   SKIP_RELEASE=1   do everything except tag/push/upload/appcast-commit
#   PRERELEASE=1     publish as a GitHub pre-release (requires an explicit
#                    suffixed version like 0.12.0-rc.1); the appcast is attached
#                    to the pre-release only, so real users never see it
```

- [ ] **Step 2: Source the library; version selection; branch check (replace lines 39–96)**

```bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=release-lib.sh
source "$REPO_ROOT/scripts/release-lib.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Version selection (feat → minor, else → patch; explicit arg overrides)
# ─────────────────────────────────────────────────────────────────────────────
PRERELEASE="${PRERELEASE:-0}"
LATEST_TAG="$(release_tags | head -1 || true)"
SUBJECTS="$(git -C "$REPO_ROOT" log "${LATEST_TAG:+$LATEST_TAG..}HEAD" --no-merges --format='%s' 2>/dev/null || true)"
RECOMMENDED="$(recommend_version "$LATEST_TAG" "$SUBJECTS")"

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  VERSION="$1"
  if [[ "$VERSION" != "$RECOMMENDED" ]]; then
    echo "⚠ Version $VERSION overrides the rule-recommended $RECOMMENDED" >&2
    echo "  (feat→minor, else→patch, from commits since ${LATEST_TAG:-<none>})." >&2
  fi
else
  if [[ "$PRERELEASE" == "1" ]]; then
    echo "✗ PRERELEASE=1 needs an explicit version, e.g. PRERELEASE=1 $0 ${RECOMMENDED}-rc.1" >&2
    exit 1
  fi
  VERSION="$RECOMMENDED"
  echo "→ Auto-selected v$VERSION (feat→minor, else→patch) from commits since ${LATEST_TAG:-<none>}"
fi

case "$(version_kind "$VERSION")" in
  release)
    if [[ "$PRERELEASE" == "1" ]]; then
      echo "✗ PRERELEASE=1 requires a suffixed version (e.g. $VERSION-rc.1), got $VERSION" >&2
      exit 1
    fi ;;
  prerelease)
    if [[ "$PRERELEASE" != "1" ]]; then
      echo "✗ $VERSION looks like a pre-release. Set PRERELEASE=1 to publish it as one." >&2
      exit 1
    fi ;;
  *)
    echo "✗ Invalid version '$VERSION' (want MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-suffix)" >&2
    exit 1 ;;
esac
TAG="v$VERSION"

# A real release pushes appcast.xml to main, so it must be cut from a clean
# main checkout (pre-releases and SKIP_RELEASE=1 dry runs may run anywhere).
if [[ "$PRERELEASE" != "1" && "${SKIP_RELEASE:-0}" != "1" ]]; then
  BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short -q HEAD || true)"
  if [[ "$BRANCH" != "main" ]]; then
    echo "✗ Releases are cut from main (on '${BRANCH:-detached}'). Use PRERELEASE=1 for rehearsal builds." >&2
    exit 1
  fi
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
    echo "✗ Working tree is not clean; commit or stash before releasing." >&2
    exit 1
  fi
fi

BUILD_DIR="$REPO_ROOT/build"
RELEASE_DIR="$BUILD_DIR/release"
DMG="$RELEASE_DIR/Scout-$VERSION.dmg"
APPCAST="$RELEASE_DIR/appcast.xml"

SIGN_IDENTITY="${SCOUT_SIGN_IDENTITY:-Developer ID Application}"
NOTARY_PROFILE="${SCOUT_NOTARY_PROFILE:-scout-notary}"

# Fail fast if the signing identity isn't in the keychain — otherwise we'd
# burn a full universal build before codesign errors out at the end.
if ! security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY"; then
  echo "✗ No codesigning identity matching \"$SIGN_IDENTITY\" found in keychain." >&2
  echo "  Create one in Xcode → Settings → Accounts → Manage Certificates →" >&2
  echo "  + → Developer ID Application, or set SCOUT_SIGN_IDENTITY to match." >&2
  exit 1
fi

# Derive `owner/repo` from origin. Needed for the compare link *and* every
# URL in the appcast, so it is now required.
ORIGIN_URL="$(git -C "$REPO_ROOT" config --get remote.origin.url || true)"
REPO_SLUG=""
case "$ORIGIN_URL" in
  https://github.com/*) REPO_SLUG="${ORIGIN_URL#https://github.com/}"; REPO_SLUG="${REPO_SLUG%.git}" ;;
  git@github.com:*)     REPO_SLUG="${ORIGIN_URL#git@github.com:}";     REPO_SLUG="${REPO_SLUG%.git}" ;;
esac
if [[ -z "$REPO_SLUG" ]]; then
  echo "✗ Cannot derive owner/repo from origin ($ORIGIN_URL); the appcast needs it." >&2
  exit 1
fi
```

(The old inline `recommend_version` and the old `REPO_ROOT`/`BUILD_DIR`/… block are removed.)

- [ ] **Step 3: Build-number guard (after `BUILD_NUMBER=` at current line 120)**

```bash
# Sparkle compares CFBundleVersion, so two releases must never share a build
# number: refuse to release from a commit that isn't ahead of the last tag.
if [[ -n "$LATEST_TAG" ]]; then
  PREV_BUILD="$(git -C "$REPO_ROOT" rev-list --count "$LATEST_TAG")"
  if (( BUILD_NUMBER <= PREV_BUILD )); then
    echo "✗ Build number $BUILD_NUMBER is not greater than $LATEST_TAG's ($PREV_BUILD)." >&2
    echo "  Land a commit (or release from a HEAD ahead of $LATEST_TAG) first." >&2
    exit 1
  fi
fi
```

- [ ] **Step 4: Sparkle tools + key preflight (after the `APP` existence check, current lines 135–139)**

```bash
echo "→ Sparkle preflight"
SPARKLE_BIN="$(find "$BUILD_DIR/SourcePackages/artifacts" -type d -path '*/sparkle/Sparkle/bin' 2>/dev/null | head -1)"
if [[ -z "$SPARKLE_BIN" || ! -x "$SPARKLE_BIN/sign_update" || ! -x "$SPARKLE_BIN/generate_keys" ]]; then
  echo "✗ Sparkle tools not found under $BUILD_DIR/SourcePackages/artifacts — did the Sparkle package resolve?" >&2
  exit 1
fi
INFO_PLIST="$APP/Contents/Info.plist"
FEED_URL="$(/usr/libexec/PlistBuddy -c 'Print :SUFeedURL' "$INFO_PLIST" 2>/dev/null || true)"
PLIST_KEY="$(/usr/libexec/PlistBuddy -c 'Print :SUPublicEDKey' "$INFO_PLIST" 2>/dev/null || true)"
MIN_OS="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$INFO_PLIST")"
KEYCHAIN_KEY="$("$SPARKLE_BIN/generate_keys" -p 2>/dev/null || true)"
if [[ -z "$FEED_URL" ]]; then
  echo "✗ SUFeedURL missing from $INFO_PLIST (Scout/Info.plist not merged?)" >&2; exit 1
fi
if [[ -z "$KEYCHAIN_KEY" ]]; then
  echo "✗ No Sparkle EdDSA key in the login keychain. Run: $SPARKLE_BIN/generate_keys" >&2
  echo "  (or restore a backup with: $SPARKLE_BIN/generate_keys -f <private-key-file>)" >&2
  exit 1
fi
if [[ "$PLIST_KEY" != "$KEYCHAIN_KEY" ]]; then
  echo "✗ SUPublicEDKey in Info.plist does not match the keychain's public key." >&2
  echo "  Info.plist: ${PLIST_KEY:-<missing>}" >&2
  echo "  keychain:   $KEYCHAIN_KEY" >&2
  echo "  Shipping this would produce an app that can never verify an update." >&2
  exit 1
fi
echo "  feed:   $FEED_URL"
echo "  min OS: $MIN_OS · build: $BUILD_NUMBER · key OK"
```

- [ ] **Step 5: Sign inside-out (replace current lines 141–147)**

```bash
echo "→ Signing Sparkle's nested components inside-out, then Scout.app (Developer ID + hardened runtime)"
# --options runtime enables the hardened runtime (required for notarization,
# on every executable in the bundle); --timestamp embeds a secure timestamp so
# signatures stay valid past the cert's expiry. Sparkle.framework carries its
# own helpers (installer/downloader XPC services, Autoupdate, Updater.app), so
# the bundle is signed inside-out in the order Sparkle documents. No --deep:
# Apple and Sparkle both advise against it (the Downloader keeps entitlements
# the other components must not get).
SPARKLE_FW="$APP/Contents/Frameworks/Sparkle.framework"
if [[ ! -d "$SPARKLE_FW" ]]; then
  echo "✗ $SPARKLE_FW missing — Sparkle was not embedded in the app" >&2
  exit 1
fi
sign_component() {  # sign_component <path> [extra codesign args…]
  local path="$1"; shift
  if [[ ! -e "$path" ]]; then
    echo "✗ Expected Sparkle component missing: $path" >&2
    echo "  (a Sparkle upgrade moved things — update this list and the spec)" >&2
    exit 1
  fi
  codesign --force --options runtime --timestamp "$@" --sign "$SIGN_IDENTITY" "$path"
}
sign_component "$SPARKLE_FW/Versions/B/XPCServices/Installer.xpc"
sign_component "$SPARKLE_FW/Versions/B/XPCServices/Downloader.xpc" --preserve-metadata=entitlements
sign_component "$SPARKLE_FW/Versions/B/Autoupdate"
sign_component "$SPARKLE_FW/Versions/B/Updater.app"
sign_component "$SPARKLE_FW"
codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --strict --deep --verbose=2 "$APP"
```

- [ ] **Step 6: EdDSA-sign the final DMG (after the DMG notarize/staple block, current line 193)**

```bash
echo "→ EdDSA-signing the DMG for Sparkle (after stapling — the signature covers the final bytes)"
SIGN_OUT="$("$SPARKLE_BIN/sign_update" "$DMG")"
if ! read -r ED_SIGNATURE DMG_LENGTH < <(parse_sign_update "$SIGN_OUT"); then
  echo "✗ Could not parse sign_update output: $SIGN_OUT" >&2
  exit 1
fi
echo "  length: $DMG_LENGTH · edSignature: ${ED_SIGNATURE:0:12}…"
```

- [ ] **Step 7: Notes + appcast before the `SKIP_RELEASE` exit (replace current lines 195–283)**

Delete the current `SKIP_RELEASE` block, the `PREV_TAG`/`ORIGIN_URL`/`REPO_SLUG` derivation and the `{ … } > "$NOTES"` block; in their place:

```bash
# ─────────────────────────────────────────────────────────────────────────────
# Release notes (Markdown for GitHub) + appcast (HTML notes inside)
# ─────────────────────────────────────────────────────────────────────────────
PREV_TAG="$(release_tags | grep -vx "$TAG" | head -1 || true)"
COMMITS=""
if [[ -n "$PREV_TAG" ]]; then
  COMMITS="$(git -C "$REPO_ROOT" log "$PREV_TAG"..HEAD --no-merges --format='%s|%h')"
fi

NOTES="$BUILD_DIR/release-notes.md"
{
  printf '%s\n' "$COMMITS" | render_changelog md "$PREV_TAG" "$TAG" "$REPO_SLUG"
  echo "---"
  echo
  echo "## Install"
  echo
  echo "**Already running Scout 0.12.0 or later?** It updates itself — wait for the daily check or use **Scout → Check for Updates…**. The steps below are for first installs and for copies older than 0.12.0."
  echo
  echo "1. Download \`Scout-$VERSION.dmg\` from the Assets below."
  echo "2. Open the DMG and drag **Scout.app** into the **Applications** folder."
  echo "3. Launch it. Scout is signed with a Developer ID and notarized by Apple, so it opens with a normal double-click."
  echo
  echo "## Configure"
  echo
  echo "Open the app, press ⌘, to open Settings. Fill in your Linear workspace and author name so deep-links and comment authorship work correctly."
  echo
  echo "The app expects a Scout instance at \`~/Scout\`. Install the [scout-plugin](https://github.com/Raven-Scout/scout-plugin) into Claude Code and run \`/scout-setup\` first if you don't have one yet."
} > "$NOTES"

echo "→ Generating appcast"
printf '%s\n' "$COMMITS" | render_changelog html "$PREV_TAG" "$TAG" "$REPO_SLUG" \
  | render_appcast "$VERSION" "$BUILD_NUMBER" "$MIN_OS" "$TAG" "$REPO_SLUG" \
      "$(basename "$DMG")" "$ED_SIGNATURE" "$DMG_LENGTH" \
      "$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S +0000')" \
  > "$APPCAST"
xmllint --noout "$APPCAST"
echo "  appcast: $APPCAST"

if [[ "${SKIP_RELEASE:-0}" == "1" ]]; then
  echo "→ SKIP_RELEASE=1 set; not tagging, uploading, or committing the appcast."
  echo "  DMG:     $DMG"
  echo "  appcast: $APPCAST"
  exit 0
fi
```

- [ ] **Step 8: Tag, upload, publish the feed (replace current lines 285–297)**

```bash
echo "→ Tagging $TAG and creating GitHub release$( [[ "$PRERELEASE" == "1" ]] && echo ' (pre-release)' )"
if git -C "$REPO_ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "  tag $TAG already exists locally — skipping tag/push"
else
  git -C "$REPO_ROOT" tag -a "$TAG" -m "Release $VERSION"
  git -C "$REPO_ROOT" push origin "$TAG"
fi

if [[ "$PRERELEASE" == "1" ]]; then
  # Rehearsal build: the appcast rides along as an asset so an rc can be
  # pointed at it with SCOUT_APPCAST_URL; main's feed is untouched.
  gh release create "$TAG" "$DMG" "$APPCAST" --title "Scout $VERSION" --notes-file "$NOTES" --prerelease
  echo "✓ Pre-released $TAG"
  echo "  rehearse with: open -a <older Scout.app> --env SCOUT_APPCAST_URL=https://github.com/$REPO_SLUG/releases/download/$TAG/appcast.xml"
  exit 0
fi

# The DMG must be downloadable before any client reads the new feed, so the
# release goes up first and the feed commit follows.
gh release create "$TAG" "$DMG" --title "Scout $VERSION" --notes-file "$NOTES"

echo "→ Publishing the feed: committing appcast.xml to main"
cp "$APPCAST" "$REPO_ROOT/appcast.xml"
git -C "$REPO_ROOT" add appcast.xml
git -C "$REPO_ROOT" commit -q -m "chore(release): appcast for $TAG [skip ci]"
git -C "$REPO_ROOT" push origin HEAD:main

echo "✓ Released $TAG"
echo "  Installed copies (0.12.0+) will pick it up from https://raw.githubusercontent.com/$REPO_SLUG/main/appcast.xml"
```

- [ ] **Step 9: Static checks**

Run: `bash -n scripts/release.sh && shellcheck -x scripts/release.sh && bash scripts/tests/release-lib.test.sh | tail -1`
Expected: silent, silent, `✓ all release-lib tests passed`. And `grep -n "recommend_version()\|no nested frameworks" scripts/release.sh` → no output.

- [ ] **Step 10: Local dry run (needs Task 2's key + Task 3's plist; several minutes)**

```bash
PRERELEASE=1 SKIP_NOTARIZE=1 SKIP_RELEASE=1 scripts/release.sh 0.12.0-rc.0 2>&1 | tail -25
```

Expected, in order: `⚠ Version 0.12.0-rc.0 overrides…`, `→ Sparkle preflight` with `key OK`, `→ Signing Sparkle's nested components…`, `→ Packaging as DMG`, `→ EdDSA-signing the DMG…`, `→ Generating appcast`, `SKIP_RELEASE=1 set` with both paths, exit 0. Then:

```bash
codesign --verify --strict --deep --verbose=2 build/Build/Products/Release/Scout.app 2>&1 | tail -2
codesign -dv build/Build/Products/Release/Scout.app/Contents/Frameworks/Sparkle.framework/Versions/B/Autoupdate 2>&1 | grep -E 'flags|Authority=Developer'
lipo -info build/Build/Products/Release/Scout.app/Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle
xmllint --noout build/release/appcast.xml && grep -E 'sparkle:version|shortVersionString|edSignature|length=' build/release/appcast.xml
SIG="$(sed -nE 's/.*sparkle:edSignature="([^"]+)".*/\1/p' build/release/appcast.xml)"
"$(find build/SourcePackages/artifacts -type d -path '*/sparkle/Sparkle/bin' | head -1)/sign_update" --verify build/release/Scout-0.12.0-rc.0.dmg "$SIG" && echo "signature verifies"
```

Expected: `valid on disk` / `satisfies its Designated Requirement`; `flags=0x10000(runtime)` and a `Developer ID Application` authority on `Autoupdate`; `Architectures in the fat file: … x86_64 arm64`; the four appcast fields; `signature verifies`.

- [ ] **Step 11: Commit**

```bash
git add scripts/release.sh
git commit -m "feat(release): sign Sparkle inside-out, EdDSA-sign the DMG, publish appcast.xml to main; PRERELEASE=1 + guards

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Docs — README

**Files:**
- Modify: `README.md:11-20` (Install), `README.md:55-62` (First-run configuration), `README.md:96-104` (Cutting a release)

- [ ] **Step 1: Install**

Replace the "Install (prebuilt DMG)" section (lines 11–20) with:

```markdown
## Install (prebuilt DMG)

The fastest path if you just want to run the app:

1. Go to the [Releases](https://github.com/Raven-Scout/Scout/releases) page and download the latest `Scout-*.dmg`.
2. Open the DMG and drag **Scout.app** into the **Applications** folder.
3. Launch it. Scout is signed with a Developer ID and notarized by Apple, so it opens with a normal double-click.
4. Press ⌘, to open Settings and fill in your Linear workspace and author name.

That is the last DMG you download: from 0.12.0 on, Scout checks for a new release daily (and shortly after launch) and offers to install and relaunch. Check on demand with **Scout → Check for Updates…**, the same item in the menu-bar extra, or Settings → Updates. The same section tells you when the **scout-plugin** is behind and copies `/scout-update` for you to paste into Claude Code — the app can't apply plugin updates itself. A badge on the Settings sidebar row and a dot on the menu-bar icon mean something is behind.

The app expects a Scout instance at `~/Scout/`. Install the [scout-plugin](https://github.com/Raven-Scout/scout-plugin) into Claude Code and run `/scout-setup` first if you don't have one yet.
```

- [ ] **Step 2: First-run configuration** — append one bullet after "**Your name**":

```markdown
- **Updates** — read-only status for the app (Sparkle) and the plugin (installed vs. latest from its marketplace source), with *Check now*, *Install…* and *Copy /scout-update*. Dev builds run from Xcode never update themselves.
```

- [ ] **Step 3: Cutting a release** (replace lines 96–104)

```markdown
## Cutting a release

Maintainers: `scripts/release.sh [<version>]` — from a clean checkout of `main` — builds a universal (arm64+x86_64) DMG, signs the app with Developer ID + hardened runtime (Sparkle's nested helpers first, inside-out), notarizes and staples **both the app and the DMG** via Apple, EdDSA-signs the DMG for Sparkle, tags `v<version>`, creates a GitHub Release with the DMG, then regenerates `appcast.xml` (one item — the newest release) and pushes it to `main`. Installed copies read `https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml`, so publishing the release *is* shipping the update. Without an argument the version is derived from the commits since the last tag (`feat:` → minor, otherwise → patch).

Requirements on the release machine:

- A `Developer ID Application` cert in the keychain and a `scout-notary` notarytool credential profile (see the header of `scripts/release.sh`).
- Sparkle's EdDSA private key in the login keychain. One-time setup after any build:

  ```bash
  build/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_keys        # prints the public key
  build/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_keys -x key  # export → password manager, then delete the file
  ```

  The public key must equal `SUPublicEDKey` in `Scout/Info.plist`; the script refuses to ship a mismatch. **Back the private key up** — losing it means every installed copy needs a manual reinstall, because Sparkle will not accept updates signed with a different key.

Examples:

```bash
scripts/release.sh                          # next version by the rule
scripts/release.sh 0.2.0                    # explicit version
PRERELEASE=1 scripts/release.sh 0.12.0-rc.1 # GitHub pre-release; main's appcast untouched
```

Set `SKIP_RELEASE=1` to build the DMG + appcast locally without tagging, uploading, or committing. Sparkle only compares build numbers (`CFBundleVersion` = commit count), so a release must be cut from a commit ahead of the previous tag; the script enforces this. Sparkle never downgrades: fix a bad release forward with a patch release. Rehearse the update path on pre-releases (design doc, Amendments → rollout): install rc.1, publish rc.2, launch rc.1 with `--env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/v0.12.0-rc.2/appcast.xml` and confirm it updates.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(updates): README — in-app updates for app + plugin, key setup, pre-release rehearsal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: End-to-end rehearsal on pre-releases, then the first Sparkle release (owner: Jordan)

**Files:** none. Proves the pipeline before 0.12.0 — the one release where a broken updater cannot be fixed *through* the updater.

**Owner:** Jordan, on the release machine, after Tasks 1–13 are merged to `main`. An agent runs it only with Jordan's explicit go-ahead.

- [ ] **Step 1: Publish rc.1; install to a scratch location**

```bash
git checkout main && git pull --ff-only
PRERELEASE=1 scripts/release.sh 0.12.0-rc.1
mkdir -p ~/scout-e2e && hdiutil attach build/release/Scout-0.12.0-rc.1.dmg -mountpoint /Volumes/scout-rc1 -nobrowse
cp -R /Volumes/scout-rc1/Scout.app ~/scout-e2e/Scout.app && hdiutil detach /Volumes/scout-rc1
```

Expected: `✓ Pre-released v0.12.0-rc.1`; the release page shows the DMG and `appcast.xml`, marked **Pre-release**; `git -C . status` clean and `appcast.xml` on `main` unchanged (still the empty feed).

- [ ] **Step 2: Land one commit, publish rc.2**

Any commit on `main` (a `chore:` bump is fine), then `PRERELEASE=1 scripts/release.sh 0.12.0-rc.2`. Expected: `<sparkle:version>` in rc.2's appcast is greater than rc.1's.

- [ ] **Step 3: Drive rc.1 → rc.2 through Sparkle**

```bash
open -a ~/scout-e2e/Scout.app --env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/v0.12.0-rc.2/appcast.xml
```

**Scout → Check for Updates…** → Sparkle's "A new version of Scout is available!" sheet shows **Scout 0.12.0-rc.2** with the rendered notes; Settings ▸ Updates shows `0.12.0-rc.1 → 0.12.0-rc.2 available` with **Install…**, the Settings row badge reads **1**, the menu-bar icon has a dot. **Install and Relaunch** → relaunch → Settings → About reads `0.12.0-rc.2 (<build>)`; `codesign -dv ~/scout-e2e/Scout.app 2>&1 | grep TeamIdentifier` → `74SD45TPC5`.

If an error sheet appears: `log show --last 10m --predicate 'process == "Scout" AND (eventMessage CONTAINS "Sparkle" OR subsystem CONTAINS "sparkle")' --info`. A signature error → key preflight / `sign_update` step; "not properly signed" → inside-out signing missed a component. Fix on a branch, merge, repeat from Step 1 with rc.3/rc.4.

- [ ] **Step 4: Plugin track on the rc build**

In rc.2, Settings ▸ Updates shows the *scout-plugin* row (`0.8.0 · up to date` on the dev machine's `directory` source). Temporarily bump `version` in `~/scout-plugin/.claude-plugin/plugin.json`, click **Check now** → `0.8.0 → x.y.z available`, **Copy /scout-update** puts `/scout-update` on the clipboard, the badge count becomes 1 (or 2 while the app update is also pending). Revert the plugin.json edit.

- [ ] **Step 5: Clean up**

```bash
gh release delete v0.12.0-rc.1 --repo Raven-Scout/Scout --cleanup-tag --yes
gh release delete v0.12.0-rc.2 --repo Raven-Scout/Scout --cleanup-tag --yes
rm -rf ~/scout-e2e
```

- [ ] **Step 6: Ship 0.12.0**

```bash
scripts/release.sh
```

Expected: auto-selects `0.12.0`, `✓ Released v0.12.0`, a new `chore(release): appcast for v0.12.0 [skip ci]` commit on `main`, and `curl -s https://raw.githubusercontent.com/Raven-Scout/Scout/main/appcast.xml | grep shortVersionString` → `0.12.0` (allow up to ~5 min of CDN cache). Announce with the note that 0.12.0 is the last manual download.

---

## Self-review against the spec

- **Summary / two tracks / one `UpdateService` / Settings + badge** → Tasks 5 (service), 9 (Settings), 10 (badges).
- **Decisions:** Sparkle via SPM (Task 1); checked-in appcast on `main` via raw GitHub (Tasks 3, 11, 12 — feed URL, empty initial feed, commit + push); Settings ▸ Updates **plus** sidebar/menu-bar badge (Tasks 9, 10); plugin apply = copy `/scout-update` (Task 9); source-aware latest (Tasks 6, 7).
- **Architecture → Shared `UpdateService`:** `UpdateStatus`/state enum, `Track`, `appUpdate`/`pluginUpdate`, `anyUpdateAvailable`, `check(_:)`/`checkAll()`, `@MainActor` mutation — Task 5. Spec's `.idle .checking .upToDate .available .error` states are all present.
- **App track:** `SPUStandardUpdaterController` in `ScoutApp` (built via the service factory), Info.plist keys `SUFeedURL`/`SUPublicEDKey`/`SUEnableAutomaticChecks`/`SUScheduledCheckInterval = 86400` (Task 3), enclosure = the existing DMG (Task 12), thin delegate mapping found / not found / failed (Task 8), "Check for Updates…" menu command + Settings button (Tasks 8, 9). No duplicate GitHub poll for the app.
- **Plugin track:** installed from `installed_plugins.json` `plugins["scout@scout-plugin"][0].version` with cache-dir fallback; latest source-aware (github/git → raw manifest, directory → local); `SemVer` compare; hand-off copies `/scout-update`; "What's new" opens `CHANGELOG.md` on GitHub — Tasks 4, 6, 7, 9.
- **UI:** two rows with `current → latest`, chips (`Up to date` / `Update available` / `Checking…` / `Couldn't check`), primary action, Check now; hidden plugin row when unknown (Task 9). Badge on `SidebarView` Settings row and `MenuBarIcon` (Task 10).
- **Data flow:** plugin check on launch + manual, no timer (Task 10 `.task` → `startLaunchChecks`); Sparkle drives app checks.
- **Error handling:** offline → `.error`, silent, Settings-only (Tasks 5, 7); missing manifests → nil versions, row hidden (Tasks 6, 7); Sparkle failures via Sparkle's dialogs (Task 8).
- **Release infra 1–3:** key setup (Task 2), inside-out signing (Task 12 Step 5), `sign_update` + appcast item + commit/push to `main` (Task 12 Steps 6–8). `sparkle:version` = commit count, `shortVersionString` = `MARKETING_VERSION`.
- **Testing:** `SemVer` (Task 4), installed-version parser with anonymized fixture (Task 6), latest-version resolver github/directory/unknown (Tasks 6, 7), `UpdateService` transitions with fake fetcher + fake app controller (Task 5), thin Sparkle wrapper with only the delegate→event mapping untested (Task 8), UI by build + eyeball (Tasks 9, 10).
- **Amendments (2026-09-02):** exact pin (Task 1); typed plist + contract test (Task 3); Debug opt-out compiled everywhere (Task 8); preflight/build-number/component guards, sign-after-staple, xmllint, plain-tag filter, main-branch check (Task 12); `PRERELEASE=1` + `SCOUT_APPCAST_URL` rehearsal (Tasks 8, 12, 14); one-item regenerated feed + empty initial feed (Tasks 11, 12); HTML notes from the same grouping + `release-lib.sh` tests in CI (Task 11); `minimumSystemVersion` from the built plist (Task 12); raw `HEAD` ref for the plugin manifest (Task 6).
- **Type consistency:** `UpdateService.appUpdatesEnabled` / `anyUpdateAvailable` / `availableCount` / `check(_:)` / `startLaunchChecks()` / `pluginChangelogURL` used identically in Tasks 5, 8, 9, 10; `PluginUpdateResult` field names identical in Tasks 5, 7; `PluginManifests.*` names identical in Tasks 6, 7; `release-lib.sh` function names identical in Tasks 11, 12; `MenuBarIcon(status:updateAvailable:)` and `SidebarView(... settingsBadge:)` identical in Tasks 8 (ScoutApp) and 10.

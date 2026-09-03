# In-App Auto-Update (Sparkle) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scout keeps itself up to date — a running app notices a new GitHub release within hours, downloads it in the background, and offers one-click Install and Relaunch; `scripts/release.sh` produces the signed appcast as part of the existing one-command release.

**Architecture:** Sparkle 2.9.6 (SPM, exact pin) wrapped in one `AppUpdater` observable so the rest of the app never imports Sparkle; a typed `Scout/Info.plist` carries the feed URL, public EdDSA key and defaults; the appcast is a one-item XML uploaded as a release asset and served through GitHub's stable `releases/latest/download/appcast.xml` redirect. `scripts/release.sh` gains a sourced, unit-tested helper library (`scripts/release-lib.sh`) for version rules, changelog rendering (Markdown + HTML) and appcast rendering, and signs Sparkle's nested executables inside-out before the app.

**Tech Stack:** Swift 6.2 (Swift 5 language mode, default MainActor isolation), SwiftUI, Combine, Sparkle 2.9.6, Swift Testing (`@Test`/`@Suite`), xcodebuild, bash 3.2-compatible shell, `codesign`/`notarytool`/`stapler`, `gh`.

**Spec:** `docs/superpowers/specs/2026-09-02-auto-update-design.md`

## Global Constraints

- Branch: `claude/scout-app-auto-update-07ef1d` (spec + this plan already committed there). Repo `Raven-Scout/Scout`, base `main`.
- Sparkle package `https://github.com/sparkle-project/Sparkle`, product `Sparkle`, **exact** version `2.9.6`.
- Feed URL (verbatim, appears in Info.plist, tests, README): `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml`.
- Info.plist Sparkle keys: `SUFeedURL`, `SUPublicEDKey`, `SUEnableAutomaticChecks = true`, `SUAutomaticallyUpdate = true`, `SUScheduledCheckInterval = 21600` — typed (boolean/number), not strings.
- Feed override env var: `SCOUT_APPCAST_URL`; honored only for well-formed `https` URLs.
- Debug builds never start the updater. The **only** `#if DEBUG` is the `AppUpdater.updatesEnabledForThisBuild` constant; all Sparkle code compiles in both configurations.
- Signing order (never `--deep` when signing; `--deep` only for `codesign --verify`): `Installer.xpc` → `Downloader.xpc` (`--preserve-metadata=entitlements`) → `Autoupdate` → `Updater.app` → `Sparkle.framework` → `Scout.app`. Every nested component is signed with `--options runtime --timestamp`.
- `sign_update` runs on the **final, stapled** DMG; the appcast `length` must be that file's byte size.
- Version string rule: `^[0-9]+\.[0-9]+\.[0-9]+$` is a release; `^[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.]+$` is a pre-release and requires `PRERELEASE=1`; `PRERELEASE=1` requires an explicit version. Only plain `vX.Y.Z` tags feed the version rule / changelog range. Build number must be strictly greater than the latest plain tag's `git rev-list --count`.
- Shell scripts must run under macOS `/bin/bash` 3.2: no `${arr[@]}` expansion of a possibly-empty array under `set -u`, no `declare -A`, no `local -n`.
- Build/test: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteTypeName>`. `-only-testing:` must name a real `@Suite` **type**; a directory-style selector like `ScoutTests/Services` silently runs ZERO tests and reports success.
- New `.swift` files under `Scout/` or `ScoutTests/` are picked up automatically (synchronized file groups). The **only** `project.pbxproj` edits in this plan are the ones written out verbatim in Tasks 1 and 3.
- SourceKit/IDE may report "Cannot find type … in scope" / "No such module 'Testing'" / "No such module 'Sparkle'" — false positives; `xcodebuild` is authoritative.
- Test files need explicit imports (`import Foundation`, `import Combine`) — `MemberImportVisibility` is on.
- No fixture or parser changes anywhere in this plan; the `parser-corpus.json` three-repo sync rules in `CLAUDE.md` do not apply.
- Conventional-commit messages ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Fixtures/tests must not contain real identifiers (use `example-org/repo`, `Alex`, etc. per `CLAUDE.md`). The real repo slug `Raven-Scout/Scout` is allowed where it is the product's actual feed URL.

---

### Task 1: Add the Sparkle package to the Scout target

**Files:**
- Modify: `Scout.xcodeproj/project.pbxproj` (PBXBuildFile section lines 9–11; Frameworks phase lines 42–49; Scout target `packageProductDependencies` lines 97–99; `packageReferences` lines 153–155; `XCRemoteSwiftPackageReference` section lines 209–218; `XCSwiftPackageProductDependency` section lines 220–226)

**Interfaces:**
- Produces: `import Sparkle` available to the `Scout` target (Task 5); `Scout.app/Contents/Frameworks/Sparkle.framework` in built products (Task 8 signs it); Sparkle CLI tools under `<derivedData>/SourcePackages/artifacts/sparkle/Sparkle/bin/` (Tasks 2 and 8).

- [ ] **Step 1: Add the build-file, framework-phase and package entries**

Object IDs are 24 hex chars; these three are new and unique in the file:

| ID | Role |
| --- | --- |
| `5AC0FFEE2F9599AB00000001` | `XCRemoteSwiftPackageReference "Sparkle"` |
| `5AC0FFEE2F9599AB00000002` | `XCSwiftPackageProductDependency` `Sparkle` |
| `5AC0FFEE2F9599AB00000003` | `PBXBuildFile` `Sparkle in Frameworks` |

1a. In `/* Begin PBXBuildFile section */`, after the Grape line, add:

```
		5AC0FFEE2F9599AB00000003 /* Sparkle in Frameworks */ = {isa = PBXBuildFile; productRef = 5AC0FFEE2F9599AB00000002 /* Sparkle */; };
```

1b. In the Scout target's `PBXFrameworksBuildPhase` (`BEEE45512F95613D0078191D /* Frameworks */`), the `files = (` list becomes:

```
			files = (
				BEEE45A02F9599AB0078191D /* Grape in Frameworks */,
				5AC0FFEE2F9599AB00000003 /* Sparkle in Frameworks */,
			);
```

1c. In the `BEEE45532F95613D0078191D /* Scout */` native target, `packageProductDependencies` becomes:

```
			packageProductDependencies = (
				BEEE45A22F9599AB0078191D /* Grape */,
				5AC0FFEE2F9599AB00000002 /* Sparkle */,
			);
```

1d. In the `PBXProject` object, `packageReferences` becomes:

```
			packageReferences = (
				BEEE45A12F9599AB0078191D /* XCRemoteSwiftPackageReference "Grape" */,
				5AC0FFEE2F9599AB00000001 /* XCRemoteSwiftPackageReference "Sparkle" */,
			);
```

1e. In `/* Begin XCRemoteSwiftPackageReference section */`, after the Grape block, add:

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

1f. In `/* Begin XCSwiftPackageProductDependency section */`, after the Grape block, add:

```
		5AC0FFEE2F9599AB00000002 /* Sparkle */ = {
			isa = XCSwiftPackageProductDependency;
			package = 5AC0FFEE2F9599AB00000001 /* XCRemoteSwiftPackageReference "Sparkle" */;
			productName = Sparkle;
		};
```

- [ ] **Step 2: Build Debug into a local derived-data dir and verify the framework is embedded**

Run:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project Scout.xcodeproj -scheme Scout -configuration Debug \
  -destination 'platform=macOS' -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO build 2>&1 | grep -E "error:|warning: .*Sparkle|BUILD (SUCCEEDED|FAILED)"
ls build/Build/Products/Debug/Scout.app/Contents/Frameworks/
ls build/SourcePackages/artifacts/sparkle/Sparkle/bin/
```

Expected: `BUILD SUCCEEDED`; the first `ls` shows `Sparkle.framework`; the second shows `generate_appcast  generate_keys  sign_update` (plus `BinaryDelta`).

**If `Contents/Frameworks/` has no `Sparkle.framework`** (Xcode did not auto-embed the binary product), add an explicit embed phase and re-run this step:

- In `/* Begin PBXBuildFile section */` add
  `		5AC0FFEE2F9599AB00000005 /* Sparkle in Embed Frameworks */ = {isa = PBXBuildFile; productRef = 5AC0FFEE2F9599AB00000002 /* Sparkle */; settings = {ATTRIBUTES = (CodeSignOnCopy, RemoveHeadersOnCopy, ); }; };`
- Add a new section (sections are alphabetical; place it right after `/* End PBXContainerItemProxy section */`):

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

- In the Scout target's `buildPhases`, append `				5AC0FFEE2F9599AB00000004 /* Embed Frameworks */,` after the Resources phase.

- [ ] **Step 3: Run the whole existing suite to confirm nothing regressed**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST SUCCEEDED` with the same test count as before (`Test run with N tests passed`).

- [ ] **Step 4: Commit**

```bash
git add Scout.xcodeproj/project.pbxproj
git commit -m "build(updates): add Sparkle 2.9.6 (exact) as an SPM dependency of the Scout target

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Generate the EdDSA signing key (owner: Jordan)

**Files:** none in the repo. Output: one 44-character base64 public key, consumed by Task 3.

**Owner:** Jordan, on the release machine (the one holding the `scout-notary` notarytool profile). An agent may run this only with Jordan's explicit go-ahead in the PR — the private key is the root of trust for every future update and lives in his login keychain.

**Interfaces:**
- Produces: public key string → `SUPublicEDKey` in `Scout/Info.plist` (Task 3); private key in the login keychain (service `https://sparkle-project.org`, account `ed25519`) used by `sign_update` / `generate_keys -p` in Task 8.

- [ ] **Step 1: Locate the tools (Task 1's build put them here)**

```bash
SPARKLE_BIN="$(find build/SourcePackages/artifacts -type d -path '*/sparkle/Sparkle/bin' | head -1)"; echo "$SPARKLE_BIN"
```

Expected: a path ending in `/sparkle/Sparkle/bin`. If empty, re-run Task 1 Step 2's build.

- [ ] **Step 2: Generate the key pair**

```bash
"$SPARKLE_BIN/generate_keys"
```

Expected: a message that the key was stored in the Keychain, followed by the public key (44 base64 chars ending in `=`). If it says a key already exists, print it with `"$SPARKLE_BIN/generate_keys" -p` instead — do **not** generate a second one.

- [ ] **Step 3: Back the private key up (mandatory — losing it strands every install)**

```bash
"$SPARKLE_BIN/generate_keys" -x ~/Desktop/scout-sparkle-ed25519.key
```

Store the file's contents in a password manager entry named "Scout Sparkle EdDSA private key", then `rm ~/Desktop/scout-sparkle-ed25519.key`. Restoring on another machine is `generate_keys -f <file>`.

- [ ] **Step 4: Hand the public key to Task 3**

Paste the public key into the PR thread (it is public by design) or straight into `Scout/Info.plist` in Task 3.

---

### Task 3: Typed `Scout/Info.plist` with the Sparkle keys + contract test

**Files:**
- Create: `Scout/Info.plist`
- Modify: `Scout.xcodeproj/project.pbxproj` (Scout target Debug config lines 347–378 and Release config lines 379–410; `PBXFileSystemSynchronizedRootGroup` for Scout lines 29–33; new exception-set section)
- Test: `ScoutTests/Services/UpdateInfoPlistTests.swift` (create)

**Interfaces:**
- Consumes: public key from Task 2.
- Produces: `Bundle.main.infoDictionary["SUFeedURL"|"SUPublicEDKey"|"SUEnableAutomaticChecks"|"SUAutomaticallyUpdate"|"SUScheduledCheckInterval"]` in both configurations — read by Sparkle at runtime and by `scripts/release.sh`'s preflight (Task 8).

- [ ] **Step 1: Write the failing contract test**

Create `ScoutTests/Services/UpdateInfoPlistTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// The test host is Scout.app itself, so `Bundle.main` is the app bundle.
/// These pin the Sparkle contract: drop the key, retype a boolean as the
/// string "YES", or edit the feed URL and this suite goes red.
@Suite("Sparkle Info.plist contract")
struct UpdateInfoPlistTests {
    private var info: [String: Any] { Bundle.main.infoDictionary ?? [:] }

    @Test func feedURLIsTheLatestReleaseAppcastRedirect() {
        #expect(info["SUFeedURL"] as? String
            == "https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml")
    }

    @Test func publicKeyIsA32ByteEd25519Key() throws {
        let key = try #require(info["SUPublicEDKey"] as? String)
        let bytes = try #require(Data(base64Encoded: key))
        #expect(bytes.count == 32)
    }

    @Test func automaticChecksAndDownloadsDefaultOn() {
        // `as? Bool` succeeds for a plist <true/> (NSNumber) and fails for the
        // string "YES" — which is exactly the retyping this guards against.
        #expect(info["SUEnableAutomaticChecks"] as? Bool == true)
        #expect(info["SUAutomaticallyUpdate"] as? Bool == true)
    }

    @Test func checkIntervalIsSixHours() {
        #expect((info["SUScheduledCheckInterval"] as? NSNumber)?.intValue == 21600)
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateInfoPlistTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST FAILED` — 4 tests fail (the keys are absent from the generated plist).

- [ ] **Step 3: Create the plist**

Create `Scout/Info.plist`, replacing `{{PUBLIC_KEY_FROM_TASK_2}}` with the 44-character key from Task 2:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<!-- Sparkle (in-app updates). Everything else in Info.plist is generated
	     from build settings (GENERATE_INFOPLIST_FILE = YES) and merged with
	     this file. See docs/superpowers/specs/2026-09-02-auto-update-design.md. -->
	<key>SUFeedURL</key>
	<string>https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml</string>
	<key>SUPublicEDKey</key>
	<string>{{PUBLIC_KEY_FROM_TASK_2}}</string>
	<key>SUEnableAutomaticChecks</key>
	<true/>
	<key>SUAutomaticallyUpdate</key>
	<true/>
	<key>SUScheduledCheckInterval</key>
	<integer>21600</integer>
</dict>
</plist>
```

Sanity check it parses: `plutil -lint Scout/Info.plist` → `Scout/Info.plist: OK`.

- [ ] **Step 4: Point both Scout configurations at it and keep it out of Copy Bundle Resources**

4a. In **both** `BEEE45602F95613E0078191D /* Debug */` and `BEEE45612F95613E0078191D /* Release */` (the Scout target configs — the ones containing `PRODUCT_BUNDLE_IDENTIFIER = com.scout.Scout…`), add directly after `GENERATE_INFOPLIST_FILE = YES;`:

```
				INFOPLIST_FILE = Scout/Info.plist;
```

4b. Add a new section after `/* End PBXFileReference section */`:

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

4c. The Scout synchronized root group becomes:

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

- [ ] **Step 5: Run the contract test to verify it passes, and inspect the merged plist**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateInfoPlistTests 2>&1 | grep -E "error:|warning: .*Info.plist|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `Test run with 4 tests passed`, `TEST SUCCEEDED`, and no "Info.plist in Copy Bundle Resources" warning.

Then confirm the generated keys survived the merge (display name, usage description) alongside ours:

```bash
APP="$(find ~/Library/Developer/Xcode/DerivedData -path '*/Build/Products/Debug/Scout.app' -maxdepth 6 | head -1)"
plutil -p "$APP/Contents/Info.plist" | grep -E 'CFBundleDisplayName|NSAppleEventsUsageDescription|SU[A-Z]'
```

Expected: `"CFBundleDisplayName" => "Scout Dev"`, the Apple-events string, and all five `SU…` keys with a `1`/`21600`/URL/key value (not quoted `"YES"`).

- [ ] **Step 6: Commit**

```bash
git add Scout/Info.plist Scout.xcodeproj/project.pbxproj ScoutTests/Services/UpdateInfoPlistTests.swift
git commit -m "feat(updates): Sparkle feed URL, public key and defaults in a typed Info.plist (+ contract test)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `UpdateFeed` — the `SCOUT_APPCAST_URL` override parser

**Files:**
- Create: `Scout/Services/UpdateFeed.swift`
- Test: `ScoutTests/Services/UpdateFeedTests.swift` (create)

**Interfaces:**
- Produces: `enum UpdateFeed { static let overrideEnvironmentKey: String; nonisolated static func overrideURLString(environment: [String: String]) -> String? }` — consumed by `AppUpdaterDelegate.feedURLString(for:)` in Task 5.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Services/UpdateFeedTests.swift`:

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

    @Test func keyNameIsStable() {
        #expect(UpdateFeed.overrideEnvironmentKey == "SCOUT_APPCAST_URL")
    }

    @Test func httpsOverrideIsReturnedVerbatim() {
        let url = "https://github.com/example-org/repo/releases/download/v0.12.0-rc.2/appcast.xml"
        #expect(override(url) == url)
    }

    @Test func missingVariableMeansNoOverride() {
        #expect(override(nil) == nil)
    }

    @Test func emptyOrWhitespaceMeansNoOverride() {
        #expect(override("") == nil)
        #expect(override("  \n") == nil)
    }

    @Test func plainHTTPIsRejected() {
        #expect(override("http://localhost:8000/appcast.xml") == nil)
        #expect(override("HTTP://example.com/appcast.xml") == nil)
    }

    @Test func nonURLsAreRejected() {
        #expect(override("not a url") == nil)
        #expect(override("https://") == nil)
        #expect(override("file:///tmp/appcast.xml") == nil)
    }

    @Test func surroundingWhitespaceIsTrimmed() {
        #expect(override(" https://example.com/appcast.xml\n") == "https://example.com/appcast.xml")
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateFeedTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — compile error `cannot find 'UpdateFeed' in scope`.

- [ ] **Step 3: Implement**

Create `Scout/Services/UpdateFeed.swift`:

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

    /// The override URL string, or nil when the variable is unset, blank, or
    /// not a well-formed `https` URL with a host.
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

- [ ] **Step 4: Run to verify it passes**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/UpdateFeedTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `Test run with 7 tests passed`, `TEST SUCCEEDED`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/UpdateFeed.swift ScoutTests/Services/UpdateFeedTests.swift
git commit -m "feat(updates): UpdateFeed — https-only SCOUT_APPCAST_URL override parser

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `AppUpdater` — Sparkle wrapper, disabled in Debug

**Files:**
- Create: `Scout/Services/AppUpdater.swift`
- Test: `ScoutTests/Services/AppUpdaterTests.swift` (create)

**Interfaces:**
- Consumes: `UpdateFeed.overrideURLString(environment:)` (Task 4); `Sparkle` module (Task 1).
- Produces (used by Task 6):

```swift
@MainActor final class AppUpdater: ObservableObject {
    static let updatesEnabledForThisBuild: Bool          // false under DEBUG
    let isEnabled: Bool
    @Published private(set) var canCheckForUpdates: Bool
    var lastUpdateCheckDate: Date? { get }
    var automaticallyChecksForUpdates: Bool { get set }
    var automaticallyDownloadsUpdates: Bool { get set }
    init(enabled: Bool = AppUpdater.updatesEnabledForThisBuild)
    func checkForUpdates()                                // no-op unless enabled && canCheckForUpdates
}
final class AppUpdaterDelegate: NSObject, SPUUpdaterDelegate  // internal to this file's users
```

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Services/AppUpdaterTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// Tests run in the Debug test host, where the updater must never start.
/// The enabled path needs a signed Release build and a real appcast — that
/// is the rc.1 → rc.2 end-to-end check in the spec, not a unit test.
@Suite("AppUpdater (disabled build)")
@MainActor
struct AppUpdaterTests {
    @Test func debugBuildsAreDisabledByDefault() {
        #expect(AppUpdater.updatesEnabledForThisBuild == false)
        #expect(AppUpdater().isEnabled == false)
    }

    @Test func disabledUpdaterNeverReportsItCanCheck() {
        let updater = AppUpdater(enabled: false)
        #expect(updater.canCheckForUpdates == false)
        #expect(updater.lastUpdateCheckDate == nil)
    }

    @Test func checkForUpdatesIsANoOpWhenDisabled() {
        let updater = AppUpdater(enabled: false)
        updater.checkForUpdates()   // must not start Sparkle, show UI, or crash
        #expect(updater.canCheckForUpdates == false)
    }

    @Test func preferenceAccessorsRoundTripWithoutStarting() {
        // Sparkle persists these in the host's UserDefaults; reading and
        // writing them must work even though the updater is never started.
        let updater = AppUpdater(enabled: false)
        let original = updater.automaticallyDownloadsUpdates
        updater.automaticallyDownloadsUpdates = !original
        #expect(updater.automaticallyDownloadsUpdates == !original)
        updater.automaticallyDownloadsUpdates = original
        #expect(updater.automaticallyDownloadsUpdates == original)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/AppUpdaterTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — compile error `cannot find 'AppUpdater' in scope`.

- [ ] **Step 3: Implement**

Create `Scout/Services/AppUpdater.swift`:

```swift
import Foundation
import Combine
import Sparkle

/// Thin wrapper around Sparkle's standard updater controller so the rest of
/// the app never imports Sparkle. Owned by `ScoutApp`, injected as an
/// environment object into the main window, the menu-bar extra and Settings.
///
/// Disabled in Debug builds: a dev build lives in DerivedData under
/// `com.scout.Scout.dev`, and letting it replace itself with the release
/// `com.scout.Scout` bundle would be confusing at best. The controller is
/// still constructed — so this code compiles and runs in Debug/CI — it is
/// just never started.
@MainActor
final class AppUpdater: ObservableObject {
    /// True in Release builds only. This is the single `#if DEBUG` in the
    /// updater; everything else compiles identically in both configurations.
    static let updatesEnabledForThisBuild: Bool = {
        #if DEBUG
        return false
        #else
        return true
        #endif
    }()

    /// Whether this instance ever started Sparkle.
    let isEnabled: Bool

    /// False while a check is in flight, and always false when disabled.
    /// Drives the enabled state of every "Check for Updates…" control.
    @Published private(set) var canCheckForUpdates = false

    private let controller: SPUStandardUpdaterController
    private let delegate = AppUpdaterDelegate()
    private var cancellables: Set<AnyCancellable> = []

    init(enabled: Bool = AppUpdater.updatesEnabledForThisBuild) {
        isEnabled = enabled
        controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: delegate,
            userDriverDelegate: nil
        )
        guard enabled else { return }
        controller.startUpdater()
        // Sparkle's own SwiftUI recipe: mirror the KVO-observable flag.
        controller.updater.publisher(for: \.canCheckForUpdates)
            .sink { [weak self] value in self?.canCheckForUpdates = value }
            .store(in: &cancellables)
    }

    /// When Sparkle last checked (any trigger). Not KVO-observable in Sparkle,
    /// so it is re-read whenever the owning view re-renders — which happens
    /// when `canCheckForUpdates` flips at the end of a check.
    var lastUpdateCheckDate: Date? {
        isEnabled ? controller.updater.lastUpdateCheckDate : nil
    }

    /// Settings → Updates → "Check for updates automatically".
    var automaticallyChecksForUpdates: Bool {
        get { controller.updater.automaticallyChecksForUpdates }
        set {
            objectWillChange.send()
            controller.updater.automaticallyChecksForUpdates = newValue
        }
    }

    /// Settings → Updates → "Download and install automatically".
    /// Sparkle downloads in the background and always asks before relaunching.
    var automaticallyDownloadsUpdates: Bool {
        get { controller.updater.automaticallyDownloadsUpdates }
        set {
            objectWillChange.send()
            controller.updater.automaticallyDownloadsUpdates = newValue
        }
    }

    /// User-initiated check: shows Sparkle's standard UI, including
    /// "You're up to date" and error alerts. No-op when disabled or busy.
    func checkForUpdates() {
        guard isEnabled, canCheckForUpdates else { return }
        controller.updater.checkForUpdates()
    }
}

/// The only delegate hook we use: an optional per-process feed override for
/// end-to-end testing (see `UpdateFeed`). Sparkle holds its delegate weakly,
/// so `AppUpdater` keeps the strong reference.
final class AppUpdaterDelegate: NSObject, SPUUpdaterDelegate {
    nonisolated func feedURLString(for updater: SPUUpdater) -> String? {
        UpdateFeed.overrideURLString(environment: ProcessInfo.processInfo.environment)
    }
}
```

If the compiler rejects `feedURLString(for:)` as not matching the protocol under default MainActor isolation, change the class declaration to `final class AppUpdaterDelegate: NSObject, @preconcurrency SPUUpdaterDelegate` and keep the method `nonisolated`.

- [ ] **Step 4: Run to verify it passes**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/AppUpdaterTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `Test run with 4 tests passed`, `TEST SUCCEEDED`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/AppUpdater.swift ScoutTests/Services/AppUpdaterTests.swift
git commit -m "feat(updates): AppUpdater — Sparkle wrapper that never starts in Debug builds

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Surfaces — app menu, menu-bar extra, Settings → Updates

**Files:**
- Create: `Scout/Shell/CheckForUpdatesView.swift`
- Modify: `Scout/ScoutApp.swift` (whole file, 31 lines)
- Modify: `Scout/Shell/MenuBarExtraContent.swift:5-20`
- Modify: `Scout/Shell/SettingsView.swift` (property list lines 12–24; insert a section before `section(label: "About")` at line 178; add a helper after `bundleId` at line 275–277)

**Interfaces:**
- Consumes: `AppUpdater` (Task 5) as an `@EnvironmentObject` / `@ObservedObject`.
- Produces: `struct CheckForUpdatesView: View { @ObservedObject var updater: AppUpdater }`.

- [ ] **Step 1: Create the shared menu item view**

Create `Scout/Shell/CheckForUpdatesView.swift`:

```swift
import SwiftUI

/// "Check for Updates…" menu item, shared by the app menu (under About Scout)
/// and the menu-bar extra. Disabled while a check is in flight or when the
/// updater is disabled (Debug builds).
struct CheckForUpdatesView: View {
    @ObservedObject var updater: AppUpdater

    var body: some View {
        Button("Check for Updates…") { updater.checkForUpdates() }
            .disabled(!updater.canCheckForUpdates)
    }
}
```

- [ ] **Step 2: Own the updater in `ScoutApp` and inject it everywhere**

Replace `Scout/ScoutApp.swift` with:

```swift
import SwiftUI

@main
struct ScoutApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var appState = AppState()
    @StateObject private var updater = AppUpdater()

    var body: some Scene {
        WindowGroup("Scout") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(appState.proposalsDocumentService)
                .environmentObject(updater)
                .frame(minWidth: 1100, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }  // suppress File > New Window
            CommandGroup(after: .appInfo) {        // Scout ▸ Check for Updates… (under About Scout)
                CheckForUpdatesView(updater: updater)
            }
        }

        MenuBarExtra {
            MenuBarExtraContent()
                .environmentObject(appState)
                .environmentObject(updater)
        } label: {
            MenuBarIcon(status: appState.menuBarStatus)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(updater)
        }
    }
}
```

- [ ] **Step 3: Menu-bar extra item (release builds only)**

In `Scout/Shell/MenuBarExtraContent.swift`, add the environment object and the item after *Open Scout folder in Finder*:

```swift
struct MenuBarExtraContent: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var updater: AppUpdater

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
        if updater.isEnabled {
            // Dev builds never update themselves — don't grow a dead item.
            CheckForUpdatesView(updater: updater)
        }
        Divider()
        Button("Quit Scout") { NSApp.terminate(nil) }
    }
```

(Everything below `body` in that file is unchanged.)

- [ ] **Step 4: Settings → Updates section**

In `Scout/Shell/SettingsView.swift`:

4a. Add after the `@State private var detectedClaudePath: String?` line (line 24):

```swift
    @EnvironmentObject private var updater: AppUpdater
```

4b. Insert this block immediately **before** `section(label: "About") {` (line 178):

```swift
                section(label: "Updates") {
                    SettingsCard {
                        if updater.isEnabled {
                            SettingsRow(
                                title: "Check for updates automatically",
                                help: "Scout looks for a new release every 6 hours and shortly after launch."
                            ) {
                                SettingsToggle(isOn: Binding(
                                    get: { updater.automaticallyChecksForUpdates },
                                    set: { updater.automaticallyChecksForUpdates = $0 }))
                            }
                            SettingsRow(
                                title: "Download and install automatically",
                                help: "Updates download in the background. Scout always asks before relaunching."
                            ) {
                                SettingsToggle(isOn: Binding(
                                    get: { updater.automaticallyDownloadsUpdates },
                                    set: { updater.automaticallyDownloadsUpdates = $0 }))
                            }
                            SettingsRow(title: "Check now", help: lastCheckedText) {
                                Button("Check for Updates…") { updater.checkForUpdates() }
                                    .disabled(!updater.canCheckForUpdates)
                            }
                        } else {
                            SettingsRow(
                                title: "Automatic updates are disabled in development builds",
                                help: "Release builds installed from the DMG check for updates automatically."
                            ) { EmptyView() }
                        }
                    }
                }

```

4c. Add after the `bundleId` computed property (after line 277, before the closing `}` of `SettingsView`):

```swift
    private var lastCheckedText: String {
        guard let date = updater.lastUpdateCheckDate else { return "Last checked: never" }
        return "Last checked: " + date.formatted(.relative(presentation: .named))
    }
```

- [ ] **Step 5: Build, run the full suite, and eyeball the Debug app**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -E "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST SUCCEEDED`; count = previous total + 15 (4 + 7 + 4 new tests).

Then launch the Debug build once and check by eye (quit it afterwards — see the "N identical Scout instances" note in memory):

```bash
APP="$(find ~/Library/Developer/Xcode/DerivedData -path '*/Build/Products/Debug/Scout.app' -maxdepth 6 | head -1)"; open "$APP"
```

Expected: **Scout Dev** menu shows *Check for Updates…* (greyed out) directly under *About Scout*; the menu-bar extra has **no** update item; Settings ⌘, shows an **UPDATES** card reading "Automatic updates are disabled in development builds".

- [ ] **Step 6: Commit**

```bash
git add Scout/Shell/CheckForUpdatesView.swift Scout/ScoutApp.swift Scout/Shell/MenuBarExtraContent.swift Scout/Shell/SettingsView.swift
git commit -m "feat(updates): Check for Updates… in the app menu + menu bar, Settings → Updates section

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `scripts/release-lib.sh` — pure helpers with a bash test suite (+ CI step)

**Files:**
- Create: `scripts/release-lib.sh`
- Create: `scripts/tests/release-lib.test.sh`
- Modify: `.github/workflows/ci.yml:37-41` (add a step after "Toolchain info")

**Interfaces:**
- Produces (all sourced by Task 8's `release.sh`):
  - `release_tags` → plain `vX.Y.Z` tags, highest first, one per line
  - `recommend_version <latest-tag-or-empty> <commit-subjects>` → next version
  - `version_kind <version>` → `release` | `prerelease` | `invalid`
  - `html_escape` (stdin → stdout)
  - `render_changelog <md|html> <prev-tag-or-empty> <tag> <owner/repo>` with `subject|hash` lines on stdin → notes body on stdout
  - `render_appcast <version> <build> <min-os> <tag> <owner/repo> <dmg-filename> <ed-signature> <length> <pubdate>` with the HTML notes on stdin → appcast XML on stdout
  - `parse_sign_update <sign_update-stdout>` → prints `<signature> <length>`; exit 1 if unparsable

- [ ] **Step 1: Write the failing test script**

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

# ── version_kind ─────────────────────────────────────────────────────────────
assert_eq "release version"              release    "$(version_kind 0.12.0)"
assert_eq "prerelease version"           prerelease "$(version_kind 0.12.0-rc.1)"
assert_eq "invalid: two components"      invalid    "$(version_kind 1.2)"
assert_eq "invalid: v prefix"            invalid    "$(version_kind v1.2.3)"
assert_eq "invalid: empty suffix"        invalid    "$(version_kind 1.2.3-)"

# ── recommend_version ────────────────────────────────────────────────────────
assert_eq "first release"                0.1.0  "$(recommend_version "" "")"
assert_eq "fix bumps patch"              0.11.3 "$(recommend_version v0.11.2 $'fix(kb): x\nchore: y')"
assert_eq "feat bumps minor"             0.12.0 "$(recommend_version v0.11.2 $'fix: x\nfeat(updates): y')"
assert_eq "breaking feat bumps minor"    0.12.0 "$(recommend_version v0.11.2 'feat!: y')"
assert_eq "no commits bumps patch"       0.11.3 "$(recommend_version v0.11.2 "")"

# ── render_changelog (markdown) ───────────────────────────────────────────────
md="$(printf 'feat(a): one|abc1234\nfix(b): two|def5678\nchore: three|9999999\n' \
  | render_changelog md v0.11.2 v0.12.0 example-org/repo)"
assert_contains "md heading"        "## What's changed" "$md"
assert_contains "md features head"  "### Features" "$md"
assert_contains "md feature item"   '- feat(a): one (`abc1234`)' "$md"
assert_contains "md fixes head"     "### Fixes" "$md"
assert_contains "md fix item"       '- fix(b): two (`def5678`)' "$md"
assert_contains "md other head"     "### Other changes" "$md"
assert_contains "md compare link"   "**Full changelog**: https://github.com/example-org/repo/compare/v0.11.2...v0.12.0" "$md"

only_fix="$(printf 'fix: x|1111111\n' | render_changelog md v0.11.2 v0.11.3 example-org/repo)"
if [[ "$only_fix" == *"### Features"* ]]; then echo "FAIL md omits empty sections"; fails=$((fails + 1)); else echo "ok   md omits empty sections"; fi

first="$(printf '' | render_changelog md "" v0.1.0 example-org/repo)"
assert_contains "md first release"  "_First tagged release._" "$first"

none="$(printf '' | render_changelog md v0.11.2 v0.11.3 example-org/repo)"
assert_contains "md no commits"     '_No commits between `v0.11.2` and `v0.11.3`._' "$none"

# ── render_changelog (html) ───────────────────────────────────────────────────
html="$(printf 'feat: a <b> & c|abc1234\n' | render_changelog html v0.11.2 v0.12.0 example-org/repo)"
assert_contains "html heading"      "<h2>What's changed</h2>" "$html"
assert_contains "html escaped item" "<li>feat: a &lt;b&gt; &amp; c (<code>abc1234</code>)</li>" "$html"
assert_contains "html list tags"    "<ul>" "$html"
assert_contains "html compare link" '<a href="https://github.com/example-org/repo/compare/v0.11.2...v0.12.0">' "$html"

# ── render_appcast ────────────────────────────────────────────────────────────
appcast="$(printf '<h2>What'"'"'s changed</h2>\n<ul><li>x</li></ul>\n' \
  | render_appcast 0.12.0 412 15.7 v0.12.0 example-org/repo Scout-0.12.0.dmg 'c2lnbmF0dXJl' 9012345 'Wed, 02 Sep 2026 20:15:00 +0000')"
if printf '%s' "$appcast" | xmllint --noout - 2>/dev/null; then echo "ok   appcast is well-formed XML"
else echo "FAIL appcast is not well-formed XML"; printf '%s\n' "$appcast"; fails=$((fails + 1)); fi
assert_contains "appcast title"         "<title>Scout 0.12.0</title>" "$appcast"
assert_contains "appcast version"       "<sparkle:version>412</sparkle:version>" "$appcast"
assert_contains "appcast short version" "<sparkle:shortVersionString>0.12.0</sparkle:shortVersionString>" "$appcast"
assert_contains "appcast min os"        "<sparkle:minimumSystemVersion>15.7</sparkle:minimumSystemVersion>" "$appcast"
assert_contains "appcast release link"  "<link>https://github.com/example-org/repo/releases/tag/v0.12.0</link>" "$appcast"
assert_contains "appcast pubdate"       "<pubDate>Wed, 02 Sep 2026 20:15:00 +0000</pubDate>" "$appcast"
assert_contains "appcast enclosure url" 'url="https://github.com/example-org/repo/releases/download/v0.12.0/Scout-0.12.0.dmg"' "$appcast"
assert_contains "appcast signature"     'sparkle:edSignature="c2lnbmF0dXJl"' "$appcast"
assert_contains "appcast length"        'length="9012345"' "$appcast"
assert_contains "appcast notes"         "<h2>What's changed</h2>" "$appcast"

cdata="$(printf 'text with ]]> inside\n' | render_appcast 0.0.1 1 15.7 v0.0.1 example-org/repo Scout-0.0.1.dmg sig 1 'Wed, 02 Sep 2026 20:15:00 +0000')"
if printf '%s' "$cdata" | xmllint --noout - 2>/dev/null; then echo "ok   appcast survives ]]> in notes"
else echo "FAIL appcast breaks on ]]> in notes"; fails=$((fails + 1)); fi

# ── parse_sign_update ─────────────────────────────────────────────────────────
assert_eq "parse sign_update" "abc/+== 123" "$(parse_sign_update 'sparkle:edSignature="abc/+==" length="123"')"
if parse_sign_update 'garbage' >/dev/null 2>&1; then echo "FAIL parse_sign_update accepted garbage"; fails=$((fails + 1))
else echo "ok   parse_sign_update rejects garbage"; fi

if (( fails > 0 )); then echo "✗ $fails failure(s)"; exit 1; fi
echo "✓ all release-lib tests passed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash scripts/tests/release-lib.test.sh`
Expected: `scripts/tests/release-lib.test.sh: line 8: .../scripts/release-lib.sh: No such file or directory` (exit 1).

- [ ] **Step 3: Implement the library**

Create `scripts/release-lib.sh`:

```bash
#!/usr/bin/env bash
# Pure helpers for scripts/release.sh — sourced, never executed directly.
# Every function is a function of its arguments/stdin (no globals, no git
# side effects except `release_tags`, which only reads), so it can be unit
# tested by scripts/tests/release-lib.test.sh (run in CI). Keep this file
# /bin/bash 3.2 compatible — macOS ships no newer bash.

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
# _cl_heading <md|html> <level> <text>
_cl_heading() {
  if [[ "$1" == html ]]; then
    printf '<h%s>%s</h%s>\n' "$2" "$3" "$2"
  else
    local marks; marks="$(printf '%*s' "$2" '' | tr ' ' '#')"
    printf '%s %s\n\n' "$marks" "$3"
  fi
}
# _cl_para <md|html> <text-already-in-that-format>
_cl_para() {
  if [[ "$1" == html ]]; then printf '<p>%s</p>\n' "$2"; else printf '%s\n\n' "$2"; fi
}
# _cl_list <md|html> <"subject|hash" lines>
_cl_list() {
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

# render_appcast <version> <build> <min-os> <tag> <owner/repo> <dmg-filename>
#                <ed-signature> <length> <pubdate-rfc822>
# stdin: HTML release notes for <description>.
# One-item Sparkle appcast: the app compares <sparkle:version> (CFBundleVersion,
# our commit count) with its own, so the newest release alone is enough.
render_appcast() {
  local version="$1" build="$2" min_os="$3" tag="$4" slug="$5" dmg="$6" sig="$7" length="$8" pubdate="$9"
  local notes; notes="$(cat)"
  notes="${notes//]]>/]]&gt;}"   # a literal ]]> would terminate the CDATA section
  local release_url="https://github.com/$slug/releases/tag/$tag"
  local dmg_url="https://github.com/$slug/releases/download/$tag/$dmg"
  cat <<EOF
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Scout</title>
    <link>https://github.com/$slug/releases</link>
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
  </channel>
</rss>
EOF
}

# parse_sign_update <stdout of sign_update <file>> → "<signature> <length>"
# sign_update prints: sparkle:edSignature="…" length="…"
parse_sign_update() {
  local sig length
  sig="$(printf '%s' "$1" | sed -nE 's/.*sparkle:edSignature="([^"]+)".*/\1/p')"
  length="$(printf '%s' "$1" | sed -nE 's/.* length="([0-9]+)".*/\1/p')"
  [[ -n "$sig" && -n "$length" ]] || return 1
  printf '%s %s\n' "$sig" "$length"
}
```

- [ ] **Step 4: Run the tests and shellcheck**

Run: `bash scripts/tests/release-lib.test.sh && shellcheck scripts/release-lib.sh scripts/tests/release-lib.test.sh`
Expected: every line starts with `ok`, final line `✓ all release-lib tests passed`, and shellcheck prints nothing. (shellcheck is installed at `/opt/homebrew/bin/shellcheck`; if a warning is about `read` without `-r` or unused variables, fix the script rather than adding a disable directive.)

- [ ] **Step 5: Add the CI step**

In `.github/workflows/ci.yml`, insert after the "Toolchain info" step (before "Run ScoutTests"):

```yaml
      # Pure shell helpers behind scripts/release.sh (version rule, changelog
      # + appcast rendering). Cheap, and the appcast must stay well-formed.
      - name: release-lib unit tests
        run: bash scripts/tests/release-lib.test.sh
```

- [ ] **Step 6: Commit**

```bash
git add scripts/release-lib.sh scripts/tests/release-lib.test.sh .github/workflows/ci.yml
git commit -m "build(release): release-lib.sh — version rule, changelog (md+html) and appcast renderers, with tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Wire `scripts/release.sh` — validation, guards, inside-out signing, EdDSA signature, appcast upload

**Files:**
- Modify: `scripts/release.sh` (whole file; the sections below reference the current line numbers)

**Interfaces:**
- Consumes: everything in `scripts/release-lib.sh` (Task 7); `Scout/Info.plist` keys (Task 3); Sparkle tools from the build's SPM artifacts (Task 1); the keychain key (Task 2).
- Produces: `build/release/Scout-<version>.dmg` **and** `build/release/appcast.xml`, both attached to the GitHub release; `PRERELEASE=1` publishes with `--prerelease`.

- [ ] **Step 1: Header comment — document the new behavior**

Replace lines 1–37 (the header comment) with:

```bash
#!/usr/bin/env bash
# Build a Release Scout.app, sign it with Developer ID + hardened runtime,
# notarize and staple both the app and the DMG it ships in, EdDSA-sign the
# DMG for Sparkle, generate the one-item appcast, and publish DMG + appcast as
# a GitHub Release. Installed copies (0.12.0+) find the appcast through the
# stable redirect
#   https://github.com/<owner>/<repo>/releases/latest/download/appcast.xml
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
#   PRERELEASE=1 scripts/release.sh 0.12.0-rc.1 # pre-release: never becomes `latest`
#
# Version rule: the next version is derived from the conventional-commit
# prefixes already used across the repo (and grouped in the changelog below).
# Any `feat:` commit since the latest plain v* tag ⇒ minor bump; otherwise
# (fix / perf / refactor / docs / chore / …) ⇒ patch bump. Pass an explicit
# version to override — e.g. a major/pre-1.0 bump; the script warns if the
# override disagrees with the rule but proceeds with what you passed.
# Pre-release tags (v0.12.0-rc.1) never feed the rule or the changelog range.
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
#   SKIP_RELEASE=1   do everything except tag/push/upload to GitHub
#   PRERELEASE=1     publish as a GitHub pre-release (requires an explicit
#                    suffixed version like 0.12.0-rc.1); excluded from
#                    releases/latest, so real users never see it
```

- [ ] **Step 2: Source the library and rewrite version selection (current lines 39–96)**

Replace everything from `set -euo pipefail` through the signing-identity check with:

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

# Derive `owner/repo` from origin. Needed for the compare link *and* for every
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

(The old `recommend_version` function body and the old `REPO_ROOT`/`BUILD_DIR`/… block are removed — they now live in the library / above.)

- [ ] **Step 3: Build-number guard (right after `BUILD_NUMBER=` is computed, current line 120)**

After `BUILD_NUMBER="$(git -C "$REPO_ROOT" rev-list --count HEAD)"` add:

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

Add after the `if [[ ! -d "$APP" ]]; then … fi` block:

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

Replace the "Signing Scout.app with Developer ID + hardened runtime" block with:

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

Immediately after the `if [[ "${SKIP_NOTARIZE:-0}" != "1" ]]; then … fi` DMG block add:

```bash
echo "→ EdDSA-signing the DMG for Sparkle (after stapling — the signature covers the final bytes)"
SIGN_OUT="$("$SPARKLE_BIN/sign_update" "$DMG")"
if ! read -r ED_SIGNATURE DMG_LENGTH < <(parse_sign_update "$SIGN_OUT"); then
  echo "✗ Could not parse sign_update output: $SIGN_OUT" >&2
  exit 1
fi
echo "  length: $DMG_LENGTH · edSignature: ${ED_SIGNATURE:0:12}…"
```

- [ ] **Step 7: Release notes + appcast, generated before the `SKIP_RELEASE` exit (replace current lines 195–283)**

Delete the current `if [[ "${SKIP_RELEASE:-0}" == "1" ]]; then … fi` block, the `PREV_TAG`/`ORIGIN_URL`/`REPO_SLUG` derivation, and the whole `{ … } > "$NOTES"` block. In their place:

```bash
# ─────────────────────────────────────────────────────────────────────────────
# Release notes (Markdown for GitHub) + appcast (HTML notes inside)
# ─────────────────────────────────────────────────────────────────────────────
# Most recent plain release tag other than the one we're creating.
PREV_TAG="$(release_tags | grep -vx "$TAG" | head -1 || true)"
COMMITS=""
if [[ -n "$PREV_TAG" ]]; then
  # subject + short hash for every commit between PREV_TAG and HEAD.
  COMMITS="$(git -C "$REPO_ROOT" log "$PREV_TAG"..HEAD --no-merges --format='%s|%h')"
fi

NOTES="$BUILD_DIR/release-notes.md"
{
  printf '%s\n' "$COMMITS" | render_changelog md "$PREV_TAG" "$TAG" "$REPO_SLUG"
  echo "---"
  echo
  echo "## Install"
  echo
  echo "**Already running Scout 0.12.0 or later?** It updates itself — wait for the automatic check (every 6 hours) or use **Scout → Check for Updates…**. The steps below are for first installs and for copies older than 0.12.0."
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
  echo "→ SKIP_RELEASE=1 set; not tagging or uploading."
  echo "  DMG:     $DMG"
  echo "  appcast: $APPCAST"
  exit 0
fi
```

- [ ] **Step 8: Tag + upload both assets (replace current lines 285–297)**

```bash
echo "→ Tagging $TAG and creating GitHub release$( [[ "$PRERELEASE" == "1" ]] && echo ' (pre-release)' )"
if git -C "$REPO_ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "  tag $TAG already exists locally — skipping tag/push"
else
  git -C "$REPO_ROOT" tag -a "$TAG" -m "Release $VERSION"
  git -C "$REPO_ROOT" push origin "$TAG"
fi

# bash 3.2: expanding an empty array under `set -u` is an error, so branch.
if [[ "$PRERELEASE" == "1" ]]; then
  gh release create "$TAG" "$DMG" "$APPCAST" --title "Scout $VERSION" --notes-file "$NOTES" --prerelease
else
  gh release create "$TAG" "$DMG" "$APPCAST" --title "Scout $VERSION" --notes-file "$NOTES"
fi

echo "✓ Released $TAG"
echo "  Installed copies (0.12.0+) will pick it up from https://github.com/$REPO_SLUG/releases/latest/download/appcast.xml"
```

- [ ] **Step 9: Static checks**

Run: `bash -n scripts/release.sh && shellcheck -x scripts/release.sh && bash scripts/tests/release-lib.test.sh | tail -1`
Expected: no output from `bash -n`/shellcheck, then `✓ all release-lib tests passed`.

Also confirm the old inline `recommend_version` and the "The bundle has no nested frameworks/helpers" comment are gone: `grep -n "recommend_version()\|no nested frameworks" scripts/release.sh` → no output.

- [ ] **Step 10: Local dry run of the whole pipeline (needs Task 2's key + Task 3's plist)**

Run (universal Release build; several minutes):

```bash
PRERELEASE=1 SKIP_NOTARIZE=1 SKIP_RELEASE=1 scripts/release.sh 0.12.0-rc.0 2>&1 | tail -25
```

Expected, in order: `⚠ Version 0.12.0-rc.0 overrides…`, `→ Sparkle preflight` with `key OK`, `→ Signing Sparkle's nested components…`, `→ Packaging as DMG`, `→ EdDSA-signing the DMG…`, `→ Generating appcast`, `SKIP_RELEASE=1 set` with both paths, exit 0.

Then verify the artifacts:

```bash
codesign --verify --strict --deep --verbose=2 build/Build/Products/Release/Scout.app 2>&1 | tail -2
codesign -dv build/Build/Products/Release/Scout.app/Contents/Frameworks/Sparkle.framework/Versions/B/Autoupdate 2>&1 | grep -E 'flags|Authority=Developer'
lipo -info build/Build/Products/Release/Scout.app/Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle
xmllint --noout build/release/appcast.xml && grep -E 'sparkle:version|shortVersionString|edSignature|length=' build/release/appcast.xml
SIG="$(sed -nE 's/.*sparkle:edSignature="([^"]+)".*/\1/p' build/release/appcast.xml)"
"$(find build/SourcePackages/artifacts -type d -path '*/sparkle/Sparkle/bin' | head -1)/sign_update" --verify build/release/Scout-0.12.0-rc.0.dmg "$SIG" && echo "signature verifies"
```

Expected: `valid on disk` / `satisfies its Designated Requirement`; `flags=0x10000(runtime)` and a `Developer ID Application` authority on `Autoupdate`; `Architectures in the fat file: … x86_64 arm64`; the four appcast fields with the rc.0 version and the commit-count build; `signature verifies`.

- [ ] **Step 11: Commit**

```bash
git add scripts/release.sh
git commit -m "feat(release): sign Sparkle inside-out, EdDSA-sign the DMG, publish appcast.xml; PRERELEASE=1 + build-number guard

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Docs — README and spec touch-up

**Files:**
- Modify: `README.md:11-20` (Install) and `README.md:96-104` (Cutting a release)
- Modify: `docs/superpowers/specs/2026-09-02-auto-update-design.md` (§4 step 9 and the Testing → Script bullet, which name `scripts/appcast.sh`)

- [ ] **Step 1: README → Install**

Replace the "Install (prebuilt DMG)" section (lines 11–20) with:

```markdown
## Install (prebuilt DMG)

The fastest path if you just want to run the app:

1. Go to the [Releases](https://github.com/Raven-Scout/Scout/releases) page and download the latest `Scout-*.dmg`.
2. Open the DMG and drag **Scout.app** into the **Applications** folder.
3. Launch it. Scout is signed with a Developer ID and notarized by Apple, so it opens with a normal double-click.
4. Press ⌘, to open Settings and fill in your Linear workspace and author name.

That is the last DMG you download: from 0.12.0 on, Scout checks for a new release every 6 hours (and shortly after launch), downloads it in the background, and asks before installing and relaunching. Check on demand with **Scout → Check for Updates…**, the same item in the menu-bar extra, or Settings → Updates, where the automatic check/download toggles live.

The app expects a Scout instance at `~/Scout/`. Install the [scout-plugin](https://github.com/Raven-Scout/scout-plugin) into Claude Code and run `/scout-setup` first if you don't have one yet.
```

- [ ] **Step 2: README → Cutting a release**

Replace the "Cutting a release" section (lines 96–104) with:

```markdown
## Cutting a release

Maintainers: `scripts/release.sh [<version>]` builds a universal (arm64+x86_64) DMG, signs the app with Developer ID + hardened runtime (Sparkle's nested helpers first, inside-out), notarizes and staples **both the app and the DMG** via Apple, EdDSA-signs the DMG for Sparkle, generates `appcast.xml`, tags `v<version>`, pushes the tag, and creates a GitHub Release with the DMG **and** the appcast attached. Installed copies find the appcast through `https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml`, so publishing the release *is* shipping the update. Without an argument the version is derived from the commits since the last tag (`feat:` → minor, otherwise → patch).

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
PRERELEASE=1 scripts/release.sh 0.12.0-rc.1 # GitHub pre-release; never becomes `latest`
```

Set `SKIP_RELEASE=1` to build the DMG + appcast locally without tagging or uploading. Sparkle only compares build numbers (`CFBundleVersion` = commit count), so a release must be cut from a commit ahead of the previous tag; the script enforces this. Sparkle never downgrades: fix a bad release forward with a patch release rather than by deleting it. Pre-releases are the way to rehearse the update path (see the design doc, §6): install rc.1, publish rc.2, launch rc.1 with `--env SCOUT_APPCAST_URL=<rc.2 appcast asset URL>` and confirm it updates.
```

- [ ] **Step 3: Spec touch-up (helper lives in `release-lib.sh`, not `appcast.sh`)**

In `docs/superpowers/specs/2026-09-02-auto-update-design.md`:

- §4 step 9: replace "with `scripts/appcast.sh`, a small pure helper (arguments in, XML on stdout) so it can be smoke-tested in isolation" with "with `render_appcast` from `scripts/release-lib.sh` — a sourced library of pure helpers (version rule, changelog in Markdown and HTML, appcast) covered by `scripts/tests/release-lib.test.sh`".
- Testing → Script bullet: replace "`scripts/appcast.sh` smoke test — run with dummy arguments, `xmllint --noout`, and grep the required fields. Added as a cheap CI step so the helper cannot silently break." with "`scripts/tests/release-lib.test.sh` — bash unit tests for every helper in `scripts/release-lib.sh` (version kinds, bump rule, both changelog formats incl. HTML escaping, appcast well-formedness via `xmllint`, `sign_update` output parsing). Runs in CI."

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-02-auto-update-design.md
git commit -m "docs(updates): README install/release sections for in-app updates; spec names release-lib.sh

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: End-to-end rehearsal on pre-releases, then the first Sparkle release (owner: Jordan)

**Files:** none. This proves the pipeline before 0.12.0 — the one release where a broken updater cannot be fixed *through* the updater.

**Owner:** Jordan, on the release machine, after Tasks 1–9 are merged to `main` (the release must be cut from `main` so the build number is ahead of `v0.11.2`). An agent may run it only with Jordan's explicit go-ahead.

- [ ] **Step 1: Publish rc.1 and install it somewhere scratch**

```bash
git checkout main && git pull --ff-only
PRERELEASE=1 scripts/release.sh 0.12.0-rc.1
mkdir -p ~/scout-e2e && hdiutil attach build/release/Scout-0.12.0-rc.1.dmg -mountpoint /Volumes/scout-rc1 -nobrowse
cp -R /Volumes/scout-rc1/Scout.app ~/scout-e2e/Scout.app && hdiutil detach /Volumes/scout-rc1
```

Expected: `✓ Released v0.12.0-rc.1`; the release page shows both `Scout-0.12.0-rc.1.dmg` and `appcast.xml`, marked **Pre-release**; `curl -sI https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml` still redirects to `v0.11.2` (i.e. 404 there) — the pre-release did not move `latest`.

- [ ] **Step 2: Land one commit, publish rc.2**

Any commit on `main` will do (Task 9's docs commit counts if it merged after rc.1; otherwise a `chore:` bump). Then:

```bash
PRERELEASE=1 scripts/release.sh 0.12.0-rc.2
```

Expected: `✓ Released v0.12.0-rc.2` with a build number one (or more) higher than rc.1's — compare the `<sparkle:version>` values in the two appcasts.

- [ ] **Step 3: Drive rc.1 to rc.2 through Sparkle**

```bash
open -a ~/scout-e2e/Scout.app --env SCOUT_APPCAST_URL=https://github.com/Raven-Scout/Scout/releases/download/v0.12.0-rc.2/appcast.xml
```

Then in the app: **Scout → Check for Updates…**.

Expected: Sparkle's "A new version of Scout is available!" sheet shows **Scout 0.12.0-rc.2** with the rendered "What's changed" notes; **Install and Relaunch** downloads (~9 MB), relaunches, and Settings → About reads `0.12.0-rc.2 (<build>)`. `codesign -dv ~/scout-e2e/Scout.app 2>&1 | grep TeamIdentifier` still shows `74SD45TPC5`.

If the sheet shows an error instead, read `log show --last 10m --predicate 'process == "Scout" AND (eventMessage CONTAINS "Sparkle" OR subsystem CONTAINS "sparkle")' --info` — a signature error means the key preflight or `sign_update` step is wrong; a "not properly signed" error means the inside-out signing missed a component. Fix on a branch, merge, and repeat from Step 1 with `rc.3`/`rc.4`.

- [ ] **Step 4: Confirm the automatic path and the Settings toggles**

Quit and relaunch `~/scout-e2e/Scout.app` normally (no env var). Settings → Updates: both toggles on, "Last checked" populated after ~1 min (Sparkle's launch check). Toggle *Download and install automatically* off and on; quit and relaunch; the state persists.

- [ ] **Step 5: Clean up the rehearsal**

```bash
gh release delete v0.12.0-rc.1 --repo Raven-Scout/Scout --cleanup-tag --yes
gh release delete v0.12.0-rc.2 --repo Raven-Scout/Scout --cleanup-tag --yes
rm -rf ~/scout-e2e
```

- [ ] **Step 6: Ship 0.12.0**

```bash
scripts/release.sh
```

Expected: auto-selects `0.12.0` (the `feat:` commits bump the minor), `✓ Released v0.12.0`, and
`curl -sI https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml | grep -i location` now points at `v0.12.0/appcast.xml`. Announce with the note that 0.12.0 is the last manual download.

---

## Self-review against the spec

- **§1 appcast hosting / one item / fields** → Task 7 (`render_appcast`, `min OS` from the built plist in Task 8 Step 4), Task 8 Steps 6–8.
- **§2 key, backup, layered trust** → Task 2; preflight in Task 8 Step 4; Developer ID continuity is Sparkle's own check, exercised in Task 10.
- **§3 dependency (exact 2.9.6)** → Task 1. **Typed Info.plist + keys** → Task 3. **`AppUpdater` semantics (never starts in Debug, compiles everywhere, KVO mirror, env override delegate)** → Tasks 4–5. **Surfaces** → Task 6.
- **§4 release script items 1–11** → Task 8 (1–2: Step 2; 3: Step 3; 4–5: Step 4; 6: Step 5; 7: Step 6; 8–9: Task 7 + Step 7; 10: Step 8; 11: Step 7). `SKIP_NOTARIZE`/`SKIP_RELEASE` still work (Step 7 exits after the appcast).
- **§5 developer setup / README** → Task 9.
- **§6 rollout + E2E** → Task 10.
- **Testing** → `UpdateInfoPlistTests` (Task 3), `UpdateFeedTests` (Task 4), `AppUpdaterTests` (Task 5), `release-lib.test.sh` + CI step (Task 7), manual E2E (Task 10).
- **Deviation from spec:** the appcast helper is `render_appcast` in `scripts/release-lib.sh` rather than a standalone `scripts/appcast.sh`; Task 9 Step 3 updates the spec to match.
- **Type consistency:** `AppUpdater.isEnabled` / `canCheckForUpdates` / `lastUpdateCheckDate` / `automaticallyChecksForUpdates` / `automaticallyDownloadsUpdates` / `checkForUpdates()` are used identically in Tasks 5 and 6; `UpdateFeed.overrideEnvironmentKey` / `overrideURLString(environment:)` identically in Tasks 4 and 5; library function names identically in Tasks 7 and 8.

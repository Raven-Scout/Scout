# Action Items Display Profile: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revised 2026-10-03 per review:** no "oldest first" sort, no `fields.plan`, no reserved keys, no commits from the app, the List/Board switch is session-only, sub-tasks whose parent is filtered out stand alone, and the arranged sections are cached. Details in the spec's revision note.

**Goal:** Let the user choose the Action Items default view, sort, grouping, density, and card fields, kept in `scout-profile.json` in the vault root and editable in Settings and from a View menu in the Action Items toolbar (#290).

**Architecture:** A pure `DisplayProfileCodec` parses and patches the file under the rules in spec section 4. `DisplayProfileService` (`@MainActor ObservableObject`) owns the file: it reads it at init, follows outside edits through `FileSystemEventSource`, and writes in-app changes as patches. It never commits. It also holds the session-only current List/Board view. A pure `ActionItemsArrangement` applies sort and grouping to the already filtered sections, and `ActionItemsView` caches its result. Views take the display values as plain parameters with today's behavior as the default, so every existing call site and test keeps compiling.

**Tech Stack:** Swift 5 mode with default MainActor isolation, SwiftUI, Swift Testing (`import Testing`, `@Test`, `#expect`), Xcode project with filesystem-synchronized groups.

**Spec:** `../specs/2026-10-01-action-items-display-profile-design.md`

## Global Constraints

- **Defaults render exactly as today.** `ActionItemsDisplay()` is file order, grouped by section, comfortable, refs and snooze on, comment count off. Every new view parameter defaults to that.
- **The app reads and writes JSON only.** No YAML, no `scout-config.yaml`, nothing under `knowledge-base/`.
- **The app never commits the profile,** and the List/Board switch never writes it.
- **The path always comes from `AppState.scoutDirectory`.** Never `~/Scout` in code.
- **Never overwrite a file the app could not fully read** (unreadable, or `schema` above 1).
- **New files need no project edits.** `PBXFileSystemSynchronizedRootGroup` picks up files under `Scout/` and `ScoutTests/`.
- **Design tokens only.** `DS.*` for colors and fonts.
- **Fixtures are anonymized** per `CLAUDE.md` (Alex, Priya, Sam, `PROJ-1234`, `example-org/<repo>`), and every new literal is checked against a real vault with the `grep` recipe there before it is committed.
- **Paths and commands are relative to `apps/macos/`.** The app moved there in the monorepo; run `xcodebuild` from that directory.
- **Build/test commands:** `xcodebuild -scheme Scout -destination 'platform=macOS' build` and `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<TypeName>`. The filter takes the Swift TYPE name, not the `@Suite("...")` display name, and a filter that matches nothing still prints `** TEST SUCCEEDED **`, so confirm `Test run with N tests`.
- **No new compiler warnings.** CI does not fail on warnings, so compare the `warning:` count of a clean build before and after.

---

### Task 1: FileWatcher on a file that does not exist yet

The service watches `scout-profile.json` directly, and the file usually does not exist at launch. FSEvents supports that, but only on the real path: a watch through `/var` (a symlink to `/private/var`, which is where `FileManager` temp directories live) gets no events at all. This task pins both facts.

**Files:**
- Create: `Scout/Utilities/URL+RealPath.swift`
- Modify: `ScoutTests/Services/FileWatcherTests.swift`

**Interfaces:**
- Produces: `extension URL { nonisolated func resolvingRealPath() -> URL }`

- [ ] **Step 1: Write the failing test**

Append to `FileWatcherTests`. The tests use its `firstEvent(named:from:within:)` helper (Raven-Scout/scout-app-legacy#123), which waits for a named event with a liveness budget; it consumes the stream, so the replace is watched by a second stream:

```swift
    /// The display profile is watched by file path before the file exists.
    /// FSEvents reports its creation and a later atomic replace, as long as
    /// the watched path is the real one: temp directories sit under the `/var`
    /// symlink, which FSEvents never matches.
    @Test func emitsEventsForAFileThatDoesNotExistYet() async throws {
        let tmp = try FileManager.default.url(
            for: .itemReplacementDirectory,
            in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory,
            create: true
        ).resolvingRealPath()
        defer { try? FileManager.default.removeItem(at: tmp) }
        let file = tmp.appendingPathComponent("scout-profile.json")
        #expect(!FileManager.default.fileExists(atPath: file.path))

        let beforeCreate = FileWatcher().events(for: file)
        try await Task.sleep(nanoseconds: 300_000_000)
        try Data("{}".utf8).write(to: file, options: .atomic)
        let created = await Self.firstEvent(named: file.lastPathComponent, from: beforeCreate, within: .seconds(30))
        #expect(created != nil, "expected an event for the file's creation")

        let beforeReplace = FileWatcher().events(for: file)
        try await Task.sleep(nanoseconds: 300_000_000)
        try Data(#"{"schema":1}"#.utf8).write(to: file, options: .atomic)
        let replaced = await Self.firstEvent(named: file.lastPathComponent, from: beforeReplace, within: .seconds(30))
        #expect(replaced != nil, "expected an event for the atomic replace")
    }

    @Test func realPathResolvesTheVarSymlink() {
        #expect(URL(fileURLWithPath: "/var", isDirectory: true).resolvingRealPath().path == "/private/var")
        #expect(URL(fileURLWithPath: "/no/such/dir").resolvingRealPath().path == "/no/such/dir")
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/FileWatcherTests`
Expected: FAIL, compile error `value of type 'URL' has no member 'resolvingRealPath'`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Utilities/URL+RealPath.swift`:

```swift
import Foundation

extension URL {
    /// The path with every symlink resolved, as FSEvents reports it. Watching
    /// an unresolved path (`/var/...` rather than `/private/var/...`, or a
    /// vault reached through a symlink) delivers no events. Returns `self`
    /// when the path does not exist.
    ///
    /// `realpath(3)`, not `resolvingSymlinksInPath()`: Foundation strips a
    /// leading `/private`, which is exactly the component FSEvents needs.
    nonisolated func resolvingRealPath() -> URL {
        guard let resolved = realpath(path, nil) else { return self }
        defer { free(resolved) }
        return URL(fileURLWithPath: String(cString: resolved), isDirectory: hasDirectoryPath)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: same command. Expected: PASS, 3 test functions.

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/URL+RealPath.swift ScoutTests/Services/FileWatcherTests.swift
git commit -m "test(file-watcher): pin watching a file path before the file exists"
```

---

### Task 2: Model and codec

The rules from spec section 4, as pure functions. `JSONSerialization` rather than `Codable`: per-key fallback, unknown-key reporting, and preservation on write all need the raw object.

**Files:**
- Create: `Scout/Profile/DisplayProfile.swift`
- Create: `Scout/Profile/DisplayProfileCodec.swift`
- Modify: `Scout/ActionItems/ActionItemsViewMode.swift` (mark `nonisolated`, add `Sendable`, update the doc comment that names `@SceneStorage`)
- Create: `ScoutTests/Profile/DisplayProfileCodecTests.swift`

**Interfaces:**
- Produces:
  - `nonisolated struct DisplayProfile: Equatable, Sendable` with `var actionItems: ActionItemsDisplay`, `static let currentSchema = 1`, `var entries: [Entry]`, `func changedEntries(from:) -> [Entry]`.
  - `DisplayProfile.Entry { let path: [String]; let value: Value }`, `Value { case string(String), bool(Bool) }`.
  - `nonisolated struct ActionItemsDisplay: Equatable, Sendable` with `defaultView: ActionItemsViewMode`, `sort: Sort` (`fileOrder`, `alphabetical`), `grouping: Grouping` (JSON key `group`; `section`, `none`), `density: Density` (`comfortable`, `compact`), `fields: Fields` (`refs`, `snooze`, `comments`); each enum `String, CaseIterable, Identifiable, Sendable` with `displayName`.
  - `nonisolated enum DisplayProfileCodec` with `fileName`, `Failure { case unreadable(String), unsupportedSchema(Int) }`, `Decoded { profile, warnings }`, `decode(_:) -> Result<Decoded, Failure>`, `patch(_:with:) throws -> Data`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Profile/DisplayProfileCodecTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("DisplayProfileCodec")
struct DisplayProfileCodecTests {
    private static func decode(_ json: String) -> Result<DisplayProfileCodec.Decoded, DisplayProfileCodec.Failure> {
        DisplayProfileCodec.decode(Data(json.utf8))
    }

    private static func profile(_ change: (inout ActionItemsDisplay) -> Void) -> DisplayProfile {
        var p = DisplayProfile()
        change(&p.actionItems)
        return p
    }

    // MARK: decode: files that apply

    static let valid: [(String, DisplayProfile)] = [
        ("", DisplayProfile()),
        ("  \n", DisplayProfile()),
        ("{}", DisplayProfile()),
        (#"{"schema":1}"#, DisplayProfile()),
        (#"{"schema":1,"actionItems":{"defaultView":"board"}}"#, profile { $0.defaultView = .board }),
        (#"{"schema":1,"actionItems":{"sort":"alphabetical","group":"none","density":"compact"}}"#,
         profile { $0.sort = .alphabetical; $0.grouping = .none; $0.density = .compact }),
        (#"{"schema":1,"actionItems":{"fields":{"refs":false,"snooze":false,"comments":true}}}"#,
         profile { $0.fields = .init(refs: false, snooze: false, comments: true) }),
        (#"{"schema":1.0,"actionItems":{"sort":"alphabetical"}}"#, profile { $0.sort = .alphabetical }),
    ]

    @Test(arguments: valid)
    func validFilesApply(_ json: String, _ expected: DisplayProfile) throws {
        let decoded = try Self.decode(json).get()
        #expect(decoded.profile == expected)
        #expect(decoded.warnings.isEmpty)
    }

    // MARK: decode: files that apply with warnings

    static let warned: [(String, DisplayProfile, [String])] = [
        (#"{"actionItems":{"density":"compact"}}"#, profile { $0.density = .compact },
         [#""schema" is missing; reading it as 1"#]),
        (#"{"schema":1,"theme":"dark"}"#, DisplayProfile(), [#"unknown key "theme""#]),
        (#"{"schema":1,"sidebar":{"order":["kb"]}}"#, DisplayProfile(), [#"unknown key "sidebar""#]),
        (#"{"schema":1,"actionItems":{"foo":1,"sort":"alphabetical"}}"#, profile { $0.sort = .alphabetical },
         [#"unknown key "actionItems.foo""#]),
        (#"{"schema":1,"actionItems":{"fields":{"plan":true}}}"#, DisplayProfile(),
         [#"unknown key "actionItems.fields.plan""#]),
        (#"{"schema":1,"actionItems":{"sort":"oldestFirst","density":"compact"}}"#, profile { $0.density = .compact },
         [#""actionItems.sort" has an unsupported value; using the default"#]),
        (#"{"schema":1,"actionItems":{"fields":{"refs":"no","snooze":0}}}"#, DisplayProfile(),
         [#""actionItems.fields.refs" is not true or false; using the default"#,
          #""actionItems.fields.snooze" is not true or false; using the default"#]),
        (#"{"schema":1,"actionItems":[]}"#, DisplayProfile(), [#""actionItems" is not an object"#]),
    ]

    @Test(arguments: warned)
    func warnedFilesApplyWhatTheyCan(_ json: String, _ expected: DisplayProfile, _ warnings: [String]) throws {
        let decoded = try Self.decode(json).get()
        #expect(decoded.profile == expected)
        #expect(decoded.warnings == warnings)
    }

    // MARK: decode: files the app must not use

    static let refused: [(String, DisplayProfileCodec.Failure)] = [
        ("{ not json", .unreadable("not valid JSON")),
        ("[1, 2]", .unreadable("the top level is not an object")),
        (#"{"schema":"1"}"#, .unreadable(#""schema" is not a whole number"#)),
        (#"{"schema":1.5}"#, .unreadable(#""schema" is not a whole number"#)),
        (#"{"schema":0}"#, .unreadable(#""schema" must be 1 or higher"#)),
        (#"{"schema":2,"actionItems":{"defaultView":"board"}}"#, .unsupportedSchema(2)),
    ]

    @Test(arguments: refused)
    func unusableFilesAreRefused(_ json: String, _ failure: DisplayProfileCodec.Failure) {
        #expect(Self.decode(json) == .failure(failure))
    }

    // MARK: patch

    private static func object(_ data: Data) throws -> NSDictionary {
        try #require(try JSONSerialization.jsonObject(with: data) as? NSDictionary)
    }

    @Test func firstWriteHoldsOnlyTheChangeAndTheSchema() throws {
        let entries = Self.profile { $0.defaultView = .board }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(nil, with: entries)
        #expect(try Self.object(data) == ["schema": 1, "actionItems": ["defaultView": "board"]] as NSDictionary)
    }

    @Test func patchPreservesWhatTheAppDoesNotUnderstand() throws {
        let existing = Data(#"{"schema":1,"sidebar":{"order":["kb"]},"theme":"dark","actionItems":{"sort":"planned","foo":[1],"fields":{"tags":true}}}"#.utf8)
        let entries = Self.profile { $0.density = .compact }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(existing, with: entries)
        let expected: NSDictionary = [
            "schema": 1,
            "sidebar": ["order": ["kb"]],
            "theme": "dark",
            "actionItems": ["sort": "planned", "foo": [1], "density": "compact", "fields": ["tags": true]],
        ]
        #expect(try Self.object(data) == expected)
    }

    @Test func patchAddsAMissingSchema() throws {
        let existing = Data(#"{"actionItems":{"sort":"alphabetical"}}"#.utf8)
        let entries = Self.profile { $0.fields.comments = true }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(existing, with: entries)
        #expect(try Self.object(data)["schema"] as? Int == 1)
    }

    @Test(arguments: ["{ not json", #"{"schema":2}"#])
    func patchRefusesFilesItMustNotRewrite(_ json: String) {
        #expect(throws: DisplayProfileCodec.Failure.self) {
            try DisplayProfileCodec.patch(Data(json.utf8), with: [])
        }
    }

    @Test func outputIsPrettySortedAndEndsInANewline() throws {
        let entries = Self.profile { $0.sort = .alphabetical; $0.defaultView = .board }.changedEntries(from: DisplayProfile())
        let text = String(decoding: try DisplayProfileCodec.patch(nil, with: entries), as: UTF8.self)
        #expect(text == """
        {
          "actionItems" : {
            "defaultView" : "board",
            "sort" : "alphabetical"
          },
          "schema" : 1
        }

        """)
    }

    @Test func changedEntriesListOnlyDifferences() {
        let old = DisplayProfile()
        let new = Self.profile { $0.grouping = .none; $0.fields.refs = false }
        #expect(new.changedEntries(from: old) == [
            .init(path: ["actionItems", "group"], value: .string("none")),
            .init(path: ["actionItems", "fields", "refs"], value: .bool(false)),
        ])
        #expect(old.changedEntries(from: old).isEmpty)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test ... -only-testing:ScoutTests/DisplayProfileCodecTests`
Expected: FAIL, compile error `cannot find 'DisplayProfileCodec' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Profile/DisplayProfile.swift`:

```swift
import Foundation

/// The user's display choices, kept in `scout-profile.json` in the vault root.
/// Only the Action Items section exists so far. Other sections are added with
/// the features that read them; until then the codec reports them as unknown
/// and preserves them on write.
nonisolated struct DisplayProfile: Equatable, Sendable {
    static let currentSchema = 1

    var actionItems = ActionItemsDisplay()

    /// One leaf of the file: its key path below the root and its value.
    struct Entry: Equatable, Sendable {
        enum Value: Equatable, Sendable {
            case string(String)
            case bool(Bool)
        }
        let path: [String]
        let value: Value
    }

    /// Every leaf this profile describes, in a fixed order.
    var entries: [Entry] {
        actionItems.entries.map { Entry(path: ["actionItems"] + $0.path, value: $0.value) }
    }

    /// The leaves whose value differs from `old`. A write patches only these,
    /// so keys the user never touched keep following the app's defaults.
    func changedEntries(from old: DisplayProfile) -> [Entry] {
        let before = Dictionary(uniqueKeysWithValues: old.entries.map { ($0.path, $0.value) })
        return entries.filter { before[$0.path] != $0.value }
    }
}

/// Action Items display options (#52). Every default is today's rendering.
nonisolated struct ActionItemsDisplay: Equatable, Sendable {
    enum Sort: String, CaseIterable, Identifiable, Sendable {
        case fileOrder, alphabetical
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .fileOrder:    return "File order"
            case .alphabetical: return "Alphabetical"
            }
        }
    }

    enum Grouping: String, CaseIterable, Identifiable, Sendable {
        case section, none
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .section: return "By section"
            case .none:    return "One list"
            }
        }
    }

    enum Density: String, CaseIterable, Identifiable, Sendable {
        case comfortable, compact
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .comfortable: return "Comfortable"
            case .compact:     return "Compact"
            }
        }
    }

    /// What the collapsed list card and the board card show. The expanded
    /// card always shows everything.
    struct Fields: Equatable, Sendable {
        var refs = true
        var snooze = true
        var comments = false
    }

    var defaultView: ActionItemsViewMode = .list
    var sort: Sort = .fileOrder
    var grouping: Grouping = .section
    var density: Density = .comfortable
    var fields = Fields()

    var entries: [DisplayProfile.Entry] {
        [
            .init(path: ["defaultView"], value: .string(defaultView.rawValue)),
            .init(path: ["sort"], value: .string(sort.rawValue)),
            .init(path: ["group"], value: .string(grouping.rawValue)),
            .init(path: ["density"], value: .string(density.rawValue)),
            .init(path: ["fields", "refs"], value: .bool(fields.refs)),
            .init(path: ["fields", "snooze"], value: .bool(fields.snooze)),
            .init(path: ["fields", "comments"], value: .bool(fields.comments)),
        ]
    }
}
```

Create `Scout/Profile/DisplayProfileCodec.swift`:

```swift
import Foundation

/// Parsing and patching for `scout-profile.json`, as pure functions. The rules
/// are spec section 4: an unknown key is reported and preserved, a bad value
/// falls back for that key only, and a file the app cannot fully read is never
/// rewritten.
nonisolated enum DisplayProfileCodec {
    static let fileName = "scout-profile.json"

    enum Failure: Error, Equatable, Sendable {
        case unreadable(String)
        case unsupportedSchema(Int)
    }

    struct Decoded: Equatable, Sendable {
        var profile: DisplayProfile
        var warnings: [String]
    }

    static func decode(_ data: Data) -> Result<Decoded, Failure> {
        let root: [String: Any]
        switch object(from: data) {
        case .failure(let failure): return .failure(failure)
        case .success(let parsed): root = parsed
        }
        guard !root.isEmpty else { return .success(Decoded(profile: DisplayProfile(), warnings: [])) }

        var warnings: [String] = []
        if let raw = root["schema"] {
            guard let number = raw as? NSNumber, !isBool(number),
                  number.doubleValue == number.doubleValue.rounded() else {
                return .failure(.unreadable("\"schema\" is not a whole number"))
            }
            if number.intValue > DisplayProfile.currentSchema { return .failure(.unsupportedSchema(number.intValue)) }
            if number.intValue < 1 { return .failure(.unreadable("\"schema\" must be 1 or higher")) }
        } else {
            warnings.append("\"schema\" is missing; reading it as 1")
        }

        let known: Set<String> = ["schema", "actionItems"]
        for key in root.keys.sorted() where !known.contains(key) {
            warnings.append("unknown key \"\(key)\"")
        }

        var profile = DisplayProfile()
        if let raw = root["actionItems"] {
            if let section = raw as? [String: Any] {
                profile.actionItems = actionItems(section, warnings: &warnings)
            } else {
                warnings.append("\"actionItems\" is not an object")
            }
        }
        return .success(Decoded(profile: profile, warnings: warnings))
    }

    /// The bytes to write after applying `entries` to `existing`, the file's
    /// current bytes (`nil` when it does not exist). Every key not in `entries`
    /// is kept as it is. Throws when `existing` is a file the app may not
    /// rewrite.
    static func patch(_ existing: Data?, with entries: [DisplayProfile.Entry]) throws -> Data {
        var root: [String: Any] = [:]
        if let existing {
            if case .failure(let failure) = decode(existing) { throw failure }
            if case .success(let parsed) = object(from: existing) { root = parsed }
        }
        root["schema"] = DisplayProfile.currentSchema
        for entry in entries {
            set(entry.value.json, at: entry.path[...], in: &root)
        }
        var data = try JSONSerialization.data(
            withJSONObject: root,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
        data.append(0x0A)
        return data
    }

    // MARK: - Private

    /// The root object. Whitespace-only content counts as an empty object, so
    /// an emptied file behaves like a missing one.
    private static func object(from data: Data) -> Result<[String: Any], Failure> {
        if data.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0A || $0 == 0x0D }) { return .success([:]) }
        guard let parsed = try? JSONSerialization.jsonObject(with: data) else {
            return .failure(.unreadable("not valid JSON"))
        }
        guard let root = parsed as? [String: Any] else {
            return .failure(.unreadable("the top level is not an object"))
        }
        return .success(root)
    }

    private static func actionItems(_ dict: [String: Any], warnings: inout [String]) -> ActionItemsDisplay {
        let path = "actionItems"
        let known: Set<String> = ["defaultView", "sort", "group", "density", "fields"]
        for key in dict.keys.sorted() where !known.contains(key) {
            warnings.append("unknown key \"\(path).\(key)\"")
        }
        var out = ActionItemsDisplay()
        if let v: ActionItemsViewMode = option(dict, "defaultView", in: path, &warnings) { out.defaultView = v }
        if let v: ActionItemsDisplay.Sort = option(dict, "sort", in: path, &warnings) { out.sort = v }
        if let v: ActionItemsDisplay.Grouping = option(dict, "group", in: path, &warnings) { out.grouping = v }
        if let v: ActionItemsDisplay.Density = option(dict, "density", in: path, &warnings) { out.density = v }
        if let raw = dict["fields"] {
            guard let fields = raw as? [String: Any] else {
                warnings.append("\"\(path).fields\" is not an object")
                return out
            }
            let fieldsPath = "\(path).fields"
            let knownFields: Set<String> = ["refs", "snooze", "comments"]
            for key in fields.keys.sorted() where !knownFields.contains(key) {
                warnings.append("unknown key \"\(fieldsPath).\(key)\"")
            }
            if let v = flag(fields, "refs", in: fieldsPath, &warnings) { out.fields.refs = v }
            if let v = flag(fields, "snooze", in: fieldsPath, &warnings) { out.fields.snooze = v }
            if let v = flag(fields, "comments", in: fieldsPath, &warnings) { out.fields.comments = v }
        }
        return out
    }

    private static func option<E: RawRepresentable>(
        _ dict: [String: Any], _ key: String, in path: String, _ warnings: inout [String]
    ) -> E? where E.RawValue == String {
        guard let raw = dict[key] else { return nil }
        if let string = raw as? String, let value = E(rawValue: string) { return value }
        warnings.append("\"\(path).\(key)\" has an unsupported value; using the default")
        return nil
    }

    private static func flag(
        _ dict: [String: Any], _ key: String, in path: String, _ warnings: inout [String]
    ) -> Bool? {
        guard let raw = dict[key] else { return nil }
        if let number = raw as? NSNumber, isBool(number) { return number.boolValue }
        warnings.append("\"\(path).\(key)\" is not true or false; using the default")
        return nil
    }

    /// `JSONSerialization` returns `true` and `1` as the same `NSNumber`
    /// class; only the CF type tells them apart.
    private static func isBool(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    private static func set(_ value: Any, at path: ArraySlice<String>, in dict: inout [String: Any]) {
        guard let key = path.first else { return }
        guard path.count > 1 else {
            dict[key] = value
            return
        }
        var child = dict[key] as? [String: Any] ?? [:]
        set(value, at: path.dropFirst(), in: &child)
        dict[key] = child
    }
}

private extension DisplayProfile.Entry.Value {
    nonisolated var json: Any {
        switch self {
        case .string(let s): return s
        case .bool(let b):   return b
        }
    }
}
```

In `Scout/ActionItems/ActionItemsViewMode.swift`, change the declaration to `nonisolated enum ActionItemsViewMode: String, CaseIterable, Identifiable, Hashable, Sendable` (the codec decodes it outside the main actor) and replace "Persists across launches via `@SceneStorage("actionItemsView")`" with "The tab opens in `scout-profile.json`'s `actionItems.defaultView`; the toolbar switch changes the session's view through `DisplayProfileService.currentView`".

- [ ] **Step 4: Run tests to verify they pass**

Run: same command. Expected: PASS, 9 test functions.

- [ ] **Step 5: Commit**

```bash
git add Scout/Profile ScoutTests/Profile Scout/ActionItems/ActionItemsViewMode.swift
git commit -m "feat(profile): scout-profile.json model and codec for Action Items display options"
```

---

### Task 3: `DisplayProfileService`

Owns the file at runtime and holds the session-only current view. The load and watch shape follows `UsageTrackerService` (`loadInitial()` at `Scout/Services/UsageTrackerService.swift:18`, `startWatching()` at `:34`); the logger follows `ConnectorHealthService.swift:41`. It never commits: the next session that commits the vault picks the file up.

**Files:**
- Create: `Scout/Profile/DisplayProfileService.swift`
- Create: `ScoutTests/Profile/DisplayProfileServiceTests.swift`

**Interfaces:**
- Consumes: `DisplayProfileCodec`, `FileSystemEventSource`, `URL.resolvingRealPath()`.
- Produces:
  - `enum DisplayProfileStatus: Equatable { case ok(warnings: [String]), unreadable(reason: String), unsupportedSchema(found: Int); var canWrite: Bool }`
  - `@MainActor final class DisplayProfileService: ObservableObject` with `@Published private(set) var profile`, `status`, `writeError: String?`; `@Published var currentView: ActionItemsViewMode`; `let fileURL: URL`; `init(scoutDirectory:fileEvents:)`; `startWatching()`; `reload()`; `update(_:)`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Profile/DisplayProfileServiceTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("DisplayProfileService", .serialized)
struct DisplayProfileServiceTests {
    private let vault: URL
    private let fs = InjectableFS()

    init() throws {
        vault = FileManager.default.temporaryDirectory.appendingPathComponent("profile-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
    }

    private func makeService() -> DisplayProfileService {
        DisplayProfileService(scoutDirectory: vault, fileEvents: fs)
    }

    private func write(_ json: String, to service: DisplayProfileService) throws {
        try Data(json.utf8).write(to: service.fileURL, options: .atomic)
        service.reload()
    }

    private func onDisk(_ service: DisplayProfileService) throws -> NSDictionary? {
        guard let data = FileManager.default.contents(atPath: service.fileURL.path) else { return nil }
        return try JSONSerialization.jsonObject(with: data) as? NSDictionary
    }

    @Test func missingFileGivesDefaultsAndStaysMissing() throws {
        let service = makeService()
        #expect(service.profile == DisplayProfile())
        #expect(service.status == .ok(warnings: []))
        #expect(service.currentView == .list)
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
        #expect(service.fileURL.lastPathComponent == "scout-profile.json")
        #expect(service.fileURL.deletingLastPathComponent().path == vault.resolvingRealPath().path)
    }

    @Test func firstChangeCreatesTheFile() throws {
        let service = makeService()
        service.update { $0.density = .compact }
        #expect(try onDisk(service) == ["schema": 1, "actionItems": ["density": "compact"]] as NSDictionary)
    }

    @Test func aNoOpChangeWritesNothing() {
        let service = makeService()
        service.update { $0.sort = .fileOrder }
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func switchingTheCurrentViewWritesNothing() {
        let service = makeService()
        service.currentView = .board
        #expect(service.profile.actionItems.defaultView == .list)
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func theTabOpensInTheDefaultViewAndChoosingADefaultShowsIt() throws {
        try Data(#"{"schema":1,"actionItems":{"defaultView":"board"}}"#.utf8)
            .write(to: vault.appendingPathComponent("scout-profile.json"))
        let service = makeService()
        #expect(service.currentView == .board)
        service.update { $0.defaultView = .list }
        #expect(service.currentView == .list)
    }

    @Test func aHandEditIsPickedUpThroughTheWatcher() async throws {
        let service = makeService()
        service.startWatching()
        try Data(#"{"schema":1,"actionItems":{"density":"compact"}}"#.utf8).write(to: service.fileURL, options: .atomic)
        fs.emit(FileSystemEvent(url: service.fileURL, kind: .modified))
        for _ in 0..<40 where service.profile.actionItems.density != .compact {
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        #expect(service.profile.actionItems.density == .compact)
    }

    @Test func eventsForOtherFilesAreIgnored() async throws {
        let service = makeService()
        service.startWatching()
        try Data(#"{"schema":1,"actionItems":{"density":"compact"}}"#.utf8).write(to: service.fileURL, options: .atomic)
        fs.emit(FileSystemEvent(url: service.fileURL.deletingLastPathComponent().appendingPathComponent("inbox.md"), kind: .modified))
        try await Task.sleep(nanoseconds: 150_000_000)
        #expect(service.profile.actionItems.density == .comfortable)
    }

    @Test func aBrokenFileKeepsTheLastGoodProfileAndHoldsWrites() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"sort":"alphabetical"}}"#, to: service)
        try write("{ broken", to: service)
        #expect(service.profile.actionItems.sort == .alphabetical)
        #expect(service.status == .unreadable(reason: "not valid JSON"))

        service.update { $0.density = .compact }
        #expect(service.profile.actionItems.density == .compact)          // applies for this session
        #expect(try String(contentsOf: service.fileURL, encoding: .utf8) == "{ broken")   // never overwritten
    }

    @Test func aBrokenFileAtLaunchGivesDefaults() throws {
        try Data("{ broken".utf8).write(to: vault.appendingPathComponent("scout-profile.json"))
        let service = makeService()
        #expect(service.profile == DisplayProfile())
        #expect(!service.status.canWrite)
    }

    @Test func aNewerSchemaIsNeverOverwritten() throws {
        let newer = #"{"schema":2,"actionItems":{"defaultView":"board"}}"#
        try Data(newer.utf8).write(to: vault.appendingPathComponent("scout-profile.json"))
        let service = makeService()
        #expect(service.status == .unsupportedSchema(found: 2))
        service.update { $0.density = .compact }
        #expect(try String(contentsOf: service.fileURL, encoding: .utf8) == newer)
    }

    @Test func fixingTheFileResumesWrites() throws {
        let service = makeService()
        try write("{ broken", to: service)
        try write(#"{"schema":1}"#, to: service)
        #expect(service.status == .ok(warnings: []))
        service.update { $0.grouping = .none }
        #expect(try onDisk(service) == ["schema": 1, "actionItems": ["group": "none"]] as NSDictionary)
    }

    @Test func warningsReachTheStatus() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"foo":true}}"#, to: service)
        #expect(service.status == .ok(warnings: [#"unknown key "actionItems.foo""#]))
        #expect(service.status.canWrite)
    }

    @Test func deletingTheFileResetsToDefaults() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"density":"compact"}}"#, to: service)
        try FileManager.default.removeItem(at: service.fileURL)
        service.reload()
        #expect(service.profile == DisplayProfile())
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test ... -only-testing:ScoutTests/DisplayProfileServiceTests`
Expected: FAIL, compile error `cannot find 'DisplayProfileService' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Profile/DisplayProfileService.swift`:

```swift
import Combine
import Foundation
import OSLog

/// Whether the profile on disk can be used and rewritten.
enum DisplayProfileStatus: Equatable {
    /// Read fine (or missing). `warnings` lists what was ignored.
    case ok(warnings: [String])
    /// Not JSON, not an object, or a bad `schema`. The last good profile stays.
    case unreadable(reason: String)
    /// Written by a newer app. Read as unreadable so it is never downgraded.
    case unsupportedSchema(found: Int)

    var canWrite: Bool {
        if case .ok = self { return true }
        return false
    }
}

/// Owns `scout-profile.json` (spec sections 4 and 5): reads it once at init,
/// follows edits made outside the app, and writes in-app changes as patches.
/// It never commits; the next session that commits the vault picks the file up.
@MainActor
final class DisplayProfileService: ObservableObject {
    @Published private(set) var profile: DisplayProfile
    @Published private(set) var status: DisplayProfileStatus
    /// Set when the last write failed (permissions, full disk); cleared by the
    /// next successful write.
    @Published private(set) var writeError: String?
    /// The List/Board view for this session. The toolbar switch sets it and it
    /// is never written. It starts from the profile's default view and follows
    /// the default whenever that changes.
    @Published var currentView: ActionItemsViewMode

    let fileURL: URL

    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?
    private var loggedContent: Data??

    private static let log = Logger(subsystem: "com.scout.Scout", category: "DisplayProfile")

    init(scoutDirectory: URL, fileEvents: any FileSystemEventSource) {
        // FSEvents reports real paths; see `URL.resolvingRealPath()`.
        self.fileURL = scoutDirectory.resolvingRealPath().appendingPathComponent(DisplayProfileCodec.fileName)
        self.fileEvents = fileEvents
        // One small synchronous read, so the tab opens in the saved view
        // instead of flashing List first.
        let snapshot = Self.read(fileURL, lastGood: DisplayProfile())
        self.profile = snapshot.profile
        self.status = snapshot.status
        self.currentView = snapshot.profile.actionItems.defaultView
        logOnce(snapshot)
    }

    deinit {
        watchTask?.cancel()
    }

    func startWatching() {
        watchTask?.cancel()
        let url = fileURL
        // Subscribe before the task starts so no early event is lost.
        let events = fileEvents.events(for: url)
        watchTask = Task { [weak self] in
            for await event in events where event.url.lastPathComponent == url.lastPathComponent {
                self?.reload()
            }
        }
    }

    /// Re-read the file. The app's own writes come back here too and re-parse
    /// to the value already published, so nothing republishes.
    func reload() {
        let snapshot = Self.read(fileURL, lastGood: profile)
        if snapshot.profile != profile { apply(snapshot.profile) }
        if snapshot.status != status { status = snapshot.status }
        logOnce(snapshot)
    }

    /// Apply a change from Settings or the View menu. It always takes effect
    /// on screen; it reaches the file only when the file is one the app may
    /// rewrite.
    func update(_ change: (inout ActionItemsDisplay) -> Void) {
        var next = profile
        change(&next.actionItems)
        let entries = next.changedEntries(from: profile)
        guard !entries.isEmpty else { return }
        apply(next)
        guard status.canWrite else { return }
        write(entries)
    }

    // MARK: - Private

    private struct Snapshot {
        let data: Data?
        let profile: DisplayProfile
        let status: DisplayProfileStatus
    }

    /// Publish a new profile; a new default view is also shown right away.
    private func apply(_ next: DisplayProfile) {
        if next.actionItems.defaultView != profile.actionItems.defaultView {
            currentView = next.actionItems.defaultView
        }
        profile = next
    }

    private static func read(_ url: URL, lastGood: DisplayProfile) -> Snapshot {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return Snapshot(data: nil, profile: DisplayProfile(), status: .ok(warnings: []))
        }
        guard let data = try? Data(contentsOf: url) else {
            return Snapshot(data: nil, profile: lastGood, status: .unreadable(reason: "the file can't be opened"))
        }
        switch DisplayProfileCodec.decode(data) {
        case .success(let decoded):
            return Snapshot(data: data, profile: decoded.profile, status: .ok(warnings: decoded.warnings))
        case .failure(let failure):
            return Snapshot(data: data, profile: lastGood, status: status(for: failure))
        }
    }

    private static func status(for failure: DisplayProfileCodec.Failure) -> DisplayProfileStatus {
        switch failure {
        case .unreadable(let reason):    return .unreadable(reason: reason)
        case .unsupportedSchema(let n):  return .unsupportedSchema(found: n)
        }
    }

    private func write(_ entries: [DisplayProfile.Entry]) {
        do {
            let existing = FileManager.default.fileExists(atPath: fileURL.path) ? try Data(contentsOf: fileURL) : nil
            let data = try DisplayProfileCodec.patch(existing, with: entries)
            try data.write(to: fileURL, options: .atomic)
            writeError = nil
        } catch let failure as DisplayProfileCodec.Failure {
            // The file turned unreadable since the last load; hold writes.
            status = Self.status(for: failure)
        } catch {
            writeError = error.localizedDescription
            Self.log.error("couldn't write \(self.fileURL.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }

    /// Log each distinct file content once, not on every reload.
    private func logOnce(_ snapshot: Snapshot) {
        guard loggedContent != .some(snapshot.data) else { return }
        loggedContent = .some(snapshot.data)
        let name = DisplayProfileCodec.fileName
        switch snapshot.status {
        case .ok(let warnings):
            for warning in warnings {
                Self.log.warning("\(name, privacy: .public): \(warning, privacy: .public)")
            }
        case .unreadable(let reason):
            Self.log.error("\(name, privacy: .public) is ignored: \(reason, privacy: .public)")
        case .unsupportedSchema(let found):
            Self.log.error("\(name, privacy: .public) has schema \(found, privacy: .public), newer than this app; it is ignored and won't be rewritten")
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command. Expected: PASS, 13 test functions.

- [ ] **Step 5: Commit**

```bash
git add Scout/Profile/DisplayProfileService.swift ScoutTests/Profile/DisplayProfileServiceTests.swift
git commit -m "feat(profile): DisplayProfileService reads, follows and patches scout-profile.json"
```

---

### Task 4: Wire the service into `AppState`

**Files:**
- Modify: `Scout/Shell/AppState.swift` (property with the other `let` services at `:25-77`, construction next to the other file-watching services after `:115`, assignment block `:233-262`, `startWatching()` in the background-work block after `:307`)
- Modify: `Scout/Shell/MainWindowView.swift:59-65` (`.environmentObject(appState.displayProfileService)` on `ActionItemsView`)
- Modify: `ScoutTests/Shell/TabViewSmokeTests.swift:39,55` (same injection, otherwise the render traps on a missing environment object)
- Create: `ScoutTests/Shell/AppStateDisplayProfileTests.swift`

**Interfaces:**
- Produces: `AppState.displayProfileService: DisplayProfileService`.

- [ ] **Step 1: Write the failing test**

```swift
import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("AppState display profile")
struct AppStateDisplayProfileTests {
    @Test func theProfileLivesInTheVaultRoot() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("vault-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        try Data(#"{"schema":1,"actionItems":{"defaultView":"board"}}"#.utf8)
            .write(to: vault.appendingPathComponent("scout-profile.json"))

        let state = AppState(configuration: .testing(scoutDirectory: vault))
        #expect(state.displayProfileService.fileURL.deletingLastPathComponent().path == vault.resolvingRealPath().path)
        #expect(state.displayProfileService.currentView == .board)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL, `value of type 'AppState' has no member 'displayProfileService'`.

- [ ] **Step 3: Write the implementation**

```swift
// AppState properties
let displayProfileService: DisplayProfileService

// init, with the other services built from `scoutDir` and `events`
let displayProfile = DisplayProfileService(scoutDirectory: scoutDir, fileEvents: events)

// assignment block
self.displayProfileService = displayProfile

// inside the `startsBackgroundWork` block
displayProfileService.startWatching()
```

`MainWindowView` and both `TabViewSmokeTests` renders add `.environmentObject(appState.displayProfileService)` (tests: `vault.state.displayProfileService`).

- [ ] **Step 4: Run** `-only-testing:ScoutTests/AppStateDisplayProfileTests -only-testing:ScoutTests/TabViewSmokeTests`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Scout/Shell/AppState.swift Scout/Shell/MainWindowView.swift ScoutTests/Shell
git commit -m "feat(profile): own the display profile in AppState"
```

---

### Task 5: `ActionItemsArrangement`

**Files:**
- Create: `Scout/ActionItems/ActionItemsArrangement.swift`
- Modify: `Scout/ActionItems/Models/ActionBoardColumn.swift` (`columns(from:sort:parents:)`, `sort` defaults to `.fileOrder` and `parents` to `[:]`)
- Create: `ScoutTests/ActionItems/ActionItemsArrangementTests.swift`

**Interfaces:**
- Produces:
  - `nonisolated enum ActionItemsArrangement` with `struct Arranged { var sections: [ActionSection]; var kinds: [UUID: ActionSection.Kind] }`, `static func parents(in:) -> [UUID: UUID]`, `static func arrange(_:parents:sort:grouping:) -> Arranged`, `static func sorted(_:by:parents:) -> [ActionTask]`.
  - `ActionBoardColumn.columns(from:sort:parents:)`.

**Sub-tasks and filters.** `parents(in:)` runs on the parsed document, before consolidation and filters: a sub-task's parent is the nearest task above it in the same section (or archive group) with a smaller indent. Sorting attaches a sub-task to the block in front of it only when that block holds its parent. A sub-task whose parent was filtered out becomes its own block, so it never attaches to an unrelated task, and in One list it never crosses into another section's task.

- [ ] **Step 1: Write the failing tests**

```swift
import Foundation
import Testing
@testable import Scout

@Suite("ActionItemsArrangement")
struct ActionItemsArrangementTests {
    private static func task(_ subject: String, indent: Int = 0, key: String? = nil) -> ActionTask {
        ActionTask(
            id: ActionItemsParser.stableID("t|\(key ?? subject)"), lineNumber: 1, done: false,
            subject: subject, plainSubject: subject, body: "", comments: [], deepLinks: [],
            details: [], snoozedUntil: nil, carriedInFrom: nil, indentLevel: indent
        )
    }

    private static func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(
            id: ActionItemsParser.stableID("s|\(kind)"), emoji: "", title: kind.rawValue, kind: kind,
            tasks: tasks, bullets: [], tables: [], subheads: [], collapsed: []
        )
    }

    private static func subjects(_ tasks: [ActionTask]) -> [String] { tasks.map(\.subject) }

    @Test func defaultsReturnTheInputUnchanged() {
        let sections = [Self.section(.urgent, [Self.task("b"), Self.task("a")]), Self.section(.focus, [])]
        let out = ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .section)
        #expect(out.sections == sections)
        #expect(out.kinds.isEmpty)
    }

    static let sorts: [(ActionItemsDisplay.Sort, [String])] = [
        (.fileOrder,    ["Review PROJ-1234", "Ask Priya", "Call Sam", "Book travel"]),
        (.alphabetical, ["Ask Priya", "Book travel", "Call Sam", "Review PROJ-1234"]),
    ]

    @Test(arguments: sorts)
    func sortOrders(_ sort: ActionItemsDisplay.Sort, _ expected: [String]) {
        let tasks = ["Review PROJ-1234", "Ask Priya", "Call Sam", "Book travel"].map { Self.task($0) }
        #expect(Self.subjects(ActionItemsArrangement.sorted(tasks, by: sort, parents: [:])) == expected)
    }

    @Test func subTasksMoveWithTheirParent() {
        let tasks = [
            Self.task("Zip the release"), Self.task("upload notes", indent: 1),
            Self.task("Ask Alex"), Self.task("draft question", indent: 1), Self.task("send it", indent: 2),
        ]
        let parents = ActionItemsArrangement.parents(in: [Self.section(.todo, tasks)])
        #expect(Self.subjects(ActionItemsArrangement.sorted(tasks, by: .alphabetical, parents: parents)) ==
                ["Ask Alex", "draft question", "send it", "Zip the release", "upload notes"])
    }

    @Test func tiesKeepFileOrder() {
        let tasks = [Self.task("Same", key: "1"), Self.task("Same", key: "2"), Self.task("Same", key: "3")]
        #expect(ActionItemsArrangement.sorted(tasks, by: .alphabetical, parents: [:]).map(\.id) == tasks.map(\.id))
    }

    /// A search removed "Ask Alex" and kept its sub-task. The sub-task must
    /// not ride along with "Zip the release", the task that now sits above it.
    @Test func aSubTaskWhoseParentWasFilteredOutStandsAlone() {
        let zip = Self.task("Zip the release"), upload = Self.task("upload notes", indent: 1)
        let ask = Self.task("Ask Alex"), draft = Self.task("draft question", indent: 1)
        let parents = ActionItemsArrangement.parents(in: [Self.section(.todo, [zip, upload, ask, draft])])
        let filtered = [zip, upload, draft]
        #expect(Self.subjects(ActionItemsArrangement.sorted(filtered, by: .alphabetical, parents: parents)) ==
                ["draft question", "Zip the release", "upload notes"])
    }

    @Test func inOneListAnOrphanedSubTaskDoesNotCrossSections() {
        let zebra = Self.task("Zebra crossing"), zNotes = Self.task("zebra notes", indent: 1)
        let apple = Self.task("Apple order"), aNotes = Self.task("apple notes", indent: 1)
        let source = [Self.section(.urgent, [zebra, zNotes]), Self.section(.todo, [apple, aNotes])]
        let parents = ActionItemsArrangement.parents(in: source)
        let filtered = [Self.section(.urgent, [zebra, zNotes]), Self.section(.todo, [aNotes])]
        let out = ActionItemsArrangement.arrange(filtered, parents: parents, sort: .alphabetical, grouping: .none)
        #expect(Self.subjects(out.sections[0].tasks) == ["apple notes", "Zebra crossing", "zebra notes"])
    }

    @Test func oneListMergesOpenSectionsAndKeepsEachKind() {
        let urgent = Self.task("Fix the demo")
        let watch = Self.task("Check PROJ-1234")
        let sections = [
            Self.section(.focus, []),
            Self.section(.urgent, [urgent]),
            Self.section(.watching, [watch]),
            Self.section(.done, [Self.task("Shipped")]),
        ]
        let out = ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .none)
        #expect(out.sections.map(\.kind) == [.focus, .neutral, .done])
        #expect(Self.subjects(out.sections[1].tasks) == ["Fix the demo", "Check PROJ-1234"])
        #expect(out.kinds == [urgent.id: .urgent, watch.id: .watching])
    }

    @Test func oneListWithNoOpenSectionsChangesNothing() {
        let sections = [Self.section(.focus, []), Self.section(.done, [Self.task("Shipped")])]
        #expect(ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .none).sections == sections)
    }

    @MainActor
    @Test func boardColumnsAreSortedInside() {
        let sections = [Self.section(.todo, [Self.task("b"), Self.task("a")])]
        let todo = ActionBoardColumn.columns(from: sections, sort: .alphabetical).first { $0.kind == .todo }
        #expect(Self.subjects(todo?.tasks ?? []) == ["a", "b"])
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL, `cannot find 'ActionItemsArrangement' in scope`.

- [ ] **Step 3: Write the implementation**

```swift
import Foundation

/// Sort and grouping for the Action Items list and board (spec section 6).
/// Runs after done-task consolidation and the filters, so List and Board keep
/// showing the same task set. `ActionItemsView` caches the result.
nonisolated enum ActionItemsArrangement {
    struct Arranged: Equatable, Sendable {
        var sections: [ActionSection]
        /// Source kind of every task moved into the merged section. Cards need
        /// it for the priority stripe and for snooze's `--from-kind`.
        var kinds: [UUID: ActionSection.Kind]
    }

    /// The sections "One list" merges.
    static let mergedKinds: Set<ActionSection.Kind> = [.urgent, .todo, .watching, .personal]
    static let mergedSectionID = ActionItemsParser.stableID("section|arranged-open")

    /// Each sub-task's parent in the source file: the nearest task above it, in
    /// the same section or archive group, with a smaller indent. Built from the
    /// parsed document before consolidation and filters, so a later filter
    /// cannot change who a sub-task belongs to.
    static func parents(in sections: [ActionSection]) -> [UUID: UUID] {
        var out: [UUID: UUID] = [:]
        for section in sections {
            for list in [section.tasks] + section.collapsed.map(\.tasks) {
                var stack: [ActionTask] = []
                for task in list {
                    while let last = stack.last, last.indentLevel >= task.indentLevel { stack.removeLast() }
                    if task.indentLevel > 0, let parent = stack.last { out[task.id] = parent.id }
                    stack.append(task)
                }
            }
        }
        return out
    }

    static func arrange(
        _ sections: [ActionSection],
        parents: [UUID: UUID],
        sort: ActionItemsDisplay.Sort,
        grouping: ActionItemsDisplay.Grouping
    ) -> Arranged {
        if sort == .fileOrder && grouping == .section { return Arranged(sections: sections, kinds: [:]) }
        let sorted = sections.map { replacing(tasks: Self.sorted($0.tasks, by: sort, parents: parents), in: $0) }
        guard grouping == .none else { return Arranged(sections: sorted, kinds: [:]) }
        return merged(sorted, sort: sort, parents: parents)
    }

    /// Stable sort that moves a top-level task together with its sub-tasks.
    /// A sub-task joins the block in front of it only when that block holds its
    /// parent; otherwise it is a block of its own. Ties keep file order.
    static func sorted(_ tasks: [ActionTask], by sort: ActionItemsDisplay.Sort, parents: [UUID: UUID]) -> [ActionTask] {
        guard sort != .fileOrder, tasks.count > 1 else { return tasks }
        var blocks: [[ActionTask]] = []
        var members: [Set<UUID>] = []
        for task in tasks {
            if task.indentLevel > 0, let parent = parents[task.id], let last = members.indices.last,
               members[last].contains(parent) {
                blocks[last].append(task)
                members[last].insert(task.id)
            } else {
                blocks.append([task])
                members.append([task.id])
            }
        }
        return blocks.enumerated()
            .sorted { a, b in
                switch compare(a.element[0], b.element[0], by: sort) {
                case .orderedAscending:  return true
                case .orderedDescending: return false
                case .orderedSame:       return a.offset < b.offset
                }
            }
            .flatMap(\.element)
    }

    private static func compare(_ a: ActionTask, _ b: ActionTask, by sort: ActionItemsDisplay.Sort) -> ComparisonResult {
        switch sort {
        case .fileOrder:    return .orderedSame
        case .alphabetical: return a.plainSubject.localizedStandardCompare(b.plainSubject)
        }
    }

    private static func merged(
        _ sections: [ActionSection], sort: ActionItemsDisplay.Sort, parents: [UUID: UUID]
    ) -> Arranged {
        var out: [ActionSection] = []
        var insertAt: Int?
        var kinds: [UUID: ActionSection.Kind] = [:]
        var tasks: [ActionTask] = []
        var bullets: [String] = []
        var tables: [ActionSection.Table] = []
        var subheads: [String] = []
        var collapsed: [ActionSection.CollapsedGroup] = []
        for section in sections {
            guard mergedKinds.contains(section.kind) else {
                out.append(section)
                continue
            }
            if insertAt == nil { insertAt = out.count }
            for task in section.tasks + section.collapsed.flatMap(\.tasks) { kinds[task.id] = section.kind }
            tasks += section.tasks
            bullets += section.bullets
            tables += section.tables
            subheads += section.subheads
            collapsed += section.collapsed
        }
        guard let insertAt else { return Arranged(sections: sections, kinds: [:]) }
        out.insert(ActionSection(
            id: mergedSectionID, emoji: "", title: "Open", kind: .neutral,
            tasks: Self.sorted(tasks, by: sort, parents: parents), bullets: bullets, tables: tables,
            subheads: subheads, collapsed: collapsed
        ), at: insertAt)
        return Arranged(sections: out, kinds: kinds)
    }

    private static func replacing(tasks: [ActionTask], in s: ActionSection) -> ActionSection {
        ActionSection(
            id: s.id, emoji: s.emoji, title: s.title, kind: s.kind, tasks: tasks,
            bullets: s.bullets, tables: s.tables, subheads: s.subheads, collapsed: s.collapsed
        )
    }
}
```

`ActionBoardColumn.columns(from:sort:parents:)` gains `sort: ActionItemsDisplay.Sort = .fileOrder, parents: [UUID: UUID] = [:]` and builds each column with `tasks: ActionItemsArrangement.sorted(tasks, by: sort, parents: parents)`.

- [ ] **Step 4: Run** `-only-testing:ScoutTests/ActionItemsArrangementTests -only-testing:ScoutTests/ActionBoardColumnTests`. Expected: PASS, 9 test functions in the new suite.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/ActionItemsArrangement.swift Scout/ActionItems/Models/ActionBoardColumn.swift ScoutTests/ActionItems/ActionItemsArrangementTests.swift
git commit -m "feat(action-items): alphabetical sort and one-list grouping as pure arrangement"
```

---

### Task 6: Cards and sections take density and fields

Plain parameters with today's values as defaults, so `ComponentSmokeTests`, `LeafSmokeTests` and `PerfHarnessTests` compile unchanged.

**Files:**
- Modify: `Scout/ActionItems/Views/TaskCardView.swift`
- Modify: `Scout/ActionItems/Views/BoardCardView.swift`
- Modify: `Scout/ActionItems/Views/BoardView.swift`
- Modify: `Scout/ActionItems/Views/SectionView.swift`
- Modify: `ScoutTests/Shell/ComponentSmokeTests.swift`, `ScoutTests/Shell/LeafSmokeTests.swift` (new compact and fields-off renders)

**Interfaces:**
- `TaskCardView(..., density: ActionItemsDisplay.Density = .comfortable, fields: ActionItemsDisplay.Fields = .init())` and `static func startsExpanded(kind:density:) -> Bool`, `static func visibleChips(_:fields:) -> [TaskChip]`.
- `BoardCardView(task:kind:density:fields:)`, `BoardView(columns:density:fields:)` plus the existing `BoardView(sections:)` as a convenience that builds unsorted columns, `SectionView(..., density:fields:kinds:)` with `kinds: [UUID: ActionSection.Kind] = [:]` (each card gets `kinds[task.id] ?? section.kind`, in the live list and in archive groups).

- [ ] **Step 1: Write the failing tests** (`ScoutTests/ActionItems/CardDisplayRulesTests.swift`)

```swift
import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("Card display rules")
struct CardDisplayRulesTests {
    static let expandRules: [(ActionSection.Kind, ActionItemsDisplay.Density, Bool)] = [
        (.urgent, .comfortable, true),
        (.urgent, .compact, false),
        (.todo, .comfortable, false),
        (.todo, .compact, false),
    ]

    @Test(arguments: expandRules)
    func urgentStartsExpandedOnlyWhenComfortable(_ kind: ActionSection.Kind, _ density: ActionItemsDisplay.Density, _ expanded: Bool) {
        #expect(TaskCardView.startsExpanded(kind: kind, density: density) == expanded)
    }

    @Test func refsOffKeepsOnlyTheCarriedChip() {
        let chips = [
            TaskChip(glyph: .linear, label: "PROJ-1234"),
            TaskChip(glyph: .carry, label: "carried Jun 2"),
        ]
        var fields = ActionItemsDisplay.Fields()
        #expect(TaskCardView.visibleChips(chips, fields: fields) == chips)
        fields.refs = false
        #expect(TaskCardView.visibleChips(chips, fields: fields) == [TaskChip(glyph: .carry, label: "carried Jun 2")])
    }
}
```

- [ ] **Step 2: Run** `-only-testing:ScoutTests/CardDisplayRulesTests`. Expected: FAIL, `type 'TaskCardView' has no member 'startsExpanded'`.

- [ ] **Step 3: Implementation notes**

`TaskCardView`:
- `init` stores `density` and `fields`; `_expanded = State(initialValue: Self.startsExpanded(kind: task.snoozedFromKind ?? kind, density: density))`, where `startsExpanded` is `kind == .urgent && density == .comfortable`.
- header padding `density == .compact ? 8 : 14`; the collapsed body preview renders only when `density == .comfortable`.
- `chips` becomes `Self.visibleChips(TaskChip.chips(...), fields: fields)`, which drops every chip except `.carry` when `!fields.refs`.
- when `fields.comments` and the task has comments, the chip row ends with a comment count chip (`text.bubble` glyph, `"\(n)"`, same `chipBody` styling).
- `trailingStatus` shows the snooze pill only when `fields.snooze`.
- `.onChange(of: density)` re-applies `startsExpanded(kind:density:)` to a card already on screen, unless the caller passed `startsExpanded:` (stored as `startsExpandedOverride`).

`BoardCardView`: padding `density == .compact ? 8 : 12`, subject `lineLimit(density == .compact ? 2 : 3)`, moon icon only when `fields.snooze`, link footer only when `fields.refs`, comment count in the footer when `fields.comments`.

`BoardView`: renders the `columns` it is given (sorted and cached by `ActionItemsView`, Task 7) and passes `density`/`fields` to each card.

`SectionView`: passes `density`/`fields` to every `TaskCardView` and `kind: kinds[task.id] ?? section.kind`.

Smoke additions: `TaskCardView` and `BoardCardView` rendered once with `.compact` and once with all fields inverted.

- [ ] **Step 4: Run** `-only-testing:ScoutTests/CardDisplayRulesTests -only-testing:ScoutTests/ComponentSmokeTests -only-testing:ScoutTests/LeafSmokeTests`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(action-items): density and card fields on list and board cards`

---

### Task 7: Toolbar, Settings and the arrangement cache

**Files:**
- Modify: `Scout/ActionItems/ActionItemsView.swift`
- Create: `Scout/Profile/ActionItemsDisplayMenu.swift`
- Create: `Scout/Shell/ActionItemsDisplaySection.swift`
- Modify: `Scout/Shell/SettingsView.swift` (new section after Engine, and the header comment's section count)
- Modify: `ScoutTests/Shell/ComponentSmokeTests.swift` (renders of the menu and the section)
- Create: `ScoutTests/ActionItems/ActionItemsLayoutTests.swift`
- Create: `ScoutTests/ActionItems/ActionItemsLayoutWindowTests.swift`

**Interfaces:**
- `extension DisplayProfileService { func binding<V>(_ keyPath: WritableKeyPath<ActionItemsDisplay, V>) -> Binding<V> }` (in `ActionItemsDisplayMenu.swift`, imports SwiftUI).
- `ActionItemsDisplayMenu` (toolbar), `ActionItemsDisplaySection` (Settings), both `@EnvironmentObject var service: DisplayProfileService`.
- `struct ActionItemsLayout: Equatable { let list: ActionItemsArrangement.Arranged; let boardColumns: [ActionBoardColumn] }` in `Scout/ActionItems/ActionItemsLayout.swift`, with `static func make(document:filtered:display:) -> ActionItemsLayout` (where `filtered` is the consolidated and filtered sections `ActionItemsView` already computes) and `static func isPassThrough(_:) -> Bool` (true for the default profile). It holds Board columns rather than sections because a column can mix tasks from several sections, and the sort applies across the column.

- [ ] **Step 1: Write the failing tests**

```swift
import Foundation
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Action Items layout")
struct ActionItemsLayoutTests {
    @Test func aBindingWritesThroughUpdate() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("bind-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        let service = DisplayProfileService(scoutDirectory: vault, fileEvents: InjectableFS())
        service.binding(\.fields.comments).wrappedValue = true
        #expect(service.profile.actionItems.fields.comments)
        #expect(FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func theBoardIsSortedButNeverMerged() {
        let section = SmokeFixtures.section(kind: .todo, tasks: [
            SmokeFixtures.task(subject: "Book travel"), SmokeFixtures.task(subject: "Ask Priya"),
        ])
        var display = ActionItemsDisplay()
        display.sort = .alphabetical
        display.grouping = .none
        let layout = ActionItemsLayout.make(document: [section], filtered: [section], display: display)
        #expect(layout.list.sections.map(\.kind) == [.neutral])
        let todo = layout.boardColumns.first { $0.kind == .todo }
        #expect(todo?.tasks.map(\.subject) == ["Ask Priya", "Book travel"])
    }

    @Test func theDefaultProfileLeavesTheSectionsAlone() {
        let sections = [SmokeFixtures.section(kind: .urgent), SmokeFixtures.section(kind: .todo)]
        #expect(ActionItemsLayout.isPassThrough(ActionItemsDisplay()))
        var sorted = ActionItemsDisplay()
        sorted.sort = .alphabetical
        #expect(!ActionItemsLayout.isPassThrough(sorted))
        let layout = ActionItemsLayout.make(document: sections, filtered: sections, display: ActionItemsDisplay())
        #expect(layout.list.sections == sections)
    }
}
```

- [ ] **Step 2: Run** `-only-testing:ScoutTests/ActionItemsLayoutTests`. Expected: FAIL, `value of type 'DisplayProfileService' has no member 'binding'` and `cannot find 'ActionItemsLayout' in scope`.

- [ ] **Step 3: Implementation notes**

`ActionItemsLayout.make` computes `parents(in: document)` once, then the list arrangement (`sort` and `grouping`) and the Board columns (`ActionBoardColumn.columns(from:sort:parents:)`, never merged).

`ActionItemsView`:
- remove `@SceneStorage("actionItemsView")`; add `@EnvironmentObject var displayProfile: DisplayProfileService` and `private var display: ActionItemsDisplay { displayProfile.profile.actionItems }`.
- `EditorialSegmentedControl(selection: $displayProfile.currentView, ...)`; every `viewMode` read becomes `displayProfile.currentView`, including `.onChange(of:)`.
- `ActionItemsDisplayMenu()` sits right after the segmented control.
- **Cache:** `@State private var layout: ActionItemsLayout?`, recomputed by `relayout()` in `.onAppear` and in `.onChange` of `docService.state`, `filter` and `display`, next to the existing `reconcileSelection()` calls (`ActionItemsView.swift:111-112`). With the default profile (`isPassThrough`) `relayout()` keeps no cache and the body renders `filteredSections(doc).map(filtered)` exactly as today. Otherwise `loadedContent` renders `layout.list.sections` with `density`, `fields` and `kinds`, and the board renders `layout.boardColumns` through `BoardView(columns:scoutDirectory:density:fields:)`.
- **Task windows:** the List builds one page of rows per section (`TaskWindow`), keyed by the drawn section. `ActionItemsLayout` gains `listSections(_:filtered:)` (the arranged sections, or the filtered ones when `layout` is nil), `selectableRows(in:windows:)`, `shownRows(in:windows:)` and `revealTarget(in:subject:shortPrefix:)`. `visibleSelectableTasks`, `visibleTaskIDs` and `revealReopened` read `listSections`, so Select all and reopen follow the sort and One list. Each `.onChange` calls `relayout()` before `reconcileSelection()`, and `revealReopened` relays out first because the reparse lands before `onChange` runs. Tests: the first selectable page per sort and grouping, One list paging the merged list once, the Done drawer shown in full, a reopened task's drawn row.

`ActionItemsDisplayMenu`: a `Menu` labelled `Label("View", systemImage: "slider.horizontal.3")` in `DS.sans(11.5, weight: .medium)`, `.menuStyle(.borderlessButton)`, `.fixedSize()`, with inline pickers for Sort, Group (List only), Density, and a "Show on cards" section of three toggles: References, Snooze date, Comment count.

`ActionItemsDisplaySection`: a `SettingsCard` with `SettingsRow`s: Default view, Sort, Group, Density (each an inline menu `Picker` with `.labelsHidden().pickerStyle(.menu).fixedSize()`, as at `SettingsView.swift:78-84`), then the three fields with `SettingsToggle`. Below the card: "Saved in `<path>`" (`fileURL.path` abbreviated with `~`), then one `DS.Status.warn` line for `status` (warnings joined, or "Can't read the file: <reason>. Fix or delete it to save changes again.", or "Written by a newer Scout (schema N). Changes here won't be saved.") and for `writeError`.

`SettingsView`: `section(label: "Action Items") { ActionItemsDisplaySection().environmentObject(appState.displayProfileService) }` after Engine, the same way Budget injects its service at `:106-109`.

- [ ] **Step 4: Run** the layout tests and the full suite. Expected: PASS.

- [ ] **Step 5: Commit** `feat(action-items): view options in the toolbar and in Settings, kept in scout-profile.json`

---

### Task 8: Pairwise rendering

Every pair of values across view, density, sort, grouping, the three fields and light/dark appears together in at least one of 7 profiles (the full product is 256). The test also checks that property, so a later edit to the table cannot silently drop a pair.

**Files:**
- Create: `ScoutTests/Shell/DisplayProfileRenderTests.swift`

- [ ] **Step 1: Write the test**

```swift
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Display profile renders", .serialized)
struct DisplayProfileRenderTests {
    struct Case: CustomTestStringConvertible, Sendable {
        let view: ActionItemsViewMode
        let density: ActionItemsDisplay.Density
        let sort: ActionItemsDisplay.Sort
        let grouping: ActionItemsDisplay.Grouping
        let fields: ActionItemsDisplay.Fields
        let dark: Bool

        var display: ActionItemsDisplay {
            ActionItemsDisplay(defaultView: view, sort: sort, grouping: grouping, density: density, fields: fields)
        }
        var values: [String] {
            [view.rawValue, density.rawValue, sort.rawValue, grouping.rawValue,
             "\(fields.refs)", "\(fields.snooze)", "\(fields.comments)", "\(dark)"]
        }
        var testDescription: String { values.joined(separator: " ") }
    }

    private static func f(_ refs: Bool, _ snooze: Bool, _ comments: Bool) -> ActionItemsDisplay.Fields {
        .init(refs: refs, snooze: snooze, comments: comments)
    }

    /// Generated as a strength-2 covering array; `coversEveryPair` checks it.
    static let cases: [Case] = [
        Case(view: .list,  density: .compact,     sort: .fileOrder,    grouping: .section, fields: f(true, true, true),    dark: false),
        Case(view: .board, density: .compact,     sort: .alphabetical, grouping: .none,    fields: f(false, false, false), dark: true),
        Case(view: .list,  density: .comfortable, sort: .fileOrder,    grouping: .section, fields: f(true, false, false),  dark: true),
        Case(view: .board, density: .comfortable, sort: .alphabetical, grouping: .section, fields: f(false, true, true),   dark: false),
        Case(view: .list,  density: .comfortable, sort: .fileOrder,    grouping: .none,    fields: f(false, true, false),  dark: false),
        Case(view: .list,  density: .comfortable, sort: .alphabetical, grouping: .none,    fields: f(true, true, true),    dark: true),
        Case(view: .board, density: .compact,     sort: .fileOrder,    grouping: .section, fields: f(true, false, true),   dark: false),
    ]

    @Test func coversEveryPair() {
        let domains: [Set<String>] = [
            ["list", "board"], ["comfortable", "compact"], ["fileOrder", "alphabetical"], ["section", "none"],
            ["true", "false"], ["true", "false"], ["true", "false"], ["true", "false"],
        ]
        for i in domains.indices {
            for j in domains.indices where j > i {
                for a in domains[i] {
                    for b in domains[j] {
                        #expect(Self.cases.contains { $0.values[i] == a && $0.values[j] == b }, "missing pair \(i)=\(a), \(j)=\(b)")
                    }
                }
            }
        }
    }

    @Test(arguments: cases)
    func renders(_ c: Case) async throws {
        let vault = try SmokeVault()
        defer { vault.tearDown() }
        vault.state.displayProfileService.update { $0 = c.display }
        await vault.loadDocuments()
        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState)
                .environmentObject(vault.state.displayProfileService)
                .environment(\.colorScheme, c.dark ? .dark : .light))
    }
}
```

- [ ] **Step 2: Run** `-only-testing:ScoutTests/DisplayProfileRenderTests`. Expected: PASS, 2 test functions (8 cases).

- [ ] **Step 3: Commit** `test(action-items): pairwise renders of the display profile`

---

### Task 9: Full verification

- [ ] Full suite: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests -resultBundlePath <tmp>/TestResults.xcresult CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`. Record `Test run with N tests`.
- [ ] Coverage: `./scripts/check-coverage.sh <tmp>/TestResults.xcresult` stays above `scripts/coverage-floor.txt`.
- [ ] Warnings: clean build on `main` and on the branch, same `warning:` count.
- [ ] Manual, Debug build ("Scout Dev") against a real vault, after tagging the vault's git:
  1. No file: the tab looks as before; no file appears after launch.
  2. Switch List and Board in the toolbar a few times: no file appears.
  3. Set Default view to Board in Settings: the file appears with `schema: 1` and `defaultView: board`, the tab shows Board, and a relaunch opens Board. The app makes no commit.
  4. Edit the file by hand to `"density": "compact"` while the app runs: cards compact without relaunch.
  5. Write `{ broken` into the file: no crash, last good profile stays, Settings shows the reason, changes are not written; fixing the file resumes.
  6. Add `"sidebar": {}` and `"actionItems": {"foo": 1}`: two warnings; after an in-app change both keys are still in the file.
  7. A vault without the file behaves as in 1.

## Plan Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| 3. File and schema | 2 |
| 4. Reading and writing rules, no commits | 2 (codec), 3 (service) |
| 5. Service | 1 (real path), 3, 4 |
| 6. Toolbar (session view), Settings | 3 (`currentView`), 7 |
| 6. Sort, group, orphaned sub-tasks | 5 |
| 6. Cache | 7 |
| 6. Fields and density | 6 |
| 7. Testing | 1 to 8, 9 (manual) |
| 8. Migration | 3 (default view at launch), 7 (`@SceneStorage` removed) |

**Type consistency.** `ActionItemsDisplay.grouping` is the Swift name for the JSON key `group` (a `Group` type would collide with SwiftUI's in view code). `ActionItemsViewMode` is reused for `defaultView` and `currentView` rather than a second List/Board enum.

**Dependencies.** The fix PR from the same issue (Raven-Scout/scout-app-legacy#122) has merged: it added `scoutDirectory` to `BoardView` and `BoardCardView` and a `startsExpanded:` parameter to `TaskCardView`, and an explicit `startsExpanded:` wins over the density rule in Task 6. The task windows (`TaskWindow`) are covered in Task 7. #297 (sub-bullet details on the card) and #312 (Settings ▸ Engine) have merged too and touch `TaskCardView`, `ActionItemsView`, `AppState` and `SettingsView`; the code branch rebases onto them before its PR.

**Deliberate divergences.** `JSONSerialization` instead of `Codable` (per-key fallback and preservation need the raw object). The service reads synchronously in `init`, unlike the async `loadInitial()` of larger services, because the file is under 1 KB and the first frame needs it. The session view lives in the service rather than in `@SceneStorage`, so it survives switching sidebar sections and the default view applies at every launch.

# Scout Schedules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **Schedules** sidebar section to Scout.app that supports full CRUD on every `com.scout.*.plist`, auto-reloads launchd on save, and auto-commits the repo copy in a path-scoped way.

**Architecture:** A new `Schedule` model round-trips every key in the plist (known + `unknownKeys`) via a shared `PlistIO` helper. A new `@MainActor` `ScheduleEditorService` owns the list, validates edits, writes to both the live and repo paths atomically, reloads via an injected `LaunchctlClient`, and commits via a new path-scoped `GitService.commitPaths`. A new `SchedulesView` + `ScheduleDetailView` render the list and form. The existing `LaunchdScheduleService` (which drives the Control Center upcoming strip) is refactored to use shared `PlistIO`, which also fixes a pre-existing weekday-convention bug.

**Tech Stack:** Swift 5.9+ / SwiftUI / macOS 13+, Apple's `Testing` framework (`@Suite` / `@Test`), existing `ProcessRunner` abstraction, existing `FileSystemEventSource` abstraction, `PropertyListSerialization` for plist round-tripping.

**Spec:** `docs/superpowers/specs/2026-04-22-scout-schedules-settings-design.md`

**Weekday convention used throughout this plan:** The model stores weekdays in **Calendar convention (1=Sun, 2=Mon, …, 7=Sat)**. `PlistIO` converts at the I/O boundary: launchd 0/7 → Calendar 1, launchd 1 → Calendar 2, …, launchd 6 → Calendar 7. This fixes a latent bug in `LaunchdScheduleService` where the raw launchd weekday was passed to `Calendar.nextDate` without conversion.

---

## File Structure

### Create
- `app/Scout/Scout/Models/Schedule.swift` — `Schedule`, `ScheduleTrigger`, `CalendarFire`, `PlistValue` types.
- `app/Scout/Scout/Services/PlistIO.swift` — `readSchedule(from:)`, `writeSchedule(_:to:)`, `plistValue(from:)`, `object(from:)`, `weekday` conversion helpers.
- `app/Scout/Scout/Services/Protocols/LaunchctlClient.swift` — protocol.
- `app/Scout/Scout/Services/SystemLaunchctlClient.swift` — production impl.
- `app/Scout/Scout/Services/ScheduleEditorService.swift` — `@MainActor` service (loadAll, save, create, delete, commit errors).
- `app/Scout/Scout/Services/ScheduleDiff.swift` — pure `summarize(original:edited:)` helper.
- `app/Scout/Scout/Services/ScheduleTriggerFormatter.swift` — pure `summary(for:)` helper.
- `app/Scout/Scout/Schedules/SchedulesView.swift` — list with `Table`, toolbar, error banner.
- `app/Scout/Scout/Schedules/ScheduleDetailView.swift` — form: label, runner, trigger, advanced disclosure, commit message disclosure, action buttons.
- `app/Scout/Scout/Schedules/ScheduleRowSummary.swift` — small view for trigger summary cell.
- `app/Scout/ScoutTests/Models/ScheduleTests.swift`
- `app/Scout/ScoutTests/Services/PlistIOTests.swift`
- `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`
- `app/Scout/ScoutTests/Services/ScheduleDiffTests.swift`
- `app/Scout/ScoutTests/Services/ScheduleTriggerFormatterTests.swift`
- `app/Scout/ScoutTests/Services/GitServiceCommitPathsTests.swift`
- `app/Scout/ScoutTests/Fixtures/plists/com.scout.briefing-weekend.plist` (copy from `~/Scout/launchd/`)
- `app/Scout/ScoutTests/Fixtures/plists/com.scout.dreaming-nightly-10pm.plist` (copy)
- `app/Scout/ScoutTests/Fixtures/plists/com.scout.heartbeat.plist` (copy)
- `app/Scout/ScoutTests/Fixtures/plists/com.scout.unknown-keys.plist` (hand-crafted; contains RunAtLoad + KeepAlive + ProcessType to test round-tripping).

### Modify
- `app/Scout/Scout/Services/GitService.swift` — add `commitPaths(_:message:)`.
- `app/Scout/Scout/Services/LaunchdScheduleService.swift` — swap `parsePlist` body for a thin call to `PlistIO.readSchedule` + convert to existing `CalendarEntry`. Fix weekday passing in `nextFireForEntry` (pass Calendar weekday directly, no conversion needed since `PlistIO` hands us Calendar convention).
- `app/Scout/Scout/Shell/SidebarView.swift` — add `.schedules` row.
- `app/Scout/Scout/Shell/MainWindowView.swift` — add `.schedules` to `SidebarItem`, render `SchedulesView` in the detail switch.
- `app/Scout/Scout/Shell/AppState.swift` — construct `ScheduleEditorService` with injected dependencies, expose as property.
- `app/Scout/ScoutTests/Services/LaunchdScheduleServiceTests.swift` — update test data for Calendar-convention weekdays (`1=Sun … 7=Sat`), keep expected Monday semantics.
- `app/Scout/Scout/Scout.xcodeproj/project.pbxproj` — add new files to the target. (Xcode maintains this; add files via Xcode's file inspector or by hand-editing the PBX block. Each task that creates a file must include this step.)

---

## Task 1: Schedule model types

**Files:**
- Create: `app/Scout/Scout/Models/Schedule.swift`
- Create: `app/Scout/ScoutTests/Models/ScheduleTests.swift`

- [ ] **Step 1: Write the failing test**

```swift
// app/Scout/ScoutTests/Models/ScheduleTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("Schedule model")
struct ScheduleTests {

    @Test func calendarFireEquatableIgnoresId() {
        let a = CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        let b = CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        // Note: id differs, but semantic equality should still hold for diff purposes.
        #expect(a.semanticallyEquals(b))
    }

    @Test func scheduleTriggerCalendarEqualityIgnoresFireIds() {
        let a: ScheduleTrigger = .calendar([
            CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        ])
        let b: ScheduleTrigger = .calendar([
            CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        ])
        #expect(a.semanticallyEquals(b))
    }

    @Test func scheduleTriggerIntervalEqualityHoldsAcrossInstances() {
        let a: ScheduleTrigger = .interval(seconds: 1800)
        let b: ScheduleTrigger = .interval(seconds: 1800)
        #expect(a.semanticallyEquals(b))
    }

    @Test func plistValueRoundTripsNested() {
        let v: PlistValue = .dict([
            "PATH": .string("/usr/bin"),
            "Nested": .array([.int(1), .bool(true)])
        ])
        let obj = v.toObject()
        let reparsed = PlistValue.from(object: obj)
        #expect(reparsed == v)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleTests 2>&1 | tail -20`
Expected: FAIL (compile error: `Schedule`, `CalendarFire`, `ScheduleTrigger`, `PlistValue` not defined).

- [ ] **Step 3: Write the model**

```swift
// app/Scout/Scout/Models/Schedule.swift
import Foundation

/// A single scheduled fire within a calendar-based plist trigger.
/// Weekday uses **Calendar convention**: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu,
/// 6=Fri, 7=Sat. `nil` means "every day" (no Weekday key in the plist).
/// `PlistIO` converts at the I/O boundary; launchd's raw 0/7 both map to 1.
struct CalendarFire: Identifiable, Equatable, Hashable, Sendable {
    let id: UUID
    var weekday: Int?
    var hour: Int
    var minute: Int

    init(id: UUID = UUID(), weekday: Int?, hour: Int, minute: Int) {
        self.id = id
        self.weekday = weekday
        self.hour = hour
        self.minute = minute
    }

    /// Equality ignoring `id` — used for diffing edits against originals.
    func semanticallyEquals(_ other: CalendarFire) -> Bool {
        weekday == other.weekday && hour == other.hour && minute == other.minute
    }
}

enum ScheduleTrigger: Equatable, Hashable, Sendable {
    case calendar([CalendarFire])
    case interval(seconds: Int)

    /// Equality that ignores `CalendarFire.id` so two triggers with the same
    /// fires (generated at different times) compare equal.
    func semanticallyEquals(_ other: ScheduleTrigger) -> Bool {
        switch (self, other) {
        case (.interval(let a), .interval(let b)): return a == b
        case (.calendar(let a), .calendar(let b)):
            guard a.count == b.count else { return false }
            return zip(a, b).allSatisfy { $0.semanticallyEquals($1) }
        default: return false
        }
    }
}

/// A tagged-union mirror of the plist value types we care about. Used to
/// round-trip `unknownKeys` without destroying them on save.
indirect enum PlistValue: Equatable, Hashable, Sendable {
    case string(String)
    case int(Int)
    case bool(Bool)
    case date(Date)
    case data(Data)
    case array([PlistValue])
    case dict([String: PlistValue])

    func toObject() -> Any {
        switch self {
        case .string(let s): return s
        case .int(let i): return i
        case .bool(let b): return b
        case .date(let d): return d
        case .data(let d): return d
        case .array(let a): return a.map { $0.toObject() }
        case .dict(let d): return d.mapValues { $0.toObject() }
        }
    }

    static func from(object: Any) -> PlistValue {
        // NSNumber comes back from PropertyListSerialization as either Bool
        // or numeric. objCType "c" distinguishes Bool from Int on this path.
        if let n = object as? NSNumber {
            if CFGetTypeID(n) == CFBooleanGetTypeID() {
                return .bool(n.boolValue)
            }
            return .int(n.intValue)
        }
        if let s = object as? String { return .string(s) }
        if let d = object as? Date { return .date(d) }
        if let d = object as? Data { return .data(d) }
        if let a = object as? [Any] { return .array(a.map { PlistValue.from(object: $0) }) }
        if let d = object as? [String: Any] {
            return .dict(d.mapValues { PlistValue.from(object: $0) })
        }
        // Fallback: stringify to preserve *something*; never reached for
        // well-formed plists.
        return .string(String(describing: object))
    }
}

struct Schedule: Identifiable, Equatable, Hashable, Sendable {
    /// Filename stem, e.g. "com.scout.briefing-weekend" — also the plist Label.
    let id: String
    var label: String
    var runnerScript: URL
    var workingDirectory: URL?
    var environment: [String: String]
    var logStdOut: URL?
    var logStdErr: URL?
    var trigger: ScheduleTrigger
    /// Every top-level plist key we don't surface, preserved verbatim for
    /// round-trip. Populated on parse, re-emitted on serialize.
    var unknownKeys: [String: PlistValue]

    init(
        id: String,
        label: String,
        runnerScript: URL,
        workingDirectory: URL? = nil,
        environment: [String: String] = [:],
        logStdOut: URL? = nil,
        logStdErr: URL? = nil,
        trigger: ScheduleTrigger,
        unknownKeys: [String: PlistValue] = [:]
    ) {
        self.id = id
        self.label = label
        self.runnerScript = runnerScript
        self.workingDirectory = workingDirectory
        self.environment = environment
        self.logStdOut = logStdOut
        self.logStdErr = logStdErr
        self.trigger = trigger
        self.unknownKeys = unknownKeys
    }
}
```

- [ ] **Step 4: Add file to Xcode target**

Open `app/Scout/Scout.xcodeproj` in Xcode (or script via `ruby` + `xcodeproj` gem if preferred), drag `Models/Schedule.swift` into the `Scout` target, and `Models/ScheduleTests.swift` into the `ScoutTests` target. Verify both files show a checkmark under "Target Membership" in the File Inspector.

- [ ] **Step 5: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleTests 2>&1 | tail -20`
Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/Scout/Scout/Models/Schedule.swift \
        app/Scout/ScoutTests/Models/ScheduleTests.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: Schedule model (task 1)"
```

---

## Task 2: PlistIO read path

**Files:**
- Create: `app/Scout/Scout/Services/PlistIO.swift`
- Create: `app/Scout/ScoutTests/Services/PlistIOTests.swift`
- Create: `app/Scout/ScoutTests/Fixtures/plists/com.scout.briefing-weekend.plist`
- Create: `app/Scout/ScoutTests/Fixtures/plists/com.scout.dreaming-nightly-10pm.plist`
- Create: `app/Scout/ScoutTests/Fixtures/plists/com.scout.heartbeat.plist`
- Create: `app/Scout/ScoutTests/Fixtures/plists/com.scout.unknown-keys.plist`

- [ ] **Step 1: Copy existing Scout plists into fixtures**

```bash
cp /Users/jordanburger/Scout/launchd/com.scout.briefing-weekend.plist \
   /Users/jordanburger/Scout/app/Scout/ScoutTests/Fixtures/plists/com.scout.briefing-weekend.plist
cp /Users/jordanburger/Scout/launchd/com.scout.dreaming-nightly-10pm.plist \
   /Users/jordanburger/Scout/app/Scout/ScoutTests/Fixtures/plists/com.scout.dreaming-nightly-10pm.plist
cp /Users/jordanburger/Scout/launchd/com.scout.heartbeat.plist \
   /Users/jordanburger/Scout/app/Scout/ScoutTests/Fixtures/plists/com.scout.heartbeat.plist
```

- [ ] **Step 2: Write the unknown-keys fixture**

Create `app/Scout/ScoutTests/Fixtures/plists/com.scout.unknown-keys.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.scout.unknown-keys</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/jordanburger/Scout/run-scout.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
```

- [ ] **Step 3: Write failing tests**

```swift
// app/Scout/ScoutTests/Services/PlistIOTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("PlistIO")
struct PlistIOTests {

    private func fixtureURL(_ name: String) -> URL {
        Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "plist")!
    }

    @Test func readsBriefingWeekendCalendarFires() throws {
        let s = try PlistIO.readSchedule(from: fixtureURL("com.scout.briefing-weekend"))
        #expect(s.id == "com.scout.briefing-weekend")
        #expect(s.label == "com.scout.briefing-weekend")
        #expect(s.runnerScript.lastPathComponent == "run-scout.sh")
        guard case .calendar(let fires) = s.trigger else {
            Issue.record("expected calendar trigger"); return
        }
        #expect(fires.count == 2)
        // launchd 6 (Sat) → Calendar 7; launchd 0 (Sun) → Calendar 1.
        #expect(fires.contains { $0.weekday == 7 && $0.hour == 8 && $0.minute == 0 })
        #expect(fires.contains { $0.weekday == 1 && $0.hour == 8 && $0.minute == 0 })
    }

    @Test func readsDreamingNightlyInterval() throws {
        let s = try PlistIO.readSchedule(from: fixtureURL("com.scout.dreaming-nightly-10pm"))
        // Dreaming-nightly uses StartCalendarInterval with a single dict
        // (not interval). Verify it comes through as calendar with one fire.
        guard case .calendar(let fires) = s.trigger else {
            Issue.record("expected calendar trigger"); return
        }
        #expect(fires.count == 1)
        #expect(fires[0].weekday == nil)  // no Weekday key = every day
        #expect(fires[0].hour == 22)
        #expect(fires[0].minute == 15)
    }

    @Test func readsHeartbeatIntervalTrigger() throws {
        let s = try PlistIO.readSchedule(from: fixtureURL("com.scout.heartbeat"))
        guard case .interval(let seconds) = s.trigger else {
            Issue.record("expected interval trigger"); return
        }
        #expect(seconds == 1800)
        #expect(s.workingDirectory?.path == "/Users/jordanburger/Scout")
        #expect(s.environment["HOME"] == "/Users/jordanburger")
    }

    @Test func preservesUnknownKeys() throws {
        let s = try PlistIO.readSchedule(from: fixtureURL("com.scout.unknown-keys"))
        #expect(s.unknownKeys["RunAtLoad"] == .bool(true))
        #expect(s.unknownKeys["ProcessType"] == .string("Background"))
        guard case .dict(let keepAlive) = s.unknownKeys["KeepAlive"] else {
            Issue.record("expected KeepAlive dict"); return
        }
        #expect(keepAlive["SuccessfulExit"] == .bool(false))
    }

    @Test func normalizesLaunchdSeven() throws {
        // Build a plist in-memory with Weekday=7 (also Sunday per launchd docs);
        // verify it normalizes to Calendar 1.
        let dict: [String: Any] = [
            "Label": "com.scout.test-seven",
            "ProgramArguments": ["/bin/bash", "/tmp/x.sh"],
            "StartCalendarInterval": [["Weekday": 7, "Hour": 9, "Minute": 0]]
        ]
        let data = try PropertyListSerialization.data(
            fromPropertyList: dict, format: .xml, options: 0
        )
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("com.scout.test-seven.plist")
        try data.write(to: tmp)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let s = try PlistIO.readSchedule(from: tmp)
        guard case .calendar(let fires) = s.trigger else {
            Issue.record("expected calendar trigger"); return
        }
        #expect(fires[0].weekday == 1)
    }
}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PlistIOTests 2>&1 | tail -20`
Expected: FAIL (compile error: `PlistIO` not defined).

- [ ] **Step 5: Implement read path**

```swift
// app/Scout/Scout/Services/PlistIO.swift
import Foundation

enum PlistIOError: Error, Equatable {
    case malformedRoot
    case missingLabel
    case missingProgramArguments
    case idMismatch(labelInFile: String, fileName: String)
}

enum PlistIO {

    // Keys we surface on the Schedule model; everything else goes to unknownKeys.
    private static let knownTopLevelKeys: Set<String> = [
        "Label",
        "ProgramArguments",
        "WorkingDirectory",
        "EnvironmentVariables",
        "StandardOutPath",
        "StandardErrorPath",
        "StartCalendarInterval",
        "StartInterval",
    ]

    /// Reads a Scout plist and returns a `Schedule`. The file's base name
    /// (without `.plist`) is used as `Schedule.id`. Weekday is converted from
    /// launchd convention (0/7=Sun ... 6=Sat) to Calendar convention
    /// (1=Sun ... 7=Sat) at this boundary.
    static func readSchedule(from url: URL) throws -> Schedule {
        let data = try Data(contentsOf: url)
        guard let root = try PropertyListSerialization.propertyList(
            from: data, options: [], format: nil
        ) as? [String: Any] else {
            throw PlistIOError.malformedRoot
        }

        guard let label = root["Label"] as? String else {
            throw PlistIOError.missingLabel
        }
        let fileStem = url.deletingPathExtension().lastPathComponent
        if label != fileStem {
            throw PlistIOError.idMismatch(labelInFile: label, fileName: fileStem)
        }

        guard let args = root["ProgramArguments"] as? [String], args.count >= 2 else {
            throw PlistIOError.missingProgramArguments
        }
        let runner = URL(fileURLWithPath: args[1])

        let workingDir = (root["WorkingDirectory"] as? String)
            .map { URL(fileURLWithPath: $0) }
        let environment = (root["EnvironmentVariables"] as? [String: String]) ?? [:]
        let logOut = (root["StandardOutPath"] as? String)
            .map { URL(fileURLWithPath: $0) }
        let logErr = (root["StandardErrorPath"] as? String)
            .map { URL(fileURLWithPath: $0) }

        let trigger = try parseTrigger(root: root)

        var unknown: [String: PlistValue] = [:]
        for (k, v) in root where !knownTopLevelKeys.contains(k) {
            unknown[k] = PlistValue.from(object: v)
        }

        return Schedule(
            id: fileStem,
            label: label,
            runnerScript: runner,
            workingDirectory: workingDir,
            environment: environment,
            logStdOut: logOut,
            logStdErr: logErr,
            trigger: trigger,
            unknownKeys: unknown
        )
    }

    private static func parseTrigger(root: [String: Any]) throws -> ScheduleTrigger {
        if let n = root["StartInterval"] as? Int {
            return .interval(seconds: n)
        }
        let dicts: [[String: Any]]
        if let arr = root["StartCalendarInterval"] as? [[String: Any]] {
            dicts = arr
        } else if let single = root["StartCalendarInterval"] as? [String: Any] {
            dicts = [single]
        } else {
            dicts = []
        }
        let fires: [CalendarFire] = dicts.map { d in
            let launchdWeekday = d["Weekday"] as? Int
            return CalendarFire(
                weekday: launchdWeekday.map(launchdToCalendarWeekday),
                hour: d["Hour"] as? Int ?? 0,
                minute: d["Minute"] as? Int ?? 0
            )
        }
        return .calendar(fires)
    }

    /// launchd weekday: 0 and 7 are Sunday, 1=Mon ... 6=Sat.
    /// Calendar weekday:     1=Sun, 2=Mon ... 7=Sat.
    /// Conversion: `((launchd + 0) % 7) + 1`. Handles 7 → 1 correctly.
    static func launchdToCalendarWeekday(_ launchd: Int) -> Int {
        ((launchd % 7) + 7) % 7 + 1  // extra %7 guards against unexpected negatives
    }

    /// Inverse of `launchdToCalendarWeekday`. Calendar 1 (Sun) → launchd 0.
    static func calendarToLaunchdWeekday(_ calendar: Int) -> Int {
        let zeroIndexed = calendar - 1   // 0=Sun ... 6=Sat
        return zeroIndexed
    }
}
```

- [ ] **Step 6: Add files to Xcode target**

Add `Services/PlistIO.swift` to `Scout` target; add `Services/PlistIOTests.swift` and the four fixture plists to `ScoutTests` target. Fixtures need "Copy files if needed" unchecked and should live under the `Fixtures/plists` group.

Verify in Xcode that under `ScoutTests > Build Phases > Copy Bundle Resources` the four new `.plist` files appear.

- [ ] **Step 7: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PlistIOTests 2>&1 | tail -30`
Expected: 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/Scout/Scout/Services/PlistIO.swift \
        app/Scout/ScoutTests/Services/PlistIOTests.swift \
        app/Scout/ScoutTests/Fixtures/plists/ \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: PlistIO.readSchedule + fixtures (task 2)"
```

---

## Task 3: PlistIO write path + round-trip

**Files:**
- Modify: `app/Scout/Scout/Services/PlistIO.swift`
- Modify: `app/Scout/ScoutTests/Services/PlistIOTests.swift`

- [ ] **Step 1: Write failing round-trip tests**

Append to `PlistIOTests`:

```swift
@Test func roundTripBriefingWeekendPreservesAllKeys() throws {
    let original = try PlistIO.readSchedule(from: fixtureURL("com.scout.briefing-weekend"))
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("com.scout.briefing-weekend.plist")
    try PlistIO.writeSchedule(original, to: tmp)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let reparsed = try PlistIO.readSchedule(from: tmp)
    #expect(reparsed.id == original.id)
    #expect(reparsed.label == original.label)
    #expect(reparsed.runnerScript == original.runnerScript)
    #expect(reparsed.workingDirectory == original.workingDirectory)
    #expect(reparsed.environment == original.environment)
    #expect(reparsed.logStdOut == original.logStdOut)
    #expect(reparsed.logStdErr == original.logStdErr)
    #expect(reparsed.unknownKeys == original.unknownKeys)
    #expect(triggerEquals(reparsed.trigger, original.trigger))
}

@Test func roundTripHeartbeatPreservesInterval() throws {
    let original = try PlistIO.readSchedule(from: fixtureURL("com.scout.heartbeat"))
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("com.scout.heartbeat.plist")
    try PlistIO.writeSchedule(original, to: tmp)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let reparsed = try PlistIO.readSchedule(from: tmp)
    #expect(triggerEquals(reparsed.trigger, original.trigger))
    #expect(reparsed.environment == original.environment)
}

@Test func roundTripUnknownKeysPreservesKeepAlive() throws {
    let original = try PlistIO.readSchedule(from: fixtureURL("com.scout.unknown-keys"))
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("com.scout.unknown-keys.plist")
    try PlistIO.writeSchedule(original, to: tmp)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let reparsed = try PlistIO.readSchedule(from: tmp)
    #expect(reparsed.unknownKeys == original.unknownKeys)
}

@Test func writeConvertsCalendarWeekdayBackToLaunchd() throws {
    // Calendar 7 (Sat) should be written as launchd 6.
    let s = Schedule(
        id: "com.scout.write-test",
        label: "com.scout.write-test",
        runnerScript: URL(fileURLWithPath: "/tmp/x.sh"),
        trigger: .calendar([CalendarFire(weekday: 7, hour: 9, minute: 0)])
    )
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("com.scout.write-test.plist")
    try PlistIO.writeSchedule(s, to: tmp)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let data = try Data(contentsOf: tmp)
    let root = try PropertyListSerialization.propertyList(
        from: data, options: [], format: nil
    ) as! [String: Any]
    let arr = root["StartCalendarInterval"] as! [[String: Any]]
    #expect(arr[0]["Weekday"] as? Int == 6)
}

// Local helper: ScheduleTrigger is Equatable but we want id-insensitive here.
private func triggerEquals(_ a: ScheduleTrigger, _ b: ScheduleTrigger) -> Bool {
    a.semanticallyEquals(b)
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PlistIOTests 2>&1 | tail -30`
Expected: 4 new tests fail with "writeSchedule not defined".

- [ ] **Step 3: Implement write path**

Append to `PlistIO`:

```swift
extension PlistIO {

    /// Serializes a `Schedule` to an XML plist at `url`. Known keys are
    /// written first (in a stable order), followed by `unknownKeys`.
    /// Writes to a temp file in the same directory and `rename()`s into
    /// place for atomicity.
    static func writeSchedule(_ schedule: Schedule, to url: URL) throws {
        var dict: [String: Any] = [:]
        dict["Label"] = schedule.label
        dict["ProgramArguments"] = ["/bin/bash", schedule.runnerScript.path]
        if let wd = schedule.workingDirectory {
            dict["WorkingDirectory"] = wd.path
        }
        if !schedule.environment.isEmpty {
            dict["EnvironmentVariables"] = schedule.environment
        }
        if let out = schedule.logStdOut {
            dict["StandardOutPath"] = out.path
        }
        if let err = schedule.logStdErr {
            dict["StandardErrorPath"] = err.path
        }
        switch schedule.trigger {
        case .interval(let seconds):
            dict["StartInterval"] = seconds
        case .calendar(let fires):
            dict["StartCalendarInterval"] = fires.map { fire -> [String: Any] in
                var d: [String: Any] = ["Hour": fire.hour, "Minute": fire.minute]
                if let w = fire.weekday {
                    d["Weekday"] = calendarToLaunchdWeekday(w)
                }
                return d
            }
        }
        for (k, v) in schedule.unknownKeys {
            dict[k] = v.toObject()
        }

        let data = try PropertyListSerialization.data(
            fromPropertyList: dict, format: .xml, options: 0
        )
        try atomicWrite(data: data, to: url)
    }

    private static func atomicWrite(data: Data, to url: URL) throws {
        let dir = url.deletingLastPathComponent()
        let tmp = dir.appendingPathComponent(
            ".\(url.lastPathComponent).tmp.\(UUID().uuidString)"
        )
        try data.write(to: tmp, options: .atomic)
        // Use FileManager.replaceItem for atomic rename-with-replace on macOS.
        _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PlistIOTests 2>&1 | tail -30`
Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/PlistIO.swift \
        app/Scout/ScoutTests/Services/PlistIOTests.swift
git commit -m "scout-app: PlistIO.writeSchedule with atomic replace (task 3)"
```

---

## Task 4: Refactor LaunchdScheduleService to use PlistIO

**Files:**
- Modify: `app/Scout/Scout/Services/LaunchdScheduleService.swift`
- Modify: `app/Scout/ScoutTests/Services/LaunchdScheduleServiceTests.swift`

- [ ] **Step 1: Update existing test for Calendar convention**

Replace the test body in `LaunchdScheduleServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("LaunchdScheduleService")
struct LaunchdScheduleServiceTests {

    @Test func parsesRealBriefingPlist() throws {
        let url = Bundle(for: FixtureAnchor.self).url(forResource: "com.scout.briefing", withExtension: "plist")!
        let entries = try LaunchdScheduleService.parsePlist(at: url)
        #expect(!entries.isEmpty)
        // briefing.plist uses launchd weekday 1-5 (Mon-Fri).
        // After Calendar conversion: Monday=2, Friday=6.
        #expect(entries.contains { $0.weekday == 2 && $0.hour == 8 && $0.minute == 3 })
        #expect(entries.first?.label == "com.scout.briefing")
    }

    @Test func nextFiresHonorWeekday() {
        // Calendar-convention weekdays: Monday=2, Wednesday=4.
        let entries = [
            LaunchdScheduleService.CalendarEntry(label: "com.scout.briefing", weekday: 2, hour: 8, minute: 3),
            LaunchdScheduleService.CalendarEntry(label: "com.scout.briefing", weekday: 4, hour: 11, minute: 3)
        ]
        // "Now" = Sunday 2026-04-19 13:00 ET
        var c = DateComponents()
        c.year = 2026; c.month = 4; c.day = 19; c.hour = 13; c.minute = 0
        c.timeZone = TimeZone(identifier: "America/New_York")
        let now = Calendar(identifier: .gregorian).date(from: c)!
        let fires = LaunchdScheduleService.nextFires(from: entries, after: now, limit: 3)
        #expect(fires.count == 3)

        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let firstComps = cal.dateComponents([.weekday, .hour, .minute], from: fires[0].scheduledAt)
        #expect(firstComps.weekday == 2) // Monday
        #expect(firstComps.hour == 8)
        #expect(firstComps.minute == 3)
    }
}
```

- [ ] **Step 2: Run tests — expect passing before refactor (existing behavior)**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/LaunchdScheduleServiceTests 2>&1 | tail -20`
Expected: first test passes because existing code doesn't convert (weekday=2 in plist is passed raw to Calendar as 2=Monday — happens to coincide); second test already asserts weekday=2 → Monday. Both pass.

Wait — the first test has a behavioral change. The existing test had `entries.contains { $0.weekday == 1 && ... }` (launchd 1=Mon) OR `weekday == 2`. After our change to use `PlistIO`, the entries will use Calendar convention: launchd 1 → Calendar 2. So only the weekday==2 predicate will match. That's what the new assertion checks. Good.

Expected: both tests pass (the new assertion for `weekday == 2` matches what the existing non-converted code also happens to produce for launchd input 1-5 → 2-6 in Calendar — need to re-run after refactor).

- [ ] **Step 3: Refactor `parsePlist` to use `PlistIO`**

In `LaunchdScheduleService.swift`, replace the body of `parsePlist(at:)`:

```swift
nonisolated static func parsePlist(at url: URL) throws -> [CalendarEntry] {
    let schedule = try PlistIO.readSchedule(from: url)
    switch schedule.trigger {
    case .calendar(let fires):
        return fires.map { fire in
            CalendarEntry(
                label: schedule.label,
                weekday: fire.weekday,
                hour: fire.hour,
                minute: fire.minute
            )
        }
    case .interval:
        // Interval-based triggers (heartbeat) are opaque to the upcoming strip.
        return []
    }
}
```

No other changes to `LaunchdScheduleService` are needed — `nextFireForEntry` already passes `comps.weekday = w`, and now `w` is in Calendar convention so the Calendar API interprets it correctly.

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/LaunchdScheduleServiceTests 2>&1 | tail -20`
Expected: both tests pass.

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/Scout/Scout/Services/LaunchdScheduleService.swift \
        app/Scout/ScoutTests/Services/LaunchdScheduleServiceTests.swift
git commit -m "scout-app: LaunchdScheduleService uses shared PlistIO (fixes weekday bug) (task 4)"
```

---

## Task 5: LaunchctlClient protocol + system impl

**Files:**
- Create: `app/Scout/Scout/Services/Protocols/LaunchctlClient.swift`
- Create: `app/Scout/Scout/Services/SystemLaunchctlClient.swift`

- [ ] **Step 1: Write the protocol**

```swift
// app/Scout/Scout/Services/Protocols/LaunchctlClient.swift
import Foundation

/// Abstraction over `/bin/launchctl`. Tests swap in a fake.
protocol LaunchctlClient: Sendable {
    /// Equivalent to `launchctl bootout gui/<uid> <path>`.
    /// Returns the raw exit code; callers decide what to do with non-zero
    /// (exit 3 "not loaded" is typically swallowed).
    func bootout(userUid: uid_t, plistPath: URL) async throws -> Int32

    /// Equivalent to `launchctl bootstrap gui/<uid> <path>`.
    /// Throws `LaunchctlError.bootstrapFailed` on non-zero exit.
    func bootstrap(userUid: uid_t, plistPath: URL) async throws
}

enum LaunchctlError: Error, Equatable {
    case bootstrapFailed(exitCode: Int32, stderr: String)
}
```

- [ ] **Step 2: Write the system impl**

```swift
// app/Scout/Scout/Services/SystemLaunchctlClient.swift
import Foundation

struct SystemLaunchctlClient: LaunchctlClient {
    private let runner: any ProcessRunner
    init(runner: any ProcessRunner) { self.runner = runner }

    func bootout(userUid: uid_t, plistPath: URL) async throws -> Int32 {
        let res = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/launchctl"),
            arguments: ["bootout", "gui/\(userUid)", plistPath.path],
            environment: [:],
            workingDirectory: nil
        )
        return res.exitCode
    }

    func bootstrap(userUid: uid_t, plistPath: URL) async throws {
        let res = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/launchctl"),
            arguments: ["bootstrap", "gui/\(userUid)", plistPath.path],
            environment: [:],
            workingDirectory: nil
        )
        if res.exitCode != 0 {
            let stderr = String(data: res.stderr, encoding: .utf8) ?? ""
            throw LaunchctlError.bootstrapFailed(exitCode: res.exitCode, stderr: stderr)
        }
    }
}
```

- [ ] **Step 3: Add files to Xcode target**

Add both files to the `Scout` target. No test file yet — covered by `ScheduleEditorServiceTests` via a fake.

- [ ] **Step 4: Build**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/Protocols/LaunchctlClient.swift \
        app/Scout/Scout/Services/SystemLaunchctlClient.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: LaunchctlClient protocol + system impl (task 5)"
```

---

## Task 6: GitService.commitPaths

**Files:**
- Modify: `app/Scout/Scout/Services/GitService.swift`
- Create: `app/Scout/ScoutTests/Services/GitServiceCommitPathsTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// app/Scout/ScoutTests/Services/GitServiceCommitPathsTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("GitService.commitPaths")
struct GitServiceCommitPathsTests {

    @Test func bailsSilentlyOutsideRepo() async throws {
        let runner = RecordingRunner(scripted: [
            ProcessResult(exitCode: 128, stdout: Data(), stderr: Data())  // rev-parse fails
        ])
        let git = GitService(
            repoURL: URL(fileURLWithPath: "/tmp/not-a-repo"),
            runner: runner
        )
        try await git.commitPaths(["file.txt"], message: "msg")
        #expect(runner.calls.count == 1)  // only rev-parse called
    }

    @Test func skipsWhenNoPathDiff() async throws {
        let runner = RecordingRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // rev-parse ok
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // add
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // diff --cached --quiet → 0 = clean
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/r"), runner: runner)
        try await git.commitPaths(["file.txt"], message: "msg")
        #expect(runner.calls.count == 3)  // no commit invoked
    }

    @Test func commitsScopedToPaths() async throws {
        let runner = RecordingRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // rev-parse
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // add
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),  // diff --cached --quiet: 1 = dirty
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // commit
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/r"), runner: runner)
        try await git.commitPaths(["launchd/a.plist", "launchd/b.plist"], message: "msg")

        #expect(runner.calls.count == 4)
        let commit = runner.calls[3]
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("-m"))
        #expect(commit.arguments.contains("msg"))
        #expect(commit.arguments.contains("--"))
        #expect(commit.arguments.contains("launchd/a.plist"))
        #expect(commit.arguments.contains("launchd/b.plist"))
    }

    @Test func throwsOnCommitFailure() async throws {
        let runner = RecordingRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data("hook failed".utf8)),
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/r"), runner: runner)
        await #expect(throws: GitServiceError.self) {
            try await git.commitPaths(["f"], message: "m")
        }
    }
}

final class RecordingRunner: ProcessRunner, @unchecked Sendable {
    struct Call { let executable: URL; let arguments: [String] }
    private var scripted: [ProcessResult]
    private(set) var calls: [Call] = []
    private let lock = NSLock()

    init(scripted: [ProcessResult]) { self.scripted = scripted }

    func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        lock.lock(); defer { lock.unlock() }
        calls.append(Call(executable: executable, arguments: arguments))
        if scripted.isEmpty {
            return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
        }
        return scripted.removeFirst()
    }
}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/GitServiceCommitPathsTests 2>&1 | tail -20`
Expected: FAIL (compile error: `commitPaths` not defined; possibly `GitServiceError` has no `commitFailed` case).

- [ ] **Step 3: Extend `GitServiceError`**

In `GitService.swift`, replace:

```swift
enum GitServiceError: Error {
    case gitExitNonZero(Int)
}
```

with:

```swift
enum GitServiceError: Error, Equatable {
    case gitExitNonZero(Int)
    case commitFailed(exitCode: Int32, stderr: String)
}
```

- [ ] **Step 4: Add `commitPaths` method**

In `GitService.swift`'s existing extension block (the one with `commitAll`), append:

```swift
/// Commit only the given repo-relative paths with the given message. Any
/// unrelated staged work in the repo is left untouched.
///
/// Pipeline:
///   1. `git rev-parse --is-inside-work-tree` — bail silently if not a repo.
///   2. `git add -- <paths>` — stage only the named paths.
///   3. `git diff --cached --quiet -- <paths>` — if exit 0, nothing to commit.
///   4. `git commit -m <message> -- <paths>` — scoped commit.
func commitPaths(_ relPaths: [String], message: String) async throws {
    let repo = repoURL.path

    let checkRepo = try await runner.run(
        executable: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
        environment: [:], workingDirectory: repoURL
    )
    guard checkRepo.exitCode == 0 else { return }

    _ = try await runner.run(
        executable: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["git", "-C", repo, "add", "--"] + relPaths,
        environment: [:], workingDirectory: repoURL
    )

    let diff = try await runner.run(
        executable: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["git", "-C", repo, "diff", "--cached", "--quiet", "--"] + relPaths,
        environment: [:], workingDirectory: repoURL
    )
    if diff.exitCode == 0 { return }  // no staged diff for these paths

    let commit = try await runner.run(
        executable: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["git", "-C", repo, "commit", "-m", message, "--"] + relPaths,
        environment: [:], workingDirectory: repoURL
    )
    if commit.exitCode != 0 {
        let stderr = String(data: commit.stderr, encoding: .utf8) ?? ""
        throw GitServiceError.commitFailed(exitCode: commit.exitCode, stderr: stderr)
    }
}
```

- [ ] **Step 5: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/GitServiceCommitPathsTests 2>&1 | tail -30`
Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/Scout/Scout/Services/GitService.swift \
        app/Scout/ScoutTests/Services/GitServiceCommitPathsTests.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: GitService.commitPaths for scoped commits (task 6)"
```

---

## Task 7: ScheduleDiff summarizer

**Files:**
- Create: `app/Scout/Scout/Services/ScheduleDiff.swift`
- Create: `app/Scout/ScoutTests/Services/ScheduleDiffTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// app/Scout/ScoutTests/Services/ScheduleDiffTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("ScheduleDiff.summarize")
struct ScheduleDiffTests {

    private func base() -> Schedule {
        Schedule(
            id: "com.scout.x", label: "com.scout.x",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            environment: ["A": "1"],
            trigger: .calendar([CalendarFire(weekday: 2, hour: 8, minute: 0)])
        )
    }

    @Test func identicalYieldsEmpty() {
        #expect(ScheduleDiff.summarize(original: base(), edited: base()) == "")
    }

    @Test func runnerOnly() {
        var e = base()
        e.runnerScript = URL(fileURLWithPath: "/other.sh")
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "runner")
    }

    @Test func triggerOnly() {
        var e = base()
        e.trigger = .calendar([CalendarFire(weekday: 2, hour: 9, minute: 0)])
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "trigger")
    }

    @Test func envOnly() {
        var e = base()
        e.environment = ["A": "2"]
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "env")
    }

    @Test func multipleFieldsCommaJoined() {
        var e = base()
        e.runnerScript = URL(fileURLWithPath: "/other.sh")
        e.environment = ["A": "2"]
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "runner, env")
    }
}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleDiffTests 2>&1 | tail -20`
Expected: FAIL (compile error: `ScheduleDiff` not defined).

- [ ] **Step 3: Implement the helper**

```swift
// app/Scout/Scout/Services/ScheduleDiff.swift
import Foundation

enum ScheduleDiff {
    /// Returns a comma-joined list of field labels that differ between
    /// `original` and `edited`. Empty string if nothing changed.
    /// Used to generate default commit messages like
    /// `"schedules: update com.scout.x (trigger, env)"`.
    static func summarize(original: Schedule, edited: Schedule) -> String {
        var parts: [String] = []
        if original.runnerScript != edited.runnerScript { parts.append("runner") }
        if !original.trigger.semanticallyEquals(edited.trigger) { parts.append("trigger") }
        if original.environment != edited.environment { parts.append("env") }
        if original.workingDirectory != edited.workingDirectory {
            parts.append("working-dir")
        }
        if original.logStdOut != edited.logStdOut
            || original.logStdErr != edited.logStdErr {
            parts.append("logs")
        }
        if original.unknownKeys != edited.unknownKeys {
            parts.append("advanced")
        }
        return parts.joined(separator: ", ")
    }
}
```

- [ ] **Step 4: Add files to Xcode target and run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleDiffTests 2>&1 | tail -20`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleDiff.swift \
        app/Scout/ScoutTests/Services/ScheduleDiffTests.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: ScheduleDiff.summarize for commit messages (task 7)"
```

---

## Task 8: ScheduleTriggerFormatter

**Files:**
- Create: `app/Scout/Scout/Services/ScheduleTriggerFormatter.swift`
- Create: `app/Scout/ScoutTests/Services/ScheduleTriggerFormatterTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// app/Scout/ScoutTests/Services/ScheduleTriggerFormatterTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("ScheduleTriggerFormatter.summary")
struct ScheduleTriggerFormatterTests {

    @Test func intervalInMinutes() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 1800))
                == "Every 30 min")
    }

    @Test func intervalBelowMinute() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 45))
                == "Every 45 sec")
    }

    @Test func intervalInHours() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 7200))
                == "Every 2 hr")
    }

    @Test func calendarSingleDaily() {
        let t: ScheduleTrigger = .calendar([CalendarFire(weekday: nil, hour: 22, minute: 15)])
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Daily 22:15")
    }

    @Test func calendarWeekdaysSingleTime() {
        // Mon-Fri at 8:03. Calendar weekdays 2-6.
        let t: ScheduleTrigger = .calendar(
            (2...6).map { CalendarFire(weekday: $0, hour: 8, minute: 3) }
        )
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Weekdays 8:03")
    }

    @Test func calendarWeekdaysMultipleTimes() {
        var fires: [CalendarFire] = []
        for d in 2...6 {  // Mon-Fri
            fires.append(CalendarFire(weekday: d, hour: 8, minute: 3))
            fires.append(CalendarFire(weekday: d, hour: 11, minute: 3))
        }
        #expect(ScheduleTriggerFormatter.summary(for: .calendar(fires))
                == "Weekdays 8:03, 11:03")
    }

    @Test func calendarWeekendSingleTime() {
        let t: ScheduleTrigger = .calendar([
            CalendarFire(weekday: 1, hour: 8, minute: 0),
            CalendarFire(weekday: 7, hour: 8, minute: 0)
        ])
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Sat–Sun 8:00")
    }

    @Test func calendarMixedFallback() {
        // Odd combination that doesn't match Weekdays / Sat–Sun / Daily.
        let t: ScheduleTrigger = .calendar([
            CalendarFire(weekday: 2, hour: 8, minute: 0),
            CalendarFire(weekday: 5, hour: 12, minute: 30)
        ])
        // Falls back to listing each fire.
        #expect(ScheduleTriggerFormatter.summary(for: t)
                == "Mon 8:00, Thu 12:30")
    }

    @Test func calendarEmptyIsIdle() {
        #expect(ScheduleTriggerFormatter.summary(for: .calendar([])) == "—")
    }
}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleTriggerFormatterTests 2>&1 | tail -20`
Expected: FAIL (compile error: not defined).

- [ ] **Step 3: Implement**

```swift
// app/Scout/Scout/Services/ScheduleTriggerFormatter.swift
import Foundation

enum ScheduleTriggerFormatter {
    static func summary(for trigger: ScheduleTrigger) -> String {
        switch trigger {
        case .interval(let seconds):
            return intervalSummary(seconds: seconds)
        case .calendar(let fires):
            return calendarSummary(fires: fires)
        }
    }

    private static func intervalSummary(seconds: Int) -> String {
        if seconds % 3600 == 0 { return "Every \(seconds / 3600) hr" }
        if seconds % 60 == 0 { return "Every \(seconds / 60) min" }
        return "Every \(seconds) sec"
    }

    private static func calendarSummary(fires: [CalendarFire]) -> String {
        guard !fires.isEmpty else { return "—" }

        // All fires share a common weekday set + list of (hour, minute)s?
        let uniqueWeekdays = Set(fires.compactMap { $0.weekday })
        let uniqueTimes = Set(fires.map { "\($0.hour):\(String(format: "%02d", $0.minute))" })

        // All fires have nil weekday → "Daily"
        if fires.allSatisfy({ $0.weekday == nil }) {
            let times = sortedTimeStrings(fires: fires)
            return "Daily " + times.joined(separator: ", ")
        }

        // Every weekday in the set is fired at every time in uniqueTimes?
        let expected = uniqueWeekdays.count * uniqueTimes.count
        if fires.count == expected && !uniqueWeekdays.contains(nil) {
            let weekdayPart = weekdayGroupName(uniqueWeekdays)
            let times = sortedTimeStrings(fires: fires).removingDuplicates()
            return "\(weekdayPart) \(times.joined(separator: ", "))"
        }

        // Fallback: list each fire.
        let parts = fires
            .sorted { ($0.weekday ?? 0, $0.hour, $0.minute) < ($1.weekday ?? 0, $1.hour, $1.minute) }
            .map { fire -> String in
                let day = fire.weekday.map(shortWeekdayName) ?? "Daily"
                return "\(day) \(fire.hour):\(String(format: "%02d", fire.minute))"
            }
        return parts.joined(separator: ", ")
    }

    private static func sortedTimeStrings(fires: [CalendarFire]) -> [String] {
        fires.sorted { ($0.hour, $0.minute) < ($1.hour, $1.minute) }
            .map { "\($0.hour):\(String(format: "%02d", $0.minute))" }
    }

    private static func weekdayGroupName(_ set: Set<Int>) -> String {
        let weekdays: Set<Int> = [2, 3, 4, 5, 6]  // Mon-Fri (Calendar convention)
        let weekend: Set<Int> = [1, 7]            // Sun + Sat
        if set == weekdays { return "Weekdays" }
        if set == weekend { return "Sat–Sun" }
        let sorted = set.sorted()
        return sorted.map(shortWeekdayName).joined(separator: "/")
    }

    private static func shortWeekdayName(_ calendarWeekday: Int) -> String {
        // 1=Sun, 2=Mon, ..., 7=Sat
        ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][max(0, min(6, calendarWeekday - 1))]
    }
}

private extension Array where Element: Hashable {
    func removingDuplicates() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleTriggerFormatterTests 2>&1 | tail -30`
Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleTriggerFormatter.swift \
        app/Scout/ScoutTests/Services/ScheduleTriggerFormatterTests.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: ScheduleTriggerFormatter.summary (task 8)"
```

---

## Task 9: ScheduleEditorService — loadAll

**Files:**
- Create: `app/Scout/Scout/Services/ScheduleEditorService.swift`
- Create: `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift
import Testing
import Foundation
@testable import Scout

@Suite("ScheduleEditorService.loadAll")
@MainActor
struct ScheduleEditorServiceLoadAllTests {

    @Test func loadsRepoPlistsIntoPublishedState() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }

        // Seed repo with two valid Scout plists.
        try copyFixture("com.scout.briefing-weekend", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.repo)

        let svc = ScheduleEditorService(
            repoDirectory: tmp.repo,
            agentsDirectory: tmp.live,
            userUid: 501,
            launchctl: FakeLaunchctl(),
            git: FakeGit(),
            fileEvents: NoopFileEvents()
        )
        try await svc.loadAll()

        #expect(svc.schedules.count == 2)
        #expect(svc.schedules.contains { $0.id == "com.scout.briefing-weekend" })
        #expect(svc.schedules.contains { $0.id == "com.scout.heartbeat" })
    }

    @Test func ignoresNonScoutPlists() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }

        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        // Add a non-Scout plist — should be ignored.
        try Data().write(to: tmp.repo.appendingPathComponent("com.example.plist"))

        let svc = makeService(repo: tmp.repo, live: tmp.live)
        try await svc.loadAll()
        #expect(svc.schedules.count == 1)
        #expect(svc.schedules.first?.id == "com.scout.heartbeat")
    }

    @Test func flagsDriftWhenRepoMissingLive() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }

        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        // No corresponding live file.

        let svc = makeService(repo: tmp.repo, live: tmp.live)
        try await svc.loadAll()
        #expect(svc.drift.count == 1)
        #expect(svc.drift.first?.id == "com.scout.heartbeat")
        #expect(svc.drift.first?.kind == .liveMissing)
    }
}

// MARK: - Helpers

struct TempDirs { let root: URL; let repo: URL; let live: URL }

func makeTempDirs() -> TempDirs {
    let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("scout-sched-\(UUID().uuidString)")
    let repo = root.appendingPathComponent("launchd")
    let live = root.appendingPathComponent("LaunchAgents")
    try? FileManager.default.createDirectory(at: repo, withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(at: live, withIntermediateDirectories: true)
    return TempDirs(root: root, repo: repo, live: live)
}

func copyFixture(_ name: String, to dir: URL) throws {
    let src = Bundle(for: FixtureAnchor.self)
        .url(forResource: name, withExtension: "plist")!
    let dst = dir.appendingPathComponent("\(name).plist")
    try? FileManager.default.removeItem(at: dst)
    try FileManager.default.copyItem(at: src, to: dst)
}

@MainActor
func makeService(repo: URL, live: URL) -> ScheduleEditorService {
    ScheduleEditorService(
        repoDirectory: repo,
        agentsDirectory: live,
        userUid: 501,
        launchctl: FakeLaunchctl(),
        git: FakeGit(),
        fileEvents: NoopFileEvents()
    )
}

final class FakeLaunchctl: LaunchctlClient, @unchecked Sendable {
    var bootoutExitCodes: [Int32] = [0]
    var bootstrapError: LaunchctlError? = nil
    private(set) var boototPaths: [URL] = []
    private(set) var bootstrapPaths: [URL] = []

    func bootout(userUid: uid_t, plistPath: URL) async throws -> Int32 {
        boototPaths.append(plistPath)
        return bootoutExitCodes.isEmpty ? 0 : bootoutExitCodes.removeFirst()
    }
    func bootstrap(userUid: uid_t, plistPath: URL) async throws {
        bootstrapPaths.append(plistPath)
        if let err = bootstrapError { throw err }
    }
}

final class FakeGit: GitServiceProtocol, @unchecked Sendable {
    struct Call { let paths: [String]; let message: String }
    var nextError: Error? = nil
    private(set) var calls: [Call] = []

    func commitPaths(_ relPaths: [String], message: String) async throws {
        calls.append(Call(paths: relPaths, message: message))
        if let err = nextError { throw err }
    }
}

struct NoopFileEvents: FileSystemEventSource {
    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { _ in }
    }
}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceLoadAllTests 2>&1 | tail -20`
Expected: FAIL (compile error: `ScheduleEditorService` and `GitServiceProtocol` not defined; `svc.drift` not defined).

- [ ] **Step 3: Add `GitServiceProtocol` and conform `GitService`**

In `GitService.swift`, at the top-level:

```swift
protocol GitServiceProtocol: Sendable {
    func commitPaths(_ relPaths: [String], message: String) async throws
}

extension GitService: GitServiceProtocol {}
```

- [ ] **Step 4: Implement `ScheduleEditorService` scaffold (loadAll only)**

```swift
// app/Scout/Scout/Services/ScheduleEditorService.swift
import Foundation
import Combine
import SwiftUI

enum ScheduleDriftKind: Equatable, Sendable {
    case liveMissing     // repo has it, ~/Library/LaunchAgents/ doesn't
    case repoMissing     // live has it, repo doesn't
}

struct ScheduleDrift: Identifiable, Equatable, Sendable {
    let id: String
    let kind: ScheduleDriftKind
}

struct CommitError: Identifiable, Equatable, Sendable {
    let id = UUID()
    let scheduleId: String
    let message: String
    let stderr: String
}

@MainActor
final class ScheduleEditorService: ObservableObject {
    @Published private(set) var schedules: [Schedule] = []
    @Published private(set) var drift: [ScheduleDrift] = []
    @Published private(set) var commitErrors: [CommitError] = []

    let repoDirectory: URL
    let agentsDirectory: URL
    let userUid: uid_t
    private let launchctl: any LaunchctlClient
    private let git: any GitServiceProtocol
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    init(
        repoDirectory: URL,
        agentsDirectory: URL,
        userUid: uid_t,
        launchctl: any LaunchctlClient,
        git: any GitServiceProtocol,
        fileEvents: any FileSystemEventSource
    ) {
        self.repoDirectory = repoDirectory
        self.agentsDirectory = agentsDirectory
        self.userUid = userUid
        self.launchctl = launchctl
        self.git = git
        self.fileEvents = fileEvents
    }

    func loadAll() async throws {
        let fm = FileManager.default
        let repoFiles = (try? fm.contentsOfDirectory(
            at: repoDirectory, includingPropertiesForKeys: nil
        )) ?? []
        var loaded: [Schedule] = []
        for url in repoFiles
            where url.lastPathComponent.hasPrefix("com.scout.")
               && url.pathExtension == "plist" {
            if let sched = try? PlistIO.readSchedule(from: url) {
                loaded.append(sched)
            }
        }
        loaded.sort { $0.id < $1.id }
        self.schedules = loaded

        // Drift detection: for each repo plist, verify a live file exists.
        let liveFiles = Set(((try? fm.contentsOfDirectory(
            at: agentsDirectory, includingPropertiesForKeys: nil
        )) ?? []).map { $0.lastPathComponent })
        var driftOut: [ScheduleDrift] = []
        for s in loaded where !liveFiles.contains("\(s.id).plist") {
            driftOut.append(ScheduleDrift(id: s.id, kind: .liveMissing))
        }
        // Repo-missing drift: live files that are Scout plists but not in repo.
        let repoIds = Set(loaded.map { "\($0.id).plist" })
        for liveName in liveFiles
            where liveName.hasPrefix("com.scout.") && !repoIds.contains(liveName) {
            let stem = (liveName as NSString).deletingPathExtension
            driftOut.append(ScheduleDrift(id: stem, kind: .repoMissing))
        }
        self.drift = driftOut
    }

    // save / create / delete — added in later tasks.
}
```

- [ ] **Step 5: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceLoadAllTests 2>&1 | tail -30`
Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleEditorService.swift \
        app/Scout/Scout/Services/GitService.swift \
        app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: ScheduleEditorService.loadAll + drift detection (task 9)"
```

---

## Task 10: ScheduleEditorService — validation

**Files:**
- Modify: `app/Scout/Scout/Services/ScheduleEditorService.swift`
- Modify: `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`

- [ ] **Step 1: Write failing tests**

Append to `ScheduleEditorServiceTests.swift`:

```swift
@Suite("ScheduleEditorService validation")
@MainActor
struct ScheduleEditorServiceValidationTests {

    @Test func rejectsInvalidLabel() {
        let s = Schedule(
            id: "BadLabel", label: "BadLabel",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .calendar([CalendarFire(weekday: nil, hour: 1, minute: 0)])
        )
        #expect(throws: ScheduleValidationError.self) {
            try ScheduleEditorService.validate(s, existingIds: [])
        }
    }

    @Test func rejectsNonScoutPrefix() {
        let s = Schedule(
            id: "com.example.x", label: "com.example.x",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .calendar([CalendarFire(weekday: nil, hour: 1, minute: 0)])
        )
        #expect(throws: ScheduleValidationError.self) {
            try ScheduleEditorService.validate(s, existingIds: [])
        }
    }

    @Test func rejectsDuplicateId() {
        let s = Schedule(
            id: "com.scout.dup", label: "com.scout.dup",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .interval(seconds: 60)
        )
        #expect(throws: ScheduleValidationError.self) {
            try ScheduleEditorService.validate(s, existingIds: ["com.scout.dup"])
        }
    }

    @Test func rejectsEmptyCalendar() {
        let s = Schedule(
            id: "com.scout.empty", label: "com.scout.empty",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .calendar([])
        )
        #expect(throws: ScheduleValidationError.self) {
            try ScheduleEditorService.validate(s, existingIds: [])
        }
    }

    @Test func rejectsZeroInterval() {
        let s = Schedule(
            id: "com.scout.zero", label: "com.scout.zero",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .interval(seconds: 0)
        )
        #expect(throws: ScheduleValidationError.self) {
            try ScheduleEditorService.validate(s, existingIds: [])
        }
    }

    @Test func acceptsValid() throws {
        let s = Schedule(
            id: "com.scout.ok", label: "com.scout.ok",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            trigger: .interval(seconds: 60)
        )
        try ScheduleEditorService.validate(s, existingIds: [])
    }
}
```

- [ ] **Step 2: Run tests — expect compile error**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceValidationTests 2>&1 | tail -20`
Expected: FAIL (`ScheduleValidationError` and `validate(_:existingIds:)` not defined).

- [ ] **Step 3: Add validation**

Append to `ScheduleEditorService.swift` (inside the class or as an extension):

```swift
enum ScheduleValidationError: Error, Equatable {
    case invalidLabel(String)
    case duplicateId(String)
    case emptyCalendar
    case nonPositiveInterval
    case labelMismatch(id: String, label: String)
}

extension ScheduleEditorService {
    static func validate(_ s: Schedule, existingIds: Set<String>) throws {
        // Label must match id (we always keep them in sync).
        if s.label != s.id {
            throw ScheduleValidationError.labelMismatch(id: s.id, label: s.label)
        }
        // Must match ^com\.scout\.[a-z0-9-]+$
        let pattern = #"^com\.scout\.[a-z0-9-]+$"#
        guard s.id.range(of: pattern, options: .regularExpression) != nil else {
            throw ScheduleValidationError.invalidLabel(s.id)
        }
        if existingIds.contains(s.id) {
            throw ScheduleValidationError.duplicateId(s.id)
        }
        switch s.trigger {
        case .calendar(let fires) where fires.isEmpty:
            throw ScheduleValidationError.emptyCalendar
        case .interval(let secs) where secs <= 0:
            throw ScheduleValidationError.nonPositiveInterval
        default:
            break
        }
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceValidationTests 2>&1 | tail -30`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleEditorService.swift \
        app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift
git commit -m "scout-app: ScheduleEditorService.validate (task 10)"
```

---

## Task 11: ScheduleEditorService — save (writes + reload)

**Files:**
- Modify: `app/Scout/Scout/Services/ScheduleEditorService.swift`
- Modify: `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`

- [ ] **Step 1: Write failing tests**

Append to the test file:

```swift
@Suite("ScheduleEditorService.save")
@MainActor
struct ScheduleEditorServiceSaveTests {

    @Test func writesBothPathsAndReloads() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.live)

        let fakeCtl = FakeLaunchctl()
        let fakeGit = FakeGit()
        let svc = ScheduleEditorService(
            repoDirectory: tmp.repo,
            agentsDirectory: tmp.live,
            userUid: 501,
            launchctl: fakeCtl,
            git: fakeGit,
            fileEvents: NoopFileEvents()
        )
        try await svc.loadAll()

        var edited = svc.schedules.first { $0.id == "com.scout.heartbeat" }!
        edited.trigger = .interval(seconds: 3600)

        try await svc.save(edited, commitMessageOverride: nil)

        // Both files written with the new interval.
        let repoRead = try PlistIO.readSchedule(
            from: tmp.repo.appendingPathComponent("com.scout.heartbeat.plist")
        )
        let liveRead = try PlistIO.readSchedule(
            from: tmp.live.appendingPathComponent("com.scout.heartbeat.plist")
        )
        #expect(repoRead.trigger.semanticallyEquals(.interval(seconds: 3600)))
        #expect(liveRead.trigger.semanticallyEquals(.interval(seconds: 3600)))

        // launchctl reloaded.
        #expect(fakeCtl.boototPaths.count == 1)
        #expect(fakeCtl.bootstrapPaths.count == 1)
        #expect(fakeCtl.bootstrapPaths.first?.path
                == tmp.live.appendingPathComponent("com.scout.heartbeat.plist").path)

        // git commit scoped to the repo path.
        #expect(fakeGit.calls.count == 1)
        #expect(fakeGit.calls.first?.paths
                == [tmp.repo.appendingPathComponent("com.scout.heartbeat.plist").path])
        #expect(fakeGit.calls.first?.message
                == "schedules: update com.scout.heartbeat (trigger)")

        // Service's in-memory list updated.
        #expect(svc.schedules.first(where: { $0.id == "com.scout.heartbeat" })?
                .trigger.semanticallyEquals(.interval(seconds: 3600)) == true)
    }

    @Test func swallowsBootoutNotLoaded() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)

        let fakeCtl = FakeLaunchctl()
        fakeCtl.bootoutExitCodes = [3]  // "not loaded"
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: fakeCtl, git: FakeGit())
        try await svc.loadAll()
        var s = svc.schedules.first!
        s.trigger = .interval(seconds: 120)

        // Should not throw.
        try await svc.save(s, commitMessageOverride: nil)
        #expect(fakeCtl.bootstrapPaths.count == 1)
    }

    @Test func bootstrapFailureRollsBackLive() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.live)

        let fakeCtl = FakeLaunchctl()
        fakeCtl.bootstrapError = .bootstrapFailed(exitCode: 5, stderr: "nope")
        let fakeGit = FakeGit()
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: fakeCtl, git: fakeGit)
        try await svc.loadAll()
        var s = svc.schedules.first!
        s.trigger = .interval(seconds: 120)

        await #expect(throws: LaunchctlError.self) {
            try await svc.save(s, commitMessageOverride: nil)
        }
        // Live file has been removed.
        #expect(!FileManager.default.fileExists(atPath:
            tmp.live.appendingPathComponent("com.scout.heartbeat.plist").path))
        // Repo file still present.
        #expect(FileManager.default.fileExists(atPath:
            tmp.repo.appendingPathComponent("com.scout.heartbeat.plist").path))
        // No git commit.
        #expect(fakeGit.calls.isEmpty)
    }

    @Test func gitFailureEnqueuesCommitErrorButPreservesEdit() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.live)

        let fakeGit = FakeGit()
        fakeGit.nextError = GitServiceError.commitFailed(exitCode: 1, stderr: "hook")
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: fakeGit)
        try await svc.loadAll()
        var s = svc.schedules.first!
        s.trigger = .interval(seconds: 120)

        try await svc.save(s, commitMessageOverride: nil)  // does NOT throw

        #expect(svc.commitErrors.count == 1)
        #expect(svc.commitErrors.first?.scheduleId == "com.scout.heartbeat")
        // Repo edit still applied.
        let reread = try PlistIO.readSchedule(
            from: tmp.repo.appendingPathComponent("com.scout.heartbeat.plist")
        )
        #expect(reread.trigger.semanticallyEquals(.interval(seconds: 120)))
    }

    @Test func respectsCommitMessageOverride() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.live)
        let fakeGit = FakeGit()
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: fakeGit)
        try await svc.loadAll()
        var s = svc.schedules.first!
        s.trigger = .interval(seconds: 120)

        try await svc.save(s, commitMessageOverride: "custom msg")
        #expect(fakeGit.calls.first?.message == "custom msg")
    }
}

@MainActor
func makeService(
    repo: URL, live: URL,
    launchctl: FakeLaunchctl, git: FakeGit
) -> ScheduleEditorService {
    ScheduleEditorService(
        repoDirectory: repo,
        agentsDirectory: live,
        userUid: 501,
        launchctl: launchctl,
        git: git,
        fileEvents: NoopFileEvents()
    )
}
```

- [ ] **Step 2: Run tests — expect compile error**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceSaveTests 2>&1 | tail -20`
Expected: FAIL (`save(_:commitMessageOverride:)` not defined).

- [ ] **Step 3: Implement `save`**

Append to `ScheduleEditorService.swift`:

```swift
extension ScheduleEditorService {

    /// Persist `edited` to both repo and live paths, reload via launchctl,
    /// and commit the repo change. A git failure does not throw — it enqueues
    /// a `CommitError` so the banner UI can surface it.
    func save(_ edited: Schedule, commitMessageOverride: String?) async throws {
        let original = schedules.first(where: { $0.id == edited.id })

        // Write plist to repo (source of truth first).
        let repoURL = repoDirectory.appendingPathComponent("\(edited.id).plist")
        let liveURL = agentsDirectory.appendingPathComponent("\(edited.id).plist")

        try PlistIO.writeSchedule(edited, to: repoURL)
        do {
            try PlistIO.writeSchedule(edited, to: liveURL)
        } catch {
            // Leave repo as-is; rethrow.
            throw error
        }

        // Reload launchd.
        let bootoutCode = try await launchctl.bootout(
            userUid: userUid, plistPath: liveURL
        )
        if bootoutCode != 0 && bootoutCode != 3 {
            // Unexpected error. Don't attempt bootstrap; don't commit.
            throw LaunchctlError.bootstrapFailed(
                exitCode: bootoutCode,
                stderr: "bootout exited \(bootoutCode)"
            )
        }
        do {
            try await launchctl.bootstrap(userUid: userUid, plistPath: liveURL)
        } catch {
            // Rollback: remove the live copy. Repo stays as the durable record.
            try? FileManager.default.removeItem(at: liveURL)
            throw error
        }

        // Update in-memory state.
        if let idx = schedules.firstIndex(where: { $0.id == edited.id }) {
            schedules[idx] = edited
        } else {
            schedules.append(edited)
            schedules.sort { $0.id < $1.id }
        }

        // Commit (best-effort; failure → banner, not throw).
        let message: String
        if let override = commitMessageOverride {
            message = override
        } else if let original {
            let suffix = ScheduleDiff.summarize(original: original, edited: edited)
            message = suffix.isEmpty
                ? "schedules: update \(edited.id)"
                : "schedules: update \(edited.id) (\(suffix))"
        } else {
            message = "schedules: add \(edited.id)"
        }
        do {
            try await git.commitPaths([repoURL.path], message: message)
        } catch {
            let stderr: String
            if case GitServiceError.commitFailed(_, let s) = error {
                stderr = s
            } else {
                stderr = String(describing: error)
            }
            commitErrors.append(CommitError(
                scheduleId: edited.id,
                message: message,
                stderr: stderr
            ))
        }
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceSaveTests 2>&1 | tail -30`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleEditorService.swift \
        app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift
git commit -m "scout-app: ScheduleEditorService.save with launchctl + git (task 11)"
```

---

## Task 12: ScheduleEditorService — create and delete

**Files:**
- Modify: `app/Scout/Scout/Services/ScheduleEditorService.swift`
- Modify: `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`

- [ ] **Step 1: Write failing tests**

Append:

```swift
@Suite("ScheduleEditorService.create")
@MainActor
struct ScheduleEditorServiceCreateTests {

    @Test func writesNewScheduleToBothPathsAndCommits() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        let fakeGit = FakeGit()
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: fakeGit)
        try await svc.loadAll()

        let s = Schedule(
            id: "com.scout.research", label: "com.scout.research",
            runnerScript: URL(fileURLWithPath: "/Users/jordanburger/Scout/run-research.sh"),
            trigger: .calendar([
                CalendarFire(weekday: 3, hour: 2, minute: 0),
                CalendarFire(weekday: 6, hour: 2, minute: 0)
            ])
        )
        try await svc.create(s, commitMessageOverride: nil)

        #expect(FileManager.default.fileExists(atPath:
            tmp.repo.appendingPathComponent("com.scout.research.plist").path))
        #expect(FileManager.default.fileExists(atPath:
            tmp.live.appendingPathComponent("com.scout.research.plist").path))
        #expect(fakeGit.calls.first?.message == "schedules: add com.scout.research")
        #expect(svc.schedules.contains { $0.id == "com.scout.research" })
    }

    @Test func rejectsDuplicateOnCreate() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: FakeGit())
        try await svc.loadAll()

        let dup = Schedule(
            id: "com.scout.heartbeat", label: "com.scout.heartbeat",
            runnerScript: URL(fileURLWithPath: "/x.sh"),
            trigger: .interval(seconds: 60)
        )
        await #expect(throws: ScheduleValidationError.self) {
            try await svc.create(dup, commitMessageOverride: nil)
        }
    }
}

@Suite("ScheduleEditorService.delete")
@MainActor
struct ScheduleEditorServiceDeleteTests {

    @Test func removesBothFilesAndCommits() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        try copyFixture("com.scout.heartbeat", to: tmp.live)
        let fakeGit = FakeGit()
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: fakeGit)
        try await svc.loadAll()
        let s = svc.schedules.first!

        try await svc.delete(s, commitMessageOverride: nil)

        #expect(!FileManager.default.fileExists(atPath:
            tmp.repo.appendingPathComponent("com.scout.heartbeat.plist").path))
        #expect(!FileManager.default.fileExists(atPath:
            tmp.live.appendingPathComponent("com.scout.heartbeat.plist").path))
        #expect(fakeGit.calls.first?.message == "schedules: remove com.scout.heartbeat")
        #expect(svc.schedules.isEmpty)
    }

    @Test func toleratesMissingLiveFile() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        // No live copy.
        let svc = makeService(repo: tmp.repo, live: tmp.live,
                              launchctl: FakeLaunchctl(), git: FakeGit())
        try await svc.loadAll()
        let s = svc.schedules.first!
        try await svc.delete(s, commitMessageOverride: nil)
        #expect(svc.schedules.isEmpty)
    }
}
```

- [ ] **Step 2: Run tests — expect compile error**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceCreateTests 2>&1 | tail -20`
Expected: FAIL.

- [ ] **Step 3: Implement `create` and `delete`**

Append to `ScheduleEditorService.swift`:

```swift
extension ScheduleEditorService {

    func create(_ schedule: Schedule, commitMessageOverride: String?) async throws {
        try Self.validate(schedule, existingIds: Set(schedules.map { $0.id }))
        try await save(schedule, commitMessageOverride: commitMessageOverride)
    }

    func delete(_ schedule: Schedule, commitMessageOverride: String?) async throws {
        let repoURL = repoDirectory.appendingPathComponent("\(schedule.id).plist")
        let liveURL = agentsDirectory.appendingPathComponent("\(schedule.id).plist")

        // bootout first; swallow "not loaded".
        let code = try await launchctl.bootout(userUid: userUid, plistPath: liveURL)
        if code != 0 && code != 3 {
            throw LaunchctlError.bootstrapFailed(exitCode: code, stderr: "bootout")
        }

        try? FileManager.default.removeItem(at: liveURL)
        try? FileManager.default.removeItem(at: repoURL)

        schedules.removeAll { $0.id == schedule.id }

        let message = commitMessageOverride ?? "schedules: remove \(schedule.id)"
        do {
            try await git.commitPaths([repoURL.path], message: message)
        } catch {
            let stderr: String
            if case GitServiceError.commitFailed(_, let s) = error {
                stderr = s
            } else {
                stderr = String(describing: error)
            }
            commitErrors.append(CommitError(
                scheduleId: schedule.id,
                message: message,
                stderr: stderr
            ))
        }
    }
}
```

Note: the duplicate check in `save` is implicit (the `original` lookup will succeed for any existing id). For `create`, we explicitly call `validate` with the current ids so a duplicate throws before the write happens.

- [ ] **Step 4: Run tests**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceCreateTests -only-testing:ScoutTests/ScheduleEditorServiceDeleteTests 2>&1 | tail -30`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleEditorService.swift \
        app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift
git commit -m "scout-app: ScheduleEditorService.create + delete (task 12)"
```

---

## Task 13: Sidebar + routing wiring

**Files:**
- Modify: `app/Scout/Scout/Shell/MainWindowView.swift`
- Modify: `app/Scout/Scout/Shell/SidebarView.swift`
- Modify: `app/Scout/Scout/Shell/AppState.swift`

- [ ] **Step 1: Add `.schedules` to `SidebarItem`**

In `MainWindowView.swift`, replace:

```swift
enum SidebarItem: Hashable {
    case controlCenter, actionItems, settings
}
```

with:

```swift
enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, settings
}
```

And in the detail switch, add the new case (before `.settings`):

```swift
case .schedules:
    SchedulesView()
        .environmentObject(appState.scheduleEditorService)
```

- [ ] **Step 2: Add row to `SidebarView`**

In `SidebarView.swift`'s first Section, add after Action Items:

```swift
sidebarRow(.schedules, label: "Schedules", system: "calendar.badge.clock")
```

- [ ] **Step 3: Wire `ScheduleEditorService` into `AppState`**

In `AppState.swift`, in `init()`, just after `let sched = LaunchdScheduleService(fileEvents: watcher)`, add:

```swift
let editor = ScheduleEditorService(
    repoDirectory: scoutDir.appendingPathComponent("launchd"),
    agentsDirectory: FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents"),
    userUid: getuid(),
    launchctl: SystemLaunchctlClient(runner: runner),
    git: git,
    fileEvents: watcher
)
```

Declare the property near the other service `let` declarations:

```swift
let scheduleEditorService: ScheduleEditorService
```

Assign it alongside the other services in the init tail:

```swift
self.scheduleEditorService = editor
```

And trigger initial load in the existing `Task { ... }` block:

```swift
_ = try? await editor.loadAll()
```

(Insert after the `sched.loadInitial()` line.)

- [ ] **Step 4: Create a stub `SchedulesView`**

Create `app/Scout/Scout/Schedules/SchedulesView.swift`:

```swift
import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditorService

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Schedules")
                .font(.title2)
            Text("\(service.schedules.count) schedule(s) loaded")
                .foregroundStyle(.secondary)
            // Full list + detail come in the next tasks.
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
```

Add it to the Scout target.

- [ ] **Step 5: Build + run manually**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch the app from Xcode (Cmd+R). Verify:
1. "Schedules" appears in the sidebar.
2. Clicking it shows "N schedule(s) loaded" where N matches `ls ~/Scout/launchd/com.scout.*.plist | wc -l`.

- [ ] **Step 6: Commit**

```bash
git add app/Scout/Scout/Shell/MainWindowView.swift \
        app/Scout/Scout/Shell/SidebarView.swift \
        app/Scout/Scout/Shell/AppState.swift \
        app/Scout/Scout/Schedules/SchedulesView.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: Schedules sidebar entry + stub view (task 13)"
```

---

## Task 14: SchedulesView — table list

**Files:**
- Modify: `app/Scout/Scout/Schedules/SchedulesView.swift`

- [ ] **Step 1: Implement the table**

Replace the stub body with:

```swift
import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditorService
    @State private var selection: Schedule.ID? = nil
    @State private var isShowingNewSheet = false

    var body: some View {
        NavigationSplitView {
            list
        } detail: {
            if let id = selection,
               let schedule = service.schedules.first(where: { $0.id == id }) {
                ScheduleDetailView(schedule: schedule)
                    .id(schedule.id)
            } else {
                ContentUnavailableView(
                    "No schedule selected",
                    systemImage: "calendar",
                    description: Text("Pick a schedule on the left to edit it.")
                )
            }
        }
        .sheet(isPresented: $isShowingNewSheet) {
            NewScheduleSheet()
                .environmentObject(service)
        }
    }

    private var list: some View {
        VStack(spacing: 0) {
            commitErrorBanner
            Table(service.schedules, selection: $selection) {
                TableColumn("Label") { Text($0.id).font(.body.monospaced()) }
                TableColumn("Runner") { Text($0.runnerScript.lastPathComponent) }
                TableColumn("Trigger") {
                    Text(ScheduleTriggerFormatter.summary(for: $0.trigger))
                }
                TableColumn("Status") { sched in
                    statusDot(for: sched)
                }
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        isShowingNewSheet = true
                    } label: {
                        Label("New Schedule", systemImage: "plus")
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var commitErrorBanner: some View {
        if !service.commitErrors.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(service.commitErrors) { err in
                    HStack(alignment: .firstTextBaseline) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Text("Saved \(err.scheduleId) — git commit failed: \(err.stderr)")
                            .font(.callout)
                    }
                }
            }
            .padding(8)
            .background(Color.orange.opacity(0.12))
        }
    }

    private func statusDot(for schedule: Schedule) -> some View {
        let drift = service.drift.first { $0.id == schedule.id }
        let color: Color = drift == nil ? .green : .orange
        return Circle()
            .fill(color)
            .frame(width: 8, height: 8)
    }
}
```

And create a placeholder `NewScheduleSheet` in the same file (replaced in Task 18):

```swift
struct NewScheduleSheet: View {
    @EnvironmentObject var service: ScheduleEditorService
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack {
            Text("New schedule form — implemented in Task 18")
            Button("Cancel") { dismiss() }
        }
        .padding()
        .frame(minWidth: 400, minHeight: 200)
    }
}
```

Also create a placeholder `ScheduleDetailView` in `app/Scout/Scout/Schedules/ScheduleDetailView.swift` (replaced in Task 15):

```swift
import SwiftUI

struct ScheduleDetailView: View {
    let schedule: Schedule
    var body: some View {
        VStack(alignment: .leading) {
            Text(schedule.id).font(.title2.monospaced())
            Text("Full detail view — implemented in Task 15")
                .foregroundStyle(.secondary)
        }
        .padding()
    }
}
```

- [ ] **Step 2: Build + smoke test**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch app. Verify:
1. The Schedules tab shows a table with rows for each `com.scout.*.plist`.
2. Trigger column shows strings like "Weekdays 8:03, 11:03, 13:07, 17:03" and "Every 30 min".
3. Selecting a row shows the placeholder detail view on the right.
4. Clicking "New Schedule" opens the placeholder sheet.

- [ ] **Step 3: Commit**

```bash
git add app/Scout/Scout/Schedules/SchedulesView.swift \
        app/Scout/Scout/Schedules/ScheduleDetailView.swift \
        app/Scout/Scout.xcodeproj/project.pbxproj
git commit -m "scout-app: SchedulesView table list + selection (task 14)"
```

---

## Task 15: ScheduleDetailView — label, runner, trigger

**Files:**
- Modify: `app/Scout/Scout/Schedules/ScheduleDetailView.swift`

- [ ] **Step 1: Implement the detail form skeleton**

Replace the placeholder with:

```swift
import SwiftUI

struct ScheduleDetailView: View {
    let schedule: Schedule
    @EnvironmentObject var service: ScheduleEditorService
    @State private var draft: Schedule
    @State private var isSaving = false
    @State private var saveError: String?
    @State private var isConfirmingDelete = false
    @State private var commitMessageOverride: String = ""
    @State private var commitMessageDisclosureExpanded = false

    init(schedule: Schedule) {
        self.schedule = schedule
        _draft = State(initialValue: schedule)
    }

    private let knownRunners: [URL] = [
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-scout.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-dreaming.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-research.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/scripts/heartbeat.sh"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                labelField
                runnerField
                triggerEditor
                // Advanced + commit-message disclosures come in Task 16–17.
                actionButtons
            }
            .padding()
        }
        .alert("Save failed",
               isPresented: .constant(saveError != nil),
               actions: {
                   Button("OK") { saveError = nil }
               },
               message: { Text(saveError ?? "") })
        .confirmationDialog("Delete \(schedule.id)?",
                            isPresented: $isConfirmingDelete,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) { Task { await performDelete() } }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This removes both the live plist and the repo copy, and commits the deletion.")
        }
    }

    private var labelField: some View {
        LabeledContent("Label", value: schedule.id)
            .font(.body.monospaced())
    }

    private var runnerField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Runner").font(.headline)
            Picker("", selection: Binding(
                get: { knownRunners.contains(draft.runnerScript) ? draft.runnerScript : URL(fileURLWithPath: "") },
                set: { newValue in
                    if newValue.path.isEmpty { return }
                    draft.runnerScript = newValue
                }
            )) {
                ForEach(knownRunners, id: \.self) { url in
                    Text(url.lastPathComponent).tag(url)
                }
                Text("Custom…").tag(URL(fileURLWithPath: ""))
            }
            .labelsHidden()
            TextField("Path", text: Binding(
                get: { draft.runnerScript.path },
                set: { draft.runnerScript = URL(fileURLWithPath: $0) }
            ))
            .textFieldStyle(.roundedBorder)
        }
    }

    @ViewBuilder
    private var triggerEditor: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Trigger").font(.headline)
            Picker("", selection: Binding(
                get: { draft.trigger.isCalendar ? "cal" : "int" },
                set: { newValue in
                    if newValue == "cal", !draft.trigger.isCalendar {
                        draft.trigger = .calendar([CalendarFire(weekday: nil, hour: 9, minute: 0)])
                    } else if newValue == "int", draft.trigger.isCalendar {
                        draft.trigger = .interval(seconds: 1800)
                    }
                }
            )) {
                Text("Calendar fires").tag("cal")
                Text("Interval").tag("int")
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            switch draft.trigger {
            case .calendar(let fires):
                calendarEditor(fires: fires)
            case .interval(let seconds):
                intervalEditor(seconds: seconds)
            }
        }
    }

    @ViewBuilder
    private func calendarEditor(fires: [CalendarFire]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(fires) { fire in
                HStack {
                    Picker("Weekday", selection: Binding<Int>(
                        get: { fire.weekday ?? 0 },
                        set: { newValue in
                            updateFire(id: fire.id) { f in
                                f.weekday = (newValue == 0) ? nil : newValue
                            }
                        }
                    )) {
                        Text("Every day").tag(0)
                        ForEach(1...7, id: \.self) { w in
                            Text(weekdayName(w)).tag(w)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 120)

                    Stepper(value: Binding(
                        get: { fire.hour },
                        set: { newValue in updateFire(id: fire.id) { $0.hour = newValue } }
                    ), in: 0...23) {
                        Text("\(fire.hour):\(String(format: "%02d", fire.minute))")
                            .font(.body.monospaced())
                    }
                    Stepper(":\(String(format: "%02d", fire.minute))", value: Binding(
                        get: { fire.minute },
                        set: { newValue in updateFire(id: fire.id) { $0.minute = newValue } }
                    ), in: 0...59)

                    Button(role: .destructive) {
                        removeFire(id: fire.id)
                    } label: {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.borderless)
                }
            }
            Button("Add fire") {
                appendFire()
            }
        }
    }

    private func intervalEditor(seconds: Int) -> some View {
        HStack {
            Stepper(value: Binding(
                get: { seconds },
                set: { draft.trigger = .interval(seconds: max(1, $0)) }
            ), in: 1...86_400, step: 60) {
                Text("\(seconds) seconds (\(seconds / 60) min)")
            }
        }
    }

    private var actionButtons: some View {
        HStack {
            Button("Revert") { draft = schedule }
                .disabled(draft == schedule || isSaving)
            Spacer()
            Button("Delete", role: .destructive) {
                isConfirmingDelete = true
            }
            Button("Save") {
                Task { await performSave() }
            }
            .keyboardShortcut(.defaultAction)
            .disabled(draft == schedule || isSaving)
        }
    }

    // MARK: - Actions

    private func performSave() async {
        isSaving = true
        defer { isSaving = false }
        do {
            try await service.save(
                draft,
                commitMessageOverride: commitMessageOverride.isEmpty ? nil : commitMessageOverride
            )
        } catch {
            saveError = String(describing: error)
        }
    }

    private func performDelete() async {
        do {
            try await service.delete(schedule, commitMessageOverride: nil)
        } catch {
            saveError = String(describing: error)
        }
    }

    // MARK: - Fire list mutations

    private func updateFire(id: UUID, _ mutate: (inout CalendarFire) -> Void) {
        guard case .calendar(var fires) = draft.trigger,
              let idx = fires.firstIndex(where: { $0.id == id }) else { return }
        mutate(&fires[idx])
        draft.trigger = .calendar(fires)
    }

    private func removeFire(id: UUID) {
        guard case .calendar(var fires) = draft.trigger else { return }
        fires.removeAll { $0.id == id }
        draft.trigger = .calendar(fires)
    }

    private func appendFire() {
        guard case .calendar(var fires) = draft.trigger else { return }
        fires.append(CalendarFire(weekday: nil, hour: 9, minute: 0))
        draft.trigger = .calendar(fires)
    }

    private func weekdayName(_ calendarWeekday: Int) -> String {
        ["", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][calendarWeekday]
    }
}

private extension ScheduleTrigger {
    var isCalendar: Bool { if case .calendar = self { return true }; return false }
}
```

- [ ] **Step 2: Build + manual test**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch app. Select a calendar-based schedule (e.g. `com.scout.briefing-weekend`):
- Fire rows appear with weekday menus + hour/minute steppers.
- Adjust a minute, Save — verify (via Terminal) that `~/Library/LaunchAgents/com.scout.briefing-weekend.plist` and `~/Scout/launchd/com.scout.briefing-weekend.plist` reflect the change.
- Run `launchctl list | grep com.scout.briefing-weekend` to confirm it's loaded.
- Run `git -C ~/Scout log -1 launchd/com.scout.briefing-weekend.plist` to confirm the scoped commit.

Select `com.scout.heartbeat`:
- Segmented control shows Interval mode.
- Stepper shows 1800 seconds (30 min).

**Important:** only do this against a non-critical plist first; consider testing against a throwaway `com.scout.test` plist you create manually to avoid breaking real schedules.

- [ ] **Step 3: Commit**

```bash
git add app/Scout/Scout/Schedules/ScheduleDetailView.swift
git commit -m "scout-app: ScheduleDetailView label/runner/trigger editors (task 15)"
```

---

## Task 16: ScheduleDetailView — Advanced disclosure

**Files:**
- Modify: `app/Scout/Scout/Schedules/ScheduleDetailView.swift`

- [ ] **Step 1: Insert the Advanced disclosure before `actionButtons`**

In the main `VStack` body, add between `triggerEditor` and `actionButtons`:

```swift
DisclosureGroup("Advanced") {
    VStack(alignment: .leading, spacing: 8) {
        workingDirectoryField
        environmentEditor
        logPathFields
    }
    .padding(.top, 8)
}
```

And add the three editors as computed vars:

```swift
private var workingDirectoryField: some View {
    VStack(alignment: .leading, spacing: 4) {
        Text("Working directory").font(.subheadline)
        TextField("Optional", text: Binding(
            get: { draft.workingDirectory?.path ?? "" },
            set: { newValue in
                draft.workingDirectory = newValue.isEmpty
                    ? nil : URL(fileURLWithPath: newValue)
            }
        ))
        .textFieldStyle(.roundedBorder)
    }
}

@ViewBuilder
private var environmentEditor: some View {
    VStack(alignment: .leading, spacing: 4) {
        Text("Environment variables").font(.subheadline)
        let keys = draft.environment.keys.sorted()
        ForEach(keys, id: \.self) { k in
            HStack {
                TextField("KEY", text: Binding(
                    get: { k },
                    set: { newKey in
                        let oldValue = draft.environment[k] ?? ""
                        draft.environment.removeValue(forKey: k)
                        draft.environment[newKey] = oldValue
                    }
                ))
                .frame(width: 160)
                TextField("value", text: Binding(
                    get: { draft.environment[k] ?? "" },
                    set: { draft.environment[k] = $0 }
                ))
                Button(role: .destructive) {
                    draft.environment.removeValue(forKey: k)
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
            }
        }
        Button("Add variable") {
            // Find a unique placeholder key.
            var candidate = "KEY"
            var n = 1
            while draft.environment[candidate] != nil {
                n += 1
                candidate = "KEY\(n)"
            }
            draft.environment[candidate] = ""
        }
    }
}

private var logPathFields: some View {
    VStack(alignment: .leading, spacing: 4) {
        Text("Log paths").font(.subheadline)
        TextField("StandardOutPath", text: Binding(
            get: { draft.logStdOut?.path ?? "" },
            set: { newValue in
                draft.logStdOut = newValue.isEmpty
                    ? nil : URL(fileURLWithPath: newValue)
            }
        ))
        .textFieldStyle(.roundedBorder)
        TextField("StandardErrorPath", text: Binding(
            get: { draft.logStdErr?.path ?? "" },
            set: { newValue in
                draft.logStdErr = newValue.isEmpty
                    ? nil : URL(fileURLWithPath: newValue)
            }
        ))
        .textFieldStyle(.roundedBorder)
    }
}
```

- [ ] **Step 2: Build + manual test**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch app, pick `com.scout.briefing`, expand Advanced:
- Environment variables show `PATH` and `HOME` rows.
- Working directory shows `/Users/jordanburger/Scout`.
- Log paths populated.
Edit an env value, Save, verify change on disk.

- [ ] **Step 3: Commit**

```bash
git add app/Scout/Scout/Schedules/ScheduleDetailView.swift
git commit -m "scout-app: ScheduleDetailView Advanced disclosure (task 16)"
```

---

## Task 17: ScheduleDetailView — Commit message disclosure

**Files:**
- Modify: `app/Scout/Scout/Schedules/ScheduleDetailView.swift`

- [ ] **Step 1: Add the commit-message disclosure below Advanced**

In the main `VStack`, insert between Advanced and `actionButtons`:

```swift
DisclosureGroup("Commit message", isExpanded: $commitMessageDisclosureExpanded) {
    VStack(alignment: .leading, spacing: 4) {
        Text(defaultCommitMessage)
            .font(.callout.monospaced())
            .foregroundStyle(.secondary)
        TextField("Override", text: $commitMessageOverride)
            .textFieldStyle(.roundedBorder)
    }
    .padding(.top, 8)
}
```

And compute the default message:

```swift
private var defaultCommitMessage: String {
    let suffix = ScheduleDiff.summarize(original: schedule, edited: draft)
    return suffix.isEmpty
        ? "schedules: update \(schedule.id)"
        : "schedules: update \(schedule.id) (\(suffix))"
}
```

- [ ] **Step 2: Build + manual test**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch app. Pick a schedule, change something, expand "Commit message" — the preview updates to e.g. `schedules: update com.scout.heartbeat (trigger)`. Type an override, Save, verify the git log shows your override (`git -C ~/Scout log -1 --pretty=%s launchd/com.scout.heartbeat.plist`).

- [ ] **Step 3: Commit**

```bash
git add app/Scout/Scout/Schedules/ScheduleDetailView.swift
git commit -m "scout-app: ScheduleDetailView commit message disclosure (task 17)"
```

---

## Task 18: New Schedule sheet

**Files:**
- Modify: `app/Scout/Scout/Schedules/SchedulesView.swift`

- [ ] **Step 1: Replace `NewScheduleSheet` stub with a real form**

```swift
struct NewScheduleSheet: View {
    @EnvironmentObject var service: ScheduleEditorService
    @Environment(\.dismiss) var dismiss
    @State private var idField: String = "com.scout."
    @State private var runner: URL = URL(fileURLWithPath: "/Users/jordanburger/Scout/run-scout.sh")
    @State private var isInterval: Bool = false
    @State private var intervalSeconds: Int = 1800
    @State private var fires: [CalendarFire] = [
        CalendarFire(weekday: nil, hour: 9, minute: 0)
    ]
    @State private var error: String?
    @State private var isSaving = false

    private let knownRunners: [URL] = [
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-scout.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-dreaming.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/run-research.sh"),
        URL(fileURLWithPath: "/Users/jordanburger/Scout/scripts/heartbeat.sh"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("New Schedule").font(.title3)

            LabeledContent("Label") {
                TextField("com.scout.something", text: $idField)
            }

            LabeledContent("Runner") {
                Picker("", selection: $runner) {
                    ForEach(knownRunners, id: \.self) { url in
                        Text(url.lastPathComponent).tag(url)
                    }
                }
                .labelsHidden()
            }

            Picker("Trigger", selection: $isInterval) {
                Text("Calendar fires").tag(false)
                Text("Interval").tag(true)
            }
            .pickerStyle(.segmented)

            if isInterval {
                Stepper(value: $intervalSeconds, in: 1...86_400, step: 60) {
                    Text("\(intervalSeconds) sec (\(intervalSeconds / 60) min)")
                }
            } else {
                ForEach(Array(fires.enumerated()), id: \.element.id) { idx, fire in
                    HStack {
                        Stepper(value: Binding(
                            get: { fire.hour },
                            set: { fires[idx].hour = $0 }
                        ), in: 0...23) {
                            Text("\(fire.hour):\(String(format: "%02d", fire.minute))")
                        }
                        Stepper(":\(String(format: "%02d", fire.minute))", value: Binding(
                            get: { fire.minute },
                            set: { fires[idx].minute = $0 }
                        ), in: 0...59)
                    }
                }
                Button("Add fire") {
                    fires.append(CalendarFire(weekday: nil, hour: 9, minute: 0))
                }
            }

            if let error {
                Text(error).foregroundStyle(.red).font(.callout)
            }

            HStack {
                Button("Cancel") { dismiss() }
                Spacer()
                Button("Create") {
                    Task { await create() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(isSaving)
            }
        }
        .padding()
        .frame(minWidth: 480, minHeight: 360)
    }

    private func create() async {
        isSaving = true
        defer { isSaving = false }
        let s = Schedule(
            id: idField,
            label: idField,
            runnerScript: runner,
            trigger: isInterval
                ? .interval(seconds: intervalSeconds)
                : .calendar(fires)
        )
        do {
            try await service.create(s, commitMessageOverride: nil)
            dismiss()
        } catch {
            self.error = String(describing: error)
        }
    }
}
```

- [ ] **Step 2: Build + manual test**

Run: `cd app/Scout && xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | tail -10`
Expected: Build succeeded.

Launch app, click "New Schedule", fill in `com.scout.research` with runner `run-research.sh` and a calendar fire "Wednesday 2:00". Click Create:
- Both files written.
- Appears in the list.
- `launchctl list | grep com.scout.research` returns a row.
- `git -C ~/Scout log -1 --pretty=%s launchd/com.scout.research.plist` → `schedules: add com.scout.research`.

(Optionally, delete it afterwards via the detail view's Delete button to tidy up.)

- [ ] **Step 3: Commit**

```bash
git add app/Scout/Scout/Schedules/SchedulesView.swift
git commit -m "scout-app: New Schedule sheet (task 18)"
```

---

## Task 19: Reload-on-save watch + tests for FileSystemEventSource integration

**Files:**
- Modify: `app/Scout/Scout/Services/ScheduleEditorService.swift`
- Modify: `app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift`

- [ ] **Step 1: Write failing test**

Append:

```swift
@Suite("ScheduleEditorService watch")
@MainActor
struct ScheduleEditorServiceWatchTests {

    final class ManualEvents: FileSystemEventSource, @unchecked Sendable {
        var continuation: AsyncStream<FileSystemEvent>.Continuation?
        func events(for url: URL) -> AsyncStream<FileSystemEvent> {
            AsyncStream { cont in self.continuation = cont }
        }
        func emit(url: URL, kind: FileSystemEvent.Kind) {
            continuation?.yield(FileSystemEvent(url: url, kind: kind))
        }
    }

    @Test func reloadsOnRepoDirEvent() async throws {
        let tmp = makeTempDirs()
        defer { try? FileManager.default.removeItem(at: tmp.root) }

        let events = ManualEvents()
        let svc = ScheduleEditorService(
            repoDirectory: tmp.repo,
            agentsDirectory: tmp.live,
            userUid: 501,
            launchctl: FakeLaunchctl(),
            git: FakeGit(),
            fileEvents: events
        )
        try await svc.loadAll()
        #expect(svc.schedules.isEmpty)

        // Drop a new plist and simulate a file system event.
        try copyFixture("com.scout.heartbeat", to: tmp.repo)
        svc.startWatching()
        events.emit(
            url: tmp.repo.appendingPathComponent("com.scout.heartbeat.plist"),
            kind: .created
        )

        // Give the service a tick to react.
        try await Task.sleep(for: .milliseconds(50))
        #expect(svc.schedules.contains { $0.id == "com.scout.heartbeat" })
    }
}
```

- [ ] **Step 2: Expose `startWatching()` and react to events**

In `ScheduleEditorService.swift`, inside the main class, add:

```swift
func startWatching() {
    watchTask?.cancel()
    watchTask = Task { [weak self] in
        guard let self else { return }
        for await event in self.fileEvents.events(for: self.repoDirectory) {
            guard event.url.lastPathComponent.hasPrefix("com.scout.") else { continue }
            try? await self.loadAll()
        }
    }
}

deinit { watchTask?.cancel() }
```

Call `startWatching()` from `AppState.init`'s `Task` block right after `_ = try? await editor.loadAll()`:

```swift
await MainActor.run { editor.startWatching() }
```

- [ ] **Step 3: Run the test**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScheduleEditorServiceWatchTests 2>&1 | tail -20`
Expected: 1 test passes.

- [ ] **Step 4: Commit**

```bash
git add app/Scout/Scout/Services/ScheduleEditorService.swift \
        app/Scout/Scout/Shell/AppState.swift \
        app/Scout/ScoutTests/Services/ScheduleEditorServiceTests.swift
git commit -m "scout-app: ScheduleEditorService watches repo for external edits (task 19)"
```

---

## Task 20: Full integration check + README note

**Files:**
- Modify: `app/Scout/Scout/BACKLOG.md` (mark relevant backlog items done if present)
- Modify: `README.md` or `app/Scout/Scout/BACKLOG.md` — add one-line note about the feature

- [ ] **Step 1: Run full test suite**

Run: `cd app/Scout && xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: All tests pass.

- [ ] **Step 2: Manual end-to-end walkthrough**

1. Launch Scout.app.
2. Click "Schedules". Verify list shows all 8 existing `com.scout.*` plists with correct runner + trigger summaries + green status dots.
3. Click `com.scout.dreaming-nightly-10pm`. Expand Advanced. Change stdout path (add ` .test` suffix), Save.
4. Verify:
   - `cat ~/Library/LaunchAgents/com.scout.dreaming-nightly-10pm.plist` shows the new path.
   - `cat ~/Scout/launchd/com.scout.dreaming-nightly-10pm.plist` shows the same.
   - `launchctl list | grep dreaming-nightly-10pm` still loaded.
   - `git -C ~/Scout log -1 --pretty=%s launchd/com.scout.dreaming-nightly-10pm.plist` reads `schedules: update com.scout.dreaming-nightly-10pm (logs)`.
5. Revert the change: change stdout path back, Save. Verify git shows another `(logs)` commit.
6. Click "New Schedule", create `com.scout.research` with runner `run-research.sh` and a weekly fire. Verify it appears, is loaded, and has `schedules: add com.scout.research` as the git message.
7. Select `com.scout.research`, click Delete, confirm. Verify:
   - Both files removed.
   - `launchctl list | grep com.scout.research` returns nothing.
   - `git -C ~/Scout log -1 --pretty=%s launchd/com.scout.research.plist` → `schedules: remove com.scout.research`.

- [ ] **Step 3: Add a BACKLOG note**

Append to `app/Scout/Scout/BACKLOG.md`:

```markdown
### Shipped — 2026-04-22
- **Schedules tab.** Full CRUD on `com.scout.*.plist` files with auto-reload
  via `launchctl` and path-scoped git commits. Resolves the "research has no
  launchd plist" gap by making it trivial to create new schedules.
```

- [ ] **Step 4: Commit**

```bash
git add app/Scout/Scout/BACKLOG.md
git commit -m "scout-app: Schedules tab shipped — update BACKLOG (task 20)"
```

---

## Self-Review Notes

- **Spec coverage:**
  - ✅ Placement (Task 13)
  - ✅ Data model (Task 1)
  - ✅ PlistIO (Tasks 2, 3)
  - ✅ LaunchdScheduleService refactor + weekday bug fix (Task 4)
  - ✅ LaunchctlClient (Task 5)
  - ✅ GitService.commitPaths (Task 6)
  - ✅ ScheduleDiff / ScheduleTriggerFormatter (Tasks 7, 8)
  - ✅ ScheduleEditorService loadAll/validate/save/create/delete (Tasks 9–12, 19)
  - ✅ SchedulesView + detail (Tasks 13–17)
  - ✅ New Schedule sheet (Task 18)
  - ✅ Commit error banner (Task 14 — in the list view)
  - ✅ Error handling matrix (rollback tests in Task 11, swallowed exit 3 in Task 11, tolerant delete in Task 12)

- **Placeholders:** None. Every code step shows full code.

- **Type consistency:** `Schedule`, `CalendarFire`, `ScheduleTrigger`, `PlistValue`, `ScheduleValidationError`, `LaunchctlError`, `GitServiceError`, `CommitError`, `ScheduleDrift` all defined and used with consistent signatures.

- **Xcode project.pbxproj edits:** Each task that creates a file includes a step to add it to the target. If you prefer, do all additions in one pass at the start (open the project in Xcode, add all files, then proceed task-by-task without touching `project.pbxproj` between commits). Note that doing them up-front means the very first commit includes all file references — adjust the commit-per-task command to exclude `project.pbxproj` in that case.

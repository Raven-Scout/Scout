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
        // Uses StartCalendarInterval with a single dict (not interval).
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
        #expect(s.workingDirectory?.path == "/Users/scout-dev/Scout")
        #expect(s.environment["HOME"] == "/Users/scout-dev")
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
        #expect(reparsed.trigger.semanticallyEquals(original.trigger))
    }

    @Test func roundTripHeartbeatPreservesInterval() throws {
        let original = try PlistIO.readSchedule(from: fixtureURL("com.scout.heartbeat"))
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("com.scout.heartbeat.plist")
        try PlistIO.writeSchedule(original, to: tmp)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let reparsed = try PlistIO.readSchedule(from: tmp)
        #expect(reparsed.trigger.semanticallyEquals(original.trigger))
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
        // Calendar 7 (Sat) → launchd 6.
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

    @Test func normalizesLaunchdSeven() throws {
        // Weekday=7 in launchd is also Sunday. Must normalize to Calendar 1.
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

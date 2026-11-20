import Foundation
import Testing
@testable import Scout

/// Follow-ups to card T2 (#748): one schedule has one name across Control
/// Center, the Runs list's Type filter and search file a run by its ledger
/// key, and a run the ledger says someone started by hand shows "Started by
/// you". Synthetic slot keys, logs and ledger rows only.
@Suite("Run names: upcoming, Type filter, Started by you")
@MainActor
struct RunNameFollowupsTests {

    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    private func upcoming(_ key: String, _ slotType: String) -> UpcomingRun {
        UpcomingRun(slotKey: key, slotType: slotType, scheduledAtUTC: "2027-01-15T08:00:00Z")!
    }

    private func log(_ name: String) -> URL {
        URL(fileURLWithPath: "/v/.scout-logs/\(name)")
    }

    // MARK: 1. One name per schedule

    @Test("an upcoming default schedule is named from its slot key, as the Runs list names its runs")
    func upcomingUsesSlotKeyNames() {
        for (key, type, name) in [
            ("midday-consolidation", "consolidation", "Midday consolidation"),
            ("evening-consolidation", "consolidation", "Evening consolidation"),
            ("dreaming-nightly", "dreaming", "Nightly dreaming"),
            ("morning-briefing", "briefing", "Morning briefing"),
            ("research", "research", "Research"),
            ("late-research", "research", "Late research"),
        ] {
            let up = upcoming(key, type)
            #expect(up.displayName == name)
            #expect(up.displayName == DisplayName.ledgerRun(slot: key, mode: key))
        }
    }

    @Test("Upcoming, the menu bar and the status bar all print UpcomingRun.displayName")
    func viewsUseDisplayName() throws {
        for path in ["ControlCenter/UpcomingStripView.swift", "Shell/MenuBarExtraContent.swift",
                     "Shell/StatusBarView.swift", "ControlCenter/NowStripView.swift"] {
            let text = try source(path)
            #expect(text.contains(".displayName"), "\(path)")
            #expect(!text.contains("type.displayName"), "\(path) names an upcoming run by its type")
        }
    }

    // MARK: 2. Type filter and search

    @Test("a custom slot on run-scout.sh is filed by its slot_type, not the hour's guess")
    func customSlotFiledBySlotType() throws {
        let parsed = try #require(SessionLogService.parseFilename(log("scout-2027-01-12_01-03.log")))
        #expect(parsed.type == .morningBriefing)  // the 1 AM guess
        let run = Run.make(type: parsed.type, startedAt: t0, logPath: log("scout-a.log"),
                           runnerScript: "run-scout.sh", slotKey: "overnight-research",
                           ledgerKey: "overnight-research")
        let types = SessionsListView.scheduleTypes(slots: [], upcoming: [upcoming("overnight-research", "research")])
        #expect(run.listType(scheduleTypes: types) == .research)
        // Not in the schedule (deleted since): the log's type stays.
        #expect(run.listType(scheduleTypes: [:]) == .morningBriefing)
    }

    @Test("default keys map through RunType(slotKey:), hand-started ones by their runner")
    func defaultAndManualKeys() {
        let midday = Run.make(type: .morningBriefing, startedAt: t0, ledgerKey: "midday-consolidation")
        #expect(midday.listType() == .consolidation)
        let dreaming = Run.make(type: .dreaming, startedAt: t0, runnerScript: "run-dreaming.sh",
                                ledgerKey: "dreaming-manual")
        #expect(dreaming.listType() == .dreaming)
        let research = Run.make(type: .research, startedAt: t0, runnerScript: "run-research.sh",
                                ledgerKey: "research-manual")
        #expect(research.listType() == .research)
        // A bare `manual` on run-scout.sh keeps the runner's own guess by the hour.
        let scout = Run.make(type: .consolidation, startedAt: t0, runnerScript: "run-scout.sh", ledgerKey: "manual")
        #expect(scout.listType() == .consolidation)
        // No ledger row: the log's type.
        #expect(Run.make(type: .dreaming, startedAt: t0).listType() == .dreaming)
    }

    @Test("a row that names nothing leaves the log's type and the old badge rule")
    func unknownRowKeepsTheLogGuess() {
        let key = DisplayName.ledgerRunKey(slot: "unknown", mode: "unknown")
        #expect(key == nil)
        let run = Run.make(type: .consolidation, startedAt: t0, slotKey: "unknown", trigger: "schedule",
                           ledgerKey: key)
        #expect(run.listType(scheduleTypes: ["unknown": .research]) == .consolidation)
        #expect(!run.showsManualBadge)
    }

    @Test("an owner's schedule shaped like a hand-run key is filed as that schedule")
    func handRunShapedScheduleKey() {
        // SlotDraft allows `weekly-manual`; its slot_type is research.
        let run = Run.make(type: .consolidation, startedAt: t0, runnerScript: "run-scout.sh",
                           slotKey: "weekly-manual", ledgerKey: "weekly-manual", ledgerKeyIsSchedule: true)
        #expect(run.listType(scheduleTypes: ["weekly-manual": .research]) == .research)
        // Not in the schedule: a hand-run key, by its runner (run-scout.sh keeps the hour's guess).
        #expect(run.listType() == .consolidation)
    }

    @Test("the full schedule's slot_type wins over the upcoming list's")
    func fullScheduleWins() {
        let slot = Slot(key: "focus-hour", type: .research, runner: "run-research.sh", firesAtLocal: "14:00",
                        weekdays: ["mon"], missedWindowHours: 2, onMiss: .fire, cooldownMinutes: 30)
        let types = SessionsListView.scheduleTypes(slots: [slot], upcoming: [upcoming("focus-hour", "consolidation")])
        #expect(types["focus-hour"] == .research)
    }

    @Test("search matches the ledger key, and not the hour's guess")
    func searchUsesLedgerKey() {
        let run = Run.make(type: .morningBriefing, startedAt: t0, runnerScript: "run-scout.sh",
                           slotKey: "overnight-research", ledgerName: "Overnight research",
                           ledgerKey: "overnight-research")
        #expect(!SessionsListView.matches(run, "morning"))
        #expect(SessionsListView.matches(run, "overnight-research"))
        // The slot is unknown, so the mode names the run: search finds the mode's key too.
        let byMode = Run.make(type: .morningBriefing, startedAt: t0, slotKey: "unknown",
                              ledgerName: "Weekly research", ledgerKey: "weekly-research")
        #expect(SessionsListView.matches(byMode, "weekly-research"))
        #expect(!SessionsListView.matches(byMode, "morning"))
    }

    // MARK: 3. Started by you

    @Test("a Run now of a schedule (fire-now's row) marks a log-built run as started by you")
    func ledgerTriggerShowsBadge() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("t2-followups-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let logName = "scout-2027-01-12_19-03.log"
        try "=== Scout run starting at Tue Jan 12 19:03:00 UTC 2027 ===\n=== Scout run finished at Tue Jan 12 19:20:00 UTC 2027 (exit 0) ===\n"
            .write(to: dir.appendingPathComponent(logName), atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: dir.appendingPathComponent("usage.jsonl"), fileEvents: NoopFS())
        let svc = SessionLogService(logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
                                    parseCacheURL: dir.appendingPathComponent("cache.json"))
        _ = try await svc.loadInitial()
        let before = try #require(svc.runs.first)
        #expect(before.source == .launchdScheduled)
        #expect(!before.showsManualBadge)

        // Someone pressed Run now on the evening schedule: `scoutctl schedule fire-now` sets
        // SCOUT_FORCE_MODE (so slot and mode are the schedule's key) and SCOUT_TRIGGER=manual.
        let row = RunLedgerEntry(slot: "evening-consolidation", mode: "evening-consolidation", runner: "scout",
                                 trigger: "manual", startedAt: t0, finishedAt: t0 + 1_020, outcome: "ok", log: logName)
        svc.applyLedger([logName: row])
        let after = try #require(svc.runs.first)
        #expect(after.displayName == "Evening consolidation")
        #expect(after.ledgerKey == "evening-consolidation")
        #expect(after.showsManualBadge)

        // The tick, the heartbeat or a connector event started it: no badge.
        for trigger in ["schedule", "heartbeat", "event"] {
            let other = RunLedgerEntry(slot: "evening-consolidation", mode: "evening-consolidation", runner: "scout",
                                       trigger: trigger, startedAt: t0, finishedAt: t0 + 1_020, outcome: "ok", log: logName)
            svc.applyLedger([logName: other])
            #expect(try #require(svc.runs.first).showsManualBadge == false, "\(trigger)")
        }
    }

    @Test("a schedule named weekly-manual reads as Upcoming names it, and its Run now shows the badge")
    func scheduleKeysRenameHandRunShapedSchedules() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("t2-weekly-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let logName = "scout-2027-01-12_19-03.log"
        try "=== Scout run starting at Tue Jan 12 19:03:00 UTC 2027 ===\n=== Scout run finished at Tue Jan 12 19:20:00 UTC 2027 (exit 0) ===\n"
            .write(to: dir.appendingPathComponent(logName), atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: dir.appendingPathComponent("usage.jsonl"), fileEvents: NoopFS())
        let svc = SessionLogService(logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
                                    parseCacheURL: dir.appendingPathComponent("cache.json"))
        _ = try await svc.loadInitial()
        let row = RunLedgerEntry(slot: "weekly-manual", mode: "weekly-manual", runner: "scout",
                                 trigger: "manual", startedAt: t0, finishedAt: t0 + 1_020, outcome: "ok", log: logName)
        svc.applyLedger([logName: row])
        // Not known as a schedule yet: a hand-run key, whose name says so.
        #expect(svc.runs.first?.displayName == "Weekly (manual)")
        #expect(svc.runs.first?.showsManualBadge == false)

        svc.applyScheduleKeys(["weekly-manual", "evening-consolidation"])
        let run = try #require(svc.runs.first)
        #expect(run.displayName == "Weekly manual")
        #expect(run.displayName == upcoming("weekly-manual", "research").displayName)
        #expect(run.ledgerKeyIsSchedule)
        #expect(run.showsManualBadge)
    }

    @Test("the badge and the name read the same slot-or-mode key")
    func badgeAndNameShareTheKey() {
        // The slot is unknown, so the mode names the run "Dreaming (manual)":
        // the badge must not repeat it.
        let key = DisplayName.ledgerRunKey(slot: "unknown", mode: "dreaming-manual")
        #expect(key == "dreaming-manual")
        let name = DisplayName.ledgerRun(slot: "unknown", mode: "dreaming-manual", runner: "dreaming")
        let run = Run.make(type: .dreaming, startedAt: t0, runnerScript: "run-dreaming.sh", slotKey: "unknown",
                           trigger: "manual", ledgerName: name, ledgerKey: key)
        #expect(run.displayName == "Dreaming (manual)")
        #expect(!run.showsManualBadge)
        #expect(run.listType() == .dreaming)

        // The mode names a schedule: the name doesn't say manual, so the badge does.
        let forced = Run.make(type: .consolidation, startedAt: t0, slotKey: "unknown", trigger: "manual",
                              ledgerName: DisplayName.ledgerRun(slot: "unknown", mode: "midday-consolidation"),
                              ledgerKey: DisplayName.ledgerRunKey(slot: "unknown", mode: "midday-consolidation"))
        #expect(forced.displayName == "Midday consolidation")
        #expect(forced.showsManualBadge)
    }

    private func source(_ path: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appending(path: "Scout")
        return try String(contentsOf: root.appending(path: path), encoding: .utf8)
    }
}

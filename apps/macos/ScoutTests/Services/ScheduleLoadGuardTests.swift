import Testing
import Foundation
@testable import Scout

/// Pins the C1 data-safety fix: a failed schedule load can never be followed
/// by a save that overwrites schedule.yaml, and the stale check is never
/// skipped (it used to be, whenever `loadedMtime` was nil).
private let oneSlotJSON = """
[
  {"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
   "fires_at_local":"08:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
   "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
   "budget_usd":null,"tz":null,"runtime":"local"}
]
"""

private let seedYAML = """
schema_version: 1
slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
  research:
    type: research
    runner: run-research.sh
    fires_at_local: "14:00"
    weekdays: [Mon]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 240
"""

/// A runner whose `schedule list` fails to launch, like a missing scoutctl.
private actor ScheduleLaunchFailingRunner: ProcessRunner {
    nonisolated func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        throw NSError(domain: "test", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "scoutctl not found"])
    }
}

private func tempDir() throws -> URL {
    let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("schedule-guard-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

private func newSlot(_ key: String) -> Slot {
    SchedulesView.makeNewDraftSlot(key: key)
}

@Suite("Schedule load guard (C1)")
@MainActor
struct ScheduleLoadGuardTests {

    @Test("a decode failure marks the load failed and save refuses to touch the file")
    func failedDecode_refusesSave() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: ["Traceback: not json"]),
            canonicalSchedulePath: canonical)

        await #expect(throws: (any Error).self) { try await service.loadAll() }
        guard case .failed = service.loadState else {
            Issue.record("expected .failed, got \(service.loadState)"); return
        }

        // What the page used to do: add one slot to an empty list and save.
        await #expect(throws: ScheduleNotLoadedError.self) {
            try await service.save(allSlots: [newSlot("new-slot-1")])
        }
        #expect(try String(contentsOf: canonical, encoding: .utf8) == seedYAML)
    }

    @Test("a runner failure (scoutctl missing) also marks the load failed")
    func runnerFailure_refusesSave() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: ScheduleLaunchFailingRunner(), canonicalSchedulePath: canonical)

        await #expect(throws: (any Error).self) { try await service.loadAll() }
        #expect(service.loadState == .failed("scoutctl not found"))
        await #expect(throws: ScheduleNotLoadedError.self) {
            try await service.delete(slotKey: "research")
        }
        #expect(try String(contentsOf: canonical, encoding: .utf8) == seedYAML)
    }

    @Test("save before any load is refused")
    func saveWithoutLoad_refused() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [oneSlotJSON]),
            canonicalSchedulePath: canonical)
        #expect(service.loadState == .notLoaded)
        await #expect(throws: ScheduleNotLoadedError.self) {
            try await service.save(allSlots: [newSlot("new-slot-1")])
        }
        #expect(try String(contentsOf: canonical, encoding: .utf8) == seedYAML)
    }

    @Test("a failed reload after a good load also blocks saving")
    func failedReloadAfterSuccess_refused() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [oneSlotJSON, "garbage"]),
            canonicalSchedulePath: canonical)
        try await service.loadAll()
        #expect(service.loadState == .loaded)
        await #expect(throws: (any Error).self) { try await service.loadAll() }
        await #expect(throws: ScheduleNotLoadedError.self) {
            try await service.save(allSlots: service.slots)
        }
    }

    @Test("Retry after a failure loads and allows saving again")
    func retryAfterFailure_allowsSave() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: ["garbage", oneSlotJSON, oneSlotJSON]),
            canonicalSchedulePath: canonical)
        await #expect(throws: (any Error).self) { try await service.loadAll() }
        try await service.loadAll()
        #expect(service.loadState == .loaded)
        try await service.save(allSlots: service.slots)
        #expect(try String(contentsOf: canonical, encoding: .utf8).contains("morning-briefing:"))
    }

    @Test("no file at load, file created since: save is stale, not skipped")
    func nilLoadedMtime_fileAppeared_isStale() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [oneSlotJSON]),
            canonicalSchedulePath: canonical)
        try await service.loadAll()
        #expect(service.loadedMtime == nil)

        // Someone (scoutctl schedule init, an editor) writes the file now.
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)

        await #expect(throws: StaleScheduleError.self) {
            try await service.save(allSlots: [newSlot("new-slot-1")])
        }
        #expect(try String(contentsOf: canonical, encoding: .utf8) == seedYAML)
    }

    @Test("no file at load and still none: save creates it")
    func nilLoadedMtime_stillMissing_saves() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [oneSlotJSON, oneSlotJSON]),
            canonicalSchedulePath: canonical)
        try await service.loadAll()
        try await service.save(allSlots: service.slots)
        #expect(FileManager.default.fileExists(atPath: canonical.path))
    }

    @Test("an mtime that moved backwards (restored from backup) is stale too")
    func mtimeMovedBackwards_isStale() async throws {
        let dir = try tempDir()
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try seedYAML.write(to: canonical, atomically: true, encoding: .utf8)
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [oneSlotJSON]),
            canonicalSchedulePath: canonical)
        try await service.loadAll()
        try FileManager.default.setAttributes(
            [.modificationDate: Date().addingTimeInterval(-3600)], ofItemAtPath: canonical.path)

        await #expect(throws: StaleScheduleError.self) {
            try await service.save(allSlots: service.slots)
        }
    }

    @Test("a removed file reads as removed, not as a nil date")
    func staleErrorForRemovedFile() {
        let error = StaleScheduleError(loadedAt: Date(), modifiedAt: nil)
        #expect(error.errorDescription?.contains("removed") == true)
    }

    // MARK: - The page

    @Test("a failed load shows the error state, never the empty state")
    func failedLoad_isNotEmptyState() {
        let state = SchedulesView.contentState(
            isInitialLoading: false, loadState: .failed("bad json"),
            hasVisibleSlots: false, hasNewDraft: false)
        #expect(state == .loadFailed("bad json"))
    }

    @Test("a failed reload replaces the list with the error state")
    func failedReload_hidesStaleList() {
        #expect(SchedulesView.contentState(
            isInitialLoading: false, loadState: .failed("x"),
            hasVisibleSlots: true, hasNewDraft: false) == .loadFailed("x"))
    }

    @Test("only a successful load with no slots shows the empty state")
    func emptyStateNeedsASuccessfulLoad() {
        #expect(SchedulesView.contentState(
            isInitialLoading: false, loadState: .loaded,
            hasVisibleSlots: false, hasNewDraft: false) == .empty)
        #expect(SchedulesView.contentState(
            isInitialLoading: false, loadState: .notLoaded,
            hasVisibleSlots: false, hasNewDraft: false) == .loading)
        #expect(SchedulesView.contentState(
            isInitialLoading: true, loadState: .loaded,
            hasVisibleSlots: true, hasNewDraft: false) == .loading)
    }
}

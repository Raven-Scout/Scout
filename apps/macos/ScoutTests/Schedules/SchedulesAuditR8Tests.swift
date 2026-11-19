import Foundation
import Testing
@testable import Scout

/// UI audit card R8 (Schedules): findings #55, #56, #58, #60 and #61.
@Suite("Schedules R8 — validation wording, time picker, duplicates")
struct SchedulesR8FormTests {
    static let slot = Slot(
        key: "morning-briefing", type: .briefing, runner: "run-scout.sh",
        firesAtLocal: "08:00", weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
        missedWindowHours: 4, onMiss: .fire, cooldownMinutes: 60
    )

    // #55: errors use the form's field names, never the file's.
    @Test("validation errors name the form's fields, not runner/cooldown/missed window")
    func plainValidationWording() {
        var empty = SlotDraft(from: Self.slot)
        empty.runner = "  "
        var negative = SlotDraft(from: Self.slot)
        negative.cooldownMinutes = -15
        var zero = SlotDraft(from: Self.slot)
        zero.missedWindowHours = 0
        let messages = [empty, negative, zero].compactMap { $0.firstError() }
        #expect(messages.count == 3)
        for message in messages {
            for jargon in ["Runner", "runner", "Cooldown", "Missed window", ">=", "> 0", "HH:MM"] {
                #expect(!message.contains(jargon), "\(message) still says \(jargon)")
            }
        }
        #expect(messages[0].contains(SlotFieldLabel.script))
        #expect(messages[1].contains(SlotFieldLabel.waitBetweenRuns))
        #expect(messages[2].contains(SlotFieldLabel.catchUpWindow))
        #expect(SlotDraft.validateFiresAtLocal("25:00") == SlotValidationMessage.badTime)
    }

    // #58: a new schedule can't take a saved schedule's name.
    @Test("a new schedule's name must not clash with a saved one")
    func duplicateName() {
        var draft = SlotDraft(from: Self.slot)
        draft.key = "research"
        let taken: Set<String> = ["morning-briefing", "research"]
        #expect(SlotDraft.validateNewSlotKey("research", existingKeys: taken) != nil)
        #expect(SlotDraft.validateNewSlotKey("evening-research", existingKeys: taken) == nil)
        #expect(draft.firstError(existingKeys: taken) == SlotValidationMessage.duplicateName("research"))
        #expect(SlotValidationMessage.duplicateName("research").contains("Research"))
    }

    @Test("only a new schedule is checked against saved names")
    func takenKeysOnlyForNewDrafts() {
        let keys: Set<String> = ["morning-briefing", "new-slot-1"]
        #expect(SlotEditForm.takenKeys(isNewDraft: false, existingKeys: keys, liveKey: "morning-briefing").isEmpty)
        // The new draft's own placeholder key never counts against it.
        #expect(SlotEditForm.takenKeys(isNewDraft: true, existingKeys: keys, liveKey: "new-slot-1") == ["morning-briefing"])
        // An existing schedule's own key is fine.
        #expect(SlotEditForm.saveBlockedReason(draft: SlotDraft(from: Self.slot), live: Self.slot,
                                               isNewDraft: false, existingKeys: ["morning-briefing"]) == nil)
    }

    @Test("Save's blocked reason surfaces a field hidden under Advanced")
    func saveBlockedReasonForAdvancedField() {
        var draft = SlotDraft(from: Self.slot)
        draft.runner = ""
        #expect(SlotEditForm.saveBlockedReason(draft: draft, live: Self.slot, isNewDraft: false, existingKeys: [])
                == SlotValidationMessage.emptyScript)
    }

    // #58: the time picker round-trips to zero-padded 24-hour.
    @Test("the time picker reads and writes fires_at_local", arguments: ["00:00", "07:05", "13:30", "23:59"])
    func timeRoundTrip(_ value: String) throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Prague")!
        let day = Date(timeIntervalSince1970: 1_780_000_000)
        let date = try #require(SlotDraft.date(fromFiresAt: value, calendar: calendar, on: day))
        #expect(SlotDraft.firesAt(from: date, calendar: calendar) == value)
    }

    @Test("a picked time is always zero-padded; garbage reads as no time")
    func timeEdges() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let day = Date(timeIntervalSince1970: 0)
        let eight = try #require(SlotDraft.date(fromFiresAt: "8:00", calendar: calendar, on: day))
        #expect(SlotDraft.firesAt(from: eight, calendar: calendar) == "08:00")
        for bad in ["", "noon", "24:00", "08:60", "08:5", "08:00:00"] {
            #expect(SlotDraft.date(fromFiresAt: bad, calendar: calendar, on: day) == nil, "\(bad)")
        }
    }
}

@Suite("Schedules R8 — problems behind Details")
struct SchedulesR8ProblemTests {
    // #56: Run now from the Schedules page says when it didn't start.
    @Test("a failed Run now becomes a plain problem with scoutctl's words under Details")
    func fireNowProblem() throws {
        #expect(SchedulesView.fireNowProblem(nil, slotKey: "research") == nil)
        let raw = "Run now failed (exit 2): Traceback (most recent call last): boom"
        let problem = try #require(SchedulesView.fireNowProblem(raw, slotKey: "morning-briefing"))
        #expect(problem.severity == .error)
        #expect(problem.title == "Couldn't start Morning briefing")
        #expect(problem.details == raw)
        #expect(!(problem.impact ?? "").contains("exit"))
        #expect(!(problem.fix ?? "").contains("Traceback"))
    }

    @Test("the load-failed state keeps scoutctl's output under Details")
    func loadFailedProblem() {
        let raw = "scoutctl printed something that isn't JSON:\nWARNING: stale"
        let problem = SchedulesView.loadFailedProblem(raw)
        #expect(problem.title == "Couldn't load the schedule")
        #expect(problem.details == raw)
        #expect(!problem.title.contains("JSON") && !(problem.impact ?? "").contains("JSON"))
    }

    @Test("a duplicate-key refusal maps to a plain write problem")
    func duplicateKeyWriteProblem() {
        let write = WriteProblem.from(ScheduleDuplicateKeyError(keys: ["research"]), action: "add the schedule")
        #expect(write.title == "Couldn't add the schedule")
        #expect(write.message.contains("same name"))
        #expect(write.details?.contains("research") == true)
    }
}

// #60: the timeline draws one day at a time.
@Suite("Schedules R8 — timeline weekdays")
struct SchedulesR8TimelineTests {
    static func slot(_ key: String, _ days: [String]) -> Slot {
        Slot(key: key, type: .briefing, runner: "run-scout.sh", firesAtLocal: "08:00",
             weekdays: days, missedWindowHours: 4, onMiss: .fire, cooldownMinutes: 60)
    }

    @Test("a weekday-only schedule doesn't draw on a weekend")
    func filtersByDay() {
        let weekday = Self.slot("morning-briefing", ["Mon", "Tue", "Wed", "Thu", "Fri"])
        let weekend = Self.slot("weekend-briefing", ["Sat", "Sun"])
        let all = [weekday, weekend]
        #expect(SchedulesTimelineView.slots(all, on: "Sat").map(\.key) == ["weekend-briefing"])
        #expect(SchedulesTimelineView.slots(all, on: "Wed").map(\.key) == ["morning-briefing"])
        #expect(SchedulesTimelineView.slots(all, on: nil).count == 2)
    }

    @Test("weekday tokens match the schedule's spelling")
    func weekdayToken() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        // 1970-01-01 was a Thursday; 1970-01-04 a Sunday.
        #expect(SchedulesTimelineView.weekdayToken(for: Date(timeIntervalSince1970: 0), calendar: calendar) == "Thu")
        #expect(SchedulesTimelineView.weekdayToken(for: Date(timeIntervalSince1970: 3 * 86_400), calendar: calendar) == "Sun")
        #expect(SchedulesTimelineView.weekdayToken(for: Date(timeIntervalSince1970: 4 * 86_400), calendar: calendar) == "Mon")
    }

    @Test("the now line shows only on today or every day")
    func nowLine() {
        #expect(SchedulesTimelineView.showsNow(day: nil, today: "Tue"))
        #expect(SchedulesTimelineView.showsNow(day: "Tue", today: "Tue"))
        #expect(!SchedulesTimelineView.showsNow(day: "Sat", today: "Tue"))
    }
}

// MARK: - Service: duplicate guard and watching schedule.yaml (#58, #61)

private let listJSON = """
[{"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
  "fires_at_local":"08:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
  "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
  "budget_usd":null,"tz":null,"runtime":"local"}]
"""

private let editedListJSON = """
[{"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
  "fires_at_local":"09:15","weekdays":["Mon","Tue","Wed","Thu","Fri"],
  "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
  "budget_usd":null,"tz":null,"runtime":"local"}]
"""

private let yaml = """
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
"""

@MainActor
private func makeService(stdouts: [String], drafts: SlotDraftStore? = nil) throws -> (ScheduleEditService, QueueProcessRunner, URL) {
    let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("schedule-r8-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let canonical = dir.appendingPathComponent("schedule.yaml")
    try yaml.write(to: canonical, atomically: true, encoding: .utf8)
    let runner = QueueProcessRunner(stdouts: stdouts)
    let service = ScheduleEditService(
        scoutctl: URL(fileURLWithPath: "/usr/bin/env"), runner: runner,
        canonicalSchedulePath: canonical, argumentsPrefix: ["scoutctl"], drafts: drafts)
    return (service, runner, canonical)
}

@Suite("Schedules R8 — service")
@MainActor
struct SchedulesR8ServiceTests {
    @Test("save refuses two slots under one key and leaves the file alone")
    func duplicateKeysRefused() async throws {
        let (service, runner, canonical) = try makeService(stdouts: [listJSON])
        try await service.loadAll()
        let before = try Data(contentsOf: canonical)
        let slot = service.slots[0]
        await #expect(throws: ScheduleDuplicateKeyError(keys: ["morning-briefing"])) {
            try await service.save(allSlots: [slot, slot])
        }
        #expect(try Data(contentsOf: canonical) == before)
        #expect(await runner.calls.allSatisfy { !$0.arguments.contains("validate") })
    }

    @Test("an unchanged file doesn't reload; a changed one does")
    func reloadOnlyWhenChanged() async throws {
        let (service, runner, canonical) = try makeService(stdouts: [listJSON, editedListJSON])
        try await service.loadAll()
        #expect(await service.reloadIfChangedOnDisk() == false)
        #expect(await runner.calls.count == 1)
        #expect(service.externalReloadCount == 0)

        try yaml.replacingOccurrences(of: "\"08:00\"", with: "\"09:15\"")
            .write(to: canonical, atomically: true, encoding: .utf8)
        #expect(await service.reloadIfChangedOnDisk() == true)
        #expect(service.slots[0].firesAtLocal == "09:15")
        #expect(service.externalReloadCount == 1)
        // The reload's baseline is the new file, so a save isn't stale.
        #expect(await service.reloadIfChangedOnDisk() == false)
    }

    @Test("a reload from disk keeps an unsaved draft and its baseline")
    func reloadKeepsDrafts() async throws {
        let store = SlotDraftStore()
        let (service, _, canonical) = try makeService(stdouts: [listJSON, editedListJSON], drafts: store)
        try await service.loadAll()
        let live = service.slots[0]
        var draft = SlotDraft(from: live)
        draft.cooldownMinutes = 120
        store.update(draft, baseline: live, for: live.key)

        try yaml.replacingOccurrences(of: "\"08:00\"", with: "\"09:15\"")
            .write(to: canonical, atomically: true, encoding: .utf8)
        #expect(await service.reloadIfChangedOnDisk())

        // The draft survives, and reopening the form rebases it: the user's
        // wait stays, the time edited on disk comes through.
        let entry = try #require(store.entry(for: live.key))
        let reopened = SlotEditForm.initialDraft(live: service.slots[0], stashed: entry)
        #expect(reopened.draft.cooldownMinutes == 120)
        #expect(reopened.draft.firesAtLocal == "09:15")
        #expect(reopened.conflicts.isEmpty)
        #expect(store.droppedSlotNames.isEmpty)
    }

    @Test("a change event for schedule.yaml reloads; other files in the folder don't")
    func watcherReloadsOnScheduleEvents() async throws {
        let (service, runner, canonical) = try makeService(stdouts: [listJSON, editedListJSON])
        try await service.loadAll()
        let fs = InjectableFS()
        service.startWatching(fs, debounce: .milliseconds(10))
        defer { service.stopWatching() }

        // A temp file beside it (the app's own save writes one) is ignored.
        fs.emit(FileSystemEvent(url: canonical.deletingLastPathComponent()
            .appendingPathComponent("schedule.yaml.tmp"), kind: .modified))
        try yaml.replacingOccurrences(of: "\"08:00\"", with: "\"09:15\"")
            .write(to: canonical, atomically: true, encoding: .utf8)
        fs.emit(FileSystemEvent(url: canonical, kind: .modified))
        fs.emit(FileSystemEvent(url: canonical, kind: .modified))

        for _ in 0..<200 where service.externalReloadCount == 0 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(service.externalReloadCount == 1)
        #expect(service.slots[0].firesAtLocal == "09:15")
        #expect(await runner.calls.count == 2)
    }
}

import Testing
import Foundation
@testable import Scout

/// Reusable canned `scoutctl schedule list --json` output.
private let sampleListJSON = """
[
  {"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
   "fires_at_local":"08:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
   "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
   "budget_usd":null,"tz":null,"runtime":"local"},
  {"key":"research","type":"research","runner":"run-research.sh",
   "fires_at_local":"14:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
   "missed_window_hours":4,"on_miss":"skip","cooldown_minutes":240,
   "budget_usd":null,"tz":null,"runtime":"local"}
]
"""

/// Builds a service backed by a real on-disk schedule.yaml. Returns the
/// service, the runner (so tests can inspect calls), and the temp dir
/// (so tests can read back the canonical file or check for tmpfile leaks).
@MainActor
private func makeServiceOnDisk(
    listJSON: String = sampleListJSON,
    validateExitCode: Int32 = 0,
    validateStderr: Data = Data(),
    drafts: SlotDraftStore? = nil,
    reloadJSON: String? = nil
) throws -> (ScheduleEditService, QueueProcessRunner, URL) {
    let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("schedule-edit-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let canonical = dir.appendingPathComponent("schedule.yaml")
    try """
    # Header comment — line 1
    # Header comment — line 2
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
        weekdays: [Mon, Tue, Wed, Thu, Fri]
        missed_window_hours: 4
        on_miss: skip
        cooldown_minutes: 240
    """.write(to: canonical, atomically: true, encoding: .utf8)

    // Two queued stdouts: one for loadAll(), one for the post-save reload.
    let runner = QueueProcessRunner(
        stdouts: [listJSON, reloadJSON ?? listJSON],
        exitCode: 0,
        validateExitCode: validateExitCode,
        validateStderr: validateStderr
    )
    let service = ScheduleEditService(
        scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
        runner: runner,
        canonicalSchedulePath: canonical,
        argumentsPrefix: ["scoutctl"],
        drafts: drafts
    )
    return (service, runner, dir)
}

@Suite("ScheduleEditService")
@MainActor
struct ScheduleEditServiceTests {

    @Test func loadAll_decodes_slots_from_scoutctl_output() async throws {
        let runner = QueueProcessRunner(stdouts: [sampleListJSON])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none")
        )
        try await service.loadAll()
        #expect(service.slots.count == 2)
        #expect(service.slots[0].key == "morning-briefing")
        #expect(service.slots[1].key == "research")
    }

    @Test func loadAll_invokes_scoutctl_with_correct_arguments() async throws {
        let runner = QueueProcessRunner(stdouts: [sampleListJSON])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none"),
            argumentsPrefix: ["scoutctl"]
        )
        try await service.loadAll()
        let calls = await runner.calls
        #expect(calls.count == 1)
        #expect(calls[0].arguments == ["scoutctl", "schedule", "list", "--json"])
    }

    @Test func loadAll_throws_on_malformed_json() async throws {
        let runner = QueueProcessRunner(stdouts: ["{not valid json"])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none")
        )
        do {
            try await service.loadAll()
            Issue.record("expected throw")
        } catch {
            // Expected — DecodingError or similar.
        }
    }

    @Test("save writes canonical atomically on validate success")
    func test_save_writes_canonical_atomically_on_validate_success() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        // Edit cooldown on the first slot.
        var draftSlots = service.slots
        let original = draftSlots[0]
        draftSlots[0] = Slot(
            key: original.key,
            type: original.type,
            runner: original.runner,
            firesAtLocal: original.firesAtLocal,
            weekdays: original.weekdays,
            missedWindowHours: original.missedWindowHours,
            onMiss: original.onMiss,
            cooldownMinutes: 999, // changed
            budgetUsd: original.budgetUsd,
            tz: original.tz,
            runtime: original.runtime
        )

        try await service.save(allSlots: draftSlots)
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let written = try String(contentsOf: canonical, encoding: .utf8)
        #expect(written.contains("cooldown_minutes: 999"))
    }

    @Test("save throws StaleScheduleError when file modified externally")
    func test_save_throws_stale_when_file_modified_externally() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        // Simulate an external edit by setting the canonical mtime to the future.
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let future = Date().addingTimeInterval(60)
        try FileManager.default.setAttributes(
            [.modificationDate: future],
            ofItemAtPath: canonical.path
        )

        do {
            try await service.save(allSlots: service.slots)
            Issue.record("expected StaleScheduleError")
        } catch is StaleScheduleError {
            // Expected.
        }

        // Canonical still contains the original cooldown — save was blocked.
        let unchanged = try String(contentsOf: canonical, encoding: .utf8)
        #expect(unchanged.contains("cooldown_minutes: 60"))
    }

    @Test("save cleans up tmpfile on validate failure")
    func test_save_cleans_up_tmpfile_on_validate_failure() async throws {
        let (service, _, dir) = try makeServiceOnDisk(
            validateExitCode: 1,
            validateStderr: "schema_version mismatch".data(using: .utf8) ?? Data()
        )
        try await service.loadAll()

        do {
            try await service.save(allSlots: service.slots)
            Issue.record("expected throw on validate failure")
        } catch is StaleScheduleError {
            Issue.record("got StaleScheduleError instead of validate-failure NSError")
        } catch {
            // Expected — engine validate failed, surfaced as NSError.
        }

        // No tmpfiles left in the directory.
        let contents = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        let tmps = contents.filter { $0.lastPathComponent.contains(".tmp") }
        #expect(tmps == [], "tmpfile leaked: \(tmps)")
    }

    @Test("save cleans up tmpfile on success")
    func test_save_cleans_up_tmpfile_on_success() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()
        try await service.save(allSlots: service.slots)
        let contents = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        let tmps = contents.filter { $0.lastPathComponent.contains(".tmp") }
        #expect(tmps == [], "tmpfile leaked after success: \(tmps)")
    }

    @Test("save preserves header comments byte-for-byte")
    @MainActor
    func test_save_preserves_header_comments_byte_for_byte() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        let canonical = dir.appendingPathComponent("schedule.yaml")
        let beforeText = try String(contentsOf: canonical, encoding: .utf8)
        // The seed has two header comment lines + a `schema_version: 1` line.
        let beforeHeader = beforeText.components(separatedBy: "\nslots:").first ?? ""
        #expect(beforeHeader.contains("# Header comment — line 1"))
        #expect(beforeHeader.contains("schema_version: 1"))

        try await service.save(allSlots: service.slots)
        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        let afterHeader = afterText.components(separatedBy: "\nslots:").first ?? ""
        #expect(afterHeader == beforeHeader, "header should survive byte-for-byte")
    }

    @Test("delete removes slot and rewrites canonical")
    @MainActor
    func test_delete_removes_slot_and_rewrites_canonical() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        try await service.delete(slotKey: "research")

        let canonical = dir.appendingPathComponent("schedule.yaml")
        let written = try String(contentsOf: canonical, encoding: .utf8)
        #expect(!written.contains("research:"), "research slot should be gone")
        #expect(written.contains("morning-briefing:"), "morning-briefing should remain")
    }

    @Test("delete throws when key not found")
    @MainActor
    func test_delete_throws_when_key_not_found() async throws {
        let (service, _, _) = try makeServiceOnDisk()
        try await service.loadAll()
        do {
            try await service.delete(slotKey: "nonexistent")
            Issue.record("expected throw")
        } catch {
            // Expected.
        }
    }

    @Test("agent_runtime survives load → edit another field → save (C2)")
    @MainActor
    func test_save_keeps_agent_runtime_when_another_field_is_edited() async throws {
        let listJSON = """
        [
          {"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
           "fires_at_local":"08:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
           "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
           "budget_usd":null,"tz":null,"runtime":"local","model":null,
           "agent_runtime":"claude-code"},
          {"key":"research","type":"research","runner":"run-research.sh",
           "fires_at_local":"14:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
           "missed_window_hours":4,"on_miss":"skip","cooldown_minutes":240,
           "budget_usd":null,"tz":null,"runtime":"local","model":null,
           "agent_runtime":null}
        ]
        """
        let (service, _, dir) = try makeServiceOnDisk(listJSON: listJSON)
        try await service.loadAll()
        #expect(service.slots[0].agentRuntime == "claude-code")

        // Edit the cooldown through the same draft path the form uses.
        var draft = SlotDraft(from: service.slots[0])
        draft.cooldownMinutes = 90
        var updated = service.slots
        updated[0] = draft.toSlot()
        try await service.save(allSlots: updated)

        let written = try String(
            contentsOf: dir.appendingPathComponent("schedule.yaml"), encoding: .utf8
        )
        let blocks = written.components(separatedBy: "\n  research:\n")
        #expect(blocks.count == 2)
        #expect(blocks[0].contains("cooldown_minutes: 90"))
        #expect(blocks[0].contains("\n    agent_runtime: claude-code"))
        // An unset agent runtime stays unset: no key, so the slot keeps inheriting.
        #expect(!blocks[1].contains("agent_runtime"))
    }

    @Test("save falls back to header-less emit when canonical has no slots: anchor")
    @MainActor
    func test_save_falls_back_to_pure_yaml_when_no_slots_anchor() async throws {
        // Seed a malformed file (no `\nslots:` line). save still produces
        // valid YAML via the header-less hand-rolled emit path.
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("schedule-edit-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try "not valid yaml at all".write(to: canonical, atomically: true, encoding: .utf8)

        // Two queued list-stdouts: initial loadAll + post-save reload.
        let runner = QueueProcessRunner(stdouts: [sampleListJSON, sampleListJSON])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )
        try await service.loadAll()
        try await service.save(allSlots: service.slots)

        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        #expect(afterText.contains("schema_version"))
        #expect(afterText.contains("morning-briefing"))
    }
}

/// FIFO-stdouts ProcessRunner test stub. Mirrors the pattern used in
/// ScheduleServiceTests.StubScheduleRunner (Plan 5). Single-stdout queues
/// reuse their last entry on exhaustion so existing single-stdout tests
/// never run dry.
///
/// Validate calls (arguments contain "validate") return a configurable
/// exit code + stderr (no stdout); list calls drain the queued stdouts.
actor QueueProcessRunner: ProcessRunner {
    struct Call: Sendable {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
        let workingDirectory: URL?
    }

    private(set) var calls: [Call] = []
    private var outputs: [Data]
    private let exitCode: Int32
    private let validateExitCode: Int32
    private let validateStderr: Data

    init(
        stdouts: [String],
        exitCode: Int32 = 0,
        validateExitCode: Int32 = 0,
        validateStderr: Data = Data()
    ) {
        self.outputs = stdouts.map { $0.data(using: .utf8) ?? Data() }
        self.exitCode = exitCode
        self.validateExitCode = validateExitCode
        self.validateStderr = validateStderr
    }

    nonisolated func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        await record(Call(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: workingDirectory
        ))
        if arguments.contains("validate") {
            return await validateResult()
        }
        let payload = await consume()
        return ProcessResult(exitCode: exitCode, stdout: payload, stderr: Data())
    }

    private func record(_ call: Call) {
        calls.append(call)
    }

    private func consume() -> Data {
        if outputs.count > 1 {
            return outputs.removeFirst()
        }
        return outputs.first ?? Data()
    }

    private func validateResult() -> ProcessResult {
        ProcessResult(exitCode: validateExitCode, stdout: Data(), stderr: validateStderr)
    }
}

/// Unsaved slot-form drafts are settled by the service itself after every
/// load, so it happens whether or not the Schedules page is on screen.
@Suite("ScheduleEditService + SlotDraftStore")
@MainActor
struct ScheduleEditServiceDraftTests {
    @Test("every load reconciles stored drafts: saved ones quietly, orphans reported")
    func loadReconcilesDrafts() async throws {
        let store = SlotDraftStore()
        let (service, _, _) = try makeServiceOnDisk(drafts: store)
        try await service.loadAll()
        let morning = try #require(service.slots.first { $0.key == "morning-briefing" })

        // A draft typed against an older morning slot that the file now
        // matches (its save landed with the page closed)…
        var older = SlotDraft(from: morning)
        older.cooldownMinutes = 15
        store.update(SlotDraft(from: morning), baseline: older.toSlot(), for: morning.key)
        // …and an edit to a slot the file no longer has.
        var gone = SlotDraft(from: morning)
        gone.key = "evening-run"
        gone.firesAtLocal = "18:00"
        store.update(gone, baseline: morning, for: "evening-run")
        #expect(store.unsavedSlotNames.count == 2)

        try await service.loadAll()
        #expect(!store.hasUnsavedDrafts)
        #expect(store.droppedSlotNames == ["Evening run"])
    }

    @Test("a failed save keeps the draft")
    func failedSaveKeepsDraft() async throws {
        let store = SlotDraftStore()
        let (service, _, _) = try makeServiceOnDisk(validateExitCode: 1, drafts: store)
        try await service.loadAll()
        let morning = try #require(service.slots.first { $0.key == "morning-briefing" })
        var draft = SlotDraft(from: morning)
        draft.firesAtLocal = "07:30"
        store.update(draft, baseline: morning, for: morning.key)

        await #expect(throws: (any Error).self) {
            try await service.update(actionName: "Edit Schedule") { slots in
                slots.map { $0.key == morning.key ? draft.toSlot() : $0 }
            }
        }
        #expect(store.entry(for: morning.key)?.draft == draft)
        #expect(store.droppedSlotNames.isEmpty)
    }

    @Test("a write that lands but can't reload says so, with its change for undo")
    func reloadFailureAfterWriteIsDistinct() async throws {
        let (service, _, dir) = try makeServiceOnDisk(reloadJSON: "not json")
        try await service.loadAll()

        do {
            try await service.delete(slotKey: "research")
            Issue.record("expected ScheduleReloadAfterWriteError")
        } catch let written as ScheduleReloadAfterWriteError {
            #expect(written.change?.actionName == "Delete Schedule")
        }
        // The delete is on disk.
        let onDisk = try String(contentsOf: dir.appendingPathComponent("schedule.yaml"), encoding: .utf8)
        #expect(!onDisk.contains("research:"))
        #expect(SchedulesView.problemText(
            ScheduleReloadAfterWriteError(change: nil, reason: "bad output"),
            action: "delete the schedule").hasPrefix("Saved, but the page didn't update."))
    }
}

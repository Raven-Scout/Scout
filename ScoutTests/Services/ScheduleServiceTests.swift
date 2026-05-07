import Testing
import Foundation
@testable import Scout

/// Mock `ProcessRunner` that returns canned stdout payloads from a FIFO
/// queue — each `run(...)` consumes one entry. Tests that exercise multi-
/// refresh sequences (e.g. "valid then malformed") rely on the queue so
/// they can drive the same service instance through a sequence of stubbed
/// outputs without juggling instances. If the queue is exhausted, the last
/// payload is reused — most single-refresh tests just push one entry.
actor StubScheduleRunner: ProcessRunner {
    struct Call: Sendable {
        let executable: URL
        let arguments: [String]
    }

    private(set) var calls: [Call] = []
    private var outputs: [Data]
    private let exitCode: Int32

    init(stdout: String, exitCode: Int32 = 0) {
        self.outputs = [stdout.data(using: .utf8) ?? Data()]
        self.exitCode = exitCode
    }

    init(stdouts: [String], exitCode: Int32 = 0) {
        self.outputs = stdouts.map { $0.data(using: .utf8) ?? Data() }
        self.exitCode = exitCode
    }

    nonisolated func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        await record(executable: executable, arguments: arguments)
        let payload = await consume()
        return ProcessResult(exitCode: exitCode, stdout: payload, stderr: Data())
    }

    private func record(executable: URL, arguments: [String]) {
        calls.append(.init(executable: executable, arguments: arguments))
    }

    private func consume() -> Data {
        // Pop from the front; reuse the last entry if exhausted so callers
        // that don't care about the queue (single-refresh tests) keep working.
        if outputs.count > 1 {
            return outputs.removeFirst()
        }
        return outputs.first ?? Data()
    }
}

/// Mock that throws to exercise error swallowing.
struct ThrowingRunner: ProcessRunner {
    struct E: Error {}
    func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        throw E()
    }
}

@Suite("ScheduleService")
@MainActor
struct ScheduleServiceTests {
    private static let validJSON = """
    [
      {"slot_key": "morning-briefing", "slot_type": "briefing",
       "scheduled_at_local": "2026-05-08T08:00:00-04:00",
       "scheduled_at_utc": "2026-05-08T12:00:00Z"},
      {"slot_key": "evening-consolidation", "slot_type": "consolidation",
       "scheduled_at_local": "2026-05-07T19:00:00-04:00",
       "scheduled_at_utc": "2026-05-07T23:00:00Z"},
      {"slot_key": "dreaming-evening", "slot_type": "dreaming",
       "scheduled_at_local": "2026-05-07T18:30:00-04:00",
       "scheduled_at_utc": "2026-05-07T22:30:00Z"}
    ]
    """

    @Test func refreshDecodesJSONIntoUpcoming() async {
        let runner = StubScheduleRunner(stdout: Self.validJSON)
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        let upcoming = service.upcoming
        #expect(upcoming.count == 3)
        #expect(upcoming[0].slotKey == "morning-briefing")
        #expect(upcoming[0].type == .morningBriefing)
        #expect(upcoming[1].slotKey == "evening-consolidation")
        #expect(upcoming[1].type == .consolidation)
        #expect(upcoming[2].slotKey == "dreaming-evening")
        #expect(upcoming[2].type == .dreaming)
    }

    @Test func refreshPassesExpectedArguments() async {
        let runner = StubScheduleRunner(stdout: Self.validJSON)
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        let calls = await runner.calls
        #expect(calls.count == 1)
        #expect(calls[0].executable.path == "/usr/bin/env")
        #expect(calls[0].arguments == [
            "scoutctl", "schedule", "list-upcoming", "--window", "24", "--json",
        ])
    }

    @Test func refreshWithEmptyJSONProducesEmptyUpcoming() async {
        let runner = StubScheduleRunner(stdout: "[]")
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        #expect(service.upcoming.isEmpty)
    }

    @Test func refreshWithMalformedJSONLeavesUpcomingUnchanged() async {
        // Queue: first refresh returns valid JSON; second returns garbage.
        // The service should keep the last-good `upcoming` instead of
        // resetting to `[]` when decoding throws.
        let runner = StubScheduleRunner(stdouts: [Self.validJSON, "{not valid json"])
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        let lastGood = service.upcoming
        #expect(!lastGood.isEmpty)

        await service.refresh()  // malformed payload — should be swallowed.
        #expect(service.upcoming == lastGood)
    }

    @Test func refreshSwallowsRunnerErrors() async {
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: ThrowingRunner(),
            argumentsPrefix: ["scoutctl"]
        )
        // Should not crash or throw; upcoming stays at default (empty).
        await service.refresh()
        #expect(service.upcoming.isEmpty)
    }

    @Test func startIsIdempotent() async {
        // Calling start() twice in a row should not crash and should not
        // orphan a still-firing timer. After stop(), the timer reference
        // is cleared.
        let runner = StubScheduleRunner(stdout: Self.validJSON)
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        service.start()
        service.start()
        service.stop()
        // No assertion on internal pollTimer — relying on no-crash + the
        // implementation's invalidate-before-reassign guard.
    }

    @Test func refreshSkipsUnknownSlotKeys() async {
        let mixedJSON = """
        [
          {"slot_key": "morning-briefing", "slot_type": "briefing",
           "scheduled_at_local": "2026-05-08T08:00:00-04:00",
           "scheduled_at_utc": "2026-05-08T12:00:00Z"},
          {"slot_key": "totally-made-up", "slot_type": "mystery",
           "scheduled_at_local": "2026-05-08T09:00:00-04:00",
           "scheduled_at_utc": "2026-05-08T13:00:00Z"}
        ]
        """
        let runner = StubScheduleRunner(stdout: mixedJSON)
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        // Only the known slot key survives compactMap.
        #expect(service.upcoming.count == 1)
        #expect(service.upcoming[0].slotKey == "morning-briefing")
    }
}

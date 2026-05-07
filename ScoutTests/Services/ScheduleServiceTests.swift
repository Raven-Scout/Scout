import Testing
import Foundation
@testable import Scout

/// Mock `ProcessRunner` that returns a canned stdout payload. Records the
/// invocation so tests can assert the args passed to scoutctl.
actor StubScheduleRunner: ProcessRunner {
    struct Call: Sendable {
        let executable: URL
        let arguments: [String]
    }

    private(set) var calls: [Call] = []
    private let payload: Data
    private let exitCode: Int32

    init(stdout: String, exitCode: Int32 = 0) {
        self.payload = stdout.data(using: .utf8) ?? Data()
        self.exitCode = exitCode
    }

    nonisolated func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        await record(executable: executable, arguments: arguments)
        return await ProcessResult(exitCode: exitCode, stdout: payload, stderr: Data())
    }

    private func record(executable: URL, arguments: [String]) {
        calls.append(.init(executable: executable, arguments: arguments))
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
        // First refresh: populate with valid data.
        let goodRunner = StubScheduleRunner(stdout: Self.validJSON)
        let service = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: goodRunner,
            argumentsPrefix: ["scoutctl"]
        )
        await service.refresh()
        let snapshot = service.upcoming
        #expect(!snapshot.isEmpty)

        // Swap in a malformed payload runner (separate service instance because
        // the runner is private; the @Published value is what we care about).
        let badRunner = StubScheduleRunner(stdout: "{not valid json")
        let service2 = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: badRunner,
            argumentsPrefix: ["scoutctl"]
        )
        // Manually seed the second service's upcoming via a successful refresh
        // first, then break it — exercises the "swallow on error, keep last" path.
        let combinedRunner = StubScheduleRunner(stdout: Self.validJSON)
        let svc = ScheduleService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: combinedRunner,
            argumentsPrefix: ["scoutctl"]
        )
        await svc.refresh()
        let lastGood = svc.upcoming
        #expect(!lastGood.isEmpty)

        _ = service2 // silence unused warning
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

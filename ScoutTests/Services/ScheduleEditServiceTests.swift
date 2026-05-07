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
}

/// FIFO-stdouts ProcessRunner test stub. Mirrors the pattern used in
/// ScheduleServiceTests.StubScheduleRunner (Plan 5). Single-stdout queues
/// reuse their last entry on exhaustion so existing single-stdout tests
/// never run dry.
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

    init(stdouts: [String], exitCode: Int32 = 0) {
        self.outputs = stdouts.map { $0.data(using: .utf8) ?? Data() }
        self.exitCode = exitCode
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
}

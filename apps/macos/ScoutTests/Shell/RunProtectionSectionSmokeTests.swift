import SwiftUI
import Testing
@testable import Scout

/// Smoke coverage for Settings ▸ Engine ▸ Run protection (card C9): the card renders with the
/// shared fixture (protection off, problems with fix buttons), with protection on and nothing
/// wrong, before any summary, and when the engine is too old. The service runs against a
/// `RuleBasedRunner`, so nothing shells out and the real switch is never touched.
@MainActor
@Suite("Run protection — smoke", .serialized)
struct RunProtectionSectionSmokeTests {
    private func service(stdout: String = "", stderr: String = "", exit: Int32 = 0) -> RunProtectionService {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["protection"], stdout: stdout, stderr: stderr, exit: exit)
        return RunProtectionService(runner: runner, environment: [:], scoutctl: { URL(fileURLWithPath: "/engine/scoutctl") })
    }

    private let now = { ISO8601DateFormatter().date(from: "2026-11-16T12:00:00Z")! }

    @Test func rendersTheSharedFixture() async throws {
        let svc = service(stdout: String(decoding: try RunProtectionTests.sharedFixture(), as: UTF8.self))
        await svc.refresh()
        #expect(svc.status != nil)
        ViewHost.render(RunProtectionSection(service: svc, vault: URL(fileURLWithPath: "/Users/alex/Scout"), now: now))
        ViewHost.render(RunProtectionSection(service: svc, vault: nil, now: now), size: CGSize(width: 800, height: 900))
    }

    @Test func rendersProtectionOn() async throws {
        let svc = service(stdout: String(decoding: try RunProtectionTests.enforcedJSON(), as: UTF8.self))
        await svc.refresh()
        #expect(svc.status?.sandbox.status == "enforced")
        ViewHost.render(RunProtectionSection(service: svc, vault: nil, now: now))
    }

    @Test func rendersBeforeAnySummaryAndForAnOldEngine() async {
        ViewHost.render(RunProtectionSection(service: service(), vault: nil, now: now))
        let old = service(stderr: "No such command 'protection'.", exit: 2)
        await old.refresh()
        #expect(old.lastError != nil)
        ViewHost.render(RunProtectionSection(service: old, vault: nil, now: now))
    }
}

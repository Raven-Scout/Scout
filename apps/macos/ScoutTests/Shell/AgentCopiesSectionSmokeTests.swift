import SwiftUI
import Testing
@testable import Scout

/// Smoke coverage for Settings ▸ Agent copies (#550): the card renders with the shared fixture,
/// after a preview, before any status, and with nothing linked (hidden). The service runs against
/// a `RuleBasedRunner`, so nothing shells out and no mirror is ever synced.
@MainActor
@Suite("Agent copies — smoke", .serialized)
struct AgentCopiesSectionSmokeTests {
    private func service(stdout: String = "", stderr: String = "", exit: Int32 = 0) -> DelegateStatusService {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["delegate", "status"], stdout: stdout, stderr: stderr, exit: exit)
        runner.on(tool: "scoutctl", prefix: ["delegate", "preview"],
                  stdout: #"{"ok": true, "files": 3, "withheld": 1, "left_out": 0, "redacted_lines": 0, "agent_config": 0}"#)
        return DelegateStatusService(runner: runner, environment: [:], scoutctl: { URL(fileURLWithPath: "/engine/scoutctl") })
    }

    private let now = { ISO8601DateFormatter().date(from: "2026-11-19T12:00:00Z")! }

    @Test func rendersTheSharedFixtureAndAPreview() async throws {
        let svc = service(stdout: String(decoding: try DelegateStatusTests.sharedFixture(), as: UTF8.self))
        await svc.refresh()
        #expect(svc.status != nil)
        ViewHost.render(AgentCopiesSection(service: svc, now: now))
        await svc.preview("grok-personal")
        #expect(svc.results["grok-personal"] != nil)
        ViewHost.render(AgentCopiesSection(service: svc, now: now), size: CGSize(width: 800, height: 900))
    }

    @Test func rendersBeforeAnyStatusAndWithNothingLinked() async {
        ViewHost.render(AgentCopiesSection(service: service(), now: now))
        let none = service(stdout: #"{"schema_version": 1, "generator": "scoutctl delegate status", "state": "none", "owner": true, "skipped": 0, "delegates": []}"#)
        await none.refresh()
        #expect(none.status?.delegates.isEmpty == true)
        ViewHost.render(AgentCopiesSection(service: none, now: now))
        let failing = service(stderr: "Traceback: boom", exit: 1)
        await failing.refresh()
        #expect(failing.status == nil && failing.lastError != nil)
        ViewHost.render(AgentCopiesSection(service: failing, now: now))   // the error-only card
    }
}

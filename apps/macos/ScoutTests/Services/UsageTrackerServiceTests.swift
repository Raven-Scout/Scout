import Testing
import Foundation
@testable import Scout

@Suite("UsageTrackerService")
struct UsageTrackerServiceTests {
    @Test func parsesSessionLinesAndFiltersRunnerDuplicates() async throws {
        let fixture = Self.fixtureURL.appendingPathComponent("usage-tracker.jsonl")
        let service = await UsageTrackerService(trackerURL: fixture, fileEvents: NoopFS())

        let entries = try await service.loadInitial()
        #expect(!entries.isEmpty, "fixture should have at least one session entry")
        #expect(entries.allSatisfy { ($0.source ?? "session") == "session" })
    }

    @Test func costLookupByTypeAndTimestamp() async throws {
        let json = """
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":4.12,"exit_code":0,"source":"session"}
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":0,"exit_code":0,"source":"runner"}
        """
        let tmp = try Self.writeTemp(json)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let service = await UsageTrackerService(trackerURL: tmp, fileEvents: NoopFS())
        _ = try await service.loadInitial()

        let target = ISO8601DateFormatter().date(from: "2026-04-19T12:03:00Z")!
        let match = await service.cost(matching: "briefing", near: target, tolerance: 120)
        #expect(match?.budgetSpent == Decimal(string: "4.12"))
        #expect(match?.source == "session")
    }

    // MARK: - helpers

    static var fixtureURL: URL {
        Bundle(for: FixtureAnchor.self).resourceURL!
    }

    static func writeTemp(_ s: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try s.write(to: url, atomically: true, encoding: .utf8)
        return url
    }
}

struct NoopFS: FileSystemEventSource {
    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { $0.finish() }
    }
}

import Testing
import Foundation
@testable import Scout

@Suite("SessionTokensService")
struct SessionTokensServiceTests {
    @Test func loadInitialParsesFixtureAndSkipsCorrupt() async throws {
        let fixture = Bundle(for: FixtureAnchor.self)
            .resourceURL!
            .appendingPathComponent("session-tokens.jsonl")
        let svc = await SessionTokensService(trackerURL: fixture, fileEvents: NoopFS())
        let entries = try await svc.loadInitial()
        #expect(entries.count == 3, "corrupt row 'not json' must be skipped silently")
        let ids = entries.map(\.sessionId)
        #expect(ids.contains("abc"))
        #expect(ids.contains("def"))
        #expect(ids.contains("ghi"))
    }

    @Test func totalsForDateIntervalFilters() async throws {
        let body = """
        {"ts":"2026-04-22T12:00:00Z","ts_et":"","session_id":"a","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":100,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.1,"num_turns":1,"duration_ms":0,"error":null}
        {"ts":"2026-04-23T12:00:00Z","ts_et":"","session_id":"b","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":200,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.2,"num_turns":1,"duration_ms":0,"error":null}
        """
        let tmp = try writeTemp(body)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let svc = await SessionTokensService(trackerURL: tmp, fileEvents: NoopFS())
        _ = try await svc.loadInitial()

        let start = ISO8601DateFormatter().date(from: "2026-04-23T00:00:00Z")!
        let end = ISO8601DateFormatter().date(from: "2026-04-24T00:00:00Z")!
        let totals = await svc.totals(in: start..<end)
        #expect(totals.inputTokens == 200)
    }

    @Test func handlesMissingFileAsEmpty() async throws {
        let missing = URL(fileURLWithPath: "/tmp/does-not-exist-\(UUID().uuidString).jsonl")
        let svc = await SessionTokensService(trackerURL: missing, fileEvents: NoopFS())
        let entries = try await svc.loadInitial()
        #expect(entries.isEmpty)
    }

    // MARK: - helpers

    private func writeTemp(_ s: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try s.write(to: url, atomically: true, encoding: .utf8)
        return url
    }
}

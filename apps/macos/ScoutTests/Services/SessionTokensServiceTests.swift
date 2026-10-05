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

    // MARK: - tolerance of real log files

    @Test("one invalid UTF-8 byte does not discard every entry in the file")
    func corruptByteDoesNotDiscardWholeFile() async throws {
        // `session-tokens.jsonl` is appended by a Stop hook while other
        // sessions run; a torn write can leave a byte that is not valid UTF-8.
        // Decoding the whole file as a String first turned that into total
        // data loss — every entry vanished, silently.
        var bytes = Data("""
        {"ts":"2026-04-22T12:00:00Z","ts_et":"","session_id":"a","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":100,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.1,"num_turns":1,"duration_ms":0,"error":null}

        """.utf8)
        bytes.append(0xFF)                      // never valid in UTF-8
        bytes.append(contentsOf: Data("\n".utf8))
        bytes.append(contentsOf: Data("""
        {"ts":"2026-04-23T12:00:00Z","ts_et":"","session_id":"b","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":200,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.2,"num_turns":1,"duration_ms":0,"error":null}

        """.utf8))

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try bytes.write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let svc = await SessionTokensService(trackerURL: url, fileEvents: NoopFS())
        let entries = try await svc.loadInitial()

        #expect(entries.count == 2, "both intact lines must survive one corrupt byte")
        #expect(entries.map(\.sessionId).sorted() == ["a", "b"])
    }

    // MARK: - watching

    @Test("an event emitted right after loadInitial() returns refreshes entries")
    @MainActor func eventRightAfterLoadInitialRefreshes() async throws {
        let tmp = try writeTemp("""
        {"ts":"2026-04-22T12:00:00Z","ts_et":"","session_id":"a","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":100,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.1,"num_turns":1,"duration_ms":0,"error":null}
        """)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let fakeFS = InjectableFS()
        let svc = SessionTokensService(trackerURL: tmp, fileEvents: fakeFS)
        let initial = try await svc.loadInitial()
        #expect(initial.count == 1)

        // No suspension between loadInitial() returning and the emit: on the
        // main actor the watch task has not run yet, so the event lands only
        // if startWatching() subscribed synchronously.
        try """
        {"ts":"2026-04-22T12:00:00Z","ts_et":"","session_id":"a","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":100,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.1,"num_turns":1,"duration_ms":0,"error":null}
        {"ts":"2026-04-23T12:00:00Z","ts_et":"","session_id":"b","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":200,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.2,"num_turns":1,"duration_ms":0,"error":null}
        """.write(to: tmp, atomically: true, encoding: .utf8)
        fakeFS.emit(FileSystemEvent(url: tmp, kind: .modified))

        await waitUntil("entries never refreshed after the event") { svc.entries.count == 2 }
    }

    // MARK: - helpers

    private func writeTemp(_ s: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try s.write(to: url, atomically: true, encoding: .utf8)
        return url
    }
}

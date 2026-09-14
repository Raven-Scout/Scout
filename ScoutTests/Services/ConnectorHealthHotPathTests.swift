import Testing
import Foundation
@testable import Scout

/// Guards the connector-health hot path found burning a core continuously:
/// every append to `connector-calls-<today>.jsonl` triggered a full re-parse
/// of *all* history, and each decoded `ts` built a fresh `ISO8601DateFormatter`
/// (an ICU formatter construction — 54% of parse time on a 29k-record corpus).
@Suite("ConnectorHealthHotPath")
struct ConnectorHealthHotPathTests {
    static func tmpDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    // MARK: - Formatter hoist must not change parsing behaviour

    @Test func parsesBothFractionalAndPlainTimestamps() throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("connector-calls-2026-09-11.jsonl")
        let lines = """
        {"ts":"2026-09-11T17:18:00Z","session_id":"s1","mode":"m","tool":"t","connector":"c","error":false}
        {"ts":"2026-09-11T17:18:01.123Z","session_id":"s2","mode":"m","tool":"t","connector":"c","error":false}
        {"ts":"2026-09-11T12:18:00-05:00","session_id":"s3","mode":"m","tool":"t","connector":"c","error":false}
        {"ts":"not-a-date","session_id":"s4","mode":"m","tool":"t","connector":"c","error":false}
        """
        try lines.write(to: url, atomically: true, encoding: .utf8)

        let calls = ConnectorCall.parseFile(at: url)
        #expect(calls.count == 3, "plain, fractional and offset timestamps parse; corrupt line is skipped")
        #expect(calls.map(\.sessionId) == ["s1", "s2", "s3"])
        // s1 and s3 denote the same instant.
        #expect(calls[0].ts == calls[2].ts)
    }

    // MARK: - Only files inside the window should be read at all

    @Test func selectsOnlyLogFilesInsideWindow() throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let cal = Calendar(identifier: .iso8601)
        let today = Date()
        // 30 daily files, today back to 29 days ago.
        for back in 0..<30 {
            let d = cal.date(byAdding: .day, value: -back, to: today)!
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            f.timeZone = TimeZone.current
            let name = "connector-calls-\(f.string(from: d)).jsonl"
            try "".write(to: dir.appendingPathComponent(name),
                         atomically: true, encoding: .utf8)
        }
        // Noise that must never be picked up.
        try "".write(to: dir.appendingPathComponent("connector-alerts.log"),
                     atomically: true, encoding: .utf8)
        try "".write(to: dir.appendingPathComponent("connector-calls-garbage.jsonl"),
                     atomically: true, encoding: .utf8)

        let windowStart = today.addingTimeInterval(-14 * 24 * 3600)
        let picked = ConnectorCall.logURLs(in: dir, since: windowStart)

        // 14-day window plus a grace day for local-midnight naming: the dated
        // files inside it, never all 30.
        let dated = picked.filter { $0.lastPathComponent != "connector-calls-garbage.jsonl" }
        #expect(dated.count <= 16, "window must exclude old history, got \(dated.count)")
        #expect(dated.count >= 14)
        #expect(picked.allSatisfy { $0.lastPathComponent.hasPrefix("connector-calls-") })
        // An undated stem is never *proof* the rows are old, so it is still
        // read — a hook rename must not silently empty the health matrix.
        #expect(picked.contains { $0.lastPathComponent == "connector-calls-garbage.jsonl" },
                "undated file must still be read")
        #expect(!picked.contains { $0.lastPathComponent == "connector-alerts.log" })
    }

    @Test func windowSelectionKeepsUndatedNothingAndHandlesEmptyDir() throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(ConnectorCall.logURLs(in: dir, since: Date()).isEmpty)
    }

    // MARK: - The watcher must coalesce append storms

    @MainActor
    @Test func watcherCoalescesAppendBursts() async throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("connector-calls-2026-09-11.jsonl")
        try "".write(to: url, atomically: true, encoding: .utf8)

        let fs = InjectableFS()
        let service = ConnectorHealthService(
            logsDirectory: dir,
            ackStoreURL: dir.appendingPathComponent("ack.json"),
            fileEvents: fs,
            connectors: ["c"]
        )
        try await service.loadInitial()
        let before = service.refreshCount

        // A burst the way a live Scout run appends.
        for _ in 0..<50 { fs.emit(FileSystemEvent(url: url, kind: .modified)) }

        // Poll rather than sleep a fixed span: under full-suite parallelism a
        // fixed wait is a coin flip, and a flaky perf guard is worse than none.
        let deadline = Date().addingTimeInterval(10)
        while service.refreshCount == before, Date() < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        #expect(service.refreshCount > before, "the burst must still produce an update")

        // Let any further coalesced flushes land before bounding the count.
        try await Task.sleep(nanoseconds: 750_000_000)
        let added = service.refreshCount - before
        #expect(added <= 3, "50 appends must coalesce into a couple of refreshes, got \(added)")
    }
}

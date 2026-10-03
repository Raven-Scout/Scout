import Testing
import Foundation
@testable import Scout

@Suite("ConnectorCallCache")
@MainActor
struct ConnectorCallCacheTests {
    static func tmpDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func write(_ text: String, _ name: String, in dir: URL) throws -> URL {
        let url = dir.appendingPathComponent(name)
        try text.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    static func line(_ session: String) -> String {
        """
        {"ts":"2026-09-11T17:18:00Z","session_id":"\(session)","mode":"m","tool":"t","connector":"c","error":false}
        """
    }

    @Test func parsesEachFileOnceThenServesFromCache() async throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let a = try Self.write(Self.line("a"), "connector-calls-2026-09-10.jsonl", in: dir)
        let b = try Self.write(Self.line("b"), "connector-calls-2026-09-11.jsonl", in: dir)

        var cache = ConnectorCallCache()
        var parsed: [URL] = []
        let parse: (Set<URL>) async -> [URL: [ConnectorCall]] = { urls in
            parsed.append(contentsOf: urls)
            return urls.reduce(into: [:]) { $0[$1] = ConnectorCall.parseFile(at: $1) }
        }

        let first = await cache.calls(for: [a, b], parse: parse)
        #expect(first.count == 2)
        #expect(parsed.count == 2, "cold cache parses both files")

        parsed.removeAll()
        let second = await cache.calls(for: [a, b], parse: parse)
        #expect(second.count == 2)
        #expect(parsed.isEmpty, "unchanged files must not be re-parsed")
    }

    @Test func reparsesOnlyTheChangedFile() async throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let a = try Self.write(Self.line("a"), "connector-calls-2026-09-10.jsonl", in: dir)
        let b = try Self.write(Self.line("b"), "connector-calls-2026-09-11.jsonl", in: dir)

        var cache = ConnectorCallCache()
        var parsed: [URL] = []
        let parse: (Set<URL>) async -> [URL: [ConnectorCall]] = { urls in
            parsed.append(contentsOf: urls)
            return urls.reduce(into: [:]) { $0[$1] = ConnectorCall.parseFile(at: $1) }
        }
        _ = await cache.calls(for: [a, b], parse: parse)
        parsed.removeAll()

        // Today's file grows, the way a live run appends to it.
        try (Self.line("b") + "\n" + Self.line("b2"))
            .write(to: b, atomically: true, encoding: .utf8)

        let out = await cache.calls(for: [a, b], parse: parse)
        #expect(parsed == [b], "only the changed file re-parses, got \(parsed.map(\.lastPathComponent))")
        #expect(out.count == 3)
    }

    @Test func dropsEntriesForFilesNoLongerRequested() async throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let a = try Self.write(Self.line("a"), "connector-calls-2026-09-10.jsonl", in: dir)
        let b = try Self.write(Self.line("b"), "connector-calls-2026-09-11.jsonl", in: dir)

        var cache = ConnectorCallCache()
        let parse: (Set<URL>) async -> [URL: [ConnectorCall]] = { urls in
            urls.reduce(into: [:]) { $0[$1] = ConnectorCall.parseFile(at: $1) }
        }
        _ = await cache.calls(for: [a, b], parse: parse)
        // `a` falls out of the window.
        _ = await cache.calls(for: [b], parse: parse)
        #expect(cache.cachedFileCount == 1, "evicts files that rolled out of the window")
    }

    @Test func missingFileYieldsNoCallsAndNoCacheEntry() async throws {
        let dir = try Self.tmpDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let gone = dir.appendingPathComponent("connector-calls-2026-01-01.jsonl")

        var cache = ConnectorCallCache()
        let out = await cache.calls(for: [gone]) { urls in
            urls.reduce(into: [:]) { $0[$1] = ConnectorCall.parseFile(at: $1) }
        }
        #expect(out.isEmpty)
        #expect(cache.cachedFileCount == 0)
    }
}

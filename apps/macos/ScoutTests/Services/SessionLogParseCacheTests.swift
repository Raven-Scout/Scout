import Testing
import Foundation
@testable import Scout

@Suite("SessionLogService parse cache")
struct SessionLogParseCacheTests {

    private func meta(_ name: String, size: Int64 = 100, mtime: TimeInterval = 1000)
        -> SessionLogService.LogFileMeta {
        SessionLogService.LogFileMeta(
            url: URL(fileURLWithPath: "/logs/\(name)"),
            name: name,
            sizeBytes: size,
            modifiedAt: Date(timeIntervalSince1970: mtime)
        )
    }

    private func body(_ exit: Int?) -> SessionLogService.ParsedBody {
        SessionLogService.ParsedBody(
            endedAt: nil, exitCode: exit, status: .success,
            logSizeBytes: 100, errorsDetected: []
        )
    }

    @Test func emptyCache_parsesEveryFile() {
        let files = [meta("a.log"), meta("b.log"), meta("c.log")]
        var calls: [String] = []
        let result = SessionLogService.resolveCachedBodies(
            files: files, cache: SessionLogService.ParseCache()
        ) { m in calls.append(m.name); return self.body(0) }

        #expect(calls.sorted() == ["a.log", "b.log", "c.log"])
        #expect(result.bodies.count == 3)
        #expect(result.updatedCache.entries.count == 3)
        #expect(result.parsedNames.sorted() == ["a.log", "b.log", "c.log"])
    }

    @Test func validCache_parsesNothing() {
        let files = [meta("a.log"), meta("b.log")]
        let seeded = SessionLogService.resolveCachedBodies(files: files, cache: .init()) {
            _ in self.body(0)
        }.updatedCache

        var calls: [String] = []
        let result = SessionLogService.resolveCachedBodies(files: files, cache: seeded) { m in
            calls.append(m.name); return self.body(0)
        }
        #expect(calls.isEmpty)          // the whole point: no re-parsing of unchanged logs
        #expect(result.bodies.count == 2)
        #expect(result.parsedNames.isEmpty)
    }

    @Test func changedFile_reparsesOnlyThatFile() {
        let files = [meta("a.log", size: 100), meta("b.log", size: 100)]
        let seeded = SessionLogService.resolveCachedBodies(files: files, cache: .init()) {
            _ in self.body(0)
        }.updatedCache

        // b.log grew (size changed) → its cache entry is stale; a.log unchanged.
        let changed = [meta("a.log", size: 100), meta("b.log", size: 250)]
        var calls: [String] = []
        _ = SessionLogService.resolveCachedBodies(files: changed, cache: seeded) { m in
            calls.append(m.name); return self.body(0)
        }
        #expect(calls == ["b.log"])
    }

    @Test func changedModificationDate_reparses() {
        let original = [meta("a.log", mtime: 1000)]
        let seeded = SessionLogService.resolveCachedBodies(files: original, cache: .init()) {
            _ in self.body(0)
        }.updatedCache

        let touched = [meta("a.log", mtime: 2000)]   // same size, newer mtime
        var calls: [String] = []
        _ = SessionLogService.resolveCachedBodies(files: touched, cache: seeded) { m in
            calls.append(m.name); return self.body(0)
        }
        #expect(calls == ["a.log"])
    }

    @Test func removedFile_droppedFromCache() {
        let seeded = SessionLogService.resolveCachedBodies(
            files: [meta("a.log"), meta("gone.log")], cache: .init()
        ) { _ in self.body(0) }.updatedCache
        #expect(seeded.entries["gone.log"] != nil)

        // Next launch: gone.log no longer on disk → must not linger in the cache.
        let result = SessionLogService.resolveCachedBodies(
            files: [meta("a.log")], cache: seeded
        ) { _ in self.body(0) }
        #expect(result.updatedCache.entries["gone.log"] == nil)
        #expect(result.updatedCache.entries.count == 1)
    }

    @Test func versionMismatch_invalidatesEntireCache() {
        let files = [meta("a.log")]
        var stale = SessionLogService.resolveCachedBodies(files: files, cache: .init()) {
            _ in self.body(0)
        }.updatedCache
        stale.version = SessionLogService.ParseCache.version - 1   // simulate an old schema

        var calls: [String] = []
        _ = SessionLogService.resolveCachedBodies(files: files, cache: stale) { m in
            calls.append(m.name); return self.body(0)
        }
        #expect(calls == ["a.log"])   // forced re-parse, ignoring stale-schema entries
    }

    @Test func cachedBodyValueIsReturnedUnchanged() {
        let files = [meta("a.log")]
        let distinct = SessionLogService.ParsedBody(
            endedAt: Date(timeIntervalSince1970: 5), exitCode: 42, status: .failure,
            logSizeBytes: 999, errorsDetected: [DetectedError(line: 3, pattern: "p", snippet: "s")]
        )
        let seeded = SessionLogService.resolveCachedBodies(files: files, cache: .init()) {
            _ in distinct
        }.updatedCache

        let result = SessionLogService.resolveCachedBodies(files: files, cache: seeded) {
            _ in self.body(0)   // must NOT be used — cache hit
        }
        #expect(result.bodies["a.log"] == distinct)
    }

    @Test func parseCache_roundTripsThroughJSON() throws {
        let original = SessionLogService.resolveCachedBodies(
            files: [meta("a.log"), meta("b.log")], cache: .init()
        ) { _ in self.body(0) }.updatedCache

        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(SessionLogService.ParseCache.self, from: data)
        #expect(decoded == original)
    }
}

import Foundation
import Testing
@testable import Scout

/// The progress watcher's line (`RunProgress`) and how `SessionLogService` reads it (review of #701).
///
/// `run-progress.alive` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/run-progress.alive` (root CLAUDE.md; `contract.yml` compares
/// them). When it is regenerated, update the pins in `parsesTheSharedFixture`.
@MainActor
@Suite("RunProgress — the progress watcher's line")
struct RunProgressTests {

    private final class FixtureAnchor {}
    private static let ny = TimeZone(identifier: "America/New_York")!

    @Test("parses the engine's shared fixture")
    func parsesTheSharedFixture() throws {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "run-progress", withExtension: "alive"))
        let progress = try #require(RunProgress.parse(try String(contentsOf: url, encoding: .utf8)))
        #expect(progress.runnerPID == 4242)
        #expect(progress.progressAt == Date(timeIntervalSince1970: 1_763_550_000))
        #expect(progress.stalled == 1500)
        #expect(progress.freshFor == 210)
        // The log is the last field and may hold spaces.
        #expect(progress.logPath == "/Users/alex/My Vault/.scout-logs/scout-2026-11-19_14-00.log")
        #expect(progress.names(URL(fileURLWithPath: "/Users/alex/Scout/.scout-logs/scout-2026-11-19_14-00.log")))
        #expect(!progress.names(URL(fileURLWithPath: "/Users/alex/Scout/.scout-logs/scout-2026-11-19_15-00.log")))
    }

    @Test("anything but the line is unreadable", arguments: [
        "",
        "2 4242 1 0 210 /log\n",           // another version
        "1 4242 1 0 210\n",                // no log
        "1 0 1 0 210 /log\n",              // no runner
        "1 4242 1 0 0 /log\n",             // never fresh
        "1 4242 x 0 210 /log\n",
        "1 4242 1 -5 210 /log\n",
        "1 4242 1 ٣ 210 /log\n",           // a non-ASCII digit
        "1 4242 1 0 210 /log\nsecond\n",
    ])
    func rejectsMalformedLines(_ text: String) {
        #expect(RunProgress.parse(text) == nil)
    }

    @Test("a line is trusted only while its watcher samples")
    func freshness() throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let line = dir.appendingPathComponent(RunProgress.fileName)
        try "1 4242 1763550000 0 210 /v/.scout-logs/scout-2026-04-20_11-00.log\n"
            .write(to: line, atomically: true, encoding: .utf8)
        let sampled = Self.et(day: 20, hour: 11, minute: 30)
        try pinLogModified(line, to: sampled)
        #expect(RunProgress.readFresh(logsDirectory: dir, now: sampled.addingTimeInterval(210)) != nil)
        #expect(RunProgress.readFresh(logsDirectory: dir, now: sampled.addingTimeInterval(211)) == nil)
        try FileManager.default.removeItem(at: line)
        #expect(RunProgress.readFresh(logsDirectory: dir, now: sampled) == nil)
    }

    // MARK: - SessionLogService: the watcher's stall, not the log, decides

    @Test("a hung claude is orphaned although the keepalive wrote its log a minute ago")
    func promoteOrphan_watcherStallBeatsARecentLog() {
        let now = Self.et(day: 20, hour: 11, minute: 45)
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: Self.et(day: 20, hour: 11, minute: 0),
            lastActivityAt: now.addingTimeInterval(-60),
            watcherStall: 25 * 60, type: .consolidation, now: now) == .orphaned)   // cutoff 20 min
    }

    @Test("a slow but working run is running although nothing has written its log for 40 minutes")
    func promoteOrphan_watcherStallBeatsAQuietLog() {
        let now = Self.et(day: 20, hour: 11, minute: 45)
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: Self.et(day: 20, hour: 11, minute: 0),
            lastActivityAt: now.addingTimeInterval(-40 * 60),
            watcherStall: 60, type: .consolidation, now: now) == .running)
        // A terminal run is never touched, whatever the watcher said.
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .success, startedAt: Self.et(day: 20, hour: 11, minute: 0),
            watcherStall: 99 * 60, type: .consolidation, now: now) == .success)
    }

    @Test("a hung claude whose log the keepalive keeps writing is Didn't finish")
    func loadInitial_hungClaudeBehindLiveShell() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let log = try writeRunningLog(in: dir, keepalives: 6)
        try pinLogModified(log, to: Self.et(day: 20, hour: 11, minute: 44))
        // 45 min in, the keepalive wrote a minute ago, but claude has done nothing for 30 min.
        try writeLine(in: dir, stalledMinutes: 30, log: log, sampledAt: Self.et(day: 20, hour: 11, minute: 44))
        let service = try await makeService(in: dir, now: Self.et(day: 20, hour: 11, minute: 45))
        #expect(try await service.loadInitial().first?.status == .orphaned)
    }

    @Test("a slow but working run is Running however long its log is quiet")
    func sweep_slowButAliveStaysRunning() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let log = try writeRunningLog(in: dir, keepalives: 0)
        try pinLogModified(log, to: Self.et(day: 20, hour: 11, minute: 0))   // nothing since the start
        try writeLine(in: dir, stalledMinutes: 2, log: log, sampledAt: Self.et(day: 20, hour: 11, minute: 39))
        let clock = SteppableClock(date: Self.et(day: 20, hour: 11, minute: 40))
        let service = try await makeService(in: dir, clock: clock)
        _ = try await service.loadInitial()
        #expect(service.runs.first?.status == .running)

        // The watcher samples on; claude keeps starting tools, so the stall stays low.
        try writeLine(in: dir, stalledMinutes: 1, log: log, sampledAt: Self.et(day: 20, hour: 11, minute: 59))
        clock.date = Self.et(day: 20, hour: 12, minute: 0)
        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == .running)

        // Then it hangs: the stall passes the cutoff, and the sweep says so.
        try writeLine(in: dir, stalledMinutes: 21, log: log, sampledAt: Self.et(day: 20, hour: 12, minute: 20))
        clock.date = Self.et(day: 20, hour: 12, minute: 20)
        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == .orphaned)
    }

    @Test("a dead watcher's line, or another run's, falls back to the log (#701)")
    func loadInitial_staleOrForeignLineFallsBack() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let log = try writeRunningLog(in: dir, keepalives: 4)
        try pinLogModified(log, to: Self.et(day: 20, hour: 11, minute: 20))
        let now = Self.et(day: 20, hour: 11, minute: 23)
        // A crashed runner's watcher stopped sampling 10 minutes ago: not fresh.
        try writeLine(in: dir, stalledMinutes: 30, log: log, sampledAt: now.addingTimeInterval(-600))
        #expect(try await makeService(in: dir, now: now).loadInitial().first?.status == .running)

        // A fresh line about another run says nothing about this one.
        let other = dir.appendingPathComponent("scout-2026-04-20_09-00.log")
        try writeLine(in: dir, stalledMinutes: 30, log: other, sampledAt: now)
        #expect(try await makeService(in: dir, now: now).loadInitial().first?.status == .running)
    }

    // MARK: - helpers

    private func makeTempDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("run-progress-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// A consolidation started 11:00 with no terminal marker, and `keepalives` lease keepalive lines.
    private func writeRunningLog(in dir: URL, keepalives: Int) throws -> URL {
        let log = dir.appendingPathComponent("scout-2026-04-20_11-00.log")
        var text = "=== Scout run starting at Mon Apr 20 11:00:29 EDT 2026 ===\n"
        for i in 0..<keepalives {
            text += "lease keepalive: renewed run scout-morning-consolidation-1's lease until \(i)\n"
        }
        try text.write(to: log, atomically: true, encoding: .utf8)
        return log
    }

    private func writeLine(in dir: URL, stalledMinutes: Int, log: URL, sampledAt: Date) throws {
        let line = dir.appendingPathComponent(RunProgress.fileName)
        try "1 4242 1763550000 \(stalledMinutes * 60) 210 \(log.path)\n".write(to: line, atomically: true, encoding: .utf8)
        try pinLogModified(line, to: sampledAt)
    }

    private func makeService(in dir: URL, now: Date) async throws -> SessionLogService {
        try await makeService(in: dir, clock: FixedClock(date: now))
    }

    private func makeService(in dir: URL, clock: any ClockSource) async throws -> SessionLogService {
        let trackerURL = dir.appendingPathComponent("usage-tracker.jsonl")
        if !FileManager.default.fileExists(atPath: trackerURL.path) {
            try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        }
        let tracker = UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()
        return SessionLogService(
            logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(), clock: clock,
            timeZone: Self.ny, parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
    }

    /// April 2026 in America/New_York (EDT).
    private static func et(day: Int, hour: Int, minute: Int) -> Date {
        var comps = DateComponents()
        comps.year = 2026; comps.month = 4; comps.day = day
        comps.hour = hour; comps.minute = minute
        comps.timeZone = ny
        return Calendar(identifier: .gregorian).date(from: comps)!
    }
}

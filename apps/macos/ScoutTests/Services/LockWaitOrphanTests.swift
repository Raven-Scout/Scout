import Foundation
import Testing
@testable import Scout

/// A run queued behind another one for the session lock is waiting, not
/// "Didn't finish". `scout_lock_acquire` (plugin/templates/scripts/scout-lock.sh.tmpl)
/// writes one "waiting up to Ns for the lock" line, then sleeps in 30 s polls
/// for up to `SCOUT_LOCK_WAIT_SECS` (1800 s) writing nothing, and the progress
/// watcher starts only once the run holds the lock. So the app gives the wait
/// in full, from that line, and the kind's threshold only after it. Synthetic
/// data only: invented times, PIDs and log names.
@MainActor
@Suite("Lock wait is not Didn't finish")
struct LockWaitOrphanTests {

    private static let ny = TimeZone(identifier: "America/New_York")!

    private static func et(day: Int, hour: Int, minute: Int, second: Int = 0) -> Date {
        var c = DateComponents()
        c.year = 2026; c.month = 4; c.day = day; c.hour = hour; c.minute = minute; c.second = second
        c.timeZone = ny
        return Calendar(identifier: .gregorian).date(from: c)!
    }

    private static let waitLine =
        "=== Another Scout session running (PID 40001) — waiting up to 1800s for the lock ===\n"

    private func makeTempDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("lock-wait-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private func parse(_ text: String, name: String = "scout-2026-04-20_14-05.log") throws
        -> SessionLogService.ParsedBody {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent(name)
        try text.write(to: url, atomically: true, encoding: .utf8)
        let filename = try #require(SessionLogService.parseFilename(url, timeZone: Self.ny))
        return try SessionLogService.parseBody(at: url, filename: filename)
    }

    // MARK: - Reading the log

    @Test func waitLineAloneIsAWait() throws {
        let body = try parse(Self.waitLine)
        #expect(body.status == .running)
        #expect(body.lockWaitSecs == 1800)
    }

    @Test func waitLineCarriesTheConfiguredWait() throws {
        let body = try parse("=== Another Scout session running (PID 40001) — waiting up to 600s for the lock ===\n")
        #expect(body.lockWaitSecs == 600)
    }

    @Test func aCustomInstanceNameStillReads() throws {
        let body = try parse("=== Another SCOUT-LAB session running (PID unknown) — waiting up to 1800s for the lock ===\n")
        #expect(body.lockWaitSecs == 1800)
    }

    @Test func theLockAcquiredLineEndsTheWait() throws {
        let body = try parse(Self.waitLine + """
        === Lock acquired after 412s wait ===
        === Scout run starting at Mon Apr 20 14:12:00 EDT 2026 ===

        """)
        #expect(body.status == .running)
        #expect(body.lockWaitSecs == nil)
    }

    @Test func aRunThatGaveUpIsASkipNotAWait() throws {
        let body = try parse(Self.waitLine
            + "=== Another Scout session running (PID 40001) — skipping after a 1800s wait ===\n")
        #expect(body.status == .skippedConcurrency)
        #expect(body.lockWaitSecs == nil)
    }

    @Test func aLogWithoutTheLineIsNoWait() throws {
        let body = try parse("=== Scout run starting at Mon Apr 20 14:05:01 EDT 2026 ===\n")
        #expect(body.lockWaitSecs == nil)
    }

    @Test func theParseCacheRoundTripsTheWait() throws {
        let body = try parse(Self.waitLine)
        let data = try JSONEncoder().encode(body)
        #expect(try JSONDecoder().decode(SessionLogService.ParsedBody.self, from: data) == body)
        // Caches written before the field are dropped, not read as "no wait".
        #expect(SessionLogService.ParseCache.version == 4)
    }

    // MARK: - Every kind

    /// Queued 29 minutes behind the lock, every kind is still waiting, even
    /// those whose own threshold (consolidation 20 min, briefing 30) is shorter
    /// than the wait. Silent past the wait plus its threshold, it didn't finish.
    @Test(arguments: RunType.allCases)
    func queuedBehindTheLock(type: RunType) {
        let lockWait: TimeInterval = 1800
        let start = Self.et(day: 20, hour: 14, minute: 5)
        let waitingLine = start.addingTimeInterval(1)   // the log's only line while it waits
        func status(afterSeconds s: TimeInterval) -> RunStatus {
            SessionLogService.promoteOrphan(
                parsedStatus: .running, startedAt: start, lastActivityAt: waitingLine,
                lockWait: lockWait, type: type, now: waitingLine.addingTimeInterval(s))
        }
        #expect(status(afterSeconds: 29 * 60) == .running, "29 min into the wait")
        #expect(status(afterSeconds: lockWait + type.orphanAfter - 60) == .running,
                "the wait, then a minute short of the threshold")
        #expect(status(afterSeconds: lockWait + type.orphanAfter + 60) == .orphaned,
                "silent past the wait plus the threshold")
    }

    /// Without the wait line the rule is unchanged: a consolidation silent for
    /// 21 minutes is "Didn't finish". This is what a waiting run used to get.
    @Test func withoutAWaitTheThresholdAloneApplies() {
        let start = Self.et(day: 20, hour: 14, minute: 5)
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: start, lastActivityAt: start,
            type: .consolidation, now: start.addingTimeInterval(21 * 60)) == .orphaned)
    }

    /// The wait is counted from the waiting line, never from the start.
    @Test func theWaitCountsFromTheWaitingLine() {
        let start = Self.et(day: 20, hour: 14, minute: 5)
        let waitingLine = start.addingTimeInterval(3 * 60)   // a slow pre-lock step
        let now = waitingLine.addingTimeInterval(1800 + RunType.consolidation.orphanAfter - 30)
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: start, lastActivityAt: waitingLine,
            lockWait: 1800, type: .consolidation, now: now) == .running)
    }

    /// A finished or skipped run is never changed by a wait.
    @Test func aWaitNeverChangesATerminalStatus() {
        let start = Self.et(day: 20, hour: 14, minute: 5)
        for status in [RunStatus.success, .skippedConcurrency, .failure] {
            #expect(SessionLogService.promoteOrphan(
                parsedStatus: status, startedAt: start, lockWait: 1800,
                type: .consolidation, now: start.addingTimeInterval(9 * 3600)) == status)
        }
    }

    // MARK: - The run list

    /// A consolidation queued behind a long dreaming run, read from disk: it
    /// shows "Waiting for another run" at 29 minutes, through the sweep too, and
    /// "Didn't finish" only once the wait and its 20 minutes have both passed.
    @Test func consolidationQueuedBehindDreamingIsWaiting() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let dreaming = dir.appendingPathComponent("dreaming-2026-04-20_13-00.log")
        try "=== Scout Dreaming run starting at Mon Apr 20 13:00:02 EDT 2026 ===\n"
            .write(to: dreaming, atomically: true, encoding: .utf8)
        let queued = dir.appendingPathComponent("scout-2026-04-20_14-05.log")
        try Self.waitLine.write(to: queued, atomically: true, encoding: .utf8)
        let waitingLine = Self.et(day: 20, hour: 14, minute: 5, second: 1)
        try pinLogModified(queued, to: waitingLine)
        try pinLogModified(dreaming, to: Self.et(day: 20, hour: 14, minute: 30))

        let trackerURL = dir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()
        let clock = SteppableClock(date: waitingLine.addingTimeInterval(29 * 60))
        let service = SessionLogService(
            logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
            clock: clock, timeZone: Self.ny, parseCacheURL: sandboxParseCacheURL(in: dir))

        let runs = try await service.loadInitial()
        let waiting = try #require(runs.first { $0.logPath.lastPathComponent == queued.lastPathComponent })
        #expect(waiting.type == .consolidation)
        #expect(waiting.status == .running)
        #expect(waiting.isWaitingForLock)
        #expect(waiting.statusDisplayName == "Waiting for another run")
        #expect(waiting.statusReason?.isEmpty == false)

        // 49 min after the line: past the 30-min wait, inside the 20 min after it.
        clock.date = waitingLine.addingTimeInterval(49 * 60)
        service.sweepStaleStatuses()
        #expect(service.runs.first { $0.logPath.lastPathComponent == queued.lastPathComponent }?.status == .running)

        // 51 min: the waiter would have written "Lock acquired" or "skipping" by now.
        clock.date = waitingLine.addingTimeInterval(51 * 60)
        service.sweepStaleStatuses()
        let gone = try #require(service.runs.first { $0.logPath.lastPathComponent == queued.lastPathComponent })
        #expect(gone.status == .orphaned)
        #expect(!gone.isWaitingForLock)
        #expect(gone.statusDisplayName == "Didn't finish")
    }

    // MARK: - Plain language

    @Test func waitingReadsAsPlainWords() {
        let run = Run.make(type: .consolidation, status: .running, exitCode: nil).withLockWait(1800)
        #expect(run.statusDisplayName == "Waiting for another run")
        #expect(Run.make(type: .consolidation, status: .running, exitCode: nil).statusDisplayName == "Running")
        // `with(status:)` keeps the wait, and only a running run reads as waiting.
        #expect(run.with(status: .running).lockWait == 1800)
        #expect(run.with(status: .orphaned).statusDisplayName == "Didn't finish")
    }
}

private extension Run {
    func withLockWait(_ secs: TimeInterval) -> Run {
        var copy = self
        copy.lockWait = secs
        return copy
    }
}

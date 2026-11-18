import Foundation
import Testing
@testable import Scout

/// Covers the pieces of `SessionLogService` that the file-driven tests don't
/// reach: on-demand commit resolution, the periodic stale sweep, and the
/// per-run-type commit-subject prefixes it keys on.
@MainActor
@Suite("SessionLogService — stale sweep & commits")
struct SessionLogStaleSweepTests {

    private static let ny = TimeZone(identifier: "America/New_York")!

    private func makeTempDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("session-log-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private func makeTracker(in dir: URL) async throws -> UsageTrackerService {
        let trackerURL = dir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()
        return tracker
    }

    @Test("cost-tracker keys stay aligned with the run families")
    func costTrackerKey_perRunType() {
        #expect(RunType.morningBriefing.costTrackerKey == "briefing")
        #expect(RunType.weekendBriefing.costTrackerKey == "briefing")
        #expect(RunType.consolidation.costTrackerKey == "consolidation")
        #expect(RunType.dreaming.costTrackerKey == "dreaming")
        #expect(RunType.research.costTrackerKey == "research")
        #expect(RunType.manual.costTrackerKey == "manual")
    }

    @Test("orphan cutoffs are ordered short-run → long-run")
    func orphanAfter_perRunType() {
        #expect(RunType.consolidation.orphanAfter == 20 * 60)
        #expect(RunType.morningBriefing.orphanAfter == 30 * 60)
        #expect(RunType.weekendBriefing.orphanAfter == 30 * 60)
        #expect(RunType.manual.orphanAfter == 45 * 60)
        #expect(RunType.dreaming.orphanAfter == 2 * 3600)
        #expect(RunType.research.orphanAfter == 2 * 3600)
        // Consolidation is the tightest, the long-form types the loosest.
        #expect(RunType.consolidation.orphanAfter < RunType.morningBriefing.orphanAfter)
        #expect(RunType.manual.orphanAfter < RunType.dreaming.orphanAfter)
    }

    // MARK: - commits(for:)

    @Test("with no git service, commits resolve to empty")
    func commits_withoutGitServiceIsEmpty() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: nil, fileEvents: NoopFS(), timeZone: Self.ny)

        #expect(await service.commits(for: Run.make()).isEmpty)
    }

    @Test("commits query the run's window, padded 30s back and 5min forward")
    func commits_queriesPaddedWindowWithTypePrefix() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let sep = "\u{1E}"
        let logOut = ["sha1", "sha1", "1781600000", "dreaming: synthesis"]
            .joined(separator: sep)
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(logOut.utf8), stderr: Data()),
        ])
        let git = GitService(repoURL: dir, runner: runner)
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: git, fileEvents: NoopFS(), timeZone: Self.ny)

        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let run = Run.make(type: .dreaming, startedAt: started,
                           endedAt: started.addingTimeInterval(600))
        let commits = await service.commits(for: run)

        #expect(commits.map(\.id) == ["sha1"])
        let args = try #require(runner.calls.first?.arguments)
        let iso = ISO8601DateFormatter()
        #expect(args.contains("--since=\(iso.string(from: started.addingTimeInterval(-30)))"))
        #expect(args.contains(
            "--until=\(iso.string(from: started.addingTimeInterval(600 + 300)))"))
    }

    @Test("a still-running run bounds the window with the clock's now")
    func commits_openEndedRunUsesClockNow() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let now = Date(timeIntervalSince1970: 1_781_610_000)
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: GitService(repoURL: dir, runner: runner),
            fileEvents: NoopFS(), clock: FixedClock(date: now), timeZone: Self.ny)

        let run = Run.make(type: .research,
                           startedAt: Date(timeIntervalSince1970: 1_781_600_000),
                           endedAt: nil, status: .running)
        _ = await service.commits(for: run)

        let iso = ISO8601DateFormatter()
        let args = try #require(runner.calls.first?.arguments)
        #expect(args.contains("--until=\(iso.string(from: now.addingTimeInterval(300)))"))
    }

    @Test("a weekend run's commits are found even though their subject says 'weekend briefing'")
    func commits_weekendBriefingSubjectsAreClaimed() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let sep = "\u{1E}"
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let ts = String(Int(started.timeIntervalSince1970) + 60)
        let logOut = [
            ["wb1", "wb1", ts, "weekend briefing [2026-04-18]: nothing new on the example board"].joined(separator: sep),
            ["dr1", "dr1", ts, "dreaming [08:0x]: unrelated"].joined(separator: sep),
        ].joined(separator: "\n")
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(logOut.utf8), stderr: Data()),
        ])
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: GitService(repoURL: dir, runner: runner),
            fileEvents: NoopFS(), timeZone: Self.ny)

        let run = Run.make(type: .weekendBriefing, startedAt: started,
                           endedAt: started.addingTimeInterval(600))
        #expect(await service.commits(for: run).map(\.id) == ["wb1"])
    }

    @Test("a git failure degrades to no commits rather than throwing")
    func commits_gitFailureYieldsEmpty() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 128, stdout: Data(), stderr: Data("not a repo".utf8)),
        ])
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: GitService(repoURL: dir, runner: runner),
            fileEvents: NoopFS(), timeZone: Self.ny)

        #expect(await service.commits(for: Run.make()).isEmpty)
    }

    // MARK: - sweepStaleStatuses

    @Test("the sweep demotes a running run once its cutoff has passed")
    func sweep_demotesRunningPastCutoff() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        // Consolidation started 22:05 ET; its cutoff is 20 minutes.
        try "=== SCOUT Consolidation run starting at Mon Apr 20 22:05:01 EDT 2026 ===\n"
            .write(to: dir.appendingPathComponent("scout-2026-04-20_22-05.log"),
                   atomically: true, encoding: .utf8)

        // Load with a clock 5 minutes in — still legitimately running.
        let fresh = FixedClock(date: Self.et(day: 20, hour: 22, minute: 10))
        // `loadInitial` rewrites the parse cache from this directory's
        // fixtures, so it must never resolve to the real per-user cache.
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(), clock: fresh, timeZone: Self.ny,
            parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        let runs = try await service.loadInitial()
        #expect(runs.first?.status == .running)

        // The sweep with the same clock changes nothing.
        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == .running)
    }

    @Test("a run past its cutoff at load time is already orphaned, and the sweep is a no-op")
    func sweep_isIdempotentOnAlreadyOrphanedRuns() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try "=== SCOUT Consolidation run starting at Mon Apr 20 22:05:01 EDT 2026 ===\n"
            .write(to: dir.appendingPathComponent("scout-2026-04-20_22-05.log"),
                   atomically: true, encoding: .utf8)

        try Self.setLogModified(dir.appendingPathComponent("scout-2026-04-20_22-05.log"),
                                to: Self.et(day: 20, hour: 22, minute: 5))

        // 90 minutes later — well past consolidation's 20-minute cutoff.
        let stale = FixedClock(date: Self.et(day: 20, hour: 23, minute: 35))
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(), clock: stale, timeZone: Self.ny,
            parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        let runs = try await service.loadInitial()
        #expect(runs.first?.status == .orphaned)

        let before = service.runs
        service.sweepStaleStatuses()
        #expect(service.runs == before)     // no needless republish
    }

    @Test("sweeping an empty snapshot is safe")
    func sweep_emptySnapshotIsSafe() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(), timeZone: Self.ny,
            parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        _ = try await service.loadInitial()
        #expect(service.runs.isEmpty)
        service.sweepStaleStatuses()
        #expect(service.runs.isEmpty)
    }

    @Test("a terminal run is never touched by the sweep")
    func sweep_leavesTerminalRunsAlone() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try """
        === SCOUT Consolidation run starting at Mon Apr 20 22:05:01 EDT 2026 ===
        === SCOUT run finished at Mon Apr 20 22:09:00 EDT 2026 (exit 0) ===
        """.write(to: dir.appendingPathComponent("scout-2026-04-20_22-05.log"),
                  atomically: true, encoding: .utf8)
        try Self.setLogModified(dir.appendingPathComponent("scout-2026-04-20_22-05.log"),
                                to: Self.et(day: 20, hour: 22, minute: 9))

        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(),
            clock: FixedClock(date: Self.et(day: 22, hour: 12, minute: 0)),   // 2 days later
            timeZone: Self.ny,
            parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        let runs = try await service.loadInitial()
        let statusBefore = runs.first?.status
        #expect(statusBefore != .running)

        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == statusBefore)
    }

    // MARK: - Liveness: a run that is still writing its log is not orphaned

    @Test("a long run whose log was written recently stays running past its cutoff")
    func promoteOrphan_recentLogActivityKeepsRunRunning() {
        let started = Self.et(day: 20, hour: 11, minute: 0)
        let now = Self.et(day: 20, hour: 11, minute: 25)          // 25 min in, cutoff 20
        let heartbeat = Self.et(day: 20, hour: 11, minute: 20)    // last lease keepalive
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: started, lastActivityAt: heartbeat,
            type: .consolidation, now: now) == .running)
    }

    @Test("a run whose log has been quiet longer than the cutoff is orphaned")
    func promoteOrphan_quietLogIsOrphaned() {
        let started = Self.et(day: 20, hour: 11, minute: 0)
        let lastWrite = Self.et(day: 20, hour: 11, minute: 10)
        let now = Self.et(day: 20, hour: 11, minute: 31)          // 21 min of silence
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: started, lastActivityAt: lastWrite,
            type: .consolidation, now: now) == .orphaned)
    }

    @Test("with no log activity known, the cutoff counts from the start")
    func promoteOrphan_noActivityFallsBackToStart() {
        let started = Self.et(day: 20, hour: 11, minute: 0)
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: started, lastActivityAt: nil,
            type: .consolidation, now: Self.et(day: 20, hour: 11, minute: 21)) == .orphaned)
        // A modification time before the start can't make the run look older.
        #expect(SessionLogService.promoteOrphan(
            parsedStatus: .running, startedAt: started,
            lastActivityAt: Self.et(day: 20, hour: 10, minute: 0),
            type: .consolidation, now: Self.et(day: 20, hour: 11, minute: 15)) == .running)
    }

    @Test("loading a long consolidation that is still heartbeating shows it running")
    func loadInitial_longRunWithRecentHeartbeatStaysRunning() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let log = dir.appendingPathComponent("scout-2026-04-20_11-00.log")
        try """
        === Scout run starting at Mon Apr 20 11:00:29 EDT 2026 ===
        lease keepalive: renewed run scout-morning-consolidation-1's lease until 2026-04-20T15:25:32Z
        lease keepalive: renewed run scout-morning-consolidation-1's lease until 2026-04-20T15:30:37Z

        """.write(to: log, atomically: true, encoding: .utf8)
        try Self.setLogModified(log, to: Self.et(day: 20, hour: 11, minute: 20))

        // 23 minutes after the start, 3 minutes after the last keepalive.
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(), clock: FixedClock(date: Self.et(day: 20, hour: 11, minute: 23)),
            timeZone: Self.ny, parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        let runs = try await service.loadInitial()
        #expect(runs.first?.status == .running)
    }

    @Test("the sweep orphans a run once its log goes quiet, not while it is still writing")
    func sweep_usesLogActivityNotStartTime() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let log = dir.appendingPathComponent("scout-2026-04-20_11-00.log")
        try "=== Scout run starting at Mon Apr 20 11:00:29 EDT 2026 ===\n"
            .write(to: log, atomically: true, encoding: .utf8)
        try Self.setLogModified(log, to: Self.et(day: 20, hour: 11, minute: 10))

        let clock = SteppableClock(date: Self.et(day: 20, hour: 11, minute: 12))
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            fileEvents: NoopFS(), clock: clock, timeZone: Self.ny,
            parseCacheURL: dir.appendingPathComponent("parse-cache.json"))
        _ = try await service.loadInitial()
        #expect(service.runs.first?.status == .running)

        // 25 min after the start, but a heartbeat landed at 11:20 → still running.
        try Self.setLogModified(log, to: Self.et(day: 20, hour: 11, minute: 20))
        clock.date = Self.et(day: 20, hour: 11, minute: 25)
        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == .running)

        // The log then stays silent for 21 minutes → orphaned.
        clock.date = Self.et(day: 20, hour: 11, minute: 41)
        service.sweepStaleStatuses()
        #expect(service.runs.first?.status == .orphaned)
    }

    /// Pin a fixture log's modification time — the liveness signal the orphan
    /// rule reads — so tests don't depend on when they happen to run.
    nonisolated static func setLogModified(_ url: URL, to date: Date) throws {
        try FileManager.default.setAttributes([.modificationDate: date], ofItemAtPath: url.path)
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

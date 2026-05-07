import Testing
import Foundation
@testable import Scout

@Suite("SessionLogService")
struct SessionLogServiceTests {

    // MARK: - Filename parsing

    private static let ny = TimeZone(identifier: "America/New_York")!

    @Test func parseFilename_scoutMorningBriefing() {
        // 2026-04-20 is a Monday
        let url = URL(fileURLWithPath: "/x/scout-2026-04-20_08-03.log")
        let parsed = SessionLogService.parseFilename(url, timeZone: Self.ny)
        #expect(parsed?.runnerScript == "run-scout.sh")
        #expect(parsed?.type == .morningBriefing)
        #expect(parsed?.startedAt != nil)
        let comps = Calendar(identifier: .gregorian)
            .dateComponents(in: Self.ny, from: parsed!.startedAt)
        #expect(comps.hour == 8)
        #expect(comps.minute == 3)
    }

    @Test func parseFilename_weekendBriefing() {
        // 2026-04-19 is a Sunday
        let url = URL(fileURLWithPath: "/x/scout-2026-04-19_08-03.log")
        let parsed = SessionLogService.parseFilename(url, timeZone: Self.ny)
        #expect(parsed?.type == .weekendBriefing)
    }

    @Test func parseFilename_dreamingNightly() {
        let url = URL(fileURLWithPath: "/x/dreaming-2026-04-18_22-00.log")
        let parsed = SessionLogService.parseFilename(url, timeZone: Self.ny)
        #expect(parsed?.type == .dreaming)
        #expect(parsed?.runnerScript == "run-dreaming.sh")
    }

    @Test func parseFilename_rejectsUnrelated() {
        #expect(SessionLogService.parseFilename(URL(fileURLWithPath: "/x/launchd-stdout.log")) == nil)
        #expect(SessionLogService.parseFilename(URL(fileURLWithPath: "/x/failures.log")) == nil)
    }

    // MARK: - Body parsing

    @Test func parseBody_stillRunning() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-19_15-00.log")
        try "=== SCOUT run starting at Sun Apr 19 15:00:01 EDT 2026 ===\n"
            .write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .running)
        #expect(body.exitCode == nil)
        #expect(body.endedAt == nil)
    }

    @Test func parseBody_timeout() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-19_16-00.log")
        try """
        === SCOUT run starting at Sun Apr 19 16:00:01 EDT 2026 ===
        === TIMEOUT: claude exceeded 1h wall-clock (exit 124) ===
        === SCOUT run finished at Sun Apr 19 17:00:35 EDT 2026 (exit code: 124, duration: 3634s) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .timeout)
        #expect(body.exitCode == 124)
    }

    @Test func parseBody_success() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-19_17-00.log")
        try """
        === SCOUT run starting at Sun Apr 19 17:00:01 EDT 2026 ===
        doing stuff
        === SCOUT run finished at Sun Apr 19 17:05:00 EDT 2026 (exit code: 0, duration: 299s) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .success)
        #expect(body.exitCode == 0)
    }

    @Test func parseBody_dreamingFinished() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("dreaming-2026-04-20_22-15.log")
        try """
        === SCOUT Dreaming run starting at Mon Apr 20 22:15:05 EDT 2026 ===
        did some dreaming
        === SCOUT Dreaming run finished at Mon Apr 20 22:15:36 EDT 2026 (exit code: 0, duration: 31s) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .success)
        #expect(body.exitCode == 0)
        #expect(body.endedAt != nil)
    }

    @Test func parseBody_researchFinished() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("research-2026-04-20_10-00.log")
        try """
        === SCOUT Research run starting at Mon Apr 20 10:00:01 EDT 2026 ===
        did research
        === SCOUT Research run finished at Mon Apr 20 10:20:00 EDT 2026 (exit code: 0, duration: 1199s) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .success)
        #expect(body.exitCode == 0)
        #expect(body.endedAt != nil)
    }

    @Test func parseBody_finishedWithoutDuration() throws {
        // Older (pre-duration-segment) log shape from Apr 10–11
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("dreaming-2026-04-11_09-37.log")
        try """
        === SCOUT Dreaming run starting at Sat Apr 11 09:37:13 EDT 2026 ===
        === SCOUT Dreaming run finished at Sat Apr 11 09:47:11 EDT 2026 (exit code: 0) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .success)
        #expect(body.exitCode == 0)
        #expect(body.endedAt != nil)
    }

    @Test func parseBody_budgetSkip() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-19_18-00.log")
        try """
        === SCOUT run starting at Sun Apr 19 18:00:01 EDT 2026 ===
        === Budget check: skipping this run ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .skippedBudget)
    }

    @Test func parseBody_concurrencySkipWithoutFinish() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-20_11-03.log")
        try """
        === SCOUT run starting at Mon Apr 20 11:03:02 EDT 2026 ===
        === Another SCOUT session running (PID 42392) — skipping ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .skippedConcurrency)
    }

    @Test func parseBody_budgetSkipWinsOverFinishLine() throws {
        // Guards the status-chain precedence: when a log contains BOTH a
        // skip marker AND a finish line with exit 0, the marker must win.
        // This is defensive — a script bug where budget-skip fires but
        // still writes a finish line shouldn't be classified as `.success`.
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-2026-04-21_19-00.log")
        try """
        === SCOUT run starting at Tue Apr 21 19:00:01 EDT 2026 ===
        === Budget check: skipping this run ===
        === SCOUT run finished at Tue Apr 21 19:00:02 EDT 2026 (exit code: 0, duration: 1s) ===
        """.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let parsed = SessionLogService.parseFilename(tmp)!
        let body = try SessionLogService.parseBody(at: tmp, filename: parsed)
        #expect(body.status == .skippedBudget)
    }

    // MARK: - Assembly

    @Test func assemblesRunsFromFixtureLogs() async throws {
        // Xcode's synchronized source groups flatten resources into the test
        // bundle root. Reconstitute a logs directory in a temp location.
        let tempDir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true
        )
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let logNames = [
            "scout-2026-04-19_08-08",
            "dreaming-2026-04-19_07-00",
            "scout-2026-04-17_08-03"
        ]
        for n in logNames {
            if let src = Bundle(for: FixtureAnchor.self).url(forResource: n, withExtension: "log") {
                let dst = tempDir.appendingPathComponent(src.lastPathComponent)
                try FileManager.default.copyItem(at: src, to: dst)
            }
        }

        let trackerURL = Bundle(for: FixtureAnchor.self)
            .url(forResource: "usage-tracker", withExtension: "jsonl")!
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()

        let service = await SessionLogService(
            logsDirectory: tempDir,
            trackerService: tracker,
            fileEvents: NoopFS(),
            timeZone: Self.ny
        )
        let runs = try await service.loadInitial()
        #expect(!runs.isEmpty, "fixture logs should produce at least one Run")
        #expect(runs.allSatisfy { $0.logSizeBytes > 0 })
        #expect(runs.allSatisfy { $0.id.contains("-") })
    }

    @Test func loadInitial_sweepsStaleDreamingRunningToOrphaned() async throws {
        // Dreaming run started 13h ago, never wrote a finish line.
        // Dreaming's orphan cutoff is 12h → should promote to .orphaned.
        let tempDir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true
        )
        defer { try? FileManager.default.removeItem(at: tempDir) }

        // Filename date is in America/New_York. Pick a dreaming slot.
        let logURL = tempDir.appendingPathComponent("dreaming-2026-04-20_22-15.log")
        try "=== SCOUT Dreaming run starting at Mon Apr 20 22:15:05 EDT 2026 ===\n"
            .write(to: logURL, atomically: true, encoding: .utf8)

        // Clock: 13h after that start (22:15 ET + 13h = 11:15 ET next day)
        var comps = DateComponents()
        comps.year = 2026; comps.month = 4; comps.day = 21
        comps.hour = 11; comps.minute = 15
        comps.timeZone = TimeZone(identifier: "America/New_York")
        let fakeNow = Calendar(identifier: .gregorian).date(from: comps)!
        let clock = FixedClock(date: fakeNow)

        let trackerURL = tempDir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()

        let service = await SessionLogService(
            logsDirectory: tempDir,
            trackerService: tracker,
            fileEvents: NoopFS(),
            clock: clock,
            timeZone: Self.ny
        )
        let runs = try await service.loadInitial()
        #expect(runs.count == 1)
        #expect(runs.first?.status == .orphaned)
    }

    @Test func loadInitial_freshRunningStaysRunning() async throws {
        let tempDir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true
        )
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let logURL = tempDir.appendingPathComponent("dreaming-2026-04-21_10-00.log")
        try "=== SCOUT Dreaming run starting at Tue Apr 21 10:00:05 EDT 2026 ===\n"
            .write(to: logURL, atomically: true, encoding: .utf8)

        // Clock: 10 minutes later
        var comps = DateComponents()
        comps.year = 2026; comps.month = 4; comps.day = 21
        comps.hour = 10; comps.minute = 10
        comps.timeZone = TimeZone(identifier: "America/New_York")
        let fakeNow = Calendar(identifier: .gregorian).date(from: comps)!
        let clock = FixedClock(date: fakeNow)

        let trackerURL = tempDir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()

        let service = await SessionLogService(
            logsDirectory: tempDir,
            trackerService: tracker,
            fileEvents: NoopFS(),
            clock: clock,
            timeZone: Self.ny
        )
        let runs = try await service.loadInitial()
        #expect(runs.count == 1)
        #expect(runs.first?.status == .running)
    }

    @Test func loadInitial_sweepsStaleResearchRunningToOrphaned() async throws {
        // Research cutoff is 2h. Research run started 3h ago, no finish line
        // → should promote to .orphaned. Verifies per-type plumbing on a
        // different cutoff than the dreaming test (defensive: catches a
        // hard-coded dreaming-only promotion bug).
        let tempDir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true
        )
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let logURL = tempDir.appendingPathComponent("research-2026-04-21_08-00.log")
        try "=== SCOUT Research run starting at Tue Apr 21 08:00:05 EDT 2026 ===\n"
            .write(to: logURL, atomically: true, encoding: .utf8)

        // Clock: 3h after start (08:00 + 3h = 11:00 ET). Research cutoff is 2h.
        var comps = DateComponents()
        comps.year = 2026; comps.month = 4; comps.day = 21
        comps.hour = 11; comps.minute = 0
        comps.timeZone = TimeZone(identifier: "America/New_York")
        let fakeNow = Calendar(identifier: .gregorian).date(from: comps)!
        let clock = FixedClock(date: fakeNow)

        let trackerURL = tempDir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()

        let service = await SessionLogService(
            logsDirectory: tempDir,
            trackerService: tracker,
            fileEvents: NoopFS(),
            clock: clock,
            timeZone: Self.ny
        )
        let runs = try await service.loadInitial()
        #expect(runs.count == 1)
        #expect(runs.first?.status == .orphaned)
    }

    @Test func loadInitial_staleSkippedBudgetStaysSkippedBudget() async throws {
        // Orphan sweep only touches .running — skip/success/failure are immune.
        let tempDir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true
        )
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let logURL = tempDir.appendingPathComponent("scout-2026-04-20_11-03.log")
        try """
        === SCOUT run starting at Mon Apr 20 11:03:02 EDT 2026 ===
        === Budget check: skipping this run ===
        """.write(to: logURL, atomically: true, encoding: .utf8)

        var comps = DateComponents()
        comps.year = 2026; comps.month = 4; comps.day = 22
        comps.hour = 11; comps.minute = 15
        comps.timeZone = TimeZone(identifier: "America/New_York")
        let fakeNow = Calendar(identifier: .gregorian).date(from: comps)!
        let clock = FixedClock(date: fakeNow)

        let trackerURL = tempDir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()

        let service = await SessionLogService(
            logsDirectory: tempDir,
            trackerService: tracker,
            fileEvents: NoopFS(),
            clock: clock,
            timeZone: Self.ny
        )
        let runs = try await service.loadInitial()
        #expect(runs.count == 1)
        #expect(runs.first?.status == .skippedBudget)
    }

    // MARK: - Orphan cutoffs

    @Test func orphanAfter_research2h() {
        #expect(RunType.research.orphanAfter == 2 * 3600)
    }

    @Test func orphanAfter_briefingsAndConsolidations6h() {
        #expect(RunType.morningBriefing.orphanAfter == 6 * 3600)
        #expect(RunType.weekendBriefing.orphanAfter == 6 * 3600)
        #expect(RunType.consolidation.orphanAfter == 6 * 3600)
    }

    @Test func orphanAfter_dreaming12h() {
        #expect(RunType.dreaming.orphanAfter == 12 * 3600)
    }
}

struct FixedClock: ClockSource {
    let date: Date
    func now() -> Date { date }
}

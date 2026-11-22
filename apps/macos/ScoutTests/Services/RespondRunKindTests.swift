import Testing
import Foundation
@testable import Scout

/// The fourth runner, `run-respond.sh` (a reply to the owner's Telegram
/// messages): its `respond-*.log` runs reach the run list under one plain name,
/// and a run kind newer than this build never breaks the list. Synthetic data
/// only: invented times, message ids and log names.
@Suite("Respond run kind")
struct RespondRunKindTests {

    private static let ny = TimeZone(identifier: "America/New_York")!

    private static func date(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        var c = DateComponents()
        c.year = 2026; c.month = 4; c.day = day; c.hour = hour; c.minute = minute
        c.timeZone = ny
        return Calendar(identifier: .gregorian).date(from: c)!
    }

    // MARK: - Log names

    @Test func parseFilename_respondMinuteName() throws {
        let parsed = try #require(SessionLogService.parseFilename(
            URL(fileURLWithPath: "/x/respond-2026-04-20_14-07.log"), timeZone: Self.ny))
        #expect(parsed.type == .respond)
        #expect(parsed.runnerScript == "run-respond.sh")
        #expect(parsed.pid == nil)
        #expect(Run.makeId(type: parsed.type, startedAt: parsed.startedAt) == "respond-2026-04-20T18:07:00Z")
    }

    @Test func parseFilename_respondSecondsAndPidName() throws {
        let parsed = try #require(SessionLogService.parseFilename(
            URL(fileURLWithPath: "/x/respond-2026-04-20_14-07-31-4242.log"), timeZone: Self.ny))
        #expect(parsed.type == .respond)
        #expect(parsed.pid == 4242)
    }

    /// A runner this build doesn't know is left out, never filed as another kind.
    @Test func parseFilename_unknownRunnerIsLeftOut() {
        for name in ["triage-2026-04-20_14-07", "triage-2026-04-20_14-07-31-4242"] {
            #expect(SessionLogService.parseFilename(URL(fileURLWithPath: "/x/\(name).log")) == nil, "\(name)")
        }
    }

    // MARK: - The run list

    @Test func loadInitial_listsRespondRunAndSkipsUnknownKind() async throws {
        let dir = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory, create: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try """
        === Scout run starting at Mon Apr 20 08:03:02 EDT 2026 ===
        === Scout run finished at Mon Apr 20 08:20:40 EDT 2026 (exit code: 0, duration: 1058s) ===
        """.write(to: dir.appendingPathComponent("scout-2026-04-20_08-03.log"), atomically: true, encoding: .utf8)
        try """
        === Scout Respond run starting at Mon Apr 20 14:07:02 EDT 2026 ===
        === model: sonnet ===
        === Scout Respond run finished at Mon Apr 20 14:09:40 EDT 2026 (exit code: 0, duration: 158s) ===
        """.write(to: dir.appendingPathComponent("respond-2026-04-20_14-07.log"), atomically: true, encoding: .utf8)
        // A kind from a newer engine: its log must not break, or join, the list.
        try """
        === Scout Triage run starting at Mon Apr 20 14:30:02 EDT 2026 ===
        === Scout Triage run finished at Mon Apr 20 14:31:00 EDT 2026 (exit code: 0, duration: 58s) ===
        """.write(to: dir.appendingPathComponent("triage-2026-04-20_14-30.log"), atomically: true, encoding: .utf8)

        let tracker = await UsageTrackerService(
            trackerURL: dir.appendingPathComponent("usage-tracker.jsonl"), fileEvents: NoopFS())
        let service = await SessionLogService(
            logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
            clock: FixedClock(date: Self.date(20, 15, 0)), timeZone: Self.ny,
            parseCacheURL: sandboxParseCacheURL(in: dir))

        let runs = try await service.loadInitial()

        #expect(runs.map(\.logPath.lastPathComponent).sorted()
                == ["respond-2026-04-20_14-07.log", "scout-2026-04-20_08-03.log"])
        let respond = try #require(runs.first { $0.type == .respond })
        #expect(respond.runnerScript == "run-respond.sh")
        #expect(respond.status == .success)
        #expect(respond.model == "sonnet")
        #expect(respond.displayName == "Telegram reply")

        // With its ledger row (the inbound trigger ran the `scout-respond` skill).
        let row = RunLedgerEntry(
            slot: "scout-respond", mode: "respond", runner: "respond", trigger: "event",
            startedAt: Self.date(20, 14, 7), finishedAt: Self.date(20, 14, 9),
            outcome: "ok", log: "respond-2026-04-20_14-07.log")
        await service.applyLedger(["respond-2026-04-20_14-07.log": row])
        let named = try #require(await service.runs.first { $0.type == .respond })
        #expect(named.displayName == "Telegram reply")
        #expect(named.listType() == .respond)
        #expect(named.rerunSlotKey == nil)
        #expect(!named.showsManualBadge)
    }

    /// Started by hand, the ledger names the slot `respond`, which no schedule
    /// has: the run still reads "Telegram reply", says it was started by you,
    /// and offers no "Run again" (`fire-now respond` has nothing to fire).
    @Test func handStartedRespondRun() {
        let run = Run.make(type: .respond, slotKey: "respond", trigger: "manual",
                           ledgerName: DisplayName.ledgerRun(slot: "respond", mode: "respond", runner: "respond"),
                           ledgerKey: "respond")
        #expect(run.runnerScript == "run-respond.sh")
        #expect(run.displayName == "Telegram reply")
        #expect(run.rerunSlotKey == nil)
        #expect(run.showsManualBadge)
    }

    @Test func respondRunTypeVocabulary() {
        #expect(RunType.allCases.contains(.respond), "the Runs Type filter lists every case")
        #expect(RunType.respond.displayName == "Telegram reply")
        #expect(RunType.respond.costTrackerKey == "respond")
        // No schedule runs a Telegram reply.
        #expect(RunType(slotKey: "respond") == nil)
        #expect(RunType(slotKey: "scout-respond") == nil)
    }

    // MARK: - Didn't finish

    /// A respond run queued behind another run waits for the session lock
    /// (`SCOUT_LOCK_WAIT_SECS`, 30 min) before its progress watcher starts,
    /// with one "waiting up to …" log line, then gets the default 45-minute
    /// run timeout. It is not "Didn't finish" until both could have passed:
    /// the wait comes from that line (`lockWait`, as for every kind), and
    /// respond's own threshold is the run timeout alone.
    @Test func respondRunWaitingForTheLockIsNotOrphaned() {
        let lockWait: TimeInterval = 1800
        let runTimeout: TimeInterval = 45 * 60
        #expect(RunType.respond.orphanAfter == runTimeout)

        let start = Self.date(20, 14, 7)
        let waitingLine = start.addingTimeInterval(2)   // the log's only line while it waits
        func status(after minutes: Double) -> RunStatus {
            SessionLogService.promoteOrphan(
                parsedStatus: .running, startedAt: start, lastActivityAt: waitingLine,
                lockWait: lockWait, type: .respond, now: waitingLine.addingTimeInterval(minutes * 60))
        }
        #expect(status(after: 29) == .running, "still waiting for the lock")
        #expect(status(after: 74) == .running, "the full wait plus a full run")
        #expect(status(after: 76) == .orphaned)

        // Holding the lock, the watcher's stall is judged against the run timeout.
        #expect(SessionLogService.promoteOrphan(parsedStatus: .running, startedAt: start, watcherStall: 40 * 60,
                                                type: .respond, now: start) == .running)
        #expect(SessionLogService.promoteOrphan(parsedStatus: .running, startedAt: start, watcherStall: 50 * 60,
                                                type: .respond, now: start) == .orphaned)
    }

    // MARK: - Commits

    @Test func respondCommitsLinkToTheRespondRun() {
        let subject = "respond [14:08]: tg:1001,1002 — answered two questions"
        #expect(CommitFamily.of(subject: subject) == .respond)
        #expect(CommitFamily.respond.displayName == "Telegram reply")

        let start = Self.date(20, 14, 7)
        let commit = Commit(id: "abc1234def5678abc1234def5678abc1234def56", shortSHA: "abc1234",
                            timestamp: start.addingTimeInterval(60), subject: subject,
                            filesChanged: 1, insertions: 1, deletions: 0)
        let respond = Run.make(type: .respond, startedAt: start, endedAt: start.addingTimeInterval(160))
        let briefing = Run.make(type: .consolidation, startedAt: start, endedAt: start.addingTimeInterval(160))
        #expect(respond.claims(commit))
        #expect(!briefing.claims(commit))
        #expect(CommitRunLinker.run(for: commit, in: [briefing, respond], now: start.addingTimeInterval(600)) == respond)
    }

    // MARK: - The run ledger (`scoutctl runs --json`)

    /// A respond row decodes and names itself; a row from a kind this build has
    /// never seen keeps its own words; one row that can't decode costs that row only.
    @Test func ledgerDecodesNewAndUnknownKinds() throws {
        let json = #"""
        {"schema_version": 1, "generated_at": "2026-04-20T19:00:00Z", "truncated": false, "runs": [
          {"slot": "morning-briefing", "mode": "scout", "runner": "scout", "trigger": "schedule",
           "started_at": "2026-04-20T12:03:00Z", "finished_at": "2026-04-20T12:20:00Z",
           "exit_code": 0, "outcome": "ok", "log": "scout-2026-04-20_08-03.log"},
          {"slot": "scout-respond", "mode": "respond", "runner": "respond", "trigger": "event",
           "started_at": "2026-04-20T18:07:00Z", "finished_at": "2026-04-20T18:09:40Z",
           "exit_code": 0, "outcome": "ok", "log": "respond-2026-04-20_14-07.log"},
          {"slot": "scout-triage", "mode": "triage", "runner": "triage", "trigger": "webhook",
           "lane": "future-field", "started_at": "2026-04-20T18:30:00Z", "finished_at": "2026-04-20T18:31:00Z",
           "exit_code": 0, "outcome": "ok", "log": "triage-2026-04-20_14-30.log"},
          {"slot": "broken-row", "started_at": "not a time", "finished_at": "2026-04-20T18:40:00Z",
           "exit_code": 0, "outcome": "ok"}
        ]}
        """#
        let ledger = try RunLedger.parse(Data(json.utf8)).get()
        #expect(ledger.runs.map(\.slot) == ["morning-briefing", "scout-respond", "scout-triage"])

        let respond = try #require(ledger.runs.first { $0.slot == "scout-respond" })
        #expect(respond.runner == "respond")
        #expect(DisplayName.ledgerRun(slot: respond.slot, mode: respond.mode, runner: respond.runner) == "Telegram reply")

        let future = try #require(ledger.runs.first { $0.slot == "scout-triage" })
        #expect(future.runner == "triage")
        #expect(future.trigger == "webhook")
        #expect(DisplayName.ledgerRun(slot: future.slot, mode: future.mode, runner: future.runner) == "Scout triage")
    }

    // MARK: - Plain language

    @Test func glossaryExplainsTelegramReply() {
        #expect(GlossaryTerm.telegramReply.term == "Telegram reply")
        #expect(GlossaryTerm.matching("respond").contains(.telegramReply))
        #expect(GlossaryTerm.matching("Telegram").contains(.telegramReply))
    }

    @Test func diagnosticsKeepRespondBrainFileName() {
        #expect(DiagnosticsScrubber.structuralMarkdown.contains("RESPOND.md"))
    }
}

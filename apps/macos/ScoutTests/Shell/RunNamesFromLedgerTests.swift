import Foundation
import Testing
@testable import Scout

/// UI audit card T2: Control Center names a run from its run ledger row's
/// `slot` / `mode`, as the Health page does, and falls back to the log-name
/// guess only without a row. The run list is headed "Runs", drops the two
/// columns that only ever showed dashes, and a selected run no longer renames
/// the main window. Synthetic logs and ledger rows only.
@Suite("Run names from the ledger (T2)")
@MainActor
struct RunNamesFromLedgerTests {

    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    // MARK: DisplayName.ledgerRun

    @Test("a row's slot names the run, the same way Schedules names it")
    func slotNamesTheRun() {
        #expect(DisplayName.ledgerRun(slot: "overnight-research", mode: "overnight-research") == "Overnight research")
        #expect(DisplayName.ledgerRun(slot: "morning-briefing", mode: "morning-briefing") == "Morning briefing")
        #expect(DisplayName.ledgerRun(slot: "dreaming-nightly", mode: "dreaming") == "Nightly dreaming")
        #expect(DisplayName.ledgerRun(slot: "evening-consolidation", mode: "scout") == "Evening consolidation")
    }

    @Test("the mode stands in when the slot is unknown, and nothing is named from an unknown row")
    func modeThenNothing() {
        #expect(DisplayName.ledgerRun(slot: "unknown", mode: "weekly-research") == "Weekly research")
        #expect(DisplayName.ledgerRun(slot: " ", mode: "midday-consolidation") == "Midday consolidation")
        #expect(DisplayName.ledgerRun(slot: "unknown", mode: "unknown") == nil)
        #expect(DisplayName.ledgerRun(slot: nil, mode: nil) == nil)
    }

    /// A local wall-clock time, since a bare `manual` run is named by the
    /// day and hour it started where the app runs.
    private func local(_ y: Int, _ m: Int, _ d: Int, _ h: Int, _ min: Int = 0) -> Date {
        Calendar(identifier: .gregorian).date(from: DateComponents(
            timeZone: .current, year: y, month: m, day: d, hour: h, minute: min))!
    }

    @Test("a hand-started run says so once, named by its runner")
    func manualRuns() {
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "dreaming") == "Dreaming (manual)")
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "research") == "Research (manual)")
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "something-new") == "Manual run")
        #expect(DisplayName.ledgerRun(slot: "dreaming-manual", mode: "dreaming-manual") == "Dreaming (manual)")
        #expect(DisplayName.ledgerRun(slot: "research-manual", mode: "research-manual") == "Research (manual)")
    }

    @Test("a bare manual run-scout.sh run is a briefing or a consolidation by its day and hour, as the session decides")
    func bareManualByStartTime() {
        // 17 Nov 2026 is a Tuesday, 21 Nov a Saturday.
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "scout",
                                      startedAt: local(2026, 11, 17, 14)) == "Consolidation (manual)")
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: nil,
                                      startedAt: local(2026, 11, 17, 8, 30)) == "Morning briefing (manual)")
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "scout",
                                      startedAt: local(2026, 11, 21, 9)) == "Weekend briefing (manual)")
        // Without a start time there is nothing to tell them apart by.
        #expect(DisplayName.ledgerRun(slot: "manual", mode: "manual", runner: "scout") == "Manual run")
        // Health names the row the same way.
        let row = RunLedgerEntry(slot: "manual", mode: "manual", runner: "scout", trigger: "manual",
                                 startedAt: local(2026, 11, 17, 14), finishedAt: local(2026, 11, 17, 14, 20),
                                 outcome: "ok", log: "scout-2026-11-17_14-00.log")
        #expect(HealthPanelModel.line(row).title == "Consolidation (manual)")
    }

    @Test("a slot or mode not shaped like an engine key is not named from")
    func forgedKeysIgnored() {
        #expect(DisplayName.isLedgerKey("overnight-research"))
        #expect(DisplayName.isLedgerKey("slot_2.v1"))
        #expect(!DisplayName.isLedgerKey("-leading-dash"))
        #expect(!DisplayName.isLedgerKey("has space"))
        #expect(!DisplayName.isLedgerKey("new\nline"))
        #expect(!DisplayName.isLedgerKey("caf\u{e9}"))
        #expect(!DisplayName.isLedgerKey(String(repeating: "a", count: 82)))
        #expect(DisplayName.isLedgerKey(String(repeating: "a", count: 81)))
        #expect(DisplayName.ledgerRun(slot: "Click <here>", mode: "weekly-research") == "Weekly research")
        #expect(DisplayName.ledgerRun(slot: "Click <here>", mode: "also bad") == nil)
    }

    @Test("a skipped hand-started run's problem names it the way its title does")
    func skipProblemName() throws {
        let row = RunLedgerEntry(slot: "dreaming-manual", mode: "dreaming-manual", runner: "dreaming",
                                 trigger: "manual", startedAt: t0, finishedAt: t0 + 5,
                                 outcome: "skipped_gates", failureClass: nil, log: "dreaming-b.log")
        let title = try #require(row.problem?.title)
        #expect(title.hasPrefix("Dreaming (manual): "))
    }

    // MARK: Run.displayName

    @Test("the ledger's name beats the log-name guess, and the guess stays as the fallback")
    func ledgerNameWins() throws {
        // A 1:03 AM weekday `scout-…` log: the hour alone calls it a briefing.
        let parsed = try #require(SessionLogService.parseFilename(
            URL(fileURLWithPath: "/v/.scout-logs/scout-2026-11-17_01-03.log")))
        #expect(parsed.type == .morningBriefing)

        let guessed = Run.make(type: parsed.type, startedAt: t0)
        #expect(guessed.displayName == "Morning briefing")

        let named = Run.make(type: parsed.type, startedAt: t0, ledgerName: "Overnight research")
        #expect(named.displayName == "Overnight research")
        #expect(named.with(status: .failure).displayName == "Overnight research")
    }

    @Test("the overlay gives each run its own row's name, and leaves rowless runs alone")
    func overlayNames() {
        let a = Run.make(startedAt: t0, logPath: URL(fileURLWithPath: "/v/.scout-logs/scout-a.log"))
        let b = Run.make(startedAt: t0 + 60, logPath: URL(fileURLWithPath: "/v/.scout-logs/scout-b.log"))
        let out = SessionLogService.overlayLedger([a, b], statuses: [:], names: ["scout-a.log": "Overnight research"])
        #expect(out.map(\.displayName) == ["Overnight research", "Morning briefing"])
    }

    @Test("a scout-*.log run the ledger records as overnight-research reads as Overnight research in Control Center")
    func endToEnd() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("t2-names-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let logName = "scout-2026-11-17_01-03.log"
        try "=== Scout run starting at Tue Nov 17 01:03:00 UTC 2026 ===\n=== Scout run finished at Tue Nov 17 01:40:00 UTC 2026 (exit 0) ===\n"
            .write(to: dir.appendingPathComponent(logName), atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: dir.appendingPathComponent("usage.jsonl"), fileEvents: NoopFS())
        let svc = SessionLogService(logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
                                    parseCacheURL: dir.appendingPathComponent("cache.json"))
        _ = try await svc.loadInitial()
        #expect(svc.runs.first?.displayName == "Morning briefing")

        let before = try #require(svc.runs.first)
        #expect(NotificationService.title(for: before).hasPrefix("Morning briefing "))

        // Newest first: a retry's row for the same log wins, and a row with no
        // log (a run that never opened one) names nothing.
        let row = RunLedgerEntry(slot: "overnight-research", mode: "overnight-research", runner: "scout",
                                 startedAt: t0, finishedAt: t0 + 2_220, outcome: "ok", log: logName)
        let older = RunLedgerEntry(slot: "morning-briefing", mode: "morning-briefing", runner: "scout",
                                   startedAt: t0 - 60, finishedAt: t0 - 30, outcome: "ok", log: logName)
        let logless = RunLedgerEntry(slot: "evening-consolidation", mode: "evening-consolidation", runner: "scout",
                                     startedAt: t0 - 120, finishedAt: t0 - 90, outcome: "skipped_lock")
        let ledger = RunLedger(runs: [row, older, logless], health: nil)
        #expect(ledger.entriesByLog.count == 1)
        svc.applyLedger(ledger.entriesByLog)
        let after = try #require(svc.runs.first)
        #expect(svc.runs.count == 1)
        #expect(after.displayName == "Overnight research")
        #expect(NotificationService.title(for: after).hasPrefix("Overnight research "))
        // Health names the same row the same way.
        #expect(HealthPanelModel.line(row).title == "Overnight research")
        // Search finds it by its own slot, not by the log-name guess.
        #expect(SessionsListView.matches(after, "overnight-research"))
        #expect(!SessionsListView.matches(after, "morning"))
    }

    @Test("Health and Control Center agree on hand-started runs")
    func healthMatchesControlCenter() {
        let row = RunLedgerEntry(slot: "dreaming-manual", mode: "dreaming-manual", runner: "dreaming",
                                 trigger: "manual", startedAt: t0, finishedAt: t0 + 60, outcome: "ok", log: "dreaming-a.log")
        let run = SessionLogService.overlayLedger(
            [Run.make(type: .dreaming, startedAt: t0, logPath: URL(fileURLWithPath: "/v/.scout-logs/dreaming-a.log"))],
            statuses: [:], names: ["dreaming-a.log": DisplayName.ledgerRun(slot: row.slot, mode: row.mode, runner: row.runner)!]
        )
        #expect(HealthPanelModel.line(row).title == "Dreaming (manual)")
        #expect(run.first?.displayName == HealthPanelModel.line(row).title)
    }

    // MARK: The manual badge

    @Test("the manual badge shows unless the name already says manual")
    func manualBadge() {
        #expect(Run.make(type: .morningBriefing, source: .manual, startedAt: t0).showsManualBadge)
        #expect(!Run.make(type: .morningBriefing, source: .launchdScheduled, startedAt: t0).showsManualBadge)
        #expect(!Run.make(type: .manual, source: .manual, startedAt: t0).showsManualBadge)
        #expect(!Run.make(type: .dreaming, source: .manual, startedAt: t0, slotKey: "dreaming-manual",
                          ledgerName: "Dreaming (manual)").showsManualBadge)
    }

    // MARK: The run list and the window title

    @Test("the run list is headed Runs, not Sessions")
    func runsHeading() {
        #expect(SessionsListView.heading == "Runs")
        #expect(SessionsListView.searchPrompt == "Search runs")
        #expect(!SessionsListView.emptyText.localizedCaseInsensitiveContains("session"))
    }

    private func source(_ path: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appending(path: "Scout")
        return try String(contentsOf: root.appending(path: path), encoding: .utf8)
    }

    @Test("a selected run doesn't rename the main window, and the row has no dash-only columns")
    func windowTitleAndColumns() throws {
        #expect(try !source("ControlCenter/RunDetailView.swift").contains(".navigationTitle("))
        let row = try source("ControlCenter/RunRow.swift")
        #expect(!row.contains("commitsString"))
        #expect(!row.contains("costString"))
    }
}

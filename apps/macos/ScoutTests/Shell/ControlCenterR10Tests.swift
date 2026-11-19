import Foundation
import Testing
import UserNotifications
@testable import Scout

/// UI audit R10: one run-status rule for the Control Center, Signals that
/// follow the engine's failure classes, chunked logs, the connector cards,
/// failure notifications with a reason and Run again, and the wake schedule.
@Suite("Control Center and notifications (R10)")
@MainActor
struct ControlCenterR10Tests {

    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    // MARK: #21 one status model

    @Test("a failure is the ledger's failed outcome: never a skip, a running run or one that didn't finish")
    func failureRule() {
        #expect(RunStatus.allCases.filter(RunOutcomeDisplay.isFailure) == [.failure, .timeout, .rateLimited])
    }

    @Test("the Today tile, the heatmap and the failed signal count the same runs")
    func oneCount() {
        let runs: [Run] = [
            .make(startedAt: t0, status: .failure),
            .make(startedAt: t0, status: .rateLimited),
            .make(startedAt: t0, status: .skippedBudget),
            .make(startedAt: t0, status: .skippedGates),
            .make(startedAt: t0, status: .orphaned),
            .make(startedAt: t0, status: .success),
        ]
        #expect(NowStripView.todayHeadline(runs) == "6 runs · 2 failed")
        let cal = Calendar(identifier: .gregorian)
        let cells = ActivityHeatmapView.heatmapCells(runs: runs, dayCount: 1, calendar: cal,
                                                    today: cal.startOfDay(for: t0))
        #expect(cells.first?.failures == 2)
        #expect(SignalsRailCard.failedTodaySignal(todayRuns: runs)?.body.hasPrefix("2 runs failed today") == true)
        #expect(NowStripView.todayHeadline([.make(startedAt: t0)]) == "1 run · 0 failed")
    }

    @Test("a routine skip is amber with a pause, a safety skip red; only failures get the ✗")
    func tones() {
        #expect(RunOutcomeDisplay.tone(.skippedBudget) == .warn)
        #expect(RunOutcomeDisplay.symbol(.skippedBudget) == "pause")
        #expect(RunOutcomeDisplay.tone(.skippedGates) == .err)
        #expect(RunOutcomeDisplay.symbol(.skippedGates) != "xmark")
        #expect(RunOutcomeDisplay.tone(.failure) == .err)
        #expect(RunOutcomeDisplay.symbol(.failure) == "xmark")
        #expect(RunOutcomeDisplay.tone(.success) == .ok)
        #expect(RunOutcomeDisplay.tone(.running) == .active)
    }

    // MARK: #22 the day filter is visible

    @Test("the day filter chip names the day")
    func dayChip() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = .current
        let day = cal.date(from: DateComponents(year: 2026, month: 11, day: 12))!
        let label = ActivityHeatmapView.dayFilterLabel(day)
        #expect(label.hasPrefix("Runs on "))
        #expect(label.contains("12"))
    }

    // MARK: #23 Signals follow the engine

    @Test("the usage-limit signal fires on the engine's rate_limited, not on a log line mentioning 429")
    func usageLimit() {
        let now = t0
        var mentions = Run.make(startedAt: now.addingTimeInterval(-600), status: .failure,
                                failureClass: "auth_race")
        mentions = Run(id: mentions.id, type: mentions.type, runnerScript: mentions.runnerScript,
                       source: mentions.source, scheduledAt: nil, startedAt: mentions.startedAt, endedAt: nil,
                       headerStartedAt: nil, status: .failure, exitCode: 1, cost: nil, budgetCap: nil,
                       logPath: mentions.logPath, logSizeBytes: 0,
                       errorsDetected: [DetectedError(line: 3, pattern: "429", snippet: "API Error: 429")],
                       commits: [], retryOf: nil, failureClass: "auth_race")
        #expect(SignalsRailCard.usageLimitSignal(runs: [mentions], now: now) == nil)

        let limited = Run.make(startedAt: now.addingTimeInterval(-600), status: .rateLimited,
                               failureClass: "rate_limited")
        let signal = SignalsRailCard.usageLimitSignal(runs: [limited], now: now)
        #expect(signal?.severity == .warn)
        #expect(signal?.body.contains("top-up") == false)
        #expect(signal?.body.contains("tries again after it resets") == true)

        let old = Run.make(startedAt: now.addingTimeInterval(-25 * 3600), status: .rateLimited)
        #expect(SignalsRailCard.usageLimitSignal(runs: [old], now: now) == nil)
    }

    @Test("failed runs point to the Health page with their shared reason; sign-in races alone are amber")
    func failedSignal() {
        let races = [Run.make(startedAt: t0, status: .failure, failureClass: "auth_race"),
                     Run.make(startedAt: t0, status: .failure, failureClass: "auth_race")]
        let soft = SignalsRailCard.failedTodaySignal(todayRuns: races)
        #expect(soft?.action == .openHealth)
        #expect(soft?.severity == .warn)
        #expect(soft?.body == "2 runs failed today (Claude login was busy).")
        #expect(soft?.body.contains("Sessions") == false)

        // Every class that clears by itself is amber, as Problem has it.
        let softMix = [Run.make(startedAt: t0, status: .rateLimited),
                       Run.make(startedAt: t0, status: .failure, failureClass: "network"),
                       Run.make(startedAt: t0, status: .failure, failureClass: "git_lock")]
        #expect(SignalsRailCard.failedTodaySignal(todayRuns: softMix)?.severity == .warn)
        #expect(SignalsRailCard.failedTodaySignal(todayRuns: [Run.make(startedAt: t0, status: .failure)])?.severity == .err)

        let mixed = races + [Run.make(startedAt: t0, status: .timeout, failureClass: "timeout")]
        let hard = SignalsRailCard.failedTodaySignal(todayRuns: mixed)
        #expect(hard?.severity == .err)
        #expect(hard?.body == "3 runs failed today.")

        #expect(SignalsRailCard.failedTodaySignal(todayRuns: [.make(startedAt: t0, status: .skippedBudget)]) == nil)
    }

    @Test("the ledger's failure class reaches the run")
    func overlayFailureClass() {
        let run = Run.make(startedAt: t0, status: .failure, logPath: URL(fileURLWithPath: "/x/scout-a.log"))
        let out = SessionLogService.overlayLedger([run], statuses: [:], failureClasses: ["scout-a.log": "auth_race"])
        #expect(out.first?.failureClass == "auth_race")
        #expect(out.first?.with(status: .failure).failureClass == "auth_race")
    }

    // MARK: #25 chunked logs and diffs

    @Test("chunks keep every line, in order, at most the chunk size each")
    func chunks() {
        let text = (1...450).map { "line \($0)" }.joined(separator: "\n")
        let chunks = TextChunks.split(text: text)
        #expect(chunks.count == 3)
        #expect(chunks.joined(separator: "\n") == text)
        #expect(chunks.allSatisfy { $0.split(separator: "\n", omittingEmptySubsequences: false).count <= 200 })
        #expect(TextChunks.split(text: "").isEmpty)
        #expect(TextChunks.split(text: "a\n\nb", size: 1) == ["a", "", "b"])
        #expect(TextChunks.split(["a", "b", "c"], size: 0) == [["a"], ["b"], ["c"]])
    }

    @Test("chunks end at the byte cap too, and CRLF lines split")
    func chunkBytesAndCRLF() {
        let long = String(repeating: "x", count: 40_000)
        let chunks = TextChunks.split([long, long, "short"])
        #expect(chunks == [[long], [long, "short"]])
        #expect(TextChunks.split(text: "a\r\nb\r\nc") == ["a\nb\nc"])
        #expect(TextChunks.split(text: "a\r\nb", size: 1) == ["a", "b"])
    }

    // MARK: #28–#29 connector cards

    @Test("the banner is red only for a critical alert, and names it first")
    func bannerSeverity() {
        let warn = ConnectorAlert(ts: t0, level: .warning, connector: "github", reason: "slow", firstSeen: t0)
        let crit = ConnectorAlert(ts: t0, level: .critical, connector: "linear", reason: "down", firstSeen: t0)
        #expect(!ConnectorAlertBanner.isCritical([warn]))
        #expect(ConnectorAlertBanner.isCritical([warn, crit]))
        #expect(ConnectorAlertBanner.ordered([warn, crit]).map(\.connector) == ["linear", "github"])
    }

    @Test("matrix columns are named by when the run started, and unused connectors get no row")
    func matrix() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let now = cal.date(from: DateComponents(year: 2026, month: 11, day: 19, hour: 15))!
        let today = cal.date(from: DateComponents(year: 2026, month: 11, day: 19, hour: 8))!
        let earlier = cal.date(from: DateComponents(year: 2026, month: 11, day: 17, hour: 8))!
        let long = cal.date(from: DateComponents(year: 2026, month: 11, day: 3, hour: 8))!
        #expect(!ConnectorHealthRailCard.columnLabel(today, now: now, calendar: cal).hasPrefix("r"))
        #expect(ConnectorHealthRailCard.columnLabel(earlier, now: now, calendar: cal)
                == earlier.formatted(.dateTime.weekday(.abbreviated)))
        #expect(ConnectorHealthRailCard.columnLabel(long, now: now, calendar: cal).contains("3"))

        let calls = [ConnectorCall(ts: today, sessionId: "s1", mode: "briefing", tool: "search",
                                   connector: "slack", error: false, err: nil)]
        let m = ConnectorHealthMatrix(calls: calls, connectors: ["slack", "linear", "github"])
        #expect(ConnectorHealthRailCard.usedConnectors(matrix: m, in: m.sessionsNewestFirst) == ["slack"])
    }

    // MARK: #96 notifications

    @Test("a failed notification says why and offers Run again for its own schedule")
    func failureNotification() {
        let run = Run.make(type: .consolidation, status: .failure, slotKey: "evening-consolidation",
                           trigger: "schedule", failureClass: "auth_race")
        let content = NotificationService.content(for: run)
        #expect(content.body.hasPrefix("Claude login was busy."))
        #expect(content.categoryIdentifier == NotificationService.retryableRunCategory)
        #expect(NotificationService.slotKey(from: content.userInfo) == "evening-consolidation")
        #expect(NotificationService.runID(from: content.userInfo) == run.id)
    }

    @Test("an event-triggered or manual run, or a skip, gets no Run again")
    func noRetry() {
        let event = Run.make(status: .failure, slotKey: "triage-skill", trigger: "event")
        #expect(NotificationService.content(for: event).categoryIdentifier == NotificationService.runCategory)
        #expect(NotificationService.slotKey(from: NotificationService.content(for: event).userInfo) == nil)
        let manual = Run.make(status: .failure, slotKey: "dreaming-manual", trigger: "manual")
        #expect(NotificationService.retrySlotKey(for: manual) == nil)
        let skip = Run.make(status: .skippedGates, slotKey: "morning-briefing", trigger: "schedule")
        #expect(NotificationService.retrySlotKey(for: skip) == nil)
        let noLedger = Run.make(status: .failure)
        #expect(NotificationService.retrySlotKey(for: noLedger) == nil)
    }

    private let prefs = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: true)

    @Test("a notification shown before the ledger row is replaced once the ledger says why")
    func updateWhenLedgerArrives() {
        let early = Run.make(status: .failure)
        let shown = RunNotificationPolicy.Shown(run: early)
        var late = early
        late.failureClass = "network"
        late.slotKey = "morning-briefing"
        #expect(RunNotificationPolicy.shouldUpdate(shown: shown, run: late, preferences: prefs))
        #expect(!RunNotificationPolicy.shouldUpdate(shown: shown, run: early, preferences: prefs))
        #expect(!RunNotificationPolicy.shouldUpdate(shown: nil, run: late, preferences: prefs))
    }

    @Test("the ledger turning a log failure into a usage limit replaces the notification, without Run again")
    func failureBecomesUsageLimit() {
        let early = Run.make(status: .failure, slotKey: "morning-briefing", trigger: "schedule")
        let shown = RunNotificationPolicy.Shown(run: early)
        #expect(shown.retrySlotKey == "morning-briefing")
        var late = early.with(status: .rateLimited)
        late.failureClass = "rate_limited"
        #expect(RunNotificationPolicy.shouldUpdate(shown: shown, run: late, preferences: prefs))
        let updated = RunNotificationPolicy.Shown(run: late)
        #expect(updated.title.hasSuffix("hit a usage limit"))
        #expect(updated.body.hasPrefix("Usage limit reached."))
        #expect(updated.retrySlotKey == nil)
        // The owner turned usage-limit notifications off: leave it be.
        #expect(!RunNotificationPolicy.shouldUpdate(shown: shown, run: late,
                                                    preferences: .init(onFailure: true, onRateLimit: false)))
    }

    @Test("didn't finish → failed replaces the notification too")
    func orphanBecomesFailed() {
        let early = Run.make(status: .orphaned)
        var late = early.with(status: .failure)
        late.failureClass = "timeout"
        #expect(RunNotificationPolicy.shouldUpdate(shown: .init(run: early), run: late, preferences: prefs))
    }

    @Test("a usage limit gets no Run again: it would fail again until the limit resets")
    func noRetryForUsageLimit() {
        let run = Run.make(status: .rateLimited, slotKey: "morning-briefing", trigger: "schedule",
                           failureClass: "rate_limited")
        #expect(NotificationService.retrySlotKey(for: run) == nil)
        #expect(NotificationService.content(for: run).categoryIdentifier == NotificationService.runCategory)
    }

    @Test("a quiet replacement lands in Notification Center without a banner")
    func quietPresentation() {
        #expect(NotificationService.presentationOptions(for: [NotificationService.quietUpdateKey: true]) == [.list])
        #expect(NotificationService.presentationOptions(for: [:]) == [.banner, .sound])
    }

    // MARK: #4 wake schedule

    private func result(_ code: Int32, out: String = "", err: String = "") -> ProcessResult {
        ProcessResult(exitCode: code, stdout: Data(out.utf8), stderr: Data(err.utf8))
    }

    private let dry = "Note: AC-only.\n[dry-run] would run: pmset repeat wakeorpoweron MTWRF 07:55:00"

    @Test("a refused wake schedule gives the exact sudo command on its own")
    func wakeRefused() {
        let r = AppState.wakeScheduleResult(result(1, err: "RuntimeError: pmset failed: must be run as root"),
                                            dryRun: result(0, out: dry), check: nil)
        #expect(r.problem.severity == .error)
        #expect(r.sudoCommand == "sudo pmset repeat wakeorpoweron MTWRF 07:55:00")
        #expect(r.problem.fix?.contains("replaces any repeating wake time") == true)
        #expect(r.problem.details?.contains("must be run as root") == true)
    }

    @Test("installed only once pmset lists the wake; otherwise unconfirmed")
    func wakeConfirmed() {
        let sched = "Repeating power events:\n  wakepoweron at 7:55AM weekdays only\nScheduled power events:\n [0]  wake at 11/18/2026 14:20:00"
        let ok = AppState.wakeScheduleResult(result(0, out: "installed: …"), dryRun: result(0, out: dry),
                                             check: result(0, out: sched))
        #expect(ok.problem.severity == .info)
        #expect(ok.problem.title == "Wake schedule installed")
        #expect(ok.sudoCommand == nil)

        let other = "Repeating power events:\n  wakepoweron at 1:00AM weekdays only\n"
        let stale = AppState.wakeScheduleResult(result(0), dryRun: result(0, out: dry), check: result(0, out: other))
        #expect(stale.problem.title == "Couldn't confirm the wake schedule")
        let unchecked = AppState.wakeScheduleResult(result(0), dryRun: result(0, out: dry), check: nil)
        #expect(unchecked.problem.severity == .warning)

        #expect(AppState.wakeScheduleConfirmed(sched: "Repeating power events:\n  wakepoweron at 12:30PM weekdays only",
                                               command: "pmset repeat wakeorpoweron MTWRF 12:30:00"))
        #expect(!AppState.wakeScheduleConfirmed(sched: "Scheduled power events:\n [0] wake at 7:55AM", command: nil))
    }

    @Test("no weekday run, and a scoutctl that won't start, say why")
    func wakeOutcomes() {
        let none = AppState.wakeScheduleResult(
            result(1, err: "ValueError: no weekday slot found in schedule; cannot compute wake time"),
            dryRun: nil, check: nil)
        #expect(none.problem.title == "Nothing to wake for")
        let launch = AppState.wakeScheduleResult(nil, dryRun: nil, check: nil, launchError: "scoutctl not found")
        #expect(launch.problem.severity == .error)
        #expect(launch.problem.details == "scoutctl not found")
        #expect(launch.sudoCommand == nil)
        #expect(AppState.wakeCommand(fromDryRun: result(0, out: "[dry-run] would run: rm -rf /")) == nil)
    }
}

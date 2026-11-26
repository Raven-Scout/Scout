import Foundation
import Testing
import UserNotifications
@testable import Scout

/// Follow-ups from the review of UI audit R10 (#713): run notifications that
/// survive the ledger landing milliseconds later, a notification taken back
/// when the run didn't fail after all, one posted when the ledger first makes
/// a run worth telling, an honest wake-schedule check, cut-down giant log
/// lines, and a refused Run again that says so.
@Suite("Control Center and notifications follow-ups (R10)")
@MainActor
struct ControlCenterR10FollowUpTests {

    private let prefs = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: true)

    private func tracker(_ fake: FakeRunNotifications) -> RunNotificationTracker {
        RunNotificationTracker(poster: fake, recheckDelay: .milliseconds(20))
    }

    /// One run as the log first shows it (failed, no reason yet) and as the
    /// ledger later fills it in.
    private func failedRun() -> (running: Run, early: Run, late: Run) {
        let running = Run.make(status: .running)
        let early = running.with(status: .failure)
        var late = early
        late.failureClass = "network"
        late.slotKey = "morning-briefing"
        return (running, early, late)
    }

    // MARK: 1. the quiet update survives the race

    @Test("a ledger update that lands before the first notification shows still replaces it")
    func ledgerRace() async {
        let fake = FakeRunNotifications()
        fake.deliverOnPost = false   // Notification Center hasn't listed the first one yet
        let t = tracker(fake)
        let (running, early, late) = failedRun()
        t.update(runs: [running], armed: true, preferences: prefs)
        t.update(runs: [early], armed: true, preferences: prefs)
        #expect(fake.posts.map(\.quietly) == [false])

        // The first look misses; by the second the first notification is there.
        fake.onCheck = { fake in if fake.checks == 2 { fake.delivered.insert(early.id) } }
        t.update(runs: [late], armed: true, preferences: prefs)
        // Not marked as shown until it was actually posted.
        #expect(t.shown[late.id] == RunNotificationPolicy.Shown(run: early))
        await t.waitForBackgroundWork()

        #expect(fake.checks == 2)
        #expect(fake.posts.map(\.quietly) == [false, true])
        #expect(fake.posts.last?.body == NotificationService.body(for: late))
        #expect(t.shown[late.id] == RunNotificationPolicy.Shown(run: late))
    }

    @Test("a dismissed notification stays dismissed, and isn't looked for again")
    func dismissedStaysDismissed() async {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let (running, early, late) = failedRun()
        t.update(runs: [running], armed: true, preferences: prefs)
        t.update(runs: [early], armed: true, preferences: prefs)
        fake.delivered.removeAll()   // the owner dismissed it
        t.update(runs: [late], armed: true, preferences: prefs)
        await t.waitForBackgroundWork()
        #expect(fake.posts.count == 1)
        #expect(fake.checks == 2)    // looked, and once more
        t.update(runs: [late], armed: true, preferences: prefs)
        await t.waitForBackgroundWork()
        #expect(fake.checks == 2)
    }

    @Test("a run that changes again while the check is out posts its newest words, once")
    func newestWins() async {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let (running, early, late) = failedRun()
        t.update(runs: [running], armed: true, preferences: prefs)
        t.update(runs: [early], armed: true, preferences: prefs)
        t.update(runs: [late], armed: true, preferences: prefs)
        var later = late
        later.failureClass = "auth_race"
        t.update(runs: [later], armed: true, preferences: prefs)
        await t.waitForBackgroundWork()
        #expect(fake.posts.map(\.quietly) == [false, true])
        #expect(fake.posts.last?.body == NotificationService.body(for: later))
    }

    // MARK: 2. a notification the ledger proves wrong is taken back

    @Test("the ledger turning a notified failure into a success or a routine skip removes it")
    func withdraw() async {
        for outcome in [RunStatus.success, .skippedBudget, .skippedConcurrency, .skippedLease, .skippedRedundant] {
            let fake = FakeRunNotifications()
            let t = tracker(fake)
            let (running, early, _) = failedRun()
            t.update(runs: [running], armed: true, preferences: prefs)
            t.update(runs: [early], armed: true, preferences: prefs)
            t.update(runs: [early.with(status: outcome)], armed: true, preferences: prefs)
            #expect(fake.removed == [early.id], "\(outcome)")   // at once
            await t.waitForBackgroundWork()
            #expect(fake.removed == [early.id, early.id], "\(outcome)")   // and once more
            #expect(!fake.delivered.contains(early.id), "\(outcome)")
            #expect(t.shown[early.id] == nil, "\(outcome)")
            #expect(fake.posts.count == 1, "\(outcome)")
        }
    }

    @Test("a notified failure the ledger calls a usage limit, with that toggle off, is taken back with its Run again")
    func withdrawUsageLimitToggledOff() async {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let quietLimits = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: false)
        let running = Run.make(status: .running, slotKey: "morning-briefing", trigger: "schedule")
        let failed = running.with(status: .failure)
        t.update(runs: [running], armed: true, preferences: quietLimits)
        t.update(runs: [failed], armed: true, preferences: quietLimits)
        #expect(NotificationService.retrySlotKey(for: failed) == "morning-briefing")   // it offered Run again
        var limited = failed.with(status: .rateLimited)
        limited.failureClass = "rate_limited"
        t.update(runs: [limited], armed: true, preferences: quietLimits)
        await t.waitForBackgroundWork()
        #expect(!fake.delivered.contains(failed.id))
        #expect(t.shown[failed.id] == nil)
        #expect(fake.posts.map(\.quietly) == [false])
    }

    @Test("a withdrawal before Notification Center lists the notification still removes it")
    func withdrawRace() async {
        let fake = FakeRunNotifications()
        fake.deliverOnPost = false
        let t = tracker(fake)
        let (running, early, _) = failedRun()
        t.update(runs: [running], armed: true, preferences: prefs)
        t.update(runs: [early], armed: true, preferences: prefs)
        t.update(runs: [early.with(status: .success)], armed: true, preferences: prefs)
        // The first removal found nothing; now the notification shows up.
        fake.delivered.insert(early.id)
        await t.waitForBackgroundWork()
        #expect(!fake.delivered.contains(early.id))
        #expect(fake.removed == [early.id, early.id])
    }

    @Test("the second removal is skipped when the run was notified again meanwhile")
    func withdrawThenNotifiedAgain() async {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let (running, early, _) = failedRun()
        t.update(runs: [running], armed: true, preferences: prefs)
        t.update(runs: [early], armed: true, preferences: prefs)
        t.update(runs: [early.with(status: .success)], armed: true, preferences: prefs)
        t.update(runs: [early.with(status: .timeout)], armed: true, preferences: prefs)
        await t.waitForBackgroundWork()
        #expect(fake.posts.map(\.quietly) == [false, false])
        #expect(fake.delivered.contains(early.id))
        #expect(fake.removed == [early.id])
    }

    @Test("a status change to one not worth telling under the toggles withdraws; a toggle alone doesn't")
    func withdrawPolicy() {
        let quietLimits = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: false)
        let shown = RunNotificationPolicy.Shown(run: Run.make(status: .failure))
        let run = Run.make(status: .failure)
        #expect(!RunNotificationPolicy.shouldWithdraw(shown: nil, run: run.with(status: .success), preferences: prefs))
        #expect(!RunNotificationPolicy.shouldWithdraw(shown: shown, run: run.with(status: .rateLimited), preferences: prefs))
        #expect(RunNotificationPolicy.shouldWithdraw(shown: shown, run: run.with(status: .rateLimited), preferences: quietLimits))
        #expect(!RunNotificationPolicy.shouldWithdraw(shown: shown, run: run.with(status: .skippedGates), preferences: prefs))
        #expect(RunNotificationPolicy.shouldWithdraw(shown: shown, run: run.with(status: .success), preferences: prefs))
        #expect(!RunNotificationPolicy.shouldWithdraw(shown: shown, run: run,
                                                      preferences: .init(onFailure: false, onRateLimit: false)))
    }

    // MARK: 3. the ledger making a run worth telling posts

    @Test("a usage limit with that toggle off that the ledger later calls failed notifies")
    func laterWorthTelling() async {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let quietLimits = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: false)
        let running = Run.make(status: .running)
        t.update(runs: [running], armed: true, preferences: quietLimits)
        t.update(runs: [running.with(status: .rateLimited)], armed: true, preferences: quietLimits)
        #expect(fake.posts.isEmpty)
        t.update(runs: [running.with(status: .failure)], armed: true, preferences: quietLimits)
        #expect(fake.posts.map(\.quietly) == [false])
        #expect(fake.posts.first?.title.hasSuffix("failed") == true)
        // Once told, a repeat of the same outcome stays quiet.
        t.update(runs: [running.with(status: .failure)], armed: true, preferences: quietLimits)
        await t.waitForBackgroundWork()
        #expect(fake.posts.count == 1)
    }

    @Test("a run already on disk at launch never notifies, whatever the ledger says later")
    func onDiskStaysQuiet() {
        let fake = FakeRunNotifications()
        let t = tracker(fake)
        let old = Run.make(status: .skippedBudget)
        t.update(runs: [old], armed: false, preferences: prefs)
        t.update(runs: [old.with(status: .failure)], armed: true, preferences: prefs)
        #expect(fake.posts.isEmpty)
        #expect(!RunNotificationPolicy.shouldNotify(previous: .skippedBudget, run: old.with(status: .failure),
                                                    armed: true, preferences: prefs))
        #expect(RunNotificationPolicy.shouldNotify(previous: .skippedBudget, run: old.with(status: .failure),
                                                   armed: true, preferences: prefs, isLive: true))
    }

    // MARK: 4. wake schedule confirmation

    private func result(_ code: Int32, out: String = "", err: String = "") -> ProcessResult {
        ProcessResult(exitCode: code, stdout: Data(out.utf8), stderr: Data(err.utf8))
    }

    private let dry = "[dry-run] would run: pmset repeat wakeorpoweron MTWRF 07:55:00"
    private let otherWake = "Repeating power events:\n  wakepoweron at 6:00AM weekdays only\n"

    @Test("when pmset -g sched didn't run, the panel says Scout couldn't check")
    func uncheckedSaysSo() {
        for check in [nil, result(1, err: "pmset: error")] {
            let r = AppState.wakeScheduleResult(result(0), dryRun: result(0, out: dry), check: check)
            #expect(r.problem.title == "Couldn't confirm the wake schedule")
            #expect(r.problem.impact?.contains("couldn't check") == true)
            #expect(r.problem.impact?.contains("doesn't list it") != true)
        }
        let listed = AppState.wakeScheduleResult(result(0), dryRun: result(0, out: dry), check: result(0, out: otherWake))
        #expect(listed.problem.impact?.contains("doesn't list it") == true)
    }

    @Test("with no command from the dry run, an unrelated existing wake confirms nothing")
    func noCommandNoConfirmation() {
        #expect(!AppState.wakeScheduleConfirmed(sched: otherWake, command: nil))
        #expect(!AppState.wakeScheduleConfirmed(sched: otherWake, command: "pmset repeat cancel"))
        let r = AppState.wakeScheduleResult(result(0), dryRun: nil, check: result(0, out: otherWake))
        #expect(r.problem.severity == .warning)
        #expect(r.problem.impact?.contains("which wake time") == true)
    }

    /// Stands in for a hung child: waits `seconds` and ignores cancellation,
    /// as a stuck `pmset` would. Never blocks a thread or an actor.
    nonisolated private static func hang(seconds: Double) async {
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().asyncAfter(deadline: .now() + seconds) { c.resume() }
        }
    }

    // No wall-clock assertion: under a parallel run the main actor (and so
    // the caller's resumption) can be held by other suites for seconds. The
    // operations below hang for two minutes and ignore cancellation, so only
    // the time limit can make these calls return; the one-minute trait is
    // the generous bound that fails a regression instead of hanging the run.

    @Test("a slow operation gives up at the time limit; a quick one returns", .timeLimit(.minutes(1)))
    func timeLimit() async {
        let slow: Int? = await AppState.withinTimeLimit(.milliseconds(300)) {
            await Self.hang(seconds: 120)
            return 1
        }
        #expect(slow == nil)
        let quick: Int? = await AppState.withinTimeLimit(.seconds(30)) { 2 }
        #expect(quick == 2)
    }

    @Test("a pmset -g sched that hangs doesn't hang the install", .timeLimit(.minutes(1)))
    func hungCheck() async throws {
        let rule = RuleBasedRunner()
        rule.on(tool: "false", prefix: ["schedule", "install-wake-schedule", "--dry-run"], stdout: dry)
        rule.on(tool: "false", prefix: ["schedule", "install-wake-schedule"], stdout: "installed")
        rule.on({ url, _ in url.lastPathComponent == "pmset" }) { _, _, _ in
            await Self.hang(seconds: 120)
            // Reached only if nothing gave up first: an empty list reads
            // "doesn't list it", never "couldn't check".
            return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
        }
        let state = AppState(configuration: .testing(scoutDirectory: Self.tempDir(), runner: rule))
        await state.installWakeSchedule(checkLimit: .milliseconds(300))
        #expect(state.wakeScheduleResult?.problem.impact?.contains("couldn't check") == true)
        #expect(rule.calls(to: "pmset") == [["-g", "sched"]])
        #expect(!state.installingWakeSchedule)
    }

    // MARK: 5. a giant log line

    @Test("the pretty log view cuts one line over the chunk cap, at a whole character")
    func clipLine() {
        let long = String(repeating: "x", count: 70_000)
        let clipped = TextChunks.clipped(long)
        #expect(clipped.hasPrefix(String(repeating: "x", count: TextChunks.bytesPerChunk)))
        #expect(!clipped.hasPrefix(String(repeating: "x", count: TextChunks.bytesPerChunk + 1)))
        #expect(clipped.contains("line cut"))
        #expect(TextChunks.clipped("short") == "short")
        #expect(TextChunks.clipped("ééé", maxBytes: 3).hasPrefix("é …"))
    }

    // MARK: 6. a refused Run again from a notification says why

    private func makeState(fireNow exit: Int32, stderr: String = "") -> (AppState, FakeRunNotifications) {
        let rule = RuleBasedRunner()
        rule.on(tool: "false", prefix: ["schedule", "fire-now"], stderr: stderr, exit: exit)
        rule.on({ _, _ in true }) { _, _, _ in ProcessResult(exitCode: 0, stdout: Data("[]".utf8), stderr: Data()) }
        var config = AppState.Configuration.testing(scoutDirectory: Self.tempDir(), runner: rule)
        let fake = FakeRunNotifications()
        config.notifications = fake
        return (AppState(configuration: config), fake)
    }

    @Test("a Run again the engine refuses posts a notification saying why")
    func refusedRunAgain() async {
        let (state, fake) = makeState(fireNow: 1, stderr: "failed: lock_held")
        await state.runAgainFromNotification(slotKey: "morning-briefing")
        #expect(fake.refused.count == 1)
        #expect(fake.refused.first?.slotKey == "morning-briefing")
        #expect(fake.refused.first?.error.contains("lock_held") == true)
        #expect(state.fireNowError?.contains("lock_held") == true)

        let (ok, okFake) = makeState(fireNow: 0)
        await ok.runAgainFromNotification(slotKey: "morning-briefing")
        #expect(okFake.refused.isEmpty)
        #expect(ok.fireNowError == nil)
    }

    @Test("the refusal says why in plain words, never scoutctl's own")
    func refusedWording() {
        #expect(NotificationService.runAgainRefusedReason("Run now failed (exit 1): failed: lock_held")
                    .hasPrefix("Scout was starting another run"))
        #expect(NotificationService.runAgainRefusedReason("Run now failed (exit 1): failed: unknown slot: x")
                    .hasPrefix("That schedule no longer exists"))
        #expect(NotificationService.runAgainRefusedReason("x is already being started — request ignored.")
                    == "It's already starting.")
        let other = NotificationService.runAgainRefusedReason("Run now failed (exit 2): Traceback …")
        #expect(!other.contains("Traceback"))
        let content = NotificationService.runAgainRefusedContent(slotKey: "morning-briefing", error: "failed: lock_held")
        #expect(content.title.hasPrefix("Couldn't run "))
        #expect(content.title.hasSuffix(" again"))
        #expect(!content.title.contains("morning-briefing"))
        #expect(NotificationService.runID(from: content.userInfo) == nil)
        #expect(content.sound != nil)
        let starting = NotificationService.runAgainRefusedContent(
            slotKey: "morning-briefing", error: "morning-briefing is already being started — request ignored.")
        #expect(starting.sound == nil)
    }

    private static func tempDir() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("ControlCenterR10FollowUpTests-\(UUID().uuidString)", isDirectory: true)
    }
}

/// Records what the tracker and AppState ask Notification Center to do.
@MainActor
final class FakeRunNotifications: RunNotificationPosting {
    struct Post { let id: Run.ID; let title: String; let body: String; let quietly: Bool }
    var posts: [Post] = []
    var delivered: Set<Run.ID> = []
    var removed: [Run.ID] = []
    var refused: [(slotKey: String, error: String)] = []
    /// Whether a post lands in Notification Center at once.
    var deliverOnPost = true
    var checks = 0
    /// Runs on each `isDelivered` before it answers.
    var onCheck: ((FakeRunNotifications) -> Void)?

    func post(run: Run, quietly: Bool) {
        posts.append(Post(id: run.id, title: NotificationService.title(for: run),
                          body: NotificationService.body(for: run), quietly: quietly))
        if deliverOnPost { delivered.insert(run.id) }
    }

    func isDelivered(runID: Run.ID) async -> Bool {
        checks += 1
        onCheck?(self)
        return delivered.contains(runID)
    }

    func withdraw(runID: Run.ID) {
        removed.append(runID)
        delivered.remove(runID)
    }

    func postRunAgainRefused(slotKey: String, error: String) {
        refused.append((slotKey, error))
    }
}

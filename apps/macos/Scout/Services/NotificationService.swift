import Foundation
import UserNotifications

final class NotificationService: @unchecked Sendable {
    /// The `userInfo` key holding the run's id; `AppDelegate` reads it back
    /// when the notification is clicked and opens that run.
    nonisolated static let runIDKey = "runId"
    /// The `userInfo` key holding the schedule "Run again" fires
    /// (`Run.rerunSlotKey`); absent when the run has none.
    nonisolated static let slotKeyKey = "slotKey"
    /// The `userInfo` key marking a quiet replacement (`notify(run:quietly:)`):
    /// in the foreground it goes to Notification Center without a banner.
    nonisolated static let quietUpdateKey = "quietUpdate"

    /// A notification about a run that can be run again: it carries the
    /// "Run again" button. A run with no schedule of its own (an event run, a
    /// manual one) gets `runCategory`, without it.
    static let retryableRunCategory = "scout.run.retryable"
    static let runCategory = "scout.run"
    nonisolated static let retryActionID = "scout.run.retry"

    /// Registers the two run categories. Call once at launch.
    static func registerCategories(on center: UNUserNotificationCenter = .current()) {
        let retry = UNNotificationAction(identifier: retryActionID, title: "Run again", options: [])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: retryableRunCategory, actions: [retry], intentIdentifiers: []),
            UNNotificationCategory(identifier: runCategory, actions: [], intentIdentifiers: []),
        ])
    }

    /// Posts `run`'s notification under its id. `quietly` replaces the one
    /// already shown for this run without a sound or, in the foreground, a
    /// banner: the run ledger often names a failure's reason, class and
    /// schedule a moment after the run's log ended. Whether to replace at
    /// all (only while it's still in Notification Center, so one the owner
    /// dismissed stays dismissed) is `RunNotificationTracker`'s call.
    func post(run: Run, quietly: Bool) {
        let content = Self.content(for: run)
        if quietly {
            content.sound = nil
            content.userInfo[Self.quietUpdateKey] = true
        }
        UNUserNotificationCenter.current()
            .add(UNNotificationRequest(identifier: run.id, content: content, trigger: nil))
    }

    func isDelivered(runID: Run.ID) async -> Bool {
        await withCheckedContinuation { cont in
            UNUserNotificationCenter.current().getDeliveredNotifications { delivered in
                cont.resume(returning: delivered.contains { $0.request.identifier == runID })
            }
        }
    }

    /// Takes this run's notification back, whether Notification Center
    /// lists it yet or it's still on its way.
    func withdraw(runID: Run.ID) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [runID])
        center.removeDeliveredNotifications(withIdentifiers: [runID])
    }

    /// A notification's Run again that Scout's engine refused (an unknown
    /// schedule, another run starting at that moment): it says so, since the
    /// owner pressed it from outside the app and would see nothing otherwise.
    /// One per schedule; a second refusal replaces the first.
    func postRunAgainRefused(slotKey: String, error: String) {
        let content = Self.runAgainRefusedContent(slotKey: slotKey, error: error)
        UNUserNotificationCenter.current().add(UNNotificationRequest(
            identifier: "\(Self.runAgainRefusedPrefix)\(slotKey)", content: content, trigger: nil))
    }

    static let runAgainRefusedPrefix = "scout.run-again-refused."

    static func runAgainRefusedContent(slotKey: String, error: String) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "Couldn't run \(DisplayName.slotKey(slotKey)) again"
        content.body = runAgainRefusedReason(error)
        // "It's already starting" is good news: no sound for it.
        content.sound = error.contains("already being started") ? nil : .default
        content.categoryIdentifier = runCategory
        return content
    }

    /// Why a Run again didn't start, in plain words. `error` is
    /// `AppState.fireNowError`, which carries scoutctl's own words: they
    /// never reach the notification, only the reason they point to.
    nonisolated static func runAgainRefusedReason(_ error: String) -> String {
        if error.contains("already being started") {
            return "It's already starting."
        }
        if error.contains("lock_held") {
            return "Scout was starting another run at that moment. Try again in a minute."
        }
        if error.contains("unknown slot") {
            return "That schedule no longer exists. Check the Schedules page."
        }
        return "Scout's engine didn't start it. Try again in a minute, or open Scout's Schedules page to see why."
    }

    static func content(for run: Run) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = title(for: run)
        content.body = body(for: run)
        content.sound = .default
        content.interruptionLevel = (run.status == .rateLimited) ? .timeSensitive : .active
        var info: [String: Any] = [runIDKey: run.id]
        if let slot = retrySlotKey(for: run) { info[slotKeyKey] = slot }
        content.userInfo = info
        content.categoryIdentifier = info[slotKeyKey] == nil ? runCategory : retryableRunCategory
        return content
    }

    /// How a notification shows while Scout is in front: a quiet replacement
    /// only lands in Notification Center; anything else is a banner with sound.
    nonisolated static func presentationOptions(for userInfo: [AnyHashable: Any]) -> UNNotificationPresentationOptions {
        (userInfo[quietUpdateKey] as? Bool) == true ? [.list] : [.banner, .sound]
    }

    /// The schedule a notification's "Run again" fires: the run's own
    /// (`Run.rerunSlotKey`, the slot RunDetailView's Run again fires), and
    /// only for a run that failed or didn't finish. Never for an event run or
    /// a manual one; never for a skip, which would only skip again until its
    /// cause is fixed; and never for a usage limit, which would fail again
    /// (and cost) until the limit resets, when Scout retries by itself.
    nonisolated static func retrySlotKey(for run: Run) -> String? {
        guard run.status != .rateLimited,
              RunOutcomeDisplay.isFailure(run.status) || run.status == .orphaned else { return nil }
        return run.rerunSlotKey
    }

    /// The run a clicked notification is about, if it names one.
    nonisolated static func runID(from userInfo: [AnyHashable: Any]) -> Run.ID? {
        (userInfo[runIDKey] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }

    /// The schedule a notification's "Run again" should fire, if it names one.
    nonisolated static func slotKey(from userInfo: [AnyHashable: Any]) -> String? {
        (userInfo[slotKeyKey] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }

    nonisolated static func title(for run: Run) -> String {
        "\(run.displayName) \(verb(for: run.status))"
    }

    /// One line on why, then where to look. A failure's reason is its
    /// run-ledger failure class ("Claude login was busy"), never a line
    /// quoted from the log, which can hold the session's own output.
    nonisolated static func body(for run: Run) -> String {
        if let reason = run.status.skipReason { return "\(reason). Click to see the run." }
        if let reason = RunOutcomeDisplay.failureReason(run) { return "\(reason). Click to see the run and its log." }
        if run.status == .orphaned { return "It stopped without saying how it ended. Click to see the run and its log." }
        return "Click to see the run and its log."
    }

    nonisolated private static func verb(for status: RunStatus) -> String {
        switch status {
        case .success:          return "finished"
        case .failure:          return "failed"
        case .timeout:          return "ran out of time"
        case .rateLimited:      return "hit a usage limit"
        case .orphaned:         return "stopped without finishing"
        case .running:          return "started"
        case .scheduled:        return "is scheduled"
        case .skippedBudget, .skippedConcurrency, .skippedGates, .skippedSandbox,
             .skippedRuntime, .skippedLease, .skippedRedundant:
            return "was skipped"
        }
    }
}

/// The "a new version of Scout is ready" notification (`UpdateService` posts
/// it once per version). One identifier, so a newer version replaces an
/// older one still in Notification Center. Clicking it brings up Sparkle's
/// update alert (`AppDelegate.showAppUpdate`).
@MainActor
final class AppUpdateNotifier: AppUpdateNotifying {
    nonisolated static let identifier = "scout.app-update"
    /// The `userInfo` key holding the version; its presence is what marks a
    /// notification as this one when clicked.
    nonisolated static let versionKey = "appUpdateVersion"

    func postAppUpdate(version: String) {
        UNUserNotificationCenter.current().add(UNNotificationRequest(
            identifier: Self.identifier, content: Self.content(version: version), trigger: nil))
    }

    func withdrawAppUpdate() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.identifier])
        center.removeDeliveredNotifications(withIdentifiers: [Self.identifier])
    }

    nonisolated static func content(version: String) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "Scout \(version) is ready"
        content.body = "Click to update. Scout relaunches in a few seconds."
        content.sound = .default
        content.userInfo = [versionKey: version]
        return content
    }

    nonisolated static func isAppUpdate(_ userInfo: [AnyHashable: Any]) -> Bool {
        userInfo[versionKey] is String
    }
}

/// Which finished runs raise a notification, as Settings ▸ Notifications
/// sets it (card C3: the two toggles used to be stored and never read).
nonisolated enum RunNotificationPolicy {
    /// The `@AppStorage` keys Settings ▸ Notifications writes. Both default on.
    static let failureKey = "notifyOnFailure"
    static let rateLimitKey = "notifyOnRateLimit"

    struct Preferences: Equatable, Sendable {
        var onFailure: Bool
        var onRateLimit: Bool
    }

    /// `previous` is the status the app last saw for this run, nil when the
    /// run is new. `armed` is false until the launch-time log load is done,
    /// so runs that were already on disk never notify. A run notifies once,
    /// when it first reaches an outcome worth telling: from running, or
    /// straight away when its first sighting is already finished (a fast
    /// failure or skip). Later changes replace that one notification
    /// (`shouldUpdate`) rather than raising another.
    ///
    /// `shown` is the notification already posted for this run, if any.
    /// `isLive` says the app saw this run start, or first saw it after
    /// launch: then a first outcome that wasn't worth telling (a usage limit
    /// with that toggle off, a routine skip) that the ledger later turns into
    /// one that is (failed) notifies too. A run already on disk at launch is
    /// never live, so the ledger catching up on it stays quiet.
    static func shouldNotify(previous: RunStatus?, run: Run, armed: Bool, preferences: Preferences,
                             shown: Shown? = nil, isLive: Bool = false) -> Bool {
        guard armed, shown == nil, previous != run.status,
              previous == nil || previous == .running || isLive else { return false }
        return isWorthTelling(run.status, preferences: preferences)
    }

    /// Whether the notification shown for this run should be taken back: the
    /// ledger changed its status to one not worth telling under the owner's
    /// toggles. It succeeded after all, was a routine skip, or became a usage
    /// limit with that toggle off; either way the "failed" in Notification
    /// Center is wrong, and its Run again would only start a run that fails
    /// again, at a cost. A toggle turned off with no change to the run leaves
    /// what's already shown alone.
    static func shouldWithdraw(shown: Shown?, run: Run, preferences: Preferences) -> Bool {
        guard let shown, run.status != shown.status else { return false }
        return !isWorthTelling(run.status, preferences: preferences)
    }

    /// The outcomes a notification is raised for, under the owner's toggles.
    static func isWorthTelling(_ status: RunStatus, preferences: Preferences) -> Bool {
        switch status {
        case .failure, .timeout, .orphaned:
            return preferences.onFailure
        // The run couldn't happen safely: something to fix, like a failure.
        case .skippedGates, .skippedSandbox, .skippedRuntime:
            return preferences.onFailure
        case .rateLimited:
            return preferences.onRateLimit
        // Routine: a budget, lock, lease or redundant skip, a success, or no outcome yet.
        case .success, .running, .scheduled, .skippedBudget, .skippedConcurrency, .skippedLease, .skippedRedundant:
            return false
        }
    }

    /// What a notification shown for a run says: its title (which names the
    /// status), the reason and whether it offers "Run again". All three can
    /// change once the run ledger has its row.
    struct Shown: Equatable, Sendable {
        let status: RunStatus
        let title: String
        let body: String
        let retrySlotKey: String?

        init(run: Run) {
            status = run.status
            title = NotificationService.title(for: run)
            body = NotificationService.body(for: run)
            retrySlotKey = NotificationService.retrySlotKey(for: run)
        }
    }

    /// Whether the notification already shown for this run (keyed by its id)
    /// should be replaced, quietly. The ledger often lands after the log: it
    /// can change the status itself (a log "failed" becomes a usage limit or a
    /// timeout; "didn't finish" becomes failed), and it brings the failure
    /// class and the schedule for Run again. Replaced whenever what it says
    /// changed, as long as the new status is still one the owner wants told.
    static func shouldUpdate(shown: Shown?, run: Run, preferences: Preferences) -> Bool {
        guard let shown, isWorthTelling(run.status, preferences: preferences) else { return false }
        return Shown(run: run) != shown
    }
}

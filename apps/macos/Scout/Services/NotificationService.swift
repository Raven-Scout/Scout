import Foundation
import UserNotifications

final class NotificationService: @unchecked Sendable {
    /// The `userInfo` key holding the run's id; `AppDelegate` reads it back
    /// when the notification is clicked and opens that run.
    nonisolated static let runIDKey = "runId"
    /// The `userInfo` key holding the schedule "Run again" fires
    /// (`Run.rerunSlotKey`); absent when the run has none.
    nonisolated static let slotKeyKey = "slotKey"

    /// A notification about a run that can be run again: it carries the
    /// "Run again" button. A run with no schedule of its own (an event run, a
    /// manual one) gets `runCategory`, without it.
    static let retryableRunCategory = "scout.run.retryable"
    static let runCategory = "scout.run"
    static let retryActionID = "scout.run.retry"

    /// Registers the two run categories. Call once at launch.
    static func registerCategories(on center: UNUserNotificationCenter = .current()) {
        let retry = UNNotificationAction(identifier: retryActionID, title: "Run again", options: [])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: retryableRunCategory, actions: [retry], intentIdentifiers: []),
            UNNotificationCategory(identifier: runCategory, actions: [], intentIdentifiers: []),
        ])
    }

    /// `quietly` replaces a notification already shown (same run, same
    /// identifier) without sounding again: the run ledger often names a
    /// failure's reason and schedule a moment after the run's log ended.
    func notify(run: Run, quietly: Bool = false) {
        let content = Self.content(for: run)
        if quietly { content.sound = nil }
        let req = UNNotificationRequest(identifier: run.id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    static func content(for run: Run) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "\(run.displayName) \(verb(for: run.status))"
        content.body = body(for: run)
        content.sound = .default
        content.interruptionLevel = (run.status == .rateLimited) ? .timeSensitive : .active
        var info: [String: String] = [runIDKey: run.id]
        if let slot = retrySlotKey(for: run) { info[slotKeyKey] = slot }
        content.userInfo = info
        content.categoryIdentifier = info[slotKeyKey] == nil ? runCategory : retryableRunCategory
        return content
    }

    /// The schedule a notification's "Run again" fires: the run's own
    /// (`Run.rerunSlotKey`, the slot RunDetailView's Run again fires), and
    /// only for a run that failed or didn't finish. Never for an event run or
    /// a manual one, and never for a skip: running it again would only skip
    /// again until its cause is fixed.
    nonisolated static func retrySlotKey(for run: Run) -> String? {
        guard RunOutcomeDisplay.isFailure(run.status) || run.status == .orphaned else { return nil }
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

    /// One line on why, then where to look. A failure's reason is its
    /// run-ledger failure class ("Claude login was busy"), never a line
    /// quoted from the log, which can hold the session's own output.
    nonisolated static func body(for run: Run) -> String {
        if let reason = run.status.skipReason { return "\(reason). Click to see the run." }
        if let reason = RunOutcomeDisplay.failureReason(run) { return "\(reason). Click to see the run and its log." }
        if run.status == .orphaned { return "It stopped without saying how it ended. Click to see the run and its log." }
        return "Click to see the run and its log."
    }

    private static func verb(for status: RunStatus) -> String {
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
    /// failure or skip).
    static func shouldNotify(previous: RunStatus?, run: Run, armed: Bool, preferences: Preferences) -> Bool {
        guard armed, previous == nil || previous == .running, previous != run.status else { return false }
        switch run.status {
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

    /// What a notification shown for a run says that can change once the run
    /// ledger has its row: the reason and whether it offers "Run again".
    struct Shown: Equatable, Sendable {
        let body: String
        let retrySlotKey: String?

        init(run: Run) {
            body = NotificationService.body(for: run)
            retrySlotKey = NotificationService.retrySlotKey(for: run)
        }
    }

    /// Whether a notification already shown for `run` should be replaced,
    /// quietly, because the ledger has since said why it failed or which
    /// schedule it ran for. Only while the run's status is the one notified.
    static func shouldUpdate(shown: Shown?, notifiedStatus: RunStatus?, run: Run) -> Bool {
        guard let shown, notifiedStatus == run.status else { return false }
        return Shown(run: run) != shown
    }
}

import Foundation
import UserNotifications

final class NotificationService: @unchecked Sendable {
    /// The `userInfo` key holding the run's id; `AppDelegate` reads it back
    /// when the notification is clicked and opens that run.
    static let runIDKey = "runId"

    func notify(run: Run) {
        let content = Self.content(for: run)
        let req = UNNotificationRequest(identifier: run.id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    static func content(for run: Run) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "\(run.displayName) \(verb(for: run.status))"
        content.body = body(for: run)
        content.sound = .default
        content.interruptionLevel = (run.status == .rateLimited) ? .timeSensitive : .active
        content.userInfo = [runIDKey: run.id]
        return content
    }

    /// The run a clicked notification is about, if it names one.
    static func runID(from userInfo: [AnyHashable: Any]) -> Run.ID? {
        (userInfo[runIDKey] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }

    private static func body(for run: Run) -> String {
        if let reason = run.status.skipReason { return "\(reason). Click to see the run." }
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
}

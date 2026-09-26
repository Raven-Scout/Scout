import Foundation
import UserNotifications

final class NotificationService: @unchecked Sendable {
    func notify(run: Run) {
        let content = UNMutableNotificationContent()
        content.title = "Scout — \(run.type.rawValue) \(verb(for: run.status))"
        if let e = run.exitCode {
            content.body = "Exit code \(e). Log: \(run.logPath.lastPathComponent)"
        } else {
            content.body = "Log: \(run.logPath.lastPathComponent)"
        }
        content.sound = .default
        content.interruptionLevel = (run.status == .rateLimited) ? .timeSensitive : .active
        content.userInfo = ["runId": run.id]
        let req = UNNotificationRequest(identifier: run.id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    private func verb(for status: RunStatus) -> String {
        switch status {
        case .success:            return "succeeded"
        case .failure:            return "failed"
        case .timeout:            return "timed out"
        case .rateLimited:        return "rate-limited"
        case .skippedBudget:      return "skipped (budget)"
        case .skippedConcurrency: return "skipped (concurrency)"
        default:                  return "state changed"
        }
    }
}

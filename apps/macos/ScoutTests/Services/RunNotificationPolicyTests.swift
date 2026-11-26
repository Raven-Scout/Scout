import Foundation
import Testing
import UserNotifications
@testable import Scout

/// Card C3: the Settings ▸ Notifications toggles are read, and a clicked
/// notification carries the run's id back to the app.
@Suite("RunNotificationPolicy")
struct RunNotificationPolicyTests {
    let both = RunNotificationPolicy.Preferences(onFailure: true, onRateLimit: true)
    let none = RunNotificationPolicy.Preferences(onFailure: false, onRateLimit: false)

    private func notifies(_ status: RunStatus, from previous: RunStatus? = .running, armed: Bool = true,
                          _ prefs: RunNotificationPolicy.Preferences) -> Bool {
        RunNotificationPolicy.shouldNotify(previous: previous, run: Run.make(status: status), armed: armed, preferences: prefs)
    }

    @Test("failures follow the failure toggle")
    func failureToggle() {
        for status in [RunStatus.failure, .timeout, .orphaned, .skippedGates, .skippedSandbox, .skippedRuntime] {
            #expect(notifies(status, both), "\(status)")
            #expect(!notifies(status, none), "\(status)")
            #expect(!notifies(status, .init(onFailure: false, onRateLimit: true)), "\(status)")
        }
    }

    @Test("usage limits follow the rate-limit toggle")
    func rateLimitToggle() {
        #expect(notifies(.rateLimited, both))
        #expect(notifies(.rateLimited, .init(onFailure: false, onRateLimit: true)))
        #expect(!notifies(.rateLimited, .init(onFailure: true, onRateLimit: false)))
    }

    @Test("successes and routine skips never notify")
    func routine() {
        for status in [RunStatus.success, .running, .scheduled, .skippedBudget, .skippedConcurrency, .skippedLease, .skippedRedundant] {
            #expect(!notifies(status, both), "\(status)")
        }
    }

    @Test("only new outcomes notify: runs on disk at launch, and repeats, don't")
    func onlyNewOutcomes() {
        #expect(!notifies(.failure, from: nil, armed: false, both))       // launch-time load
        #expect(notifies(.failure, from: nil, armed: true, both))         // a fast failure, first seen finished
        #expect(!notifies(.failure, from: .failure, both))                // already told
        #expect(!notifies(.orphaned, from: .failure, both))               // not from running
    }

    @Test("the notification names the run in plain words and carries its id")
    @MainActor
    func content() {
        let run = Run.make(type: .research, status: .skippedSandbox)
        let content = NotificationService.content(for: run)
        #expect(content.title == "Research was skipped")
        #expect(content.body.hasPrefix("Couldn't protect this run"))
        #expect(!content.title.contains("research "))   // no raw enum names
        #expect(NotificationService.runID(from: content.userInfo) == run.id)
        #expect(NotificationService.runID(from: [:]) == nil)
        #expect(NotificationService.runID(from: [NotificationService.runIDKey: ""]) == nil)
    }
}

import Testing
import Foundation
import Combine
import UserNotifications
@testable import Scout

@MainActor
private final class ReminderAppController: AppUpdateController {
    let isEnabled = true
    let currentVersion: String? = "0.18.17"
    var startsCheck = true
    var canCheckForUpdates: AnyPublisher<Bool, Never> { Just(true).eraseToAnyPublisher() }
    @discardableResult
    func checkForUpdates() -> Bool { startsCheck }
}

@MainActor
private final class FakeAppUpdateNotifier: AppUpdateNotifying {
    var posted: [String] = []
    var withdrawals = 0
    func postAppUpdate(version: String) { posted.append(version) }
    func withdrawAppUpdate() { withdrawals += 1 }
}

private struct NoPluginChecker: PluginUpdateChecking {
    func check(engine: EngineState) async -> PluginUpdateResult { .notApplicable }
}

/// How a found app update reaches the user when Sparkle's own scheduled check
/// finds it (gentle reminders): the banner on every page and one macOS
/// notification per version. Checks the user starts are Sparkle's to show.
@Suite("UpdateService app-update reminders")
@MainActor
struct AppUpdateReminderTests {
    private func make() -> (UpdateService, ReminderAppController) {
        make(notifier: FakeAppUpdateNotifier())
    }

    private func make(notifier: FakeAppUpdateNotifier?, defaults: UserDefaults? = nil)
        -> (UpdateService, ReminderAppController) {
        var controller: ReminderAppController!
        let service = UpdateService(
            pluginChecker: NoPluginChecker(),
            engineStates: Empty<EngineState, Never>().eraseToAnyPublisher(),
            appUpdateNotifier: notifier,
            defaults: defaults ?? Self.freshDefaults()
        ) { _ in
            let c = ReminderAppController()
            controller = c
            return c
        }
        return (service, controller)
    }

    private static func freshDefaults() -> UserDefaults {
        let suite = "AppUpdateReminderTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return defaults
    }

    // MARK: Notification

    @Test func scheduledFindPostsTheNotificationOnce() {
        let notifier = FakeAppUpdateNotifier()
        let (service, _) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        service.applyAppEvent(.cycleEnded)
        // The next hourly check finds the same version again.
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(notifier.posted == ["0.18.18"])
    }

    @Test func onceAVersionSurvivesARelaunch() {
        let defaults = Self.freshDefaults()
        let first = FakeAppUpdateNotifier()
        let (service1, _) = make(notifier: first, defaults: defaults)
        service1.applyAppEvent(.found(version: "0.18.18"))
        let second = FakeAppUpdateNotifier()
        let (service2, _) = make(notifier: second, defaults: defaults)
        service2.applyAppEvent(.found(version: "0.18.18"))
        #expect(first.posted == ["0.18.18"])
        #expect(second.posted.isEmpty)
    }

    @Test func aNewerVersionPostsAgain() {
        let notifier = FakeAppUpdateNotifier()
        let (service, _) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        service.applyAppEvent(.found(version: "0.18.19"))
        #expect(notifier.posted == ["0.18.18", "0.18.19"])
    }

    @Test func aFindTheUserAskedForPostsNothingAndCountsAsTold() {
        let notifier = FakeAppUpdateNotifier()
        let (service, _) = make(notifier: notifier)
        service.checkApp()                                   // user: Check for Updates…
        service.applyAppEvent(.found(version: "0.18.18"))    // Sparkle shows its own alert
        service.applyAppEvent(.cycleEnded)                   // "Remind Me Later"
        service.applyAppEvent(.found(version: "0.18.18"))    // next scheduled check
        #expect(notifier.posted.isEmpty)
        #expect(service.appUpdate.state == .available)
    }

    @Test func checkingWithdrawsTheNotification() {
        let notifier = FakeAppUpdateNotifier()
        let (service, _) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        service.checkApp()                                   // banner's Update Now, or the notification's click
        #expect(notifier.withdrawals == 1)
    }

    @Test func aCheckThatDidNotStartLeavesTheNotification() {
        let notifier = FakeAppUpdateNotifier()
        let (service, controller) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        controller.startsCheck = false                       // Sparkle declined (downloading in the background)
        service.checkApp()
        #expect(notifier.withdrawals == 0)
        #expect(service.appUpdate.state == .available)
    }

    @Test(arguments: [AppUpdateEvent.upToDate, .skipped])
    func upToDateOrSkippedWithdrawsTheNotification(event: AppUpdateEvent) {
        let notifier = FakeAppUpdateNotifier()
        let (service, _) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        service.applyAppEvent(event)
        #expect(notifier.withdrawals == 1)
    }

    @Test func withoutANotifierDefaultsAreNeverWritten() {
        let defaults = Self.freshDefaults()
        let (service, _) = make(notifier: nil, defaults: defaults)
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(defaults.string(forKey: UpdateService.lastNotifiedAppVersionKey) == nil)
    }

    // MARK: Banner

    @Test func bannerShowsWhileAnUpdateIsAvailable() {
        let (service, _) = make()
        #expect(service.showsAppUpdateBanner == false)
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(service.showsAppUpdateBanner)
        service.applyAppEvent(.upToDate)
        #expect(service.showsAppUpdateBanner == false)
    }

    @Test func bannerHidesWhileSparklesAlertIsUp() {
        let (service, _) = make()
        service.applyAppEvent(.found(version: "0.18.18"))
        service.checkApp()
        #expect(service.showsAppUpdateBanner == false)
        service.applyAppEvent(.cycleEnded)                   // dismissed: back to available
        #expect(service.showsAppUpdateBanner)
    }

    @Test func dismissingHidesThatVersionOnly() {
        let (service, _) = make()
        service.applyAppEvent(.found(version: "0.18.18"))
        service.dismissAppUpdateBanner()
        #expect(service.showsAppUpdateBanner == false)
        #expect(service.anyUpdateAvailable)                  // badge and menu-bar dot stay
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(service.showsAppUpdateBanner == false)
        service.applyAppEvent(.found(version: "0.18.19"))
        #expect(service.showsAppUpdateBanner)
    }
}

@Suite("App update notification and banner text")
struct AppUpdateNotificationContentTests {
    @Test func contentNamesTheVersionAndCarriesIt() {
        let content = AppUpdateNotifier.content(version: "0.18.18")
        #expect(content.title == "Scout 0.18.18 is ready")
        #expect(content.userInfo[AppUpdateNotifier.versionKey] as? String == "0.18.18")
        #expect(AppUpdateNotifier.isAppUpdate(content.userInfo))
    }

    @Test func aRunNotificationIsNotTheUpdateOne() {
        #expect(AppUpdateNotifier.isAppUpdate([NotificationService.runIDKey: "run-1"]) == false)
    }

    @Test func bannerText() {
        #expect(AppUpdateBanner.title(version: "0.18.18") == "Scout 0.18.18 is ready")
        #expect(AppUpdateBanner.message(current: "0.18.17")
            == "You have 0.18.17. Updating relaunches Scout in a few seconds.")
        #expect(AppUpdateBanner.message(current: nil) == "Updating relaunches Scout in a few seconds.")
    }
}

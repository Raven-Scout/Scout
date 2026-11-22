import Testing
import Foundation
import Combine
import UserNotifications
@testable import Scout

@MainActor
private final class ReminderAppController: AppUpdateController {
    let isEnabled: Bool
    let currentVersion: String? = "0.18.17"
    /// Whether `checkForUpdates()` starts one; follows `canCheck` unless set.
    var startsCheck: Bool?
    var checks = 0
    let canCheck = CurrentValueSubject<Bool, Never>(true)

    init(isEnabled: Bool = true) { self.isEnabled = isEnabled }

    var canCheckForUpdates: AnyPublisher<Bool, Never> { canCheck.eraseToAnyPublisher() }
    @discardableResult
    func checkForUpdates() -> Bool {
        guard startsCheck ?? canCheck.value else { return false }
        checks += 1
        return true
    }
}

@MainActor
private final class FakeAppUpdateNotifier: AppUpdateNotifying {
    var posted: [String] = []
    var withdrawals = 0
    /// False: macOS refused the post (notifications off for Scout).
    var delivers = true
    func postAppUpdate(version: String, onPosted: @escaping @MainActor () -> Void) {
        posted.append(version)
        if delivers { onPosted() }
    }
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

    private func make(notifier: FakeAppUpdateNotifier?, defaults: UserDefaults? = nil,
                      enabled: Bool = true) -> (UpdateService, ReminderAppController) {
        var controller: ReminderAppController!
        let service = UpdateService(
            pluginChecker: NoPluginChecker(),
            engineStates: Empty<EngineState, Never>().eraseToAnyPublisher(),
            appUpdateNotifier: notifier,
            defaults: defaults ?? Self.freshDefaults()
        ) { _ in
            let c = ReminderAppController(isEnabled: enabled)
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

    @Test func aPostMacOSRefusedIsNotCountedAsTold() {
        let defaults = Self.freshDefaults()
        let notifier = FakeAppUpdateNotifier()
        notifier.delivers = false                            // notifications off for Scout
        let (service, _) = make(notifier: notifier, defaults: defaults)
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(defaults.string(forKey: UpdateService.lastNotifiedAppVersionKey) == nil)
        notifier.delivers = true                             // the user turns them on
        service.applyAppEvent(.found(version: "0.18.18"))    // next hourly find
        service.applyAppEvent(.found(version: "0.18.18"))
        #expect(notifier.posted == ["0.18.18", "0.18.18"])
        #expect(defaults.string(forKey: UpdateService.lastNotifiedAppVersionKey) == "0.18.18")
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
        service.showAppUpdate()                              // banner's Update now, or the notification's click
        #expect(notifier.withdrawals == 1)
    }

    @Test func aCheckThatDidNotStartLeavesTheNotification() {
        let notifier = FakeAppUpdateNotifier()
        let (service, controller) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        controller.startsCheck = false                       // Sparkle declined
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

    // MARK: Update now while Sparkle is busy

    @Test func aClickWhileSparkleIsBusyWaitsAndThenOpensTheUpdate() {
        let notifier = FakeAppUpdateNotifier()
        let (service, controller) = make(notifier: notifier)
        service.applyAppEvent(.found(version: "0.18.18"))
        controller.canCheck.send(false)                      // downloading in the background
        service.showAppUpdate()
        #expect(controller.checks == 0)
        #expect(service.appUpdateShowPending)
        #expect(service.showsAppUpdateBanner)                // still up, saying it's on its way
        #expect(notifier.withdrawals == 0)
        controller.canCheck.send(true)                       // download done, update held
        #expect(controller.checks == 1)
        #expect(service.appUpdateShowPending == false)
        #expect(service.appUpdate.state == .checking)
        #expect(notifier.withdrawals == 1)
    }

    @Test func aClickSparkleDeclinesDespiteCanCheckStillWaits() {
        let (service, controller) = make()
        service.applyAppEvent(.found(version: "0.18.18"))
        controller.startsCheck = false                       // canCheck was stale
        service.showAppUpdate()
        #expect(service.appUpdateShowPending)
        controller.startsCheck = nil
        controller.canCheck.send(true)
        #expect(controller.checks == 1)
        #expect(service.appUpdateShowPending == false)
    }

    @Test(arguments: [AppUpdateEvent.upToDate, .skipped])
    func aWaitingClickIsDroppedOnceThereIsNothingToShow(event: AppUpdateEvent) {
        let (service, controller) = make()
        service.applyAppEvent(.found(version: "0.18.18"))
        controller.canCheck.send(false)
        service.showAppUpdate()
        service.applyAppEvent(event)
        #expect(service.appUpdateShowPending == false)
        controller.canCheck.send(true)
        #expect(controller.checks == 0)
    }

    @Test func aDisabledUpdaterNeverWaits() {
        let (service, controller) = make(notifier: nil, enabled: false)
        service.showAppUpdate()
        #expect(service.appUpdateShowPending == false)
        #expect(controller.checks == 0)
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
        service.showAppUpdate()
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
        #expect(content.body == "Click to see what's new and install it.")
        #expect(content.userInfo[AppUpdateNotifier.versionKey] as? String == "0.18.18")
        #expect(AppUpdateNotifier.isAppUpdate(content.userInfo))
    }

    @Test func aRunNotificationIsNotTheUpdateOne() {
        #expect(AppUpdateNotifier.isAppUpdate([NotificationService.runIDKey: "run-1"]) == false)
    }

    @Test(arguments: [
        (UNAuthorizationStatus.authorized, true),
        (.provisional, true),
        (.denied, false),
        (.notDetermined, false),
    ])
    func postsOnlyWhereMacOSShowsThem(status: UNAuthorizationStatus, allowed: Bool) {
        #expect(AppUpdateNotifier.allowsPosting(status) == allowed)
    }

    @Test func bannerText() {
        #expect(AppUpdateBanner.title(version: "0.18.18") == "Scout 0.18.18 is ready")
        #expect(AppUpdateBanner.message(current: "0.18.17")
            == "You have 0.18.17. Click Update now to see what's new and install it.")
        #expect(AppUpdateBanner.message(current: nil) == "Click Update now to see what's new and install it.")
        #expect(AppUpdateBanner.pendingMessage == "Getting the update ready. It opens as soon as it's done.")
    }
}

/// Where a click on one of Scout's notifications goes (`AppDelegate`).
@Suite("Notification click routing")
struct NotificationClickRoutingTests {
    private let open = UNNotificationDefaultActionIdentifier

    @Test func theUpdateNotificationShowsTheUpdate() {
        let info = AppUpdateNotifier.content(version: "0.18.18").userInfo
        #expect(AppDelegate.click(actionIdentifier: open, userInfo: info) == .showAppUpdate)
    }

    @Test func theUpdateIsRoutedAheadOfARun() {
        let info: [AnyHashable: Any] = [AppUpdateNotifier.versionKey: "0.18.18",
                                        NotificationService.runIDKey: "run-1"]
        #expect(AppDelegate.click(actionIdentifier: open, userInfo: info) == .showAppUpdate)
    }

    @Test func aRunNotificationOpensItsRun() {
        let info: [AnyHashable: Any] = [NotificationService.runIDKey: "run-1"]
        #expect(AppDelegate.click(actionIdentifier: open, userInfo: info) == .openRun("run-1"))
    }

    @Test func runAgainFiresTheSlot() {
        let info: [AnyHashable: Any] = [NotificationService.runIDKey: "run-1",
                                        NotificationService.slotKeyKey: "morning"]
        #expect(AppDelegate.click(actionIdentifier: NotificationService.retryActionID, userInfo: info)
            == .retrySlot("morning"))
    }

    @Test func anythingElseDoesNothing() {
        #expect(AppDelegate.click(actionIdentifier: open, userInfo: [:]) == .nothing)
        #expect(AppDelegate.click(actionIdentifier: UNNotificationDismissActionIdentifier,
                                  userInfo: [AppUpdateNotifier.versionKey: "0.18.18"]) == .nothing)
    }
}

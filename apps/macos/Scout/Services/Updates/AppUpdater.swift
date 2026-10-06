import Foundation
import Sparkle

/// Thin adapter around Sparkle's standard updater controller. The only file
/// that imports Sparkle. Sparkle owns detection, download, install and
/// relaunch (and shows its own dialogs); this class forwards "check now" and
/// maps delegate callbacks onto `AppUpdateEvent`s for `UpdateService`. The
/// feed is the latest release's `appcast.xml` asset (see `UpdateFeed`).
///
/// Disabled in Debug builds: a dev build lives in DerivedData under
/// `com.scout.Scout.dev`, and letting it replace itself with the release
/// `com.scout.Scout` bundle would be confusing at best. The controller is
/// still constructed — so this code compiles and runs in Debug/CI — it is
/// just never started.
@MainActor
final class AppUpdater: AppUpdateController {
    /// True in Release builds only. The single `#if DEBUG` in the updater.
    static let updatesEnabledForThisBuild: Bool = {
        #if DEBUG
        return false
        #else
        return true
        #endif
    }()

    let isEnabled: Bool
    private let controller: SPUStandardUpdaterController
    private let delegate: AppUpdaterDelegate

    init(enabled: Bool = AppUpdater.updatesEnabledForThisBuild,
         onEvent: @escaping @MainActor (AppUpdateEvent) -> Void) {
        isEnabled = enabled
        delegate = AppUpdaterDelegate(onEvent: onEvent)
        controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: delegate,
            userDriverDelegate: nil
        )
        if enabled {
            controller.startUpdater()
        }
    }

    var currentVersion: String? {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
    }

    /// User-initiated: Sparkle shows "checking…", then found / up to date /
    /// error UI itself. Scheduled checks are Sparkle's own (launch + daily).
    func checkForUpdates() {
        guard isEnabled else { return }
        controller.updater.checkForUpdates()
    }
}

/// Sparkle calls its delegate on the main thread; we assert that and hop the
/// result into the service. Sparkle holds the delegate weakly, so
/// `AppUpdater` keeps the strong reference.
final class AppUpdaterDelegate: NSObject, @preconcurrency SPUUpdaterDelegate {
    private let onEvent: @MainActor (AppUpdateEvent) -> Void

    init(onEvent: @escaping @MainActor (AppUpdateEvent) -> Void) {
        self.onEvent = onEvent
    }

    private nonisolated func emit(_ event: AppUpdateEvent) {
        MainActor.assumeIsolated { onEvent(event) }
    }

    nonisolated func feedURLString(for updater: SPUUpdater) -> String? {
        UpdateFeed.overrideURLString(environment: ProcessInfo.processInfo.environment)
    }

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        emit(.found(version: item.displayVersionString))
    }

    nonisolated func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: any Error) {
        emit(.upToDate)
    }

    nonisolated func updater(_ updater: SPUUpdater, didAbortWithError error: any Error) {
        // Sparkle reports user cancellation through this hook too; keep the
        // Settings row honest but never alarming — it's shown as "Couldn't
        // check", Sparkle already showed any dialog it wanted to.
        emit(.failed(error.localizedDescription))
    }
}

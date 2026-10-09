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
    /// error UI itself, and always finishes the cycle through the delegate's
    /// `didFinishUpdateCycleForUpdateCheck:error:` — including a silent
    /// cancel. Scheduled checks are Sparkle's own (launch + daily).
    ///
    /// Returns whether a check actually started. `canCheckForUpdates` is
    /// false (and Sparkle declines silently, with no delegate callback at
    /// all) while a session is already in progress or a permission prompt is
    /// showing — `UpdateService` uses the return value to avoid marking
    /// `.checking` for a check that never happened.
    @discardableResult
    func checkForUpdates() -> Bool {
        guard isEnabled, controller.updater.canCheckForUpdates else { return false }
        controller.updater.checkForUpdates()
        return true
    }

    /// Sparkle-free so it can be unit tested without starting Sparkle or
    /// importing it; the Sparkle constants are resolved into plain values
    /// at the one call site that has them, `AppUpdaterDelegate.updater(_:
    /// didFinishUpdateCycleFor:error:)` below.
    ///
    /// - nil error: the cycle ended with nothing further to report — a
    ///   silent cancel ("checking…" dismissed, or "install later" on a found
    ///   update) or a normal finish after `.found`/`.upToDate` already fired.
    /// - `SUSparkleErrorDomain` + `SUNoUpdateError` (1001): no update was
    ///   found. (`updaterDidNotFindUpdate(_:error:)` already emits
    ///   `.upToDate` for this same cycle; mapping it here too keeps this
    ///   function's result correct standalone and idempotent in
    ///   `UpdateService.applyAppEvent`.)
    /// - `SUInstallationCanceledError` (4007) or
    ///   `SUInstallationAuthorizeLaterError` (4008): the user canceled the
    ///   authorization prompt, or deferred installation — a cancel, not a
    ///   failure.
    /// - anything else: a genuine failure.
    nonisolated static func cycleEndEvent(errorDomain: String?, code: Int?, localizedDescription: String) -> AppUpdateEvent {
        guard let domain = errorDomain, let code else { return .cycleEnded }
        guard domain == sparkleErrorDomain else { return .failed(localizedDescription) }
        switch code {
        case noUpdateErrorCode:
            return .upToDate
        case installationCanceledErrorCode, installationAuthorizeLaterErrorCode:
            return .cycleEnded
        default:
            return .failed(localizedDescription)
        }
    }

    // `nonisolated`: plain Sendable values (a String, three Ints) read only
    // from the `nonisolated static func` above — without this, the
    // MainActor isolation this class defaults to would make them
    // inaccessible from that nonisolated context.
    private nonisolated static let sparkleErrorDomain = SUSparkleErrorDomain as String
    private nonisolated static let noUpdateErrorCode = Int(SUError.noUpdateError.rawValue)
    private nonisolated static let installationCanceledErrorCode = Int(SUError.installationCanceledError.rawValue)
    private nonisolated static let installationAuthorizeLaterErrorCode = Int(SUError.installationAuthorizeLaterError.rawValue)
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

    /// Fires for *every* completed update cycle, found-or-not, error-or-not
    /// — including a silent cancel (dismissing the "checking…" sheet or the
    /// found-update alert aborts with a `nil` error, which never reaches
    /// `didAbortWithError:`). This is the only reliable terminal signal, so
    /// it — not `didAbortWithError:` — is what resolves `.checking`.
    nonisolated func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        let nsError = error as NSError?
        emit(AppUpdater.cycleEndEvent(
            errorDomain: nsError?.domain,
            code: nsError?.code,
            localizedDescription: nsError?.localizedDescription ?? ""
        ))
    }
}

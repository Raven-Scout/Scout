import Testing
import Foundation
import Combine
@testable import Scout

/// Runs in the Debug test host, where the updater must never start. The
/// enabled path needs a signed Release build and a real appcast — that is the
/// rc.1 → rc.2 rehearsal in the spec, not a unit test.
@Suite("AppUpdater (disabled build)")
@MainActor
struct AppUpdaterTests {
    @Test func debugBuildsAreDisabledByDefault() {
        #expect(AppUpdater.updatesEnabledForThisBuild == false)
        #expect(AppUpdater(onEvent: { _ in }).isEnabled == false)
    }

    @Test func checkForUpdatesReturnsFalseAndEmitsNothingWhenDisabled() {
        var events: [AppUpdateEvent] = []
        let updater = AppUpdater(enabled: false) { events.append($0) }
        let started = updater.checkForUpdates()   // must not start Sparkle, show UI, emit, or crash
        #expect(started == false)
        #expect(events.isEmpty)
    }

    /// A disabled (Debug) updater is never started, so "Check for Updates…"
    /// must read as unavailable regardless of Sparkle's own flag.
    @Test func disabledUpdaterPublishesCannotCheck() {
        let updater = AppUpdater(enabled: false) { _ in }
        var values: [Bool] = []
        let cancellable = updater.canCheckForUpdates.sink { values.append($0) }
        cancellable.cancel()
        #expect(values == [false])
    }
}

/// `AppUpdater.cycleEndEvent` is the Sparkle-free mapping from
/// `didFinishUpdateCycleForUpdateCheck:error:` onto `AppUpdateEvent`. Values
/// are pinned as plain literals (`SUErrors.h`: `SUSparkleErrorDomain` =
/// "SUSparkleErrorDomain", `SUNoUpdateError` = 1001,
/// `SUInstallationCanceledError` = 4007, `SUInstallationAuthorizeLaterError`
/// = 4008) so this suite needs no Sparkle import.
@Suite("AppUpdater.cycleEndEvent")
struct AppUpdaterCycleEndEventTests {
    private static let sparkleDomain = "SUSparkleErrorDomain"

    @Test func nilErrorIsACleanCycleEnd() {
        let event = AppUpdater.cycleEndEvent(errorDomain: nil, code: nil, localizedDescription: "")
        #expect(event == .cycleEnded)
    }

    @Test func noUpdateErrorMapsToUpToDate() {
        let event = AppUpdater.cycleEndEvent(errorDomain: Self.sparkleDomain, code: 1001, localizedDescription: "No update found.")
        #expect(event == .upToDate)
    }

    @Test func installationCanceledMapsToCycleEndedNotFailed() {
        let event = AppUpdater.cycleEndEvent(errorDomain: Self.sparkleDomain, code: 4007, localizedDescription: "The user canceled the update.")
        #expect(event == .cycleEnded)
    }

    @Test func installationAuthorizeLaterMapsToCycleEndedNotFailed() {
        let event = AppUpdater.cycleEndEvent(errorDomain: Self.sparkleDomain, code: 4008, localizedDescription: "Authorization deferred.")
        #expect(event == .cycleEnded)
    }

    @Test func anyOtherErrorMapsToFailedWithItsDescription() {
        let event = AppUpdater.cycleEndEvent(errorDomain: Self.sparkleDomain, code: 2001, localizedDescription: "The update couldn't be downloaded.")
        #expect(event == .failed("The update couldn't be downloaded."))
    }

    @Test func sameCodeUnderADifferentDomainIsStillAFailure() {
        // A code that collides with 1001/4007/4008 in some unrelated error
        // domain must not be mistaken for Sparkle's own special-cased codes.
        let event = AppUpdater.cycleEndEvent(errorDomain: "NSURLErrorDomain", code: 4007, localizedDescription: "Unrelated error.")
        #expect(event == .failed("Unrelated error."))
    }
}

/// `AppUpdater.userChoiceEvent` is the Sparkle-free mapping from
/// `updater:userDidMakeChoice:forUpdate:state:` onto `AppUpdateEvent`. Raw
/// values pinned from `SPUUserUpdateState.h` (2.10.0):
/// `SPUUserUpdateChoiceSkip` = 0, `…Install` = 1, `…Dismiss` = 2.
@Suite("AppUpdater.userChoiceEvent")
struct AppUpdaterUserChoiceEventTests {
    @Test func skipThisVersionMapsToSkipped() {
        #expect(AppUpdater.userChoiceEvent(choice: 0) == .skipped)
    }

    @Test func installAndDismissMapToNothing() {
        // Install: Sparkle carries on and relaunches. Dismiss ("Remind Me
        // Later"): the update is still available, so the badge stays.
        #expect(AppUpdater.userChoiceEvent(choice: 1) == nil)
        #expect(AppUpdater.userChoiceEvent(choice: 2) == nil)
    }
}

/// Sparkle calls optional delegate methods only if the delegate responds to
/// their exact Objective-C selectors; a Swift spelling that doesn't import
/// to that selector compiles fine and silently never fires.
@Suite("AppUpdaterDelegate selectors")
@MainActor
struct AppUpdaterDelegateSelectorTests {
    @Test(arguments: [
        "updater:userDidMakeChoice:forUpdate:state:",
        "updater:didFinishUpdateCycleForUpdateCheck:error:",
        "updater:didFindValidUpdate:",
        "updaterDidNotFindUpdate:error:",
        "feedURLStringForUpdater:",
    ])
    func delegateRespondsTo(selector: String) {
        let delegate = AppUpdaterDelegate(onEvent: { _ in })
        #expect(delegate.responds(to: NSSelectorFromString(selector)))
    }
}

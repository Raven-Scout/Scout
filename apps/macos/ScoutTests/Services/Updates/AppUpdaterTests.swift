import Testing
import Foundation
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

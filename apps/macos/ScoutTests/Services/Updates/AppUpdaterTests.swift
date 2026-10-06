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

    @Test func reportsTheBundleVersion() {
        let expected = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
        #expect(AppUpdater(enabled: false, onEvent: { _ in }).currentVersion == expected)
    }

    @Test func checkForUpdatesIsANoOpWhenDisabled() {
        var events: [AppUpdateEvent] = []
        let updater = AppUpdater(enabled: false) { events.append($0) }
        updater.checkForUpdates()   // must not start Sparkle, show UI, emit, or crash
        #expect(events.isEmpty)
    }
}

import Testing
import Foundation
@testable import Scout

/// `Configuration.production()` reads the real home by design — never called
/// from a test. These exercise `testHost()` and `testing(...)` only, which
/// must point `engineLayout` at a temp directory so a test run can never
/// locate (or doctor-check) the user's real engine.
@MainActor
@Suite("AppState engine wiring")
struct AppStateEngineWiringTests {
    @Test func testHostEngineLayoutIsUnderTempNotRealHome() {
        let layout = AppState.Configuration.testHost().engineLayout
        let realHome = FileManager.default.homeDirectoryForCurrentUser
        #expect(layout.home != realHome)
        #expect(layout.home.path.hasPrefix(FileManager.default.temporaryDirectory.path))
    }

    @Test func testingEngineLayoutIsUnderTempNotRealHome() {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let layout = AppState.Configuration.testing(scoutDirectory: tmp).engineLayout
        let realHome = FileManager.default.homeDirectoryForCurrentUser
        #expect(layout.home != realHome)
        #expect(layout.home.path.hasPrefix(tmp.path))
    }

    @Test func constructionExposesInitialStateAndRunsNoDoctorCall() {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let rule = RuleBasedRunner()
        let configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: rule)
        let appState = AppState(configuration: configuration)
        #expect(appState.engineHealth.state == configuration.initialEngineState)
        #expect(rule.calls.isEmpty)
    }
}

import Testing
import Foundation
@testable import Scout

@Suite("AppState.resolveScoutDirectory")
struct AppStateVaultResolutionTests {
    let home = URL(fileURLWithPath: "/Users/alex")
    func defaults(_ value: String?) -> UserDefaults {
        let d = UserDefaults(suiteName: "AppStateVaultResolutionTests-\(UUID().uuidString)")!
        if let value { d.set(value, forKey: "scoutDataDir") }
        return d
    }
    let pointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: "/e", python: "/p", scoutctl: "/s",
                                vault: "/Users/alex/Vaults/Work", managedBy: "scout-app", writtenAt: "")

    @Test func userDefaultWinsAndExpandsTilde() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("~/Custom"), pointer: pointer, home: home).path == "/Users/alex/Custom")
    }
    @Test func pointerVaultIsSecond() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults(nil), pointer: pointer, home: home).path == "/Users/alex/Vaults/Work")
    }
    @Test func blankDefaultFallsThrough() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("  "), pointer: nil, home: home).path == "/Users/alex/Scout")
    }
}

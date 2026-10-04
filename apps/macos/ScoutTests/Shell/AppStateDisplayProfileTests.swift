import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("AppState display profile")
struct AppStateDisplayProfileTests {
    @Test func theProfileLivesInTheVaultRoot() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("vault-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        try Data(#"{"schema":1,"actionItems":{"defaultView":"board"}}"#.utf8)
            .write(to: vault.appendingPathComponent("scout-profile.json"))

        let state = AppState(configuration: .testing(scoutDirectory: vault))
        #expect(state.displayProfileService.fileURL.deletingLastPathComponent().path == vault.resolvingRealPath().path)
        #expect(state.displayProfileService.currentView == .board)
    }
}

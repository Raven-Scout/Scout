import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("AppState — sessions wiring", .serialized)
struct AppStateSessionsTests {

    @Test func theSidebarBadgeFollowsTheIndex() async throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-appstate-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        let state = AppState(configuration: .testing(
            scoutDirectory: vault,
            runner: ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))))
        #expect(state.sessionsNeedsYouCount == 0)
        await state.sessionIndexService.refreshFast()
        await waitUntil("the badge never picked up the needs-you count") { state.sessionsNeedsYouCount == 1 }
    }

    @Test func noTestGraphWatchesTheRealSources() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-appstate-\(UUID().uuidString)")
        #expect(AppState.Configuration.testing(scoutDirectory: vault).agentSessionWatchRoots.isEmpty)
        #expect(AppState.Configuration.testHost().agentSessionWatchRoots.isEmpty)
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        #expect(SessionsRefresh.productionWatchRoots().map(\.path) == [
            home + "/Library/Application Support/Claude/claude-code-sessions",
            home + "/.claude/sessions",
            home + "/.claude/projects",
        ])
    }
}

import AppKit
import SwiftUI
import Testing
@testable import Scout

extension SessionsFixture {
    /// A service that has already run one fast build answering `result`. Its
    /// index file is in a temp folder and nothing is watched.
    @MainActor
    static func loadedService(answering result: ProcessResult) async -> SessionIndexService {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-smoke-\(UUID().uuidString)")
        let service = SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            argumentsPrefix: [],
            runner: ScriptedSessionsRunner(fast: result),
            fileEvents: NoopFS(),
            indexFile: vault.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: []
        ))
        await service.refreshFast()
        return service
    }
}

/// Renders the board, table, cards, header and pills against the fixture
/// (pattern: ViewSmokeTests). Every body and branch must evaluate and lay out.
@MainActor
@Suite("View smoke — sessions board", .serialized)
struct SessionsBoardSmokeTests {
    private let now = SessionsFixture.now

    @Test("the board renders every lane with each filter")
    func boardRenders() throws {
        let index = try SessionsFixture.index()
        var everything = SessionsFilter()
        everything.showScoutRuns = true
        everything.showRecentlyDone = true
        var nothing = SessionsFilter()
        nothing.search = "no session is called this"
        for filter in [SessionsFilter(), everything, nothing] {
            ViewHost.render(SessionsBoardView(index: index, filter: filter, now: now, selectedID: .constant("local_A")))
        }
    }

    @Test("the table renders every row")
    func tableRenders() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showScoutRuns = true
        filter.showRecentlyDone = true
        ViewHost.render(SessionsTableView(
            rows: SessionsLayout.tableRows(index: index, filter: filter, now: now),
            now: now,
            selectedID: .constant("local_R")))
    }

    @Test("every fixture session renders as a card")
    func cardsRender() throws {
        for session in try SessionsFixture.index().sessions {
            ViewHost.render(SessionCardView(session: session, now: now, parentTitle: "Fix the parser",
                                            isSelected: true, isHighlighted: true),
                            size: CGSize(width: 260, height: 180))
        }
    }

    @Test("the header renders with chips, menus and freshness")
    func headerRenders() async throws {
        let service = await SessionsFixture.loadedService(answering: .ok(try SessionsFixture.data()))
        let index = try #require(service.index)
        var filter = SessionsFilter()
        filter.states = [.needsYou]
        filter.showRecentlyDone = true
        ViewHost.render(SessionsHeader(
            viewMode: .constant(.table),
            filter: .constant(filter),
            counts: SessionsLayout.stateCounts(index: index, filter: filter, now: now),
            projects: SessionsLayout.menuProjects(index: index),
            service: service))
    }

    @Test("every state renders as a pill")
    func pillsRender() {
        for state in AgentSessionState.allCases {
            ViewHost.render(SessionStatePill(state: state, isOpen: state == .parked), size: CGSize(width: 120, height: 30))
        }
    }
}

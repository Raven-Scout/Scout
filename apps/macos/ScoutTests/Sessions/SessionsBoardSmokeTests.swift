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

    /// UI audit T4: lanes wrap whole cards rather than scroll sideways and cut the last one off.
    /// A wrapping board fits the page it is given, at every window width the audit used.
    @Test("the board's lanes wrap within the page at every window width")
    func boardWrapsWithinThePage() throws {
        let index = try SessionsFixture.index()
        var everything = SessionsFilter()
        everything.showScoutRuns = true
        everything.showRecentlyDone = true
        let board = SessionsBoardView(index: index, filter: everything, now: now, selectedID: .constant(nil))
        for window in LayoutSize.windowWidths {
            let page = LayoutSize.pageWidth(window: window)
            ViewHost.render(board, size: CGSize(width: page, height: 1200))
            let laidOut = LayoutHarness.size(of: board, proposing: CGSize(width: page, height: 4000))
            #expect(laidOut.width <= page + 0.5, "board is \(laidOut.width) pt on a \(page) pt page")
        }
        // A lane offers each card at least its minimum, so the narrowest page still fits one whole card.
        #expect(SessionsLane.minCardWidth + 2 * 24 <= LayoutSize.pageWidth(window: MainWindowSize.minWidth) - DetailPaneLayout.regularWidth)
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

    @Test("the header renders its PR-problems button, and the popover renders")
    func headerWithPRProblemsRenders() async throws {
        var object = try SessionsFixture.object()
        object["source_errors"] = [
            ["source": "gh", "message": "pr view failed: example-org/example-repo#98"],
            ["source": "gh", "message": "pr view failed: example-org/example-repo#102"],
        ]
        let data = try SessionsFixture.encode(object)
        let service = await SessionsFixture.loadedService(answering: .ok(data))
        await service.refreshPRs()
        let errors = try #require(service.prStatus?.errors)
        #expect(errors.count == 2)
        let index = try #require(service.index)
        ViewHost.render(SessionsHeader(
            viewMode: .constant(.board),
            filter: .constant(SessionsFilter()),
            counts: SessionsLayout.stateCounts(index: index, filter: SessionsFilter(), now: now),
            projects: SessionsLayout.menuProjects(index: index),
            service: service))
        ViewHost.render(PRCheckProblemsView(errors: errors, finishedAt: now, now: now, onCheckAgain: {}),
                        size: CGSize(width: 380, height: 360))
    }

    @Test("a Now card renders with its project name")
    func nowCardRenders() throws {
        let session = try #require(SessionsFixture.session("local_A", in: SessionsFixture.index()))
        ViewHost.render(SessionCardView(session: session, now: now, projectName: "Scout (code/Scout)"),
                        size: CGSize(width: 260, height: 180))
    }

    @Test("the detail pane renders with its Resume and Copy handoff actions")
    func detailRenders() throws {
        let index = try SessionsFixture.index()
        for id in ["local_A", "local_U"] {
            ViewHost.render(SessionDetailView(session: SessionsFixture.session(id, in: index), index: index,
                                              now: now, onSelect: { _ in }),
                            size: CGSize(width: 380, height: 900))
        }
    }

    @Test("every state renders as a pill")
    func pillsRender() {
        for state in AgentSessionState.allCases {
            ViewHost.render(SessionStatePill(state: state, isOpen: state == .parked), size: CGSize(width: 120, height: 30))
        }
    }
}

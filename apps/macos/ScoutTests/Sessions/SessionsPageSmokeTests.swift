import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Renders the detail pane and the whole page (pattern: ViewSmokeTests).
/// `.onAppear` does not run under `ViewHost`, so no watch or process starts.
@MainActor
@Suite("View smoke — sessions page", .serialized)
struct SessionsPageSmokeTests {
    private let now = SessionsFixture.now

    @Test("every fixture session renders in the detail pane")
    func detailRendersForEverySession() throws {
        let index = try SessionsFixture.index()
        for session in index.sessions {
            ViewHost.render(SessionDetailView(session: session, index: index, now: now) { _ in },
                            size: CGSize(width: 380, height: 900))
        }
    }

    @Test("the detail pane's empty states render")
    func emptyDetailRenders() throws {
        ViewHost.render(SessionDetailView(session: nil, index: try SessionsFixture.index(), now: now) { _ in },
                        size: CGSize(width: 380, height: 600))
        ViewHost.render(SessionDetailView(session: nil, index: nil, now: now) { _ in },
                        size: CGSize(width: 380, height: 600))
    }

    @Test("the page renders populated and in every banner state")
    func pageRendersInEveryState() async throws {
        var object = try SessionsFixture.object()
        object["schema_version"] = 2
        let schemaTwo = try SessionsFixture.encode(object)
        for result in [
            ProcessResult.ok(try SessionsFixture.data()),
            .failed(2, stderr: "Error: No such command 'index'."),
            .failed(127, stderr: "env: scoutctl: No such file or directory"),
            .failed(1, stderr: "boom"),
            .ok(schemaTwo),
        ] {
            let service = await SessionsFixture.loadedService(answering: result)
            ViewHost.render(SessionsView().environmentObject(service))
        }
    }
}

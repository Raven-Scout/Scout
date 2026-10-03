import Foundation
import Testing
@testable import Scout

/// `refreshUrgentActionCount()` runs at launch and on every menu-bar open, and
/// used to read and parse the whole day synchronously on the main actor — the
/// one parse #103 left behind. These pin the two paths it now takes: the
/// document the service already holds, or an off-actor parse.
@Suite("AppState — urgent count refresh")
@MainActor
struct AppStateUrgentCountRefreshTests {

    /// A vault root with an `action-items/` directory and today's file written
    /// with `text`. Fixture content is anonymized per CLAUDE.md.
    static func vault(todayFile text: String) throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("urgent-count-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("action-items"), withIntermediateDirectories: true
        )
        let stem = ActionItemsDay.stem(for: ActionItemsDay.today())
        try text.write(
            to: root.appendingPathComponent("action-items/action-items-\(stem).md"),
            atomically: true, encoding: .utf8
        )
        return root
    }

    static func day(urgentOpen: Int) -> String {
        var s = "# Action Items — synthetic\n\n## 🔴 Urgent\n\n"
        for i in 0 ..< urgentOpen {
            s += "- [ ] [#TAG\(i)] **Chase PROJ-\(1000 + i) with Priya** — body for \(i)\n"
        }
        s += "- [x] [#DONE] **Already landed** — body\n\n## 🟡 To do\n\n- [ ] **Ordinary todo** — body\n"
        return s
    }

    @Test("Refreshing the count does not block the main actor")
    func refreshDoesNotBlockTheMainActor() async throws {
        let root = try Self.vault(todayFile: Self.day(urgentOpen: 3))
        defer { try? FileManager.default.removeItem(at: root) }
        let state = AppState(configuration: .testing(scoutDirectory: root))

        // Queued before the refresh starts. While the parse ran synchronously
        // on the main actor there was no suspension point for this to slip
        // through, so it could only run after the refresh had finished.
        var order: [String] = []
        let other = Task { @MainActor in order.append("other") }

        await state.refreshUrgentActionCount()
        order.append("refresh")
        await other.value

        #expect(order.first == "other",
                "main actor was blocked through the refresh parse (order: \(order))")
        #expect(state.urgentActionCount == 3)
    }

    @Test("A missing file leaves the count at zero")
    func missingFileZeroesTheCount() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("urgent-count-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("action-items"), withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let state = AppState(configuration: .testing(scoutDirectory: root))

        await state.refreshUrgentActionCount()

        #expect(state.urgentActionCount == 0)
    }

    @Test("Today's already-parsed document is reused instead of re-read")
    func reusesTheServiceDocumentForToday() async throws {
        // The menu-bar badge and the Action Items list have to agree. The
        // service watches the file and republishes on every change, so its
        // document is the authority for today — re-reading the disk here would
        // parse the same day a second time and could disagree with the list
        // inside the watcher's debounce window.
        let root = try Self.vault(todayFile: Self.day(urgentOpen: 2))
        defer { try? FileManager.default.removeItem(at: root) }
        let state = AppState(configuration: .testing(scoutDirectory: root))
        await state.actionItemsDocumentService.load(date: ActionItemsDay.today())
        guard case .loaded = state.actionItemsDocumentService.state else {
            Issue.record("precondition: expected .loaded, got \(state.actionItemsDocumentService.state)")
            return
        }
        // `AppState` also mirrors the count off `docService.$state` through a
        // `DispatchQueue.main` sink. Let that land before the file changes, so
        // what the refresh does is the only thing this measures.
        try await Task.sleep(nanoseconds: 50_000_000)
        #expect(state.urgentActionCount == 2, "precondition: the state sink did not settle")

        // Change the file behind the service's back. `.testing` wires an inert
        // event source, so nothing reparses and the service still holds the
        // two-task document.
        let stem = ActionItemsDay.stem(for: ActionItemsDay.today())
        try Self.day(urgentOpen: 9).write(
            to: root.appendingPathComponent("action-items/action-items-\(stem).md"),
            atomically: true, encoding: .utf8
        )

        await state.refreshUrgentActionCount()

        #expect(state.urgentActionCount == 2,
                "the refresh re-read the file instead of reusing the loaded document")
    }
}

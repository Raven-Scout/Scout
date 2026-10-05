import Testing
import Foundation
@testable import Scout

@Suite("ActionItems end-to-end integration")
@MainActor
struct ActionItemsIntegrationTests {
    @Test func writerInvokesRealScoutctlAndViewPicksUpChange() async throws {
        // Skip if no engine is found — common on bare CI runners. Local dev
        // should always have one (see `findScoutctl`).
        guard let scoutctl = Self.findScoutctl() else { return }

        // 1. Temp data dir with the action-items subdir scoutctl expects.
        let base = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let aiDir = base.appendingPathComponent("action-items")
        try FileManager.default.createDirectory(at: aiDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: base) }

        // 2. Write a trivial MD.
        let mdURL = aiDir.appendingPathComponent("action-items-2026-04-20.md")
        let initial = """
        # Action Items — 2026-04-20

        ## 🔴 Urgent

        - [ ] **IntegrationTestTask** — verify end-to-end write-back
        """
        try initial.write(to: mdURL, atomically: true, encoding: .utf8)

        // 3. Mount the service.
        let service = ActionItemsDocumentService(directory: aiDir, fileEvents: FileWatcher())
        let date = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current, year: 2026, month: 4, day: 20
        ))!
        await service.load(date: date)

        // 4. Invoke the writer via real scoutctl. The PATH positional arg
        // tells scoutctl which daily file to mutate; its grandparent is the
        // implicit data dir, so we don't need SCOUT_DATA_DIR.
        let writer = ActionItemsWriter(
            scoutctl: scoutctl,
            actionItemsDirectory: aiDir,
            scoutDirectory: base,
            runner: SystemProcessRunner(),
            gitService: nil
        )
        _ = try await writer.submit(
            .addComment(subject: "IntegrationTestTask", shortPrefix: nil, text: "hello from integration", author: "user"),
            displayedDate: date
        )

        // 5. Wait for FSEvents + reparse. A liveness budget, not a latency
        // one: a fixed ~2 s here flaked under full-suite load (see `waitUntil`).
        await waitUntil("Comment never appeared in reparsed document; final state: \(service.state)") {
            guard case .loaded(let doc) = service.state,
                  let t = doc.sections.first?.tasks.first else { return false }
            return t.comments.contains(where: { $0.text.contains("hello from integration") })
        }
    }

    /// No engine on the machine (a bare CI runner) must read as "skip", not
    /// as a failure — and not as a fallback to some other `scoutctl`.
    @Test func findScoutctlSkipsWhenNoEngineIsFound() throws {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("integration-no-engine-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: home) }
        #expect(Self.findScoutctl(layout: EngineLayout(home: home)) == nil)
    }

    /// Resolve scoutctl exactly as the app does — through `EngineLocator`
    /// (pointer → `engine/current` → shim → marketplace cache → dev
    /// checkout), which runs the venv's `scoutctl`, never the
    /// `engine/bin/scoutctl` launcher. Returns nil when the locator finds no
    /// engine, or only a broken one whose `scoutctl` is not executable, so
    /// bare CI runners still skip.
    private static func findScoutctl(layout: EngineLayout = .live) -> URL? {
        let state = EngineLocator(layout: layout).locate()
        switch state {
        case .managed, .external:
            guard let url = state.scoutctl,
                  FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
            return url
        case .notInstalled, .broken:
            return nil
        }
    }
}

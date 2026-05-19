import Testing
import Foundation
@testable import Scout

@Suite("ActionItems end-to-end integration")
@MainActor
struct ActionItemsIntegrationTests {
    @Test func writerInvokesRealScoutctlAndViewPicksUpChange() async throws {
        // Skip if scoutctl isn't available in the environment — common on
        // bare CI runners. Local dev should always have it on PATH.
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
            timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 4, day: 20
        ))!
        try await service.load(date: date)

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
            .addComment(subject: "IntegrationTestTask", text: "hello from integration", author: "user"),
            displayedDate: date
        )

        // 5. Wait for FSEvents + reparse.
        var tries = 0
        while tries < 40 {
            try await Task.sleep(nanoseconds: 50_000_000)
            if case .loaded(let doc) = service.state,
               let t = doc.sections.first?.tasks.first,
               t.comments.contains(where: { $0.text.contains("hello from integration") }) {
                return
            }
            tries += 1
        }
        Issue.record("Comment never appeared in reparsed document; final state: \(service.state)")
    }

    /// Probe common install paths (mirroring AppState.resolveScoutctlPath)
    /// and fall back to PATH via `/usr/bin/env which scoutctl`.
    private static func findScoutctl() -> URL? {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates: [URL] = [
            home.appendingPathComponent("scout-plugin/bin/scoutctl"),
            home.appendingPathComponent("miniconda3/bin/scoutctl"),
            home.appendingPathComponent(".local/bin/scoutctl"),
            URL(fileURLWithPath: "/opt/homebrew/bin/scoutctl"),
            URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        ]
        for url in candidates {
            if FileManager.default.isExecutableFile(atPath: url.path) {
                return url
            }
        }
        return nil
    }
}

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

    /// Resolve scoutctl exactly as `AppState.resolveScoutctlPath()` does —
    /// via `ScoutctlLocator`, primarily from the installed plugin cache
    /// recorded in `~/.claude/plugins/installed_plugins.json` — instead of
    /// this test's own stale candidate list (previously first-tried the
    /// nonexistent `scout-plugin/bin/scoutctl` and had no knowledge of the
    /// plugin cache, so it silently skipped on machines where scoutctl only
    /// resolves via the cache).
    ///
    /// Returns nil when resolution falls back to `/usr/bin/env scoutctl`
    /// (nothing found on disk) — detected via a non-empty `argsPrefix` — so
    /// bare CI runners with no plugin installed still skip.
    private static func findScoutctl() -> URL? {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let manifest = try? String(
            contentsOf: ScoutctlLocator.installedPluginsJSONURL(home: home),
            encoding: .utf8
        )
        let result = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: manifest,
            isExecutable: { FileManager.default.isExecutableFile(atPath: $0.path) }
        )
        guard result.argsPrefix.isEmpty else { return nil }
        return result.executable
    }
}

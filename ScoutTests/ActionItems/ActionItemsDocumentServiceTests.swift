import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsDocumentService")
@MainActor
struct ActionItemsDocumentServiceTests {
    static func tmpDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test func loadsPresentFile() async throws {
        let dir = try Self.tmpDir()
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try "# Action Items — 2026-04-20\n\n## 🔴 Urgent\n\n- [ ] **A** — body\n".write(to: url, atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        let y = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current,
            year: 2026, month: 4, day: 20
        ))!
        try await service.load(date: y)

        switch service.state {
        case .loaded(let doc):
            #expect(doc.title.contains("2026-04-20"))
            #expect(doc.sections.count == 1)
            #expect(doc.sections[0].tasks.count == 1)
        default:
            Issue.record("expected .loaded, got \(service.state)")
        }
    }

    @Test func dailyFileUsesLocalTimezoneNotHardcodedEastern() async throws {
        // #46: the daily filename must follow the *system* timezone (matching
        // the engine's Python date.today()), not a hardcoded America/New_York.
        // Build a near-midnight instant in the current tz and expect that
        // calendar day's file. On a non-ET machine the old ET formatter would
        // roll to the wrong day.
        let dir = try Self.tmpDir()
        var comps = DateComponents()
        comps.timeZone = TimeZone.current
        comps.year = 2026; comps.month = 4; comps.day = 20
        comps.hour = 0; comps.minute = 30
        let date = Calendar.current.date(from: comps)!
        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        #expect(service.url(for: date).lastPathComponent == "action-items-2026-04-20.md")
    }

    @Test func emitsMissingWhenFileAbsent() async throws {
        let dir = try Self.tmpDir()
        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        let y = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current,
            year: 2099, month: 1, day: 1
        ))!
        try await service.load(date: y)
        switch service.state {
        case .missing: break
        default: Issue.record("expected .missing, got \(service.state)")
        }
    }

    @Test func reparseCurrentSurfacesErrorInsteadOfSwallowing() async throws {
        // #47: a reparse failure after the initial load must move the view to
        // .failed, not silently keep the stale .loaded state (the old `try?`).
        let dir = try Self.tmpDir()
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try "# T\n\n## 🔴 Urgent\n\n- [ ] **A** — body\n".write(to: url, atomically: true, encoding: .utf8)
        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        var comps = DateComponents()
        comps.timeZone = .current; comps.year = 2026; comps.month = 4; comps.day = 20
        let date = Calendar(identifier: .iso8601).date(from: comps)!
        try await service.load(date: date)
        guard case .loaded = service.state else {
            Issue.record("precondition: expected .loaded, got \(service.state)"); return
        }

        // Replace the file with a directory at the same path so the read throws
        // on a still-"present" path (deterministic, no permission/root games).
        try FileManager.default.removeItem(at: url)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)

        service.reparseCurrent()
        guard case .failed = service.state else {
            Issue.record("expected .failed after reparse error, got \(service.state)"); return
        }
    }

    @Test func reparsesOnFileChange() async throws {
        let dir = try Self.tmpDir()
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try "# T\n\n## 🔴 Urgent\n\n- [ ] **A** — body\n".write(to: url, atomically: true, encoding: .utf8)

        let fakeFS = InjectableFS()
        let service = ActionItemsDocumentService(directory: dir, fileEvents: fakeFS)
        let y = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current,
            year: 2026, month: 4, day: 20
        ))!
        try await service.load(date: y)

        // Mutate the file, then push an FSEvent.
        try "# T\n\n## 🔴 Urgent\n\n- [ ] **A** — body\n- [ ] **B** — body\n".write(to: url, atomically: true, encoding: .utf8)
        fakeFS.emit(FileSystemEvent(url: url, kind: .modified))

        // Wait up to 1s for the reparse.
        var tries = 0
        while tries < 20 {
            try await Task.sleep(nanoseconds: 50_000_000)  // 50ms
            if case .loaded(let doc) = service.state, doc.sections.first?.tasks.count == 2 { return }
            tries += 1
        }
        Issue.record("document did not reparse to 2 tasks in time; final state: \(service.state)")
    }
}

/// Test-only FS event source that lets tests push events manually.
final class InjectableFS: FileSystemEventSource, @unchecked Sendable {
    private var continuations: [AsyncStream<FileSystemEvent>.Continuation] = []
    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { cont in self.continuations.append(cont) }
    }
    func emit(_ e: FileSystemEvent) { continuations.forEach { $0.yield(e) } }
}

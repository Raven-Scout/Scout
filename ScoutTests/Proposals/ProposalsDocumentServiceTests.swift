import Foundation
import Testing
@testable import Scout

private struct EmptyFileEvents: FileSystemEventSource {
    nonisolated func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { $0.finish() }
    }
}

/// A file-event source the test drives by hand.
private final class ManualFileEvents: FileSystemEventSource, @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: AsyncStream<FileSystemEvent>.Continuation?

    nonisolated func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { cont in
            lock.lock(); continuation = cont; lock.unlock()
        }
    }

    func emit(_ event: FileSystemEvent) {
        lock.lock(); let c = continuation; lock.unlock()
        c?.yield(event)
    }
}

@MainActor
@Suite("ProposalsDocumentService")
struct ProposalsDocumentServiceTests {

    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("proposals-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @discardableResult
    private func writeProposal(
        in dir: URL, file: String, title: String, status: String, date: String = "2026-06-15"
    ) throws -> URL {
        let url = dir.appendingPathComponent(file)
        try """
        ---
        date: \(date)
        title: \(title)
        status: \(status)
        target: SKILL.md
        ---

        # \(date) — \(title)
        **Trigger:** the demo kept timing out.
        """.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    // MARK: - load

    @Test("load parses every proposal file, newest filename first")
    func load_parsesAndSortsNewestFirst() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeProposal(in: dir, file: "2026-06-10-older.md", title: "Older", status: "Proposed")
        try writeProposal(in: dir, file: "2026-06-15-newer.md", title: "Newer", status: "Approved")

        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()

        #expect(svc.state == .loaded)
        #expect(svc.proposals.map(\.title) == ["Newer", "Older"])
        #expect(svc.directoryURL == dir)
    }

    @Test("pendingCount counts only proposals awaiting a decision")
    func pendingCount_countsAwaitingDecision() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Proposed")
        try writeProposal(in: dir, file: "2026-06-02-b.md", title: "B",
                          status: "Pending (auto-apply after 2026-06-20)")
        try writeProposal(in: dir, file: "2026-06-03-c.md", title: "C", status: "Approved")
        try writeProposal(in: dir, file: "2026-06-04-d.md", title: "D", status: "Rejected")
        try writeProposal(in: dir, file: "2026-06-05-e.md", title: "E", status: "Applied — 2026-06-06")

        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()

        #expect(svc.proposals.count == 5)
        #expect(svc.pendingCount == 2)          // Proposed + Pending
    }

    @Test("non-markdown files and the frontmatter-less index are skipped")
    func load_ignoresNonProposalFiles() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeProposal(in: dir, file: "2026-06-15-real.md", title: "Real", status: "Proposed")
        // The legacy index file: markdown, but no `---` frontmatter.
        try "# Dreaming proposals\n\nAn index, not a proposal.\n"
            .write(to: dir.appendingPathComponent("dreaming-proposals.md"),
                   atomically: true, encoding: .utf8)
        try "not markdown"
            .write(to: dir.appendingPathComponent("notes.txt"), atomically: true, encoding: .utf8)

        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()

        #expect(svc.proposals.map(\.title) == ["Real"])
    }

    @Test("an absent directory reports .missing and holds no proposals")
    func load_missingDirectory() {
        let absent = FileManager.default.temporaryDirectory
            .appendingPathComponent("absent-\(UUID().uuidString)")
        let svc = ProposalsDocumentService(directoryURL: absent, fileEvents: EmptyFileEvents())
        svc.load()

        #expect(svc.proposals.isEmpty)
        #expect(svc.state == .missing(absent))
        #expect(svc.pendingCount == 0)
    }

    @Test("a path that is a file, not a directory, also reports .missing")
    func load_pathIsAFile() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("a-file.md")
        try "x".write(to: file, atomically: true, encoding: .utf8)

        let svc = ProposalsDocumentService(directoryURL: file, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.state == .missing(file))
    }

    @Test("an empty directory loads cleanly with no proposals")
    func load_emptyDirectory() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.state == .loaded)
        #expect(svc.proposals.isEmpty)
    }

    @Test("state starts idle before load")
    func state_startsIdle() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        #expect(svc.state == .idle)
        #expect(svc.proposals.isEmpty)
    }

    // MARK: - reload

    @Test("reload picks up a file added after load")
    func reload_seesNewFiles() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Proposed")

        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.proposals.count == 1)

        try writeProposal(in: dir, file: "2026-06-02-b.md", title: "B", status: "Approved")
        svc.reload()

        #expect(svc.proposals.map(\.title) == ["B", "A"])
        #expect(svc.pendingCount == 1)
    }

    @Test("reload reflects a status flipped on disk")
    func reload_seesStatusChange() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Proposed")
        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.pendingCount == 1)

        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Approved")
        svc.reload()
        #expect(svc.pendingCount == 0)
        #expect(svc.proposals.first?.status == .approved)
    }

    // MARK: - file watching

    @Test("a markdown file event triggers a debounced reparse")
    func watching_markdownEventReparses() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let events = ManualFileEvents()
        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: events)
        svc.load()
        #expect(svc.proposals.isEmpty)

        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Proposed")
        events.emit(FileSystemEvent(
            url: dir.appendingPathComponent("2026-06-01-a.md"), kind: .created))

        try await waitUntil { svc.proposals.count == 1 }
        #expect(svc.proposals.first?.title == "A")
    }

    @Test("a non-markdown file event is ignored")
    func watching_ignoresNonMarkdownEvents() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let events = ManualFileEvents()
        let svc = ProposalsDocumentService(directoryURL: dir, fileEvents: events)
        svc.load()

        // The file exists, but the event names a .txt — no reparse should run.
        try writeProposal(in: dir, file: "2026-06-01-a.md", title: "A", status: "Proposed")
        events.emit(FileSystemEvent(url: dir.appendingPathComponent("log.txt"), kind: .modified))

        try await Task.sleep(nanoseconds: 500_000_000)
        #expect(svc.proposals.isEmpty)
    }

    /// Poll `condition` on the main actor until it holds or the budget runs out.
    private func waitUntil(
        timeout: TimeInterval = 3.0,
        _ condition: @MainActor () -> Bool
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        #expect(condition(), "condition never became true within \(timeout)s")
    }
}

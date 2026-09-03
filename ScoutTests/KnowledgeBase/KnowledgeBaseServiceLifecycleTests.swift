import Foundation
import Testing
@testable import Scout

/// A KB event source the test drives by hand.
private final class ManualKBEvents: FileSystemEventSource, @unchecked Sendable {
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

/// Covers the KB service's lifecycle — load / reload / readFile / watching —
/// and the search-content edge cases the graph tests don't reach.
@MainActor
@Suite("KnowledgeBaseService lifecycle")
struct KnowledgeBaseServiceLifecycleTests {

    /// A scout root with a `knowledge-base/` folder holding `files`.
    private func makeKB(_ files: [String: String]) throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-life-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        for (path, contents) in files {
            let url = kb.appendingPathComponent(path)
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try contents.write(to: url, atomically: true, encoding: .utf8)
        }
        return root
    }

    // MARK: - init

    @Test("the KB root is the knowledge-base folder under the scout directory")
    func init_derivesKBDirectory() throws {
        let root = try makeKB([:]); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        // Compare paths, not URLs: resolvingSymlinksInPath() on an existing
        // directory hands back a trailing-slash URL.
        #expect(svc.scoutDirectory.path == root.path)
        #expect(svc.kbDirectory.path == root.appendingPathComponent("knowledge-base").path)
        #expect(svc.state == .idle)
        #expect(svc.tree.isEmpty)
        #expect(svc.index == .empty)
    }

    // MARK: - load / reload

    @Test("load builds the tree and index and reports .loaded")
    func load_buildsTreeAndIndex() async throws {
        let root = try makeKB([
            "people/alex.md": "---\ntype: person\n---\n# Alex\nWorks on [[the-demo]].\n",
            "projects/the-demo.md": "# The demo\n",
        ])
        defer { try? FileManager.default.removeItem(at: root) }

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        await svc.reparseAndWait()

        #expect(svc.state == .loaded)
        #expect(!svc.tree.isEmpty)
        #expect(svc.index.stemToPath["alex"] != nil)
        #expect(svc.index.typeByFile.values.contains("person"))
    }

    @Test("an absent knowledge-base folder reports .missing")
    func load_missingKBDirectory() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-absent-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        await svc.reparseAndWait()

        #expect(svc.state == .missing(root.appendingPathComponent("knowledge-base")))
        #expect(svc.tree.isEmpty)
    }

    @Test("an empty knowledge base loads cleanly")
    func load_emptyKB() async throws {
        let root = try makeKB([:]); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        await svc.reparseAndWait()
        #expect(svc.state == .loaded)
        #expect(svc.tree.isEmpty)
    }

    @Test("reload picks up a note added after load")
    func reload_seesNewNotes() async throws {
        let root = try makeKB(["a.md": "# A\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        await svc.reparseAndWait()
        #expect(svc.tree.flatMap(\.allFiles).count == 1)

        try "# B\n".write(
            to: root.appendingPathComponent("knowledge-base/b.md"),
            atomically: true, encoding: .utf8)
        svc.reload()
        await svc.reparseAndWait()

        #expect(svc.tree.flatMap(\.allFiles).map(\.displayName).sorted() == ["a", "b"])
    }

    // MARK: - readFile

    @Test("readFile returns a note's full text")
    func readFile_returnsContents() async throws {
        let root = try makeKB(["a.md": "# A\nbody line\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        let url = root.appendingPathComponent("knowledge-base/a.md")
        #expect(svc.readFile(url) == "# A\nbody line\n")
    }

    @Test("readFile returns nil for a file that isn't there")
    func readFile_missingFileIsNil() async throws {
        let root = try makeKB([:]); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        #expect(svc.readFile(root.appendingPathComponent("knowledge-base/nope.md")) == nil)
    }

    // MARK: - searchContent

    @Test("a query shorter than two characters returns nothing")
    func searchContent_rejectsShortQueries() async throws {
        let root = try makeKB(["a.md": "# Alex\nthe demo\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.searchContent("").isEmpty)
        #expect(svc.searchContent("a").isEmpty)
    }

    @Test("a content match returns the first matching line as a snippet")
    func searchContent_returnsFirstMatchingLine() async throws {
        let root = try makeKB([
            "notes.md": "# Notes\nnothing here\nthe tracing job failed twice\nmore text\n",
        ])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        let hits = svc.searchContent("tracing")
        #expect(hits.count == 1)
        #expect(hits.first?.snippet == "the tracing job failed twice")
    }

    @Test("a name-only match yields a hit with an empty snippet")
    func searchContent_nameMatchHasEmptySnippet() async throws {
        let root = try makeKB(["priya.md": "no body match at all\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        let hits = svc.searchContent("priya")
        #expect(hits.count == 1)
        #expect(hits.first?.name == "priya")
        #expect(hits.first?.snippet == "")
    }

    @Test("a free-text search is case-insensitive")
    func searchContent_isCaseInsensitive() async throws {
        let root = try makeKB(["a.md": "# A\nThe Tracing Job\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.searchContent("Tracing").count == 1)
        #expect(svc.searchContent("tracing").count == 1)
        #expect(svc.searchContent("tRaCiNg").count == 1)
    }

    @Test("a tag-shaped query matches tags, not substrings")
    func searchContent_tagQueryMatchesTagsOnly() async throws {
        // An all-caps token 2–8 chars long normalizes to a tag, so the search
        // switches from substring to tag matching — otherwise `#MIRO` would
        // also report every note merely containing those letters.
        let root = try makeKB([
            "tagged.md": "# Tagged\nWork tracked under [#MIRO] this week.\n",
            "prose.md": "# Prose\nThe word MIRO appears here as plain text.\n",
        ])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        let hits = svc.searchContent("MIRO")
        #expect(hits.map(\.name) == ["tagged"])

        // The same letters as free text still match both notes.
        #expect(svc.searchContent("Miro").count == 2)
    }

    @Test("a long matching line is truncated to 120 characters")
    func searchContent_truncatesLongSnippets() async throws {
        let long = String(repeating: "x", count: 300) + " needle"
        let root = try makeKB(["a.md": "# A\n\(long)\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        let hits = svc.searchContent("xxx")
        #expect(hits.count == 1)
        #expect(hits.first?.snippet.count == 120)
    }

    @Test("results are capped at 30 hits")
    func searchContent_capsAtThirtyHits() async throws {
        var files: [String: String] = [:]
        for i in 0..<45 { files["note-\(i).md"] = "# Note \(i)\nthe needle is here\n" }
        let root = try makeKB(files); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        #expect(svc.searchContent("needle").count == 30)
    }

    @Test("a query matching nothing returns no hits")
    func searchContent_noMatches() async throws {
        let root = try makeKB(["a.md": "# A\nbody\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.searchContent("zzzznothing").isEmpty)
    }

    @Test("non-markdown notes are excluded from content search")
    func searchContent_onlySearchesMarkdown() async throws {
        let root = try makeKB(["schema.yaml": "needle: true\n", "a.md": "# A\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.searchContent("needle").isEmpty)
    }

    // MARK: - watching

    @Test("a file event triggers a debounced reparse")
    func watching_fileEventReparses() async throws {
        let root = try makeKB(["a.md": "# A\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let events = ManualKBEvents()
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: events)
        svc.load()
        await svc.reparseAndWait()
        #expect(svc.tree.flatMap(\.allFiles).count == 1)

        try "# B\n".write(
            to: root.appendingPathComponent("knowledge-base/b.md"),
            atomically: true, encoding: .utf8)
        events.emit(FileSystemEvent(
            url: root.appendingPathComponent("knowledge-base/b.md"), kind: .created))

        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline, svc.tree.flatMap(\.allFiles).count < 2 {
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        #expect(svc.tree.flatMap(\.allFiles).count == 2)
    }
}

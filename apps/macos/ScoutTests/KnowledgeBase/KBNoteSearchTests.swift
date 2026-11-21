import Foundation
import Testing
@testable import Scout

/// The KB sidebar search: a note several folders deep, found by a word in its
/// file name, including when the search was typed before the note was in the
/// tree (the first scan still running, or a note added after the search ran).
@MainActor
@Suite("KB note search")
struct KBNoteSearchTests {

    static let deepPath = "knowledge-base/projects/widgets/knowledge/quarterly-planning-archive/"
        + "regional-rollout-notes/rollout-checklist-for-regional-widgets-launch.md"

    /// A scout root with a `knowledge-base/` folder holding `files`.
    private func makeKB(_ files: [String: String]) throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-search-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        for (path, contents) in files { try write(contents, to: "knowledge-base/" + path, in: root) }
        return root
    }

    private func write(_ contents: String, to relPath: String, in root: URL) throws {
        let url = root.appendingPathComponent(relPath)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try contents.write(to: url, atomically: true, encoding: .utf8)
    }

    private let deepRel = String(deepPath.dropFirst("knowledge-base/".count))

    // MARK: - The service: no depth limit, names as well as text, any case

    @Test("a note five folders deep is found by a word in its file name, in any case")
    func service_findsDeepNoteByFileName() async throws {
        let root = try makeKB([
            "people/alex.md": "# Alex\n",
            deepRel: "# Checklist\nnothing about the word here\n",
        ])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        for q in ["rollout", "Rollout", "regional-widgets"] {
            #expect(svc.searchContent(q).map(\.path) == [Self.deepPath], "query \(q)")
        }
    }

    // MARK: - The sidebar: results follow the tree

    @Test("a search typed before the first scan lands shows its hits once it does")
    func sidebar_searchBeforeFirstScan_updatesWhenTreeLands() async throws {
        let root = try makeKB([deepRel: "# Checklist\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        let search = KBNoteSearch(debounce: .zero)
        search.attach(svc)

        search.query = "rollout"
        await search.settle()
        #expect(search.hits.isEmpty)   // nothing scanned yet

        await svc.reparseAndWait()
        await search.settle()
        #expect(search.hits.map(\.path) == [Self.deepPath])
    }

    @Test("a note added after a search ran joins its results on the next scan")
    func sidebar_noteAddedAfterSearch_joinsResults() async throws {
        let root = try makeKB(["people/alex.md": "# Alex\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        let search = KBNoteSearch(debounce: .zero)
        search.attach(svc)

        search.query = "rollout"
        await search.settle()
        #expect(search.hits.isEmpty)

        try write("# Checklist\n", to: Self.deepPath, in: root)
        await svc.reparseAndWait()
        await search.settle()
        #expect(search.hits.map(\.path) == [Self.deepPath])
    }

    @Test("a short or cleared query shows nothing, and a rescan doesn't bring hits back")
    func sidebar_clearedQuery_staysEmptyAcrossRescans() async throws {
        let root = try makeKB([deepRel: "# Checklist\n"])
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        let search = KBNoteSearch(debounce: .zero)
        search.attach(svc)

        search.query = "rollout"
        await search.settle()
        #expect(search.hits.count == 1)

        search.query = "r"
        #expect(search.hits.isEmpty)
        search.query = ""
        await svc.reparseAndWait()
        await search.settle()
        #expect(search.hits.isEmpty)
    }
}

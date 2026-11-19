import Foundation
import Testing
@testable import Scout

/// New and renamed notes share one name rule: they stay `.md`, linkable and
/// visible (follow-ups to UI audit #75).
@Suite("KB note names")
struct KBNoteNameTests {
    private func makeScoutDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-names-\(UUID().uuidString)").resolvingSymlinksInPath()
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("knowledge-base"), withIntermediateDirectories: true)
        return dir
    }

    @Test("a leading dot is refused for a note, typed or renamed")
    func leadingDot() {
        let note = URL(fileURLWithPath: "/kb/knowledge-base/alex.md")
        #expect(KnowledgeBaseFileWriter.noteNameProblem(".alex") == KnowledgeBaseFileWriter.hiddenNameMessage)
        #expect(KBEditorView.renameNameProblem("  .alex", for: note) != nil)
        #expect(throws: KBWriterError.self) { try KnowledgeBaseFileWriter.renameDestination(for: note, to: ".alex") }
        #expect(KnowledgeBaseFileWriter.noteNameProblem("alex.v2") == nil)
    }

    @Test("creating a note follows the rename rules")
    func createFollowsRenameRules() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let kb = scout.appendingPathComponent("knowledge-base")
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: nil)
        let dotted = try await writer.createFile(in: kb, name: "acme.com", initialContents: "# acme.com\n")
        #expect(dotted.lastPathComponent == "acme.com.md")
        let plain = try await writer.createFile(in: kb, name: "priya.md", initialContents: "")
        #expect(plain.lastPathComponent == "priya.md")
        for bad in ["C# notes", "Q3 | draft", ".hidden"] {
            await #expect(throws: KBWriterError.self, "\(bad)") {
                _ = try await writer.createFile(in: kb, name: bad, initialContents: "")
            }
        }
    }

    @Test("the commit-failure notice always ends its sentence before Undo")
    func commitProblemPunctuation() {
        typealias Links = KBEditorView.RenameLinks
        let bare = Links.outcomeNotice(.none, commitProblem: "index.lock exists")?.message ?? ""
        #expect(bare.contains("failed: index.lock exists. Edit ▸ Undo"))
        let stopped = Links.outcomeNotice(.none, commitProblem: "index.lock exists.")?.message ?? ""
        #expect(stopped.contains("failed: index.lock exists. Edit ▸ Undo"))
    }

    @Test("the sheet warns when the note's own links stay because it has unsaved edits")
    func selfUnsaved() {
        typealias Links = KBEditorView.RenameLinks
        #expect(Links.selfUnsavedNote(selfLinks: 2, hasUnsavedEdits: true)
                == "This note has unsaved edits, so its 2 links to itself won't be updated. Save it first to include them.")
        #expect(Links.selfUnsavedNote(selfLinks: 0, hasUnsavedEdits: true) == nil)
        #expect(Links.selfUnsavedNote(selfLinks: 1, hasUnsavedEdits: false) == nil)
    }
}

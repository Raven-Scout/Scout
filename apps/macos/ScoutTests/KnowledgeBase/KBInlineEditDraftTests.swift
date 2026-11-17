import Testing
import Foundation
@testable import Scout

/// A git service whose commits always fail, so the writer reports
/// "written but uncommitted" (`KBWriterError.commitFailed`).
private struct FailingCommitGit: GitServiceProtocol {
    struct Boom: Error {}
    func commitPaths(_ relPaths: [String], message: String) async throws { throw Boom() }
}

/// Pins the C1 review follow-ups: a rename whose commit fails still carries the
/// draft to the moved file, and Read mode's inline edits are part of the
/// note's draft from the first keystroke.
@Suite("KB inline edits and rename drafts (C1)")
@MainActor
struct KBInlineEditDraftTests {

    private func makeScoutDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-inline-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("knowledge-base"), withIntermediateDirectories: true)
        return dir
    }

    // MARK: - Rename whose commit fails

    @Test("a rename whose commit fails has moved the file to the planned destination")
    func commitFailedRename_landsAtPlannedDestination() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let file = scout.appendingPathComponent("knowledge-base/alex.md")
        try "body".write(to: file, atomically: true, encoding: .utf8)
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: FailingCommitGit())
        let planned = try KnowledgeBaseFileWriter.renameDestination(for: file, to: "alex-renamed")

        var thrown: Error?
        do { _ = try await writer.rename(fileURL: file, to: "alex-renamed") } catch { thrown = error }

        let error = try #require(thrown)
        guard case .movedButUncommitted(let moved, let problem) =
                KBEditorView.renameOutcome(after: error, planned: planned) else {
            Issue.record("expected movedButUncommitted"); return
        }
        #expect(moved == planned)
        // The git problem is handed to the parent, which outlives the editor.
        #expect(problem.contains("git commit failed"))
        #expect(FileManager.default.fileExists(atPath: moved.path))
        #expect(!FileManager.default.fileExists(atPath: file.path))
    }

    @Test("the draft follows a rename whose commit failed")
    func commitFailedRename_movesDraft() throws {
        let old = URL(fileURLWithPath: "/tmp/vault/knowledge-base/alex.md")
        let planned = try KnowledgeBaseFileWriter.renameDestination(for: old, to: "alex-renamed")
        let store = KBDraftStore()
        store.update(KBDraft(text: "edited", originalText: "body",
                             baselineContents: "body", baselineDate: nil), for: old)

        guard case .movedButUncommitted(let moved, _) = KBEditorView.renameOutcome(
            after: KBWriterError.commitFailed("index.lock"), planned: planned) else {
            Issue.record("expected movedButUncommitted"); return
        }
        store.move(from: old, to: moved)

        #expect(store.draft(for: old) == nil)
        #expect(store.draft(for: moved)?.text == "edited")
        #expect(store.unsavedFileNames == ["alex-renamed.md"])
    }

    @Test("any other rename failure leaves the file, and its draft, where they were")
    func otherRenameFailures_doNotMove() {
        let planned = URL(fileURLWithPath: "/tmp/vault/knowledge-base/b.md")
        #expect(KBEditorView.renameOutcome(after: KBWriterError.alreadyExists("b.md"), planned: planned)
                == .notMoved(problem: "A file named b.md already exists."))
        #expect(KBEditorView.renameOutcome(after: KBWriterError.writeFailed("x"), planned: planned)
                == .notMoved(problem: "x"))
        // No planned destination (the name didn't normalize): nothing to follow.
        #expect(KBEditorView.renameOutcome(after: KBWriterError.commitFailed("x"), planned: nil)
                == .notMoved(problem: "The change was written, but the git commit failed: x"))
    }

    @Test("renameDestination keeps the original extension, like rename does")
    func renameDestination_keepsExtension() throws {
        let yaml = URL(fileURLWithPath: "/tmp/vault/knowledge-base/config.yaml")
        #expect(try KnowledgeBaseFileWriter.renameDestination(for: yaml, to: "settings")
                    .lastPathComponent == "settings.yaml")
    }

    // MARK: - Read-mode inline edits

    private let note = """
    # Alex

    First paragraph.

    | Name | Role |
    | --- | --- |
    | Alex | Lead |
    """

    @Test("opening an inline editor without typing leaves the note byte-identical")
    func untouchedInlineEdit_isClean() {
        let edit = KBEditableView.InlineEdit(
            target: .block(lineStart: 2, lineEnd: 2), snapshot: note,
            original: "First paragraph.", buffer: "First paragraph.")
        #expect(edit.applied == note)
        #expect(edit.owns(note))
    }

    @Test("typing in a block puts the change in the draft right away")
    func blockEdit_writesThrough() {
        var edit = KBEditableView.InlineEdit(
            target: .block(lineStart: 2, lineEnd: 2), snapshot: note,
            original: "First paragraph.", buffer: "First paragraph.")
        edit.buffer = "First paragraph, edited."
        #expect(edit.applied.contains("First paragraph, edited."))
        #expect(!edit.applied.contains("First paragraph.\n"))
        // Every other line is untouched.
        #expect(edit.applied.hasPrefix("# Alex\n\n"))
        #expect(edit.applied.hasSuffix("| Alex | Lead |"))

        // That draft is dirty, so the store keeps it and the quit prompt names it.
        let store = KBDraftStore()
        let url = URL(fileURLWithPath: "/tmp/vault/knowledge-base/alex.md")
        store.update(KBDraft(text: edit.applied, originalText: note,
                             baselineContents: note, baselineDate: nil), for: url)
        #expect(store.unsavedFileNames == ["alex.md"])
    }

    @Test("typing in a table cell rewrites only that cell in the draft")
    func cellEdit_writesThrough() {
        var edit = KBEditableView.InlineEdit(
            target: .cell(segmentID: 4, row: 0, col: 1, sourceLine: 6), snapshot: note,
            original: "Lead", buffer: "Lead")
        edit.buffer = "Owner"
        let lines = edit.applied.components(separatedBy: "\n")
        #expect(lines[6] == "| Alex | Owner |")
        #expect(Array(lines[0..<6]) == Array(note.components(separatedBy: "\n")[0..<6]))
    }

    @Test("a draft the edit didn't produce (Reload, another note) ends the edit")
    func foreignSourceEndsEdit() {
        var edit = KBEditableView.InlineEdit(
            target: .block(lineStart: 2, lineEnd: 2), snapshot: note,
            original: "First paragraph.", buffer: "First paragraph.")
        edit.buffer = "Typed."
        #expect(edit.owns(edit.applied))
        #expect(!edit.owns(note))            // reloaded from disk
        #expect(!edit.owns("# Priya\n"))     // a different note
    }

    @Test("Send to Scout waits while an inline edit is open")
    func commentSendWaitsForInlineEdit() {
        // Its marker goes in by line number from the edit's snapshot, and
        // sending saves the note with the unfinished edit in it.
        #expect(!KBEditableView.canSendComment("Check this", inlineEditOpen: true))
        #expect(KBEditableView.canSendComment("Check this", inlineEditOpen: false))
        #expect(!KBEditableView.canSendComment("   ", inlineEditOpen: false))
    }

    @Test("the inline editor's buttons say what they do")
    func inlineButtonTitles() {
        #expect(KBEditableView.inlineCommitTitle == "Done")
        #expect(KBEditableView.inlineDiscardTitle == "Discard")
    }
}

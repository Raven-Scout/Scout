import SwiftUI
import Testing
import Foundation
@testable import Scout

/// Pins the C1 KB editor fixes: unsaved edits survive navigation, Esc on the
/// conflict alert keeps them, and an unreadable file is never edited as an
/// empty document.
@Suite("KB draft safety (C1)")
@MainActor
struct KBDraftSafetyTests {
    private let a = URL(fileURLWithPath: "/tmp/vault/knowledge-base/people/alex.md")
    private let b = URL(fileURLWithPath: "/tmp/vault/knowledge-base/people/priya.md")

    private func dirty(_ text: String = "edited") -> KBDraft {
        KBDraft(text: text, originalText: "original",
                baselineContents: "original", baselineDate: Date(timeIntervalSince1970: 1))
    }

    // MARK: - Draft store

    @Test("a dirty draft is kept per file; a clean one is dropped")
    func storeKeepsOnlyDirtyDrafts() {
        let store = KBDraftStore()
        store.update(dirty(), for: a)
        #expect(store.draft(for: a) == dirty())
        #expect(store.draft(for: b) == nil)
        #expect(store.unsavedFileNames == ["alex.md"])

        store.update(.fromDisk("original", date: nil), for: a)
        #expect(store.draft(for: a) == nil)
        #expect(!store.hasUnsavedDrafts)
    }

    @Test("a rename carries the draft to the new path")
    func storeMovesDraftOnRename() {
        let store = KBDraftStore()
        store.update(dirty(), for: a)
        store.move(from: a, to: b)
        #expect(store.draft(for: a) == nil)
        #expect(store.draft(for: b) == dirty())
    }

    @Test("keys ignore path spelling (./ and ..)")
    func storeStandardizesPaths() {
        let store = KBDraftStore()
        store.update(dirty(), for: URL(fileURLWithPath: "/tmp/vault/knowledge-base/people/./alex.md"))
        #expect(store.draft(for: a) != nil)
        store.discard(for: URL(fileURLWithPath: "/tmp/vault/knowledge-base/x/../people/alex.md"))
        #expect(store.draft(for: a) == nil)
    }

    // MARK: - Opening a file

    @Test("reopening a file restores its unsaved draft over disk, baseline included")
    func openRestoresDraft() {
        let result = KBEditorView.resolveLoad(
            diskText: "changed on disk", fileExists: true, diskDate: Date(), stashed: dirty())
        // The baseline is what the user typed against, so saving still
        // reports the external change instead of overwriting it.
        #expect(result == .editing(dirty(), restored: true))
    }

    @Test("a clean stash is ignored and disk wins")
    func cleanStashIgnored() {
        let date = Date(timeIntervalSince1970: 5)
        let result = KBEditorView.resolveLoad(
            diskText: "disk", fileExists: true, diskDate: date,
            stashed: .fromDisk("old", date: nil))
        #expect(result == .editing(.fromDisk("disk", date: date), restored: false))
    }

    @Test("a file that exists but can't be read is unreadable, not an empty document")
    func unreadableIsNotEmpty() {
        #expect(KBEditorView.resolveLoad(
            diskText: nil, fileExists: true, diskDate: nil, stashed: nil) == .unreadable)
    }

    @Test("a missing file opens empty, with a nil baseline")
    func missingOpensEmpty() {
        #expect(KBEditorView.resolveLoad(
            diskText: nil, fileExists: false, diskDate: nil, stashed: nil)
            == .editing(KBDraft(text: "", originalText: "", baselineContents: nil, baselineDate: nil),
                        restored: false))
    }

    // MARK: - Conflict alert

    @Test("Esc on the conflict alert keeps the edits")
    func conflictCancelKeepsEdits() {
        let cancel = KBEditorView.ConflictChoice.allCases.filter { $0.role == .cancel }
        #expect(cancel == [.keepEditing])
        #expect(KBEditorView.ConflictChoice.reload.role != .cancel)
        #expect(KBEditorView.ConflictChoice.reload.title.contains("Discard"))
    }

    // MARK: - Reload (R2)

    @Test("Reload asks before discarding unsaved edits, and reloads at once without any")
    func reloadAsksWhenDirty() {
        #expect(KBEditorView.ReloadStep.forReload(isDirty: true) == .confirmDiscard)
        #expect(KBEditorView.ReloadStep.forReload(isDirty: false) == .reloadNow)
    }

    /// What the Reload buttons call: a dirty draft only asks, it never
    /// reloads (discards) on the way.
    @Test("requestReload never reloads a dirty draft; it only asks")
    func requestReloadDispatch() {
        for isDirty in [true, false] {
            var reloaded = 0, asked = 0
            KBEditorView.requestReload(isDirty: isDirty, reload: { reloaded += 1 }, askToDiscard: { asked += 1 })
            #expect(reloaded == (isDirty ? 0 : 1), "isDirty \(isDirty)")
            #expect(asked == (isDirty ? 1 : 0), "isDirty \(isDirty)")
        }
    }

    @Test("Esc on the Reload prompt keeps the edits")
    func reloadCancelKeepsEdits() {
        let cancel = KBEditorView.ReloadChoice.allCases.filter { $0.role == .cancel }
        #expect(cancel == [.keepEditing])
        #expect(KBEditorView.ReloadChoice.discardAndReload.role == .destructive)
        #expect(KBEditorView.ReloadChoice.discardAndReload.title.contains("Discard"))
    }

    // MARK: - Read mode, quitting

    @Test("the inline editor's confirm button doesn't claim to save the file")
    func inlineConfirmIsDone() {
        #expect(KBEditableView.inlineCommitTitle == "Done")
    }

    @Test("the quit prompt names the notes with unsaved edits")
    func quitPromptNamesNotes() {
        #expect(AppDelegate.unsavedDraftsMessage(["alex.md"])
            == "1 knowledge-base note has unsaved edits (alex.md). Quitting discards them.")
        let many = (1...7).map { "n\($0).md" }
        let text = AppDelegate.unsavedDraftsMessage(many)
        #expect(text.hasPrefix("7 knowledge-base notes have unsaved edits"))
        #expect(text.contains("and 2 more"))
    }
}

import Foundation

/// One open KB file's editor state: what the user has typed, and what it was
/// typed against. Kept whole so a restored draft saves with the same conflict
/// guard it would have had if the user had never navigated away.
struct KBDraft: Equatable {
    /// The editor's current text.
    var text: String
    /// The text as last loaded or saved; `text != originalText` means unsaved.
    var originalText: String
    /// Disk contents the conflict guard compares against (nil = the file was
    /// missing at load).
    var baselineContents: String?
    /// Disk mtime at load, the cheap trigger for external-change detection.
    var baselineDate: Date?

    var isDirty: Bool { text != originalText }

    /// A clean draft of what's on disk.
    static func fromDisk(_ text: String?, date: Date?) -> KBDraft {
        KBDraft(text: text ?? "", originalText: text ?? "",
                baselineContents: text, baselineDate: date)
    }
}

/// Unsaved KB editor drafts, held for the app's lifetime so that navigating
/// to another note, a wikilink, the overview or another page never discards
/// edits. The editor writes its state here on every change; reopening a file
/// restores the draft. Only dirty drafts are kept.
///
/// In memory only: `AppDelegate` asks before quitting while any remain.
@MainActor
final class KBDraftStore {
    /// The store the app's single `KnowledgeBaseService` uses, and the one
    /// `AppDelegate` checks before quitting.
    static let shared = KBDraftStore()

    private var drafts: [String: KBDraft] = [:]

    private static func key(_ url: URL) -> String { url.standardizedFileURL.path }

    /// Record `draft` for `url`; a clean draft removes any entry.
    func update(_ draft: KBDraft, for url: URL) {
        drafts[Self.key(url)] = draft.isDirty ? draft : nil
    }

    func draft(for url: URL) -> KBDraft? { drafts[Self.key(url)] }

    /// Drop the draft, e.g. after the user chose to reload or deleted the file.
    func discard(for url: URL) { drafts[Self.key(url)] = nil }

    /// Carry a draft over a rename.
    func move(from old: URL, to new: URL) {
        guard let draft = drafts.removeValue(forKey: Self.key(old)) else { return }
        drafts[Self.key(new)] = draft
    }

    /// File names with unsaved drafts, sorted, for the quit prompt.
    var unsavedFileNames: [String] {
        drafts.keys.map { ($0 as NSString).lastPathComponent }.sorted()
    }

    var hasUnsavedDrafts: Bool { !drafts.isEmpty }
}

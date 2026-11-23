import Foundation
import Observation

/// The task an Action Items comment draft belongs to: the day's file and the
/// task in it, by `[#TAG]`, else by subject.
///
/// Not `ActionTask.id`: that encodes the row's position, so a scheduled run
/// adding an item above renumbers the card, and the draft would be lost the
/// way it was when it lived in the composer's `@State`. The day is part of
/// the key because a carried task keeps its tag on every day's file, and a
/// draft typed on one day must not turn up, and be sent, on another.
nonisolated struct CommentDraftTaskKey: Hashable, Sendable {
    /// `ActionItemsDay.stem` of the file: `YYYY-MM-DD`.
    let day: String
    /// `#TAG`, or `subject:` and the matchable subject for an untagged task.
    let task: String

    init(day: String, task: ActionTask) {
        self.day = day
        self.task = Self.identity(of: task)
    }

    init(task: ActionTask, displayedDate: Date) {
        self.init(day: ActionItemsDay.stem(for: displayedDate), task: task)
    }

    static func identity(of task: ActionTask) -> String {
        if let tag = task.shortPrefix { return "#" + tag }
        return "subject:" + task.matchableSubject
    }
}

/// Which comment of a task a draft is for.
nonisolated enum CommentDraftTarget: Hashable, Sendable {
    /// The composer's new comment.
    case new
    /// An edit of the comment shown at `index` (1-based, as `CommentListView`
    /// counts), whose text was `original` when the edit began. The text is
    /// part of the key so a comment that moved or changed underneath doesn't
    /// pick up an edit typed against another one.
    case edit(index: Int, original: String)
}

nonisolated struct CommentDraftKey: Hashable, Sendable {
    let task: CommentDraftTaskKey
    let target: CommentDraftTarget
}

/// Whether one task holds a comment draft. One per task, so a draft appearing
/// on one card redraws that card only: the List draws every card (a plain
/// `VStack`, #83), and a published set would redraw them all.
@MainActor
@Observable
final class CommentDraftMark {
    fileprivate(set) var isDrafted = false
}

/// Unsent Action Items comments, new and edited, kept for the app's lifetime
/// so that collapsing a card, which tears its composer and comment editors
/// down, never drops what the owner typed. The editors write their text here
/// on every change and read it back when they are built again. Same model as
/// `KBDraftStore` and `SlotDraftStore`.
///
/// A kept draft is discarded only when
/// - the owner cancels it (Cancel in the composer or the comment editor),
/// - it lands (Send or Save, once the write succeeds; a failed write keeps it),
/// - its task is completed or archived: `reconcile` drops it when the day's
///   file next parses with the task done (one that was open when the draft
///   began), moved into an archive region, or gone, since nothing could send
///   it then,
/// - the editor is emptied, or an edit is put back to the comment's text.
///
/// Never written to the vault, or anywhere else: in memory only, so
/// `AppDelegate` asks before quitting while any remain.
@MainActor
final class CommentDraftStore {
    static let shared = CommentDraftStore()

    private struct Entry: Equatable {
        var text: String
        /// The task's subject, for the quit prompt.
        var label: String
        /// The task was already done when the draft began (a done task with a
        /// `[#TAG]` takes comments), so finding it done isn't a completion.
        var startedDone: Bool
    }

    private var entries: [CommentDraftKey: Entry] = [:]
    private var marks: [CommentDraftTaskKey: CommentDraftMark] = [:]

    func text(for key: CommentDraftKey) -> String? { entries[key]?.text }

    /// Record `text` for `key`, typed into `task`'s card. Text with nothing
    /// but whitespace, or an edit back at the comment's own text, removes the
    /// entry: there is nothing to keep.
    func update(_ text: String, for key: CommentDraftKey, task: ActionTask) {
        if Self.isEmpty(text, for: key.target) {
            entries[key] = nil
        } else {
            let startedDone = entries[key]?.startedDone ?? task.done
            entries[key] = Entry(text: text, label: task.plainSubject, startedDone: startedDone)
        }
        syncMark(key.task)
    }

    /// Drop the draft: Cancel, or the write landed.
    func discard(_ key: CommentDraftKey) {
        guard entries.removeValue(forKey: key) != nil else { return }
        syncMark(key.task)
    }

    func hasDraft(_ task: CommentDraftTaskKey) -> Bool {
        entries.keys.contains { $0.task == task }
    }

    /// The mark a card reads for its Draft marker. Made on first ask and kept,
    /// so a card holding one never misses a change.
    func mark(for task: CommentDraftTaskKey) -> CommentDraftMark {
        if let mark = marks[task] { return mark }
        let mark = CommentDraftMark()
        mark.isDrafted = hasDraft(task)
        marks[task] = mark
        return mark
    }

    /// Bring the drafts for `day` in line with its file as just parsed. A
    /// draft is kept while its task is in one of the live sections, open, or
    /// done when the draft began on a done task. One whose task is now done,
    /// archived (only in a `<details>` region) or gone is dropped. Returns the
    /// dropped keys.
    @discardableResult
    func reconcile(day: String, sections: [ActionSection]) -> [CommentDraftKey] {
        let keys = entries.keys.filter { $0.task.day == day }
        guard !keys.isEmpty else { return [] }
        var done: [String: Bool] = [:]
        for task in sections.flatMap(\.tasks) {
            let identity = CommentDraftTaskKey.identity(of: task)
            // Two untagged tasks can share a subject; one still open keeps it.
            done[identity] = (done[identity] ?? true) && task.done
        }
        var dropped: [CommentDraftKey] = []
        for key in keys {
            guard let entry = entries[key] else { continue }
            let keep: Bool
            switch done[key.task.task] {
            case nil: keep = false
            case true?: keep = entry.startedDone
            case false?: keep = true
            }
            if !keep {
                entries[key] = nil
                dropped.append(key)
            }
        }
        Set(dropped.map(\.task)).forEach(syncMark)
        return dropped
    }

    var hasUnsavedDrafts: Bool { !entries.isEmpty }

    /// The subjects of tasks holding a draft, sorted and once each, for the
    /// quit prompt.
    var unsavedTaskNames: [String] {
        Array(Set(entries.values.map(\.label))).sorted()
    }

    private func syncMark(_ task: CommentDraftTaskKey) {
        guard let mark = marks[task] else { return }
        let has = hasDraft(task)
        if mark.isDrafted != has { mark.isDrafted = has }
    }

    nonisolated static func isEmpty(_ text: String, for target: CommentDraftTarget) -> Bool {
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return true }
        if case .edit(_, let original) = target { return text == original }
        return false
    }
}

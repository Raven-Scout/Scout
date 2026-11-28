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

/// One draft's write while it is on its way. It outlives the editor that
/// sent it: collapse the card mid-Send and open it again, and the rebuilt
/// editor sees the write still going (no second Send) and, once it lands,
/// closes as the first one would have.
@MainActor
@Observable
final class CommentSendState {
    fileprivate(set) var inFlight = false
    /// Counts the writes that landed, for an editor to close on.
    fileprivate(set) var landed = 0
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
///   began), moved into an archive region (one that was live when the draft
///   began: an archived card takes comments too), or gone, since nothing
///   could send it then,
/// - it is an edit whose comment is gone: `reconcile` follows a comment that
///   moved (one above it deleted) by its text, and drops the edit when the
///   text is nowhere, or in more than one place, on its task,
/// - the editor is emptied, or an edit is put back to the comment's text.
///
/// Two untagged tasks with the same subject share one key, so they share a
/// draft: it shows on both cards. Nothing can land on the wrong one, since
/// `scoutctl` refuses an ambiguous subject and a failed Send keeps the draft.
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
        /// The card was in an archive region when the draft began, so
        /// finding it there isn't an archiving.
        var startedArchived: Bool
    }

    private var entries: [CommentDraftKey: Entry] = [:]
    private var marks: [CommentDraftTaskKey: CommentDraftMark] = [:]
    private var sends: [CommentDraftKey: CommentSendState] = [:]
    /// The tasks of the day parsed last that sit only in an archive region,
    /// by identity: where a draft begun now was typed.
    private var archivedOnly: (day: String, tasks: Set<String>)?

    func text(for key: CommentDraftKey) -> String? { entries[key]?.text }

    /// Record `text` for `key`, typed into `task`'s card. Text with nothing
    /// but whitespace, or an edit back at the comment's own text, removes the
    /// entry: there is nothing to keep.
    func update(_ text: String, for key: CommentDraftKey, task: ActionTask) {
        if Self.isEmpty(text, for: key.target) {
            entries[key] = nil
        } else {
            let old = entries[key]
            entries[key] = Entry(text: text, label: task.plainSubject,
                                 startedDone: old?.startedDone ?? task.done,
                                 startedArchived: old?.startedArchived ?? isArchived(key.task))
        }
        syncMark(key.task)
    }

    private func isArchived(_ task: CommentDraftTaskKey) -> Bool {
        guard let archivedOnly, archivedOnly.day == task.day else { return false }
        return archivedOnly.tasks.contains(task.task)
    }

    /// The write state an editor for `key` shows and waits on.
    func sendState(for key: CommentDraftKey) -> CommentSendState {
        if let state = sends[key] { return state }
        let state = CommentSendState()
        sends[key] = state
        return state
    }

    func isSending(_ key: CommentDraftKey) -> Bool { sends[key]?.inFlight ?? false }

    /// Writes `text` through `write` unless a write for `key` is already on
    /// its way; drops the kept draft once it lands. Returns whether it landed
    /// (false, too, when one was already going).
    func send(_ text: String, for key: CommentDraftKey, through write: (String) async -> Bool) async -> Bool {
        let state = sendState(for: key)
        guard !state.inFlight else { return false }
        state.inFlight = true
        let landed = await write(text)
        state.inFlight = false
        if landed {
            discard(key)
            state.landed += 1
        }
        return landed
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

    /// What one identity's tasks in a parsed file add up to. Two untagged
    /// tasks can share a subject, so it can be more than one.
    private struct Found {
        /// Every one of them is done; one still open keeps a draft.
        var allDone = true
        /// Each one's comment texts, in the order `CommentListView` counts.
        var comments: [[String]] = []

        mutating func add(_ task: ActionTask) {
            allDone = allDone && task.done
            comments.append(task.comments.map(\.text))
        }
    }

    /// Bring the drafts for `day` in line with its file as just parsed.
    ///
    /// A draft is kept while its task is in one of the live sections (or, for
    /// a draft begun on an archived card, in an archive region too), open, or
    /// done when the draft began on a done task. One whose task is now done,
    /// archived or gone is dropped. An edit whose comment moved follows it;
    /// one whose comment can't be found is dropped. Returns the dropped keys.
    @discardableResult
    func reconcile(day: String, sections: [ActionSection]) -> [CommentDraftKey] {
        var live: [String: Found] = [:]
        var anywhere: [String: Found] = [:]
        for task in sections.flatMap(\.tasks) {
            let identity = CommentDraftTaskKey.identity(of: task)
            live[identity, default: Found()].add(task)
            anywhere[identity, default: Found()].add(task)
        }
        for task in sections.flatMap(\.collapsed).flatMap(\.tasks) {
            anywhere[CommentDraftTaskKey.identity(of: task), default: Found()].add(task)
        }
        archivedOnly = (day, Set(anywhere.keys).subtracting(live.keys))

        var dropped: [CommentDraftKey] = []
        var touched: Set<CommentDraftTaskKey> = []
        for key in entries.keys where key.task.day == day {
            guard let entry = entries[key] else { continue }
            let found = (entry.startedArchived ? anywhere : live)[key.task.task]
            var keep: Bool
            switch found?.allDone {
            case nil: keep = false
            case true?: keep = entry.startedDone
            case false?: keep = true
            }
            if keep, let found, case .edit(let index, let original) = key.target {
                switch Self.place(of: original, at: index, in: found.comments) {
                case .some(index): break
                case .some(let moved):
                    entries[key] = nil
                    let to = CommentDraftKey(task: key.task, target: .edit(index: moved, original: original))
                    if entries[to] == nil { entries[to] = entry }
                case nil: keep = false
                }
            }
            if !keep {
                entries[key] = nil
                dropped.append(key)
            }
            touched.insert(key.task)
        }
        touched.forEach(syncMark)
        // Marks and send states are made per card drawn; keep only those a
        // card of this file, or a kept draft, still needs.
        marks = marks.filter { task, _ in
            hasDraft(task) || (task.day == day && anywhere[task.task] != nil)
        }
        sends = sends.filter { key, state in state.inFlight || entries[key] != nil }
        return dropped
    }

    /// Where an edit typed against comment `index` (1-based) with text
    /// `original` now is: `index` while that comment is still there, else the
    /// one place on the task holding that text, else nil (gone, or in more
    /// than one place, where a guess could land on the wrong comment).
    private static func place(of original: String, at index: Int, in tasks: [[String]]) -> Int? {
        if tasks.contains(where: { $0.indices.contains(index - 1) && $0[index - 1] == original }) {
            return index
        }
        let places = Set(tasks.flatMap { texts in
            texts.indices.filter { texts[$0] == original }.map { $0 + 1 }
        })
        return places.count == 1 ? places.first : nil
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

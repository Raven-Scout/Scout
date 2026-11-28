import Foundation
import Testing
@testable import Scout

/// A comment write that waits until the test lets it land.
@MainActor
private final class HeldWrite {
    private(set) var count = 0
    private var waiting: CheckedContinuation<Void, Never>?
    var isHeld: Bool { waiting != nil }

    func run() async -> Bool {
        count += 1
        await withCheckedContinuation { waiting = $0 }
        return true
    }

    func release() {
        waiting?.resume()
        waiting = nil
    }
}

/// Unsent comments outlive the card's composer (`CommentDraftStore`): before,
/// the draft lived in the composer's `@State`, and a click on an expanded
/// card's title collapsed it and dropped the text without a word.
@Suite("Comment drafts")
@MainActor
struct CommentDraftStoreTests {
    static let day = "2026-04-20"

    static func task(_ subject: String, tag: String? = nil, done: Bool = false,
                     comments: [String] = [], id: UUID = UUID()) -> ActionTask {
        ActionTask(id: id, lineNumber: 4, done: done, subject: subject, plainSubject: subject,
                   body: "",
                   comments: comments.enumerated().map {
                       TaskComment(author: "alex", timestamp: "", text: $1, engineIndex: $0 + 1)
                   },
                   deepLinks: [], details: [],
                   snoozedUntil: nil, carriedInFrom: nil, shortPrefix: tag)
    }

    static func section(_ tasks: [ActionTask], archived: [ActionTask] = []) -> ActionSection {
        ActionSection(
            id: UUID(), emoji: "", title: "Urgent", kind: .urgent, tasks: tasks, bullets: [],
            tables: [], subheads: [],
            collapsed: archived.isEmpty ? [] : [.init(id: UUID(), summary: "Parked", tasks: archived,
                                                      bullets: [], tables: [])])
    }

    static func key(_ task: ActionTask, _ target: CommentDraftTarget = .new,
                    day: String = day) -> CommentDraftKey {
        CommentDraftKey(task: CommentDraftTaskKey(day: day, task: task), target: target)
    }

    // MARK: Kept across a collapse

    @Test("a draft typed, then the card collapsed and opened again: the composer opens on the text")
    func collapseAndReopenKeepsTheDraft() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1")
        let key = Self.key(task)
        // What the composer's editor writes on every change.
        store.update("Ping Sam about the rollout", for: key, task: task)
        // Collapsing tears the composer down; opening the card builds a new one.
        let reopened = CommentDraftState.restored(store.text(for: key))
        #expect(reopened.expanded)
        #expect(reopened.text == "Ping Sam about the rollout")
    }

    @Test("with no kept draft the composer opens closed, as before")
    func noDraftOpensClosed() {
        let reopened = CommentDraftState.restored(CommentDraftStore().text(for: Self.key(Self.task("A"))))
        #expect(reopened == CommentDraftState())
    }

    @Test("the draft follows its task by tag when the card is renumbered")
    func taggedDraftSurvivesARenumber() {
        let store = CommentDraftStore()
        let before = Self.task("Reply to Priya", tag: "TAG1")
        store.update("draft", for: Self.key(before), task: before)
        // A run added an item above: same task, new position-derived id.
        let after = Self.task("Reply to Priya, edited", tag: "TAG1")
        #expect(store.text(for: Self.key(after)) == "draft")
    }

    @Test("a draft belongs to its day: the same tag on another day's file doesn't show it")
    func draftsAreKeptPerDay() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1")
        store.update("draft", for: Self.key(task), task: task)
        #expect(store.text(for: Self.key(task, day: "2026-04-21")) == nil)
    }

    @Test("an edit to an existing comment is kept apart from the new comment")
    func editDraftsAreSeparate() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya")
        let edit = Self.key(task, .edit(index: 1, original: "Sent the RFC"))
        store.update("Sent the RFC to Alex", for: edit, task: task)
        #expect(store.text(for: edit) == "Sent the RFC to Alex")
        #expect(store.text(for: Self.key(task)) == nil)
        // Put back to the comment's own text, there is nothing to keep.
        store.update("Sent the RFC", for: edit, task: task)
        #expect(store.text(for: edit) == nil)
    }

    @Test("an emptied editor keeps nothing")
    func whitespaceIsNoDraft() {
        let store = CommentDraftStore()
        let task = Self.task("A")
        store.update("draft", for: Self.key(task), task: task)
        store.update(" \n ", for: Self.key(task), task: task)
        #expect(store.text(for: Self.key(task)) == nil)
        #expect(!store.hasUnsavedDrafts)
    }

    // MARK: Discarded

    @Test("Cancel discards the kept draft")
    func cancelDiscards() {
        let store = CommentDraftStore()
        let task = Self.task("A")
        let key = Self.key(task)
        store.update("draft", for: key, task: task)
        var state = CommentDraftState.restored(store.text(for: key))
        CommentComposerView.cancel(&state, drafts: store, key: key)
        #expect(state == CommentDraftState())
        #expect(store.text(for: key) == nil)
        #expect(CommentDraftState.restored(store.text(for: key)) == CommentDraftState())
    }

    @Test("Send clears the kept draft once the write lands")
    func sendClears() async {
        let store = CommentDraftStore()
        let task = Self.task("A")
        let key = Self.key(task)
        store.update("Ping Sam", for: key, task: task)
        var written: [String] = []
        let landed = await CommentComposerView.send("Ping Sam", drafts: store, key: key) {
            written.append($0)
            return true
        }
        #expect(landed)
        #expect(written == ["Ping Sam"])
        #expect(store.text(for: key) == nil)
    }

    @Test("a Send that fails keeps the draft")
    func failedSendKeeps() async {
        let store = CommentDraftStore()
        let task = Self.task("A")
        let key = Self.key(task)
        store.update("Ping Sam", for: key, task: task)
        let landed = await CommentComposerView.send("Ping Sam", drafts: store, key: key) { _ in false }
        #expect(!landed)
        #expect(store.text(for: key) == "Ping Sam")
    }

    @Test("completing the task clears its draft")
    func completingClears() {
        let store = CommentDraftStore()
        let open = Self.task("Reply to Priya", tag: "TAG1")
        store.update("draft", for: Self.key(open), task: open)
        store.update("edit", for: Self.key(open, .edit(index: 1, original: "x")), task: open)
        let dropped = store.reconcile(day: Self.day,
                                      sections: [Self.section([Self.task("Reply to Priya", tag: "TAG1", done: true)])])
        #expect(dropped.count == 2)
        #expect(!store.hasDraft(CommentDraftTaskKey(day: Self.day, task: open)))
    }

    @Test("archiving the task, or its leaving the file, clears its draft")
    func archivingClears() {
        let store = CommentDraftStore()
        let archived = Self.task("Parked one", tag: "TAG2")
        let gone = Self.task("Gone one")
        store.update("a", for: Self.key(archived), task: archived)
        store.update("b", for: Self.key(gone), task: gone)
        store.reconcile(day: Self.day, sections: [Self.section([], archived: [archived])])
        #expect(!store.hasUnsavedDrafts)
    }

    @Test("an open task keeps its draft through a reparse; another day's drafts are left alone")
    func openTaskKeeps() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1")
        store.update("today", for: Self.key(task), task: task)
        store.update("tomorrow", for: Self.key(task, day: "2026-04-21"), task: task)
        store.reconcile(day: Self.day, sections: [Self.section([task])])
        store.reconcile(day: "2026-04-22", sections: [])
        #expect(store.text(for: Self.key(task)) == "today")
        #expect(store.text(for: Self.key(task, day: "2026-04-21")) == "tomorrow")
    }

    @Test("a draft begun on a done task (one with a tag takes comments) isn't a completion")
    func draftOnADoneTaskKeeps() {
        let store = CommentDraftStore()
        let done = Self.task("Shipped", tag: "TAG3", done: true)
        store.update("follow-up", for: Self.key(done), task: done)
        store.reconcile(day: Self.day, sections: [Self.section([done])])
        #expect(store.text(for: Self.key(done)) == "follow-up")
    }

    @Test("a draft typed on an archived card survives the next reparse (a scheduled run's write, say)")
    func draftOnAnArchivedCardKeeps() {
        let store = CommentDraftStore()
        let parked = Self.task("Parked one", tag: "TAG2")
        // The card is drawn from this parse, in its archive region.
        store.reconcile(day: Self.day, sections: [Self.section([], archived: [parked])])
        store.update("still relevant", for: Self.key(parked), task: parked)
        store.reconcile(day: Self.day, sections: [Self.section([], archived: [parked])])
        #expect(store.text(for: Self.key(parked)) == "still relevant")
        // Back out of the archive, it stays too; gone from the file, it goes.
        store.reconcile(day: Self.day, sections: [Self.section([parked])])
        #expect(store.text(for: Self.key(parked)) == "still relevant")
        store.reconcile(day: Self.day, sections: [Self.section([])])
        #expect(store.text(for: Self.key(parked)) == nil)
    }

    @Test("a draft on a live card still goes when its task is archived")
    func archivingALiveTaskStillClears() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1")
        store.reconcile(day: Self.day, sections: [Self.section([task])])
        store.update("draft", for: Self.key(task), task: task)
        store.reconcile(day: Self.day, sections: [Self.section([], archived: [task])])
        #expect(!store.hasUnsavedDrafts)
    }

    @Test("an edit follows its comment when one above it is deleted")
    func editFollowsAMovedComment() {
        let store = CommentDraftStore()
        let before = Self.task("Reply to Priya", tag: "TAG1", comments: ["First", "Sent the RFC"])
        let edit = Self.key(before, .edit(index: 2, original: "Sent the RFC"))
        store.update("Sent the RFC to Alex", for: edit, task: before)
        let after = Self.task("Reply to Priya", tag: "TAG1", comments: ["Sent the RFC"])
        store.reconcile(day: Self.day, sections: [Self.section([after])])
        #expect(store.text(for: edit) == nil)
        #expect(store.text(for: Self.key(after, .edit(index: 1, original: "Sent the RFC"))) == "Sent the RFC to Alex")
        #expect(store.unsavedTaskNames == ["Reply to Priya"])
    }

    @Test("an edit whose comment is gone, or now in two places, is dropped: no ghost Draft tag")
    func orphanedEditIsDropped() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1", comments: ["Sent the RFC"])
        let edit = Self.key(task, .edit(index: 1, original: "Sent the RFC"))
        let mark = store.mark(for: edit.task)
        store.update("Sent the RFC to Alex", for: edit, task: task)
        #expect(mark.isDrafted)
        store.reconcile(day: Self.day, sections: [Self.section([Self.task("Reply to Priya", tag: "TAG1",
                                                                          comments: ["Something else"])])])
        #expect(!store.hasUnsavedDrafts)
        #expect(!mark.isDrafted)
        #expect(store.unsavedTaskNames.isEmpty)

        // Moved to where the same text sits twice: a guess could edit the wrong one.
        store.update("Sent the RFC to Alex", for: Self.key(task, .edit(index: 3, original: "Sent the RFC")), task: task)
        store.reconcile(day: Self.day, sections: [Self.section([Self.task("Reply to Priya", tag: "TAG1",
                                                                          comments: ["Sent the RFC", "Sent the RFC"])])])
        #expect(!store.hasUnsavedDrafts)
    }

    @Test("a second Send while one is on its way is refused, and the rebuilt composer sees it")
    func sendInFlightIsNotRepeated() async {
        let store = CommentDraftStore()
        let task = Self.task("A")
        let key = Self.key(task)
        store.update("Ping Sam", for: key, task: task)
        let state = store.sendState(for: key)
        let write = HeldWrite()
        let first = Task { @MainActor in
            await CommentComposerView.send("Ping Sam", drafts: store, key: key) { _ in await write.run() }
        }
        while !write.isHeld { await Task.yield() }
        // Collapse and reopen: the composer built again reads the same state.
        #expect(store.sendState(for: key) === state)
        #expect(state.inFlight)
        let second = await CommentComposerView.send("Ping Sam", drafts: store, key: key) { _ in await write.run() }
        #expect(!second)
        write.release()
        #expect(await first.value)
        #expect(write.count == 1)
        #expect(!state.inFlight)
        #expect(state.landed == 1)
        #expect(store.text(for: key) == nil)
    }

    @Test("marks are pruned: only this file's cards and kept drafts hold one")
    func marksArePruned() {
        let store = CommentDraftStore()
        let here = Self.task("Here", tag: "TAG1")
        let gone = Self.task("Gone", tag: "TAG2")
        let drafted = Self.task("Drafted", tag: "TAG3")
        let hereMark = store.mark(for: CommentDraftTaskKey(day: Self.day, task: here))
        let goneMark = store.mark(for: CommentDraftTaskKey(day: Self.day, task: gone))
        let otherDay = store.mark(for: CommentDraftTaskKey(day: "2026-04-19", task: here))
        store.update("draft", for: Self.key(drafted, day: "2026-04-19"), task: drafted)
        let draftedMark = store.mark(for: CommentDraftTaskKey(day: "2026-04-19", task: drafted))
        store.reconcile(day: Self.day, sections: [Self.section([here])])
        #expect(store.mark(for: CommentDraftTaskKey(day: Self.day, task: here)) === hereMark)
        #expect(store.mark(for: CommentDraftTaskKey(day: "2026-04-19", task: drafted)) === draftedMark)
        #expect(store.mark(for: CommentDraftTaskKey(day: Self.day, task: gone)) !== goneMark)
        #expect(store.mark(for: CommentDraftTaskKey(day: "2026-04-19", task: here)) !== otherDay)
    }

    @Test("two untagged tasks with the same subject share one draft; it goes only when both are done")
    func namesakesShareADraft() {
        let store = CommentDraftStore()
        let first = Self.task("Follow up")
        let second = Self.task("Follow up")
        store.update("draft", for: Self.key(first), task: first)
        // One key for both: scoutctl refuses an ambiguous subject, so a Send
        // from either fails and keeps the draft; it can't land on the other.
        #expect(Self.key(first) == Self.key(second))
        #expect(store.text(for: Self.key(second)) == "draft")
        store.reconcile(day: Self.day, sections: [Self.section([Self.task("Follow up", done: true), second])])
        #expect(store.text(for: Self.key(first)) == "draft")
        store.reconcile(day: Self.day, sections: [Self.section([Self.task("Follow up", done: true),
                                                               Self.task("Follow up", done: true)])])
        #expect(store.text(for: Self.key(first)) == nil)
    }

    @Test("the document service reconciles on load: a completed task's draft goes, an open one's stays")
    func serviceReconcilesOnLoad() async throws {
        let dir = try DocumentServiceReparseTests.tmpDir()
        let date = DocumentServiceReparseTests.day(2026, 4, 20)
        try """
        # Action Items — synthetic

        ## 🔴 Urgent

        - [ ] [#TAG1] **Reply to Priya** — about the rollout
        - [x] [#TAG2] **Book travel** — for Sam

        """.write(to: dir.appendingPathComponent("action-items-2026-04-20.md"), atomically: true, encoding: .utf8)
        let store = CommentDraftStore()
        let open = Self.task("Reply to Priya", tag: "TAG1")
        let completed = Self.task("Book travel", tag: "TAG2")
        let day = ActionItemsDay.stem(for: date)
        store.update("keep", for: Self.key(open, day: day), task: open)
        store.update("drop", for: Self.key(completed, day: day), task: completed)
        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS(), drafts: store)
        await service.load(date: date)
        #expect(store.text(for: Self.key(open, day: day)) == "keep")
        #expect(store.text(for: Self.key(completed, day: day)) == nil)
    }

    // MARK: The Draft marker

    @Test("the Draft marker shows on a collapsed card only while a draft exists")
    func markerFollowsTheDraft() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya", tag: "TAG1")
        let mark = store.mark(for: CommentDraftTaskKey(day: Self.day, task: task))
        #expect(!TaskCardView.showsDraftMarker(expanded: false, hasDraft: mark.isDrafted))
        store.update("draft", for: Self.key(task), task: task)
        #expect(TaskCardView.showsDraftMarker(expanded: false, hasDraft: mark.isDrafted))
        // Open, the draft itself is on screen.
        #expect(!TaskCardView.showsDraftMarker(expanded: true, hasDraft: mark.isDrafted))
        store.discard(Self.key(task))
        #expect(!TaskCardView.showsDraftMarker(expanded: false, hasDraft: mark.isDrafted))
    }

    @Test("a mark asked for after the draft began starts on")
    func lateMarkStartsOn() {
        let store = CommentDraftStore()
        let task = Self.task("A")
        store.update("draft", for: Self.key(task), task: task)
        #expect(store.mark(for: CommentDraftTaskKey(day: Self.day, task: task)).isDrafted)
    }

    @Test("another task's draft doesn't mark this card")
    func markIsPerTask() {
        let store = CommentDraftStore()
        let mine = Self.task("Mine", tag: "TAG1")
        let other = Self.task("Other", tag: "TAG2")
        let mark = store.mark(for: CommentDraftTaskKey(day: Self.day, task: mine))
        store.update("draft", for: Self.key(other), task: other)
        #expect(!mark.isDrafted)
    }

    // MARK: Quit

    @Test("quitting with an unsent comment asks first and names its task")
    func quitPromptNamesTheTask() {
        let store = CommentDraftStore()
        let task = Self.task("Reply to Priya")
        store.update("draft", for: Self.key(task), task: task)
        store.update("edit", for: Self.key(task, .edit(index: 1, original: "x")), task: task)
        #expect(store.unsavedTaskNames == ["Reply to Priya"])
        #expect(AppDelegate.unsavedDraftsMessage(notes: [], slots: [], comments: store.unsavedTaskNames)
                == "1 action item has an unsent comment (Reply to Priya). Quitting discards them.")
    }
}

import Testing
import Foundation
@testable import Scout

/// Pins the C1 fixes outside Schedules and the KB: comment drafts survive a
/// failed write, collapsed cards show quick-action errors, the run Feedback
/// tab never saves over a note it couldn't read, and an unreadable
/// connector-ack file is set aside, not overwritten.
@Suite("Write safety (C1)")
@MainActor
struct WriteSafetyTests {

    // MARK: - Comment composer

    @Test("a failed send keeps the comment text in the open composer")
    func failedSendKeepsDraft() {
        var state = CommentDraftState(text: "  Ping Priya about the rollout  ", expanded: true)
        #expect(state.beginSubmit() == "Ping Priya about the rollout")
        #expect(state.text == "  Ping Priya about the rollout  ")   // not cleared while sending
        #expect(!state.canSubmit)
        state.finishSubmit(succeeded: false)
        #expect(state.text == "  Ping Priya about the rollout  ")
        #expect(state.expanded)
        #expect(state.canSubmit)
    }

    @Test("a landed send clears and collapses")
    func successfulSendClears() {
        var state = CommentDraftState(text: "done", expanded: true)
        _ = state.beginSubmit()
        state.finishSubmit(succeeded: true)
        #expect(state.text.isEmpty)
        #expect(!state.expanded)
    }

    @Test("blank text can't be sent")
    func blankNotSent() {
        var state = CommentDraftState(text: " \n ", expanded: true)
        #expect(state.beginSubmit() == nil)
    }

    // MARK: - Collapsed cards

    @Test("a collapsed card shows the quick-action error; an expanded one shows it in the detail")
    func collapsedCardShowsError() {
        #expect(TaskCardView.collapsedError(expanded: false, inlineError: "Write failed.") == "Write failed.")
        #expect(TaskCardView.collapsedError(expanded: true, inlineError: "Write failed.") == nil)
        #expect(TaskCardView.collapsedError(expanded: false, inlineError: nil) == nil)
    }

    // MARK: - Run feedback note

    /// Two runs of one type in one minute keep separate feedback files; a
    /// minute-named run keeps the file name its notes always had.
    @Test func feedbackFilesDontCollideForSameMinuteRuns() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let minute = Run.make(type: .consolidation, startedAt: t, logPath: URL(fileURLWithPath: "/x/scout-2027-01-15_08-00.log"))
        let later = Run.make(type: .consolidation, startedAt: t.addingTimeInterval(31),
                             logPath: URL(fileURLWithPath: "/x/scout-2027-01-15_08-00-31-4242.log"))
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd_HH-mm"
        #expect(FeedbackTab.slug(for: minute) == "\(fmt.string(from: t))_consolidation")
        #expect(FeedbackTab.slug(for: later) == "\(fmt.string(from: t))-31-4242_consolidation")
    }

    @Test("a note that exists but can't be read is unreadable, not empty")
    func feedbackUnreadable() {
        #expect(FeedbackTab.loadOutcome(data: nil, fileExists: true) == .unreadable)
        #expect(FeedbackTab.loadOutcome(data: Data([0xFF, 0xFE, 0xFD]), fileExists: true) == .unreadable)
        #expect(FeedbackTab.loadOutcome(data: nil, fileExists: false) == .missing)
        let file = "---\nrun_id: r1\n---\n\nGood run."
        #expect(FeedbackTab.loadOutcome(data: Data(file.utf8), fileExists: true) == .loaded("Good run."))
    }

    @Test("feedback saves only real edits, and never while unreadable")
    func feedbackSaveRule() {
        // Loading a note (or switching runs) sets the text: no write.
        #expect(!FeedbackTab.shouldSave(text: "Good run.", loadedText: "Good run.", isUnreadable: false))
        #expect(FeedbackTab.shouldSave(text: "Good run!", loadedText: "Good run.", isUnreadable: false))
        // An empty body deletes the note, so it must never follow a failed read.
        #expect(!FeedbackTab.shouldSave(text: "", loadedText: "", isUnreadable: true))
        #expect(!FeedbackTab.shouldSave(text: "typed", loadedText: "", isUnreadable: true))
    }

    // MARK: - Connector ack sidecar

    private func ackFile() throws -> URL {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("ack-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("connector-alerts-acked.json")
    }

    @Test("an unreadable ack file is moved aside, not overwritten, on the next ack")
    func unreadableAckFileIsKept() throws {
        let url = try ackFile()
        let torn = Data(#"{"fp-old": "2026-11-01T10:00:00Z", "fp-tw"#.utf8)
        try torn.write(to: url)

        let store = ConnectorAckStore(fileURL: url)
        store.ack(fingerprint: "fp-new")

        let backup = ConnectorAckStore.unreadableBackupURL(for: url)
        #expect(try Data(contentsOf: backup) == torn)
        let reloaded = try #require(ConnectorAckStore.load(from: url))
        #expect(Set(reloaded.keys) == ["fp-new"])
    }

    @Test("fractional-second dates load, so they don't read as unreadable")
    func fractionalDatesLoad() throws {
        let url = try ackFile()
        try Data(#"{"a": "2026-11-01T10:00:00.123Z", "b": "2026-11-01T10:00:00Z"}"#.utf8).write(to: url)
        let loaded = try #require(ConnectorAckStore.load(from: url))
        #expect(Set(loaded.keys) == ["a", "b"])

        let store = ConnectorAckStore(fileURL: url)
        #expect(store.isAcked("a"))
        store.ack(fingerprint: "c")
        #expect(Set(try #require(ConnectorAckStore.load(from: url)).keys) == ["a", "b", "c"])
        #expect(!FileManager.default.fileExists(atPath: ConnectorAckStore.unreadableBackupURL(for: url).path))
    }

    @Test("a missing ack file is an empty store, not an unreadable one")
    func missingAckFile() throws {
        let url = try ackFile()
        #expect(ConnectorAckStore.load(from: url) == [:])
    }
}

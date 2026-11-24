import Foundation
import Testing
@testable import Scout

/// Every writer's errors map to one plain-language problem shape (C10).
@Suite("Write problems")
@MainActor
struct WriteProblemTests {
    @Test func staleFileSaysWhatHappenedAndWhatToDo() {
        let p = WriteProblem.from(VaultWriteError.stale(file: "demo.md"), action: "save the note")
        #expect(p.kind == .stale)
        #expect(p.title == "Couldn't save the note")
        #expect(p.message.contains("demo.md changed on disk"))
        #expect(!p.retryable)
    }

    @Test func refusedUndoExplainsWhy() {
        let p = WriteProblem.from(VaultWriteError.undoStale(file: "schedule.yaml"), action: "undo edit slot")
        #expect(p.kind == .undoStale)
        #expect(p.message.contains("Scout left the file as it is"))
        // The change may have been made by hand or in another app; Scout
        // can't tell, so the message doesn't guess who made it.
        #expect(p.message.contains("schedule.yaml has changed since your edit"))
        #expect(!p.message.contains("scheduled run"))
    }

    @Test func engineOutputGoesToDetailsNotTheMessage() {
        let err = ActionItemsWriterError.cliNonZeroExit(exitCode: 22, stderr: "Error: no task #QZX7", classification: .noMatch)
        let p = WriteProblem.from(err, action: "mark the task done")
        #expect(p.kind == .notFound)
        #expect(!p.message.contains("Error:"))
        #expect(p.details?.contains("no task #QZX7") == true)
    }

    @Test func missingEngineCommandPointsAtSettings() {
        let err = ActionItemsWriterError.cliNonZeroExit(exitCode: 2, stderr: "No such option: --undo", classification: .environment)
        let p = WriteProblem.from(err, action: "reopen the task")
        #expect(p.kind == .environment)
        #expect(p.message.contains("Settings ▸ Engine"))
        #expect(p.retryable)
    }

    @Test func untaggedStaleWriteMapsToStale() {
        #expect(WriteProblem.from(ActionItemsWriterError.stale(file: "a.md"), action: "x").kind == .stale)
    }

    @Test func kbConflictAndCommitFailure() {
        #expect(WriteProblem.from(KBWriterError.conflict(file: "n.md"), action: "save").kind == .stale)
        let commit = WriteProblem.from(KBWriterError.commitFailed("index.lock exists"), action: "save")
        #expect(commit.kind == .committedOnDiskOnly)
        #expect(commit.title == "Saved, but not committed")
        #expect(commit.details == "index.lock exists")
    }

    @Test func scheduleErrors() {
        #expect(WriteProblem.from(StaleScheduleError(loadedAt: nil, modifiedAt: Date()), action: "save the slot").kind == .stale)
        #expect(WriteProblem.from(ScheduleNotLoadedError(), action: "save the slot").kind == .stale)
    }

    @Test func fieldWriteErrors() {
        #expect(WriteProblem.from(GuardedFileWrite.Failure.conflictPersisted, action: "approve").retryable)
        #expect(WriteProblem.from(PerFileItemWriterError.fieldNotFound(field: "status", file: "p.md"), action: "approve").message.contains("p.md"))
        #expect(WriteProblem.from(PerFileItemWriterError.emptyTitle, action: "add").message == "Give the item a title first.")
    }

    @Test func blankDetailsAreDropped() {
        let p = WriteProblem(kind: .failed, title: "t", message: "m", details: "  \n", retryable: true)
        #expect(p.details == nil)
    }
}

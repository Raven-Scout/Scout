import Foundation
import Testing
@testable import Scout

/// One test group per write path the app has (C10): each covers a stale file,
/// a failed write, and undo (plus redo where it differs).
///
/// Every fixture is invented: `[#TAG]`s, titles and slots appear in no vault.

/// A throwaway vault: `<tmp>/knowledge-base`, `<tmp>/action-items`, … .
private struct TempVault {
    let root: URL
    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("c10-vault-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    func url(_ rel: String) -> URL { root.appendingPathComponent(rel) }
    @discardableResult
    func write(_ rel: String, _ text: String) throws -> URL {
        let u = url(rel)
        try FileManager.default.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: u, atomically: true, encoding: .utf8)
        return u
    }
    func read(_ rel: String) -> String? { try? String(contentsOf: url(rel), encoding: .utf8) }
    func exists(_ rel: String) -> Bool { FileManager.default.fileExists(atPath: url(rel).path) }
}

private let day = Calendar(identifier: .iso8601).date(from: DateComponents(
    timeZone: TimeZone.current, year: 2026, month: 4, day: 20))!
private let dayFile = "action-items/action-items-2026-04-20.md"
private let openTask = "## 🔥 Urgent\n\n- [ ] [#QZX7] **Draft the demo outline** — for Priya\n"
private let doneTask = "## 🔥 Urgent\n\n- [x] [#QZX7] **Draft the demo outline** — for Priya\n"

// MARK: - Action items (scoutctl)

@Suite("C10 write path: action items")
struct ActionItemsWritePathTests {
    private func writer(_ vault: TempVault, _ runner: any ProcessRunner) -> ActionItemsWriter {
        ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: vault.url("action-items"),
            scoutDirectory: vault.root,
            runner: runner,
            gitService: nil)
    }

    /// A runner that plays scoutctl: rewrites the day file per call.
    private func scoutctl(_ vault: TempVault, _ rewrite: @escaping @Sendable ([String]) -> String?) -> RecordingRunner {
        let runner = RecordingRunner()
        let file = vault.url(dayFile)
        runner.onCall = { call in
            if let text = rewrite(call.arguments) { try? text.write(to: file, atomically: true, encoding: .utf8) }
        }
        return runner
    }

    @Test func markDoneRecordsTheDayFileBeforeAndAfter() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let runner = scoutctl(vault) { $0.contains("--undo") ? openTask : doneTask }
        let result = try await writer(vault, runner).submit(
            .markDone(subject: "Draft the demo outline", shortPrefix: "QZX7"), displayedDate: day)
        let change = try #require(result.change)
        #expect(change.actionName == "Mark Done")
        #expect(change.files.map(\.lineDelta) == [LineDelta(removed: 1, added: 1)])
        #expect(!result.undoUnavailable)
    }

    @Test @MainActor func undoOfTaggedDoneRunsTheEngineInverse() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let runner = scoutctl(vault) { $0.contains("--undo") ? openTask : doneTask }
        let w = writer(vault, runner)
        let op = WriteOp.markDone(subject: "Draft the demo outline", shortPrefix: "QZX7")
        let result = try await w.submit(op, displayedDate: day)
        // A scheduled run edits another line in between: the inverse still
        // works, because it goes by [#TAG], not by restoring bytes.
        try vault.write(dayFile, doneTask + "- [ ] [#WVB2] **Another task**\n")

        let step = try #require(ActionItemsView.undoStep(for: op, result: result, writer: w, date: day))
        try await step.undo()
        let last = try #require(await runner.calls.last)
        #expect(last.arguments.contains("--undo"))
        #expect(last.arguments.contains("QZX7"))
        #expect(vault.read(dayFile) == openTask)
    }

    @Test func untaggedWriteIsRefusedWhenTheDayFileChangedSinceShown() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let shown = try FileSnapshot.capture(vault.url(dayFile)).digest
        try vault.write(dayFile, "- [ ] **A run rewrote this day**\n" + openTask)
        let runner = RecordingRunner()

        await #expect(throws: ActionItemsWriterError.stale(file: "action-items-2026-04-20.md")) {
            try await writer(vault, runner).submit(
                .markDone(subject: "Draft the demo outline", shortPrefix: nil), displayedDate: day, shownDigest: shown)
        }
        #expect(await runner.calls.isEmpty)
    }

    @Test func taggedWriteIgnoresAStaleDigest() async throws {
        // Tagged ops are re-resolved on the current bytes by scoutctl.
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let runner = scoutctl(vault) { _ in doneTask }
        _ = try await writer(vault, runner).submit(
            .markDone(subject: "x", shortPrefix: "QZX7"), displayedDate: day, shownDigest: Data([1, 2, 3]))
        #expect(await runner.calls.count == 1)
    }

    @Test func failedWriteThrowsAndLeavesTheFile() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        await #expect(throws: ActionItemsWriterError.self) {
            try await writer(vault, FailingRunner(exit: 22, stderr: "Error: no task #QZX7")).submit(
                .markDone(subject: "x", shortPrefix: "QZX7"), displayedDate: day)
        }
        #expect(vault.read(dayFile) == openTask)
    }

    @Test func aRunEditDuringTheWriteLeavesNoByteUndo() async throws {
        // Review finding 1: B0 → a run's B1 → the app's B2. Recording (B0, B2)
        // would let undo drop the run's line, so the change is discarded.
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let runAndSnooze = "## 🔥 Urgent\n\n- [ ] [#WVB2] **A run added this**\n- [ ] [#QZX7] **Draft the demo outline** — for Priya\n  - snoozed-until: 2026-04-22\n"
        let runner = scoutctl(vault) { _ in runAndSnooze }
        let result = try await writer(vault, runner).submit(
            .snooze(subject: "x", shortPrefix: "QZX7", until: day, fromKind: nil), displayedDate: day)
        #expect(result.change == nil)
        #expect(result.undoUnavailable)
        #expect(vault.read(dayFile) == runAndSnooze)
    }

    @Test func aRunEditFarFromTheOpIsAlsoCaught() async throws {
        let vault = try TempVault()
        let longDay = openTask + "\n## 👀 Watch\n\n- [ ] [#WVB2] **Keep an eye on the tracing job**\n"
        try vault.write(dayFile, longDay)
        let both = doneTask + "\n## 👀 Watch\n\n- [x] [#WVB2] **Keep an eye on the tracing job**\n"
        let runner = scoutctl(vault) { _ in both }
        let result = try await writer(vault, runner).submit(
            .markDone(subject: "Draft the demo outline", shortPrefix: nil), displayedDate: day)
        #expect(result.change == nil)
        #expect(result.undoUnavailable)
    }

    @Test func expectedLineShapes() {
        #expect(WriteOp.snooze(subject: "s", shortPrefix: nil, until: day, fromKind: nil).expectedLineDelta == LineDelta(removed: 0, added: 1))
        #expect(WriteOp.deleteComment(subject: "s", shortPrefix: nil, selector: .index(1)).expectedLineDelta == LineDelta(removed: 1, added: 0))
        #expect(WriteOp.addComment(subject: "s", shortPrefix: nil, text: "two\nlines", author: "alex").expectedLineDelta == nil)
    }

    @Test func snoozeUndoRestoresTheBytesAndRedoReappliesThem() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let snoozed = openTask + "  - snoozed-until: 2026-04-22 (from-kind: urgent)\n"
        let runner = scoutctl(vault) { _ in snoozed }
        let w = writer(vault, runner)
        let op = WriteOp.snooze(subject: "x", shortPrefix: "QZX7", until: day, fromKind: "urgent")
        let change = try #require(try await w.submit(op, displayedDate: day).change)
        #expect(op.inverse == nil)

        try await w.restore(change, .undo)
        #expect(vault.read(dayFile) == openTask)
        try await w.restore(change, .redo)
        #expect(vault.read(dayFile) == snoozed)
    }

    @Test func snoozeUndoIsRefusedAfterARunRewroteTheDay() async throws {
        let vault = try TempVault()
        try vault.write(dayFile, openTask)
        let runner = scoutctl(vault) { _ in openTask + "  - snoozed-until: 2026-04-22\n" }
        let w = writer(vault, runner)
        let change = try #require(try await w.submit(
            .snooze(subject: "x", shortPrefix: "QZX7", until: day, fromKind: nil), displayedDate: day).change)
        let rewritten = "## 🔥 Urgent\n\n- [ ] [#WVB2] **Consolidation rewrote the day**\n"
        try vault.write(dayFile, rewritten)

        await #expect(throws: VaultWriteError.undoStale(file: "action-items-2026-04-20.md")) {
            try await w.restore(change, .undo)
        }
        #expect(vault.read(dayFile) == rewritten)
    }
}

// MARK: - Topics board (scoutctl)

@Suite("C10 write path: Topics board")
struct BoardWritePathTests {
    private func writer(_ vault: TempVault, _ runner: any ProcessRunner) -> BoardWriter {
        BoardWriter(scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
                    actionItemsDirectory: vault.url("action-items"), scoutDirectory: vault.root,
                    runner: runner, git: nil)
    }

    @Test func dismissIsUndoableAndRedoable() async throws {
        let vault = try TempVault()
        let file = "action-items/\(DismissedFileLoader.fileName)"
        try vault.write(file, "- https://a.example/1\n")
        let runner = RecordingRunner()
        let url = vault.url(file)
        runner.onCall = { _ in try? "- https://a.example/1\n- https://a.example/2\n".write(to: url, atomically: true, encoding: .utf8) }
        let w = writer(vault, runner)
        let change = try #require(try await w.submit(.dismiss(key: "https://a.example/2")).change)
        #expect(change.actionName == "Dismiss Entry")

        try await w.restore(change, .undo)
        #expect(vault.read(file) == "- https://a.example/1\n")
        try await w.restore(change, .redo)
        #expect(vault.read(file)?.contains("a.example/2") == true)
    }

    @Test func dismissUndoOfANewFileRemovesIt() async throws {
        let vault = try TempVault()
        let file = "action-items/\(DismissedFileLoader.fileName)"
        let runner = RecordingRunner()
        let url = vault.url(file)
        runner.onCall = { _ in
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? "- https://a.example/2\n".write(to: url, atomically: true, encoding: .utf8)
        }
        let w = writer(vault, runner)
        let change = try #require(try await w.submit(.dismiss(key: "https://a.example/2")).change)
        try await w.restore(change, .undo)
        #expect(!vault.exists(file))
    }

    @Test func aDismissMixedWithAnotherEditHasNoByteUndo() async throws {
        let vault = try TempVault()
        let file = "action-items/\(DismissedFileLoader.fileName)"
        try vault.write(file, "- a\n")
        let runner = RecordingRunner()
        let url = vault.url(file)
        runner.onCall = { _ in try? "- someone else\n- a\n- k\n".write(to: url, atomically: true, encoding: .utf8) }
        let result = try await writer(vault, runner).submit(.dismiss(key: "k"))
        #expect(result.change == nil)
        #expect(result.undoUnavailable)
    }

    @Test func failedDismissThrowsAndHasNothingToUndo() async throws {
        let vault = try TempVault()
        await #expect(throws: ActionItemsWriterError.self) {
            try await writer(vault, FailingRunner(exit: 1, stderr: "Error: no such entry")).submit(.dismiss(key: "k"))
        }
    }

    @Test func dismissUndoIsRefusedWhenTheListChangedSince() async throws {
        let vault = try TempVault()
        let file = "action-items/\(DismissedFileLoader.fileName)"
        try vault.write(file, "")
        let runner = RecordingRunner()
        let url = vault.url(file)
        runner.onCall = { _ in try? "- k\n".write(to: url, atomically: true, encoding: .utf8) }
        let w = writer(vault, runner)
        let change = try #require(try await w.submit(.dismiss(key: "k")).change)
        try vault.write(file, "- k\n- another\n")
        await #expect(throws: VaultWriteError.undoStale(file: DismissedFileLoader.fileName)) {
            try await w.restore(change, .undo)
        }
    }
}

// MARK: - Proposals (frontmatter field)

private let proposal = """
---
title: Add a re-check step
status: Proposed (awaiting Alex approval)
---

# Add a re-check step
"""

@Suite("C10 write path: proposals")
struct ProposalsWritePathTests {
    private func writer(_ vault: TempVault) -> ProposalsWriter {
        ProposalsWriter(scoutDirectory: vault.root, gitService: nil,
                        now: { Date(timeIntervalSince1970: 1_780_000_000) })
    }

    @Test func approveIsUndoable() async throws {
        let vault = try TempVault()
        let url = try vault.write("dreaming-proposals/p.md", proposal)
        let w = writer(vault)
        let change = try #require(try await w.decide(.approve, fileURL: url, label: "p"))
        #expect(change.actionName == "Approve")
        #expect(vault.read("dreaming-proposals/p.md")?.contains("status: Approved") == true)

        try await w.restore(change, .undo)
        #expect(vault.read("dreaming-proposals/p.md") == proposal)
    }

    @Test func undoIsRefusedOnceADreamingRunActedOnIt() async throws {
        let vault = try TempVault()
        let url = try vault.write("dreaming-proposals/p.md", proposal)
        let w = writer(vault)
        let change = try #require(try await w.decide(.approve, fileURL: url, label: "p"))
        let applied = proposal.replacingOccurrences(of: "Proposed (awaiting Alex approval)", with: "Applied (2026-06-15)")
        try vault.write("dreaming-proposals/p.md", applied)

        await #expect(throws: VaultWriteError.undoStale(file: "p.md")) { try await w.restore(change, .undo) }
        #expect(vault.read("dreaming-proposals/p.md") == applied)
    }

    @Test func aFileWithoutStatusFailsAndIsLeftAlone() async throws {
        let vault = try TempVault()
        let url = try vault.write("dreaming-proposals/p.md", "# no frontmatter\n")
        await #expect(throws: ProposalsWriterError.frontmatterNotFound(file: "p.md")) {
            try await writer(vault).decide(.decline, fileURL: url, label: "p")
        }
        #expect(vault.read("dreaming-proposals/p.md") == "# no frontmatter\n")
    }

    @Test func aStaleFileIsReappliedNotClobbered() async throws {
        // A field write is targeted: it lands on whatever the file holds now,
        // keeping the other writer's change.
        let vault = try TempVault()
        let url = try vault.write("dreaming-proposals/p.md", proposal + "\nA run appended this.\n")
        _ = try await writer(vault).decide(.approve, fileURL: url, label: "p")
        let text = try #require(vault.read("dreaming-proposals/p.md"))
        #expect(text.contains("A run appended this."))
        #expect(text.contains("status: Approved"))
    }
}

// MARK: - Wishlist / Research (frontmatter field)

@Suite("C10 write path: wishlist and research")
struct PerFileWritePathTests {
    private let item = "---\ntitle: Try the tracing job\nstatus: open\npriority: high\n---\n\n# Try the tracing job\n"

    @Test func markDoneIsUndoable() async throws {
        let vault = try TempVault()
        let url = try vault.write("wishlist/t.md", item)
        let w = PerFileItemWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.resolve(.done, fileURL: url, label: "t"))
        #expect(change.actionName == "Mark Done")
        try await w.restore(change, .undo)
        #expect(vault.read("wishlist/t.md") == item)
    }

    @Test func undoIsRefusedAfterAnotherEdit() async throws {
        let vault = try TempVault()
        let url = try vault.write("wishlist/t.md", item)
        let w = PerFileItemWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.setPriority(.low, fileURL: url, label: "t"))
        try vault.write("wishlist/t.md", item + "\nProgress: a dreaming run logged this.\n")
        await #expect(throws: VaultWriteError.undoStale(file: "t.md")) { try await w.restore(change, .undo) }
    }

    @Test func aFileWithoutFrontmatterFails() async throws {
        let vault = try TempVault()
        let url = try vault.write("wishlist/t.md", "just text\n")
        await #expect(throws: PerFileItemWriterError.frontmatterNotFound(file: "t.md")) {
            try await PerFileItemWriter(scoutDirectory: vault.root, gitService: nil).setStatus(.done, fileURL: url, label: "t")
        }
        #expect(vault.read("wishlist/t.md") == "just text\n")
    }
}

// MARK: - Knowledge base (whole-file writes)

@Suite("C10 write path: knowledge base")
struct KnowledgeBaseWritePathTests {
    private let note = "knowledge-base/projects/demo.md"

    @Test func saveIsUndoableAndRedoable() async throws {
        let vault = try TempVault()
        let url = try vault.write(note, "v1\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.save(fileURL: url, contents: "v2\n", baselineContents: "v1\n", label: "demo"))
        #expect(change.actionName == "Edit Note")
        try await w.restore(change, .undo)
        #expect(vault.read(note) == "v1\n")
        try await w.restore(change, .redo)
        #expect(vault.read(note) == "v2\n")
    }

    @Test func saveOverAStaleNoteIsRefused() async throws {
        let vault = try TempVault()
        let url = try vault.write(note, "a run's version\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        await #expect(throws: KBWriterError.conflict(file: "demo.md")) {
            try await w.save(fileURL: url, contents: "mine\n", baselineContents: "v1\n", label: "demo")
        }
        #expect(vault.read(note) == "a run's version\n")
    }

    @Test func saveUndoIsRefusedAfterARunEditedTheNote() async throws {
        let vault = try TempVault()
        let url = try vault.write(note, "v1\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.save(fileURL: url, contents: "v2\n", baselineContents: "v1\n", label: "demo"))
        try vault.write(note, "v2\nResearch appended this.\n")
        await #expect(throws: VaultWriteError.undoStale(file: "demo.md")) { try await w.restore(change, .undo) }
        #expect(vault.read(note) == "v2\nResearch appended this.\n")
    }

    @Test func saveComparesBytesNotCanonicalText() async throws {
        // Review finding 6: NFC and NFD spell "é" differently on disk but
        // compare equal as Strings. A byte compare sees the change.
        let vault = try TempVault()
        let url = try vault.write(note, "Cafe\u{301}\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        await #expect(throws: KBWriterError.conflict(file: "demo.md")) {
            try await w.save(fileURL: url, contents: "mine\n", baselineContents: "Caf\u{E9}\n", label: "demo")
        }
        #expect(try Data(contentsOf: url) == Data("Cafe\u{301}\n".utf8))
    }

    @Test func undoNeverTouchesAnUnreadableFile() async throws {
        // Review finding 3: "can't read" must not pass for "doesn't exist".
        let vault = try TempVault()
        let url = try vault.write(note, "keep me\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.delete(fileURL: url, label: "demo"))
        try vault.write(note, "recreated, then locked\n")
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: url.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path) }

        await #expect(throws: VaultWriteError.self) { try await w.restore(change, .undo) }
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
        #expect(vault.read(note) == "recreated, then locked\n")
    }

    @Test func deleteIsUndoable() async throws {
        let vault = try TempVault()
        let url = try vault.write(note, "keep me\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        let change = try #require(try await w.delete(fileURL: url, label: "demo"))
        #expect(!vault.exists(note))
        try await w.restore(change, .undo)
        #expect(vault.read(note) == "keep me\n")
    }

    @Test func renameIsUndoable() async throws {
        let vault = try TempVault()
        let url = try vault.write(note, "body\n")
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        let (dest, change) = try await w.renameReturningChange(fileURL: url, to: "renamed")
        #expect(dest.lastPathComponent == "renamed.md")
        try await w.restore(try #require(change), .undo)
        #expect(vault.read(note) == "body\n")
        #expect(!vault.exists("knowledge-base/projects/renamed.md"))
    }

    @Test func deleteOfAMissingNoteFails() async throws {
        let vault = try TempVault()
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        await #expect(throws: KBWriterError.notFound("demo.md")) {
            try await w.delete(fileURL: vault.url(note), label: "demo")
        }
    }

    @Test func restoreNeverWritesOutsideTheKnowledgeBase() async throws {
        let vault = try TempVault()
        let outside = try vault.write("action-items/x.md", "a\n")
        let change = try #require(VaultChange(actionName: "Edit Note",
                                              before: [.text(outside, "b\n")], after: [.text(outside, "a\n")]))
        let w = KnowledgeBaseFileWriter(scoutDirectory: vault.root, gitService: nil)
        await #expect(throws: KBWriterError.outsideKnowledgeBase("x.md")) { try await w.restore(change, .undo) }
        #expect(vault.read("action-items/x.md") == "a\n")
    }
}

// MARK: - Schedule (whole-file write via scoutctl validate)

private let scheduleJSON = """
[{"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
  "fires_at_local":"08:00","weekdays":["Mon"],"missed_window_hours":4,
  "on_miss":"fire","cooldown_minutes":60,"budget_usd":null,"tz":null,"runtime":"local"}]
"""
private let scheduleYAML = """
schema_version: 1

slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
"""

@Suite("C10 write path: schedule")
@MainActor
struct ScheduleWritePathTests {
    private func service(_ vault: TempVault, validateExit: Int32 = 0) throws -> ScheduleEditService {
        let path = try vault.write("schedule.yaml", scheduleYAML)
        return ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: Array(repeating: scheduleJSON, count: 6),
                                       validateExitCode: validateExit, validateStderr: Data("bad slot".utf8)),
            canonicalSchedulePath: path,
            argumentsPrefix: ["scoutctl"])
    }

    private func edited(_ s: ScheduleEditService) -> [Slot] {
        s.slots + [SchedulesView.makeNewDraftSlot(key: "new-slot-1")]
    }

    @Test func saveIsUndoableAndRedoable() async throws {
        let vault = try TempVault()
        let s = try service(vault)
        try await s.loadAll()
        let change = try #require(try await s.save(allSlots: edited(s), actionName: "Edit Slot"))
        #expect(vault.read("schedule.yaml")?.contains("new-slot-1") == true)

        try await s.restore(change, .undo)
        #expect(vault.read("schedule.yaml") == scheduleYAML)
        try await s.restore(change, .redo)
        #expect(vault.read("schedule.yaml")?.contains("new-slot-1") == true)
    }

    @Test func aSameMtimeRewriteIsStillCaughtAsStale() async throws {
        // The content compare catches what a coarse mtime compare can't.
        let vault = try TempVault()
        let s = try service(vault)
        try await s.loadAll()
        let path = vault.url("schedule.yaml")
        let mtime = try #require(GuardedFileWrite.fsModificationDate(path))
        try vault.write("schedule.yaml", scheduleYAML.replacingOccurrences(of: "08:00", with: "07:00"))
        try FileManager.default.setAttributes([.modificationDate: mtime], ofItemAtPath: path.path)

        await #expect(throws: StaleScheduleError.self) { try await s.save(allSlots: edited(s)) }
        #expect(vault.read("schedule.yaml")?.contains("07:00") == true)
    }

    @Test func aFailedValidationLeavesTheFile() async throws {
        let vault = try TempVault()
        let s = try service(vault, validateExit: 1)
        try await s.loadAll()
        await #expect(throws: (any Error).self) { try await s.save(allSlots: edited(s)) }
        #expect(vault.read("schedule.yaml") == scheduleYAML)
    }

    @Test func undoIsRefusedAfterTheScheduleChangedElsewhere() async throws {
        let vault = try TempVault()
        let s = try service(vault)
        try await s.loadAll()
        let change = try #require(try await s.save(allSlots: edited(s)))
        let cli = scheduleYAML.replacingOccurrences(of: "08:00", with: "06:45")
        try vault.write("schedule.yaml", cli)

        await #expect(throws: VaultWriteError.undoStale(file: "schedule.yaml")) { try await s.restore(change, .undo) }
        #expect(vault.read("schedule.yaml") == cli)
    }

    @Test func aWriteDuringValidationIsNotClobbered() async throws {
        // Review finding 2: the stale check ran before `schedule validate`.
        let vault = try TempVault()
        let path = try vault.write("schedule.yaml", scheduleYAML)
        let cli = scheduleYAML.replacingOccurrences(of: "08:00", with: "06:45")
        let s = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: EditDuringValidateRunner(list: scheduleJSON, file: path, edit: cli),
            canonicalSchedulePath: path,
            argumentsPrefix: ["scoutctl"])
        try await s.loadAll()
        await #expect(throws: StaleScheduleError.self) { try await s.save(allSlots: edited(s)) }
        #expect(vault.read("schedule.yaml") == cli)
    }

    @Test func deleteIsUndoable() async throws {
        let vault = try TempVault()
        let s = try service(vault)
        try await s.loadAll()
        let change = try #require(try await s.delete(slotKey: "morning-briefing"))
        #expect(change.actionName == "Delete Slot")
        try await s.restore(change, .undo)
        #expect(vault.read("schedule.yaml") == scheduleYAML)
    }
}

/// `scoutctl` for the schedule: lists `list`, and on `schedule validate`
/// rewrites `file` with `edit` (another writer landing mid-save) before
/// passing.
private struct EditDuringValidateRunner: ProcessRunner {
    let list: String
    let file: URL
    let edit: String
    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        if arguments.contains("validate") {
            try edit.write(to: file, atomically: true, encoding: .utf8)
            return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
        }
        return ProcessResult(exitCode: 0, stdout: Data(list.utf8), stderr: Data())
    }
}

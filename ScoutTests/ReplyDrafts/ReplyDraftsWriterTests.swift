import Testing
import Foundation
@testable import Scout

private let writerFixture = """
---
tag: NAHSEND
channel: email
loop_type: direct-debt
to: "Priya <priya@example.com>"
thread_ref: "https://mail.google.com/mail/u/0/#inbox/abc123"
subject: "Re: Q3 budget"
status: draft
created: 2026-06-29
context_answer_ref: ""
---

Hi Priya,

sending over the numbers. [TBD: amount]
"""

@Suite("ReplyDraftsWriter.rewriteFrontmatterStatus (pure)")
struct ReplyDraftsWriterRewriteTests {

    @Test func replacesOnlyTheFrontmatterStatusValue() throws {
        let out = try ReplyDraftsWriter.rewriteFrontmatterStatus(
            text: writerFixture, newStatusValue: "sent", file: "NAHSEND.md")
        #expect(out.contains("status: sent"))
        #expect(!out.contains("status: draft"))
        // Other frontmatter fields untouched.
        #expect(out.contains("tag: NAHSEND"))
        #expect(out.contains("subject: \"Re: Q3 budget\""))
    }

    @Test func leavesBodyByteIdentical() throws {
        let out = try ReplyDraftsWriter.rewriteFrontmatterStatus(
            text: writerFixture, newStatusValue: "dismissed", file: "NAHSEND.md")
        #expect(out.contains("Hi Priya,"))
        #expect(out.contains("sending over the numbers. [TBD: amount]"))
    }

    @Test func reparsingTheRewriteReflectsTheNewStatus() throws {
        let out = try ReplyDraftsWriter.rewriteFrontmatterStatus(
            text: writerFixture, newStatusValue: "sent", file: "NAHSEND.md")
        let d = try #require(ReplyDraftsParser.parseFile(
            contents: out, fileURL: URL(fileURLWithPath: "/x/NAHSEND.md")))
        #expect(d.status == .sent)
    }

    @Test func noFrontmatterThrows() {
        #expect(throws: ReplyDraftsWriterError.self) {
            try ReplyDraftsWriter.rewriteFrontmatterStatus(
                text: "# Just a heading\n\nbody", newStatusValue: "sent", file: "p.md")
        }
    }

    @Test func frontmatterWithoutStatusFieldThrows() {
        let text = "---\ntag: T\nchannel: email\n---\n\nbody"
        #expect(throws: ReplyDraftsWriterError.self) {
            try ReplyDraftsWriter.rewriteFrontmatterStatus(
                text: text, newStatusValue: "sent", file: "p.md")
        }
    }

    @Test func preservesIndentationOnStatusLine() throws {
        let text = "---\n  status: draft\n---\nbody"
        let out = try ReplyDraftsWriter.rewriteFrontmatterStatus(
            text: text, newStatusValue: "sent", file: "p.md")
        #expect(out.contains("  status: sent"))
    }

    /// An opening `---` with no closing fence is not frontmatter. Without the
    /// fence check the scan runs into the body and rewrites the first line that
    /// happens to look like `status:` — the writer re-reads the file at write
    /// time, so it can see a truncated version the parser never accepted.
    @Test func unterminatedFrontmatterThrowsRatherThanEditingTheBody() {
        let text = "---\ntag: T\n\nHi — here is the status: draft we discussed.\n"
        #expect(throws: ReplyDraftsWriterError.frontmatterNotFound(file: "p.md")) {
            try ReplyDraftsWriter.rewriteFrontmatterStatus(
                text: text, newStatusValue: "sent", file: "p.md")
        }
    }
}

@Suite("ReplyDraftsWriter.fillPlaceholder (pure)")
struct ReplyDraftsFillTests {

    @Test func replacesTheOnlyOccurrenceWithValue() {
        let text = "I'll confirm the time [TBD: check the calendar] and get back to you."
        let out = ReplyDraftsWriter.fillPlaceholder(
            text: text, placeholder: "[TBD: check the calendar]", occurrence: 0, value: "Thursday 14:00")
        #expect(out == "I'll confirm the time Thursday 14:00 and get back to you.")
        #expect(!out.contains("[TBD"))
    }

    @Test func missingPlaceholderReturnsUnchanged() {
        let text = "Nothing to fill here."
        let out = ReplyDraftsWriter.fillPlaceholder(
            text: text, placeholder: "[TBD: x]", occurrence: 0, value: "y")
        #expect(out == text)
    }

    /// Identical markers must be fillable independently — replacing "the first
    /// one" every time made the second marker permanently unfillable.
    @Test func replacesTheRequestedOccurrenceNotTheFirst() {
        let text = "[TBD: a] then [TBD: a]"
        #expect(ReplyDraftsWriter.fillPlaceholder(
            text: text, placeholder: "[TBD: a]", occurrence: 0, value: "X") == "X then [TBD: a]")
        #expect(ReplyDraftsWriter.fillPlaceholder(
            text: text, placeholder: "[TBD: a]", occurrence: 1, value: "Y") == "[TBD: a] then Y")
    }

    @Test func occurrencePastTheEndReturnsUnchanged() {
        let text = "[TBD: a] then [TBD: a]"
        #expect(ReplyDraftsWriter.fillPlaceholder(
            text: text, placeholder: "[TBD: a]", occurrence: 2, value: "Z") == text)
    }
}

@Suite("ReplyDraftsWriter end-to-end (file + git commit)")
struct ReplyDraftsWriterE2ETests {

    private func makeDraftsDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("drafts-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("drafts"),
            withIntermediateDirectories: true)
        return dir
    }

    @Test func markSentWritesStatusAndCommitsScopedToFile() async throws {
        let repo = try makeDraftsDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("drafts/NAHSEND.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        // rev-parse(0) → add(0) → diff(1=dirty) → commit(0)
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ReplyDraftsWriter(scoutDirectory: repo, gitService: git)

        try await writer.apply(.markSent, fileURL: fileURL, label: "NAHSEND")

        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: sent"))

        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: mark-sent reply draft NAHSEND"))
        #expect(commit.arguments.contains("drafts/NAHSEND.md"))
    }

    @Test func dismissWritesDismissedStatus() async throws {
        let repo = try makeDraftsDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("drafts/NAHSEND.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ReplyDraftsWriter(scoutDirectory: repo, gitService: git)

        try await writer.apply(.dismiss, fileURL: fileURL, label: "NAHSEND")

        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: dismissed"))
        let d = ReplyDraftsParser.parseFile(contents: written, fileURL: fileURL)
        #expect(d?.status == .dismissed)
    }

    @Test func fileWithoutFrontmatterThrowsAndDoesNotCommit() async throws {
        let repo = try makeDraftsDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("drafts/README.md")
        let original = "# Reply Drafts, no frontmatter\n"
        try original.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ReplyDraftsWriter(scoutDirectory: repo, gitService: git)

        await #expect(throws: ReplyDraftsWriterError.self) {
            try await writer.apply(.markSent, fileURL: fileURL, label: "README")
        }
        #expect(runner.calls.isEmpty)
        #expect(try String(contentsOf: fileURL, encoding: .utf8) == original)
    }
}

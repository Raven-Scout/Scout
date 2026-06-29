import Testing
import Foundation
@testable import Scout

private let writerFixture = """
---
tag: NAHSEND
channel: email
loop_type: direct-debt
to: "Jan Novák <jan@firma.cz>"
thread_ref: "https://mail.google.com/mail/u/0/#inbox/abc123"
subject: "Re: Rozpočet Q3"
status: draft
created: 2026-06-29
context_answer_ref: ""
---

Ahoj Jane,

posílám čísla. [TBD: částka]
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
        #expect(out.contains("subject: \"Re: Rozpočet Q3\""))
    }

    @Test func leavesBodyByteIdentical() throws {
        let out = try ReplyDraftsWriter.rewriteFrontmatterStatus(
            text: writerFixture, newStatusValue: "dismissed", file: "NAHSEND.md")
        #expect(out.contains("Ahoj Jane,"))
        #expect(out.contains("posílám čísla. [TBD: částka]"))
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

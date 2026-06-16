import Testing
import Foundation
@testable import Scout

// A per-file proposal: frontmatter (with `status:`) + body with a code fence.
private let writerFixture = """
---
date: 2026-06-13
title: Add a risk-scoped PR re-resolution step
status: Proposed (awaiting Adam approval)
target: SKILL.md
parent: [[dreaming-proposals]]
---

# 2026-06-13 — Add a risk-scoped PR re-resolution step

**Problem.** SKILL.md anchored on one PR.

```bash
gh pr list --repo <repo> --search "<keyword>"
```
"""

@Suite("ProposalsWriter.rewriteFrontmatterStatus (pure)")
struct ProposalsWriterRewriteTests {

    @Test func replacesOnlyTheFrontmatterStatusValue() throws {
        let out = try ProposalsWriter.rewriteFrontmatterStatus(
            text: writerFixture,
            newStatusValue: "Approved (2026-06-14, via Scout app)",
            file: "p.md"
        )
        #expect(out.contains("status: Approved (2026-06-14, via Scout app)"))
        #expect(!out.contains("status: Proposed (awaiting Adam approval)"))
        // Other frontmatter fields untouched.
        #expect(out.contains("title: Add a risk-scoped PR re-resolution step"))
        #expect(out.contains("target: SKILL.md"))
    }

    @Test func leavesBodyAndCodeFenceByteIdentical() throws {
        let out = try ProposalsWriter.rewriteFrontmatterStatus(
            text: writerFixture,
            newStatusValue: "Rejected (2026-06-14, via Scout app)",
            file: "p.md"
        )
        #expect(out.contains(#"gh pr list --repo <repo> --search "<keyword>""#))
        #expect(out.contains("**Problem.** SKILL.md anchored on one PR."))
        #expect(out.contains("# 2026-06-13 — Add a risk-scoped PR re-resolution step"))
    }

    @Test func reparsingTheRewriteReflectsTheNewStatus() throws {
        let out = try ProposalsWriter.rewriteFrontmatterStatus(
            text: writerFixture,
            newStatusValue: "Approved (2026-06-14, via Scout app)",
            file: "p.md"
        )
        let p = try #require(ProposalsParser.parseFile(
            contents: out, fileURL: URL(fileURLWithPath: "/x/2026-06-13-pr.md")))
        #expect(p.status == .approved)
    }

    @Test func noFrontmatterThrows() {
        #expect(throws: ProposalsWriterError.self) {
            try ProposalsWriter.rewriteFrontmatterStatus(
                text: "# Just a heading\n\nbody",
                newStatusValue: "Approved",
                file: "p.md"
            )
        }
    }

    @Test func frontmatterWithoutStatusFieldThrows() {
        let text = "---\ndate: 2026-06-13\ntitle: t\n---\n\nbody"
        #expect(throws: ProposalsWriterError.self) {
            try ProposalsWriter.rewriteFrontmatterStatus(
                text: text, newStatusValue: "Approved", file: "p.md")
        }
    }

    @Test func preservesIndentationOnStatusLine() throws {
        let text = "---\n  status: Proposed\n---\nbody"
        let out = try ProposalsWriter.rewriteFrontmatterStatus(
            text: text, newStatusValue: "Approved (x)", file: "p.md")
        #expect(out.contains("  status: Approved (x)"))
    }
}

@Suite("ProposalsWriter end-to-end (file + git commit)")
struct ProposalsWriterE2ETests {

    /// A fixed date so the written status stamp is deterministic: 2026-06-14.
    private static func fixedDate() -> Date {
        var c = DateComponents()
        c.year = 2026; c.month = 6; c.day = 14; c.hour = 12
        c.timeZone = TimeZone(identifier: "UTC")
        return Calendar(identifier: .gregorian).date(from: c)!
    }

    private func makeProposalDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("proposals-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("dreaming-proposals"),
            withIntermediateDirectories: true)
        return dir
    }

    @Test func approveWritesFrontmatterStatusAndCommitsScopedToFile() async throws {
        let repo = try makeProposalDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("dreaming-proposals/2026-06-13-pr-recheck.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        // rev-parse(0) → add(0) → diff(1=dirty) → commit(0)
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ProposalsWriter(
            scoutDirectory: repo,
            gitService: git,
            now: { Self.fixedDate() }
        )

        try await writer.decide(.approve, fileURL: fileURL, label: "PR re-resolution")

        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: Approved (2026-06-14, via Scout app)"))

        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: approve proposal PR re-resolution"))
        // Commit is scoped to the per-file proposal path under the repo.
        #expect(commit.arguments.contains("dreaming-proposals/2026-06-13-pr-recheck.md"))
    }

    @Test func declineWritesRejectedStatus() async throws {
        let repo = try makeProposalDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("dreaming-proposals/2026-06-13-pr-recheck.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ProposalsWriter(scoutDirectory: repo, gitService: git, now: { Self.fixedDate() })

        try await writer.decide(.decline, fileURL: fileURL, label: "PR re-resolution")

        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: Rejected (2026-06-14, via Scout app)"))
        let p = ProposalsParser.parseFile(contents: written, fileURL: fileURL)
        #expect(p?.status == .rejected)
    }

    @Test func fileWithoutFrontmatterThrowsAndDoesNotCommit() async throws {
        let repo = try makeProposalDir()
        defer { try? FileManager.default.removeItem(at: repo) }
        let fileURL = repo.appendingPathComponent("dreaming-proposals/index.md")
        let original = "# Index, no frontmatter\n"
        try original.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [])
        let git = GitService(repoURL: repo, runner: runner)
        let writer = ProposalsWriter(scoutDirectory: repo, gitService: git)

        await #expect(throws: ProposalsWriterError.self) {
            try await writer.decide(.approve, fileURL: fileURL, label: "index")
        }
        #expect(runner.calls.isEmpty)
        #expect(try String(contentsOf: fileURL, encoding: .utf8) == original)
    }
}

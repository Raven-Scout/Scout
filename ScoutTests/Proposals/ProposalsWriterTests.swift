import Testing
import Foundation
@testable import Scout

private let writerFixture = """
## Proposals

### P-2026-06-13-01 — Add a risk-scoped PR re-resolution step

**Status:** Proposed (awaiting Adam approval)

**Problem.** SKILL.md anchored on one PR.

```bash
gh pr list --repo <repo> --search "<keyword>"
```

### P-2026-06-10-02 — Tighten the budget gate

**Status:** Pending (auto-apply after 2026-06-13)

**Trigger:** repeated overruns.
"""

private let heading1 = "### P-2026-06-13-01 — Add a risk-scoped PR re-resolution step"
private let heading2 = "### P-2026-06-10-02 — Tighten the budget gate"

@Suite("ProposalsWriter.rewrite (pure)")
struct ProposalsWriterRewriteTests {

    @Test func replacesOnlyTheTargetStatusLine() throws {
        let out = try ProposalsWriter.rewrite(
            text: writerFixture,
            headingLine: heading1,
            newStatusValue: "Approved (2026-06-14, via Scout app)"
        )
        // Target flipped.
        #expect(out.contains("**Status:** Approved (2026-06-14, via Scout app)"))
        // The other proposal's status is untouched.
        #expect(out.contains("**Status:** Pending (auto-apply after 2026-06-13)"))
        // The proposed status is gone (exactly one status changed).
        #expect(!out.contains("**Status:** Proposed (awaiting Adam approval)"))
    }

    @Test func leavesBodyAndCodeFenceByteIdentical() throws {
        let out = try ProposalsWriter.rewrite(
            text: writerFixture,
            headingLine: heading1,
            newStatusValue: "Rejected (2026-06-14, via Scout app)"
        )
        #expect(out.contains(#"gh pr list --repo <repo> --search "<keyword>""#))
        #expect(out.contains("**Problem.** SKILL.md anchored on one PR."))
        #expect(out.contains("**Trigger:** repeated overruns."))
    }

    @Test func reparsingTheRewriteReflectsTheNewStatus() throws {
        let out = try ProposalsWriter.rewrite(
            text: writerFixture,
            headingLine: heading2,
            newStatusValue: "Approved (2026-06-14, via Scout app)"
        )
        let proposals = ProposalsParser.parse(text: out)
        let target = try #require(proposals.first { $0.headingLine == heading2 })
        #expect(target.status == .approved)
        // The first proposal is still awaiting.
        let other = try #require(proposals.first { $0.headingLine == heading1 })
        #expect(other.status == .proposed)
    }

    @Test func unknownHeadingThrows() {
        #expect(throws: ProposalsWriterError.self) {
            try ProposalsWriter.rewrite(
                text: writerFixture,
                headingLine: "### P-9999-99-99-99 — Does not exist",
                newStatusValue: "Approved"
            )
        }
    }

    @Test func sectionWithoutStatusLineThrows() {
        let text = """
        ### P-1 — No status here

        Just a body, no status marker.
        """
        #expect(throws: ProposalsWriterError.self) {
            try ProposalsWriter.rewrite(
                text: text,
                headingLine: "### P-1 — No status here",
                newStatusValue: "Approved"
            )
        }
    }

    @Test func preservesIndentationOnStatusLine() throws {
        let text = "### P-1 — Indented status\n\n  **Status:** Proposed\n"
        let out = try ProposalsWriter.rewrite(
            text: text,
            headingLine: "### P-1 — Indented status",
            newStatusValue: "Approved (x)"
        )
        #expect(out.contains("  **Status:** Approved (x)"))
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

    @Test func approveWritesStatusAndCommitsScopedToFile() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("proposals-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let fileURL = dir.appendingPathComponent("dreaming-proposals.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        // rev-parse(0) → add(0) → diff(1=dirty) → commit(0)
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: dir, runner: runner)
        let writer = ProposalsWriter(
            fileURL: fileURL,
            scoutDirectory: dir,
            gitService: git,
            now: { Self.fixedDate() }
        )

        try await writer.decide(.approve, headingLine: heading1, code: "P-2026-06-13-01")

        // File now carries the approved status with the fixed-date stamp.
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("**Status:** Approved (2026-06-14, via Scout app)"))
        #expect(written.contains("**Status:** Pending (auto-apply after 2026-06-13)"))

        // The commit is scoped to the proposals file and carries the verb+code.
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: approve proposal P-2026-06-13-01"))
        #expect(commit.arguments.contains("dreaming-proposals.md"))
    }

    @Test func declineWritesRejectedStatus() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("proposals-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let fileURL = dir.appendingPathComponent("dreaming-proposals.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let git = GitService(repoURL: dir, runner: runner)
        let writer = ProposalsWriter(
            fileURL: fileURL,
            scoutDirectory: dir,
            gitService: git,
            now: { Self.fixedDate() }
        )

        try await writer.decide(.decline, headingLine: heading2, code: "P-2026-06-10-02")

        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("**Status:** Rejected (2026-06-14, via Scout app)"))
        let proposals = ProposalsParser.parse(text: written)
        #expect(proposals.first { $0.headingLine == heading2 }?.status == .rejected)
    }

    @Test func unknownHeadingThrowsAndDoesNotCommit() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("proposals-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let fileURL = dir.appendingPathComponent("dreaming-proposals.md")
        try writerFixture.write(to: fileURL, atomically: true, encoding: .utf8)

        let runner = ScriptedRunner(scripted: [])
        let git = GitService(repoURL: dir, runner: runner)
        let writer = ProposalsWriter(fileURL: fileURL, scoutDirectory: dir, gitService: git)

        await #expect(throws: ProposalsWriterError.self) {
            try await writer.decide(.approve, headingLine: "### Nope — missing", code: "X")
        }
        // No git invoked, and the file is unchanged.
        #expect(runner.calls.isEmpty)
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written == writerFixture)
    }
}

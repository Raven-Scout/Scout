import Testing
import Foundation
@testable import Scout

// A realistic per-file proposal: YAML frontmatter + an H1 that repeats the
// title + body sections, including a fenced ```bash block.
private let proposalFixture = """
---
date: 2026-06-13
title: Add a risk-scoped PR re-resolution step
status: Proposed (awaiting Adam approval)
target: SKILL.md
parent: [[dreaming-proposals]]
---

# 2026-06-13 — Add a risk-scoped PR re-resolution step

**Trigger:** dreaming 2026-06-13 feedback on [[#BB290]].

**Problem.** SKILL.md anchored on one known PR number.

**Proposed change:**

```bash
gh pr list --repo <repo> --state all --search "<keyword>"
```

Treat any OPEN PR whose title carries the risk id as the live fix.
"""

private func url(_ name: String) -> URL {
    URL(fileURLWithPath: "/x/dreaming-proposals/\(name)")
}

@Suite("ProposalsParser")
struct ProposalsParserTests {

    @Test func parsesFrontmatterTitleDateAndStatus() throws {
        let p = try #require(ProposalsParser.parseFile(
            contents: proposalFixture, fileURL: url("2026-06-13-pr-recheck.md")))
        #expect(p.title == "Add a risk-scoped PR re-resolution step")
        #expect(p.date == "2026-06-13")
        #expect(p.status == .proposed)
        #expect(p.code == "2026-06-13")  // header chip shows the date
    }

    @Test func classifiesEachStatusVocabulary() throws {
        func status(_ raw: String) -> ProposalStatus {
            let text = "---\nstatus: \(raw)\ntitle: t\n---\nbody"
            return ProposalsParser.parseFile(contents: text, fileURL: url("2026-01-01-x.md"))!.status
        }
        #expect(status("Proposed (awaiting Adam approval)") == .proposed)
        #expect(status("Pending (auto-apply after 2026-06-18)") == .pending(autoApplyDate: "2026-06-18"))
        #expect(status("Approved — 2026-06-02") == .approved)
        #expect(status("Rejected") == .rejected)
        #expect(status("Applied — 2026-06-02") == .applied(date: "2026-06-02"))
    }

    @Test func awaitingDecisionMatchesBadgeSemantics() throws {
        let pending = ProposalsParser.parseFile(
            contents: "---\nstatus: Pending (auto-apply after 2026-06-18)\ntitle: t\n---\nb",
            fileURL: url("2026-06-18-a.md"))!
        let applied = ProposalsParser.parseFile(
            contents: "---\nstatus: Applied — 2026-06-02\ntitle: t\n---\nb",
            fileURL: url("2026-06-02-b.md"))!
        #expect(pending.isAwaitingDecision)
        #expect(!applied.isAwaitingDecision)
    }

    @Test func stripsLeadingH1ButKeepsBodyAndCodeFence() throws {
        let p = try #require(ProposalsParser.parseFile(
            contents: proposalFixture, fileURL: url("2026-06-13-pr-recheck.md")))
        let body = p.bodyMarkdown
        // The duplicate "# 2026-06-13 — …" H1 is removed (title is in the header).
        #expect(!body.contains("# 2026-06-13 —"))
        #expect(body.contains("**Problem.**"))
        #expect(body.contains("**Trigger:**"))
        #expect(body.contains("gh pr list --repo"))

        let codeBlocks = p.bodyBlocks.compactMap { block -> String? in
            if case .code(_, let code) = block { return code } else { return nil }
        }
        #expect(codeBlocks.count == 1)
        #expect(codeBlocks.first == #"gh pr list --repo <repo> --state all --search "<keyword>""#)
    }

    @Test func fallsBackToFilenameDateAndStemWhenFrontmatterSparse() throws {
        // Frontmatter present but missing date + title → derive from filename.
        let text = "---\nstatus: Pending\n---\n\nsome body"
        let p = try #require(ProposalsParser.parseFile(
            contents: text, fileURL: url("2026-05-25-recurring-task-primitive.md")))
        #expect(p.date == "2026-05-25")
        #expect(p.title == "2026-05-25-recurring-task-primitive")
    }

    @Test func returnsNilForFilesWithoutFrontmatter() {
        // The legacy index file / any non-frontmatter markdown is skipped.
        #expect(ProposalsParser.parseFile(
            contents: "# Dreaming Proposals — index\n\n## Pending\n\n| a | b |",
            fileURL: url("dreaming-proposals.md")) == nil)
        #expect(ProposalsParser.parseFile(contents: "", fileURL: url("empty.md")) == nil)
        // Opening fence but no closing fence → not valid frontmatter.
        #expect(ProposalsParser.parseFile(
            contents: "---\nstatus: Pending\nbody with no closing fence",
            fileURL: url("broken.md")) == nil)
    }
}

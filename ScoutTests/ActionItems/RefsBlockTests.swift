import Testing
import Foundation
@testable import Scout

@Suite("Refs sub-bullet recognition")
struct RefsBlockTests {
    /// Parse a minimal one-section document and return its single task.
    /// `taskLines` are passed verbatim (explicit leading spaces preserved) so
    /// the indented `- Refs:` sub-bullet is recognized as a sub-line.
    private func parseTask(_ taskLines: [String]) throws -> ActionTask {
        let text = (["# Action Items — 2026-07-20", "", "## 🔴 Urgent", ""] + taskLines)
            .joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        return try #require(urgent.tasks.first)
    }

    @Test func refsTokensBecomeDeepLinksOnePerToken() throws {
        let task = try parseTask([
            "- [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep.",
            "  - Refs: [[people/alex]] · [[PROJ-3026]] · example-org/repo#7056 · #XREF",
        ])
        // Entity, Linear (inside a wikilink), GitHub shorthand, cross-ref.
        #expect(task.deepLinks.contains(.entity(path: "people/alex", label: nil)))
        #expect(task.deepLinks.contains(.linear(id: "PROJ-3026")))
        #expect(task.deepLinks.contains {
            if case .githubPR(let repo, let n, _) = $0 { return repo == "example-org/repo" && n == 7056 }
            return false
        })
        #expect(task.deepLinks.contains(.crossRef(tag: "XREF")))
        // No stray links from the (id-free) main line — four tokens, four refs.
        #expect(task.deepLinks.count == 4)
    }

    @Test func refsLineExcludedFromBodyAndComments() throws {
        let task = try parseTask([
            "- [ ] [#REPLYX] **Reply to Alex** — She said 1/8–1/2; still open per the sweep.",
            "  - Refs: [[people/alex]] · #XREF",
        ])
        #expect(!task.body.contains("Refs"))
        #expect(!task.body.contains("XREF"))
        // The Refs line must NOT be captured as a comment from author "Refs".
        #expect(task.comments.isEmpty)
        #expect(task.body.contains("still open per the sweep"))
    }

    @Test func malformedTokenSurfacesAsPlainRef() throws {
        let task = try parseTask([
            "- [ ] [#PLN] **Do the thing** — a body.",
            "  - Refs: [[people/sam]] · ??garbage",
        ])
        #expect(task.deepLinks.contains(.entity(path: "people/sam", label: nil)))
        #expect(task.deepLinks.contains(.plainRef(text: "??garbage")))
    }

    @Test func labeledEntityWikilinkKeepsAlias() throws {
        let task = try parseTask([
            "- [ ] [#LBL] **Loop in Priya** — a body.",
            "  - Refs: [[people/priya|Priya]]",
        ])
        #expect(task.deepLinks.contains(.entity(path: "people/priya", label: "Priya")))
    }

    @Test func slackAndFullGithubURLStillParse() throws {
        let task = try parseTask([
            "- [ ] [#SG] **Follow the thread** — a body.",
            "  - Refs: https://acme-co.slack.com/archives/C0123456789/p1700000000000000 · https://github.com/example-org/repo/pull/42",
        ])
        #expect(task.deepLinks.contains { if case .slackThread = $0 { return true } else { return false } })
        #expect(task.deepLinks.contains {
            if case .githubPR(let repo, let n, _) = $0 { return repo == "example-org/repo" && n == 42 }
            return false
        })
    }

    @Test func taskWithoutRefsLineParsesAsBefore() throws {
        let task = try parseTask([
            "- [ ] [#NORF] **Plain task** — just a body, no refs.",
        ])
        #expect(task.deepLinks.isEmpty)
        #expect(task.comments.isEmpty)
        #expect(task.body.contains("just a body"))
        #expect(task.shortPrefix == "NORF")
    }

    /// Ordinary `- Source:` / `- Context:` sub-bullets are NOT Refs and must
    /// keep their existing behavior (recognized as comments today).
    @Test func sourceContextSubBulletsUntouchedByRefsRecognizer() throws {
        let task = try parseTask([
            "- [ ] [#SRC] **Item with a source** — a body.",
            "  - Source: Slack",
        ])
        #expect(task.deepLinks.isEmpty)   // Source: is not a Refs line
    }
}

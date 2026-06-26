import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsParser end-to-end against real fixtures")
struct ActionItemsParserTests {
    static let bundle = Bundle(for: ActionItemsFixtureAnchor.self)

    private func loadFixture(_ name: String) throws -> (url: URL, bytes: Int, text: String) {
        guard let url = Self.bundle.url(forResource: name, withExtension: "md")
                ?? Self.bundle.resourceURL?.appendingPathComponent("\(name).md") else {
            Issue.record("Fixture \(name).md not found in bundle")
            throw CocoaError(.fileReadNoSuchFile)
        }
        let data = try Data(contentsOf: url)
        return (url, data.count, String(data: data, encoding: .utf8)!)
    }

    @Test func parsesApr20Document() throws {
        let f = try loadFixture("action-items-2026-04-20")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)

        #expect(doc.title.contains("Monday, April 20"))
        #expect(doc.preamble.count >= 1)
        #expect(!doc.sections.isEmpty)

        // At least one section of each kind we expect in this file.
        let kinds = Set(doc.sections.map(\.kind))
        #expect(kinds.contains(.focus))
        #expect(kinds.contains(.urgent))
        #expect(kinds.contains(.todo))
        #expect(kinds.contains(.watching))
        #expect(kinds.contains(.personal))
        #expect(kinds.contains(.meetings))
        #expect(kinds.contains(.done))
        #expect(kinds.contains(.digest))
    }

    @Test func urgentSectionHasTasksWithDeepLinks() throws {
        let f = try loadFixture("action-items-2026-04-20")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        #expect(!urgent.tasks.isEmpty)
        // PROJ-2879 appears in the first urgent task body of the fixture.
        let firstTask = urgent.tasks.first!
        #expect(firstTask.deepLinks.contains(where: {
            if case .linear(let id) = $0 { return id == "PROJ-2879" }; return false
        }))
    }

    @Test func commentsAttachToTheirTask() throws {
        // Apr 18 has a comment pattern captured in the add_comment end-to-end run.
        let f = try loadFixture("action-items-2026-04-18")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)
        let taskWithComment = doc.sections.flatMap(\.tasks).first { !$0.comments.isEmpty }
        #expect(taskWithComment != nil, "fixture should contain at least one task with a comment")
    }

    @Test func plainSubjectDropsMarkdown() throws {
        let f = try loadFixture("action-items-2026-04-20")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)
        for t in doc.sections.flatMap(\.tasks) {
            #expect(!t.plainSubject.contains("**"), "plainSubject still contains bold markers")
            #expect(!t.plainSubject.contains("[["), "plainSubject still contains wikilink open")
        }
    }

    @Test func meetingsTableParses() throws {
        let f = try loadFixture("action-items-2026-04-20")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)
        let meetings = try #require(doc.sections.first { $0.kind == .meetings })
        #expect(!meetings.tables.isEmpty)
        #expect(meetings.tables.first!.headers.count >= 3)
    }

    @Test func doneSectionContainsOnlyDoneTasks() throws {
        let f = try loadFixture("action-items-2026-04-20")
        let doc = try ActionItemsParser.parse(text: f.text, sourceURL: f.url, sourceBytes: f.bytes)
        let done = doc.sections.first { $0.kind == .done }
        // If present, every task in it should be done=true.
        if let d = done {
            #expect(d.tasks.allSatisfy { $0.done })
        }
    }

    @Test func indentLevelTranslatesTabsAndSpaces() {
        #expect(ActionItemsParser.indentLevelFor("") == 0)
        #expect(ActionItemsParser.indentLevelFor("\t") == 1)
        #expect(ActionItemsParser.indentLevelFor("\t\t") == 2)
        #expect(ActionItemsParser.indentLevelFor("  ") == 1)
        #expect(ActionItemsParser.indentLevelFor("    ") == 2)
        #expect(ActionItemsParser.indentLevelFor("\t  ") == 2)
    }

    @Test func nestedTasksParsedWithIndentLevel() throws {
        // Synthetic doc that mirrors the Prague-trip nesting pattern in the
        // real action-items files (1 tab for child, 2 tabs for grand-child).
        let synthetic = """
        # Action Items — Synthetic
        Preamble line.

        ## 🔴 Urgent

        - [ ] **Top-level parent**
        \t- [ ] **Child A**
        \t- [ ] **Child B with sub-items:**
        \t\t- [ ] Grand-child 1
        \t\t- [ ] Grand-child 2
        - [ ] **Sibling top-level**
        """
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-01-01.md")
        let doc = try ActionItemsParser.parse(text: synthetic, sourceURL: url, sourceBytes: synthetic.utf8.count)
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        let levels = urgent.tasks.map(\.indentLevel)
        #expect(levels == [0, 1, 1, 2, 2, 0],
                "Got \(levels) — expected nested levels for parent / 2 children / 2 grand-children / sibling")
    }

    @Test func extractsVariableLengthSemanticTag() throws {
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-06-06.md")
        let text = "# T\n\n## 🔴 Urgent\n\n- [ ] [#AI3026] **Validate tracing** — overnight\n"
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        let t = try #require(doc.sections.flatMap { $0.tasks }.first)
        #expect(t.shortPrefix == "AI3026")
        #expect(t.subject == "**Validate tracing**")
    }

    @Test func doesNotExtractNumericGitHubRefAsPrefix() throws {
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-06-06.md")
        let text = "# T\n\n## 🔴 Urgent\n\n- [ ] [#555] **fix the bug**\n"
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        let t = try #require(doc.sections.flatMap { $0.tasks }.first)
        #expect(t.shortPrefix == nil)
    }

    // MARK: - Stable identity across reparses (#62)

    /// Markdown the stability tests reparse. Two urgent tasks + one to-do,
    /// mirroring the shape a daily file has while the user is working through
    /// it.
    private static let stableDoc = """
    # Monday, April 20

    ## 🔴 Urgent

    - [ ] **First task** — needs attention
    - [ ] **Second task** — also important

    ## 🟡 To Do

    - [ ] **Third task** — when you get to it
    """
    private static let stableURL = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")

    /// A clean reparse of identical text must reproduce identical section and
    /// task IDs — this is the property that lets SwiftUI diff in place rather
    /// than rebuild the list and reset the scroll position.
    @Test func idsAreStableAcrossIdenticalReparses() throws {
        let bytes = Self.stableDoc.utf8.count
        let a = try ActionItemsParser.parse(text: Self.stableDoc, sourceURL: Self.stableURL, sourceBytes: bytes)
        let b = try ActionItemsParser.parse(text: Self.stableDoc, sourceURL: Self.stableURL, sourceBytes: bytes)
        #expect(a.sections.map(\.id) == b.sections.map(\.id))
        #expect(a.sections.flatMap(\.tasks).map(\.id) == b.sections.flatMap(\.tasks).map(\.id))
    }

    /// Adding a comment under a task (the scoutctl `add-comment` sub-bullet
    /// shape) shifts every following line number, but must not change any
    /// row's identity: the commented task keeps its ID and gains the comment,
    /// and all other sections/tasks keep their IDs too.
    @Test func insertingACommentKeepsRowIDsStable() throws {
        let before = try ActionItemsParser.parse(
            text: Self.stableDoc, sourceURL: Self.stableURL, sourceBytes: Self.stableDoc.utf8.count)

        let withComment = Self.stableDoc.replacingOccurrences(
            of: "- [ ] **First task** — needs attention",
            with: "- [ ] **First task** — needs attention\n  - jordan: looking into it"
        )
        let after = try ActionItemsParser.parse(
            text: withComment, sourceURL: Self.stableURL, sourceBytes: withComment.utf8.count)

        // The commented task: same identity, now carrying the comment.
        let firstBefore = before.sections[0].tasks[0]
        let firstAfter = after.sections[0].tasks[0]
        #expect(firstBefore.id == firstAfter.id)
        #expect(firstBefore.comments.isEmpty)
        #expect(firstAfter.comments.count == 1)

        // Every other row keeps its identity despite shifted line numbers.
        #expect(before.sections.map(\.id) == after.sections.map(\.id))
        #expect(before.sections.flatMap(\.tasks).map(\.id) == after.sections.flatMap(\.tasks).map(\.id))
    }
}

/// Type anchor so Bundle(for:) finds the ScoutTests test bundle.
final class ActionItemsFixtureAnchor {}

// ScoutTests/PerFile/PerFileItemParserTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemParser")
struct PerFileItemParserTests {
    private func url(_ name: String) -> URL { URL(fileURLWithPath: "/tmp/\(name)") }

    @Test func parsesFullFrontmatterWishlist() throws {
        let text = """
        ---
        title: "Upgrade the graph system"
        status: in-progress
        priority: high
        date: 2026-06-12
        source: "Alex Slack DM"
        ---

        # Upgrade the graph system

        Evaluate the graph query engine.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-12-graph.md")))
        #expect(item.title == "Upgrade the graph system")
        #expect(item.status == .inProgress)
        #expect(item.priority == .high)
        #expect(item.date == "2026-06-12")
        #expect(item.source == "Alex Slack DM")
        #expect(item.area == nil)
        #expect(item.bodyMarkdown == "Evaluate the graph query engine.")   // H1 stripped
    }

    @Test func parsesResearchAreaAndUrgent() throws {
        let text = """
        ---
        title: Graph upgrade
        status: open
        priority: urgent
        date: 2026-06-10
        area: knowledge-graph
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.priority == .urgent)
        #expect(item.area == "knowledge-graph")
        #expect(item.source == nil)
    }

    @Test func defaultsAndFilenameDateFallback() throws {
        let text = "---\ntitle: No date here\n---\n\nbody"
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-16-no-date-here.md")))
        #expect(item.status == .open)        // missing -> open
        #expect(item.priority == .medium)    // missing -> medium
        #expect(item.date == "2026-06-16")   // from filename prefix
    }

    /// A retired research lane annotated its keyword fields with YAML inline
    /// comments. Read verbatim, `status` became an unknown value — which counts
    /// as active — so a done 200 KB item rendered on the Research tab and its
    /// comment-length status pill pushed the card off-screen.
    @Test func keywordFieldsIgnoreInlineYAMLComments() throws {
        let text = """
        ---
        title: "standing: the demo lane"
        status: done   # ⏹️ RETIRED 2026-10-09 `dreaming` — `expires:` reached; see §Expiry ("regardless of state").
        priority: medium   # 🔻 DEMOTED from `urgent` 2026-10-03 on Alex's direction
        date: 2026-08-25 # first logged
        area: demo
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.status == .done)
        #expect(item.isActive == false)
        #expect(item.priority == .medium)
        #expect(item.date == "2026-08-25")
    }

    @Test func keywordFieldIsBlankWhenWhollyAComment() throws {
        let text = "---\ntitle: T\nstatus: # TODO decide\npriority: #high\n---\n\nbody"
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.status == .open)        // blank -> open, as when missing
        #expect(item.priority == .medium)
    }

    /// Free-text fields keep their `#`: `Pattern #197` and `C#` are content,
    /// and the app has always shown these values verbatim.
    @Test func freeTextFieldsKeepHashes() throws {
        let text = """
        ---
        title: C# notes for issue #42
        status: open
        source: Pattern #197, logged by the evening run
        area: build #2
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.title == "C# notes for issue #42")
        #expect(item.source == "Pattern #197, logged by the evening run")
        #expect(item.area == "build #2")
    }

    @Test func returnsNilWhenNoFrontmatter() {
        #expect(PerFileItemParser.parseFile(contents: "# Just a heading\n\ntext", fileURL: url("a.md")) == nil)
    }
}

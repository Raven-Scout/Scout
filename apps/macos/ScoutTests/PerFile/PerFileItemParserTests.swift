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

        Evaluate TinkerPop + Gremlin.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-12-graph.md")))
        #expect(item.title == "Upgrade the graph system")
        #expect(item.status == .inProgress)
        #expect(item.priority == .high)
        #expect(item.date == "2026-06-12")
        #expect(item.source == "Alex Slack DM")
        #expect(item.area == nil)
        #expect(item.bodyMarkdown == "Evaluate TinkerPop + Gremlin.")   // H1 stripped
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

    @Test func returnsNilWhenNoFrontmatter() {
        #expect(PerFileItemParser.parseFile(contents: "# Just a heading\n\ntext", fileURL: url("a.md")) == nil)
    }
}

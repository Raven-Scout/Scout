// ScoutTests/KnowledgeBase/KnowledgeBaseTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("KnowledgeBaseFileWriter pure helpers")
struct KnowledgeBaseFileWriterPureTests {
    @Test func normalizedAddsMarkdownExtension() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("my-note") == "my-note.md")
    }
    @Test func normalizedKeepsExistingExtension() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("schema.yaml") == "schema.yaml")
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("notes.md") == "notes.md")
    }
    @Test func normalizedTrimsWhitespace() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("  spaced  ") == "spaced.md")
    }
    @Test func normalizedRejectsEmpty() {
        #expect(throws: KBWriterError.emptyName) {
            _ = try KnowledgeBaseFileWriter.normalizedFileName("   ")
        }
    }
    @Test func normalizedRejectsPathSeparators() {
        #expect(throws: (any Error).self) {
            _ = try KnowledgeBaseFileWriter.normalizedFileName("a/b")
        }
        #expect(throws: (any Error).self) {
            _ = try KnowledgeBaseFileWriter.normalizedFileName("..")
        }
    }
    @Test func relativePathStripsRepoPrefix() {
        let repo = URL(fileURLWithPath: "/Users/x/Scout")
        let file = URL(fileURLWithPath: "/Users/x/Scout/knowledge-base/people.md")
        #expect(KnowledgeBaseFileWriter.relativePathInRepo(fileURL: file, repo: repo)
                == "knowledge-base/people.md")
    }
}

@Suite("KnowledgeBaseService tree builder")
struct KnowledgeBaseServiceTreeTests {
    /// Build a throwaway KB tree on disk, returning its scout root.
    private func makeTree() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbtest-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        let projects = kb.appendingPathComponent("projects")
        try FileManager.default.createDirectory(at: projects, withIntermediateDirectories: true)
        try "# People".write(to: kb.appendingPathComponent("people.md"), atomically: true, encoding: .utf8)
        try "a: 1".write(to: kb.appendingPathComponent("schema.yaml"), atomically: true, encoding: .utf8)
        try "ignore".write(to: kb.appendingPathComponent("notes.txt"), atomically: true, encoding: .utf8)
        try "# Scout".write(to: projects.appendingPathComponent("scout.md"), atomically: true, encoding: .utf8)
        // An empty directory should be pruned.
        try FileManager.default.createDirectory(
            at: kb.appendingPathComponent("empty"), withIntermediateDirectories: true)
        return root
    }

    @Test func buildsSortedTreeDirsBeforeFiles() throws {
        let root = try makeTree()
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let nodes = KnowledgeBaseService.buildChildren(of: kb, scoutDirectory: root)

        // Directory ("projects") sorts before files; "empty" pruned; .txt excluded.
        #expect(nodes.map(\.name) == ["projects", "people.md", "schema.yaml"])
        #expect(nodes[0].isDirectory)
        #expect(nodes[0].children.map(\.name) == ["scout.md"])
        #expect(!nodes.contains { $0.name == "notes.txt" })
        #expect(!nodes.contains { $0.name == "empty" })
    }

    @Test func nodeRelativePathsAreRepoRelative() throws {
        let root = try makeTree()
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let nodes = KnowledgeBaseService.buildChildren(of: kb, scoutDirectory: root)
        let scout = nodes.first { $0.name == "projects" }!.children.first!
        #expect(scout.relativePath == "knowledge-base/projects/scout.md")
        #expect(scout.ext == "md")
        #expect(scout.displayName == "scout")
        #expect(scout.isEditable)
    }

    @Test func allFilesFlattensSubtree() throws {
        let root = try makeTree()
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let nodes = KnowledgeBaseService.buildChildren(of: kb, scoutDirectory: root)
        let files = nodes.flatMap(\.allFiles).map(\.name).sorted()
        #expect(files == ["people.md", "schema.yaml", "scout.md"])
    }
}

@Suite("KBMarkdownPreview parser")
struct KBMarkdownPreviewParserTests {
    @Test func splitsFrontmatter() {
        let text = "---\ntitle: X\ntags: a\n---\n\n# Heading\nbody"
        let (fm, body) = KBMarkdownPreview.splitFrontmatter(text)
        #expect(fm == ["title: X", "tags: a"])
        #expect(body == "# Heading\nbody")
    }
    @Test func noFrontmatterWhenAbsent() {
        let (fm, body) = KBMarkdownPreview.splitFrontmatter("# Heading\nbody")
        #expect(fm == nil)
        #expect(body == "# Heading\nbody")
    }
    @Test func unterminatedFenceTreatedAsBody() {
        let (fm, _) = KBMarkdownPreview.splitFrontmatter("---\ntitle: X\nno closing")
        #expect(fm == nil)
    }
    @Test func parsesHeadingLevels() {
        #expect(KBMarkdownPreview.parseHeading("## Sub") == .heading(level: 2, text: "Sub"))
        #expect(KBMarkdownPreview.parseHeading("###### Deep") == .heading(level: 6, text: "Deep"))
        #expect(KBMarkdownPreview.parseHeading("#NoSpace") == nil)        // needs a space
        #expect(KBMarkdownPreview.parseHeading("####### TooDeep") == nil) // 7 hashes invalid
    }
    @Test func parsesUnorderedAndOrderedLists() {
        #expect(KBMarkdownPreview.parseListItem("- item") == .listItem(depth: 0, ordinal: nil, text: "item"))
        #expect(KBMarkdownPreview.parseListItem("  - nested") == .listItem(depth: 1, ordinal: nil, text: "nested"))
        #expect(KBMarkdownPreview.parseListItem("1. first") == .listItem(depth: 0, ordinal: "1.", text: "first"))
        #expect(KBMarkdownPreview.parseListItem("plain") == nil)
    }
    @Test func parseProducesMixedBlocks() {
        let blocks = KBMarkdownPreview.parse("# Title\n\npara one\n\n- a\n- b\n\n```\ncode\n```\n\n> quote")
        #expect(blocks.contains(.heading(level: 1, text: "Title")))
        #expect(blocks.contains(.prose("para one")))
        #expect(blocks.contains(.listItem(depth: 0, ordinal: nil, text: "a")))
        #expect(blocks.contains(.code("code")))
        #expect(blocks.contains(.quote("quote")))
    }
    @Test func horizontalRuleBecomesRuleBlock() {
        let blocks = KBMarkdownPreview.parse("above\n\n---\n\nbelow")
        #expect(blocks.contains(.rule))
        #expect(blocks.contains(.prose("above")))
        #expect(blocks.contains(.prose("below")))
    }
}

@Suite("KBMarkdownPreview tables")
struct KBMarkdownPreviewTableTests {
    @Test func detectsSeparatorButNotHorizontalRule() {
        #expect(KBMarkdownPreview.isTableSeparator("|---|---|"))
        #expect(KBMarkdownPreview.isTableSeparator("| :--- | ---: |"))
        #expect(!KBMarkdownPreview.isTableSeparator("---"))      // hr, no pipe
        #expect(!KBMarkdownPreview.isTableSeparator("| a | b |")) // content row
    }
    @Test func splitsRowDroppingOuterPipes() {
        #expect(KBMarkdownPreview.splitRow("| Name | Role | Email |") == ["Name", "Role", "Email"])
    }
    @Test func splitsRowHonoringEscapedPipeInWikilink() {
        // `[[people\|Jordan]]` — the escaped pipe is cell content, not a column break.
        let cells = KBMarkdownPreview.splitRow("| Jan | sees [[people\\|Jordan]] | x |")
        #expect(cells == ["Jan", "sees [[people|Jordan]]", "x"])
    }
    @Test func parsesTableBlockWithPaddedRows() {
        let md = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |"
        let blocks = KBMarkdownPreview.parse(md)
        #expect(blocks == [
            .table(headers: ["A", "B", "C"], rows: [["1", "2", "3"], ["4", "5", ""]])
        ])
    }
    @Test func tableEndsAtBlankLine() {
        let md = "| A | B |\n|---|---|\n| 1 | 2 |\n\nafter"
        let blocks = KBMarkdownPreview.parse(md)
        #expect(blocks.contains(.table(headers: ["A", "B"], rows: [["1", "2"]])))
        #expect(blocks.contains(.prose("after")))
    }
}

@Suite("KB wikilink extraction")
struct KBWikilinkExtractionTests {
    @Test func extractsTargetsBeforePipeDeduped() {
        let links = KnowledgeBaseService.extractWikilinks(
            "see [[groupon]] and [[people|Alias]] and [[groupon]] again")
        #expect(links == ["groupon", "people"])
    }
    @Test func ignoresEmptyAndMalformed() {
        #expect(KnowledgeBaseService.extractWikilinks("no links here").isEmpty)
        #expect(KnowledgeBaseService.extractWikilinks("[[ ]]").isEmpty)
    }
}

@MainActor
@Suite("KnowledgeBaseService graph")
struct KBServiceGraphTests {
    /// A small linked KB: people ←→ scout/groupon, with groupon → scout too.
    private func makeLinkedKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbgraph-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        let projects = kb.appendingPathComponent("projects")
        try FileManager.default.createDirectory(at: projects, withIntermediateDirectories: true)
        try "# People\nWorks on [[groupon]] and [[scout]]."
            .write(to: kb.appendingPathComponent("people.md"), atomically: true, encoding: .utf8)
        try "# Scout\nLed by [[people|Someone]]."
            .write(to: projects.appendingPathComponent("scout.md"), atomically: true, encoding: .utf8)
        try "# Groupon\nWith [[people]] and related to [[scout]]."
            .write(to: projects.appendingPathComponent("groupon.md"), atomically: true, encoding: .utf8)
        return root
    }

    @Test func resolvesLinksBacklinksAndLocalGraph() throws {
        let root = try makeLinkedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()

        #expect(svc.resolveWikilink("scout") == "knowledge-base/projects/scout.md")
        #expect(svc.resolveWikilink("nonexistent") == nil)

        let out = svc.outgoingLinks(for: "knowledge-base/people.md")
        #expect(Set(out.map(\.target)) == ["groupon", "scout"])
        #expect(out.allSatisfy { $0.resolved != nil })

        let back = Set(svc.backlinks(for: "knowledge-base/people.md").map(\.path))
        #expect(back == ["knowledge-base/projects/scout.md", "knowledge-base/projects/groupon.md"])

        let g = svc.localGraph(around: "knowledge-base/people.md")
        #expect(g.nodes.count == 3)
        #expect(g.nodes.contains { $0.id == "knowledge-base/people.md" && $0.isCenter })
        #expect(!g.edges.isEmpty)

        let stats = svc.graphStats()
        #expect(stats.notes == 3)
        #expect(stats.links == 3)   // people–scout, people–groupon, scout–groupon
    }

    @Test func contentSearchReturnsSnippet() throws {
        let root = try makeLinkedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        let hits = svc.searchContent("groupon")
        #expect(hits.contains { $0.path == "knowledge-base/projects/groupon.md" })
        #expect(hits.contains { $0.path == "knowledge-base/people.md" })
    }
}

@MainActor
@Suite("KnowledgeBaseService full graph")
struct KBFullGraphTests {
    @Test func fullGraphHasAllNotesAndEdges() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbfull-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try "# A\nlinks [[b]]".write(to: kb.appendingPathComponent("a.md"), atomically: true, encoding: .utf8)
        try "# B\nno links".write(to: kb.appendingPathComponent("b.md"), atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: root) }

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        svc.load()
        let g = svc.fullGraph()
        #expect(g.nodes.count == 2)
        #expect(g.edges.count == 1)
        #expect(g.nodes.allSatisfy { !$0.isCenter })
    }
}

@Suite("KBDocSegment parse + splice")
struct KBDocSegmentTests {
    private let src = """
    ---
    type: person
    ---
    # Title

    First para.

    - item one
    - item two

    | A | B |
    |---|---|
    | 1 | 2 |
    """

    @Test func parsesSegmentsWithLineRanges() {
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.contains { $0.kind == .frontmatter && $0.lineStart == 0 && $0.lineEnd == 2 })
        #expect(segs.contains { $0.kind == .heading(1) && $0.lineStart == 3 })
        #expect(segs.contains { $0.kind == .paragraph && $0.raw == "First para." })
        #expect(segs.filter { $0.kind == .list }.count == 2)
        let table = segs.first { $0.kind == .table }
        #expect(table?.headers == ["A", "B"])
        #expect(table?.rows == [["1", "2"]])
        #expect(table?.rowLines == [12])
    }

    @Test func replaceLinesRewritesOnlyThatBlock() {
        let segs = KBDocSegment.segments(from: src)
        let para = segs.first { $0.kind == .paragraph }!
        let out = KBDocSegment.replaceLines(in: src, start: para.lineStart, end: para.lineEnd, with: "Edited para.")
        #expect(out.contains("Edited para."))
        #expect(!out.contains("First para."))
        #expect(out.contains("# Title"))          // untouched
        #expect(out.contains("| 1 | 2 |"))        // untouched
    }

    @Test func replaceCellRewritesOneCell() {
        let out = KBDocSegment.replaceCell(in: src, sourceLine: 12, col: 1, value: "99")
        #expect(out.contains("| 1 | 99 |"))
        #expect(!out.contains("| 1 | 2 |"))
    }

    @Test func replaceCellEscapesPipes() {
        let out = KBDocSegment.replaceCell(in: src, sourceLine: 12, col: 0, value: "a|b")
        #expect(out.contains(#"| a\|b | 2 |"#))
    }
}

@Suite("KBMarkdownPreview metadata collapse")
struct KBMarkdownPreviewPartitionTests {
    @Test func collapsesChangelogAfterTitle() {
        let blocks = KBMarkdownPreview.parse(
            "# People\n**Last updated:** today\n**Prev:** yesterday\n\nReal intro.\n\n## Team")
        let parts = KBMarkdownPreview.partition(blocks)
        #expect(parts.title == .heading(level: 1, text: "People"))
        // The changelog para collapses into history; the intro stays in the body.
        #expect(parts.history.count == 1)
        #expect(parts.rest.contains(.prose("Real intro.")))
        #expect(parts.rest.contains(.heading(level: 2, text: "Team")))
        #expect(!parts.rest.contains { KBMarkdownPreview.isMetadata($0) })
    }
    @Test func noTitleNoCollapseWhenPlain() {
        let blocks = KBMarkdownPreview.parse("Just a normal note.\n\nSecond paragraph.")
        let parts = KBMarkdownPreview.partition(blocks)
        #expect(parts.title == nil)
        #expect(parts.history.isEmpty)
        #expect(parts.rest.count == 2)
    }
    @Test func identifiesMetadataProse() {
        #expect(KBMarkdownPreview.isMetadata(.prose("**Parent:** [[knowledge-base]]")))
        #expect(KBMarkdownPreview.isMetadata(.prose("**Prev:** 2026-05-20 ...")))
        #expect(!KBMarkdownPreview.isMetadata(.prose("Normal text **bold** inside")))
        #expect(!KBMarkdownPreview.isMetadata(.heading(level: 1, text: "Title")))
    }
}

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

@Suite("KBMarkdownLexer")
struct KBMarkdownLexerTests {
    @Test func parsesHeadingLevels() {
        #expect(KBMarkdownLexer.heading("## Sub")?.level == 2)
        #expect(KBMarkdownLexer.heading("## Sub")?.text == "Sub")
        #expect(KBMarkdownLexer.heading("###### Deep")?.level == 6)
        #expect(KBMarkdownLexer.heading("#NoSpace") == nil)        // needs a space
        #expect(KBMarkdownLexer.heading("####### TooDeep") == nil) // 7 hashes invalid
    }
    @Test func parsesUnorderedAndOrderedLists() {
        let bullet = KBMarkdownLexer.listItem("- item")
        #expect(bullet?.depth == 0 && bullet?.ordinal == nil && bullet?.text == "item")
        let nested = KBMarkdownLexer.listItem("  - nested")
        #expect(nested?.depth == 1 && nested?.text == "nested")
        let ordered = KBMarkdownLexer.listItem("1. first")
        #expect(ordered?.ordinal == "1." && ordered?.text == "first")
        #expect(KBMarkdownLexer.listItem("plain") == nil)
    }
    @Test func detectsSeparatorButNotHorizontalRule() {
        #expect(KBMarkdownLexer.isTableSeparator("|---|---|"))
        #expect(KBMarkdownLexer.isTableSeparator("| :--- | ---: |"))
        #expect(!KBMarkdownLexer.isTableSeparator("---"))      // hr, no pipe
        #expect(!KBMarkdownLexer.isTableSeparator("| a | b |")) // content row
    }
    @Test func splitsRowDroppingOuterPipes() {
        #expect(KBMarkdownLexer.splitRow("| Name | Role | Email |") == ["Name", "Role", "Email"])
    }
    @Test func splitsRowHonoringEscapedPipeInWikilink() {
        // `[[people\|Priya]]` — the escaped pipe is cell content, not a column break.
        let cells = KBMarkdownLexer.splitRow("| Alex | sees [[people\\|Priya]] | x |")
        #expect(cells == ["Alex", "sees [[people|Priya]]", "x"])
    }
}

@Suite("KB wikilink extraction")
struct KBWikilinkExtractionTests {
    @Test func extractsTargetsBeforePipeDeduped() {
        let links = KnowledgeBaseService.extractWikilinks(
            "see [[atlas]] and [[people|Alias]] and [[atlas]] again")
        #expect(links == ["atlas", "people"])
    }
    @Test func extractsTargetBeforeEscapedPipe() {
        // Table cells write the alias separator as `\|` so the pipe isn't a
        // column break — the target must still resolve.
        let links = KnowledgeBaseService.extractWikilinks(
            #"| Alex | sees [[people\|Priya]] and [[projects\|the roadmap]] |"#)
        #expect(links == ["people", "projects"])
    }
    @Test func ignoresEmptyAndMalformed() {
        #expect(KnowledgeBaseService.extractWikilinks("no links here").isEmpty)
        #expect(KnowledgeBaseService.extractWikilinks("[[ ]]").isEmpty)
    }
}

@MainActor
@Suite("KnowledgeBaseService graph")
struct KBServiceGraphTests {
    /// A small linked KB: people ←→ scout/atlas, with atlas → scout too.
    private func makeLinkedKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbgraph-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        let projects = kb.appendingPathComponent("projects")
        try FileManager.default.createDirectory(at: projects, withIntermediateDirectories: true)
        try "# People\nWorks on [[atlas]] and [[scout]]."
            .write(to: kb.appendingPathComponent("people.md"), atomically: true, encoding: .utf8)
        try "# Scout\nLed by [[people|Someone]]."
            .write(to: projects.appendingPathComponent("scout.md"), atomically: true, encoding: .utf8)
        try "# Atlas\nWith [[people]] and related to [[scout]]."
            .write(to: projects.appendingPathComponent("atlas.md"), atomically: true, encoding: .utf8)
        return root
    }

    @Test func resolvesLinksBacklinksAndLocalGraph() async throws {
        let root = try makeLinkedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        #expect(svc.resolveWikilink("scout") == "knowledge-base/projects/scout.md")
        #expect(svc.resolveWikilink("nonexistent") == nil)

        let out = svc.outgoingLinks(for: "knowledge-base/people.md")
        #expect(Set(out.map(\.target)) == ["atlas", "scout"])
        #expect(out.allSatisfy { $0.resolved != nil })

        let back = Set(svc.backlinks(for: "knowledge-base/people.md").map(\.path))
        #expect(back == ["knowledge-base/projects/scout.md", "knowledge-base/projects/atlas.md"])

        let g = svc.localGraph(around: "knowledge-base/people.md")
        #expect(g.nodes.count == 3)
        #expect(g.nodes.contains { $0.id == "knowledge-base/people.md" && $0.isCenter })
        #expect(!g.edges.isEmpty)

        let stats = svc.graphStats()
        #expect(stats.notes == 3)
        #expect(stats.links == 3)   // people–scout, people–atlas, scout–atlas
    }

    @Test func contentSearchReturnsSnippet() async throws {
        let root = try makeLinkedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        let hits = svc.searchContent("atlas")
        #expect(hits.contains { $0.path == "knowledge-base/projects/atlas.md" })
        #expect(hits.contains { $0.path == "knowledge-base/people.md" })
    }
}

@MainActor
@Suite("KnowledgeBaseService full graph")
struct KBFullGraphTests {
    @Test func fullGraphHasAllNotesAndEdges() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbfull-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try "# A\nlinks [[b]]".write(to: kb.appendingPathComponent("a.md"), atomically: true, encoding: .utf8)
        try "# B\nno links".write(to: kb.appendingPathComponent("b.md"), atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: root) }

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
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

    @Test func replaceLinesAcceptsMultilineReplacement() {
        let segs = KBDocSegment.segments(from: src)
        let para = segs.first { $0.kind == .paragraph }!
        let out = KBDocSegment.replaceLines(in: src, start: para.lineStart, end: para.lineEnd,
                                            with: "Line one\nLine two")
        #expect(out.contains("Line one\nLine two"))
        #expect(out.contains("# Title"))
    }

    @Test func replaceLinesOutOfRangeIsNoOp() {
        #expect(KBDocSegment.replaceLines(in: src, start: 999, end: 1000, with: "x") == src)
    }

    @Test func replaceCellOutOfRangeLineIsNoOp() {
        #expect(KBDocSegment.replaceCell(in: src, sourceLine: 999, col: 0, value: "x") == src)
        #expect(KBDocSegment.replaceCell(in: src, sourceLine: 12, col: -1, value: "x") == src)
    }

    @Test func replaceCellPadsRaggedRow() {
        // Header has 3 columns but the source row only 2 — the parser pads the
        // display row, and editing that padded cell must land in the file.
        let doc = "| A | B | C |\n|---|---|---|\n| 4 | 5 |"
        let out = KBDocSegment.replaceCell(in: doc, sourceLine: 2, col: 2, value: "6")
        #expect(out.hasSuffix("| 4 | 5 | 6 |"))
    }

    @Test func codeFenceIsOneSegmentIncludingFences() {
        let doc = "before\n\n```swift\nlet x = 1\n```\n\nafter"
        let segs = KBDocSegment.segments(from: doc)
        let code = segs.first { $0.kind == .code }
        #expect(code?.raw == "```swift\nlet x = 1\n```")
        #expect(segs.contains { $0.kind == .paragraph && $0.raw == "before" })
        #expect(segs.contains { $0.kind == .paragraph && $0.raw == "after" })
    }

    @Test func blockquoteGroupsConsecutiveLines() {
        let doc = "> line a\n> line b\n\nnormal"
        let segs = KBDocSegment.segments(from: doc)
        let quote = segs.first { $0.kind == .quote }
        #expect(quote?.lineStart == 0 && quote?.lineEnd == 1)
    }

    @Test func tablePadsAndTruncatesRaggedRows() {
        let md = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |"
        let table = KBDocSegment.segments(from: md).first { $0.kind == .table }
        #expect(table?.headers == ["A", "B", "C"])
        #expect(table?.rows == [["1", "2", "3"], ["4", "5", ""]])
    }

    @Test func tableEndsAtBlankLine() {
        let md = "| A | B |\n|---|---|\n| 1 | 2 |\n\nafter"
        let segs = KBDocSegment.segments(from: md)
        let table = segs.first { $0.kind == .table }
        #expect(table?.rows == [["1", "2"]])
        #expect(segs.contains { $0.kind == .paragraph && $0.raw == "after" })
    }

    @Test func unterminatedFrontmatterIsNotFrontmatter() {
        let segs = KBDocSegment.segments(from: "---\ntitle: X\nno closing")
        #expect(!segs.contains { $0.kind == .frontmatter })
    }

    @Test func multilineParagraphSpansLines() {
        let doc = "one\ntwo\nthree"
        let segs = KBDocSegment.segments(from: doc)
        #expect(segs.count == 1)
        #expect(segs[0].kind == .paragraph)
        #expect(segs[0].lineStart == 0 && segs[0].lineEnd == 2)
    }
}

@Suite("KnowledgeBaseFileWriter symlink paths")
struct KBWriterSymlinkTests {
    @Test func relativePathResolvesSymlinkedRepo() throws {
        let fm = FileManager.default
        let real = fm.temporaryDirectory.appendingPathComponent("kbreal-\(UUID().uuidString)")
        let kb = real.appendingPathComponent("knowledge-base")
        try fm.createDirectory(at: kb, withIntermediateDirectories: true)
        let file = kb.appendingPathComponent("people.md")
        try "x".write(to: file, atomically: true, encoding: .utf8)
        let link = fm.temporaryDirectory.appendingPathComponent("kblink-\(UUID().uuidString)")
        try fm.createSymbolicLink(at: link, withDestinationURL: real)
        defer { try? fm.removeItem(at: link); try? fm.removeItem(at: real) }

        // Repo via symlink, file via the real (symlink-resolved) path — the exact
        // mismatch that produced "people.md is outside the knowledge base".
        #expect(KnowledgeBaseFileWriter.relativePathInRepo(fileURL: file, repo: link)
                == "knowledge-base/people.md")
        // Both via the symlink path.
        let fileViaLink = link.appendingPathComponent("knowledge-base/people.md")
        #expect(KnowledgeBaseFileWriter.relativePathInRepo(fileURL: fileViaLink, repo: link)
                == "knowledge-base/people.md")
    }
}

@Suite("KBDocSegment metadata collapse")
struct KBDocSegmentPartitionTests {
    @Test func collapsesChangelogAfterTitle() {
        let segs = KBDocSegment.segments(from:
            "# People\n**Last updated:** today\n\n**Prev:** yesterday\n\nReal intro.\n\n## Team")
        let parts = KBDocSegment.partition(segs)
        #expect(parts.title?.kind == .heading(1))
        // The changelog paragraphs collapse into history; the intro stays in the body.
        #expect(parts.history.count == 2)
        #expect(parts.rest.contains { $0.kind == .paragraph && $0.raw == "Real intro." })
        #expect(parts.rest.contains { $0.kind == .heading(2) })
        #expect(!parts.rest.contains { KBDocSegment.isMetadata($0) })
    }
    @Test func titleRisesAboveFrontmatter() {
        // Frontmatter is segment 0, but the H1 must still become the title so
        // the metadata disclosure renders below it, not above.
        let segs = KBDocSegment.segments(from:
            "---\ntype: person\n---\n# Alex\n**Last updated:** today\n\nIntro.")
        let parts = KBDocSegment.partition(segs)
        #expect(parts.title?.kind == .heading(1))
        #expect(parts.history.count == 2)   // frontmatter + changelog line
        #expect(parts.history.contains { $0.kind == .frontmatter })
        #expect(parts.rest.contains { $0.kind == .paragraph && $0.raw == "Intro." })
    }
    @Test func noTitleNoCollapseWhenPlain() {
        let segs = KBDocSegment.segments(from: "Just a normal note.\n\nSecond paragraph.")
        let parts = KBDocSegment.partition(segs)
        #expect(parts.title == nil)
        #expect(parts.history.isEmpty)
        #expect(parts.rest.count == 2)
    }
    @Test func identifiesMetadataParagraphs() {
        let segs = KBDocSegment.segments(from:
            "**Parent:** [[knowledge-base]]\n\nNormal text **bold** inside\n\n# Title")
        #expect(KBDocSegment.isMetadata(segs[0]))
        #expect(!KBDocSegment.isMetadata(segs[1]))
        #expect(!KBDocSegment.isMetadata(segs[2]))   // heading, not prose
    }
}

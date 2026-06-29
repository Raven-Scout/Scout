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

import Foundation
import Testing
@testable import Scout

@Suite("KBIndex link resolution")
struct KBIndexResolveTests {
    private let index = KBIndex(
        stemPaths: ["alex": ["knowledge-base/people/alex.md"], "people": ["knowledge-base/people.md"]],
        outByFile: [:], textByFile: [:], typeByFile: [:], vaultNames: [])

    @Test func pathFormPicksTheRightNoteWhenStemsCollide() {
        let collided = KBIndex(
            stemPaths: ["alex": ["knowledge-base/people/alex.md", "knowledge-base/projects/alex.md"]],
            outByFile: [:], textByFile: [:], typeByFile: [:], vaultNames: [])
        #expect(collided.resolve("people/alex") == "knowledge-base/people/alex.md")
        #expect(collided.resolve("projects/alex") == "knowledge-base/projects/alex.md")
        #expect(collided.resolve("alex") == "knowledge-base/projects/alex.md")   // bare: last in tree order, as before
    }

    @Test func bareStemAnyCase() {
        #expect(index.resolve("alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("Alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("ghost") == nil)
    }

    @Test func pathFormMatchesByPathSuffix() {
        #expect(index.resolve("people/alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("knowledge-base/people/alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("knowledge-base/people") == "knowledge-base/people.md")
        #expect(index.resolve("projects/alex") == nil)             // stem exists, path doesn't
    }

    @Test func anchorsAndExtension() {
        #expect(index.resolve("alex#Role") == "knowledge-base/people/alex.md")
        #expect(index.resolve("alex#^block1") == "knowledge-base/people/alex.md")
        #expect(index.resolve("alex.md") == "knowledge-base/people/alex.md")
        #expect(index.resolve("people/alex.md#Role") == "knowledge-base/people/alex.md")
    }

    @Test func anchorOnlyIsTheLinkingNote() {
        #expect(index.resolve("#Role", from: "knowledge-base/hub.md") == "knowledge-base/hub.md")
        #expect(index.resolve("#Role") == nil)
    }
}

@MainActor
@Suite("KnowledgeBaseService path and anchor links")
struct KBServicePathLinkTests {
    /// hub → `[[people/alex]]`; alex → `[[hub#Intro]]` + `[[#Role]]`;
    /// priya → `[[hub.md]]`. All resolve; none is dangling.
    private func makeKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbpath-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        let people = kb.appendingPathComponent("people")
        try FileManager.default.createDirectory(at: people, withIntermediateDirectories: true)
        try "Owner: [[people/alex]].".write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        try "See [[hub#Intro]] and [[#Role]].\n## Role"
            .write(to: people.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "[[hub.md]]".write(to: people.appendingPathComponent("priya.md"), atomically: true, encoding: .utf8)
        return root
    }

    @Test func pathAnchorAndExtensionLinksBecomeEdgesAndBacklinks() async throws {
        let root = try makeKB(); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        #expect(svc.resolveWikilink("people/alex") == "knowledge-base/people/alex.md")
        #expect(svc.outgoingLinks(for: "knowledge-base/hub.md")
                == [KBLink(target: "people/alex", resolved: "knowledge-base/people/alex.md")])
        #expect(svc.graphStats().links == 2)                       // hub–alex, hub–priya
        #expect(Set(svc.backlinks(for: "knowledge-base/hub.md").map(\.path))
                == ["knowledge-base/people/alex.md", "knowledge-base/people/priya.md"])
        let toAlex = svc.backlinks(for: "knowledge-base/people/alex.md")
        #expect(toAlex.map(\.path) == ["knowledge-base/hub.md"])
        #expect(toAlex.first?.excerpt == "Owner: [[people/alex]].")
        #expect(svc.networkStats().dangling.isEmpty)
        // Resolved once, at index build time (off the main actor).
        #expect(svc.index.resolvedOut["knowledge-base/hub.md"] == ["knowledge-base/people/alex.md"])
        #expect(svc.index.edges.count == 2)
    }

    @Test func backlinkExcerptIsTheLineWhoseLinkResolves() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbexcerpt-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try "# Scout".write(to: kb.appendingPathComponent("scout.md"), atomically: true, encoding: .utf8)
        try "Repo: https://github.com/example-org/scout#readme\nWorks on [[scout]]."
            .write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.backlinks(for: "knowledge-base/scout.md").first?.excerpt == "Works on [[scout]].")
    }
}

@Suite("KB dangling exclusions")
struct KBDanglingExclusionTests {
    @Test func ticketIDs() {
        #expect(KBIndex.isTicketID("PROJ-1234"))
        #expect(KBIndex.isTicketID("OPS-7"))
        #expect(!KBIndex.isTicketID("proj-1234"))                  // lowercase → a note name
        #expect(!KBIndex.isTicketID("day-1"))
        #expect(!KBIndex.isTicketID("PROJ-12a"))
    }

    @Test func vaultNamesMatchByLastPathComponent() {
        let index = KBIndex(stemPaths: [:], outByFile: [:], textByFile: [:], typeByFile: [:],
                            vaultNames: ["action-items-2020-01-05", "chart-x.png"])
        #expect(index.existsInVault("action-items-2020-01-05"))
        #expect(index.existsInVault("action-items/Action-Items-2020-01-05#Morning"))
        #expect(index.existsInVault("chart-x.png"))
        #expect(!index.existsInVault("ghost"))
        #expect(!index.existsInVault("#Role"))
    }

    @Test func vaultFileNamesSkipHiddenAndIgnoredDirs() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbvault-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        for dir in ["notes", ".hidden", "node_modules"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(dir),
                                                    withIntermediateDirectories: true)
        }
        try "".write(to: root.appendingPathComponent("notes/fresh-note.md"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent("chart-x.png"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent(".hidden/secret.md"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent("node_modules/dep.md"), atomically: true, encoding: .utf8)
        #expect(KnowledgeBaseService.vaultFileNames(under: root, skippingMarkdownIn: root.appendingPathComponent("kb"))
                == ["fresh-note", "chart-x.png"])
    }

    /// The KB's own notes are the resolver's job; listing them too would hide
    /// every resolver miss whose stem exists somewhere in the KB.
    @Test func vaultFileNamesSkipKBMarkdownButKeepOtherKBFiles() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbvault-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try "".write(to: kb.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "".write(to: kb.appendingPathComponent("chart-x.png"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent("fresh-note.md"), atomically: true, encoding: .utf8)
        #expect(KnowledgeBaseService.vaultFileNames(under: root, skippingMarkdownIn: kb)
                == ["chart-x.png", "fresh-note"])
    }
}

@MainActor
@Suite("KnowledgeBaseService dangling links")
struct KBServiceDanglingTests {
    @Test func onlyTrulyMissingTargetsAreDangling() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbdangle-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let daily = root.appendingPathComponent("action-items")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: daily, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: kb.appendingPathComponent("people"), withIntermediateDirectories: true)
        try "".write(to: daily.appendingPathComponent("action-items-2020-01-05.md"), atomically: true, encoding: .utf8)
        try "".write(to: kb.appendingPathComponent("people/alex.md"), atomically: true, encoding: .utf8)
        // `projects/alex` names a folder alex isn't in: Obsidian leaves it
        // unresolved, so it is dangling even though an `alex` note exists.
        try "[[action-items-2020-01-05]] [[PROJ-1234]] [[people/ghost]] [[projects/alex]]"
            .write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.networkStats().dangling == [
            KBDanglingLink(source: "knowledge-base/hub.md", target: "people/ghost"),
            KBDanglingLink(source: "knowledge-base/hub.md", target: "projects/alex"),
        ])
    }
}

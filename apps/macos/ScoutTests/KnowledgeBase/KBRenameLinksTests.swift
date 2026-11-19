import Foundation
import Testing
@testable import Scout

/// Renaming a note points the links to it at the new name (UI audit #75).
@Suite("KB rename — inbound links")
struct KBLinkRewriteTests {
    private let alex = "knowledge-base/people/alex.md"
    private let index = KBIndex(
        stemPaths: ["alex": ["knowledge-base/people/alex.md"],
                    "hub": ["knowledge-base/hub.md"],
                    "priya": ["knowledge-base/projects/priya.md"]],
        outByFile: [:], textByFile: [:], typeByFile: [:], vaultNames: [])

    private func rewrite(_ text: String, to stem: String = "alexandra",
                         in index: KBIndex? = nil) -> (text: String, count: Int) {
        KBLinkRewrite.rewrite(text, source: "knowledge-base/hub.md", oldPath: alex,
                              newStem: stem, newKBPath: "people/\(stem)", index: index ?? self.index)
    }

    @Test("every form of a link to the note is renamed, keeping what was written around the name")
    func formsKept() {
        let text = """
        Owner: [[alex]], also [[Alex|our Alex]] and [[people/alex#Role]].
        | Who | [[alex\\|A]] |
        Spelled out: [[alex.md]] and [[ people/alex.md#^b1 ]].
        """
        let out = rewrite(text)
        #expect(out.count == 6)
        #expect(out.text == """
        Owner: [[alexandra]], also [[alexandra|our Alex]] and [[people/alexandra#Role]].
        | Who | [[alexandra\\|A]] |
        Spelled out: [[alexandra.md]] and [[ people/alexandra.md#^b1 ]].
        """)
    }

    @Test("links to other notes, missing notes and the note itself stay as written")
    func othersUntouched() {
        let text = "[[hub]] [[priya]] [[ghost]] [[projects/alex]] [[#Role]] [alex](https://example.com/alex) alex"
        let out = rewrite(text)
        #expect(out.count == 0)
        #expect(out.text == text)
    }

    @Test("when another note already has the new name, a bare link takes the path form")
    func collidingNameUsesPath() {
        let crowded = KBIndex(
            stemPaths: ["alex": [alex], "priya": ["knowledge-base/projects/priya.md"]],
            outByFile: [:], textByFile: [:], typeByFile: [:], vaultNames: [])
        let out = rewrite("[[alex]] and [[people/alex]]", to: "priya", in: crowded)
        #expect(out.text == "[[people/priya]] and [[people/priya]]")
    }

    @Test("inbound notes are counted per note, most links first, without the note itself")
    func inbound() {
        let idx = KBIndex(
            stemPaths: index.stemPaths,
            outByFile: ["knowledge-base/hub.md": ["alex", "people/alex"],
                        "knowledge-base/projects/priya.md": ["alex"],
                        alex: ["alex"]],
            textByFile: ["knowledge-base/hub.md": "[[alex]] [[people/alex]] [[alex|A]]",
                         "knowledge-base/projects/priya.md": "[[alex]]",
                         alex: "[[alex]]"],
            typeByFile: [:], vaultNames: [])
        let inbound = KBLinkRewrite.inboundNotes(to: alex, in: idx)
        #expect(inbound.map(\.path) == ["knowledge-base/hub.md", "knowledge-base/projects/priya.md"])
        #expect(inbound.map(\.links) == [3, 1])
    }

    @Test("the rename sheet says how many links change, and what happens if they don't")
    func sheetWording() {
        typealias Links = KBEditorView.RenameLinks
        let links = Links.make(inbound: [("a", 3), ("b", 1)], unsavedPaths: ["b"])
        #expect(links == Links(links: 4, notes: 2, unsaved: 1))
        #expect(links.toggleTitle == "Update 4 links in 2 notes")
        #expect(links.detail(updating: false).hasPrefix("4 links in 2 notes will stop working"))
        #expect(links.unsavedNote == "1 note with unsaved edits won't be updated. Save it first to include it.")
        #expect(Links(links: 1, notes: 1, unsaved: 0).toggleTitle == "Update 1 link in 1 note")
        #expect(Links(links: 1, notes: 1, unsaved: 0).unsavedNote == nil)
    }

    @Test("a rename that left notes alone says which, and one that didn't says nothing")
    func skippedNotice() {
        #expect(KBEditorView.RenameLinks.skippedNotice(.none) == nil)
        let notice = KBEditorView.RenameLinks.skippedNotice(
            KBRenameLinkReport(linksUpdated: 2, notesUpdated: 1, notesSkipped: ["knowledge-base/hub.md"]))
        #expect(notice?.title == "Renamed, but some links still use the old name")
        #expect(notice?.message.contains("1 note that links here wasn't updated: hub.") == true)
    }
}

@MainActor
@Suite("KB rename — writer updates links")
struct KBRenameWriterLinkTests {

    private final class Commits: GitServiceProtocol, @unchecked Sendable {
        private let lock = NSLock()
        private var _all: [(paths: [String], message: String)] = []
        var all: [(paths: [String], message: String)] { lock.lock(); defer { lock.unlock() }; return _all }
        func commitPaths(_ relPaths: [String], message: String) async throws {
            lock.lock(); _all.append((relPaths, message)); lock.unlock()
        }
    }

    /// people/alex.md, and three notes linking to it: hub (two links, one
    /// path form), priya (a table cell alias, with a byte-order mark) and
    /// sam (has unsaved edits in the app).
    private func makeVault() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-rename-links-\(UUID().uuidString)").resolvingSymlinksInPath()
        let kb = root.appendingPathComponent("knowledge-base")
        let people = kb.appendingPathComponent("people")
        try FileManager.default.createDirectory(at: people, withIntermediateDirectories: true)
        try "# Alex\n\nWorks with [[priya]].\n".write(to: people.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n"
            .write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        try (KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alex\\|A]] |\n".utf8))
            .write(to: people.appendingPathComponent("priya.md"))
        try "Pairs with [[alex]].\n".write(to: people.appendingPathComponent("sam.md"), atomically: true, encoding: .utf8)
        return root
    }

    private func text(_ url: URL) throws -> String { try String(contentsOf: url, encoding: .utf8) }

    @Test("renaming updates every linking note in one commit and one undo, leaving unsaved notes alone")
    func renameUpdatesLinks() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let people = kb.appendingPathComponent("people")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        // sam has unsaved edits open in the app.
        let sam = people.appendingPathComponent("sam.md")
        service.drafts.update(KBDraft(text: "Pairs with [[alex]]. More.\n", originalText: "Pairs with [[alex]].\n",
                                      baselineContents: "Pairs with [[alex]].\n", baselineDate: nil), for: sam)

        let plan = KBRenameLinkPlan.make(renaming: "knowledge-base/people/alex.md", index: service.index,
                                         scoutDirectory: service.scoutDirectory, drafts: service.drafts)
        #expect(plan.skipped == ["knowledge-base/people/sam.md"])
        #expect(Set(plan.notes.map(\.lastPathComponent)) == ["hub.md", "priya.md"])

        let git = Commits()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: git)
        let alex = people.appendingPathComponent("alex.md")
        let (dest, change, report) = try await writer.renameUpdatingLinks(fileURL: alex, to: "alexandra", links: plan)

        #expect(dest.lastPathComponent == "alexandra.md")
        #expect(report == KBRenameLinkReport(linksUpdated: 3, notesUpdated: 2,
                                             notesSkipped: ["knowledge-base/people/sam.md"]))
        #expect(try text(kb.appendingPathComponent("hub.md"))
                == "Owner: [[alexandra]]. Profile: [[people/alexandra#Role|Alex]].\n")
        let priya = try Data(contentsOf: people.appendingPathComponent("priya.md"))
        #expect(priya == KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alexandra\\|A]] |\n".utf8))
        #expect(try text(sam) == "Pairs with [[alex]].\n")   // unsaved edits: not touched

        // One commit holds the move and both link updates.
        #expect(git.all.count == 1)
        #expect(Set(git.all[0].paths) == ["knowledge-base/people/alex.md", "knowledge-base/people/alexandra.md",
                                          "knowledge-base/hub.md", "knowledge-base/people/priya.md"])
        #expect(git.all[0].message.hasSuffix("(updated 3 links in 2 notes)"))

        // After the rename the links resolve; only sam's is left dangling.
        await service.reparseAndWait()
        #expect(Set(service.backlinks(for: "knowledge-base/people/alexandra.md").map(\.path))
                == ["knowledge-base/hub.md", "knowledge-base/people/priya.md"])
        #expect(service.networkStats().dangling.map(\.source) == ["knowledge-base/people/sam.md"])

        // Undo puts the note and every rewritten link back.
        let undo = try #require(change)
        try await writer.restore(undo, .undo)
        #expect(FileManager.default.fileExists(atPath: alex.path))
        #expect(!FileManager.default.fileExists(atPath: dest.path))
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
        #expect(try Data(contentsOf: people.appendingPathComponent("priya.md"))
                == KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alex\\|A]] |\n".utf8))
    }

    @Test("without a plan a rename moves only the note")
    func renameWithoutLinks() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        let (_, _, report) = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "alexandra", links: nil)
        #expect(report == .none)
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
    }

    @Test("a linking note that isn't UTF-8 is reported, not rewritten, and the rename still happens")
    func unreadableNoteSkipped() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        let plan = KBRenameLinkPlan.make(renaming: "knowledge-base/people/alex.md", index: service.index,
                                         scoutDirectory: service.scoutDirectory, drafts: service.drafts)
        // hub turns into invalid UTF-8 after the sheet was shown.
        let bad = Data([0x5B, 0x5B, 0xFF, 0xFE, 0x5D, 0x5D])
        try bad.write(to: kb.appendingPathComponent("hub.md"))
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        let (dest, _, report) = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "alexandra", links: plan)
        #expect(FileManager.default.fileExists(atPath: dest.path))
        #expect(report.notesSkipped == ["knowledge-base/hub.md"])
        #expect(try Data(contentsOf: kb.appendingPathComponent("hub.md")) == bad)
    }
}

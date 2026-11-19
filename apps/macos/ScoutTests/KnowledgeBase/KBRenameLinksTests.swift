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
                         in index: KBIndex? = nil) -> (text: String, count: Int, ambiguous: Int) {
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

    @Test("links to other notes, missing notes and markdown links stay as written")
    func othersUntouched() {
        let text = "[[hub]] [[priya]] [[ghost]] [[projects/alex]] [[#Role]] [alex](https://example.com/alex) [a](people/alex.md) alex"
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
        #expect(KBLinkRewrite.newNameCollides("Priya", oldPath: alex, index: crowded))
        #expect(!KBLinkRewrite.newNameCollides("alexandra", oldPath: alex, index: crowded))
    }

    @Test("with two notes of the old name, bare links are left alone and counted; path links still move")
    func sharedOldName() {
        let projectAlex = "knowledge-base/projects/alex.md"
        // projects/alex is last in tree order, so a bare [[alex]] resolves to it.
        let shared = KBIndex(
            stemPaths: ["alex": [alex, projectAlex], "hub": ["knowledge-base/hub.md"]],
            outByFile: ["knowledge-base/hub.md": ["alex", "projects/alex", "people/alex"]],
            textByFile: ["knowledge-base/hub.md": "[[alex]] [[projects/alex|the project]] [[people/alex]]"],
            typeByFile: [:], vaultNames: [])
        #expect(KBLinkRewrite.oldNameIsShared(projectAlex, index: shared))
        let out = KBLinkRewrite.rewrite("[[alex]] [[projects/alex|the project]] [[people/alex]]",
                                        source: "knowledge-base/hub.md", oldPath: projectAlex,
                                        newStem: "atlas", newKBPath: "projects/atlas", index: shared)
        #expect(out.text == "[[alex]] [[projects/atlas|the project]] [[people/alex]]")
        #expect(out.count == 1)
        #expect(out.ambiguous == 1)
        #expect(KBLinkRewrite.inboundNotes(to: projectAlex, in: shared)
                == [KBLinkRewrite.Inbound(path: "knowledge-base/hub.md", links: 1, ambiguous: 1)])
    }

    @Test("inbound notes are counted per note, most links first, without the note itself")
    func inbound() {
        let idx = KBIndex(
            stemPaths: index.stemPaths,
            outByFile: ["knowledge-base/hub.md": ["alex", "people/alex"],
                        "knowledge-base/projects/priya.md": ["alex"],
                        alex: ["alex", "#Role"]],
            textByFile: ["knowledge-base/hub.md": "[[alex]] [[people/alex]] [[alex|A]]",
                         "knowledge-base/projects/priya.md": "[[alex]]",
                         alex: "[[alex#Contact]] and [[#Role]]"],
            typeByFile: [:], vaultNames: [])
        let inbound = KBLinkRewrite.inboundNotes(to: alex, in: idx)
        #expect(inbound.map(\.path) == ["knowledge-base/hub.md", "knowledge-base/projects/priya.md"])
        #expect(inbound.map(\.links) == [3, 1])
        // [[alex#Contact]] names the note; [[#Role]] doesn't need changing.
        #expect(KBLinkRewrite.selfLinks(of: alex, in: idx) == 1)
    }

    @Test("the rename sheet says how many links change, and what happens if they don't")
    func sheetWording() {
        typealias Links = KBEditorView.RenameLinks
        let links = Links.make(inbound: [.init(path: "a", links: 3), .init(path: "b", links: 1, ambiguous: 2)],
                               selfLinks: 1, unsavedPaths: ["b"])
        #expect(links == Links(links: 5, notes: 3, unsaved: 1, ambiguous: 2))
        #expect(links.toggleTitle == "Update 5 links in 3 notes")
        #expect(links.detail(updating: true).contains("as long as none of those notes has changed since"))
        #expect(links.detail(updating: false).hasPrefix("5 links in 3 notes will stop working"))
        #expect(links.unsavedNote == "1 note with unsaved edits won't be updated. Save it first to include it.")
        #expect(links.ambiguousNote(name: "alex")
                == "2 links say [[alex]], and more than one note has that name, so they aren't changed.")
        #expect(Links(links: 1, notes: 1, unsaved: 0).toggleTitle == "Update 1 link in 1 note")
        #expect(Links(links: 1, notes: 1, unsaved: 0).unsavedNote == nil)
        #expect(Links.make(inbound: [], unsavedPaths: []).isEmpty)
        #expect(Links.collisionNote(newName: "priya").contains("[[priya]] may start pointing to this note"))
    }

    @Test("a name with link characters is refused as it's typed, for notes only")
    func linkCharacters() {
        let note = URL(fileURLWithPath: "/kb/knowledge-base/alex.md")
        for name in ["C# notes", "Q3 | draft", "a[b]", "x^y"] {
            #expect(KBEditorView.renameNameProblem(name, for: note) != nil, "\(name)")
            #expect(throws: KBWriterError.self) { try KnowledgeBaseFileWriter.renameDestination(for: note, to: name) }
        }
        #expect(KBEditorView.renameNameProblem("alexandra", for: note) == nil)
        #expect(KBEditorView.renameNameProblem("C# notes", for: URL(fileURLWithPath: "/kb/knowledge-base/s.yaml")) == nil)
    }

    @Test("a dotted name keeps the note a note")
    func dottedNames() throws {
        let note = URL(fileURLWithPath: "/kb/knowledge-base/vendor.md")
        for (typed, file) in [("acme.com", "acme.com.md"), ("vue.js", "vue.js.md"), ("v2.1", "v2.1.md"),
                              ("acme", "acme.md"), ("Acme.MD", "Acme.MD"), ("acme.md", "acme.md")] {
            #expect(try KnowledgeBaseFileWriter.renameDestination(for: note, to: typed).lastPathComponent == file)
        }
        #expect(KnowledgeBaseFileWriter.noteStem("acme.com.md") == "acme.com")
        #expect(KnowledgeBaseFileWriter.noteStem("Acme.MD") == "Acme")
        // Other files still keep their own extension only when none is typed.
        let yaml = URL(fileURLWithPath: "/kb/knowledge-base/schema.yaml")
        #expect(try KnowledgeBaseFileWriter.renameDestination(for: yaml, to: "s2").lastPathComponent == "s2.yaml")
    }

    @Test("the notice after a rename names each note left alone, with why, and a failed commit")
    func outcomeNotice() {
        typealias Links = KBEditorView.RenameLinks
        #expect(Links.outcomeNotice(.none, commitProblem: nil) == nil)
        let report = KBRenameLinkReport(
            linksUpdated: 2, notesUpdated: 1,
            skipped: [.init(path: "knowledge-base/hub.md", reason: .unsavedEdits),
                      .init(path: "knowledge-base/sam.md", reason: .writeFailed)])
        let notice = Links.outcomeNotice(report, commitProblem: nil)
        #expect(notice?.title == "Renamed, but some links still use the old name")
        #expect(notice?.message.contains("hub (unsaved edits); sam (couldn't be written)") == true)
        let failed = Links.outcomeNotice(KBRenameLinkReport(linksUpdated: 2, notesUpdated: 1),
                                         commitProblem: "index.lock exists.")
        #expect(failed?.title == "Renamed, but not committed")
        #expect(failed?.message.contains("renamed and 2 links in 1 note updated, but the git commit failed: index.lock exists.") == true)
        #expect(failed?.message.contains("Edit ▸ Undo puts it all back.") == true)
    }
}

@MainActor
@Suite("KB rename — writer updates links")
struct KBRenameWriterLinkTests {

    private final class Commits: GitServiceProtocol, @unchecked Sendable {
        private let lock = NSLock()
        private var _all: [(paths: [String], message: String)] = []
        private let fails: Bool
        init(fails: Bool = false) { self.fails = fails }
        struct Boom: Error {}
        var all: [(paths: [String], message: String)] { lock.lock(); defer { lock.unlock() }; return _all }
        func commitPaths(_ relPaths: [String], message: String) async throws {
            lock.lock(); _all.append((relPaths, message)); lock.unlock()
            if fails { throw Boom() }
        }
    }

    /// people/alex.md (linking to itself), and three notes linking to it:
    /// hub (two links, one path form), priya (a table cell alias, with a
    /// byte-order mark) and sam (has unsaved edits in the app).
    private func makeVault() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-rename-links-\(UUID().uuidString)").resolvingSymlinksInPath()
        let kb = root.appendingPathComponent("knowledge-base")
        let people = kb.appendingPathComponent("people")
        try FileManager.default.createDirectory(at: people, withIntermediateDirectories: true)
        try "# Alex\n\nWorks with [[priya]]. See [[alex#Contact]] and [[#Contact]].\n## Contact\n"
            .write(to: people.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n"
            .write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        try (KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alex\\|A]] |\n".utf8))
            .write(to: people.appendingPathComponent("priya.md"))
        try "Pairs with [[alex]].\n".write(to: people.appendingPathComponent("sam.md"), atomically: true, encoding: .utf8)
        return root
    }

    private func text(_ url: URL) throws -> String { try String(contentsOf: url, encoding: .utf8) }

    private func plan(_ service: KnowledgeBaseService) -> KBRenameLinkPlan {
        KBRenameLinkPlan.make(renaming: "knowledge-base/people/alex.md", index: service.index,
                              scoutDirectory: service.scoutDirectory, drafts: service.drafts)
    }

    @Test("renaming updates every linking note and the note's own links in one commit and one undo, leaving unsaved notes alone")
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

        let plan = plan(service)
        #expect(plan.skipped == ["knowledge-base/people/sam.md"])
        #expect(Set(plan.notes.map(\.lastPathComponent)) == ["hub.md", "priya.md"])
        #expect(plan.selfLinks == 1)

        let git = Commits()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: git)
        let alex = people.appendingPathComponent("alex.md")
        let original = try text(alex)
        let result = try await writer.renameUpdatingLinks(fileURL: alex, to: "alexandra", links: plan)
        let dest = result.url

        #expect(dest.lastPathComponent == "alexandra.md")
        #expect(result.commitProblem == nil)
        #expect(result.links == KBRenameLinkReport(
            linksUpdated: 4, notesUpdated: 3,
            skipped: [.init(path: "knowledge-base/people/sam.md", reason: .unsavedEdits)]))
        #expect(try text(dest) == "# Alex\n\nWorks with [[priya]]. See [[alexandra#Contact]] and [[#Contact]].\n## Contact\n")
        #expect(try text(kb.appendingPathComponent("hub.md"))
                == "Owner: [[alexandra]]. Profile: [[people/alexandra#Role|Alex]].\n")
        let priya = try Data(contentsOf: people.appendingPathComponent("priya.md"))
        #expect(priya == KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alexandra\\|A]] |\n".utf8))
        #expect(try text(sam) == "Pairs with [[alex]].\n")   // unsaved edits: not touched

        // One commit holds the move and both link updates.
        #expect(git.all.count == 1)
        #expect(Set(git.all[0].paths) == ["knowledge-base/people/alex.md", "knowledge-base/people/alexandra.md",
                                          "knowledge-base/hub.md", "knowledge-base/people/priya.md"])
        #expect(git.all[0].message.hasSuffix("(updated 4 links in 3 notes)"))

        // After the rename the links resolve; only sam's is left dangling.
        await service.reparseAndWait()
        #expect(Set(service.backlinks(for: "knowledge-base/people/alexandra.md").map(\.path))
                == ["knowledge-base/hub.md", "knowledge-base/people/priya.md"])
        #expect(service.networkStats().dangling.map(\.source) == ["knowledge-base/people/sam.md"])

        // Undo puts the note, its own text and every rewritten link back.
        let undo = try #require(result.change)
        try await writer.restore(undo, .undo)
        #expect(try text(alex) == original)
        #expect(!FileManager.default.fileExists(atPath: dest.path))
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
        #expect(try Data(contentsOf: people.appendingPathComponent("priya.md"))
                == KnowledgeBaseFileWriter.utf8BOM + Data("| Lead | [[alex\\|A]] |\n".utf8))
    }

    @Test("a dotted new name keeps the note in the index and its links working")
    func dottedRenameKeepsLinks() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        let result = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "acme.com", links: plan(service))
        #expect(result.url.lastPathComponent == "acme.com.md")
        #expect(try text(kb.appendingPathComponent("hub.md"))
                == "Owner: [[acme.com]]. Profile: [[people/acme.com#Role|Alex]].\n")
        await service.reparseAndWait()
        #expect(service.resolveWikilink("acme.com") == "knowledge-base/people/acme.com.md")
        #expect(service.backlinks(for: "knowledge-base/people/acme.com.md").map(\.path).contains("knowledge-base/hub.md"))
    }

    @Test("a failed commit still returns the undo step and the report")
    func commitFailureKeepsUndo() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: Commits(fails: true))
        let result = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "alexandra", links: plan(service))
        #expect(result.commitProblem != nil)
        #expect(result.links.notesUpdated == 4)   // hub, priya, sam and the note itself
        let undo = try #require(result.change)
        try await writer.restore(undo, .undo)
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
        #expect(FileManager.default.fileExists(atPath: kb.appendingPathComponent("people/alex.md").path))

        // The old API still throws for a failed commit, after the move.
        let plain = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: Commits(fails: true))
        await #expect(throws: KBWriterError.self) {
            _ = try await plain.renameReturningChange(fileURL: kb.appendingPathComponent("hub.md"), to: "hub2")
        }
        #expect(FileManager.default.fileExists(atPath: kb.appendingPathComponent("hub2.md").path))
    }

    @Test("without a plan a rename moves only the note")
    func renameWithoutLinks() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        let result = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "alexandra", links: nil)
        #expect(result.links == .none)
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
        #expect(try text(result.url).contains("[[alex#Contact]]"))
    }

    @Test("a linking note that isn't UTF-8 is reported as unreadable, and the rename still happens")
    func unreadableNoteSkipped() async throws {
        let root = try makeVault(); defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        let plan = plan(service)
        // hub turns into invalid UTF-8 after the sheet was shown.
        let bad = Data([0x5B, 0x5B, 0xFF, 0xFE, 0x5D, 0x5D])
        try bad.write(to: kb.appendingPathComponent("hub.md"))
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        let result = try await writer.renameUpdatingLinks(
            fileURL: kb.appendingPathComponent("people/alex.md"), to: "alexandra", links: plan)
        #expect(FileManager.default.fileExists(atPath: result.url.path))
        #expect(result.links.skipped == [.init(path: "knowledge-base/hub.md", reason: .unreadable)])
        #expect(try Data(contentsOf: kb.appendingPathComponent("hub.md")) == bad)
    }

    @Test("with links to update, a note that can't be read isn't renamed")
    func unreadableNoteRefused() async throws {
        let root = try makeVault(); defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o644],
                                                   ofItemAtPath: root.appendingPathComponent("knowledge-base/people/alex.md").path)
            try? FileManager.default.removeItem(at: root)
        }
        let kb = root.appendingPathComponent("knowledge-base")
        let service = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS(), drafts: KBDraftStore())
        await service.reparseAndWait()
        let plan = plan(service)
        let alex = kb.appendingPathComponent("people/alex.md")
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: alex.path)
        let writer = KnowledgeBaseFileWriter(scoutDirectory: root, gitService: nil)
        await #expect(throws: KBWriterError.self) {
            _ = try await writer.renameUpdatingLinks(fileURL: alex, to: "alexandra", links: plan)
        }
        #expect(FileManager.default.fileExists(atPath: alex.path))
        #expect(try text(kb.appendingPathComponent("hub.md")) == "Owner: [[alex]]. Profile: [[people/alex#Role|Alex]].\n")
    }
}

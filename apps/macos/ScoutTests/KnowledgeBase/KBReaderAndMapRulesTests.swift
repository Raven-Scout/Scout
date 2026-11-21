import AppKit
import Combine
import Foundation
import SwiftUI
import Testing
@testable import Scout

/// UI audit T1: the note reader's header at narrow widths, the Links panel
/// collapsing, drafts shown and marked, the conflict check on a file change,
/// and the knowledge map's panels and layout cache.
@MainActor
@Suite("KB reader and map rules", .serialized)
struct KBReaderAndMapRulesTests {

    // MARK: - Header

    @Test("the breadcrumb splits into the folders between and the note's stem")
    func breadcrumbTail() {
        func tail(_ path: String) -> [String] {
            let t = KBEditorView.breadcrumbTail(path)
            return [t.folders, t.note]
        }
        #expect(tail("knowledge-base/people/team/alex.md") == ["people / team", "alex"])
        #expect(tail("knowledge-base/alex.md") == ["", "alex"])
        #expect(tail("knowledge-base/ontology/schema.yaml") == ["ontology", "schema"])
        #expect(tail("knowledge-base") == ["", ""])
    }

    /// At 1280 pt with the sidebar, tree and Links panel open the reader was
    /// about 410 pt wide, and the header wrapped one letter per line, which
    /// made the page taller than the window.
    /// From `KnowledgeBaseView.minEditorWidth` (the narrowest the page lets
    /// the editor get) up. `KBNoteColumnLayout` caps the column's height at
    /// the one offered, so the height is measured with the cap out of the way
    /// (`fixedSize` asks for the natural height): an overgrown header still
    /// fails here instead of hiding as clipped content.
    @Test("the editor fits a narrow reader column without growing taller than the window",
          arguments: [Double(KnowledgeBaseView.minEditorWidth), 410.0, 520.0, 800.0])
    func editorFitsNarrowColumn(width: Double) async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = vault.state.knowledgeBaseService
        svc.load()
        await svc.reparseAndWait()
        let node = try #require(svc.tree.flatMap(\.allFiles).first)
        let editor = KBEditorView(
            node: node, service: svc, writer: vault.state.knowledgeBaseWriterBox.writer,
            onDeleted: {}, onRenamed: { _ in }, onRenameProblem: { _ in }, onOverview: {},
            linksPanelShown: true, onToggleLinksPanel: {})
            .environmentObject(vault.state.vaultUndo)
        let proposal = CGSize(width: width, height: 600)
        let natural = LayoutHarness.size(of: editor.fixedSize(horizontal: false, vertical: true), proposing: proposal)
        #expect(natural.height <= 600, "the editor's natural height is \(natural.height) pt at \(width) pt wide")
        let size = LayoutHarness.size(of: editor, proposing: proposal)
        #expect(size.width <= width + 0.5, "the editor asked for \(size.width) pt at \(width) pt wide")
    }

    // MARK: - Links panel

    @Test("the Links panel follows the page width until the user picks")
    func linksPanelByWidth() {
        typealias V = KnowledgeBaseView
        // 1280 pt window: 1059 pt page, too narrow for tree + reader + panel.
        #expect(!V.linksPanelShown(choice: .auto, pageWidth: 1059))
        #expect(V.linksPanelShown(choice: .auto, pageWidth: 1379))
        #expect(V.linksPanelShown(choice: .auto, pageWidth: nil))
        #expect(V.linksPanelShown(choice: .shown, pageWidth: 800))
        #expect(!V.linksPanelShown(choice: .hidden, pageWidth: 2000))
        #expect(V.toggledLinksPanel(shown: true) == .hidden)
        #expect(V.toggledLinksPanel(shown: false) == .shown)
    }

    // MARK: - Drafts

    @Test("leaving a note for the overview asks only when it has an unsaved draft")
    func leaveStep() {
        #expect(KnowledgeBaseView.leaveStep(hasUnsavedDraft: false) == .leave)
        #expect(KnowledgeBaseView.leaveStep(hasUnsavedDraft: true) == .confirm)
        // Esc stays; only Discard is destructive.
        #expect(KnowledgeBaseView.LeaveChoice.stay.role == .cancel)
        #expect(KnowledgeBaseView.LeaveChoice.discard.role == .destructive)
        #expect(KnowledgeBaseView.LeaveChoice.keepDraft.role == nil)
    }

    @Test("a restored draft gets the Unsaved draft banner until saved, discarded or dismissed")
    func draftBanner() {
        #expect(KBEditorView.showsDraftBanner(restored: true, isDirty: true, dismissed: false))
        #expect(!KBEditorView.showsDraftBanner(restored: true, isDirty: false, dismissed: false))
        #expect(!KBEditorView.showsDraftBanner(restored: true, isDirty: true, dismissed: true))
        #expect(!KBEditorView.showsDraftBanner(restored: false, isDirty: true, dismissed: false))
    }

    /// A banner above the note jumped the whole window's content up under
    /// the title bar (hand check of #750). The split view measures the detail
    /// column's minimum size in its own hosting view, at a near-zero width the
    /// window's 1070 pt minimum doesn't reach. There a banner's text wraps a
    /// word per line and asked for 1,500-2,300 pt, and the overflow was
    /// centred, pushing the content up by half of it. The note column caps
    /// its height at the one offered.
    @Test("a reader banner never makes the note column ask for more height than offered",
          arguments: ["draft", "disk"])
    func bannerDoesNotGrowTheWindow(kind: String) {
        @ViewBuilder func banner() -> some View {
            if kind == "draft" {
                KBEditorView.draftBanner(save: {}, discard: {}, dismiss: {})
            } else {
                KBEditorView.changedOnDiskBanner(name: "a-long-note-name-about-the-quarterly-plan",
                                                 saveMine: {}, reload: {})
            }
        }
        func stack() -> some View {
            VStack(spacing: 0) {
                Text("header").lineLimit(1)
                banner()
                Color.clear
            }
        }
        // Why the cap is needed: unwrapped, the zero-size probe is taller than any window.
        let unwrapped = LayoutHarness.size(of: stack(), proposing: .zero)
        #expect(unwrapped.height > LayoutSize.minimumPageHeight,
                "the banner asked for only \(unwrapped.height) pt at zero width")

        let column = KBNoteColumnLayout { stack() }
        #expect(LayoutHarness.size(of: column, proposing: .zero).height == 0)
        // At a real size it takes exactly what it's offered.
        let width = Double(KnowledgeBaseView.minEditorWidth)
        let real = LayoutHarness.size(of: column, proposing: CGSize(width: width, height: 600))
        #expect(real.height == 600)
        #expect(real.width <= width + 0.5, "the column asked for \(real.width) pt at \(width) pt wide")
    }

    @Test("the note editor's column is the capped one")
    func editorUsesTheCappedColumn() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = vault.state.knowledgeBaseService
        svc.load()
        await svc.reparseAndWait()
        let node = try #require(svc.tree.flatMap(\.allFiles).first)
        let editor = KBEditorView(
            node: node, service: svc, writer: vault.state.knowledgeBaseWriterBox.writer,
            onDeleted: {}, onRenamed: { _ in }, onRenameProblem: { _ in }, onOverview: {},
            linksPanelShown: true, onToggleLinksPanel: {})
            .environmentObject(vault.state.vaultUndo)
        // Without the cap the header alone answers the zero-size probe with its height.
        #expect(LayoutHarness.size(of: editor, proposing: .zero).height == 0)
    }

    /// The bug as the window showed it: a split view whose detail column is
    /// the note column with a banner, in a 1081 × 800 window. Uncapped, the
    /// header was laid out at y = -304 (draft) and -720 (disk).
    @Test("with a banner in the detail column, the header and sidebar stay in the window",
          arguments: ["draft", "disk"])
    func bannerKeepsTheWindowInPlace(kind: String) async {
        final class Tops: ObservableObject { var header = CGFloat.nan; var sidebar = CGFloat.nan }
        struct Page: View {
            let kind: String
            let tops: Tops
            var body: some View {
                NavigationSplitView {
                    List {
                        Text("A sidebar row")
                            .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).minY } action: { tops.sidebar = $0 }
                    }
                    .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 240)
                } detail: {
                    HStack(spacing: 0) {
                        Color.clear.frame(width: KnowledgeBaseView.treeWidth)
                        KBNoteColumnLayout {
                            VStack(spacing: 0) {
                                Text("The note header").lineLimit(1).padding(12)
                                    .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).minY } action: { tops.header = $0 }
                                if kind == "draft" {
                                    KBEditorView.draftBanner(save: {}, discard: {}, dismiss: {})
                                } else {
                                    KBEditorView.changedOnDiskBanner(name: "a-long-note-name-about-the-quarterly-plan",
                                                                     saveMine: {}, reload: {})
                                }
                                Color.clear
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
                .frame(minWidth: MainWindowSize.minWidth, minHeight: MainWindowSize.minHeight)
            }
        }
        let tops = Tops()
        let controller = NSHostingController(rootView: Page(kind: kind, tops: tops))
        let window = NSWindow(contentViewController: controller)
        window.styleMask = [.titled, .resizable, .fullSizeContentView]
        window.isReleasedWhenClosed = false
        window.setContentSize(CGSize(width: 1081, height: 800))
        window.orderFrontRegardless()
        defer { window.orderOut(nil); window.contentViewController = nil }
        for _ in 0..<20 {
            controller.view.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(50))
        }
        #expect(tops.header >= 0, "the header was laid out at y = \(tops.header)")
        #expect(tops.sidebar >= 0, "the sidebar row was laid out at y = \(tops.sidebar)")
    }

    @Test("a squeezed folders crumb truncates to an ellipsis, never a clipped glyph")
    func folderCrumbFloor() {
        let floor = KBEditorView.minFolderCrumbWidth
        func crumb(_ s: String) -> some View {
            KBCrumbFloorLayout(floor: floor) { Text(s).lineLimit(1).truncationMode(.middle) }
        }
        let squeezed = LayoutHarness.size(of: crumb("people / team / projects"), proposing: CGSize(width: 4, height: 20))
        #expect(squeezed.width >= floor - 8 && squeezed.width <= floor,
                "a squeezed crumb took \(squeezed.width) pt")
        // A short folder keeps its own width at any proposal: no padding after it.
        let natural = LayoutHarness.size(of: Text("ops"), proposing: CGSize(width: 200, height: 20)).width
        #expect(LayoutHarness.size(of: crumb("ops"), proposing: CGSize(width: 4, height: 20)).width == natural)
        #expect(LayoutHarness.size(of: crumb("ops"), proposing: CGSize(width: 200, height: 20)).width == natural)
    }

    @Test("the draft store publishes when a file gains or loses a draft, not on every keystroke")
    func draftStorePublishes() {
        let store = KBDraftStore()
        let scout = URL(fileURLWithPath: "/tmp/vault")
        let url = scout.appendingPathComponent("knowledge-base/people/alex.md")
        var changes = 0
        let sub = store.objectWillChange.sink { changes += 1 }
        defer { sub.cancel() }

        store.update(KBDraft(text: "a", originalText: "", baselineContents: "", baselineDate: nil), for: url)
        store.update(KBDraft(text: "ab", originalText: "", baselineContents: "", baselineDate: nil), for: url)
        store.update(KBDraft(text: "abc", originalText: "", baselineContents: "", baselineDate: nil), for: url)
        #expect(changes == 1)
        #expect(store.relativePaths(in: scout) == ["knowledge-base/people/alex.md"])
        #expect(KBDraftStore.containsDraft(under: "knowledge-base/people", in: store.relativePaths(in: scout)))
        #expect(!KBDraftStore.containsDraft(under: "knowledge-base/peo", in: store.relativePaths(in: scout)))

        store.discard(for: url)
        #expect(changes == 2)
        #expect(store.relativePaths(in: scout).isEmpty)
    }

    // MARK: - Changes on disk

    @Test("a change on disk under unsaved edits is a conflict at once; without edits it reloads")
    func externalChange() {
        let t0 = Date(timeIntervalSince1970: 1_000), t1 = Date(timeIntervalSince1970: 2_000)
        typealias E = KBEditorView
        #expect(E.externalChange(isDirty: true, baselineDate: t0, currentDate: t0,
                                 baselineContents: "a", diskText: { "b" }) == .none)
        #expect(E.externalChange(isDirty: true, baselineDate: t0, currentDate: nil,
                                 baselineContents: "a", diskText: { "b" }) == .none)
        #expect(E.externalChange(isDirty: true, baselineDate: t0, currentDate: t1,
                                 baselineContents: "a", diskText: { "a" }) == .sameContent(t1))
        #expect(E.externalChange(isDirty: true, baselineDate: t0, currentDate: t1,
                                 baselineContents: "a", diskText: { "b" }) == .conflict)
        #expect(E.externalChange(isDirty: false, baselineDate: t0, currentDate: t1,
                                 baselineContents: "a", diskText: { "b" }) == .reload)
        // A restored draft can predate the file's mtime being known.
        #expect(E.externalChange(isDirty: true, baselineDate: nil, currentDate: t1,
                                 baselineContents: "a", diskText: { "b" }) == .conflict)
    }

    // MARK: - Map

    @Test("the map's panel choices round-trip, and nil means by width")
    func mapPanels() throws {
        let defaults = try #require(UserDefaults(suiteName: "kb-map-panels-\(UUID().uuidString)"))
        #expect(KBMapPanels.load(from: defaults) == KBMapPanels())
        KBMapPanels(message: ["legend": true, "card": NSNull()]).save(to: defaults)
        #expect(KBMapPanels.load(from: defaults) == KBMapPanels(legend: true, card: nil))

        // The page sees a missing key, which it reads as "by width".
        let json = try #require(String(data: JSONEncoder().encode(KBMapPanels(legend: false)), encoding: .utf8))
        #expect(json == #"{"legend":false}"#)
    }

    @Test("the layout cache keeps finite positions only, and survives a relaunch on disk")
    func layoutCacheOnDisk() throws {
        let raw: [String: [Double]] = ["a": [1, 2], "b": [.nan, 3], "c": [4], "d": [.infinity, 0]]
        #expect(KBMapLayoutCache.sanitized(raw) == ["a": [1, 2]])

        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kb-map-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("layout.json")
        try #require(KBMapLayoutCache.encode(raw)).write(to: file)
        #expect(KBMapLayoutCache.read(file) == ["a": [1, 2]])
        #expect(KBMapLayoutCache.read(dir.appendingPathComponent("missing.json")).isEmpty)

        // One file per vault.
        let one = KBMapLayoutCache.defaultFile(scoutDirectory: URL(fileURLWithPath: "/tmp/vault-one"))
        let two = KBMapLayoutCache.defaultFile(scoutDirectory: URL(fileURLWithPath: "/tmp/vault-two"))
        #expect(one != nil && one != two)
    }

    @Test("a corrupt or wrongly shaped cache file opens an empty map instead of failing")
    func layoutCacheCorruptFile() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kb-map-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let garbage = dir.appendingPathComponent("garbage.json")
        try Data([0xff, 0x00, 0x7b, 0x22]).write(to: garbage)
        let shape = dir.appendingPathComponent("shape.json")
        try Data(#"{"a":[1,2],"b":"not a point"}"#.utf8).write(to: shape)
        let truncated = dir.appendingPathComponent("truncated.json")
        try Data(#"{"a":[1,"#.utf8).write(to: truncated)
        #expect(KBMapLayoutCache.read(garbage).isEmpty)
        #expect(KBMapLayoutCache.read(shape).isEmpty)
        #expect(KBMapLayoutCache.read(truncated).isEmpty)

        // Through the cache itself: the map opens empty, then a settled
        // layout replaces the bad file.
        let saved = (KBMapLayoutCache.file, KBMapLayoutCache.positions)
        defer { (KBMapLayoutCache.file, KBMapLayoutCache.positions) = saved }
        KBMapLayoutCache.file = garbage
        KBMapLayoutCache.positions = [:]
        #expect(KBMapLayoutCache.current().isEmpty)
        KBMapLayoutCache.store(["a": [1, 2]])
        KBMapLayoutCache.waitForWrites()
        #expect(KBMapLayoutCache.read(garbage) == ["a": [1, 2]])
    }

    @Test("layouts reach the disk in the order they were stored")
    func layoutCacheWritesInOrder() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kb-map-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("nested/layout.json")
        let saved = (KBMapLayoutCache.file, KBMapLayoutCache.positions)
        defer { (KBMapLayoutCache.file, KBMapLayoutCache.positions) = saved }
        KBMapLayoutCache.file = file
        for i in 0..<50 { KBMapLayoutCache.store(["n": [Double(i), 0]]) }
        KBMapLayoutCache.waitForWrites()
        #expect(KBMapLayoutCache.read(file) == ["n": [49, 0]])
    }

    // MARK: - Web bridge

    @Test("a page saying ready gets what it last showed first, then the queue, without repeats")
    func bridgeMessagesOnReady() {
        typealias B = KBWebBridge
        // First load: the queued load is the latest one; it isn't sent twice.
        #expect(B.messagesOnReady(queue: [("load", "L1"), ("shown", "S")], latest: ["load": "L1"])
                == ["L1", "S"])
        // A reload that raced the old page's ready: the queue is empty, the
        // new page still gets the map back.
        #expect(B.messagesOnReady(queue: [], latest: ["load": "L1"]) == ["L1"])
        // A newer load waiting in the queue wins over replaying the old one.
        #expect(B.messagesOnReady(queue: [("load", "L2")], latest: ["load": "L2"]) == ["L2"])
        #expect(B.messagesOnReady(queue: [("focus", "F")], latest: ["load": "L1"]) == ["L1", "F"])
    }

    @Test("folder dots follow the tree: a draft whose note left it marks nothing")
    func draftMarksFollowTree() throws {
        func node(_ kind: KBNode.Kind, _ rel: String, _ children: [KBNode] = []) -> KBNode {
            KBNode(kind: kind, url: URL(fileURLWithPath: "/tmp/vault/\(rel)"), relativePath: rel,
                   name: (rel as NSString).lastPathComponent, ext: kind == .file ? "md" : "", children: children)
        }
        let folder = node(.directory, "knowledge-base/people", [node(.file, "knowledge-base/people/alex.md")])
        let drafted: Set<String> = ["knowledge-base/people/alex.md", "knowledge-base/people/priya.md"]
        #expect(KBDraftStore.shownDraftPaths(drafted, tree: [folder]) == ["knowledge-base/people/alex.md"])
        #expect(KBDraftStore.shownDraftPaths(drafted, tree: []).isEmpty)
    }
}

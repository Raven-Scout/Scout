import AppKit
import SwiftUI
import Testing
@testable import Scout

/// The Control Center's run-detail tabs and the Knowledge Base's editor
/// panes. Both sit behind a selection the app only makes at runtime, so
/// nothing reached them before.
@MainActor
@Suite("View smoke — detail panes", .serialized)
struct DetailSmokeTests {

    private let paneSize = CGSize(width: 1000, height: 700)

    /// A run pointing at the vault's real session log, so the log-backed tabs
    /// have something to parse.
    private func run(in vault: SmokeVault, status: RunStatus = .success) -> Run {
        Run.make(
            type: .morningBriefing,
            startedAt: SmokeFixtures.day,
            endedAt: SmokeFixtures.day.addingTimeInterval(368),
            status: status,
            logPath: vault.root.appendingPathComponent(".scout-logs/scout-2026-06-15_08-03.log"),
            commits: SmokeFixtures.commits)
    }

    // MARK: - Run detail

    @Test("the run detail pane renders for every run status")
    func runDetailRendersEveryStatus() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        for status in [RunStatus.success, .failure, .running, .timeout,
                       .orphaned, .skippedBudget, .skippedConcurrency, .rateLimited] {
            ViewHost.render(
                RunDetailView(run: run(in: vault, status: status))
                    .environmentObject(vault.state),
                size: paneSize)
        }
    }

    @Test("every run-detail tab has a display name")
    func runDetailTabsHaveNames() {
        #expect(RunDetailTab.allCases.count == 7)
        for tab in RunDetailTab.allCases {
            #expect(!tab.displayName.isEmpty)
            #expect(tab.id == tab.rawValue)
        }
        #expect(Set(RunDetailTab.allCases.map(\.displayName)).count == 7)
    }

    // MARK: - Detail tabs

    @Test("the summary tab renders from a real log")
    func summaryTabRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(SummaryTab(logPath: run(in: vault).logPath), size: paneSize)
        // A log that isn't there — the load-failed branch.
        ViewHost.render(
            SummaryTab(logPath: vault.root.appendingPathComponent(".scout-logs/absent.log")),
            size: paneSize)
    }

    @Test("the log viewer renders a real log and a missing one")
    func logViewerRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(LogViewer(logPath: run(in: vault).logPath), size: paneSize)
        ViewHost.render(
            LogViewer(logPath: vault.root.appendingPathComponent(".scout-logs/absent.log")),
            size: paneSize)
    }

    @Test("the tools and files tabs render")
    func toolsAndFilesTabsRender() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let r = run(in: vault)
        ViewHost.render(ToolsTab(run: r).environmentObject(vault.state), size: paneSize)
        ViewHost.render(FilesTab(run: r).environmentObject(vault.state), size: paneSize)
    }

    @Test("the feedback tab renders")
    func feedbackTabRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            FeedbackTab(run: run(in: vault)).environmentObject(vault.state), size: paneSize)
    }

    @Test("the diff viewer renders with and without commits")
    func diffViewerRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            DiffViewer(commits: SmokeFixtures.commits).environmentObject(vault.state),
            size: paneSize)
        ViewHost.render(
            DiffViewer(commits: []).environmentObject(vault.state),
            size: paneSize)
    }

    @Test("the errors tab renders with and without errors")
    func errorsTabRenders() {
        ViewHost.render(ErrorsTab(errors: SmokeFixtures.errors), size: paneSize)
        ViewHost.render(ErrorsTab(errors: []), size: paneSize)
    }

    @Test("the metadata tab renders")
    func metadataTabRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(MetadataTab(run: run(in: vault)), size: paneSize)
    }

    // MARK: - Knowledge Base panes

    /// A KB service loaded from the smoke vault's notes.
    private func loadedKB(_ vault: SmokeVault) async -> KnowledgeBaseService {
        let svc = vault.state.knowledgeBaseService
        svc.load()
        await svc.reparseAndWait()
        return svc
    }

    @Test("the KB overview renders")
    func kbOverviewRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = await loadedKB(vault)
        ViewHost.render(KBOverviewView(service: svc, onNavigate: { _ in }), size: paneSize)
    }

    @Test("the KB tree renders, collapsed and expanded")
    func kbTreeRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = await loadedKB(vault)

        var selected: String? = nil
        var expanded: Set<String> = []
        let selectionBinding = Binding(get: { selected }, set: { selected = $0 })
        let expandedBinding = Binding(get: { expanded }, set: { expanded = $0 })

        ViewHost.render(
            KBTreeView(nodes: svc.tree, selectedPath: selectionBinding,
                       expanded: expandedBinding),
            size: CGSize(width: 280, height: 700))

        expanded = Set(svc.tree.map(\.relativePath))
        selected = svc.tree.flatMap(\.allFiles).first?.relativePath
        ViewHost.render(
            KBTreeView(nodes: svc.tree, selectedPath: selectionBinding,
                       expanded: expandedBinding),
            size: CGSize(width: 280, height: 700))
    }

    @Test("the KB editor renders for a real note")
    func kbEditorRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = await loadedKB(vault)
        let node = try #require(svc.tree.flatMap(\.allFiles).first)

        ViewHost.render(
            KBEditorView(
                node: node, service: svc,
                writer: vault.state.knowledgeBaseWriterBox.writer,
                onDeleted: {}, onRenamed: { _ in }, onOverview: {}),
            size: paneSize)
    }

    @Test("the KB right panel renders both tabs' content")
    func kbRightPanelRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = await loadedKB(vault)
        let relPath = try #require(svc.tree.flatMap(\.allFiles).first?.relativePath)

        ViewHost.render(
            KBRightPanel(relPath: relPath, service: svc, onNavigate: { _ in }),
            size: CGSize(width: 320, height: 700))
    }

    @Test("the editable document view renders and survives an empty document")
    func kbEditableViewRenders() {
        var source = """
        ---
        type: person
        ---
        # Alex

        A paragraph with **bold**, `code`, and a [[wikilink]].

        - a list item
        - another item

        > a blockquote line

        | Col | Val |
        | --- | --- |
        | a   | 1   |

        ```
        a fenced block
        ```
        """
        let binding = Binding(get: { source }, set: { source = $0 })
        ViewHost.render(KBEditableView(source: binding), size: paneSize)

        source = ""
        ViewHost.render(KBEditableView(source: binding), size: paneSize)
    }

    @Test("the local graph canvas renders")
    func kbGraphCanvasRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = await loadedKB(vault)
        let graph = svc.fullGraph()
        ViewHost.render(
            KBGraphCanvas(graph: graph, onNavigate: { _ in }),
            size: CGSize(width: 700, height: 600))
        ViewHost.render(
            KBGraphCanvas(graph: .empty, onNavigate: { _ in }),
            size: CGSize(width: 700, height: 600))
    }
}

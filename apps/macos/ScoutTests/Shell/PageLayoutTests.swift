import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Every page at the window sizes people actually use (UI audit card C6),
/// against `BigVault`: paragraph-length titles, long note and connector
/// names, ~150 sessions.
///
/// The gate is width: each page, and each header or filter row the audit
/// found clipping, must lay out within the width it gets at the minimum
/// window (1100 pt), so nothing is pushed off an edge at 1100, 1280 or
/// 1600 pt. Run with `TEST_RUNNER_SCOUT_LAYOUT_SNAPSHOTS=<dir>` to also write
/// a PNG of the whole window per page, size and appearance for review.
@MainActor
@Suite("Layout — pages at real window sizes", .serialized)
struct PageLayoutTests {

    /// Each page as `MainWindowView` builds it.
    static func page(_ item: SidebarItem, in vault: BigVault) -> AnyView {
        let state = vault.state
        let view: AnyView
        switch item {
        case .controlCenter:
            view = AnyView(ControlCenterView())
        case .sessions:
            view = AnyView(SessionsView().environmentObject(state.sessionIndexService))
        case .actionItems:
            view = AnyView(ActionItemsView(scoutDirectory: state.scoutDirectory,
                                           actionItemsDirectory: state.actionItemsDirectory)
                .environmentObject(state.actionItemsDocumentService)
                .environmentObject(state.actionItemsWriterBox)
                .environmentObject(state.actionItemsEnvState)
                .environmentObject(state.displayProfileService))
        case .schedules:
            view = AnyView(SchedulesView()
                .environmentObject(state.scheduleEditService)
                .environmentObject(state.modelSettingsService))
        case .proposals:
            view = AnyView(ProposalsView()
                .environmentObject(state.proposalsDocumentService)
                .environmentObject(state.proposalsWriterBox))
        case .wishlist:
            view = AnyView(PerFileListView(config: .wishlist)
                .environmentObject(state.wishlistDocumentService)
                .environmentObject(state.perFileWriterBox))
        case .research:
            view = AnyView(PerFileListView(config: .research)
                .environmentObject(state.researchDocumentService)
                .environmentObject(state.perFileWriterBox))
        case .knowledgeBase:
            view = AnyView(KnowledgeBaseView()
                .environmentObject(state.knowledgeBaseService)
                .environmentObject(state.knowledgeBaseWriterBox))
        case .settings:
            view = AnyView(SettingsView())
        }
        return AnyView(view
            .environmentObject(state)
            .environmentObject(state.proposalsDocumentService)
            .environmentObject(vault.updates))
    }

    /// A loaded `BigVault`, checked: a page that silently failed to load
    /// renders its empty state, which fits any width and proves nothing.
    static func loadedVault() async throws -> BigVault {
        let vault = try BigVault()
        await vault.load()
        let state = vault.state
        guard case .loaded(let doc) = state.actionItemsDocumentService.state else {
            Issue.record("action items didn't load: \(state.actionItemsDocumentService.state)")
            return vault
        }
        #expect(doc.sections.flatMap(\.tasks).count >= 100)
        #expect(!state.knowledgeBaseService.tree.isEmpty)
        #expect((state.sessionIndexService.index?.sessions.count ?? 0) >= 100)
        #expect(state.wishlistDocumentService.activeCount > 0)
        #expect(!state.researchDocumentService.items.isEmpty)
        #expect(state.proposalsDocumentService.pendingCount > 0)
        #expect(!state.scheduleEditService.slots.isEmpty)
        #expect(!state.sessionLogService.runs.isEmpty)
        return vault
    }

    static func window(_ item: SidebarItem, in vault: BigVault) -> some View {
        MainWindowView(initialSelection: item)
            .environmentObject(vault.state)
            .environmentObject(vault.state.proposalsDocumentService)
            .environmentObject(vault.updates)
    }

    @Test("every page fits the page width at the minimum window", arguments: SidebarItem.allCases)
    func pageFitsAtMinimumWindow(_ item: SidebarItem) async throws {
        let vault = try await Self.loadedVault(); defer { vault.tearDown() }
        let available = LayoutSize.pageWidth(window: LayoutSize.windowWidths[0])
        let needed = LayoutHarness.minimumWidth(of: Self.page(item, in: vault), height: LayoutSize.minimumPageHeight)
        #expect(needed <= available, "\(item) needs \(needed) pt; it gets \(available) pt at a 1100 pt window")
    }

    // MARK: - Action Items filter row

    @Test("the Action Items filter row fits beside its controls at every width")
    func actionItemsFilterRowFits() {
        var filter = ActionItemsFilter(kinds: [.urgent, .watching, .digest], status: .snoozed, searchText: "")
        let binding = Binding(get: { filter }, set: { filter = $0 })
        let minimum = LayoutHarness.minimumWidth(of: FilterChipsView(filter: binding), height: 40)
        // The view switch, View menu and Select beside it, plus the row's
        // padding: what the filters can never have.
        let besideIt: CGFloat = 400
        for window in LayoutSize.windowWidths {
            let available = LayoutSize.pageWidth(window: window) - besideIt
            #expect(minimum <= available, "filters need \(minimum) pt; \(available) pt left at a \(window) pt window")
        }
    }

    @Test("the compact menus name what is chosen")
    func filterMenuTitles() {
        #expect(FilterChipsView.kindMenuTitle(for: []) == "Sections: All")
        #expect(FilterChipsView.kindMenuTitle(for: [.watching]) == "Sections: Watching")
        // Listed in chip order, not set order.
        #expect(FilterChipsView.kindMenuTitle(for: [.digest, .urgent, .todo]) == "Sections: Urgent + 2")
        #expect(FilterChipsView.statusTitle(.snoozed) == "Snoozed")
    }

    // MARK: - Sessions header and detail pane

    @Test("the Sessions header never wraps its title and fits the board's width")
    func sessionsHeaderFits() async throws {
        let vault = try await Self.loadedVault(); defer { vault.tearDown() }
        let service = vault.state.sessionIndexService
        let index = try #require(service.index)
        var mode = SessionsViewMode.board
        // The longest project name chosen: the menu label is at its widest.
        var filter = SessionsFilter()
        filter.projectKey = index.projects.max { $0.name.count < $1.name.count }?.key
        let header = SessionsHeader(
            viewMode: Binding(get: { mode }, set: { mode = $0 }),
            filter: Binding(get: { filter }, set: { filter = $0 }),
            counts: [:], projects: SessionsLayout.menuProjects(index: index), service: service)

        let minimum = LayoutHarness.minimumWidth(of: header, height: 400)
        let title = LayoutHarness.size(of: Text("Sessions").font(DS.serif(28, weight: .medium)),
                                       proposing: CGSize(width: 10_000, height: 100)).height
        for window in LayoutSize.windowWidths {
            let page = LayoutSize.pageWidth(window: window)
            let pane = SessionsView.DetailPane.layout(pageWidth: page, hasSelection: true)
            guard case .shown(let paneWidth, _) = pane else { Issue.record("no pane with a selection"); continue }
            let board = page - paneWidth - 1
            #expect(minimum <= board, "header needs \(minimum) pt; the board gets \(board) pt at \(window) pt")
            // Title (one line) + freshness (at most two) + controls + chips +
            // padding. A title squeezed into "Session" / "s" is taller.
            let height = LayoutHarness.height(of: header, width: board)
            #expect(height <= title + 2 * 16 + 120, "header is \(height) pt tall at \(window) pt")
        }
    }

    @Test("the detail pane steps aside on a narrow page with nothing selected")
    func detailPaneLayout() {
        typealias Pane = SessionsView.DetailPane
        #expect(Pane.layout(pageWidth: nil, hasSelection: false) == .shown(width: 380, closable: false))
        #expect(Pane.layout(pageWidth: 1379, hasSelection: false) == .shown(width: 380, closable: false))
        #expect(Pane.layout(pageWidth: 1059, hasSelection: true) == .shown(width: 380, closable: false))
        #expect(Pane.layout(pageWidth: 879, hasSelection: false) == .hidden)
        #expect(Pane.layout(pageWidth: 879, hasSelection: true) == .shown(width: 340, closable: true))
    }

    @Test("Schedules closes its pane only when compact, and never over a new slot or unsaved edits")
    func schedulesCloseRule() {
        let compact = DetailPaneLayout.shown(width: 340, closable: true)
        let regular = DetailPaneLayout.shown(width: 380, closable: false)
        #expect(SchedulesView.canCloseDetail(pane: compact, isNewDraft: false, isDirty: false))
        // A dirty edit of an existing slot survives Esc and the close button.
        #expect(!SchedulesView.canCloseDetail(pane: compact, isNewDraft: false, isDirty: true))
        #expect(!SchedulesView.canCloseDetail(pane: compact, isNewDraft: true, isDirty: false))
        // At full width Esc never closes the pane, dirty or not.
        #expect(!SchedulesView.canCloseDetail(pane: regular, isNewDraft: false, isDirty: false))
        #expect(!SchedulesView.canCloseDetail(pane: .hidden, isNewDraft: false, isDirty: false))
    }

    @Test("a long project name is shortened in the middle")
    func projectMenuTitle() {
        #expect(SessionsHeader.menuTitle("example-repo") == "example-repo")
        let long = "the-quarterly-cross-team-observability-and-cost-attribution-initiative-0"
        let short = SessionsHeader.menuTitle(long)
        #expect(short.count == 22)
        #expect(short.hasPrefix("the-quarter"))
        #expect(short.hasSuffix("itiative-0"))
        #expect(short.contains("…"))
    }

    // MARK: - Other width rules

    @Test("Control Center moves its rail under the main column only at the minimum window")
    func controlCenterRail() {
        #expect(ControlCenterView.railBelow(pageWidth: nil) == false)
        #expect(ControlCenterView.railBelow(pageWidth: LayoutSize.pageWidth(window: 1100)) == true)
        #expect(ControlCenterView.railBelow(pageWidth: LayoutSize.pageWidth(window: 1280)) == false)
        #expect(ControlCenterView.railBelow(pageWidth: LayoutSize.pageWidth(window: 1600)) == false)
    }

    @Test("the KB Links/Graph panel narrows so the editor keeps its minimum")
    func knowledgeBaseRightPanel() {
        typealias KB = KnowledgeBaseView
        // Unknown width: the stored width, clamped to the drag range.
        #expect(KB.rightPanelWidth(stored: 300, pageWidth: nil) == 300)
        #expect(KB.rightPanelWidth(stored: 9000, pageWidth: nil) == KB.rightPanelMax)
        for window in LayoutSize.windowWidths {
            let page = LayoutSize.pageWidth(window: window)
            let panel = KB.rightPanelWidth(stored: Double(KB.rightPanelMax), pageWidth: page)
            let editor = page - KB.treeWidth - 1 - 8 - panel
            #expect(panel >= KB.rightPanelMin)
            #expect(editor >= KB.minEditorWidth - 0.5 || panel == KB.rightPanelMin,
                    "editor gets \(editor) pt at \(window) pt")
        }
    }

    @Test("a progress-log body collapses; a short one doesn't")
    func longBodies() {
        #expect(!PerFileItemCardView.isLongBody([.prose("Group digest lines by source.")]))
        let log = (1...18).map { "- 2026-06-\($0): \(BigVault.longTitle($0))." }.joined(separator: "\n")
        #expect(PerFileItemCardView.isLongBody([.prose("Context."), .prose(log)]))
        #expect(PerFileItemCardView.isLongBody([.prose(String(repeating: "word ", count: 200))]))
    }

    @Test("the adaptive control names the chosen option")
    func adaptiveSegmentLabel() {
        let options: [(label: String, value: RunDetailTab)] = RunDetailTab.allCases.map { ($0.displayName, $0) }
        #expect(AdaptiveSegmentedControl<RunDetailTab>.currentLabel(.feedback, in: options) == "Feedback")
    }

    @Test("the run detail's tabs fit the 460 pt side panel")
    func runDetailTabsFit() {
        var tab = RunDetailTab.summary
        let control = AdaptiveSegmentedControl(
            selection: Binding(get: { tab }, set: { tab = $0 }),
            options: RunDetailTab.allCases.map { ($0.displayName, $0) },
            minSegmentWidth: 70, accessibilityTitle: "Run detail tab")
        // The side panel is 460 pt; RunDetailView pads 16 on each side.
        #expect(LayoutHarness.minimumWidth(of: control, height: 60) <= 460 - 32)
    }

    // MARK: - Every page

    @Test("every page renders at 1100, 1280 and 1600 pt and the minimum height", arguments: SidebarItem.allCases)
    func pageRendersAtEverySize(_ item: SidebarItem) async throws {
        let vault = try await Self.loadedVault(); defer { vault.tearDown() }
        for width in LayoutSize.windowWidths {
            for height in [LayoutSize.minimumWindowHeight, 900] {
                let size = CGSize(width: width, height: height)
                ViewHost.render(Self.window(item, in: vault), size: size)
                LayoutHarness.snapshot(Self.window(item, in: vault), size: size, name: "window-\(item)")
            }
        }
    }
}

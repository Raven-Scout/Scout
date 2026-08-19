import Foundation
import SwiftUI

struct MainWindowView: View {
    // Test-harness override (issue #83 repro): boot straight into a tab so
    // hang trials are scriptable without UI automation. Inert when unset.
    @State private var selection: SidebarItem =
        ProcessInfo.processInfo.environment["SCOUT_TEST_BOOT_TAB"] == "actionItems"
            ? .actionItems : .controlCenter
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var proposalsService: ProposalsDocumentService

    var body: some View {
        // The NavigationSplitView must be the root view of the window — not
        // wrapped in a VStack. On macOS 26, embedding it in an intermediate
        // container makes the root NSHostingView absorb the theme frame's
        // safe-area corner insets directly; toggling the sidebar then fires a
        // KVO-driven `invalidateSafeAreaCornerInsets()` →
        // `setNeedsUpdateConstraints:` mid-layout, which AppKit asserts on
        // (issue #9). The status bar is delivered as a bottom safe-area inset
        // instead, which keeps the split view on the native titlebar/sidebar
        // layout path while rendering the same persistent bottom strip.
        NavigationSplitView {
            SidebarView(selection: $selection,
                        proposalsBadge: proposalsService.pendingCount,
                        wishlistBadge: appState.wishlistDocumentService.activeCount,
                        researchBadge: appState.researchDocumentService.activeCount)
                .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 240)
        } detail: {
            detail
                .background(PaperBackdrop())
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            StatusBarView(viewLabel: selection.statusLabel)
        }
        .onAppear {
            // Test-harness (issue #83 repro): switch to Action Items after a
            // delay, reproducing the sidebar-click transition (the click
            // handler performs the same `selection =` write). Inert when unset.
            if let raw = ProcessInfo.processInfo.environment["SCOUT_TEST_SWITCH_TAB_AFTER"],
               let secs = Double(raw) {
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: UInt64(secs * 1_000_000_000))
                    selection = .actionItems
                    TestHarness.log("tab-switched to actionItems")
                }
            }
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch selection {
        case .controlCenter:
            ControlCenterView()
        case .actionItems:
            ActionItemsView(
                scoutDirectory: appState.scoutDirectory,
                actionItemsDirectory: appState.actionItemsDirectory
            )
            .environmentObject(appState.actionItemsDocumentService)
            .environmentObject(appState.actionItemsWriterBox)
            .environmentObject(appState.actionItemsEnvState)
        case .schedules:
            SchedulesView()
                .environmentObject(appState.scheduleEditService)
        case .proposals:
            ProposalsView()
                .environmentObject(appState.proposalsDocumentService)
                .environmentObject(appState.proposalsWriterBox)
        case .wishlist:
            PerFileListView(config: .wishlist)
                .environmentObject(appState.wishlistDocumentService)
                .environmentObject(appState.perFileWriterBox)
        case .research:
            PerFileListView(config: .research)
                .environmentObject(appState.researchDocumentService)
                .environmentObject(appState.perFileWriterBox)
        case .knowledgeBase:
            KnowledgeBaseView()
                .environmentObject(appState.knowledgeBaseService)
                .environmentObject(appState.knowledgeBaseWriterBox)
        case .settings:
            SettingsView()
        }
    }
}

/// Test-harness (issue #83 repro): marker-file logging, since the unified log
/// is not readable in this environment. Inert unless SCOUT_TEST_LOG is set.
enum TestHarness {
    static func log(_ message: String) {
        guard let path = ProcessInfo.processInfo.environment["SCOUT_TEST_LOG"],
              !path.isEmpty else { return }
        let line = "\(Date()) \(message)\n"
        if let handle = FileHandle(forWritingAtPath: path) {
            defer { try? handle.close() }
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
        } else {
            try? line.write(toFile: path, atomically: true, encoding: .utf8)
        }
    }
}

enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, proposals, wishlist, research, knowledgeBase, settings

    /// Short label shown in the bottom status bar's "view" cell.
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .actionItems:   return "actions"
        case .schedules:     return "schedules"
        case .proposals:     return "proposals"
        case .wishlist:      return "wishlist"
        case .research:      return "research"
        case .knowledgeBase: return "knowledge"
        case .settings:      return "settings"
        }
    }
}

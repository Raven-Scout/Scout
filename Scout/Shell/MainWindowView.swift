import SwiftUI

struct MainWindowView: View {
    @State private var selection: SidebarItem = .controlCenter
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
                        replyDraftsBadge: appState.replyDraftsDocumentService.pendingCount,
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
        case .replyDrafts:
            RepliesView()
                .environmentObject(appState.replyDraftsDocumentService)
                .environmentObject(appState.replyDraftsWriterBox)
        case .wishlist:
            PerFileListView(config: .wishlist)
                .environmentObject(appState.wishlistDocumentService)
                .environmentObject(appState.perFileWriterBox)
        case .research:
            PerFileListView(config: .research)
                .environmentObject(appState.researchDocumentService)
                .environmentObject(appState.perFileWriterBox)
        case .settings:
            SettingsView()
        }
    }
}

enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, proposals, replyDrafts, wishlist, research, settings

    /// Short label shown in the bottom status bar's "view" cell.
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .actionItems:   return "actions"
        case .schedules:     return "schedules"
        case .proposals:     return "proposals"
        case .replyDrafts:   return "replies"
        case .wishlist:      return "wishlist"
        case .research:      return "research"
        case .settings:      return "settings"
        }
    }
}

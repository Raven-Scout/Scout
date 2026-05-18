import SwiftUI

struct MainWindowView: View {
    @State private var selection: SidebarItem = .controlCenter
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(spacing: 0) {
            NavigationSplitView {
                SidebarView(selection: $selection)
            } detail: {
                detail
                    .background(PaperBackdrop())
            }
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
        case .settings:
            SettingsView()
        }
    }
}

enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, settings

    /// Short label shown in the bottom status bar's "view" cell.
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .actionItems:   return "actions"
        case .schedules:     return "schedules"
        case .settings:      return "settings"
        }
    }
}

import SwiftUI

struct MainWindowView: View {
    @State private var selection: SidebarItem = .controlCenter
    @EnvironmentObject var appState: AppState

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: $selection)
        } detail: {
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
}

enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, settings
}

import SwiftUI

@main
struct ScoutApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup("Scout") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(appState.proposalsDocumentService)
                .frame(minWidth: 1100, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }  // suppress File > New Window
        }

        MenuBarExtra {
            MenuBarExtraContent().environmentObject(appState)
        } label: {
            MenuBarIcon(status: appState.menuBarStatus)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView().environmentObject(appState)
        }
    }
}

import SwiftUI
import Combine

@main
struct ScoutApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var appState: AppState
    @StateObject private var updates: UpdateService
    // Read once at launch: the scene graph is built before Settings can
    // change it, and the toggle documents itself as next-launch anyway.
    private let launchMinimized = UserDefaults.standard.bool(forKey: "launchMinimized")

    init() {
        let state = AppState(configuration: .forCurrentProcess())
        _appState = StateObject(wrappedValue: state)
        _updates = StateObject(wrappedValue: UpdateService(
            pluginChecker: PluginUpdateChecker.standard(),
            engineStates: state.engineHealth.$state.eraseToAnyPublisher(),
            makeAppController: { AppUpdater(onEvent: $0) }))
    }

    var body: some Scene {
        // `Window` (single, identified), not `WindowGroup`: the menu-bar
        // panel reopens this scene via `openWindow(id: "main")`, which must
        // front the existing window or recreate a closed one — a title-string
        // hunt through NSApp.windows breaks as soon as a detail view retitles
        // the window.
        Window("Scout", id: "main") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(appState.proposalsDocumentService)
                .environmentObject(updates)
                .frame(minWidth: 1100, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }  // suppress File > New Window
            CommandGroup(after: .appInfo) {        // Scout ▸ Check for Updates… (under About Scout)
                CheckForUpdatesView(updates: updates)
            }
        }
        // "Start in menu bar": suppress the window at launch instead of
        // creating it and hiding it a run-loop later (which raced scene
        // creation and flashed the window when it won).
        .defaultLaunchBehavior(launchMinimized ? .suppressed : .automatic)
        .restorationBehavior(launchMinimized ? .disabled : .automatic)

        MenuBarExtra {
            MenuBarExtraContent()
                .environmentObject(appState)
                .environmentObject(updates)
        } label: {
            MenuBarIcon(status: appState.menuBarStatus)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(updates)
        }
    }
}

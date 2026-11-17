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
        // The test host (this process under `xcodebuild test`) must never
        // touch the real `~/.claude` or make an HTTPS fetch just by
        // instantiating `ScoutApp` — which no test does — but `AppState` is
        // built eagerly here, so an inert checker keeps this `UpdateService`
        // from subscribing a live `PluginUpdateChecker.standard()` to the
        // test host's own `engineHealth.$state` on every test run.
        let pluginChecker: any PluginUpdateChecking = AppState.Configuration.isTestHost
            ? InertPluginChecker()
            : PluginUpdateChecker.standard()
        _updates = StateObject(wrappedValue: UpdateService(
            pluginChecker: pluginChecker,
            engineStates: state.engineHealth.$state.eraseToAnyPublisher(),
            makeAppController: { AppUpdater(onEvent: $0) }))
        // A clicked run notification opens that run (card C3).
        AppDelegate.openRun = { [weak state] id in state?.openRun(id: id) }
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
            CommandGroup(after: .appInfo) {        // app menu ▸ Check for Updates… (under About Scout)
                CheckForUpdatesView(updates: updates)
            }
            CommandGroup(replacing: .help) {       // Help ▸ docs, doctor, logs, diagnostics (audit C11)
                HelpCommands(vault: appState.scoutDirectory)
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
            MenuBarIcon(status: appState.status, updateAvailable: updates.anyUpdateAvailable)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(updates)
        }

        // Help ▸ Run Doctor… and Help ▸ Export Diagnostics… (audit C11).
        Window("Scout Doctor", id: HelpWindow.doctor) {
            DoctorView(health: appState.engineHealth)
        }
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)   // opened from Help only, never at launch
        .restorationBehavior(.disabled)

        Window("Export Diagnostics", id: HelpWindow.diagnostics) {
            DiagnosticsExportView()
                .environmentObject(appState)
        }
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)   // opened from Help only, never at launch
        .restorationBehavior(.disabled)
    }
}

/// Plugin-track checker for the unit-test host only: `ScoutApp` is never
/// constructed by a test, but its `init()` still runs once as this process's
/// own entry point when `xcodebuild test` launches the Debug Scout.app as
/// the test runner's host application — and that init must stay inert.
/// Always `.notApplicable`, touching neither disk nor network.
private nonisolated struct InertPluginChecker: PluginUpdateChecking {
    func check(engine: EngineState) async -> PluginUpdateResult { .notApplicable }
}

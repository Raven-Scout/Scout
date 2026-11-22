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
        let updates = UpdateService(
            pluginChecker: pluginChecker,
            engineStates: state.engineHealth.$state.eraseToAnyPublisher(),
            // Same rule as the plugin checker: the test host posts nothing.
            appUpdateNotifier: AppState.Configuration.isTestHost ? nil : AppUpdateNotifier(),
            makeAppController: { AppUpdater(onEvent: $0) })
        _updates = StateObject(wrappedValue: updates)
        // A clicked run notification opens that run (card C3).
        AppDelegate.openRun = { [weak state] id in state?.openRun(id: id) }
        AppDelegate.retrySlot = { [weak state] slot in state?.retryFromNotification(slotKey: slot) }
        // A clicked "Scout X is ready" brings up Sparkle's alert for it.
        AppDelegate.showAppUpdate = { [weak updates] in
            NSApp.activate()
            updates?.showAppUpdate()
        }
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
                .environment(\.resolvePromptService, appState.resolvePromptService)
                .environmentObject(appState.proposalsDocumentService)
                .environmentObject(updates)
                .frame(minWidth: MainWindowSize.minWidth, minHeight: MainWindowSize.minHeight)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }  // suppress File > New Window
            CommandGroup(after: .appInfo) {        // app menu ▸ Check for Updates… (under About Scout)
                CheckForUpdatesView(updates: updates)
            }
            GoCommands(appState: appState)         // Go ▸ each page, ⌘1–⌘9
            CommandGroup(replacing: .help) {       // Help ▸ docs, doctor, logs, diagnostics (audit C11)
                HelpCommands(vault: appState.scoutDirectory)
            }
            CommandGroup(after: .help) {           // Help ▸ Scout Glossary
                GlossaryMenuItem()
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
                .environment(\.resolvePromptService, appState.resolvePromptService)
                .environmentObject(updates)
        } label: {
            MenuBarIcon(status: appState.status, updateAvailable: updates.anyUpdateAvailable)
        }
        .menuBarExtraStyle(.window)

        // Help ▸ Scout Glossary is its one menu item: `commandsRemoved()`
        // keeps the window out of the Window menu (UI audit R11).
        Window("Scout Glossary", id: GlossaryView.windowID) {
            GlossaryView()
        }
        .windowResizability(.contentSize)
        .defaultLaunchBehavior(.suppressed)
        .commandsRemoved()

        // ⌘, — Scout's only Settings (UI audit #10). Every "Open Settings"
        // button and link opens this window at its tab.
        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(updates)
        }
        .windowResizability(.contentMinSize)

        // Help ▸ Run Doctor… and Help ▸ Export Diagnostics… (audit C11).
        Window("Scout Doctor", id: HelpWindow.doctor) {
            DoctorView(health: appState.engineHealth)
        }
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)   // opened from Help only, never at launch
        .restorationBehavior(.disabled)
        .commandsRemoved()                    // Help's item only, not the Window menu too

        Window("Export Diagnostics", id: HelpWindow.diagnostics) {
            DiagnosticsExportView()
                .environmentObject(appState)
        }
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)   // opened from Help only, never at launch
        .restorationBehavior(.disabled)
        .commandsRemoved()                    // Help's item only, not the Window menu too
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

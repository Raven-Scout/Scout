import SwiftUI

struct MainWindowView: View {
    @State private var selection: SidebarItem
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var updates: UpdateService
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    /// `initialSelection` lets the layout tests render the whole window on
    /// any page; the app always opens on Control Center.
    init(initialSelection: SidebarItem = .controlCenter) {
        _selection = State(initialValue: initialSelection)
    }

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
                        sessionsBadge: appState.sessionsNeedsYouCount,
                        changesBadge: ChangesView.needsYouCount(appState.changesService.state),
                        researchBadge: appState.researchDocumentService.activeCount,
                        settingsBadge: updates.availableCount,
                        settingsAttention: appState.engineHealth.needsAttention,
                        healthAttention: appState.runLedgerService.needsAttention,
                        tabsGated: appState.engineHealth.state.gatesTabs)
                .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 240)
        } detail: {
            Group {
                if Self.showsOnboarding(state: appState.engineHealth.state,
                                        onboardingActive: appState.onboarding != nil) {
                    if let onboarding = appState.onboarding {
                        // Identity per flow, so a new flow's `.task` runs `start()`.
                        OnboardingView(model: onboarding).id(ObjectIdentifier(onboarding))
                    } else {
                        // The gate's model lands one main-queue hop after the
                        // engine state changes.
                        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                } else {
                    detail
                }
            }
            // Every page, onboarding included: a new version is worth
            // seeing wherever you are (Settings ▸ Updates has the detail).
            .safeAreaInset(edge: .top, spacing: 0) {
                AppUpdateBanner(updates: updates)
            }
            .background(PaperBackdrop())
            .environmentObject(appState.vaultUndo)
            .overlay(alignment: .bottomTrailing) {
                WriteFeedbackOverlay(controller: appState.vaultUndo)
            }
            .sheet(isPresented: upgradeSheetPresented) {
                EngineUpgradeSheet(
                    progress: appState.engineUpgradeProgress ?? [:],
                    error: appState.engineUpgradeError,
                    targetVersion: appState.engineRelease?.engine.version ?? "",
                    isRepair: appState.engineUpgradeIsRepair,
                    isRunning: appState.isUpgradingEngine,
                    retry: { Task { await appState.runEngineUpgrade() } },
                    dismiss: { appState.dismissEngineUpgrade() })
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            StatusBarView(viewLabel: selection.statusLabel)
        }
        .onAppear {
            // So a notification click can reopen this window once it's closed (card C3).
            if appState.openMainWindow == nil {
                appState.openMainWindow = { [openWindow] in openWindow(id: "main") }
            }
        }
        .onChange(of: appState.requestedSidebar) { _, requested in
            guard let requested else { return }
            selection = requested
            appState.requestedSidebar = nil
        }
        .onAppear {
            // The Go menu can reopen a closed window with a page already
            // requested; `onChange` doesn't fire for a value set before appear.
            if let requested = appState.requestedSidebar {
                selection = requested
                appState.requestedSidebar = nil
            }
        }
    }

    private var upgradeSheetPresented: Binding<Bool> {
        Binding(get: { appState.engineUpgradeProgress != nil },
                set: { if !$0 { appState.dismissEngineUpgrade() } })
    }

    /// Pure gate decision (spec §5, Ruling 41): the detail pane shows the
    /// onboarding flow whenever the engine can't back the tabs — or while a
    /// flow `AppState` keeps is still finishing. Settings stays reachable as
    /// its own window (⌘,, UI audit #10), which offers the same flow in a
    /// sheet. Static + pure so it can be unit-tested over every `EngineState`
    /// without a view.
    nonisolated static func showsOnboarding(state: EngineState, onboardingActive: Bool) -> Bool {
        state.gatesTabs || onboardingActive
    }

    @ViewBuilder
    private var detail: some View {
        switch selection {
        case .controlCenter:
            ControlCenterView()
        case .health:
            HealthView(openSettings: { HelpActions.showEngineSettings(openSettings) },
                       logsDirectory: appState.scoutDirectory.appendingPathComponent(".scout-logs"))
                .environmentObject(appState.runLedgerService)
                .environmentObject(appState.engineHealth)
        case .sessions:
            SessionsView()
                .environmentObject(appState.sessionIndexService)
        case .actionItems:
            ActionItemsView(
                scoutDirectory: appState.scoutDirectory,
                actionItemsDirectory: appState.actionItemsDirectory
            )
            .environmentObject(appState.actionItemsDocumentService)
            .environmentObject(appState.actionItemsWriterBox)
            .environmentObject(appState.actionItemsEnvState)
            .environment(\.openKBNote, StableAction { [appState] in appState.requestOpenKBNote($0) })
            .environmentObject(appState.displayProfileService)
        case .schedules:
            SchedulesView()
                .environmentObject(appState.scheduleEditService)
                .environmentObject(appState.modelSettingsService)
        case .changes:
            ChangesView(vaultDirectory: appState.scoutDirectory)
                .environmentObject(appState.changesService)
                .environmentObject(appState.changesDecisions)
        case .research:
            PerFileListView(config: .research)
                .environmentObject(appState.researchDocumentService)
                .environmentObject(appState.perFileWriterBox)
        case .knowledgeBase:
            KnowledgeBaseView(pendingNote: $appState.pendingKBNote, privacy: appState.knowledgeBasePrivacy)
                .environmentObject(appState.knowledgeBaseService)
                .environmentObject(appState.knowledgeBaseWriterBox)
        }
    }
}

/// The main window's pages. Settings isn't one: it is the ⌘, window
/// (UI audit #10), which the sidebar's Settings row opens.
/// `nonisolated`: a plain value the pure dimming rule (`SidebarView.isDimmed`)
/// compares off the main actor.
nonisolated enum SidebarItem: Hashable, CaseIterable {
    case controlCenter, health, sessions, actionItems, schedules, changes, research, knowledgeBase

    /// Short label shown in the bottom status bar's "view" cell.
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .health:        return "health"
        case .sessions:      return "sessions"
        case .actionItems:   return "actions"
        case .schedules:     return "schedules"
        case .changes:       return "changes"
        case .research:      return "research"
        case .knowledgeBase: return "knowledge"
        }
    }
}

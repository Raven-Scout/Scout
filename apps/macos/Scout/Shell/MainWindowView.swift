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
                        sessionsBadge: appState.sessionsNeedsYouCount,
                        proposalsBadge: proposalsService.pendingCount,
                        wishlistBadge: appState.wishlistDocumentService.activeCount,
                        researchBadge: appState.researchDocumentService.activeCount,
                        settingsAttention: appState.engineHealth.needsAttention,
                        tabsGated: appState.engineHealth.state.gatesTabs)
                .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 240)
        } detail: {
            Group {
                if Self.showsOnboarding(state: appState.engineHealth.state, selection: selection,
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
            .background(PaperBackdrop())
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
    }

    private var upgradeSheetPresented: Binding<Bool> {
        Binding(get: { appState.engineUpgradeProgress != nil },
                set: { if !$0 { appState.dismissEngineUpgrade() } })
    }

    /// Pure gate decision (spec §5, Ruling 41): the detail pane shows the
    /// onboarding flow whenever the engine can't back the tabs — or while a
    /// flow `AppState` keeps is still finishing — except on Settings, which
    /// stays reachable and offers the same flow in a sheet. Static + pure so
    /// it can be unit-tested over every `EngineState` without a view.
    nonisolated static func showsOnboarding(state: EngineState, selection: SidebarItem, onboardingActive: Bool) -> Bool {
        selection != .settings && (state.gatesTabs || onboardingActive)
    }

    @ViewBuilder
    private var detail: some View {
        switch selection {
        case .controlCenter:
            ControlCenterView()
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

/// `nonisolated`: a plain value the pure gate/dimming rules
/// (`showsOnboarding`, `SidebarView.isDimmed`) compare off the main actor.
nonisolated enum SidebarItem: Hashable, CaseIterable {
    case controlCenter, sessions, actionItems, schedules, proposals, wishlist, research, knowledgeBase, settings

    /// Short label shown in the bottom status bar's "view" cell.
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .sessions:      return "sessions"
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

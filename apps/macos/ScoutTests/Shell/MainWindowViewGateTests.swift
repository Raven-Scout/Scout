import Testing
import Foundation
@testable import Scout

/// Unit coverage for the window gate (spec §5, Ruling 41): pure static
/// helpers so the decision is testable without rendering a view. Exercised
/// over every `EngineState` case crossed with the Settings selection (never
/// gated) and one other tab (gated exactly when onboarding is kept).
@Suite("MainWindowView.showsOnboarding")
struct MainWindowViewGateTests {
    private let install = EngineInstall(
        root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
        scoutctl: URL(fileURLWithPath: "/usr/bin/false"), python: nil, version: "0.10.0", vault: nil)

    private var allStates: [(name: String, state: EngineState)] {
        [
            ("notInstalled", .notInstalled),
            ("managed-bootstrapped", .managed(install, vaultBootstrapped: true)),
            ("managed-not-bootstrapped", .managed(install, vaultBootstrapped: false)),
            ("external", .external(install, .devCheckout)),
            ("broken-with-install", .broken(install, reason: "engine pointer names a missing scoutctl")),
            ("broken-no-install", .broken(nil, reason: "no install found")),
        ]
    }

    @Test("onboarding tracks gatesTabs on a non-Settings tab, and never shows on Settings")
    func gateFollowsGatesTabsExceptOnSettings() {
        for (name, state) in allStates {
            let kept = AppState.keepsOnboarding(state: state, holdsWindow: false)
            #expect(kept == state.gatesTabs, "unexpected keep for \(name)")
            #expect(
                MainWindowView.showsOnboarding(state: state, selection: .controlCenter, onboardingActive: kept) == state.gatesTabs,
                "unexpected gate for \(name) on .controlCenter"
            )
            #expect(
                MainWindowView.showsOnboarding(state: state, selection: .settings, onboardingActive: kept) == false,
                "gate must never show on .settings for \(name)"
            )
        }
    }

    /// A flow that is finishing (vault just created, or Ready showing) holds
    /// the window even once the engine no longer gates the tabs, so the user
    /// sees it through to "Open Scout".
    @Test func aFinishingFlowHoldsTheWindowAfterTheGateLifts() {
        for (name, state) in allStates {
            #expect(AppState.keepsOnboarding(state: state, holdsWindow: true), "a finishing flow must be kept for \(name)")
            #expect(MainWindowView.showsOnboarding(state: state, selection: .controlCenter, onboardingActive: true), "\(name)")
            #expect(!MainWindowView.showsOnboarding(state: state, selection: .settings, onboardingActive: true), "\(name)")
        }
    }
}

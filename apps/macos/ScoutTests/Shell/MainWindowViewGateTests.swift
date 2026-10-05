import Testing
import Foundation
@testable import Scout

/// Unit coverage for `MainWindowView.showsEngineGate` (spec §5, Ruling 32
/// item 1): a pure static helper so the gate decision is testable without
/// rendering a view. Exercised over every `EngineState` case crossed with
/// the Settings selection (must never gate) and one other tab (must follow
/// `gatesTabs`).
@Suite("MainWindowView.showsEngineGate")
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

    @Test("gate tracks gatesTabs on a non-Settings tab, and never shows on Settings")
    func gateFollowsGatesTabsExceptOnSettings() {
        for (name, state) in allStates {
            #expect(
                MainWindowView.showsEngineGate(state: state, selection: .controlCenter) == state.gatesTabs,
                "unexpected gate for \(name) on .controlCenter"
            )
            #expect(
                MainWindowView.showsEngineGate(state: state, selection: .settings) == false,
                "gate must never show on .settings for \(name)"
            )
        }
    }
}

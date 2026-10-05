import SwiftUI
import Testing
@testable import Scout

private let engineUnavailableInstall = EngineInstall(
    root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
    scoutctl: URL(fileURLWithPath: "/usr/bin/false"), python: nil, version: "0.10.0", vault: nil)

/// Smoke coverage for `EngineUnavailableView` (spec §5, Ruling 32 item 2):
/// renders once per `EngineState` that can reach the detail pane's gate,
/// including both `.managed` branches — bootstrapped and not — since they
/// pick different explanation text.
@MainActor
@Suite("EngineUnavailableView — smoke", .serialized)
struct EngineUnavailableViewSmokeTests {
    @Test("renders without trapping for each engine state", arguments: [
        EngineState.notInstalled,
        .broken(engineUnavailableInstall, reason: "engine pointer names a missing scoutctl: /s"),
        .managed(engineUnavailableInstall, vaultBootstrapped: false),
        .managed(engineUnavailableInstall, vaultBootstrapped: true),
        .external(engineUnavailableInstall, .devCheckout),
    ])
    func renders(state: EngineState) {
        ViewHost.render(
            EngineUnavailableView(state: state, openSettings: {})
                .frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    /// Part B ships no Install/Repair buttons: the gate's copy points at the
    /// copyable next step Settings ▸ Engine shows, and promises nothing more.
    @Test func explanationPointsAtSettingsWithoutPromisingButtons() {
        #expect(EngineUnavailableView.explanation(for: .notInstalled)
                == "No Scout engine was found on this Mac. Settings ▸ Engine shows how to install it.")
        #expect(EngineUnavailableView.explanation(for: .broken(engineUnavailableInstall, reason: "engine pointer names a missing scoutctl: /s"))
                == "The engine that was here is broken: engine pointer names a missing scoutctl: /s. Settings ▸ Engine shows how to repair it.")
        #expect(EngineUnavailableView.explanation(for: .managed(engineUnavailableInstall, vaultBootstrapped: false))
                == "The engine is installed but your vault has not been set up yet. Settings ▸ Engine shows how.")
        #expect(EngineUnavailableView.explanation(for: .managed(engineUnavailableInstall, vaultBootstrapped: true)) == "The engine is present.")
        #expect(EngineUnavailableView.explanation(for: .external(engineUnavailableInstall, .devCheckout)) == "The engine is present.")
    }
}

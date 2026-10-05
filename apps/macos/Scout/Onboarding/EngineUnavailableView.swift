import SwiftUI

/// Shown in the detail pane whenever the engine cannot back the tabs
/// (spec §5). Part C swaps this for the full onboarding flow.
struct EngineUnavailableView: View {
    let state: EngineState
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Scout needs its engine").font(DS.serif(24, weight: .medium)).foregroundStyle(DS.Ink.p1)
            Text(Self.explanation(for: state)).font(DS.sans(13)).foregroundStyle(DS.Ink.p2).fixedSize(horizontal: false, vertical: true)
            Button("Open Settings ▸ Engine") { openSettings() }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium))
        }
        .frame(maxWidth: 520, alignment: .leading)
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    /// Part B has no Install/Repair buttons, so the copy points at the
    /// copyable next step Settings ▸ Engine shows for each state.
    nonisolated static func explanation(for state: EngineState) -> String {
        switch state {
        case .notInstalled: return "No Scout engine was found on this Mac. Settings ▸ Engine shows how to install it."
        case .broken(_, let reason): return "The engine that was here is broken: \(reason). Settings ▸ Engine shows how to repair it."
        case .managed(_, false): return "The engine is installed but your vault has not been set up yet. Settings ▸ Engine shows how."
        case .managed, .external: return "The engine is present."
        }
    }
}

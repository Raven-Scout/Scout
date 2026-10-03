import SwiftUI

/// Shown in the detail pane whenever the engine cannot back the tabs
/// (spec §5). Part C swaps this for the full onboarding flow.
struct EngineUnavailableView: View {
    let state: EngineState
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Scout needs its engine").font(DS.serif(24, weight: .medium)).foregroundStyle(DS.Ink.p1)
            Text(explanation).font(DS.sans(13)).foregroundStyle(DS.Ink.p2).fixedSize(horizontal: false, vertical: true)
            Button("Open Settings ▸ Engine") { openSettings() }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium))
        }
        .frame(maxWidth: 520, alignment: .leading)
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var explanation: String {
        switch state {
        case .notInstalled: return "No Scout engine was found on this Mac. Install it from Settings ▸ Engine to start collecting briefings."
        case .broken(_, let reason): return "The engine that was here is broken: \(reason)"
        case .managed(_, false): return "The engine is installed but your vault has not been set up yet."
        case .managed, .external: return "The engine is present."
        }
    }
}

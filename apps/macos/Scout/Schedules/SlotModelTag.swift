import SwiftUI

/// Small tag naming a slot's own model ("Sonnet"), shown in the Schedules list
/// and cards only when the slot overrides the default — slots that inherit
/// stay as they always looked.
struct SlotModelTag: View {
    let model: String

    var body: some View {
        Text(ModelChoice.displayName(for: model))
            .dsTag()
            .help("Runs on \(model). Schedules without a tag use the default model (Settings → Model).")
    }
}

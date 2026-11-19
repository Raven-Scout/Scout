import SwiftUI

/// Small tag naming a slot's own model ("Sonnet"), shown in the Schedules list
/// and cards only when the slot overrides the default — slots that inherit
/// stay as they always looked.
struct SlotModelTag: View {
    let model: String

    var body: some View {
        Text(ModelChoice.displayName(for: model))
            .font(DS.font(.caption2, weight: .medium))
            .foregroundStyle(DS.Ink.p2)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .background(
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(DS.Rule.soft, lineWidth: 0.5)
            )
            .help("Runs on \(model). Schedules without a tag use the default model (Settings → Model).")
    }
}

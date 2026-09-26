import SwiftUI

/// Compact slot-type indicator: 6pt filled circle in the slot's type color
/// + capitalized type name. Used in table rows, cards, filter chips, and
/// the detail-pane header.
struct TypePill: View {
    let type: SlotType

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(DS.SlotType.color(for: type))
                .frame(width: 6, height: 6)
            Text(type.rawValue.capitalized)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
        }
    }
}

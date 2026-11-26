import SwiftUI

/// Compact slot-type indicator: 6pt filled circle in the slot's type color
/// + capitalized type name. Used in table rows, cards, filter chips, and
/// the detail-pane header.
///
/// One line, at its own width: it never breaks a word ("Consolidatio / n" at
/// Largest text in a narrow card, UI audit T4). A row too narrow for it
/// moves it, as `SlotCard`'s top row does.
struct TypePill: View {
    let type: SlotType

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(DS.SlotType.color(for: type))
                .frame(width: 6, height: 6)
            Text(type.displayName)
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p2)
                .lineLimit(1)
        }
        .fixedSize()
    }
}

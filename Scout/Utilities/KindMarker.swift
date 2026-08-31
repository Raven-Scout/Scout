import SwiftUI

/// Kind marker for section headers, board columns, and filter chips —
/// replacing the emoji "color dots". Priority kinds (urgent/to-do/watching/
/// neutral) render as a soft tinted ring around a solid colored dot; category
/// kinds (focus/meetings/digest/personal/done) render as a bare, full-size SF
/// Symbol in the kind's hue. The ring therefore reads specifically as a
/// priority dot, and the category glyphs stay legible at small sizes.
struct KindMarker: View {
    let kind: ActionSection.Kind
    var size: CGFloat = 14

    var body: some View {
        let hue = DS.priorityColor(kind)
        if let symbol = DS.kindSymbol(kind) {
            Image(systemName: symbol)
                .font(.system(size: size, weight: .medium))
                .foregroundStyle(hue)
                .frame(width: size, height: size)
        } else {
            ZStack {
                Circle()
                    .strokeBorder(hue.opacity(0.4), lineWidth: 1.5)
                Circle()
                    .fill(hue)
                    .frame(width: size * 0.42, height: size * 0.42)
            }
            .frame(width: size, height: size)
        }
    }
}

import SwiftUI

/// Concentric status marker: a soft tinted ring wrapping either a solid
/// colored dot (priority kinds) or a small SF Symbol (category kinds).
/// Replaces the emoji "color dots" that used to render in section headers,
/// board columns, and filter chips.
struct KindMarker: View {
    let kind: ActionSection.Kind
    var size: CGFloat = 14

    var body: some View {
        let hue = DS.priorityColor(kind)
        ZStack {
            Circle()
                .strokeBorder(hue.opacity(0.4), lineWidth: 1.5)
            if let symbol = DS.kindSymbol(kind) {
                Image(systemName: symbol)
                    .font(.system(size: size * 0.5, weight: .medium))
                    .foregroundStyle(hue)
            } else {
                Circle()
                    .fill(hue)
                    .frame(width: size * 0.42, height: size * 0.42)
            }
        }
        .frame(width: size, height: size)
    }
}

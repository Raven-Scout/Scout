import SwiftUI

/// A fixed-height row that toggles `expanded` on click, with an Ask Claude
/// button on hover. Not a `Button`, so the links, chips and fields inside the
/// row keep their own clicks.
struct DisclosureRow<Content: View>: View {
    let height: CGFloat
    let label: String
    @Binding var expanded: Bool
    let onAsk: () -> Void
    /// Gets whether the row is hovered, for controls that show on hover.
    @ViewBuilder let content: (_ hovered: Bool) -> Content

    static var askButtonWidth: CGFloat { 30 }

    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Image(systemName: "chevron.right")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(DS.Ink.p3)
                .rotationEffect(.degrees(expanded ? 90 : 0))
                .frame(width: 12, height: 20)
            content(hovering)
                .padding(.trailing, Self.askButtonWidth)
        }
        .padding(.leading, 7)
        .padding(.trailing, 14)
        .padding(.top, 8)
        .frame(height: height, alignment: .top)
        .contentShape(Rectangle())
        .onTapGesture { expanded.toggle() }
        .background(hovering ? DS.Paper.sunk.opacity(0.5) : .clear)
        .overlay(alignment: .topTrailing) {
            if hovering {
                Button(action: onAsk) {
                    Image(systemName: "bubble.left")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Ink.p3)
                        .frame(width: 26, height: 24)
                        .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.raised))
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 1))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.top, 6)
                .padding(.trailing, 10)
                .help("Ask Claude about this")
                .accessibilityLabel("Ask Claude about \(label)")
            }
        }
        .onHover { hovering = $0 }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint(expanded ? "Collapse" : "Expand")
        .accessibilityAction { expanded.toggle() }
        .accessibilityAction(named: "Ask Claude", onAsk)
    }
}

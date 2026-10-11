import SwiftUI

/// A fixed-height row that toggles `expanded` on click, with an Ask Claude
/// button on hover or keyboard focus. Not a `Button`, so the links, chips and
/// fields inside the row keep their own clicks. Keyboard: Tab to the chevron,
/// Space expands, Return asks Claude. The chevron takes focus, not the row: a
/// focusable row holding buttons ends the Tab chain, and its key handlers
/// would take keys from fields inside it. VoiceOver gets both as named actions.
struct DisclosureRow<Content: View>: View {
    let height: CGFloat
    let label: String
    @Binding var expanded: Bool
    let onAsk: () -> Void
    /// Gets whether the row is hovered, for controls that show on hover.
    @ViewBuilder let content: (_ hovered: Bool) -> Content

    static var askButtonWidth: CGFloat { 30 }

    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Image(systemName: "chevron.right")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(DS.Ink.p3)
                .rotationEffect(.degrees(expanded ? 90 : 0))
                .frame(width: 12, height: 20)
                .focusable()
                .focused($focused)
                .focusEffectDisabled()
                .onKeyPress(.space) {
                    expanded.toggle()
                    return .handled
                }
                .onKeyPress(.return) {
                    onAsk()
                    return .handled
                }
            content(hovering)
                .padding(.trailing, Self.askButtonWidth)
        }
        .padding(.leading, 7)
        .padding(.trailing, 14)
        .padding(.top, 8)
        .frame(height: height, alignment: .top)
        .contentShape(Rectangle())
        .onTapGesture { expanded.toggle() }
        .background(hovering || focused ? DS.Paper.sunk.opacity(0.5) : .clear)
        .overlay(alignment: .leading) {
            if focused {
                Rectangle().fill(DS.Accent.fill).frame(width: 2)
            }
        }
        .overlay(alignment: .topTrailing) {
            if hovering || focused {
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
        .accessibilityValue(expanded ? "Expanded" : "Collapsed")
        .accessibilityHint("Space expands or collapses; Return asks Claude")
        .accessibilityAction(named: expanded ? "Collapse" : "Expand") { expanded.toggle() }
        .accessibilityAction(named: "Ask Claude", onAsk)
    }
}

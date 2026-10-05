import SwiftUI

/// Legacy surface + accent bridge.
///
/// The editorial redesign moved palette ownership into `DS` (DesignSystem).
/// `CardStyle` is kept so older call sites keep compiling, and now simply
/// routes through the new tokens.
enum CardStyle {
    static let cornerRadius: CGFloat = 8
    static let chipCornerRadius: CGFloat = 5

    /// Section heading typography — serif set at uppercase sans was the
    /// editorial direction; this single constant is referenced by older code
    /// and is left sans + semibold to stay safe for callers that don't want
    /// the full header restyle.
    static let sectionHeadingFont = Font.system(size: 13, weight: .semibold)

    /// Priority hue for a section kind — now sourced from `DS`.
    static func accent(_ kind: ActionSection.Kind) -> Color {
        DS.priorityColor(kind)
    }
}

/// Retained for backward-compat with existing call sites (e.g. `TaskLinksView`,
/// `TaskActionsView`). Now renders the editorial chip chrome.
struct PillBackground: View {
    var body: some View { EditorialChipBackground() }
}

/// Retained for backward-compat. The new design uses hairline rules between
/// entries rather than wrapping every task in a card — callers that still
/// reach for this modifier get the editorial raised-paper surface.
struct NeumorphicCard: ViewModifier {
    let kind: ActionSection.Kind

    func body(content: Content) -> some View {
        content.editorialCard()
    }
}

extension View {
    func neumorphicCard(kind: ActionSection.Kind) -> some View {
        modifier(NeumorphicCard(kind: kind))
    }
}

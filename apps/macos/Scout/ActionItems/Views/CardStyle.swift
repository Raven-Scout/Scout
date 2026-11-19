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
    static var sectionHeadingFont: Font { DS.font(.body, weight: .semibold) }

    /// Priority hue for a section kind — now sourced from `DS`.
    static func accent(_ kind: ActionSection.Kind) -> Color {
        DS.priorityColor(kind)
    }
}

import SwiftUI

// MARK: - Design-system v2 text field
//
// One look for every text input that isn't a system search or inline field:
// a recessed well with a hairline, the body type at medium weight. The
// component draws its own states, so callers don't add a background,
// padding, font or border:
//
//   TextField("Name", text: $name).dsField()
//   TextField("Daily budget", text: $budget).dsField(error: budgetError != nil)
//
// Focused: the shared keyboard focus ring (`dsFocusRing`), as buttons draw.
// Disabled: the faintest ink on the same well, as disabled buttons look.
// Error: the hairline turns `Status.err`; say what's wrong in text beside
// it, since colour alone doesn't. `ButtonStyleGuardTests`' sibling,
// `FieldStyleGuardTests`, keeps hand-built wells out.

extension DS {
    /// The colours a field draws for its state. Palette swatches, so the text
    /// colours are checked on the well by `ContrastPairsTests`.
    nonisolated struct FieldColors: Sendable {
        let text: Swatch
        let fill: Swatch
        let stroke: Swatch
    }

    nonisolated static func fieldColors(enabled: Bool, error: Bool) -> FieldColors {
        let P = Palette.self
        guard enabled else { return FieldColors(text: P.inkP4, fill: P.paperSunk, stroke: P.ruleSoft) }
        return FieldColors(text: P.inkP1, fill: P.paperSunk, stroke: error ? P.statusErr : P.ruleSoft)
    }

    static let fieldCornerRadius: CGFloat = 6
    static let fieldHeight: CGFloat = 30
}

private struct DSField: ViewModifier {
    var error: Bool
    @Environment(\.isEnabled) private var isEnabled
    @FocusState private var focused: Bool

    func body(content: Content) -> some View {
        let colors = DS.fieldColors(enabled: isEnabled, error: error)
        let shape = RoundedRectangle(cornerRadius: DS.fieldCornerRadius)
        content
            .textFieldStyle(.plain)
            .focused($focused)
            .font(DS.font(.body, weight: .medium))
            .foregroundStyle(colors.text.color)
            .padding(.horizontal, 10)
            .frame(height: DS.fieldHeight)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                shape.fill(colors.fill.color)
                    .overlay(shape.strokeBorder(colors.stroke.color, lineWidth: error ? 1 : 0.5))
            )
            .dsFocusRing(focused, cornerRadius: DS.fieldCornerRadius)
            // A field in a focusable list holds the focus itself (`dsFocusContainer`).
            .preference(key: DSOwnFocusKey.self, value: focused)
    }
}

extension View {
    /// The design system's text field: a recessed well that draws its own
    /// focus, disabled and error states. Apply it to a `TextField`.
    func dsField(error: Bool = false) -> some View {
        modifier(DSField(error: error))
    }
}

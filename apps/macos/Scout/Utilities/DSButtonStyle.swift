import SwiftUI

// MARK: - Design-system v2 buttons (audit #105)
//
// One set of button styles for every action in the app. Pick the role, not
// the colours:
//
//   .buttonStyle(.dsPrimary)      the one action a screen is for (Continue,
//                                 Save, Install). Filled accent. At most one
//                                 per screen or card; give it
//                                 `.keyboardShortcut(.defaultAction)` when
//                                 Return should trigger it.
//   .buttonStyle(.dsSecondary)    every other action beside it (Back, Retry,
//                                 Check now, Copy details). Raised paper with
//                                 a hairline.
//   .buttonStyle(.dsLink)         a small action that reads as text (Change…,
//                                 Re-detect, What's new, Show more).
//   .buttonStyle(.dsDestructive)  an action that discards or removes
//                                 something. Shaped like secondary, red label.
//   .buttonStyle(.dsQuiet)        a low-key action that shouldn't draw the
//                                 eye: Dismiss, Hide, Clear, an icon-and-text
//                                 action in a row. Grey text, no chrome,
//                                 darker on hover. Not accent, so it never
//                                 reads as a link.
//
// Each takes a size, `.ds(.secondary, size: .small)`, for dense rows. The
// style sets the font and colours itself, so callers don't add `.font` or
// `.foregroundStyle`. Disabled and pressed states are drawn for every role;
// keyboard focus is the system ring (see "Focus ring" below). `.plainHit`
// stays for hit areas that aren't actions in this sense: chips, segments,
// rows, disclosure headers, icon buttons.
//
// The colours are palette swatches (`DS.buttonColors`), so
// `ContrastPairsTests` checks every label against its fill, enabled and
// disabled, in light, dark and Increase Contrast. `ButtonStyleGuardTests`
// keeps hand-rolled accent buttons and the system bordered styles out.

extension DS {

    /// What a button is for. See the overview above.
    nonisolated enum ButtonRole: CaseIterable, Sendable {
        case primary, secondary, link, destructive, quiet
    }

    /// Regular for forms and footers, small for dense rows and cards.
    enum ButtonSize: CaseIterable, Sendable {
        case regular, small

        var textStyle: TextStyle { self == .regular ? .label : .caption }
        /// Minimum height of a chromed (non-link) button, in points.
        var height: CGFloat { self == .regular ? 28 : 22 }
        var horizontalPadding: CGFloat { self == .regular ? 14 : 10 }
    }

    /// A button's label, fill and outline for one state. A nil fill means the
    /// button has no chrome (links) and sits on whatever paper is under it.
    nonisolated struct ButtonColors: Sendable {
        let label: Swatch
        let fill: Swatch?
        let stroke: Swatch?
    }

    /// The colours a role draws, enabled or disabled. Disabled buttons all
    /// look the same: a recessed well and the faintest text ink, so "you
    /// can't do this yet" reads one way everywhere.
    nonisolated static func buttonColors(_ role: ButtonRole, enabled: Bool) -> ButtonColors {
        let P = Palette.self
        guard enabled else {
            if role == .link || role == .quiet { return ButtonColors(label: P.inkP4, fill: nil, stroke: nil) }
            return ButtonColors(label: P.inkP4, fill: P.paperSunk, stroke: P.ruleSoft)
        }
        switch role {
        case .primary:     return ButtonColors(label: P.accentOnFill, fill: P.accentFill, stroke: nil)
        case .secondary:   return ButtonColors(label: P.inkP1, fill: P.paperRaised, stroke: P.ruleHard)
        case .link:        return ButtonColors(label: P.accentInk, fill: nil, stroke: nil)
        case .destructive: return ButtonColors(label: P.statusErr, fill: P.paperRaised, stroke: P.ruleHard)
        case .quiet:       return ButtonColors(label: P.inkP3, fill: nil, stroke: nil)
        }
    }

    /// A quiet button's label under the pointer: one step darker, so it
    /// shows it can be clicked without looking like a link.
    nonisolated static let quietHoverLabel = Palette.inkP2

    /// Corner radius of every chromed button.
    static let buttonCornerRadius: CGFloat = 6
}

/// The design system's button style. Use the static shorthands
/// (`.dsPrimary`, `.dsSecondary`, `.dsLink`, `.dsDestructive`, `.ds(_:size:)`).
struct DSButtonStyle: ButtonStyle {
    var role: DS.ButtonRole
    var size: DS.ButtonSize = .regular

    func makeBody(configuration: Configuration) -> some View {
        DSButtonBody(configuration: configuration, role: role, size: size)
    }
}

private struct DSButtonBody: View {
    let configuration: ButtonStyleConfiguration
    let role: DS.ButtonRole
    let size: DS.ButtonSize
    @Environment(\.isEnabled) private var isEnabled
    @State private var isHovering = false

    var body: some View {
        let colors = DS.buttonColors(role, enabled: isEnabled)
        let label = role == .quiet && isEnabled && isHovering ? DS.quietHoverLabel : colors.label
        let shape = RoundedRectangle(cornerRadius: DS.buttonCornerRadius)
        let chromed = colors.fill != nil
        configuration.label
            .font(DS.font(size.textStyle, weight: .medium))
            // Long labels wrap rather than truncate; at a large Text size
            // a cut-off label hides what the button does.
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, chromed ? 4 : 0)
            .foregroundStyle(label.color)
            .padding(.horizontal, chromed ? size.horizontalPadding : 0)
            .frame(minHeight: chromed ? size.height : nil)
            .background {
                if let fill = colors.fill {
                    shape.fill(fill.color)
                }
            }
            .overlay {
                if let stroke = colors.stroke {
                    shape.strokeBorder(stroke.color, lineWidth: 0.5)
                }
            }
            // Pressed: chromed buttons darken their fill a touch; links dim,
            // as `.plain` does.
            .overlay {
                if chromed && configuration.isPressed {
                    shape.fill(Color.black.opacity(0.08)).allowsHitTesting(false)
                }
            }
            .opacity(!chromed && configuration.isPressed ? 0.7 : 1)
            .contentShape(shape)
            .onHover { hovering in if role == .quiet { isHovering = hovering } }
    }
}

extension ButtonStyle where Self == DSButtonStyle {
    /// The one action a screen or card is for. Filled accent.
    static var dsPrimary: DSButtonStyle { DSButtonStyle(role: .primary) }
    /// Any other action beside the primary one.
    static var dsSecondary: DSButtonStyle { DSButtonStyle(role: .secondary) }
    /// A small action that reads as text.
    static var dsLink: DSButtonStyle { DSButtonStyle(role: .link) }
    /// Discards or removes something.
    static var dsDestructive: DSButtonStyle { DSButtonStyle(role: .destructive) }
    /// A low-key action (Dismiss, Hide, Clear): grey text, no chrome.
    static var dsQuiet: DSButtonStyle { DSButtonStyle(role: .quiet) }
    /// Any role at either size.
    static func ds(_ role: DS.ButtonRole, size: DS.ButtonSize = .regular) -> DSButtonStyle {
        DSButtonStyle(role: role, size: size)
    }
}

// MARK: - Focus ring

// What SwiftUI reports, measured with Keyboard navigation on (UI audit T4):
//
// - A button style's `@Environment(\.isFocused)` is never the button's own
//   focus: it is the nearest focusable view *around* the button. So a style
//   that rang `isFocused` rang every button in a list the moment the list
//   had the focus, and never the one Tab was on.
// - A focused button gets the system focus ring, unless a view around it set
//   `.focusEffectDisabled()`.
// - `@FocusState` on a `.focusable()` view stays true while a control inside
//   it has the focus.
//
// So DS styles draw no ring of their own, and a list that takes the arrow
// keys is focusable through a sink behind its content (`dsFocusContainer`),
// never around it.

extension EnvironmentValues {
    /// A focusable container around this view holds the keyboard focus
    /// itself, not a control inside it: its current item draws the ring
    /// (`dsCursorRing`). See `dsFocusContainer(_:showsCursor:)`.
    @Entry var dsFocusContainerShowsCursor: Bool = false
}

extension View {
    /// Makes a list or grid that takes the arrow keys focusable: a Tab stop
    /// of its own, whose current item draws the ring (`dsCursorRing`).
    /// Every focusable list needs one (`FocusContainerGuardTests`).
    ///
    /// The focus sits on a sink behind the content, not on a view around it,
    /// so `focus` is the list's own (false while Tab is on a button inside),
    /// no button inside inherits it, and the buttons keep their system focus
    /// ring. Key handlers on this view also hear keys a focused button inside
    /// didn't use: gate them on `focus` (`keyboardListNavigation(when:)`).
    /// Pass `showsCursor: false` while a field inside has the keys.
    func dsFocusContainer(_ focus: FocusState<Bool>.Binding, showsCursor: Bool = true) -> some View {
        background {
            Color.clear
                .focusable()
                .focused(focus)
                .focusEffectDisabled()
                .accessibilityHidden(true)
        }
        .environment(\.dsFocusContainerShowsCursor, focus.wrappedValue && showsCursor)
    }

    /// The ring on a focusable container's current item (its cursor or
    /// selection) while the container holds the keyboard focus itself.
    func dsCursorRing(_ isCurrent: Bool, cornerRadius: CGFloat = 6) -> some View {
        modifier(DSCursorRing(isCurrent: isCurrent, cornerRadius: cornerRadius))
    }

    /// The keyboard focus ring Scout draws itself: on a `dsField` and on a
    /// focusable container's current item. Buttons get the system's.
    func dsFocusRing(_ isFocused: Bool, cornerRadius: CGFloat = 6) -> some View {
        overlay {
            if isFocused {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .strokeBorder(Color(nsColor: .keyboardFocusIndicatorColor), lineWidth: 2)
                    .padding(-2)
                    .allowsHitTesting(false)
            }
        }
    }
}

private struct DSCursorRing: ViewModifier {
    let isCurrent: Bool
    let cornerRadius: CGFloat
    @Environment(\.dsFocusContainerShowsCursor) private var showsCursor

    func body(content: Content) -> some View {
        content.dsFocusRing(isCurrent && showsCursor, cornerRadius: cornerRadius)
    }
}

import AppKit
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
// `.foregroundStyle`. Disabled, pressed and keyboard-focus states are drawn
// for every role. `.plainHit` stays for hit areas that aren't actions in this
// sense: chips, segments, rows, disclosure headers, icon buttons.
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
    /// The nearest focusable ancestor's focus: the button's own, or its list's (`dsFocusContainer`).
    @Environment(\.isFocused) private var isFocused
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
            .dsControlFocusRing(isFocused, cornerRadius: DS.buttonCornerRadius)
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

extension EnvironmentValues {
    /// A focusable container around this view (a list that takes the arrow
    /// keys) has the keyboard focus, itself or a control inside it.
    /// See `dsFocusContainer(_:)`.
    @Entry var dsFocusContainerHasFocus: Bool = false
    /// That container holds the keyboard focus itself, not a control inside
    /// it: its current item draws the ring (`dsCursorRing`).
    @Entry var dsFocusContainerShowsCursor: Bool = false
}

/// Whether buttons take keyboard focus themselves: on macOS only with
/// Keyboard navigation (Full Keyboard Access) on. Off, a button's
/// `isFocused` is always its container's. `@Observable` and refreshed when
/// the setting changes, so rings redraw at once rather than at the next
/// focus change (T4 review).
@Observable
final class DSKeyboardNavigation {
    static let shared = DSKeyboardNavigation()

    private(set) var isOn: Bool
    @ObservationIgnored private var observer: NSObjectProtocol?

    private init() {
        isOn = Self.current
        observer = DistributedNotificationCenter.default().addObserver(
            forName: Self.didChange, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.isOn = Self.current }
        }
    }

    /// Posted by System Settings when Keyboard navigation turns on or off.
    nonisolated static let didChange = Notification.Name("com.apple.KeyboardUIModeDidChange")

    /// `AppleKeyboardUIMode` bit 2, read fresh: AppKit's
    /// `isFullKeyboardAccessEnabled` may still hold the old value when the
    /// notification arrives.
    private static var current: Bool {
        CFPreferencesAppSynchronize(kCFPreferencesAnyApplication)
        if let mode = CFPreferencesCopyAppValue("AppleKeyboardUIMode" as CFString, kCFPreferencesAnyApplication) as? Int {
            return mode & 2 != 0
        }
        return NSApp?.isFullKeyboardAccessEnabled ?? false
    }
}

/// A DS control inside holds the keyboard focus itself (OR of every control).
struct DSOwnFocusKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}

/// The ring a DS control draws for its own focus (`dsControlFocusRing`).
private struct DSControlFocusRing: ViewModifier {
    let isFocused: Bool
    let cornerRadius: CGFloat
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.dsFocusContainerHasFocus) private var containerHasFocus

    func body(content: Content) -> some View {
        let navigation = DSKeyboardNavigation.shared.isOn
        let ring = DSFocus.controlDrawsRing(isFocused: isFocused, isEnabled: isEnabled,
                                            containerHasFocus: containerHasFocus, keyboardNavigation: navigation)
        content
            .dsFocusRing(ring, cornerRadius: cornerRadius)
            // OR'd in, never set: a control inside this one's label still counts.
            .transformPreference(DSOwnFocusKey.self) { $0 = $0 || (ring && navigation) }
    }
}

/// A control that isn't a DS style reports its own focus (`dsReportsOwnFocus`).
private struct DSReportsOwnFocus: ViewModifier {
    @FocusState private var focused: Bool

    func body(content: Content) -> some View {
        content
            .focused($focused)
            .transformPreference(DSOwnFocusKey.self) { $0 = $0 || focused }
    }
}

/// Marks a focusable container (see `dsFocusContainer(_:)`).
private struct DSFocusContainer: ViewModifier {
    let hasFocus: Bool
    let showsCursor: Bool
    @State private var focusInside = false

    func body(content: Content) -> some View {
        content
            .environment(\.dsFocusContainerHasFocus, hasFocus)
            .environment(\.dsFocusContainerShowsCursor,
                         showsCursor && DSFocus.containerShowsCursor(hasFocus: hasFocus, focusInside: focusInside))
            .onPreferenceChange(DSOwnFocusKey.self) { focusInside = $0 }
    }
}

/// The focus-ring rules, as plain functions the tests pin (UI audit T4).
nonisolated enum DSFocus {
    /// A control rings its own focus. `isFocused` is the nearest focusable
    /// ancestor's: with Keyboard navigation on, a button is focusable, so it
    /// is the button's own; off, a button inside a focused container only
    /// inherits the container's and draws nothing. Disabled controls never
    /// take focus, so theirs is always inherited.
    static func controlDrawsRing(isFocused: Bool, isEnabled: Bool, containerHasFocus: Bool,
                                 keyboardNavigation: Bool) -> Bool {
        isFocused && isEnabled && (keyboardNavigation || !containerHasFocus)
    }

    /// A container's current item rings while the container holds the focus
    /// itself. `@FocusState` on a container is true while a control inside
    /// has the focus too, so that case is subtracted: that control rings.
    static func containerShowsCursor(hasFocus: Bool, focusInside: Bool) -> Bool {
        hasFocus && !focusInside
    }
}

extension View {
    /// Marks a `.focusable()` container: a list or grid that takes the arrow
    /// keys. Every focusable container needs one (`FocusContainerGuardTests`).
    ///
    /// While `hasFocus`, the buttons inside don't draw the ring they'd
    /// inherit from it (without Keyboard navigation, every button in Action
    /// Items drew one after Done handed the keys back, UI audit T4). A
    /// control that holds the focus itself still rings. While the container
    /// holds it itself, its current item draws the ring (`dsCursorRing`), so
    /// Tab never stops on it silently. Pass `showsCursor: false` while a
    /// field inside that isn't a `dsField` has the focus.
    func dsFocusContainer(_ hasFocus: Bool, showsCursor: Bool = true) -> some View {
        modifier(DSFocusContainer(hasFocus: hasFocus, showsCursor: showsCursor))
    }

    /// The ring on a focusable container's current item (its cursor or
    /// selection) while the container holds the keyboard focus itself.
    func dsCursorRing(_ isCurrent: Bool, cornerRadius: CGFloat = 6) -> some View {
        modifier(DSCursorRing(isCurrent: isCurrent, cornerRadius: cornerRadius))
    }

    /// For a native control (a `.borderless` button, a `Menu`) inside a
    /// focusable container: tells the container when it holds the focus, so
    /// the container's current item drops its ring and the list lets go of
    /// the arrow keys. DS styles and `dsField` report their own;
    /// `FocusContainerGuardTests` checks the rest do.
    func dsReportsOwnFocus() -> some View {
        modifier(DSReportsOwnFocus())
    }

    /// A DS control's own focus ring: drawn for its own keyboard focus, never
    /// one inherited from a focusable container (`DSFocus.controlDrawsRing`).
    /// Every DS button and chip style draws focus through this.
    func dsControlFocusRing(_ isFocused: Bool, cornerRadius: CGFloat = 6) -> some View {
        modifier(DSControlFocusRing(isFocused: isFocused, cornerRadius: cornerRadius))
    }

    /// The keyboard focus ring custom-styled controls draw for themselves
    /// (they get no system focus indicator). Shared by `.plainHit` and the DS
    /// button styles so focus looks the same on every control.
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

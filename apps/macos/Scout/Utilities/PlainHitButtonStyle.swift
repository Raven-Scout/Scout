import SwiftUI

/// A drop-in replacement for `.buttonStyle(.plain)` whose entire padded frame
/// is clickable.
///
/// Stock `PlainButtonStyle` only hit-tests the label's opaque content, so a
/// button whose label is `Text(...).padding().background(.clear)` — e.g. an
/// unselected segmented-control segment or filter chip — only responds to
/// clicks landing directly on the text glyphs; the surrounding padding is dead
/// (issue #16). Wrapping the label in `.contentShape(Rectangle())` makes the
/// whole frame hittable. The press-dim matches `.plain`'s feedback so this is a
/// faithful swap.
///
/// It also draws the keyboard focus ring (C7): custom-styled buttons get no
/// system focus indicator, so with Keyboard navigation on, Tab moved focus
/// through the app's chips, segments and toggles invisibly.
///
/// For hit areas only (chips, segments, rows, icon buttons). An action with a
/// label (Continue, Save, Retry, Change…) uses a design-system button style
/// instead: `.dsPrimary`, `.dsSecondary`, `.dsLink` or `.dsDestructive`
/// (see `DSButtonStyle.swift`).
struct PlainHitButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        PlainHitButtonBody(configuration: configuration)
    }
}

private struct PlainHitButtonBody: View {
    let configuration: ButtonStyleConfiguration
    /// The nearest focusable ancestor's focus: the button's own, or its list's (`dsFocusContainer`).
    @Environment(\.isFocused) private var isFocused

    var body: some View {
        configuration.label
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.75 : 1)
            .dsControlFocusRing(isFocused)
    }
}

extension ButtonStyle where Self == PlainHitButtonStyle {
    /// `.plain`, but the padding around the label is clickable too. See `PlainHitButtonStyle`.
    static var plainHit: PlainHitButtonStyle { PlainHitButtonStyle() }
}

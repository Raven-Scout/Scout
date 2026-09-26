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
struct PlainHitButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

extension ButtonStyle where Self == PlainHitButtonStyle {
    /// `.plain`, but the padding around the label is clickable too. See `PlainHitButtonStyle`.
    static var plainHit: PlainHitButtonStyle { PlainHitButtonStyle() }
}

import SwiftUI

extension View {
    /// `.accessibilityLabel(_:)` when there is a label; unchanged otherwise.
    /// (An empty label would *replace* the element's own text with nothing.)
    @ViewBuilder
    func accessibilityLabel(ifPresent label: String?) -> some View {
        if let label, !label.isEmpty {
            accessibilityLabel(label)
        } else {
            self
        }
    }

    /// Marks a selectable element (a chip, a segment, a sidebar row, a
    /// heatmap day) as selected for VoiceOver when `isSelected` is true.
    func accessibilitySelected(_ isSelected: Bool) -> some View {
        accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

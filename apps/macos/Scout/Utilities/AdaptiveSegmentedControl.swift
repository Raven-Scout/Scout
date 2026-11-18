import SwiftUI

/// `EditorialSegmentedControl` where it fits, a menu showing the current
/// choice where it doesn't (UI audit C6). Segments never shrink, so seven
/// run-detail tabs in a 460 pt side panel, or five slot types in the 380 pt
/// slot form, used to run past the panel's edge and clip the last ones.
///
/// Under `.fixedSize()` or with room to spare it is exactly the segmented
/// control; it only changes when the parent offers less than the segments
/// need.
struct AdaptiveSegmentedControl<Value: Hashable>: View {
    @Binding var selection: Value
    let options: [(label: String, value: Value)]
    var minSegmentWidth: CGFloat = 64
    /// Read by VoiceOver and shown as the menu's tooltip.
    var accessibilityTitle: String

    var body: some View {
        ViewThatFits(in: .horizontal) {
            EditorialSegmentedControl(selection: $selection, options: options, minSegmentWidth: minSegmentWidth,
                                      accessibilityLabel: accessibilityTitle)
            menu
        }
    }

    static func currentLabel(_ selection: Value, in options: [(label: String, value: Value)]) -> String {
        options.first { $0.value == selection }?.label ?? ""
    }

    private var menu: some View {
        Menu {
            ForEach(options.indices, id: \.self) { index in
                let option = options[index]
                Toggle(option.label, isOn: Binding(
                    get: { selection == option.value },
                    set: { if $0 { selection = option.value } }
                ))
            }
        } label: {
            Text(Self.currentLabel(selection, in: options))
                .font(DS.sans(13, weight: .medium))
                .lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: 7)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
        .help(accessibilityTitle)
        .accessibilityLabel(accessibilityTitle)
        .accessibilityValue(Self.currentLabel(selection, in: options))
    }
}

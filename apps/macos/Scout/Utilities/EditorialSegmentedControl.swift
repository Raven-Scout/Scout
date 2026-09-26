import SwiftUI

/// Editorial replacement for SwiftUI's `.pickerStyle(.segmented)` — paper-raised
/// pill row with a dark-ink active segment. Matches the warm palette used
/// across the app and avoids the iOS-blue tint that stock SwiftUI pulls in.
struct EditorialSegmentedControl<Value: Hashable>: View {
    @Binding var selection: Value
    let options: [(label: String, value: Value)]
    var minSegmentWidth: CGFloat = 64

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options.indices, id: \.self) { idx in
                let option = options[idx]
                Button { selection = option.value } label: {
                    Text(option.label)
                        .font(DS.sans(13, weight: .medium))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 5)
                        .frame(minWidth: minSegmentWidth)
                        .background(
                            RoundedRectangle(cornerRadius: 5)
                                .fill(selection == option.value ? DS.Ink.p1 : .clear)
                        )
                        .foregroundStyle(selection == option.value ? DS.Paper.base : DS.Ink.p2)
                }
                .buttonStyle(.plainHit)
            }
        }
        .padding(2)
        .background(
            RoundedRectangle(cornerRadius: 7)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }
}

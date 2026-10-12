import SwiftUI

/// Parked work, one collapsed group after the topics.
struct HoldGroupView: View {
    let steps: [BoardStep]
    let scoutDirectory: URL
    @Binding var expanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                expanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                    Text("\(BoardState.hold.label) · \(steps.count)")
                }
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                ForEach(steps) { step in
                    Divider().overlay(DS.Rule.soft)
                    StepRowView(step: step, topic: nil, scoutDirectory: scoutDirectory)
                }
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft, lineWidth: 1))
    }
}

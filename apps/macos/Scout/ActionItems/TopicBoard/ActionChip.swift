import SwiftUI

/// The small uppercase "what you do next" chip on a Mine step, e.g. MERGE.
/// Clicking it opens the step's prompt box.
struct ActionChip: View {
    let label: String
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Text(label.uppercased())
                .font(DS.sans(10.5, weight: .semibold))
                .tracking(0.4)
                .foregroundStyle(DS.Paper.base)
                .padding(.horizontal, 5)
                .background(RoundedRectangle(cornerRadius: 4).fill(BoardState.mine.color))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Tell Claude what to do; the note goes to the item's session")
    }
}

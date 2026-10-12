import SwiftUI

/// Open steps per state on a topic row, e.g. ● 2  ● 1  ○ 3.
struct TopicCountsView: View {
    let counts: [BoardState: Int]

    var body: some View {
        HStack(spacing: 10) {
            ForEach(BoardState.filterable, id: \.self) { state in
                if let n = counts[state], n > 0 {
                    HStack(spacing: 5) {
                        StateDot(state: state)
                        Text("\(n)").monospacedDigit()
                    }
                    .help("\(n) \(state.label.lowercased())")
                }
            }
        }
        .font(DS.sans(12.5))
        .foregroundStyle(DS.Ink.p2)
        .fixedSize()
    }
}

import SwiftUI

/// The state legend, which doubles as the board filter: click states to show
/// only those, click All to clear.
struct BoardLegendView: View {
    @Binding var filterRaw: String

    private var filter: Set<BoardState> { BoardFilterStorage.decode(filterRaw) }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(BoardState.filterable, id: \.self) { state in
                let pressed = filter.contains(state)
                Button {
                    filterRaw = BoardFilterStorage.toggling(state, in: filterRaw)
                } label: {
                    HStack(spacing: 6) {
                        StateDot(state: state)
                        Text(state.label)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .foregroundStyle(pressed ? DS.Ink.p1 : DS.Ink.p3)
                    .background(Capsule().fill(pressed ? DS.Paper.raised : .clear))
                    .overlay(Capsule().strokeBorder(pressed ? DS.Rule.hard : .clear, lineWidth: 1))
                    .opacity(filter.isEmpty || pressed ? 1 : 0.55)
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .help(state.help)
                .accessibilityAddTraits(pressed ? .isSelected : [])
            }
            if !filter.isEmpty {
                Button {
                    filterRaw = ""
                } label: {
                    Text("All")
                        .underline(pattern: .dot)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(DS.Ink.p3)
                .help("Show every state")
            }
        }
        .font(DS.sans(12))
    }
}

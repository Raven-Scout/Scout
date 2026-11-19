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
                }
                .buttonStyle(.dsChip(selected: pressed))
                .help(state.help)
            }
            if !filter.isEmpty {
                Button("All") {
                    filterRaw = ""
                }
                .buttonStyle(.ds(.quiet, size: .small))
                .help("Show every state")
            }
        }
    }
}

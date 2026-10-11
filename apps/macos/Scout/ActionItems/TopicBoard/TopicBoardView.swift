import SwiftUI

/// The Action Items topic board: workstreams as collapsed rows, the steps that
/// need you or wait on others inside, blocked steps nested under their blocker,
/// then what got done.
struct TopicBoardView: View {
    static let holdKey = "__hold"

    let sections: [ActionSection]
    let fileDate: Date
    let actionItemsDirectory: URL
    let scoutDirectory: URL
    var search: String = ""

    @State private var topics: [TopicMeta] = []
    @AppStorage("topicBoard.open") private var openRaw: String = ""
    @AppStorage("topicBoard.filter") private var filterRaw: String = ""
    @AppStorage("topicBoard.doneRange") private var doneRange: DoneRange = .week

    private var filter: Set<BoardState> { BoardFilterStorage.decode(filterRaw) }

    private var board: TopicBoard {
        TopicBoard.build(sections: sections, topics: topics, filter: filter, search: search, today: .now)
    }

    var body: some View {
        let board = board
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                BoardLegendView(filterRaw: $filterRaw)
                    .padding(.bottom, 4)
                if board.topics.isEmpty && board.onHold.isEmpty {
                    Text("Nothing on the board")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                        .padding(14)
                }
                ForEach(board.topics) { topic in
                    TopicSectionView(topic: topic, scoutDirectory: scoutDirectory, expanded: isOpen(topic.key))
                }
                if !board.onHold.isEmpty {
                    HoldGroupView(steps: board.onHold, scoutDirectory: scoutDirectory, expanded: isOpen(Self.holdKey))
                }
                BoardSideListsView(
                    done: DoneEntry.entries(sections: sections, fileDate: fileDate, range: doneRange, today: .now),
                    doneRange: $doneRange
                )
                .padding(.top, 10)
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .task(id: sections) { topics = TopicsFileLoader.load(from: actionItemsDirectory) }
    }

    private func isOpen(_ key: String) -> Binding<Bool> {
        Binding(
            get: { !filter.isEmpty || BoardExpansion.contains(key, in: openRaw) },
            set: { openRaw = BoardExpansion.setting(key, open: $0, in: openRaw) }
        )
    }
}

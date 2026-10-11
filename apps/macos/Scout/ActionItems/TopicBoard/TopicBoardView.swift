import SwiftUI

/// The Action Items topic board: workstreams as collapsed rows, the steps that
/// need you or wait on others inside, blocked steps nested under their blocker,
/// then Inbox, Loose ends and Done.
struct TopicBoardView: View {
    static let holdKey = "__hold"

    let sections: [ActionSection]
    let fileDate: Date
    let actionItemsDirectory: URL
    let scoutDirectory: URL
    var search: String = ""

    @EnvironmentObject private var writerBox: ActionItemsWriterBox
    @State private var topics: [TopicMeta] = []
    @State private var dismissed: Set<String> = []
    @State private var canWrite = false
    @State private var writeError: String?
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
                if let writeError {
                    Text(writeError)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Status.err)
                }
                if board.topics.isEmpty && board.onHold.isEmpty {
                    Text("Nothing on the board")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                        .padding(14)
                }
                ForEach(board.topics) { topic in
                    TopicSectionView(
                        topic: topic,
                        scoutDirectory: scoutDirectory,
                        expanded: isOpen(topic.key),
                        onRename: renameAction(for: topic)
                    )
                }
                if !board.onHold.isEmpty {
                    HoldGroupView(steps: board.onHold, scoutDirectory: scoutDirectory, expanded: isOpen(Self.holdKey))
                }
                BoardSideListsView(
                    inbox: SideEntry.entries(inSectionTitled: "Inbox", of: sections, dismissed: dismissed),
                    looseEnds: SideEntry.entries(inSectionTitled: "Loose ends", of: sections, dismissed: dismissed),
                    done: DoneEntry.entries(sections: sections, fileDate: fileDate, range: doneRange, today: .now),
                    doneRange: $doneRange,
                    onDismiss: dismissAction
                )
                .padding(.top, 10)
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .task(id: sections) { reloadBoardFiles() }
        .task { canWrite = await writerBox.boardWriter.supportsBoardWrites() }
    }

    private func isOpen(_ key: String) -> Binding<Bool> {
        Binding(
            get: { !filter.isEmpty || BoardExpansion.contains(key, in: openRaw) },
            set: { openRaw = BoardExpansion.setting(key, open: $0, in: openRaw) }
        )
    }

    private func renameAction(for topic: BoardTopic) -> ((String) -> Void)? {
        guard canWrite else { return nil }
        return { write(.renameTopic(tag: topic.key, name: $0)) }
    }

    private var dismissAction: ((SideEntry) -> Void)? {
        canWrite ? { dismiss($0) } : nil
    }

    private func dismiss(_ entry: SideEntry) {
        dismissed.insert(entry.key)
        write(.dismiss(key: entry.key))
    }

    private func write(_ op: BoardWriteOp) {
        writeError = nil
        Task {
            do {
                try await writerBox.boardWriter.submit(op)
            } catch {
                writeError = Self.message(for: op)
            }
            reloadBoardFiles()
        }
    }

    private func reloadBoardFiles() {
        topics = TopicsFileLoader.load(from: actionItemsDirectory)
        dismissed = DismissedFileLoader.load(from: actionItemsDirectory)
    }

    private static func message(for op: BoardWriteOp) -> String {
        switch op {
        case .renameTopic: "Couldn't save the new name. Try again."
        case .dismiss: "Couldn't close that entry. Try again."
        }
    }
}

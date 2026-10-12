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
    @State private var doneDates: [String: Date] = [:]
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
                } else if !board.hasTopicGroups {
                    Text(Self.noTopicsHint)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p3)
                }
                ForEach(board.topics) { topic in
                    if topic.isStandalone, let step = topic.steps.first {
                        StepRowView(step: step, topic: nil, scoutDirectory: scoutDirectory)
                            .boardCard()
                    } else {
                        TopicSectionView(
                            topic: topic,
                            scoutDirectory: scoutDirectory,
                            expanded: isOpen(topic.key),
                            onRename: renameAction(for: topic)
                        )
                    }
                }
                if !board.onHold.isEmpty {
                    HoldGroupView(steps: board.onHold, scoutDirectory: scoutDirectory, expanded: isOpen(Self.holdKey))
                }
                BoardSideListsView(
                    inbox: sideEntries("Inbox"),
                    looseEnds: sideEntries("Loose ends"),
                    done: DoneEntry.entries(sections: sections, doneDates: doneDates, range: doneRange, today: .now),
                    doneRange: $doneRange,
                    onDismiss: dismissAction
                )
                .padding(.top, 10)
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .task(id: sections) {
            writeError = nil
            reloadBoardFiles()
            pruneOpenRows()
            let directory = actionItemsDirectory
            let day = fileDate
            doneDates = await Task.detached { DoneHistory.load(from: directory, upTo: day) }.value
            canWrite = await writerBox.boardWriter.supportsBoardWrites()
        }
    }

    static let noTopicsHint = "No topics yet, so each task stands on its own. Scout's runs group tasks into topics as they work out which workstream each belongs to."

    /// A side list's open entries, or `nil` when the daily file has no such
    /// section, so the board doesn't show an empty column runs never fill.
    private func sideEntries(_ title: String) -> [SideEntry]? {
        guard SideEntry.hasSection(titled: title, in: sections) else { return nil }
        return SideEntry.entries(inSectionTitled: title, of: sections, dismissed: dismissed)
    }

    /// Forgets open rows that are no longer on today's board. Past days are
    /// left alone, so browsing them doesn't close today's rows.
    private func pruneOpenRows() {
        guard Calendar.current.isDateInToday(fileDate) else { return }
        let items = TopicBoard.boardItems(sections: sections, metas: topics, today: .now)
        let live = Set(items.map { StepRowView.expansionKey($0.key) } + items.map(\.topicKey) + [Self.holdKey])
        openRaw = BoardExpansion.pruned(openRaw, keeping: live)
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
                reloadBoardFiles()
            } catch {
                reloadBoardFiles()
                await show(op.failureMessage(for: error))
            }
        }
    }

    /// Shows a write error until the next write, the next board reload, or
    /// `errorSeconds`, whichever comes first.
    private func show(_ message: String) async {
        writeError = message
        try? await Task.sleep(for: .seconds(Self.errorSeconds))
        if writeError == message { writeError = nil }
    }

    static let errorSeconds = 8

    private func reloadBoardFiles() {
        topics = TopicsFileLoader.load(from: actionItemsDirectory)
        dismissed = DismissedFileLoader.load(from: actionItemsDirectory)
    }
}

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
    /// The Done list's range, from `scout-display.json` (`actionItems.topics.doneRange`),
    /// the same value Settings ▸ Action Items ▸ Done range edits (audit #46:
    /// the board used to keep its own copy, so the setting did nothing).
    @Binding var doneRange: DoneRange

    @EnvironmentObject private var writerBox: ActionItemsWriterBox
    @EnvironmentObject private var vaultUndo: VaultUndoController
    @Environment(\.undoManager) private var undoManager
    @State private var topics: [TopicMeta] = []
    @State private var doneDates: [String: Date] = [:]
    @State private var dismissed: Set<String> = []
    /// Whether this engine has the board's write commands at all.
    @State private var canWrite = false
    /// Off while the page's engine check fails (`ActionItemsView`).
    @Environment(\.actionItemsWritesEnabled) private var writesEnabled
    @State private var writeError: String?
    @AppStorage("topicBoard.open") private var openRaw: String = ""
    @AppStorage("topicBoard.filter") private var filterRaw: String = ""
    /// Groups closed while a legend filter is on; cleared when it changes.
    @State private var closedUnderFilter: Set<String> = []
    /// Built when its inputs change, not on every `body` (audit #49). `nil`
    /// until the first build.
    @State private var board: TopicBoard?
    /// How many topic rows are built; "Show more" adds a page (audit #49).
    @State private var topicWindow = TopicWindow()

    private var filter: Set<BoardState> { BoardFilterStorage.decode(filterRaw) }

    /// What the board is built from. The day, not the moment: `boardDate`
    /// reads the clock when the board is built.
    private struct BoardInputs: Equatable {
        let sections: [ActionSection]
        let topics: [TopicMeta]
        let filterRaw: String
        let search: String
        let fileDate: Date
    }

    private var inputs: BoardInputs {
        BoardInputs(sections: sections, topics: topics, filterRaw: filterRaw, search: search, fileDate: fileDate)
    }

    /// The moment the board judges snoozes and the Done range against: now on
    /// today's file, the end of the displayed day on an older one (audit #46).
    private var boardDate: Date { TopicBoard.referenceDate(fileDate: fileDate, now: .now) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                BoardLegendView(filterRaw: $filterRaw)
                    .padding(.bottom, 4)
                if let writeError {
                    Text(writeError)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Status.err)
                }
                if let board {
                    topicRows(board)
                }
                BoardSideListsView(
                    inbox: sideEntries("Inbox"),
                    looseEnds: sideEntries("Loose ends"),
                    done: DoneEntry.entries(sections: sections, doneDates: doneDates, range: doneRange, today: boardDate),
                    doneRange: $doneRange,
                    onDismiss: dismissAction
                )
                .padding(.top, 10)
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .onChange(of: inputs, initial: true) { _, _ in
            board = TopicBoard.build(sections: sections, topics: topics, filter: filter, search: search, today: boardDate)
        }
        .onChange(of: filterRaw) { _, _ in closedUnderFilter = [] }
        .onChange(of: fileDate) { _, _ in topicWindow = TopicWindow() }
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

    @ViewBuilder
    private func topicRows(_ board: TopicBoard) -> some View {
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
        ForEach(board.topics.prefix(topicWindow.visibleCount(of: board.topics.count))) { topic in
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
        if let summary = topicWindow.summary(total: board.topics.count) {
            HStack(spacing: 10) {
                Button("Show \(topicWindow.nextPageCount(total: board.topics.count)) more") {
                    topicWindow.showMore()
                }
                .buttonStyle(.ds(.link, size: .small))
                Text("\(summary) · ⌘F searches all of them")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p4)
                Spacer(minLength: 0)
            }
            .padding(.vertical, 4)
        }
        if !board.onHold.isEmpty {
            HoldGroupView(steps: board.onHold, scoutDirectory: scoutDirectory, expanded: isOpen(Self.holdKey))
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
            get: {
                BoardExpansion.isOpen(key, in: openRaw, filtering: !filter.isEmpty, closedUnderFilter: closedUnderFilter)
            },
            set: { open in
                if filter.isEmpty {
                    openRaw = BoardExpansion.setting(key, open: open, in: openRaw)
                } else {
                    closedUnderFilter = BoardExpansion.settingUnderFilter(key, open: open, in: closedUnderFilter)
                }
            }
        )
    }

    /// Rename and Dismiss run `scoutctl action-items` too, so they need the
    /// engine to support board writes AND the page's engine check to pass:
    /// the same switch that turns the List's writes off (R2).
    static func boardWritesAllowed(engineSupports: Bool, writesEnabled: Bool) -> Bool {
        engineSupports && writesEnabled
    }

    private var boardWritesAllowed: Bool {
        Self.boardWritesAllowed(engineSupports: canWrite, writesEnabled: writesEnabled)
    }

    private func renameAction(for topic: BoardTopic) -> ((String) -> Void)? {
        guard boardWritesAllowed else { return nil }
        return { write(.renameTopic(tag: topic.key, name: $0)) }
    }

    private var dismissAction: ((SideEntry) -> Void)? {
        boardWritesAllowed ? { dismiss($0) } : nil
    }

    private func dismiss(_ entry: SideEntry) {
        dismissed.insert(entry.key)
        write(.dismiss(key: entry.key))
    }

    private func write(_ op: BoardWriteOp) {
        writeError = nil
        let writer = writerBox.boardWriter
        Task {
            do {
                let result = try await writer.submit(op)
                reloadBoardFiles()
                if let why = result.undoUnavailable { vaultUndo.noteNotUndoable(op.actionName, why) }
                if let change = result.change {
                    vaultUndo.register(
                        .restoring(change) { change, direction in try await writer.restore(change, direction) },
                        with: undoManager
                    ) { reloadBoardFiles() }
                }
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

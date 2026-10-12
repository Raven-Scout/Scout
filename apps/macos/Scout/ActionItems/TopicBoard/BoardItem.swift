import Foundation

/// One task placed on the topic board.
nonisolated struct BoardItem: Identifiable, Equatable, Sendable {
    let task: ActionTask
    let state: BoardState
    let topicKey: String
    /// Position in the daily file; breaks priority ties.
    let order: Int

    var id: UUID { task.id }
    /// What other tasks name in `- Depends:`.
    var key: String { task.shortPrefix ?? task.id.uuidString }

    var hasTicketOrThread: Bool {
        task.deepLinks.contains {
            switch $0 {
            case .linear, .slackThread: true
            default: false
            }
        }
    }

    /// Lower sorts first: work with a Linear issue or Slack thread, then Linear
    /// priority (1 urgent … 4 low, none last), then the current cycle, then state.
    var priorityKey: [Int] {
        [
            hasTicketOrThread ? 0 : 1,
            task.fields.priority.flatMap { (1...4).contains($0) ? $0 - 1 : nil } ?? 4,
            task.fields.cycle == "current" ? 0 : 1,
            state.rank,
        ]
    }

    static func byPriority(_ a: BoardItem, _ b: BoardItem) -> Bool {
        a.priorityKey != b.priorityKey ? a.priorityKey.lexicographicallyPrecedes(b.priorityKey) : a.order < b.order
    }
}

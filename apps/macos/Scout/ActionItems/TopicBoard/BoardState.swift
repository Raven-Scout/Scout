import Foundation

/// Where a task stands on the topic board.
nonisolated enum BoardState: String, CaseIterable, Sendable {
    case mine, doing, waiting, blocked, hold, done

    /// The states the legend can filter by, in legend order.
    static let filterable: [BoardState] = [.mine, .doing, .waiting, .blocked]

    var label: String {
        switch self {
        case .mine: "Mine"
        case .doing: "Doing"
        case .waiting: "Waiting"
        case .blocked: "Blocked"
        case .hold: "On hold"
        case .done: "Done"
        }
    }

    var help: String {
        switch self {
        case .mine: "Needs your action or decision"
        case .doing: "You or an agent are working on it"
        case .waiting: "Waiting on others"
        case .blocked: "Blocked by another step"
        case .hold: "On hold"
        case .done: "Done"
        }
    }

    var rank: Int { Self.allCases.firstIndex(of: self) ?? Self.allCases.count }

    var isOpen: Bool { self != .hold && self != .done }

    /// A task's state: done wins, then a current snooze, then the task's own
    /// `- State:` field, then its section. `nil` for tasks the board doesn't
    /// show (focus, meetings and digest sections).
    static func of(_ task: ActionTask, in kind: ActionSection.Kind, today: Date) -> BoardState? {
        if task.done || kind == .done { return .done }
        guard let sectionState = sectionState(kind) else { return nil }
        if let until = task.snoozedUntil, until > today { return .hold }
        return task.fields.state.map(BoardState.init) ?? sectionState
    }

    private static func sectionState(_ kind: ActionSection.Kind) -> BoardState? {
        switch kind {
        case .urgent, .todo, .personal, .neutral: .mine
        case .watching: .waiting
        case .done: .done
        case .focus, .meetings, .digest: nil
        }
    }

    private init(_ state: TaskState) {
        switch state {
        case .mine: self = .mine
        case .doing: self = .doing
        case .waiting: self = .waiting
        case .blocked: self = .blocked
        case .hold: self = .hold
        }
    }
}

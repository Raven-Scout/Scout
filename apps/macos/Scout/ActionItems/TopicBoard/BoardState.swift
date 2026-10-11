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

    /// A task's state, first rule that applies (the order the skill's Board
    /// Fields section documents): ticked → done; an active snooze → hold; a
    /// `Depends:` on a task in `openTags` → blocked; its `- State:`; its
    /// section (🔴/🟡 mine, 🟢 waiting, 🅿️ Parked hold). `nil` for tasks the
    /// board doesn't show (focus, meetings and digest sections).
    static func of(_ task: ActionTask, in section: ActionSection, today: Date, openTags: Set<String> = []) -> BoardState? {
        if task.done || section.kind == .done { return .done }
        guard let sectionState = sectionState(section) else { return nil }
        if let until = task.snoozedUntil, until > today { return .hold }
        if task.fields.depends.contains(where: openTags.contains) { return .blocked }
        return task.fields.state.map(BoardState.init) ?? sectionState
    }

    /// `## 🅿️ Parked`. The parser doesn't treat 🅿️ as a section emoji, so it
    /// can arrive in the title instead.
    static func isParked(_ section: ActionSection) -> Bool {
        let title = section.title.replacingOccurrences(of: "🅿️", with: "").replacingOccurrences(of: "🅿", with: "")
        return section.emoji.hasPrefix("🅿") || section.title.hasPrefix("🅿")
            || title.trimmingCharacters(in: .whitespaces).caseInsensitiveCompare("Parked") == .orderedSame
    }

    private static func sectionState(_ section: ActionSection) -> BoardState? {
        if isParked(section) { return .hold }
        switch section.kind {
        case .urgent, .todo, .personal, .neutral: return .mine
        case .watching: return .waiting
        case .done: return .done
        case .focus, .meetings, .digest: return nil
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

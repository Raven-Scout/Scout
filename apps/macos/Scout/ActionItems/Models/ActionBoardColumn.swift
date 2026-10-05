import Foundation

/// One column of the Action Items status board (issue #15). A presentation-only
/// bucketing of the already-parsed sections by status — no new data model.
struct ActionBoardColumn: Identifiable, Equatable {
    let kind: ActionSection.Kind
    let title: String
    let tasks: [ActionTask]

    var id: String { kind.rawValue }
    var count: Int { tasks.count }

    /// Status kinds that map onto board columns, in display order. The board is
    /// a status view, so the non-status sections (focus/meetings/digest/neutral)
    /// stay List-only and aren't represented here.
    private static let order: [(kind: ActionSection.Kind, title: String)] = [
        (.urgent,   "Urgent"),
        (.todo,     "To Do"),
        (.watching, "Watching"),
        (.personal, "Personal"),
        (.done,     "Done"),
    ]

    /// Build columns from the (already consolidated + filtered) sections. Tasks
    /// are bucketed by `effectiveKind` so a task snoozed forward from an urgent
    /// section still lands in Urgent. Urgent/To Do/Watching/Done always appear
    /// (even when empty) to keep the board's structure stable; Personal appears
    /// only when it has tasks.
    static func columns(from sections: [ActionSection]) -> [ActionBoardColumn] {
        var byKind: [ActionSection.Kind: [ActionTask]] = [:]
        for section in sections {
            for task in section.tasks {
                let kind = task.snoozedFromKind ?? section.kind
                guard order.contains(where: { $0.kind == kind }) else { continue }
                byKind[kind, default: []].append(task)
            }
        }

        return order.compactMap { entry in
            let tasks = byKind[entry.kind] ?? []
            if entry.kind == .personal && tasks.isEmpty { return nil }
            return ActionBoardColumn(kind: entry.kind, title: entry.title, tasks: tasks)
        }
    }
}

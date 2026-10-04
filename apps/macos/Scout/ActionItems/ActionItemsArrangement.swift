import Foundation

/// Sort and grouping for the Action Items list and board (spec section 6).
/// Runs after done-task consolidation and the filters, so List and Board keep
/// showing the same task set. `ActionItemsView` caches the result.
nonisolated enum ActionItemsArrangement {
    struct Arranged: Equatable, Sendable {
        var sections: [ActionSection]
        /// Source kind of every task moved into the merged section. Cards need
        /// it for the priority stripe and for snooze's `--from-kind`.
        var kinds: [UUID: ActionSection.Kind]
    }

    /// The sections "One list" merges.
    static let mergedKinds: Set<ActionSection.Kind> = [.urgent, .todo, .watching, .personal]
    static let mergedSectionID = ActionItemsParser.stableID("section|arranged-open")

    /// Each sub-task's parent in the source file: the nearest task above it, in
    /// the same section or archive group, with a smaller indent. Built from the
    /// parsed document before consolidation and filters, so a later filter
    /// cannot change who a sub-task belongs to.
    static func parents(in sections: [ActionSection]) -> [UUID: UUID] {
        var out: [UUID: UUID] = [:]
        for section in sections {
            for list in [section.tasks] + section.collapsed.map(\.tasks) {
                var stack: [ActionTask] = []
                for task in list {
                    while let last = stack.last, last.indentLevel >= task.indentLevel { stack.removeLast() }
                    if task.indentLevel > 0, let parent = stack.last { out[task.id] = parent.id }
                    stack.append(task)
                }
            }
        }
        return out
    }

    static func arrange(
        _ sections: [ActionSection],
        parents: [UUID: UUID],
        sort: ActionItemsDisplay.Sort,
        grouping: ActionItemsDisplay.Grouping
    ) -> Arranged {
        if sort == .fileOrder && grouping == .section { return Arranged(sections: sections, kinds: [:]) }
        let sorted = sections.map { replacing(tasks: Self.sorted($0.tasks, by: sort, parents: parents), in: $0) }
        guard grouping == .none else { return Arranged(sections: sorted, kinds: [:]) }
        return merged(sorted, sort: sort, parents: parents)
    }

    /// Stable sort that moves a top-level task together with its sub-tasks.
    /// A sub-task joins the block in front of it only when that block holds its
    /// parent; otherwise it is a block of its own. Ties keep file order.
    static func sorted(_ tasks: [ActionTask], by sort: ActionItemsDisplay.Sort, parents: [UUID: UUID]) -> [ActionTask] {
        guard sort != .fileOrder, tasks.count > 1 else { return tasks }
        var blocks: [[ActionTask]] = []
        var members: [Set<UUID>] = []
        for task in tasks {
            if task.indentLevel > 0, let parent = parents[task.id], let last = members.indices.last,
               members[last].contains(parent) {
                blocks[last].append(task)
                members[last].insert(task.id)
            } else {
                blocks.append([task])
                members.append([task.id])
            }
        }
        return blocks.enumerated()
            .sorted { a, b in
                switch compare(a.element[0], b.element[0], by: sort) {
                case .orderedAscending:  return true
                case .orderedDescending: return false
                case .orderedSame:       return a.offset < b.offset
                }
            }
            .flatMap(\.element)
    }

    private static func compare(_ a: ActionTask, _ b: ActionTask, by sort: ActionItemsDisplay.Sort) -> ComparisonResult {
        switch sort {
        case .fileOrder:    return .orderedSame
        case .alphabetical: return a.plainSubject.localizedStandardCompare(b.plainSubject)
        }
    }

    private static func merged(
        _ sections: [ActionSection], sort: ActionItemsDisplay.Sort, parents: [UUID: UUID]
    ) -> Arranged {
        var out: [ActionSection] = []
        var insertAt: Int?
        var kinds: [UUID: ActionSection.Kind] = [:]
        var tasks: [ActionTask] = []
        var bullets: [String] = []
        var tables: [ActionSection.Table] = []
        var subheads: [String] = []
        var collapsed: [ActionSection.CollapsedGroup] = []
        for section in sections {
            guard mergedKinds.contains(section.kind) else {
                out.append(section)
                continue
            }
            if insertAt == nil { insertAt = out.count }
            for task in section.tasks + section.collapsed.flatMap(\.tasks) { kinds[task.id] = section.kind }
            tasks += section.tasks
            bullets += section.bullets
            tables += section.tables
            subheads += section.subheads
            collapsed += section.collapsed
        }
        guard let insertAt else { return Arranged(sections: sections, kinds: [:]) }
        out.insert(ActionSection(
            id: mergedSectionID, emoji: "", title: "Open", kind: .neutral,
            tasks: Self.sorted(tasks, by: sort, parents: parents), bullets: bullets, tables: tables,
            subheads: subheads, collapsed: collapsed
        ), at: insertAt)
        return Arranged(sections: out, kinds: kinds)
    }

    private static func replacing(tasks: [ActionTask], in s: ActionSection) -> ActionSection {
        ActionSection(
            id: s.id, emoji: s.emoji, title: s.title, kind: s.kind, tasks: tasks,
            bullets: s.bullets, tables: s.tables, subheads: s.subheads, collapsed: s.collapsed
        )
    }
}

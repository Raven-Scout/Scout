import Foundation

/// A board row plus the blocked steps nested under it.
nonisolated struct BoardStep: Identifiable, Equatable, Sendable {
    let item: BoardItem
    let blocked: [BoardStep]

    var id: UUID { item.id }
    var blockedCount: Int { blocked.reduce(0) { $0 + 1 + $1.blockedCount } }
}

nonisolated struct BoardTopic: Identifiable, Equatable, Sendable {
    let key: String
    let meta: TopicMeta?
    let steps: [BoardStep]
    /// Open tasks per filterable state, across the whole topic.
    let counts: [BoardState: Int]

    var id: String { key }
    var title: String { meta?.displayTitle ?? (key == TopicBoard.otherKey ? "Other" : key) }
}

/// The topic board for one daily file: tasks grouped by workstream, blocked
/// steps nested under what blocks them, on-hold work in one group at the end.
nonisolated struct TopicBoard: Equatable, Sendable {
    static let otherKey = "__other"

    let topics: [BoardTopic]
    let onHold: [BoardStep]

    static func build(
        sections: [ActionSection],
        topics metas: [TopicMeta],
        filter: Set<BoardState> = [],
        search: String = "",
        today: Date
    ) -> TopicBoard {
        let needle = search.trimmingCharacters(in: .whitespaces).lowercased()
        let items = boardItems(sections: sections, metas: metas, today: today).filter { $0.task.matchesSearch(needle) }
        let layout = Layout(items: items, filter: filter)
        let metaByTag = Dictionary(metas.map { ($0.tag, $0) }, uniquingKeysWith: { first, _ in first })
        let topicOrder = Dictionary(metas.enumerated().map { ($1.tag, $0) }, uniquingKeysWith: { first, _ in first })

        let held = layout.filtering ? [] : items.filter { $0.state == .hold }.sorted(by: BoardItem.byPriority)
        let roots = items.filter(layout.isTopLevel) + layout.unreachable(from: items.filter(layout.isTopLevel) + held)
        let groups = Dictionary(grouping: roots, by: \.topicKey)
            .mapValues { $0.sorted(by: BoardItem.byPriority) }
            .sorted { a, b in
                let ka = a.value.first?.priorityKey ?? []
                let kb = b.value.first?.priorityKey ?? []
                if ka != kb { return ka.lexicographicallyPrecedes(kb) }
                return (topicOrder[a.key] ?? .max, a.key) < (topicOrder[b.key] ?? .max, b.key)
            }

        var seen = Set<String>()
        let topics = groups.map { key, rows in
            BoardTopic(
                key: key,
                meta: metaByTag[key],
                steps: layout.steps(rows, seen: &seen),
                counts: counts(items.filter { $0.topicKey == key })
            )
        }
        return TopicBoard(topics: topics, onHold: layout.steps(held, seen: &seen))
    }

    static func boardItems(sections: [ActionSection], metas: [TopicMeta], today: Date) -> [BoardItem] {
        let topicBySession = Dictionary(
            metas.compactMap { meta in meta.session.map { ($0, meta.tag) } },
            uniquingKeysWith: { first, _ in first }
        )
        let tasks = sections.flatMap { section in
            section.tasks.filter { $0.indentLevel == 0 }.map { (task: $0, kind: section.kind) }
        }
        return tasks.enumerated().compactMap { order, entry in
            guard let state = BoardState.of(entry.task, in: entry.kind, today: today), state != .done else { return nil }
            let fields = entry.task.fields
            let topic = fields.topic ?? fields.session.flatMap { topicBySession[$0] } ?? otherKey
            return BoardItem(task: entry.task, state: state, topicKey: topic, order: order)
        }
    }

    private static func counts(_ items: [BoardItem]) -> [BoardState: Int] {
        Dictionary(grouping: items.filter { BoardState.filterable.contains($0.state) }, by: \.state).mapValues(\.count)
    }
}

/// Which items sit at a topic's top level and which nest under a blocker.
private nonisolated struct Layout {
    let items: [BoardItem]
    let filter: Set<BoardState>
    let openKeys: Set<String>
    let stateByKey: [String: BoardState]

    init(items: [BoardItem], filter: Set<BoardState>) {
        self.items = items
        self.filter = filter
        openKeys = Set(items.filter { $0.state.isOpen }.map(\.key))
        stateByKey = Dictionary(items.map { ($0.key, $0.state) }, uniquingKeysWith: { first, _ in first })
    }

    var filtering: Bool { !filter.isEmpty }

    func shown(_ state: BoardState) -> Bool { !filtering || filter.contains(state) }

    func isTopLevel(_ item: BoardItem) -> Bool {
        let blockers = item.task.fields.depends.filter(openKeys.contains)
        if filtering {
            guard filter.contains(item.state) else { return false }
            guard item.state == .blocked else { return true }
            return !blockers.contains { stateByKey[$0].map(filter.contains) ?? false }
        }
        switch item.state {
        case .mine, .doing, .waiting: return true
        case .blocked: return blockers.isEmpty
        case .hold, .done: return false
        }
    }

    /// Blocked steps no root leads to, such as a dependency cycle with no
    /// open way in. Each becomes a root itself so nothing drops off the board.
    func unreachable(from roots: [BoardItem]) -> [BoardItem] {
        var placed = reachable(from: roots)
        var extra: [BoardItem] = []
        for item in items where item.state == .blocked && shown(.blocked) && !isTopLevel(item) && !placed.contains(item.key) {
            extra.append(item)
            placed.formUnion(reachable(from: [item]))
        }
        return extra
    }

    func steps(_ rows: [BoardItem], seen: inout Set<String>) -> [BoardStep] {
        rows.map { step($0, seen: &seen) }
    }

    private func reachable(from roots: [BoardItem]) -> Set<String> {
        var placed = Set(roots.map(\.key))
        var queue = roots
        while let next = queue.popLast() {
            for kid in nested(under: next.key) where placed.insert(kid.key).inserted {
                queue.append(kid)
            }
        }
        return placed
    }

    private func step(_ item: BoardItem, seen: inout Set<String>) -> BoardStep {
        seen.insert(item.key)
        var kids: [BoardStep] = []
        for kid in nested(under: item.key) where !seen.contains(kid.key) {
            kids.append(step(kid, seen: &seen))
        }
        return BoardStep(item: item, blocked: kids)
    }

    /// Blocked steps that nest under `key`: never ones already at a top level.
    private func nested(under key: String) -> [BoardItem] {
        guard shown(.blocked) else { return [] }
        return items
            .filter { $0.state == .blocked && $0.task.fields.depends.contains(key) && !isTopLevel($0) }
            .sorted { $0.order < $1.order }
    }
}

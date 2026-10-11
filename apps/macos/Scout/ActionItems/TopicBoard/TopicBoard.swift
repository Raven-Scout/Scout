import Foundation

/// A board row plus what nests under it: its own sub-steps (`- Parent:`),
/// then the steps it blocks.
nonisolated struct BoardStep: Identifiable, Equatable, Sendable {
    let item: BoardItem
    let children: [BoardStep]
    let blocked: [BoardStep]

    var id: UUID { item.id }
    var nested: [BoardStep] { children + blocked }
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
        let base = items.filter(layout.isTopLevel)
        let baseKeys = Set(base.map(\.key))
        let tops = base.filter { item in !(layout.parent(of: item.key).map(baseKeys.contains) ?? false) }
        let roots = tops + layout.unreachable(from: tops + held)
        let groups = Dictionary(grouping: roots, by: \.topicKey)
            .mapValues { $0.sorted(by: BoardItem.byPriority) }
            .sorted { a, b in
                let ka = a.value.first?.priorityKey ?? []
                let kb = b.value.first?.priorityKey ?? []
                if ka != kb { return ka.lexicographicallyPrecedes(kb) }
                return (topicOrder[a.key] ?? .max, a.key) < (topicOrder[b.key] ?? .max, b.key)
            }

        var seen = Set<String>()
        let topics = groups.compactMap { key, rows -> BoardTopic? in
            let steps = layout.steps(rows, seen: &seen)
            guard !steps.isEmpty else { return nil }
            return BoardTopic(key: key, meta: metaByTag[key], steps: steps, counts: counts(items.filter { $0.topicKey == key }))
        }
        let heldRows = held.filter { !seen.contains($0.key) }
        return TopicBoard(topics: topics, onHold: layout.steps(heldRows, seen: &seen))
    }

    static func boardItems(sections: [ActionSection], metas: [TopicMeta], today: Date) -> [BoardItem] {
        let topicBySession = Dictionary(
            metas.compactMap { meta in meta.session.map { ($0, meta.tag) } },
            uniquingKeysWith: { first, _ in first }
        )
        let tasks = sections.flatMap { section in
            section.tasks.filter { $0.indentLevel == 0 }.map { (task: $0, section: section) }
        }
        let openTags = Set(tasks.filter { !$0.task.done && $0.section.kind != .done }.compactMap(\.task.shortPrefix))
        return tasks.enumerated().compactMap { order, entry in
            guard let state = BoardState.of(entry.task, in: entry.section, today: today, openTags: openTags),
                  state != .done else { return nil }
            let fields = entry.task.fields
            let topic = fields.topic ?? fields.session.flatMap { topicBySession[$0] } ?? otherKey
            return BoardItem(task: entry.task, state: state, topicKey: topic, order: order)
        }
    }

    private static func counts(_ items: [BoardItem]) -> [BoardState: Int] {
        Dictionary(grouping: items.filter { BoardState.filterable.contains($0.state) }, by: \.state).mapValues(\.count)
    }
}

/// Which items sit at a topic's top level and which nest under a parent or blocker.
private nonisolated struct Layout {
    let items: [BoardItem]
    let filter: Set<BoardState>
    let openKeys: Set<String>
    let stateByKey: [String: BoardState]
    /// Built once: each step's parent (none when its `Parent:` chain leads
    /// back to it), sub-steps by parent, blocked steps by what they depend on.
    private let parentByKey: [String: String]
    private let childrenByParent: [String: [BoardItem]]
    private let dependentsByKey: [String: [BoardItem]]

    init(items: [BoardItem], filter: Set<BoardState>) {
        self.items = items
        self.filter = filter
        openKeys = Set(items.filter { $0.state.isOpen }.map(\.key))
        stateByKey = Dictionary(items.map { ($0.key, $0.state) }, uniquingKeysWith: { first, _ in first })
        let parents = Self.acyclicParents(items)
        parentByKey = parents
        childrenByParent = Dictionary(grouping: items.filter { parents[$0.key] != nil }) { parents[$0.key] ?? "" }
        var dependents: [String: [BoardItem]] = [:]
        for item in items where item.state == .blocked {
            for key in Set(item.task.fields.depends) { dependents[key, default: []].append(item) }
        }
        dependentsByKey = dependents
    }

    var filtering: Bool { !filter.isEmpty }

    func parent(of key: String) -> String? { parentByKey[key] }

    /// `Parent:` links, minus any whose chain leads back to the step itself
    /// (A ↔ B, or a step that is its own parent): those steps stand alone
    /// rather than drop off the board.
    static func acyclicParents(_ items: [BoardItem]) -> [String: String] {
        let declared = Dictionary(
            items.compactMap { item in item.task.fields.parent.map { (item.key, $0) } },
            uniquingKeysWith: { first, _ in first }
        )
        return declared.filter { key, parent in
            var visited: Set<String> = [key]
            var next: String? = parent
            while let current = next {
                guard visited.insert(current).inserted else { return current != key }
                next = declared[current]
            }
            return true
        }
    }

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

    /// Rows already placed (nested under an earlier root) are skipped, so a
    /// step reachable from two roots still shows once.
    func steps(_ rows: [BoardItem], seen: inout Set<String>) -> [BoardStep] {
        var steps: [BoardStep] = []
        for row in rows where !seen.contains(row.key) {
            steps.append(step(row, seen: &seen))
        }
        return steps
    }

    private func reachable(from roots: [BoardItem]) -> Set<String> {
        var placed = Set(roots.map(\.key))
        var queue = roots
        while let next = queue.popLast() {
            for kid in children(of: next.key) + blocked(by: next.key) where placed.insert(kid.key).inserted {
                queue.append(kid)
            }
        }
        return placed
    }

    private func step(_ item: BoardItem, seen: inout Set<String>) -> BoardStep {
        seen.insert(item.key)
        var children: [BoardStep] = []
        for kid in self.children(of: item.key) where !seen.contains(kid.key) {
            children.append(step(kid, seen: &seen))
        }
        var blocked: [BoardStep] = []
        for kid in self.blocked(by: item.key) where !seen.contains(kid.key) {
            blocked.append(step(kid, seen: &seen))
        }
        return BoardStep(item: item, children: children, blocked: blocked)
    }

    /// Sub-steps of `key` (`- Parent:`) in a state the board shows; on-hold
    /// ones only while unfiltered.
    private func children(of key: String) -> [BoardItem] {
        (childrenByParent[key] ?? [])
            .filter { shown($0.state) && ($0.state != .hold || !filtering) }
            .sorted { $0.order < $1.order }
    }

    /// Blocked steps that nest under `key`: never ones already at a top level.
    private func blocked(by key: String) -> [BoardItem] {
        guard shown(.blocked) else { return [] }
        return (dependentsByKey[key] ?? []).filter { !isTopLevel($0) }.sorted { $0.order < $1.order }
    }
}

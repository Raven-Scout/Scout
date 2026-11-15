import Foundation

/// What `ActionItemsView` renders for one document, filter and display
/// profile. The view keeps it in state and rebuilds it only when one of those
/// changes, so sorting never runs on a plain body pass (#83/#88).
struct ActionItemsLayout: Equatable {
    /// The List: sorted and, for One list, merged.
    let list: ActionItemsArrangement.Arranged
    /// The Board: sorted inside each column, never merged.
    let boardColumns: [ActionBoardColumn]

    /// The default profile renders the sections exactly as filtered, so the
    /// view can skip the cache and keep today's code path.
    static func isPassThrough(_ display: ActionItemsDisplay) -> Bool {
        display.sort == .fileOrder && display.grouping == .section
    }

    /// - Parameters:
    ///   - document: the parsed sections, before consolidation and filters,
    ///     so sub-tasks keep their real parent.
    ///   - filtered: the consolidated and filtered sections the view shows.
    static func make(
        document: [ActionSection],
        filtered: [ActionSection],
        display: ActionItemsDisplay
    ) -> ActionItemsLayout {
        let parents = ActionItemsArrangement.parents(in: document)
        let list = ActionItemsArrangement.arrange(
            filtered, parents: parents, sort: display.sort, grouping: display.grouping)
        let columns = ActionBoardColumn.columns(from: filtered, sort: display.sort, parents: parents)
        return ActionItemsLayout(list: list, boardColumns: columns)
    }
}

// MARK: - Rows the List builds

extension ActionItemsLayout {
    /// The sections the List draws: the arranged ones, or the filtered ones
    /// when the profile is the default. Task windows are keyed by these ids,
    /// so every question about which rows are on screen reads these sections.
    static func listSections(_ layout: ActionItemsLayout?, filtered: [ActionSection]) -> [ActionSection] {
        layout?.list.sections ?? filtered
    }

    /// Rows "Select all" may take: the built rows of the task sections. Focus,
    /// Meetings, Digest and the Done drawer are left out.
    static func selectableRows(in sections: [ActionSection], windows: [UUID: TaskWindow]) -> [ActionTask] {
        sections
            .filter { ![.focus, .meetings, .digest, .done].contains($0.kind) }
            .flatMap { Array((windows[$0.id] ?? TaskWindow()).visible($0.tasks)) }
    }

    /// Every row the List shows: the built rows, plus the Done drawer in full.
    static func shownRows(in sections: [ActionSection], windows: [UUID: TaskWindow]) -> [ActionTask] {
        sections.flatMap { section in
            section.kind == .done ? section.tasks[...] : (windows[section.id] ?? TaskWindow()).visible(section.tasks)
        }
    }

    /// Where a reopened task lands in the drawn List: its section and row, so
    /// the caller can grow that section's window to it.
    static func revealTarget(
        in sections: [ActionSection], subject: String, shortPrefix: String?
    ) -> (section: UUID, index: Int)? {
        for section in sections where section.kind != .done {
            let index = section.tasks.firstIndex { task in
                guard !task.done else { return false }
                if let shortPrefix { return task.shortPrefix == shortPrefix }
                return task.matchableSubject == subject
            }
            if let index { return (section.id, index) }
        }
        return nil
    }
}

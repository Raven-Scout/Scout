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

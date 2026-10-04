import Foundation

/// How much of one task list the Action Items views build at a time.
///
/// The views lay every row out eagerly — a `LazyVStack` here wedges the main
/// thread (#83), so the stacks are plain `VStack`s — which makes first paint
/// linear in the number of rows, and slightly worse than linear in practice.
/// That was tolerable at ~150 open rows. The engine never retires an open item,
/// so a real day reached 944 of them by 2026-10-04 and building them all took
/// ~18 s of main-thread layout: opening the tab froze the app. Measured with
/// `PerfHarnessTests`: ~5–12 ms per collapsed card, ~17–30 ms per expanded one.
///
/// A window caps what is built to a page per list, with a "Show more" control
/// for the rest, so the cost of opening the tab no longer grows with the
/// backlog. Search still runs over every task, not just the shown ones.
nonisolated struct TaskWindow: Equatable, Sendable {
    /// Rows built per page. Small enough that a page of expanded Urgent cards
    /// plus three pages of collapsed ones lays out in well under a second.
    static let pageSize = 20

    private(set) var limit = Self.pageSize

    /// The leading `limit` rows, plus any sub-tasks of the last one: a page
    /// boundary must not separate a nested row from its parent.
    func visible(_ tasks: [ActionTask]) -> ArraySlice<ActionTask> {
        var end = min(limit, tasks.count)
        while end < tasks.count, tasks[end].indentLevel > 0 { end += 1 }
        return tasks[..<end]
    }

    func hiddenCount(in tasks: [ActionTask]) -> Int {
        tasks.count - visible(tasks).count
    }

    /// How many rows the next ``showMore()`` adds, for the button label.
    /// Measured rather than assumed to be a page: sub-task runs at either
    /// boundary move the cut.
    func nextPageCount(in tasks: [ActionTask]) -> Int {
        var next = self
        next.showMore()
        return next.visible(tasks).count - visible(tasks).count
    }

    mutating func showMore() {
        limit += Self.pageSize
    }

    /// Grows the window a page at a time until row `index` is built.
    mutating func reveal(_ index: Int) {
        while limit <= index { showMore() }
    }
}

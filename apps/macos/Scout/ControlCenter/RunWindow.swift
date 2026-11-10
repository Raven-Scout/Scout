import Foundation

/// How many rows of the Control Center sessions list are built at a time.
///
/// The list used to show `filtered.prefix(30)` and nothing else, so any run
/// older than the newest 30 that passed the filters could not be reached from
/// the app at all: the session log keeps every run, and a vault on the default
/// schedule passes 30 runs in about three days. The window keeps the first
/// page the same size and adds "Show N more" below it, the same shape as
/// ``TaskWindow`` in Action Items.
nonisolated struct RunWindow: Equatable, Sendable {
    /// Rows built per page. The first page matches the old fixed cap.
    static let pageSize = 30

    private(set) var limit = Self.pageSize

    func visible<T>(_ rows: [T]) -> ArraySlice<T> {
        rows.prefix(limit)
    }

    func hiddenCount<T>(in rows: [T]) -> Int {
        max(0, rows.count - limit)
    }

    /// How many rows the next ``showMore()`` adds, for the button label.
    func nextPageCount<T>(in rows: [T]) -> Int {
        min(Self.pageSize, hiddenCount(in: rows))
    }

    /// "Showing 30 of 412", or `nil` when every row is built.
    func summary<T>(in rows: [T]) -> String? {
        guard hiddenCount(in: rows) > 0 else { return nil }
        return "Showing \(visible(rows).count) of \(rows.count)"
    }

    mutating func showMore() {
        limit += Self.pageSize
    }

    /// Back to one page, for when the filters change and the list is a
    /// different list.
    mutating func reset() {
        limit = Self.pageSize
    }
}

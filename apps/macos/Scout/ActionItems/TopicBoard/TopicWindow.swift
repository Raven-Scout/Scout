import Foundation

/// How many topic rows the Topics board builds at a time (audit #49). Like
/// `TaskWindow` for the List and Board, it caps first paint on a day with a
/// long backlog; the board itself is still built over every task, so search
/// and the legend filter see all of them.
nonisolated struct TopicWindow: Equatable, Sendable {
    /// Topic rows per page. A topic row is heavier than a task card (its open
    /// steps lay out inside it), so the page is smaller than `TaskWindow`'s.
    static let pageSize = 15

    private(set) var limit = Self.pageSize

    func visibleCount(of total: Int) -> Int { min(limit, total) }

    func hiddenCount(of total: Int) -> Int { total - visibleCount(of: total) }

    /// How many rows the next `showMore()` adds.
    func nextPageCount(total: Int) -> Int { min(Self.pageSize, hiddenCount(of: total)) }

    /// "Showing 15 of 40 topics", or `nil` when every row is built.
    func summary(total: Int) -> String? {
        guard hiddenCount(of: total) > 0 else { return nil }
        return "Showing \(visibleCount(of: total)) of \(total) topics"
    }

    mutating func showMore() { limit += Self.pageSize }
}

extension TopicBoard {
    /// The moment the board judges snoozes and the Done range against (audit
    /// #46). On today's file it is `now`. On an older file it is the last
    /// moment of that day, so the board shows that day as it stood: a task
    /// snoozed until the next day is on hold, and "today" in the Done list
    /// means that day, not the real today.
    nonisolated static func referenceDate(fileDate: Date, now: Date, calendar: Calendar = .current) -> Date {
        if calendar.isDate(fileDate, inSameDayAs: now) { return now }
        let start = calendar.startOfDay(for: fileDate)
        return calendar.date(byAdding: DateComponents(day: 1, second: -1), to: start) ?? start
    }
}

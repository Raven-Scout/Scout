import Foundation

/// The Done list's window; clicking its label cycles this week → today → yesterday.
nonisolated enum DoneRange: String, CaseIterable, Sendable {
    case week, today, yesterday

    var label: String {
        switch self {
        case .week: "this week"
        case .today: "today"
        case .yesterday: "yesterday"
        }
    }

    var next: DoneRange {
        switch self {
        case .week: .today
        case .today: .yesterday
        case .yesterday: .week
        }
    }

    func contains(_ date: Date, today: Date, calendar: Calendar) -> Bool {
        let start = calendar.startOfDay(for: today)
        switch self {
        case .today:
            return calendar.isDate(date, inSameDayAs: start)
        case .yesterday:
            guard let yesterday = calendar.date(byAdding: .day, value: -1, to: start) else { return false }
            return calendar.isDate(date, inSameDayAs: yesterday)
        case .week:
            var monday = calendar
            monday.firstWeekday = 2
            guard let week = monday.dateInterval(of: .weekOfYear, for: start) else { return false }
            return date >= week.start && date <= today
        }
    }
}

/// A completed task with the day it was completed, when that's known.
nonisolated struct DoneEntry: Identifiable, Equatable, Sendable {
    let task: ActionTask
    let date: Date?

    var id: UUID { task.id }

    private static let dayRe = try! NSRegularExpression(pattern: #"\d{4}-\d{2}-\d{2}"#)

    /// Done tasks in `range`, newest first. The date is the first `YYYY-MM-DD`
    /// in a `- Completed:` sub-bullet, else the day the task first showed up
    /// ticked. Without either it is unknown: the task is still done this week
    /// (Recently Completed keeps a week), but never counts as today or
    /// yesterday, and sorts last.
    /// - Parameter doneDates: the day each tag first showed up ticked in an
    ///   earlier daily file (`DoneHistory`), used when a task has no
    ///   `- Completed:` date.
    static func entries(
        sections: [ActionSection],
        doneDates: [String: Date] = [:],
        range: DoneRange,
        today: Date,
        calendar: Calendar = .current
    ) -> [DoneEntry] {
        sections
            .flatMap { section in section.tasks.filter { $0.indentLevel == 0 && ($0.done || section.kind == .done) } }
            .map { task in
                let date = completedDate(task, calendar: calendar) ?? task.shortPrefix.flatMap { doneDates[$0] }
                return DoneEntry(task: task, date: date)
            }
            .filter { entry in entry.date.map { range.contains($0, today: today, calendar: calendar) } ?? (range == .week) }
            .sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
    }

    private static func completedDate(_ task: ActionTask, calendar: Calendar) -> Date? {
        guard let text = task.comments.first(where: { $0.author.caseInsensitiveCompare("Completed") == .orderedSame })?.text else {
            return nil
        }
        let ns = text as NSString
        guard let m = dayRe.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else { return nil }
        let parts = ns.substring(with: m.range).split(separator: "-").compactMap { Int($0) }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }
}

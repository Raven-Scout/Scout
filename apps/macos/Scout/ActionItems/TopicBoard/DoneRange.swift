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

/// A completed task with the day it was completed.
nonisolated struct DoneEntry: Identifiable, Equatable, Sendable {
    let task: ActionTask
    let date: Date

    var id: UUID { task.id }

    private static let dayRe = try! NSRegularExpression(pattern: #"\d{4}-\d{2}-\d{2}"#)

    /// Done tasks in `range`, newest first. The date is the first `YYYY-MM-DD`
    /// in a `- Completed:` sub-bullet, else the daily file's date.
    static func entries(
        sections: [ActionSection],
        fileDate: Date,
        range: DoneRange,
        today: Date,
        calendar: Calendar = .current
    ) -> [DoneEntry] {
        sections
            .flatMap { section in section.tasks.filter { $0.indentLevel == 0 && ($0.done || section.kind == .done) } }
            .map { DoneEntry(task: $0, date: completedDate($0, calendar: calendar) ?? fileDate) }
            .filter { range.contains($0.date, today: today, calendar: calendar) }
            .sorted { $0.date > $1.date }
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

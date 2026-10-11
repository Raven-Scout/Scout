import Foundation

/// When each task was done, read from the past week's daily files: the first
/// day a tag shows up ticked. Runs don't always write `- Completed:`, and a
/// daily file alone can't tell today's done work from last week's.
nonisolated enum DoneHistory {
    static let days = 8

    /// `[tag: first day ticked]` from `(day, sections)` pairs in any order.
    static func firstDoneDates(_ files: [(day: Date, sections: [ActionSection])]) -> [String: Date] {
        var dates: [String: Date] = [:]
        for file in files.sorted(by: { $0.day < $1.day }) {
            for section in file.sections {
                for task in section.tasks where task.done || section.kind == .done {
                    guard let tag = task.shortPrefix, dates[tag] == nil else { continue }
                    dates[tag] = file.day
                }
            }
        }
        return dates
    }

    /// Reads the daily files for `fileDate` and the `days - 1` days before it.
    /// Missing or unreadable files are skipped.
    static func load(from directory: URL, upTo fileDate: Date, calendar: Calendar = .current) -> [String: Date] {
        let files: [(day: Date, sections: [ActionSection])] = (0..<days).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: -offset, to: calendar.startOfDay(for: fileDate)) else { return nil }
            let url = directory.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: day, timeZone: calendar.timeZone)).md")
            guard let text = try? String(contentsOf: url, encoding: .utf8),
                  let doc = try? ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
            else { return nil }
            return (day, doc.sections)
        }
        return firstDoneDates(files)
    }
}

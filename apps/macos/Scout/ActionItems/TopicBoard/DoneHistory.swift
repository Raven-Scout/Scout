import Foundation

/// When each task was done, read from the past week's daily files: the first
/// day a tag shows up ticked. Runs don't always write `- Completed:`, and a
/// daily file alone can't tell today's done work from last week's.
nonisolated enum DoneHistory {
    static let days = 8

    /// `[tag: first day ticked]` from `(day, sections)` pairs in any order.
    static func firstDoneDates(_ files: [(day: Date, sections: [ActionSection])]) -> [String: Date] {
        firstDoneDates(tags: files.map { ($0.day, doneTags(in: $0.sections)) })
    }

    static func doneTags(in sections: [ActionSection]) -> [String] {
        sections.flatMap { section in
            section.tasks.filter { $0.done || section.kind == .done }.compactMap(\.shortPrefix)
        }
    }

    /// Reads the daily files for `fileDate` and the `days - 1` days before it.
    /// Missing or unreadable files are skipped; a file unchanged since the last
    /// call isn't parsed again.
    static func load(
        from directory: URL,
        upTo fileDate: Date,
        calendar: Calendar = .current,
        cache: DoneTagCache = .shared
    ) -> [String: Date] {
        let files: [(day: Date, tags: [String])] = (0..<days).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: -offset, to: calendar.startOfDay(for: fileDate)) else { return nil }
            let url = directory.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: day, timeZone: calendar.timeZone)).md")
            return cache.doneTags(at: url).map { (day, $0) }
        }
        return firstDoneDates(tags: files)
    }

    private static func firstDoneDates(tags files: [(day: Date, tags: [String])]) -> [String: Date] {
        var dates: [String: Date] = [:]
        for file in files.sorted(by: { $0.day < $1.day }) {
            for tag in file.tags where dates[tag] == nil {
                dates[tag] = file.day
            }
        }
        return dates
    }
}

/// The done tags of each daily file, kept while the file's modification date
/// and size stay the same. A multi-MB daily file takes a second or more to
/// parse, and the board asks again after every run or write.
nonisolated final class DoneTagCache: @unchecked Sendable {
    static let shared = DoneTagCache()

    private struct Entry {
        let modified: Date
        let size: Int
        let tags: [String]
    }

    private let lock = NSLock()
    private var entries: [URL: Entry] = [:]
    private(set) var parses = 0

    func doneTags(at url: URL) -> [String]? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let modified = attributes[.modificationDate] as? Date,
              let size = attributes[.size] as? Int else { return nil }
        if let entry = lock.withLock({ entries[url] }), entry.modified == modified, entry.size == size {
            return entry.tags
        }
        guard let text = try? String(contentsOf: url, encoding: .utf8),
              let doc = try? ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        else { return nil }
        let tags = DoneHistory.doneTags(in: doc.sections)
        lock.withLock {
            entries[url] = Entry(modified: modified, size: size, tags: tags)
            parses += 1
        }
        return tags
    }
}

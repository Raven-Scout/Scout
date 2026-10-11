import Testing
import Foundation
@testable import Scout

@Suite("Topic board — Done list")
struct BoardSideListsTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Europe/Prague")!
        return c
    }

    private func day(_ y: Int, _ m: Int, _ d: Int, hour: Int = 12) -> Date {
        calendar.date(from: DateComponents(year: y, month: m, day: d, hour: hour))!
    }

    private func sections(_ lines: [String]) throws -> [ActionSection] {
        let text = (["# Action Items — 2026-10-10", ""] + lines).joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-10-10.md")
        return try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count).sections
    }

    @Test func doneRangesCycleAndCoverTheRightDays() {
        let today = day(2026, 10, 10)
        #expect(DoneRange.week.next == .today && DoneRange.today.next == .yesterday && DoneRange.yesterday.next == .week)
        #expect(DoneRange.today.contains(day(2026, 10, 10, hour: 0), today: today, calendar: calendar))
        #expect(!DoneRange.today.contains(day(2026, 10, 9), today: today, calendar: calendar))
        #expect(DoneRange.yesterday.contains(day(2026, 10, 9), today: today, calendar: calendar))
        #expect(DoneRange.week.contains(day(2026, 10, 5, hour: 0), today: today, calendar: calendar))
        #expect(!DoneRange.week.contains(day(2026, 10, 4), today: today, calendar: calendar))
    }

    @Test func doneEntriesUseCompletedDateElseUnknown() throws {
        let s = try sections([
            "## 🔴 Urgent",
            "- [x] [#AAAA] **Ticked today**",
            "- [ ] [#BBBB] **Still open**",
            "## ✅ Done",
            "- [x] [#CCCC] **Merged Friday**",
            "  - Completed: 2026-10-09 17:40",
            "- [x] [#DDDD] **Last week**",
            "  - Completed: 2026-10-01",
        ])
        let week = DoneEntry.entries(sections: s, range: .week, today: day(2026, 10, 10), calendar: calendar)
        #expect(week.map(\.task.shortPrefix) == ["CCCC", "AAAA"])
        let yesterday = DoneEntry.entries(sections: s, range: .yesterday, today: day(2026, 10, 10), calendar: calendar)
        #expect(yesterday.map(\.task.shortPrefix) == ["CCCC"])
        let today = DoneEntry.entries(sections: s, range: .today, today: day(2026, 10, 10), calendar: calendar)
        #expect(today.isEmpty)
        #expect(week.first { $0.task.shortPrefix == "AAAA" }?.date == nil)
    }

    @Test func doneDateIsTheFirstDayATaskShowsUpTicked() throws {
        let earlier = try sections(["## 🔴 Urgent", "- [x] [#AAAA] **Ticked on the 8th**", "- [ ] [#BBBB] **Still open**"])
        let later = try sections(["## ✅ Done", "- [x] [#AAAA] **Ticked on the 8th**", "- [x] [#BBBB] **Ticked on the 10th**"])
        let dates = DoneHistory.firstDoneDates([(day(2026, 10, 10, hour: 0), later), (day(2026, 10, 8, hour: 0), earlier)])
        #expect(dates == ["AAAA": day(2026, 10, 8, hour: 0), "BBBB": day(2026, 10, 10, hour: 0)])

        let yesterday = DoneEntry.entries(
            sections: later, doneDates: dates,
            range: .yesterday, today: day(2026, 10, 11), calendar: calendar
        )
        #expect(yesterday.map(\.task.shortPrefix) == ["BBBB"])
    }

    @Test func historyParsesAnUnchangedFileOnce() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let fileDate = day(2026, 10, 10, hour: 0)
        let url = dir.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: fileDate, timeZone: calendar.timeZone)).md")
        try "# Action Items — 2026-10-10\n\n## ✅ Done\n- [x] [#AAAA] **Done**\n".write(to: url, atomically: true, encoding: .utf8)
        let cache = DoneTagCache()

        #expect(DoneHistory.load(from: dir, upTo: fileDate, calendar: calendar, cache: cache) == ["AAAA": fileDate])
        #expect(DoneHistory.load(from: dir, upTo: fileDate, calendar: calendar, cache: cache) == ["AAAA": fileDate])
        #expect(cache.parses == 1)

        try "# Action Items — 2026-10-10\n\n## ✅ Done\n- [x] [#AAAA] **Done**\n- [x] [#BBBB] **Also done**\n".write(to: url, atomically: true, encoding: .utf8)
        #expect(DoneHistory.load(from: dir, upTo: fileDate, calendar: calendar, cache: cache).keys.sorted() == ["AAAA", "BBBB"])
        #expect(cache.parses == 2)
    }
}

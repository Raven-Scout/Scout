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

    @Test func doneEntriesUseCompletedDateElseFileDate() throws {
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
        let fileDate = day(2026, 10, 10, hour: 0)
        let week = DoneEntry.entries(sections: s, fileDate: fileDate, range: .week, today: day(2026, 10, 10), calendar: calendar)
        #expect(week.map(\.task.shortPrefix) == ["AAAA", "CCCC"])
        let yesterday = DoneEntry.entries(sections: s, fileDate: fileDate, range: .yesterday, today: day(2026, 10, 10), calendar: calendar)
        #expect(yesterday.map(\.task.shortPrefix) == ["CCCC"])
    }
}

import Testing
import Foundation
@testable import Scout

@Suite("Topic board — Inbox, Loose ends and Done")
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

    @Test(arguments: [
        ("Slack · Oct 10 — [Priya asked about retention](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)",
         "Priya asked about retention", "https://acme-co.slack.com/archives/C0123456789/p1700000000000000", "Slack · Oct 10"),
        ("Delete the stale branch", "Delete the stale branch", nil, nil),
        ("GitHub — merged branch left behind https://github.com/example-org/web/pull/1",
         "merged branch left behind", "https://github.com/example-org/web/pull/1", "GitHub"),
    ] as [(String, String, String?, String?)])
    func parsesBullets(bullet: String, text: String, url: String?, source: String?) {
        let entry = SideEntry.parse(bullet)
        #expect(entry.text == text)
        #expect(entry.url?.absoluteString == url)
        #expect(entry.source == source)
        #expect(entry.key == url ?? text)
    }

    @Test func entriesComeFromTheTitledSectionMinusDismissed() throws {
        let s = try sections([
            "## 📥 Inbox",
            "- Slack · Oct 10 — [Priya asked about retention](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)",
            "- Linear · Oct 9 — [PROJ-1234 assigned to you](https://linear.app/x/issue/PROJ-1234)",
            "## 🧹 Loose ends",
            "- Delete the stale branch",
        ])
        let inbox = SideEntry.entries(
            inSectionTitled: "Inbox", of: s,
            dismissed: ["https://linear.app/x/issue/PROJ-1234"]
        )
        #expect(inbox.map(\.text) == ["Priya asked about retention"])
        #expect(SideEntry.entries(inSectionTitled: "loose ends", of: s, dismissed: []).map(\.text) == ["Delete the stale branch"])
    }

    @Test func dismissedFileKeys() {
        #expect(DismissedFileLoader.parse("# Dismissed\r\n\r\nProse.\r\n\r\n- https://a.example/1\r\n-  Delete it \t\r\n- \r\n") == [
            "https://a.example/1", "Delete it",
        ])
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

    @Test(arguments: [
        ("Reattach", "Stream reattach", "Reattach"),
        ("  Stream reattach ", "Reattach", ""),
        ("", "Reattach", ""),
        ("Reattach", "Reattach", nil),
    ] as [(String, String, String?)])
    func renameSendsOnlyChanges(draft: String, current: String, expected: String?) {
        #expect(TopicNameField.nameToSave(draft: draft, current: current, scoutTitle: "Stream reattach") == expected)
    }

    @Test func doneDateIsTheFirstDayATaskShowsUpTicked() throws {
        let earlier = try sections(["## 🔴 Urgent", "- [x] [#AAAA] **Ticked on the 8th**", "- [ ] [#BBBB] **Still open**"])
        let later = try sections(["## ✅ Done", "- [x] [#AAAA] **Ticked on the 8th**", "- [x] [#BBBB] **Ticked on the 10th**"])
        let dates = DoneHistory.firstDoneDates([(day(2026, 10, 10, hour: 0), later), (day(2026, 10, 8, hour: 0), earlier)])
        #expect(dates == ["AAAA": day(2026, 10, 8, hour: 0), "BBBB": day(2026, 10, 10, hour: 0)])

        let yesterday = DoneEntry.entries(
            sections: later, fileDate: day(2026, 10, 10, hour: 0), doneDates: dates,
            range: .yesterday, today: day(2026, 10, 11), calendar: calendar
        )
        #expect(yesterday.map(\.task.shortPrefix) == ["BBBB"])
    }
}

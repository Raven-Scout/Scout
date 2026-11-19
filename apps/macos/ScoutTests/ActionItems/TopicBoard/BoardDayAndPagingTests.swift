import Testing
import Foundation
@testable import Scout

/// UI audit R9: the topic board's filter, displayed day, Done range and
/// paging, and the snooze presets' anchor (audit #45, #46, #49).
@Suite("Topic board — filter, displayed day, Done range, paging")
struct BoardDayAndPagingTests {
    private let calendar = Calendar.current

    private func day(_ y: Int, _ m: Int, _ d: Int, hour: Int = 12) -> Date {
        calendar.date(from: DateComponents(year: y, month: m, day: d, hour: hour))!
    }

    private func sections(_ lines: [String]) throws -> [ActionSection] {
        let text = (["# Action Items — 2026-07-20", ""] + lines).joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        return try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count).sections
    }

    // MARK: #45 — a legend filter no longer pins every group open

    @Test("unfiltered, the saved open rows decide")
    func unfilteredUsesSavedRows() {
        let raw = BoardExpansion.setting("STRM", open: true, in: "")
        #expect(BoardExpansion.isOpen("STRM", in: raw, filtering: false, closedUnderFilter: ["STRM"]))
        #expect(!BoardExpansion.isOpen("OTHR", in: raw, filtering: false, closedUnderFilter: []))
    }

    @Test("filtered, groups start open but a closed one stays closed")
    func filteredGroupsCanClose() {
        // Not open in the saved rows, but the filter shows it open…
        #expect(BoardExpansion.isOpen("STRM", in: "", filtering: true, closedUnderFilter: []))
        // …and its chevron works: closing it under the filter sticks.
        let closed = BoardExpansion.settingUnderFilter("STRM", open: false, in: [])
        #expect(!BoardExpansion.isOpen("STRM", in: "", filtering: true, closedUnderFilter: closed))
        #expect(BoardExpansion.isOpen("OTHR", in: "", filtering: true, closedUnderFilter: closed))
        let reopened = BoardExpansion.settingUnderFilter("STRM", open: true, in: closed)
        #expect(BoardExpansion.isOpen("STRM", in: "", filtering: true, closedUnderFilter: reopened))
    }

    @Test("the × says it can be undone")
    func closeHelpMentionsUndo() {
        #expect(SideEntryRow.closeHelp.contains("Undo"))
        #expect(!SideEntryRow.closeHelp.contains("for good"))
    }

    @Test("the × is always drawn, never hidden until hover")
    func closeButtonIsNotHoverOnly() throws {
        let source = try String(contentsOf: Self.source("ActionItems/TopicBoard/SideEntryRow.swift"), encoding: .utf8)
        #expect(!source.contains("opacity(hovering ? 1 : 0)"))
    }

    // MARK: #46 — the displayed day, the Done range, snooze presets

    @Test("today's board is judged at the moment; an older one at the end of its day")
    func referenceDate() {
        let now = day(2026, 11, 19, hour: 15)
        #expect(TopicBoard.referenceDate(fileDate: day(2026, 11, 19, hour: 0), now: now, calendar: calendar) == now)
        let past = TopicBoard.referenceDate(fileDate: day(2026, 7, 20, hour: 0), now: now, calendar: calendar)
        #expect(calendar.isDate(past, inSameDayAs: day(2026, 7, 20)))
        #expect(past > day(2026, 7, 20, hour: 23))
    }

    @Test("on a past day, a task snoozed to the next day is on hold, as it was then")
    func pastDaySnoozeIsJudgedAgainstThatDay() throws {
        let s = try sections([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Snoozed to the next day**",
            "  - snoozed-until: 2026-07-21",
        ])
        let realToday = day(2026, 11, 19)
        let asThen = TopicBoard.referenceDate(fileDate: day(2026, 7, 20, hour: 0), now: realToday, calendar: calendar)
        #expect(TopicBoard.boardItems(sections: s, metas: [], today: asThen).map(\.state) == [.hold])
        // Judged against the real today (the old behaviour) it looked active.
        #expect(TopicBoard.boardItems(sections: s, metas: [], today: realToday).map(\.state) == [.mine])
    }

    @Test("on a past day, the Done list's \"today\" is that day")
    func pastDayDoneRange() throws {
        let s = try sections([
            "## ✅ Recently Completed",
            "",
            "### 2026-07-20",
            "- [x] [#AAAA] **Done that day**",
            "",
            "### 2026-07-19",
            "- [x] [#BBBB] **Done the day before**",
        ])
        let asThen = TopicBoard.referenceDate(fileDate: day(2026, 7, 20, hour: 0), now: day(2026, 11, 19), calendar: calendar)
        let today = DoneEntry.entries(sections: s, range: .today, today: asThen, calendar: calendar)
        #expect(today.map(\.task.shortPrefix) == ["AAAA"])
        let yesterday = DoneEntry.entries(sections: s, range: .yesterday, today: asThen, calendar: calendar)
        #expect(yesterday.map(\.task.shortPrefix) == ["BBBB"])
    }

    @Test("the board reads the Done range Settings writes, not a key of its own")
    func doneRangeHasOneSource() throws {
        let board = try String(contentsOf: Self.source("ActionItems/TopicBoard/TopicBoardView.swift"), encoding: .utf8)
        #expect(!board.contains("topicBoard.doneRange"))
        let page = try String(contentsOf: Self.source("ActionItems/ActionItemsView.swift"), encoding: .utf8)
        #expect(page.contains("binding(\\.topics.doneRange)"))
        let settings = try String(contentsOf: Self.source("Shell/ActionItemsDisplaySection.swift"), encoding: .utf8)
        #expect(settings.contains("binding(\\.topics.doneRange)"))
        #expect(!settings.contains("from the next update"))
    }

    @Test("snooze presets count from the real today on an old file")
    func snoozeFromOldFile() throws {
        let old = day(2026, 7, 20, hour: 0)
        let now = day(2026, 11, 19, hour: 15)
        #expect(SnoozeDates.anchor(sourceDate: old, now: now, calendar: calendar) == calendar.startOfDay(for: now))
        let tomorrow = try #require(SnoozeDates.target(days: 1, sourceDate: old, now: now, calendar: calendar))
        #expect(calendar.isDate(tomorrow, inSameDayAs: day(2026, 11, 20)))
        #expect(tomorrow > now)
    }

    @Test("snooze presets on today's file are unchanged")
    func snoozeFromToday() throws {
        let todayFile = day(2026, 11, 19, hour: 0)
        let now = day(2026, 11, 19, hour: 9)
        let week = try #require(SnoozeDates.target(days: 7, sourceDate: todayFile, now: now, calendar: calendar))
        #expect(calendar.isDate(week, inSameDayAs: day(2026, 11, 26)))
    }

    // MARK: #49 — topic paging and the Board's read-only hint

    @Test("topic rows build a page at a time")
    func topicPaging() {
        var window = TopicWindow()
        #expect(window.summary(total: TopicWindow.pageSize) == nil)
        #expect(window.visibleCount(of: 40) == TopicWindow.pageSize)
        #expect(window.summary(total: 40) == "Showing \(TopicWindow.pageSize) of 40 topics")
        #expect(window.nextPageCount(total: 40) == TopicWindow.pageSize)
        window.showMore()
        window.showMore()
        #expect(window.visibleCount(of: 40) == 40)
        #expect(window.summary(total: 40) == nil)
        #expect(window.nextPageCount(total: 40) == 0)
    }

    @Test("the Board says it is read-only and where to act")
    func boardHint() {
        #expect(BoardView.readOnlyHint.contains("List"))
        #expect(BoardView.readOnlyHint.contains("done"))
    }

    // MARK: - Helpers

    static func source(_ relative: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout").appendingPathComponent(relative)
    }
}

/// UI audit R9: the action-item states are explained, and one menu item
/// says "Launch Claude Code" (audit #51).
@Suite("Action Items — states explained, one Launch Claude Code")
@MainActor
struct ActionItemStatesExplainedTests {
    private func section(_ heading: String) throws -> ActionSection {
        let text = ["# Action Items — 2026-07-20", "", heading, "- [ ] [#AAAA] **A task**"].joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        return try #require(ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count).sections.first)
    }

    @Test("Parked and Recently Completed headers carry their glossary entry")
    func headersExplainTheirState() throws {
        #expect(SectionView.glossaryTerm(for: try section("## 🅿️ Parked")) == .parked)
        #expect(SectionView.glossaryTerm(for: try section("## ✅ Recently Completed")) == .done)
        #expect(SectionView.glossaryTerm(for: try section("## 🔴 Urgent")) == nil)
    }

    @Test("On hold and Done on the board explain themselves, not just repeat the word")
    func boardStatesExplain() {
        #expect(BoardState.hold.help == GlossaryTerm.onHold.definition)
        #expect(BoardState.done.help == GlossaryTerm.done.definition)
        #expect(BoardState.hold.help != BoardState.hold.label)
    }

    @Test("only the split button says Launch Claude Code; the terminal item names the terminal")
    func oneLaunchClaudeCode() {
        for terminal in CLITerminal.allCases {
            let label = LaunchClaudeMenu.cliMenuLabel(for: terminal)
            #expect(!label.hasPrefix("Launch Claude Code"), "\(terminal): \(label)")
            #expect(label.contains("Claude Code"))
        }
    }
}

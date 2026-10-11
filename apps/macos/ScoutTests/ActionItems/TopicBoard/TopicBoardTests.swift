import Testing
import Foundation
@testable import Scout

@Suite("Topic board — grouping, nesting and order")
struct TopicBoardTests {
    private let today = ISO8601DateFormatter().date(from: "2026-07-20T12:00:00Z")!

    private func sections(_ lines: [String]) throws -> [ActionSection] {
        let text = (["# Action Items — 2026-07-20", ""] + lines).joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        return try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count).sections
    }

    private func board(_ lines: [String], topics: [TopicMeta] = [], filter: Set<BoardState> = []) throws -> TopicBoard {
        TopicBoard.build(sections: try sections(lines), topics: topics, filter: filter, today: today)
    }

    private func tags(_ steps: [BoardStep]) -> [String] { steps.map(\.item.key) }

    private let reattach = TopicMeta(
        tag: "STRM", title: "Make stream reattach survive a restart",
        session: "https://claude.ai/code/session_0123", summary: nil, customTitle: nil
    )

    @Test func statesComeFromFieldsSectionsSnoozeAndDone() throws {
        let items = TopicBoard.boardItems(sections: try sections([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Urgent default**",
            "- [ ] [#BBBB] **Explicit doing**",
            "  - State: doing",
            "- [ ] [#CCCC] **Snoozed**",
            "  - snoozed-until: 2026-07-25",
            "- [ ] [#CCCD] **Snooze passed**",
            "  - snoozed-until: 2026-07-19",
            "- [x] [#DDDD] **Ticked**",
            "## 🟢 Watching",
            "- [ ] [#EEEE] **Watching default**",
            "## 📋 Digest",
            "- [ ] [#FFFF] **Digest rows stay off the board**",
        ]), metas: [], today: today)
        #expect(items.map { "\($0.key)=\($0.state)" } == [
            "AAAA=mine", "BBBB=doing", "CCCC=hold", "CCCD=mine", "EEEE=waiting",
        ])
    }

    @Test func groupsByTopicFieldThenRootSessionThenOther() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **By field**",
            "  - Topic: #STRM",
            "- [ ] [#BBBB] **By root session**",
            "  - Session: https://claude.ai/code/session_0123",
            "- [ ] [#CCCC] **Loose**",
        ], topics: [reattach])
        #expect(b.topics.map(\.key) == ["STRM", TopicBoard.otherKey])
        #expect(b.topics.map(\.title) == ["Make stream reattach survive a restart", "Other"])
        #expect(tags(b.topics[0].steps) == ["AAAA", "BBBB"])
    }

    @Test func blockedStepsNestUnderTheirBlocker() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Land the API**",
            "  - Topic: #STRM",
            "- [ ] [#BBBB] **Ship the UI**",
            "  - Topic: #STRM",
            "  - State: blocked",
            "  - Depends: #AAAA",
            "- [ ] [#CCCC] **Flip the flag**",
            "  - Topic: #STRM",
            "  - State: blocked",
            "  - Depends: #BBBB",
        ], topics: [reattach])
        let top = try #require(b.topics.first)
        #expect(tags(top.steps) == ["AAAA"])
        #expect(top.steps[0].blockedCount == 2)
        #expect(tags(top.steps[0].blocked) == ["BBBB"])
        #expect(tags(top.steps[0].blocked[0].blocked) == ["CCCC"])
        #expect(top.counts == [.mine: 1, .blocked: 2])
    }

    @Test func blockedStepWithoutAnOpenBlockerIsTopLevel() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#BBBB] **Ship the UI**",
            "  - State: blocked",
            "  - Depends: #GONE",
        ])
        #expect(tags(b.topics[0].steps) == ["BBBB"])
    }

    @Test func dependencyCycleDoesNotRecurseForever() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Root**",
            "- [ ] [#BBBB] **One**",
            "  - State: blocked",
            "  - Depends: #AAAA, #CCCC",
            "- [ ] [#CCCC] **Two**",
            "  - State: blocked",
            "  - Depends: #BBBB",
        ])
        #expect(tags(b.topics[0].steps) == ["AAAA"])
        #expect(b.topics[0].steps[0].blockedCount == 2)
    }

    @Test func onHoldGoesToOneGroupAtTheEnd() throws {
        let b = try board([
            "## 🟡 To Do",
            "- [ ] [#AAAA] **Active**",
            "- [ ] [#BBBB] **Parked**",
            "  - State: hold",
        ])
        #expect(tags(b.topics.flatMap(\.steps)) == ["AAAA"])
        #expect(tags(b.onHold) == ["BBBB"])
    }

    @Test func ordersRowsAndTopicsByPriority() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **No ticket** — nothing linked",
            "- [ ] [#BBBB] **Low ticket** — PROJ-1",
            "  - Topic: #QBLK",
            "  - Priority: 4",
            "- [ ] [#CCCC] **Urgent ticket, this cycle** — PROJ-2",
            "  - Topic: #QBLK",
            "  - Priority: 1",
            "  - Cycle: current",
            "- [ ] [#DDDD] **Urgent ticket** — PROJ-3",
            "  - Topic: #QBLK",
            "  - Priority: 1",
            "- [ ] [#EEEE] **Slack thread** — see acme-co.slack.com/archives/C0123456789/p1700000000000000",
            "  - Topic: #STRM",
        ], topics: [reattach])
        #expect(b.topics.map(\.key) == ["QBLK", "STRM", TopicBoard.otherKey])
        #expect(tags(b.topics[0].steps) == ["CCCC", "DDDD", "BBBB"])
    }

    @Test func filterShowsOnlyChosenStatesAndHidesHold() throws {
        let lines = [
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Mine**",
            "- [ ] [#BBBB] **Blocked by mine**",
            "  - State: blocked",
            "  - Depends: #AAAA",
            "- [ ] [#CCCC] **Waiting**",
            "  - State: waiting",
            "- [ ] [#DDDD] **Parked**",
            "  - State: hold",
        ]
        let blockedOnly = try board(lines, filter: [.blocked])
        #expect(tags(blockedOnly.topics.flatMap(\.steps)) == ["BBBB"])
        #expect(blockedOnly.onHold.isEmpty)

        let mineAndBlocked = try board(lines, filter: [.mine, .blocked])
        #expect(tags(mineAndBlocked.topics.flatMap(\.steps)) == ["AAAA"])
        #expect(tags(mineAndBlocked.topics[0].steps[0].blocked) == ["BBBB"])

        let mineOnly = try board(lines, filter: [.mine])
        #expect(mineOnly.topics[0].steps[0].blocked.isEmpty)
    }

    @Test func searchKeepsMatchingSteps() throws {
        let b = TopicBoard.build(sections: try sections([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Land the API**",
            "- [ ] [#BBBB] **Write the demo script**",
        ]), topics: [], search: " DEMO ", today: today)
        #expect(tags(b.topics.flatMap(\.steps)) == ["BBBB"])
    }

    private func allKeys(_ b: TopicBoard) -> [String] {
        func walk(_ steps: [BoardStep]) -> [String] { steps.flatMap { [$0.item.key] + walk($0.nested) } }
        return walk(b.topics.flatMap(\.steps)) + walk(b.onHold)
    }

    @Test func closedCycleStillShowsEveryStepOnce() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **One**",
            "  - State: blocked",
            "  - Depends: #BBBB",
            "- [ ] [#BBBB] **Two**",
            "  - State: blocked",
            "  - Depends: #AAAA",
            "- [ ] [#CCCC] **Self**",
            "  - State: blocked",
            "  - Depends: #CCCC",
        ])
        #expect(allKeys(b).sorted() == ["AAAA", "BBBB", "CCCC"])
        #expect(tags(b.topics[0].steps) == ["AAAA", "CCCC"])
    }

    @Test func blockedOnHeldWorkIsTopLevelOnly() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#HELD] **Parked**",
            "  - State: hold",
            "- [ ] [#XXXX] **Waits on parked**",
            "  - State: blocked",
            "  - Depends: #HELD",
        ])
        #expect(allKeys(b) == ["XXXX", "HELD"])
    }

    @Test func blockedOnTwoTopicsShowsOnce() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **First blocker**",
            "  - Topic: #QBLK",
            "- [ ] [#BBBB] **Second blocker**",
            "  - Topic: #ZDEP",
            "- [ ] [#XXXX] **Needs both**",
            "  - State: blocked",
            "  - Depends: #AAAA, #BBBB",
        ])
        #expect(allKeys(b).sorted() == ["AAAA", "BBBB", "XXXX"])
    }

    @Test func childrenNestUnderTheirParentBeforeBlockedSteps() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#PRNT] **Land the reattach work**",
            "- [ ] [#KIDA] **Merge the API**",
            "  - Parent: #PRNT",
            "  - Action: Merge",
            "- [ ] [#KIDB] **Review the UI**",
            "  - Parent: #PRNT",
            "  - State: waiting",
            "- [ ] [#KIDH] **Parked part**",
            "  - Parent: #PRNT",
            "  - State: hold",
            "- [ ] [#BLKD] **Flip the flag**",
            "  - State: blocked",
            "  - Depends: #PRNT",
        ])
        let parent = try #require(b.topics.first?.steps.first)
        #expect(tags(b.topics[0].steps) == ["PRNT"])
        #expect(tags(parent.children) == ["KIDA", "KIDB", "KIDH"])
        #expect(tags(parent.blocked) == ["BLKD"])
        #expect(parent.blockedCount == 1)
        #expect(b.onHold.isEmpty)
        #expect(allKeys(b).sorted() == ["BLKD", "KIDA", "KIDB", "KIDH", "PRNT"])
    }

    @Test func filteringHidesHeldChildren() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#PRNT] **Parent**",
            "- [ ] [#KIDA] **Mine child**",
            "  - Parent: #PRNT",
            "- [ ] [#KIDH] **Held child**",
            "  - Parent: #PRNT",
            "  - State: hold",
        ], filter: [.mine])
        #expect(tags(b.topics[0].steps[0].children) == ["KIDA"])
    }

    @Test func childOfAMissingParentIsTopLevel() throws {
        let b = try board(["## 🔴 Urgent", "- [ ] [#KIDA] **Orphan**", "  - Parent: #GONE"])
        #expect(tags(b.topics[0].steps) == ["KIDA"])
    }

    @Test func actionChipOnlyOnMineLeaves() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#PRNT] **Parent**",
            "- [ ] [#KIDA] **Merge it**",
            "  - Parent: #PRNT",
            "  - Action: Merge",
            "- [ ] [#KIDB] **No action named**",
            "  - Parent: #PRNT",
            "- [ ] [#WAIT] **Waiting**",
            "  - State: waiting",
        ])
        let steps = b.topics.flatMap(\.steps)
        let parent = try #require(steps.first { $0.item.key == "PRNT" })
        #expect(StepRowView.actionLabel(for: parent) == nil)
        #expect(parent.children.map(StepRowView.actionLabel) == ["Merge", ActionChip.fallback])
        #expect(StepRowView.actionLabel(for: try #require(steps.first { $0.item.key == "WAIT" })) == nil)
    }

    @Test func openDependencyMakesAStepBlockedWithoutAStateField() throws {
        let items = TopicBoard.boardItems(sections: try sections([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Land the API**",
            "- [ ] [#BBBB] **Ship the UI**",
            "  - Depends: #AAAA",
            "- [ ] [#CCCC] **Needs done work**",
            "  - Depends: #DONE",
            "- [ ] [#DDDD] **Needs missing work**",
            "  - Depends: #GONE",
            "## ✅ Done",
            "- [x] [#DONE] **Finished**",
        ]), metas: [], today: today)
        #expect(items.map { "\($0.key)=\($0.state)" } == ["AAAA=mine", "BBBB=blocked", "CCCC=mine", "DDDD=mine"])
    }

    @Test func snoozeBeatsDependsAndDependsBeatsState() throws {
        let items = TopicBoard.boardItems(sections: try sections([
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Blocker**",
            "- [ ] [#BBBB] **Snoozed**",
            "  - Depends: #AAAA",
            "  - snoozed-until: 2026-07-25",
            "- [ ] [#CCCC] **Says doing**",
            "  - Depends: #AAAA",
            "  - State: doing",
        ]), metas: [], today: today)
        #expect(items.map { "\($0.key)=\($0.state)" } == ["AAAA=mine", "BBBB=hold", "CCCC=blocked"])
    }

    @Test func parkedSectionIsHold() throws {
        let b = try board(["## 🅿️ Parked", "- [ ] [#PARK] **Revisit later**", "## 🟡 To Do", "- [ ] [#TODO] **Do it**"])
        #expect(tags(b.onHold) == ["PARK"])
        #expect(tags(b.topics.flatMap(\.steps)) == ["TODO"])
    }

    @Test func stepHangingOffACycleShowsOnceWhenTheCycleSortsFirst() throws {
        let b = try board([
            "## 🔴 Urgent",
            "- [ ] [#WWWW] **Waits on the cycle**",
            "  - Depends: #XXXX",
            "- [ ] [#XXXX] **In the cycle, has a ticket** — PROJ-7",
            "  - Depends: #ZZZZ",
            "- [ ] [#ZZZZ] **In the cycle**",
            "  - Depends: #XXXX",
        ])
        #expect(allKeys(b).sorted() == ["WWWW", "XXXX", "ZZZZ"])
        #expect(!b.topics.isEmpty && b.topics.allSatisfy { !$0.steps.isEmpty })
    }
}

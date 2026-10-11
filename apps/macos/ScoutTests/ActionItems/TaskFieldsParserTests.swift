import Testing
import Foundation
@testable import Scout

@Suite("Task board fields — parser")
struct TaskFieldsParserTests {
    private let fieldLines = [
        "- [ ] [#REPLYX] **Reply to Alex about her purchase question** — still open",
        "  - Topic: #STRM",
        "  - Parent: #RTALL",
        "  - State: waiting",
        "  - Action: Review + decide",
        "  - Depends: #XREF, [#QBLK]",
        "  - Session: https://claude.ai/code/session_0123",
        "  - Priority: 2",
        "  - Cycle: Current",
        "  - alex: pinged in thread",
    ]

    private func onlyTask(_ lines: [String]) throws -> ActionTask {
        let text = (["# Action Items — 2026-07-20", "", "## 🔴 Urgent", ""] + lines).joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        let section = try #require(doc.sections.first { $0.kind == .urgent })
        return try #require(section.tasks.first)
    }

    @Test func collectsEveryFieldOnTheTask() throws {
        let task = try onlyTask(fieldLines)
        #expect(task.fields == TaskFields(
            topic: "STRM",
            parent: "RTALL",
            state: .waiting,
            action: "Review + decide",
            depends: ["XREF", "QBLK"],
            session: "https://claude.ai/code/session_0123",
            priority: 2,
            cycle: "current"
        ))
    }

    @Test func fieldsAreNotComments() throws {
        let task = try onlyTask(fieldLines)
        #expect(task.comments.map(\.author) == ["alex"])
    }

    @Test func fieldsSurviveLaterSubLines() throws {
        let task = try onlyTask([
            "- [ ] [#REPLYX] **Reply to Alex** — still open",
            "  - State: mine",
            "  - Refs: PROJ-1234",
            "  - snoozed-until: 2026-07-22",
            "  > alex: quoted comment",
            "  - alex: dash comment",
            "  //==<< inline note >>==//",
            "  context line",
        ])
        #expect(task.fields.state == .mine)
        #expect(task.comments.count == 3)
        #expect(task.details.map(\.text) == ["context line"])
    }

    @Test func taskWithoutFieldsIsEmpty() throws {
        let task = try onlyTask(["- [ ] [#REPLYX] **Reply to Alex** — still open", "  - Source: Slack"])
        #expect(task.fields == .empty)
    }

    @Test func invalidValueIsConsumedAndIgnored() throws {
        let task = try onlyTask(["- [ ] [#REPLYX] **Reply to Alex**", "  - State: sometime", "  - Priority: 7"])
        #expect(task.fields == .empty)
        #expect(task.comments.isEmpty)
    }

    @Test(arguments: [
        ("  - State: waiting", "state", "waiting"),
        ("\t- depends: #XREF", "depends", "#XREF"),
        ("  - PRIORITY: 1 ", "priority", "1"),
        ("  - Cycle:", "cycle", ""),
        ("  - State : waiting", "state", "waiting"),
    ])
    func matchesReservedKeys(line: String, key: String, value: String) {
        let match = TaskFieldsParser.match(line)
        #expect(match?.key == key)
        #expect(match?.value == value)
    }

    @Test(arguments: [
        "- State: waiting",
        "  - alex: State: waiting",
        "  - Source: Linear",
        "  - snoozed-until: 2026-10-12",
    ])
    func ignoresOtherLines(line: String) {
        #expect(TaskFieldsParser.match(line) == nil)
    }

    @Test(arguments: [
        ("mine", TaskState.mine), (" Doing ", .doing), ("HOLD", .hold), ("done", nil), ("soon", nil), ("", nil),
    ] as [(String, TaskState?)])
    func parsesState(value: String, expected: TaskState?) {
        #expect(TaskFieldsParser.state(value) == expected)
    }

    @Test(arguments: [
        ("#XREF", ["XREF"]),
        ("[#XREF], #QBLK", ["XREF", "QBLK"]),
        ("xref · 7391K  QBLK", ["XREF", "7391K", "QBLK"]),
        ("#X, not-a-tag, #TOOLONGTAG, #1234", []),
        ("", []),
    ])
    func parsesDepends(value: String, expected: [String]) {
        #expect(TaskFieldsParser.depends(value) == expected)
    }

    @Test(arguments: [("0", 0), ("4", 4), (" 2 ", 2), ("5", nil), ("-1", nil), ("+1", nil), ("high", nil), ("²", nil), ("２", nil)] as [(String, Int?)])
    func parsesPriority(value: String, expected: Int?) {
        #expect(TaskFieldsParser.priority(value) == expected)
    }
}

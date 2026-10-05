import Testing
import Foundation
@testable import Scout

@Suite("Task details — parser")
struct TaskDetailsParserTests {
    private func parse(_ lines: [String]) throws -> ActionItemsDocument {
        let text = (["# Action Items — 2026-07-20", "", "## 🔴 Urgent", ""] + lines)
            .joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        return try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
    }

    private func urgent(_ lines: [String]) throws -> ActionSection {
        let doc = try parse(lines)
        return try #require(doc.sections.first { $0.kind == .urgent })
    }

    private func onlyTask(_ lines: [String]) throws -> ActionTask {
        let section = try urgent(lines)
        return try #require(section.tasks.first)
    }

    private func d(_ depth: Int, _ text: String) -> TaskDetail {
        TaskDetail(depth: depth, text: text)
    }

    @Test func subBulletsAttachInOrder() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Order the roadmap items before Tuesday — you are the decider**",
            "  - 🗓️ Due Tuesday and it has never left Todo.",
            "  - 🔑 The other three wait on the customer; this one does not.",
            "  - 📦 The candidate items are already listed in PROJ-1234.",
        ])
        let task = try #require(section.tasks.first)
        #expect(task.body == "")
        #expect(task.details == [
            d(0, "🗓️ Due Tuesday and it has never left Todo."),
            d(0, "🔑 The other three wait on the customer; this one does not."),
            d(0, "📦 The candidate items are already listed in PROJ-1234."),
        ])
        #expect(section.bullets.isEmpty)
    }

    @Test func bodyAndDetailsCoexist() throws {
        let task = try onlyTask([
            "- [ ] [#PLN] **Ship the fix** — CI is green.",
            "  - Waiting on a second review from Priya.",
        ])
        #expect(task.body == "CI is green.")
        #expect(task.details == [d(0, "Waiting on a second review from Priya.")])
    }

    @Test func nestedBulletsCarryDepth() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Plan the cycle**",
            "  - outer point",
            "    - inner point",
            "      - innermost point",
            "  - back out",
        ])
        #expect(task.details == [
            d(0, "outer point"), d(1, "inner point"), d(2, "innermost point"), d(0, "back out"),
        ])
    }

    @Test func starAndPlusBulletsAreDetails() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Plan the cycle**",
            "  * star point",
            "  + plus point",
        ])
        #expect(task.details == [d(0, "star point"), d(0, "plus point")])
    }

    @Test func nestedChildTaskOwnsItsDetails() throws {
        let section = try urgent([
            "- [ ] [#NESTX] **Parent item**",
            "  - parent context",
            "  - [ ] [#LBL] **Child item**",
            "    - child context",
        ])
        #expect(section.tasks.count == 2)
        #expect(section.tasks[0].details == [d(0, "parent context")])
        #expect(section.tasks[1].indentLevel == 1)
        #expect(section.tasks[1].details == [d(0, "child context")])
    }

    @Test func rebuildingSubLinesKeepDetails() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - first context",
            "  - Refs: [[people/alex]] · #XREF",
            "  - snoozed-until: 2026-07-25",
            "  - alex: a dash comment",
            "  //==<< an inline note >>==//",
            "  - second context",
            "  > priya (2026-07-20 10:00 AM ET): a quoted comment",
        ])
        #expect(task.details == [d(0, "first context"), d(0, "second context")])
        #expect(task.comments.map(\.author) == ["alex", "user", "priya"])
        #expect(task.deepLinks.contains(.entity(path: "people/alex", label: nil)))
        #expect(task.snoozedUntil != nil)
    }

    @Test func windowClosesOnTopLevelBullet() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "- section prose",
            "  - indented after prose",
        ])
        #expect(section.tasks[0].details == [d(0, "task context")])
        #expect(section.bullets == ["section prose", "indented after prose"])
    }

    @Test func windowClosesOnHTMLComment() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "<!-- moved from yesterday",
            "     still inside the comment -->",
        ])
        #expect(task.details == [d(0, "task context")])
    }

    @Test func blankLinesKeepWindowOpen() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - first",
            "",
            "  - second",
        ])
        #expect(task.details == [d(0, "first"), d(0, "second")])
    }

    @Test func continuationLinesJoinTheirBullet() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - wrapped first line",
            "    second line of the same bullet",
        ])
        #expect(task.details == [d(0, "wrapped first line\nsecond line of the same bullet")])
    }

    @Test func continuationWithNoBulletStartsADetail() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  plain indented text",
        ])
        #expect(task.details == [d(0, "plain indented text")])
    }

    @Test func fenceIsVerbatimContinuation() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Make the demo cert**",
            "  - Run this",
            "    ```",
            "    openssl req -x509 \\",
            "      -keyout demo.key",
            "    - not a bullet",
            "    | not | a table |",
            "    ```",
            "  - after the fence",
        ])
        #expect(task.details == [
            d(0, "Run this\n```\nopenssl req -x509 \\\n  -keyout demo.key\n- not a bullet\n| not | a table |\n```"),
            d(0, "after the fence"),
        ])
    }

    @Test func fenceOpenedOnTheBulletLine() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Task**",
            "  - ```bash",
            "    make test",
            "    ```",
            "  - second point",
            "  - [ ] [#LBL] **Child**",
            "    - alex: please check",
            "- [ ] [#PLN] **Next**",
        ])
        #expect(section.tasks.count == 3)
        #expect(section.tasks[0].details == [d(0, "```bash\nmake test\n```"), d(0, "second point")])
        #expect(section.tasks[1].shortPrefix == "LBL")
        #expect(section.tasks[1].comments.map(\.author) == ["alex"])
    }

    @Test func unclosedFenceEndsWithItsListItem() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Task**",
            "  - Run this",
            "    ```",
            "    make test",
            "  - next point",
            "  - [ ] [#LBL] **Child**",
            "    - alex: please check",
        ])
        #expect(section.tasks.count == 2)
        #expect(section.tasks[0].details == [d(0, "Run this\n```\nmake test"), d(0, "next point")])
        #expect(section.tasks[1].comments.map(\.author) == ["alex"])
    }

    @Test func inlineCodeIsNotAFenceOpener() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Task**",
            "  - Install it",
            "    ```npm i``` then restart",
            "    - alex: done on my side",
        ])
        #expect(task.details == [d(0, "Install it\n```npm i``` then restart")])
        #expect(task.comments.map(\.author) == ["alex"])
    }

    @Test func parkedTaskKeepsItsOwnDetails() throws {
        let section = try urgent([
            "<details><summary>Superseded — yesterday</summary>",
            "",
            "- [ ] [#PLN] **Old item**",
            "  - old context",
            "",
            "</details>",
            "  - indented after the archive",
            "- [ ] [#DETX] **Live item**",
        ])
        let group = try #require(section.collapsed.first)
        #expect(group.tasks.first?.details == [d(0, "old context")])
        let live = try #require(section.tasks.first)
        #expect(live.details.isEmpty)
    }

    @Test func windowDoesNotCrossSections() throws {
        let doc = try parse([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "## 🟡 To Do",
            "  - stray indented line",
        ])
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        #expect(urgent.tasks[0].details == [d(0, "task context")])
        let todo = try #require(doc.sections.first { $0.kind == .todo })
        #expect(todo.bullets == ["stray indented line"])
    }
}

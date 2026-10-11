import Foundation
import Testing
@testable import Scout

@Suite("TaskWindow")
struct TaskWindowTests {
    private func task(_ n: Int, indent: Int = 0) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: n, done: false,
            subject: "[#IOTA] Task \(n)", plainSubject: "Task \(n)", body: "",
            comments: [], deepLinks: [], details: [], snoozedUntil: nil, carriedInFrom: nil,
            indentLevel: indent)
    }

    private func tasks(_ count: Int) -> [ActionTask] { (0..<count).map { task($0) } }

    @Test("a fresh window shows one page")
    func freshWindowShowsOnePage() {
        let all = tasks(TaskWindow.pageSize * 3)
        let window = TaskWindow()
        #expect(window.visible(all).map(\.id) == all.prefix(TaskWindow.pageSize).map(\.id))
        #expect(window.hiddenCount(in: all) == TaskWindow.pageSize * 2)
    }

    @Test("a list shorter than a page is shown whole")
    func shortListShownWhole() {
        let all = tasks(3)
        let window = TaskWindow()
        #expect(window.visible(all).map(\.id) == all.map(\.id))
        #expect(window.hiddenCount(in: all) == 0)
        #expect(window.nextPageCount(in: all) == 0)
    }

    @Test("show more reveals one more page, never more than remain")
    func showMoreRevealsOnePage() {
        let all = tasks(TaskWindow.pageSize + 7)
        var window = TaskWindow()
        #expect(window.nextPageCount(in: all) == 7)
        window.showMore()
        #expect(window.visible(all).count == all.count)
        #expect(window.hiddenCount(in: all) == 0)
        #expect(window.nextPageCount(in: all) == 0)
    }

    @Test("the cut never strands a sub-task from its parent")
    func cutKeepsChildrenWithParent() {
        // The page boundary falls on a parent; its two children come along.
        var all = tasks(TaskWindow.pageSize)
        all.append(task(900, indent: 1))
        all.append(task(901, indent: 2))
        all.append(task(902))
        let window = TaskWindow()
        let shown = window.visible(all)
        #expect(shown.count == TaskWindow.pageSize + 2)
        #expect(shown.last?.lineNumber == 901)
        #expect(window.hiddenCount(in: all) == 1)
    }

    @Test("exactly a page hides nothing; one more hides one")
    func pageBoundary() {
        let window = TaskWindow()
        #expect(window.hiddenCount(in: tasks(TaskWindow.pageSize)) == 0)
        #expect(window.hiddenCount(in: tasks(TaskWindow.pageSize + 1)) == 1)
        #expect(window.nextPageCount(in: tasks(TaskWindow.pageSize + 1)) == 1)
    }

    @Test("the Show more count is what show more actually reveals")
    func nextPageCountMatchesShowMore() {
        // A sub-task run straddles the first cut, so the first page already
        // carries 5 rows of what would have been page two.
        var all = tasks(TaskWindow.pageSize)
        all += (0..<5).map { task(900 + $0, indent: 1) }
        all += (0..<30).map { task(1000 + $0) }
        var window = TaskWindow()
        let before = window.visible(all).count
        let promised = window.nextPageCount(in: all)
        window.showMore()
        #expect(window.visible(all).count - before == promised)
        #expect(promised == TaskWindow.pageSize - 5)
    }

    @Test("the top summary counts shown of total, only while rows are hidden (#356)")
    func summaryCountsShownOfTotal() {
        var window = TaskWindow()
        let all = tasks(53)
        #expect(window.summary(in: all) == "Showing 20 of 53")
        window.showMore()
        #expect(window.summary(in: all) == "Showing 40 of 53")
        window.showMore()
        #expect(window.summary(in: all) == nil)
        #expect(TaskWindow().summary(in: tasks(TaskWindow.pageSize)) == nil)
        #expect(TaskWindow().summary(in: []) == nil)
    }

    @Test("the summary counts the sub-tasks a page boundary carries along")
    func summaryCountsCarriedSubTasks() {
        var all = tasks(TaskWindow.pageSize)
        all += (0..<2).map { task(900 + $0, indent: 1) }
        all += tasks(5)
        #expect(TaskWindow().summary(in: all) == "Showing 22 of 27")
    }

    @Test("reveal grows the window until the row is built")
    func revealBuildsTheRow() {
        let all = tasks(TaskWindow.pageSize * 5)
        var window = TaskWindow()
        window.reveal(TaskWindow.pageSize * 3 + 4)
        #expect(window.visible(all).contains { $0.lineNumber == TaskWindow.pageSize * 3 + 4 })
        #expect(window.visible(all).count == TaskWindow.pageSize * 4)

        var already = TaskWindow()
        already.reveal(3)
        #expect(already == TaskWindow())
    }
}

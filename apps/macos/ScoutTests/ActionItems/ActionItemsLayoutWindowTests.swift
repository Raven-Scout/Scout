import Foundation
import Testing
@testable import Scout

/// The List builds one page of rows per drawn section (`TaskWindow`). With a
/// sort or One list on, the drawn sections are the arranged ones, so "Select
/// all" and reopening a task have to read those, not the file order.
@Suite("Action Items layout and task windows")
struct ActionItemsLayoutWindowTests {
    private static func task(_ subject: String) -> ActionTask {
        ActionTask(
            id: ActionItemsParser.stableID("t|\(subject)"), lineNumber: 1, done: false,
            subject: subject, plainSubject: subject, body: "", comments: [], deepLinks: [],
            details: [], snoozedUntil: nil, carriedInFrom: nil, indentLevel: 0
        )
    }

    private static func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(
            id: ActionItemsParser.stableID("s|\(kind)"), emoji: "", title: kind.rawValue, kind: kind,
            tasks: tasks, bullets: [], tables: [], subheads: [], collapsed: []
        )
    }

    /// More open tasks than one page, in the file from Item 25 down to Item 01,
    /// so the first page differs between file order and A-Z.
    private static let todo = section(.todo, (1...25).reversed().map { task(String(format: "Item %02d", $0)) })
    private static let urgent = section(.urgent, [task("Item 30")])

    /// What `ActionItemsView` draws for these sections under this sort and grouping.
    private static func drawn(
        _ sections: [ActionSection], sort: ActionItemsDisplay.Sort, grouping: ActionItemsDisplay.Grouping
    ) -> [ActionSection] {
        var display = ActionItemsDisplay()
        display.sort = sort
        display.grouping = grouping
        let layout = ActionItemsLayout.isPassThrough(display)
            ? nil : ActionItemsLayout.make(document: sections, filtered: sections, display: display)
        return ActionItemsLayout.listSections(layout, filtered: sections)
    }

    @Test(arguments: [
        (ActionItemsDisplay.Sort.fileOrder, ActionItemsDisplay.Grouping.section, "Item 25"),
        (.alphabetical, .section, "Item 01"),
        (.fileOrder, .none, "Item 25"),
        (.alphabetical, .none, "Item 01"),
    ])
    func selectAllTakesTheFirstDrawnPage(
        sort: ActionItemsDisplay.Sort, grouping: ActionItemsDisplay.Grouping, first: String
    ) {
        let rows = ActionItemsLayout.selectableRows(
            in: Self.drawn([Self.todo], sort: sort, grouping: grouping), windows: [:])
        #expect(rows.count == TaskWindow.pageSize)
        #expect(rows.first?.subject == first)
    }

    @Test func oneListPagesTheMergedListOnce() {
        let sections = [Self.urgent, Self.todo]
        let bySection = ActionItemsLayout.selectableRows(
            in: Self.drawn(sections, sort: .alphabetical, grouping: .section), windows: [:])
        let oneList = ActionItemsLayout.selectableRows(
            in: Self.drawn(sections, sort: .alphabetical, grouping: .none), windows: [:])
        #expect(bySection.count == 1 + TaskWindow.pageSize)
        #expect(oneList.count == TaskWindow.pageSize)
        #expect(!oneList.map { $0.subject }.contains("Item 30"))
    }

    @Test func theDoneDrawerIsShownInFull() {
        let done = Self.section(.done, (1...25).map { Self.task(String(format: "Done %02d", $0)) })
        let shown = ActionItemsLayout.shownRows(in: [Self.todo, done], windows: [:])
        #expect(shown.count == TaskWindow.pageSize + 25)
        #expect(ActionItemsLayout.selectableRows(in: [Self.todo, done], windows: [:]).count == TaskWindow.pageSize)
    }

    @Test func aReopenedTaskLandsOnItsDrawnRow() throws {
        let sections = [Self.urgent, Self.todo]
        let item25 = Self.task("Item 25").matchableSubject
        let fileOrder = try #require(ActionItemsLayout.revealTarget(
            in: Self.drawn(sections, sort: .fileOrder, grouping: .section), subject: item25, shortPrefix: nil))
        #expect(fileOrder.section == Self.todo.id)
        #expect(fileOrder.index == 0)
        let oneList = try #require(ActionItemsLayout.revealTarget(
            in: Self.drawn(sections, sort: .alphabetical, grouping: .none), subject: item25, shortPrefix: nil))
        #expect(oneList.section == ActionItemsArrangement.mergedSectionID)
        #expect(oneList.index == 24)
    }
}

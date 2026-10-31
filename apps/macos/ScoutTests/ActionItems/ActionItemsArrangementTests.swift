import Foundation
import Testing
@testable import Scout

@Suite("ActionItemsArrangement")
struct ActionItemsArrangementTests {
    private static func task(_ subject: String, indent: Int = 0, key: String? = nil) -> ActionTask {
        ActionTask(
            id: ActionItemsParser.stableID("t|\(key ?? subject)"), lineNumber: 1, done: false,
            subject: subject, plainSubject: subject, body: "", comments: [], deepLinks: [],
            details: [], snoozedUntil: nil, carriedInFrom: nil, indentLevel: indent
        )
    }

    private static func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(
            id: ActionItemsParser.stableID("s|\(kind)"), emoji: "", title: kind.rawValue, kind: kind,
            tasks: tasks, bullets: [], tables: [], subheads: [], collapsed: []
        )
    }

    private static func subjects(_ tasks: [ActionTask]) -> [String] { tasks.map(\.subject) }

    @Test func defaultsReturnTheInputUnchanged() {
        let sections = [Self.section(.urgent, [Self.task("b"), Self.task("a")]), Self.section(.focus, [])]
        let out = ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .section)
        #expect(out.sections == sections)
        #expect(out.kinds.isEmpty)
    }

    static let sorts: [(ActionItemsDisplay.Sort, [String])] = [
        (.fileOrder,    ["Review PROJ-1234", "Ask Priya", "Call Sam", "Book travel"]),
        (.alphabetical, ["Ask Priya", "Book travel", "Call Sam", "Review PROJ-1234"]),
    ]

    @Test(arguments: sorts)
    func sortOrders(_ sort: ActionItemsDisplay.Sort, _ expected: [String]) {
        let tasks = ["Review PROJ-1234", "Ask Priya", "Call Sam", "Book travel"].map { Self.task($0) }
        #expect(Self.subjects(ActionItemsArrangement.sorted(tasks, by: sort, parents: [:])) == expected)
    }

    @Test func subTasksMoveWithTheirParent() {
        let tasks = [
            Self.task("Zip the release"), Self.task("upload notes", indent: 1),
            Self.task("Ask Alex"), Self.task("draft question", indent: 1), Self.task("send it", indent: 2),
        ]
        let parents = ActionItemsArrangement.parents(in: [Self.section(.todo, tasks)])
        #expect(Self.subjects(ActionItemsArrangement.sorted(tasks, by: .alphabetical, parents: parents)) ==
                ["Ask Alex", "draft question", "send it", "Zip the release", "upload notes"])
    }

    @Test func tiesKeepFileOrder() {
        let tasks = [Self.task("Same", key: "1"), Self.task("Same", key: "2"), Self.task("Same", key: "3")]
        #expect(ActionItemsArrangement.sorted(tasks, by: .alphabetical, parents: [:]).map(\.id) == tasks.map(\.id))
    }

    /// A search removed "Ask Alex" and kept its sub-task. The sub-task must
    /// not ride along with "Zip the release", the task that now sits above it.
    @Test func aSubTaskWhoseParentWasFilteredOutStandsAlone() {
        let zip = Self.task("Zip the release"), upload = Self.task("upload notes", indent: 1)
        let ask = Self.task("Ask Alex"), draft = Self.task("draft question", indent: 1)
        let parents = ActionItemsArrangement.parents(in: [Self.section(.todo, [zip, upload, ask, draft])])
        let filtered = [zip, upload, draft]
        #expect(Self.subjects(ActionItemsArrangement.sorted(filtered, by: .alphabetical, parents: parents)) ==
                ["draft question", "Zip the release", "upload notes"])
    }

    @Test func inOneListAnOrphanedSubTaskDoesNotCrossSections() {
        let zebra = Self.task("Zebra crossing"), zNotes = Self.task("zebra notes", indent: 1)
        let apple = Self.task("Apple order"), aNotes = Self.task("apple notes", indent: 1)
        let source = [Self.section(.urgent, [zebra, zNotes]), Self.section(.todo, [apple, aNotes])]
        let parents = ActionItemsArrangement.parents(in: source)
        let filtered = [Self.section(.urgent, [zebra, zNotes]), Self.section(.todo, [aNotes])]
        let out = ActionItemsArrangement.arrange(filtered, parents: parents, sort: .alphabetical, grouping: .none)
        #expect(Self.subjects(out.sections[0].tasks) == ["apple notes", "Zebra crossing", "zebra notes"])
    }

    @Test func oneListMergesOpenSectionsAndKeepsEachKind() {
        let urgent = Self.task("Fix the demo")
        let watch = Self.task("Check PROJ-1234")
        let sections = [
            Self.section(.focus, []),
            Self.section(.urgent, [urgent]),
            Self.section(.watching, [watch]),
            Self.section(.done, [Self.task("Shipped")]),
        ]
        let out = ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .none)
        #expect(out.sections.map(\.kind) == [.focus, .neutral, .done])
        #expect(Self.subjects(out.sections[1].tasks) == ["Fix the demo", "Check PROJ-1234"])
        #expect(out.kinds == [urgent.id: .urgent, watch.id: .watching])
    }

    @Test func oneListWithNoOpenSectionsChangesNothing() {
        let sections = [Self.section(.focus, []), Self.section(.done, [Self.task("Shipped")])]
        #expect(ActionItemsArrangement.arrange(sections, parents: [:], sort: .fileOrder, grouping: .none).sections == sections)
    }

    @MainActor
    @Test func boardColumnsAreSortedInside() {
        let sections = [Self.section(.todo, [Self.task("b"), Self.task("a")])]
        let todo = ActionBoardColumn.columns(from: sections, sort: .alphabetical).first { $0.kind == .todo }
        #expect(Self.subjects(todo?.tasks ?? []) == ["a", "b"])
    }
}

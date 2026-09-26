import Testing
import Foundation
@testable import Scout

@Suite("Action board columns")
struct ActionBoardColumnTests {
    private func task(_ subject: String, done: Bool = false, snoozedFrom: ActionSection.Kind? = nil) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: done, subject: subject, plainSubject: subject,
            body: "", comments: [], deepLinks: [], snoozedUntil: nil, carriedInFrom: nil,
            snoozedFromKind: snoozedFrom
        )
    }

    private func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(id: UUID(), emoji: "", title: kind.rawValue, kind: kind,
                      tasks: tasks, bullets: [], tables: [], subheads: [], collapsed: [])
    }

    @Test func alwaysShowsCorePillarsInOrder() {
        let cols = ActionBoardColumn.columns(from: [
            section(.urgent, [task("u")]),
            section(.todo, [task("t")]),
            section(.watching, [task("w")]),
            section(.done, [task("d", done: true)]),
        ])
        #expect(cols.map(\.kind) == [.urgent, .todo, .watching, .done])
        #expect(cols.map(\.count) == [1, 1, 1, 1])
    }

    @Test func showsEmptyCorePillars() {
        // No tasks at all — Urgent/To Do/Watching/Done still appear (empty),
        // Personal does not.
        let cols = ActionBoardColumn.columns(from: [])
        #expect(cols.map(\.kind) == [.urgent, .todo, .watching, .done])
        #expect(cols.allSatisfy { $0.count == 0 })
    }

    @Test func personalAppearsOnlyWhenPopulated() {
        let withPersonal = ActionBoardColumn.columns(from: [section(.personal, [task("p")])])
        #expect(withPersonal.contains { $0.kind == .personal })

        let withoutPersonal = ActionBoardColumn.columns(from: [section(.urgent, [task("u")])])
        #expect(!withoutPersonal.contains { $0.kind == .personal })
    }

    @Test func bucketsBySnoozedFromKind() {
        // A task living in a neutral Snoozed section but originally urgent
        // should land in the Urgent column.
        let cols = ActionBoardColumn.columns(from: [
            section(.neutral, [task("snoozed urgent", snoozedFrom: .urgent)]),
        ])
        let urgent = cols.first { $0.kind == .urgent }
        #expect(urgent?.count == 1)
    }

    @Test func ignoresNonStatusSections() {
        // focus / meetings / digest / neutral don't map to columns.
        let cols = ActionBoardColumn.columns(from: [
            section(.focus, [task("f")]),
            section(.meetings, [task("m")]),
            section(.digest, [task("g")]),
        ])
        #expect(cols.map(\.kind) == [.urgent, .todo, .watching, .done])
        #expect(cols.allSatisfy { $0.count == 0 })
    }
}

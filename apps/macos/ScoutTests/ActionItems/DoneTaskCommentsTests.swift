import Foundation
import SwiftUI
import Testing
@testable import Scout

/// #52: a done task can be commented on. scoutctl finds a done task only by its
/// `[#TAG]` (`--by-id`); its `--subject` lookup matches open tasks, so a done
/// task without a tag keeps the composer hidden.
@MainActor
@Suite("Comments on done tasks")
struct DoneTaskCommentsTests {
    private static func task(done: Bool, prefix: String?) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 4, done: done,
            subject: "Send the weekly summary to Sam", plainSubject: "Send the weekly summary to Sam",
            body: "", comments: SmokeFixtures.comments, deepLinks: [], details: [],
            snoozedUntil: nil, carriedInFrom: nil, shortPrefix: prefix)
    }

    static let cases: [(done: Bool, prefix: String?, canComment: Bool)] = [
        (false, nil, true),
        (false, "XI7391", true),
        (true, "XI7391", true),
        (true, nil, false),
    ]

    @Test(arguments: cases)
    func whoCanComment(_ c: (done: Bool, prefix: String?, canComment: Bool)) {
        #expect(TaskCardView.canComment(Self.task(done: c.done, prefix: c.prefix)) == c.canComment)
    }

    /// R2: with the engine check failing, Edit and Delete on a comment are
    /// left out like every other write; with it passing, the usual rule holds.
    @Test(arguments: cases)
    func commentChangesFollowTheEngineCheck(_ c: (done: Bool, prefix: String?, canComment: Bool)) {
        let task = Self.task(done: c.done, prefix: c.prefix)
        #expect(TaskCardView.canChangeComments(task, writesEnabled: true) == c.canComment)
        #expect(!TaskCardView.canChangeComments(task, writesEnabled: false))
    }

    @Test("a card renders with its writes turned off")
    func cardRendersWithWritesOff() {
        for done in [false, true] {
            ViewHost.render(
                TaskCardView(
                    task: Self.task(done: done, prefix: "XI7391"), kind: done ? .done : .todo,
                    displayedDate: SmokeFixtures.day,
                    scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                    startsExpanded: true,
                    onCollapse: {},
                    onOp: { _, _ in Issue.record("a write ran with writes off") })
                    .environment(\.actionItemsWritesEnabled, false),
                size: CGSize(width: 900, height: 500))
        }
    }

    @Test("an expanded done card renders with its comments and composer")
    func expandedDoneCardRenders() {
        for prefix in ["XI7391", nil] {
            ViewHost.render(
                TaskCardView(
                    task: Self.task(done: true, prefix: prefix), kind: .done,
                    displayedDate: SmokeFixtures.day,
                    scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                    startsExpanded: true,
                    onCollapse: {},
                    onOp: { _, _ in }),
                size: CGSize(width: 900, height: 500))
        }
    }
}
